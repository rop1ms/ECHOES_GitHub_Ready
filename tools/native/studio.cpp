// studio.cpp — звук студии ECHOES на C++ (вызов через ctypes отпускает GIL, интерфейс не ждёт):
//  * st_vfilter — биквад (RBJ: ФНЧ / ФВЧ / полосовой) со срезом, меняющимся по отсчётам
//    (огибающая фильтра, LFO). Коэффициенты пересчитываются каждые 8 отсчётов — в Python это был
//    цикл по 64 отсчёта со scipy на каждом шаге: 2/3 времени рендера нот синтезаторов.
//  * st_wt_read — чтение волновой таблицы: фаза и позиция по отсчётам, билинейно (кадр × отсчёт).
#include "fx_common.h"

// Smoothed listening matrix, in-place stereo float PCM. No heap allocation,
// locks, Qt work or pool dispatch in the real-time callback.
FX_API void fx_listener_matrix(float* pcm, int frames, float* state,
                               const float* target, float smoothing) {
    if (!pcm || !state || !target || frames <= 0) return;
    for (int i=0; i<frames; ++i) {
        for (int j=0;j<4;++j) state[j] += (target[j]-state[j])*smoothing;
        const float l=pcm[2*i], r=pcm[2*i+1];
        pcm[2*i]=l*state[0]+r*state[1];
        pcm[2*i+1]=l*state[2]+r*state[3];
    }
}

namespace {

struct Coef {
    double b0, b1, b2, a1, a2;
};

static inline Coef rbj(int kind, double f, double q, double sr) {
    if (f < 10.0) f = 10.0;
    if (f > sr * 0.49) f = sr * 0.49;
    if (q < 0.05) q = 0.05;
    const double w0 = 2.0 * 3.14159265358979323846 * f / sr;
    const double cw = std::cos(w0), sw = std::sin(w0);
    const double alpha = sw / (2.0 * q);
    double b0, b1, b2;
    const double a0 = 1.0 + alpha, a1 = -2.0 * cw, a2 = 1.0 - alpha;
    if (kind == 1) {            // ФВЧ
        b0 = (1.0 + cw) * 0.5;
        b1 = -(1.0 + cw);
        b2 = (1.0 + cw) * 0.5;
    } else if (kind == 2) {     // полосовой
        b0 = alpha;
        b1 = 0.0;
        b2 = -alpha;
    } else {                    // ФНЧ
        b0 = (1.0 - cw) * 0.5;
        b1 = 1.0 - cw;
        b2 = (1.0 - cw) * 0.5;
    }
    const double ia = 1.0 / a0;
    return {b0 * ia, b1 * ia, b2 * ia, a1 * ia, a2 * ia};
}

}  // namespace

// x, y: n×ch (по отсчётам, каналы подряд), double; cut: n значений среза, Гц.
FX_API void st_vfilter(const double* x, double* y, int n, int ch, const float* cut, float q, int kind, int sr) {
    if (n <= 0 || ch <= 0) return;
    const int STEP = 8;
    double z1[8] = {0}, z2[8] = {0};
    if (ch > 8) ch = 8;
    Coef c = rbj(kind, cut[0], q, sr);
    for (int s = 0; s < n; ++s) {
        if (s % STEP == 0) {
            int mid = s + STEP / 2;
            if (mid >= n) mid = n - 1;
            c = rbj(kind, cut[mid], q, sr);
        }
        for (int k = 0; k < ch; ++k) {
            const double in = x[(size_t)s * ch + k];
            const double out = c.b0 * in + z1[k];          // транспонированная прямая форма II
            z1[k] = c.b1 * in - c.a1 * out + z2[k];
            z2[k] = c.b2 * in - c.a2 * out;
            y[(size_t)s * ch + k] = out;
        }
    }
}

// tab: frames×N float; ph: фаза 0..1 (n, double); pos: позиция 0..1 (n, float) → out (n, float).
FX_API void st_wt_read(const float* tab, int frames, int N, const double* ph, const float* pos, float* out, int n) {
    if (frames < 2 || N < 2) return;
    for (int i = 0; i < n; ++i) {
        float p = pos[i];
        p = p < 0.f ? 0.f : (p > 1.f ? 1.f : p);
        const float fp = p * (frames - 1);
        int f0 = (int)fp;
        if (f0 > frames - 2) f0 = frames - 2;
        const float fa = fp - f0;
        double xph = ph[i] - std::floor(ph[i]);
        const double xx = xph * N;
        int i0 = (int)xx;
        if (i0 >= N) i0 = N - 1;
        const float xa = (float)(xx - i0);
        const int i1 = (i0 + 1) % N;
        const float* r0 = tab + (size_t)f0 * N;
        const float* r1 = r0 + N;
        const float a = r0[i0] + (r0[i1] - r0[i0]) * xa;
        const float b = r1[i0] + (r1[i1] - r1[i0]) * xa;
        out[i] = a + (b - a) * fa;
    }
}
