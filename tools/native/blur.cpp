// blur.cpp — гауссово размытие ARGB32 (премультиплицированного) — порт blur_fx.gauss_array.
// Малый радиус — точное ядро, большой — три прохода «коробкой» (Куцкир); края продолжаются.
#include "fx_common.h"

namespace {
using namespace fx;

static void boxes_for_gauss(float sigma, int n, int* out) {
    float w_ideal = std::sqrt(12.f * sigma * sigma / n + 1.f);
    int wl = (int)std::floor(w_ideal);
    if (wl % 2 == 0) wl -= 1;
    int wu = wl + 2;
    float m_ideal = (12.f * sigma * sigma - n * wl * wl - 4.f * n * wl - 3.f * n) / (-4.f * wl - 4.f);
    int m = (int)std::lround(m_ideal);
    for (int i = 0; i < n; ++i) out[i] = i < m ? wl : wu;
}

// одна строка из 4 каналов: коробка радиуса r с продолжением краёв, src → dst (len пикселей)
static void box_line(const float* src, float* dst, int len, int step, int r) {
    if (r < 1) {
        for (int i = 0; i < len; ++i)
            for (int k = 0; k < 4; ++k) dst[(size_t)i * step + k] = src[(size_t)i * step + k];
        return;
    }
    const float inv = 1.f / (2 * r + 1);
    float acc[4] = {0, 0, 0, 0};
    auto at = [&](int i, int k) { i = i < 0 ? 0 : (i >= len ? len - 1 : i); return src[(size_t)i * step + k]; };
    for (int i = -r - 1; i < r; ++i)
        for (int k = 0; k < 4; ++k) acc[k] += at(i, k);
    for (int i = 0; i < len; ++i)
        for (int k = 0; k < 4; ++k) {
            acc[k] += at(i + r, k) - at(i - r - 1, k);
            dst[(size_t)i * step + k] = acc[k] * inv;
        }
}

static void taps_line(const float* src, float* dst, int len, int step, const std::vector<float>& ker) {
    int r = (int)ker.size() / 2;
    for (int i = 0; i < len; ++i) {
        float a[4] = {0, 0, 0, 0};
        for (int t = -r; t <= r; ++t) {
            int j = i + t;
            j = j < 0 ? 0 : (j >= len ? len - 1 : j);
            const float* s = src + (size_t)j * step;
            float w = ker[t + r];
            a[0] += w * s[0];
            a[1] += w * s[1];
            a[2] += w * s[2];
            a[3] += w * s[3];
        }
        for (int k = 0; k < 4; ++k) dst[(size_t)i * step + k] = a[k];
    }
}

}  // namespace

// Размыть изображение на месте. px — 32-битные пиксели (BGRA в памяти), stride — в пикселях.
FX_API void blur_argb32(uint32_t* px, int w, int h, int stride, float sigma, int dither) {
    if (w <= 0 || h <= 0 || sigma <= 0.3f) return;
    size_t n = (size_t)w * h;
    std::vector<float> a(n * 4), b(n * 4);
    parallel_rows(h, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y) {
            const uint8_t* s = (const uint8_t*)(px + (size_t)y * stride);
            float* d = a.data() + (size_t)y * w * 4;
            for (int i = 0; i < w * 4; ++i) d[i] = s[i];
        }
    });
    auto horiz = [&](std::vector<float>& src, std::vector<float>& dst, auto&& line) {
        parallel_rows(h, [&](int y0, int y1) {
            for (int y = y0; y < y1; ++y) line(src.data() + (size_t)y * w * 4, dst.data() + (size_t)y * w * 4, w, 4);
        });
    };
    auto vert = [&](std::vector<float>& src, std::vector<float>& dst, auto&& line) {
        parallel_rows(w, [&](int x0, int x1) {
            for (int x = x0; x < x1; ++x) line(src.data() + (size_t)x * 4, dst.data() + (size_t)x * 4, h, w * 4);
        });
    };
    if (sigma < 2.f) {
        int r = std::max(1, (int)std::ceil(sigma * 3.f));
        std::vector<float> ker(2 * r + 1);
        float sum = 0;
        for (int i = -r; i <= r; ++i) sum += ker[i + r] = std::exp(-0.5f * (i / sigma) * (i / sigma));
        for (auto& k : ker) k /= sum;
        auto L = [&](const float* s, float* d, int len, int step) { taps_line(s, d, len, step, ker); };
        horiz(a, b, L);
        vert(b, a, L);
    } else {
        int boxes[3];
        boxes_for_gauss(sigma, 3, boxes);
        for (int bi = 0; bi < 3; ++bi) {
            int r = (boxes[bi] - 1) / 2;
            auto L = [&](const float* s, float* d, int len, int step) { box_line(s, d, len, step, r); };
            horiz(a, b, L);
            vert(b, a, L);
        }
    }
    parallel_rows(h, [&](int y0, int y1) {
        uint32_t seed = 0x9E3779B9u ^ (uint32_t)y0 * 2654435761u;
        for (int y = y0; y < y1; ++y) {
            uint8_t* d = (uint8_t*)(px + (size_t)y * stride);
            const float* s = a.data() + (size_t)y * w * 4;
            for (int x = 0; x < w; ++x) {
                float nz = 0.5f;
                if (dither) {
                    seed = seed * 1664525u + 1013904223u;
                    nz = (seed >> 8) * (1.f / 16777216.f);   // 0..1 → ±0.5 уровня
                }
                float al = s[x * 4 + 3] + nz;
                int A = (int)(al < 0 ? 0 : (al > 255 ? 255 : al));
                for (int k = 0; k < 3; ++k) {
                    float v = s[x * 4 + k] + nz;
                    int c = (int)(v < 0 ? 0 : (v > 255 ? 255 : v));
                    d[x * 4 + k] = (uint8_t)(c > A ? A : c);   // премультипликация: цвет ≤ альфы
                }
                d[x * 4 + 3] = (uint8_t)A;
            }
        }
    });
}
