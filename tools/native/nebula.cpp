// nebula.cpp — «Моя волна» в стиле Яндекс Музыки: многоцветная туманность на чёрном.
//
// Несколько цветных мягких «облаков» (гауссовых пятен) в координатах, закрученных двумя слоями
// плавного поля течения, — получаются живые перетекающие пятна разных оттенков; поверх — тонкие
// нити света (изолинии того же поля), редкие лучи из центра и мягкие кольца на удар баса.
// Яркость сжимается мягко (1 − e^−x), края уходят в чёрный. Поле считается на сетке ~1/3 окна в
// float и растягивается до окна билинейно в float с «зерном» ±1/255 — без ступенек на градиентах.
#include "fx_common.h"

namespace {
using namespace fx;

constexpr int MAXB = 12;
constexpr int MAXR = 6;

struct NebParams {
    int W, H, x0;              // окно (пиксели устройства) и первая колонка (левее — чёрный)
    int gw, gh;                // сетка расчёта по колонкам [x0, W)
    float cx, cy, R;           // центр и радиус туманности, пиксели устройства
    float T, flow, energy, kick;
    float gain, fil_amt, ray_amt, grain;
    float fil_col[3];
    int nblobs;
    float blobs[MAXB * 7];     // x, y, 1/r², r, g, b, сила
    int nrings;
    float rings[MAXR * 2];     // радиус, сила
    float ring_col[3];
    int seed;
    float env_r, lobe;         // граница облака: радиус (доли R) и неровность
};

// atan2 с точностью ~1e-3 рад — для лучей хватает
static inline float fatan2(float y, float x) {
    float ax = std::fabs(x), ay = std::fabs(y);
    float mx = std::max(ax, ay), mn = std::min(ax, ay);
    float a = mn / std::max(mx, 1e-12f);
    float s = a * a;
    float r = ((-0.0464964749f * s + 0.15931422f) * s - 0.327622764f) * s * a + a;
    if (ay > ax) r = 1.57079637f - r;
    if (x < 0) r = 3.14159274f - r;
    return y < 0 ? -r : r;
}

static inline uint32_t hash32(uint32_t x) {
    x ^= x >> 16;
    x *= 0x7feb352dU;
    x ^= x >> 15;
    x *= 0x846ca68bU;
    x ^= x >> 16;
    return x;
}

}  // namespace

FX_API void nebula_render(const NebParams* P, uint32_t* out, int stride_px) {
    const NebParams p = *P;
    const int cols = p.W - p.x0;
    if (cols <= 0 || p.H <= 0 || p.gw < 2 || p.gh < 2) return;
    const int gw = p.gw, gh = p.gh;
    std::vector<float> low((size_t)gw * gh * 3);
    float* const lowp = low.data();
    const float invR = 1.f / std::max(1.f, p.R);
    const float t = p.T * p.flow;
    const int nb = std::min(p.nblobs, MAXB), nr = std::min(p.nrings, MAXR);

    parallel_rows(gh, [=](int j0, int j1) {
        float bl[MAXB * 7];
        for (int k = 0; k < nb * 7; ++k) bl[k] = p.blobs[k];
        const float xk = (float)cols / gw, yk = (float)p.H / gh;
        for (int j = j0; j < j1; ++j) {
            const float v = ((j + 0.5f) * yk - p.cy) * invR;
            float* o = lowp + (size_t)j * gw * 3;
            for (int i = 0; i < gw; ++i) {
                const float u = (p.x0 + (i + 0.5f) * xk - p.cx) * invR;
                // течение: два слоя закрутки
                float w1x = u + 0.22f * fsin(v * 1.9f + t * 0.70f) + 0.11f * fsin(v * 3.7f - t * 0.50f + u * 1.3f);
                float w1y = v + 0.22f * fsin(u * 1.7f - t * 0.60f) + 0.11f * fsin(u * 3.3f + t * 0.45f - v * 1.1f);
                float w2x = w1x + 0.10f * fsin(w1y * 5.1f + t * 0.90f);
                float w2y = w1y + 0.10f * fsin(w1x * 4.7f - t * 0.80f);
                float a0 = 0.f, a1 = 0.f, a2 = 0.f;
                for (int b = 0; b < nb; ++b) {
                    const float* B = bl + b * 7;
                    float dx = (u + (w2x - u) * 0.55f) - B[0], dy = (v + (w2y - v) * 0.55f) - B[1];
                    float e = fexp(-(dx * dx + dy * dy) * B[2]) * B[6];
                    a0 += e * B[3];
                    a1 += e * B[4];
                    a2 += e * B[5];
                }
                float lum = std::max(a0, std::max(a1, a2));
                // нити света — изолинии закрученного поля, только внутри облака
                float f = fsin((fsin(w1x * 2.3f + fsin(w1y * 1.7f + t * 0.3f)) +
                                fsin(w1y * 2.1f - fsin(w1x * 1.9f - t * 0.25f))) * 2.6f);
                float fq = f * (1.f / 0.16f);
                float fw = f * (1.f / 0.55f);
                float fil = fexp(-fq * fq) * 0.6f + fexp(-fw * fw) * 0.25f;
                float lw = clamp01(lum * 1.6f);
                fil *= lw * (1.f - 0.45f * clamp01(lum - 0.7f)) * p.fil_amt;
                float in = 1.f / std::max(lum, 0.05f);
                a0 += fil * (0.4f * p.fil_col[0] + 0.6f * clamp01(a0 * in));
                a1 += fil * (0.4f * p.fil_col[1] + 0.6f * clamp01(a1 * in));
                a2 += fil * (0.4f * p.fil_col[2] + 0.6f * clamp01(a2 * in));
                float r = std::sqrt(u * u + v * v);
                for (int k = 0; k < nr; ++k) {                // кольца ударов
                    float d = (r - p.rings[k * 2]) * (1.f / 0.14f);
                    float e = fexp(-d * d) * p.rings[k * 2 + 1];
                    a0 += e * p.ring_col[0];
                    a1 += e * p.ring_col[1];
                    a2 += e * p.ring_col[2];
                }
                // цельное облако: неровная дышащая граница (супергаусс 4-й степени) — всё, что за ней, тает
                float ang = fatan2(v, u);
                float rs = p.env_r * (1.f + p.lobe * (0.55f * fsin(3.f * ang + t * 0.30f) + 0.35f * fsin(5.f * ang - t * 0.42f) +
                                                      0.25f * fsin(2.f * ang + t * 0.21f + 1.7f)));
                rs = std::max(rs, 0.05f);
                float e2 = r / rs;
                e2 *= e2;
                float vig = fexp(-e2 * e2 * 1.1f);
                const float g = p.gain;
                float b0 = (1.f - fexp(-a0 * g)) * vig, b1 = (1.f - fexp(-a1 * g)) * vig, b2 = (1.f - fexp(-a2 * g)) * vig;
                // сияние по граням: тонкие светящиеся контуры вдоль края облака (по закрученным координатам —
                // волнистые), местами прерываются и текут
                float rw = std::sqrt(w1x * w1x + w1y * w1y) / rs;
                float m1 = 0.5f + 0.5f * fsin(2.f * ang + t * 0.35f + 1.3f * fsin(3.f * ang - t * 0.2f));
                float m2 = 0.5f + 0.5f * fsin(3.f * ang - t * 0.28f + 2.1f);
                float d1 = (rw - 0.86f) * (1.f / 0.045f), d1w = (rw - 0.86f) * (1.f / 0.17f);
                float d2 = (rw - 1.04f) * (1.f / 0.04f), d2w = (rw - 1.04f) * (1.f / 0.14f);
                float rim = (fexp(-d1 * d1) * 0.45f + fexp(-d1w * d1w) * 0.30f) * (0.15f + 0.85f * m1 * m1) +
                            (fexp(-d2 * d2) * 0.22f + fexp(-d2w * d2w) * 0.14f) * m2 * m2 * m2;
                rim *= p.fil_amt * 2.2f;
                // лучи света: тонкие, длинные, в основном вбок (как на экране Яндекса), уходят за край облака
                float ray = 0.f;
                if (p.ray_amt > 0.f) {
                    float q = 0.5f + 0.5f * fsin(ang * 5.f + 2.f * fsin(ang * 2.f + t * 0.10f) + t * 0.03f);
                    q *= q; q *= q; q *= q; q *= q; q *= q;                   // q^32 — узкие
                    float q2 = 0.5f + 0.5f * fsin(ang * 11.f - 1.5f * fsin(ang * 3.f - t * 0.08f) - t * 0.05f);
                    q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2;
                    float ca = std::fabs(u) / std::max(r, 1e-4f);
                    float hw = ca * ca; hw *= hw; hw *= hw; hw *= ca;          // |cos|^9 — почти только вбок
                    float dr = r - rs * 0.65f;
                    float fall = dr > 0.f ? clamp01(dr * 2.f) * fexp(-dr * 0.85f) : 0.f;
                    ray = (q + 0.35f * q2) * hw * fall * p.ray_amt * 2.2f;
                }
                float x0c = 0.55f + 0.45f * p.fil_col[0], x1c = 0.55f + 0.45f * p.fil_col[1], x2c = 0.55f + 0.45f * p.fil_col[2];
                float ex0 = rim * (0.3f * x0c + 0.7f * clamp01(a0 * in + 0.1f)) + ray * (0.6f * x0c + 0.4f * clamp01(a0 * in));
                float ex1 = rim * (0.3f * x1c + 0.7f * clamp01(a1 * in + 0.1f)) + ray * (0.6f * x1c + 0.4f * clamp01(a1 * in));
                float ex2 = rim * (0.3f * x2c + 0.7f * clamp01(a2 * in + 0.1f)) + ray * (0.6f * x2c + 0.4f * clamp01(a2 * in));
                o[i * 3 + 0] = 1.f - (1.f - b0) * fexp(-ex0 * 1.4f);
                o[i * 3 + 1] = 1.f - (1.f - b1) * fexp(-ex1 * 1.4f);
                o[i * 3 + 2] = 1.f - (1.f - b2) * fexp(-ex2 * 1.4f);
            }
        }
    }, 2);

    // растяжение до окна в float + зерно (без полос на тёмных градиентах)
    const float sx = (float)gw / cols, sy = (float)gh / p.H;
    const float grain = p.grain;
    const uint32_t seed = (uint32_t)p.seed * 2654435761u;
    parallel_rows(p.H, [=](int y0, int y1) {
        for (int y = y0; y < y1; ++y) {
            uint32_t* row = out + (size_t)y * stride_px;
            for (int x = 0; x < p.x0; ++x) row[x] = 0xFF000000u;
            float fy = (y + 0.5f) * sy - 0.5f;
            fy = fy < 0.f ? 0.f : (fy > gh - 1.001f ? gh - 1.001f : fy);
            int iy = (int)fy;
            float ty = fy - iy;
            const float* r0 = lowp + (size_t)iy * gw * 3;
            const float* r1 = r0 + (iy + 1 < gh ? gw * 3 : 0);
            for (int x = 0; x < cols; ++x) {
                float fx = (x + 0.5f) * sx - 0.5f;
                fx = fx < 0.f ? 0.f : (fx > gw - 1.001f ? gw - 1.001f : fx);
                int ix = (int)fx;
                float tx = fx - ix;
                int i0 = ix * 3, i1 = (ix + 1 < gw ? ix + 1 : ix) * 3;
                float w00 = (1.f - tx) * (1.f - ty), w10 = tx * (1.f - ty), w01 = (1.f - tx) * ty, w11 = tx * ty;
                float c0 = r0[i0] * w00 + r0[i1] * w10 + r1[i0] * w01 + r1[i1] * w11;
                float c1 = r0[i0 + 1] * w00 + r0[i1 + 1] * w10 + r1[i0 + 1] * w01 + r1[i1 + 1] * w11;
                float c2 = r0[i0 + 2] * w00 + r0[i1 + 2] * w10 + r1[i0 + 2] * w01 + r1[i1 + 2] * w11;
                uint32_t h = hash32((uint32_t)(x + p.x0) * 73856093u ^ (uint32_t)y * 19349663u ^ seed);
                float n = ((float)(h & 0xFFFF) * (1.f / 65535.f) + (float)(h >> 16) * (1.f / 65535.f) - 1.f) * grain;
                int R = (int)(c0 * 255.f + n * 255.f + 0.5f), G = (int)(c1 * 255.f + n * 255.f + 0.5f),
                    B = (int)(c2 * 255.f + n * 255.f + 0.5f);
                R = R < 0 ? 0 : (R > 255 ? 255 : R);
                G = G < 0 ? 0 : (G > 255 ? 255 : G);
                B = B < 0 ? 0 : (B > 255 ? 255 : B);
                row[p.x0 + x] = 0xFF000000u | ((uint32_t)R << 16) | ((uint32_t)G << 8) | (uint32_t)B;
            }
        }
    });
}
