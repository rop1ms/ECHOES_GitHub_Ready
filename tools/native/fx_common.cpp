// fx_common.cpp — пул потоков и общие растровые операции.
#include "fx_common.h"

namespace fx {

Pool& Pool::get() {
    static Pool* p = new Pool();   // намеренно не удаляется: потоки живут до выхода процесса
    return *p;
}

Pool::Pool() {
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    int hc = (int)si.dwNumberOfProcessors;
    if (hc <= 0) hc = 4;
    // половина логических ядер: остальное — GUI, звук, декодер, фоновые задачи плеера
    int n = std::max(1, std::min(8, hc / 2));
    start(n - 1);
}

DWORD WINAPI Pool::thread_main(LPVOID self) {
    ((Pool*)self)->worker();
    return 0;
}

void Pool::start(int n) {
    quit_ = false;
    for (int i = 0; i < n; ++i) {
        HANDLE t = CreateThread(nullptr, 256 * 1024, &Pool::thread_main, this, 0, nullptr);
        if (!t) break;
        // чуть ниже обычного: поток звука (Python) не должен ждать, пока мы красим пиксели
        SetThreadPriority(t, THREAD_PRIORITY_BELOW_NORMAL);
        workers_.push_back(t);
    }
}

void Pool::stop_all() {
    {
        SrwGuard g(&m_);
        quit_ = true;
    }
    WakeAllConditionVariable(&cv_);
    for (HANDLE t : workers_) {
        WaitForSingleObject(t, 5000);
        CloseHandle(t);
    }
    workers_.clear();
}

void Pool::set_threads(int n) {
    n = std::max(1, std::min(32, n));
    if (n == threads()) return;
    stop_all();
    start(n - 1);
}

bool Pool::work_one(Batch* b) {
    int i = b->next.fetch_add(1);
    if (i >= b->n) return false;
    (*b->fn)(i);
    if (b->done.fetch_add(1) + 1 == b->n) {
        SrwGuard g(&m_);
        WakeAllConditionVariable(&done_cv_);
    }
    return true;
}

void Pool::worker() {
    for (;;) {
        Batch* b = nullptr;
        AcquireSRWLockExclusive(&m_);
        while (!quit_ && q_.empty()) SleepConditionVariableSRW(&cv_, &m_, INFINITE, 0);
        if (quit_) {
            ReleaseSRWLockExclusive(&m_);
            return;
        }
        b = q_.front();
        if (b->next.load() >= b->n) {   // разобрана целиком — убираем из очереди
            q_.pop_front();
            ReleaseSRWLockExclusive(&m_);
            continue;
        }
        b->users++;
        ReleaseSRWLockExclusive(&m_);
        while (work_one(b)) {
        }
        SrwGuard g(&m_);
        if (--b->users == 0) WakeAllConditionVariable(&done_cv_);
    }
}

void Pool::run(int n, const std::function<void(int)>& fn) {
    if (n <= 0) return;
    if (n == 1 || workers_.empty()) {
        for (int i = 0; i < n; ++i) fn(i);
        return;
    }
    Batch b;
    b.fn = &fn;
    b.n = n;
    {
        SrwGuard g(&m_);
        q_.push_back(&b);
    }
    WakeAllConditionVariable(&cv_);
    while (work_one(&b)) {
    }
    AcquireSRWLockExclusive(&m_);
    for (auto it = q_.begin(); it != q_.end(); ++it)
        if (*it == &b) {
            q_.erase(it);
            break;
        }
    // ждём и выполнения всех задач, и ухода всех потоков, взявших указатель на пачку
    while (!(b.done.load() >= b.n && b.users == 0)) SleepConditionVariableSRW(&done_cv_, &m_, INFINITE, 0);
    ReleaseSRWLockExclusive(&m_);
}

void upscale_u32(const uint32_t* src, int sw, int sh, int sstride, uint32_t* dst, int dw, int dh, int dstride,
                 int x0, int x1) {
    if (x1 < 0 || x1 > dw) x1 = dw;
    if (x0 < 0) x0 = 0;
    if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0 || x0 >= x1) return;
    // центры пикселей совпадают (как у Qt SmoothTransformation)
    const float kx = (float)sw / dw, ky = (float)sh / dh;
    std::vector<int> xi0(dw), xi1(dw);
    std::vector<uint16_t> xf(dw);
    for (int x = x0; x < x1; ++x) {
        float s = (x + 0.5f) * kx - 0.5f;
        if (s < 0) s = 0;
        int i = (int)s;
        if (i > sw - 1) i = sw - 1;
        float f = s - i;
        xi0[x] = i;
        xi1[x] = std::min(i + 1, sw - 1);
        xf[x] = (uint16_t)(f * 256.f);
    }
    parallel_rows(dh, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y) {
            float s = (y + 0.5f) * ky - 0.5f;
            if (s < 0) s = 0;
            int j = (int)s;
            if (j > sh - 1) j = sh - 1;
            uint32_t fy = (uint32_t)((s - j) * 256.f);
            const uint32_t* r0 = src + (size_t)j * sstride;
            const uint32_t* r1 = src + (size_t)std::min(j + 1, sh - 1) * sstride;
            uint32_t* o = dst + (size_t)y * dstride;
            for (int x = x0; x < x1; ++x) {
                uint32_t a = r0[xi0[x]], b = r0[xi1[x]], c = r1[xi0[x]], d = r1[xi1[x]];
                uint32_t fx = xf[x];
                // два канала за раз (00RR00BB и 00AA00GG)
                uint32_t rb_t = ((a & 0xFF00FF) * (256 - fx) + (b & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
                uint32_t ag_t = (((a >> 8) & 0xFF00FF) * (256 - fx) + ((b >> 8) & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
                uint32_t rb_b = ((c & 0xFF00FF) * (256 - fx) + (d & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
                uint32_t ag_b = (((c >> 8) & 0xFF00FF) * (256 - fx) + ((d >> 8) & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
                uint32_t rb = (rb_t * (256 - fy) + rb_b * fy) >> 8 & 0xFF00FF;
                uint32_t ag = (ag_t * (256 - fy) + ag_b * fy) >> 8 & 0xFF00FF;
                o[x] = rb | (ag << 8);
            }
        }
    });
}

}  // namespace fx

FX_API int fx_version() { return 1; }

FX_API int fx_threads() { return fx::Pool::get().threads(); }

FX_API void fx_set_threads(int n) { fx::Pool::get().set_threads(n); }

FX_API void fx_upscale_u32(const uint32_t* src, int sw, int sh, int sstride, uint32_t* dst, int dw, int dh,
                           int dstride) {
    fx::upscale_u32(src, sw, sh, sstride, dst, dw, dh, dstride);
}
