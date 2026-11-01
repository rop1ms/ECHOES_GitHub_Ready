// fx_common.h — общее для всех модулей echoes_fx: экспорт, быстрая математика, пул потоков.
#pragma once
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#define FX_API extern "C" __declspec(dllexport)

namespace fx {

// ── быстрая математика (точность ~1e-5, без ветвлений — векторизуется) ── //

static inline float fexp(float x) {
    x = x < -87.f ? -87.f : (x > 88.f ? 88.f : x);
    float t = x * 1.4426950409f;
    float fi = std::floor(t);
    float f = t - fi;
    float p = 1.0f + f * (0.69314718f + f * (0.24022650f + f * (0.05550411f + f * (0.00961813f + f * 0.00133336f))));
    int32_t i = ((int32_t)fi + 127) << 23;
    float s;
    std::memcpy(&s, &i, 4);
    return p * s;
}

// sin на всей оси: приведение к [-pi, pi], затем полином 11-й степени
static inline float fsin(float x) {
    const float inv2pi = 0.15915494309f, twopi = 6.28318530718f;
    x -= twopi * std::nearbyint(x * inv2pi);
    // sin(x) ≈ x - x^3/6 + ... на [-pi, pi] (ошибка < 2e-6 после свёртки к [-pi/2, pi/2])
    const float hp = 1.57079632679f, pi = 3.14159265359f;
    float ax = x < 0 ? -x : x;
    float sg = x < 0 ? -1.f : 1.f;
    ax = ax > hp ? pi - ax : ax;
    float x2 = ax * ax;
    float r = ax * (1.f + x2 * (-0.16666667f + x2 * (0.0083333310f + x2 * (-0.00019840874f + x2 * (2.7525562e-6f - x2 * 2.3889859e-8f)))));
    return r * sg;
}

static inline float fcos(float x) { return fsin(x + 1.57079632679f); }

static inline float clamp01(float v) { return v < 0.f ? 0.f : (v > 1.f ? 1.f : v); }

static inline uint32_t pack_rgb(float r, float g, float b) {   // 0..1 → 0xFFRRGGBB
    int R = (int)(clamp01(r) * 255.f + 0.5f), G = (int)(clamp01(g) * 255.f + 0.5f), B = (int)(clamp01(b) * 255.f + 0.5f);
    return 0xFF000000u | ((uint32_t)R << 16) | ((uint32_t)G << 8) | (uint32_t)B;
}

// ── пул потоков (чистый Win32: CreateThread + SRWLOCK + CONDITION_VARIABLE) ──
// std::thread из MinGW тянет winpthreads, чья очистка при выгрузке DLL на выходе процесса
// могла намертво повесить ExitProcess. Здесь — только примитивы Windows.
// Несколько Python-потоков (жидкость, MilkDrop, волна, GUI) могут звать библиотеку
// одновременно: каждый вызов кладёт свою «пачку» задач в общую очередь и сам же в ней
// работает, пока не кончится — никто не ждёт чужую пачку целиком.

class Pool {
  public:
    static Pool& get();
    // fn(i) для i в [0, n); возвращается, когда всё сделано
    void run(int n, const std::function<void(int)>& fn);
    int threads() const { return (int)workers_.size() + 1; }
    void set_threads(int n);

  private:
    struct Batch {
        const std::function<void(int)>* fn;
        int n;
        std::atomic<int> next{0};
        std::atomic<int> done{0};
        int users = 0;   // рабочие потоки, держащие указатель (под m_)
    };
    Pool();
    void start(int n);
    void stop_all();
    static DWORD WINAPI thread_main(LPVOID self);
    void worker();
    bool work_one(Batch* b);

    std::vector<HANDLE> workers_;
    std::deque<Batch*> q_;
    SRWLOCK m_ = SRWLOCK_INIT;
    CONDITION_VARIABLE cv_ = CONDITION_VARIABLE_INIT, done_cv_ = CONDITION_VARIABLE_INIT;
    bool quit_ = false;
};

// SRWLOCK как std::lock_guard
struct SrwGuard {
    SRWLOCK* l;
    explicit SrwGuard(SRWLOCK* x) : l(x) { AcquireSRWLockExclusive(l); }
    ~SrwGuard() { ReleaseSRWLockExclusive(l); }
};

// разбить строки [0, h) на полосы и раздать пулу
template <class F>
static inline void parallel_rows(int h, F&& body, int min_rows = 8) {
    if (h <= 0) return;
    Pool& P = Pool::get();
    int chunks = std::min(h / std::max(1, min_rows) + 1, P.threads() * 3);
    if (chunks <= 1) {
        body(0, h);
        return;
    }
    int per = (h + chunks - 1) / chunks;
    std::function<void(int)> fn = [&](int i) {
        int y0 = i * per, y1 = std::min(h, y0 + per);
        if (y0 < y1) body(y0, y1);
    };
    P.run(chunks, fn);
}

// билинейное растяжение 0xAARRGGBB (непрозрачного) src → dst
void upscale_u32(const uint32_t* src, int sw, int sh, int sstride, uint32_t* dst, int dw, int dh, int dstride,
                 int x0 = 0, int x1 = -1);

}  // namespace fx
