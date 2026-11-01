// glow.cpp — сияние «Моей волны» темы Echoes Music (порт ya_wave.GlowField.render).
//
// Поле считается аналитически в каждой точке (ядро из гауссовых пятен, лепестки, лучи, ореол,
// ударные кольца, прожилки света) на сетке ~1/2.5 окна и сразу растягивается до размера окна.
// «Плавание» центра входит в координаты выборки — картинку не надо сдвигать при рисовании,
// GUI просто копирует готовый кадр 1:1.
#include "fx_common.h"

namespace {
using namespace fx;

struct GlowParams {
    int W, H, x0;            // окно и первая колонка сияния (левее — чёрный)
    int gw, gh;              // сетка расчёта (по колонкам [x0, W))
    float aspect, cxo, cyo;  // центр сияния в координатах поля
    float ox, oy;            // «плавание» (доли окна)
    float T, energy, kick, beat;
    float core[3], halo[3], ray[3];
    int nblobs;
    float blobs[8 * 3];      // bx, by, 1/r2
    int nrings;
    float rings[8 * 2];      // радиус, сила
};

static inline float field(float px, float py, float t) {
    float qx = px + 0.55f * fsin(py * 1.7f + t * 0.6f) + 0.30f * fsin(py * 3.1f - t * 0.4f);
    float qy = py + 0.55f * fsin(px * 1.9f - t * 0.5f) + 0.30f * fsin(px * 2.7f + t * 0.35f);
    return fsin(qx * 1.6f + fsin(qy * 1.3f + t * 0.3f)) + fsin(qy * 1.4f - fsin(qx * 1.1f - t * 0.25f));
}

static inline float veins(float px, float py, float t) {
    float f = fsin(field(px, py, t) * 2.4f);
    float g = fsin(field(px * 1.25f + 5.f, py * 1.25f - 3.f, t * 1.2f + 7.f) * 2.f);
    const float iw = 1.f / 0.16f;
    float f2 = (f * iw) * (f * iw), g2 = (g * iw) * (g * iw);
    return fexp(-f2) * 0.6f + fexp(-g2) * 0.42f + fexp(-f2 * (1.f / 16.f)) * 0.35f + fexp(-g2 * (1.f / 16.f)) * 0.25f;
}

}  // namespace

FX_API void glow_render(const GlowParams* P, uint32_t* out, int stride_px) {
    const GlowParams p = *P;
    const int cols = p.W - p.x0;
    if (cols <= 0 || p.H <= 0 || p.gw < 2 || p.gh < 2) return;
    const float T = p.T, E = p.energy, kick = p.kick, beat = p.beat;
    // фазы гармоник угла: sn(k, ph) = sin(k·a)·cos(ph) + cos(k·a)·sin(ph)
    auto cs = [](float ph, float* c, float* s) { *c = std::cos(ph); *s = std::sin(ph); };
    float c5, s5, c3, s3, c7, s7, c2, s2, c9, s9, c6, s6;
    cs(T * 0.65f, &c5, &s5);
    cs(-T * 0.95f + 1.f, &c3, &s3);
    cs(T * 1.5f, &c7, &s7);
    cs(-T * 0.4f, &c2, &s2);
    cs(-T * 2.f, &c9, &s9);
    cs(-T * 0.5f, &c6, &s6);
    const float lobe5 = 0.17f + 0.07f * E;
    const float ct13 = std::cos(T * 1.3f), st13 = std::sin(T * 1.3f);
    const float rh = 1.15f + 0.10f * beat + 0.25f * kick;
    const float k_halo = -1.2f / (rh * rh);
    const float rl = 1.6f + 0.3f * kick;
    const float k_rl = -1.f / (rl * rl);
    const float rayl_k = 0.35f + 0.35f * E;
    const float halo_m = 1.2f + 0.4f * kick;
    const float inner_k = 0.35f + 0.5f * kick;
    const float blob_norm = 1.f / (p.nblobs * 0.42f);
    const float vt = T * 0.45f;
    float vein_c[3], ring_c[3], inner_c[3];
    for (int k = 0; k < 3; ++k) {
        vein_c[k] = (p.core[k] * 0.55f + 0.45f) * (0.20f + 0.10f * E);
        ring_c[k] = p.ray[k] * 0.7f + p.halo[k] * 0.5f;
        inner_c[k] = (1.f - p.core[k]) * 0.6f;
    }

    std::vector<uint32_t> low((size_t)p.gw * p.gh);
    const int gw = p.gw;
    uint32_t* const lowp = low.data();
    // построчные массивы: каждая стадия — простой цикл без ветвлений, GCC разворачивает его в SIMD.
    // Захват копиями ([=]) — иначе компилятор боится, что записи в массивы меняют параметры
    parallel_rows(p.gh, [=](int j0, int j1) {
        // локальные копии: регистры, а не поля замыкания (иначе SIMD не включается)
        const float L_c5 = c5, L_s5 = s5, L_c3 = c3, L_s3 = s3, L_c7 = c7, L_s7 = s7, L_c2 = c2, L_s2 = s2, L_c9 = c9, L_s9 = s9, L_c6 = c6, L_s6 = s6, L_lobe5 = lobe5, L_ct13 = ct13, L_st13 = st13, L_k_halo = k_halo, L_k_rl = k_rl, L_rayl_k = rayl_k, L_halo_m = halo_m, L_inner_k = inner_k, L_blob_norm = blob_norm, L_vt = vt, L_T = T;
        const float L_x0 = (float)p.x0, L_W = (float)p.W, L_ox = p.ox, L_aspect = p.aspect, L_cxo = p.cxo;
        std::vector<float> buf((size_t)gw * 12);
        float *__restrict X = buf.data(), *__restrict R2a = X + gw, *__restrict R0a = R2a + gw, *__restrict Xw = R0a + gw,
              *__restrict Yw = Xw + gw, *__restrict F = Yw + gw;
        float *__restrict RS = F + gw, *__restrict HALO = RS + gw, *__restrict RAYL = HALO + gw, *__restrict SH = RAYL + gw,
              *__restrict VN = SH + gw;
        const float xk = (float)cols / gw;
        for (int j = j0; j < j1; ++j) {
            const float wy = (j + 0.5f) * p.H / p.gh;
            const float Y = (wy / p.H - p.oy) * 2.f - 1.f - p.cyo;
            const float shy = std::cos(Y * 1.8f - L_T * 0.7f);
            for (int i = 0; i < gw; ++i) {
                float wx = L_x0 + (i + 0.5f) * xk;
                float x = ((wx / L_W - L_ox) * 2.f - 1.f) * L_aspect - L_cxo;
                X[i] = x;
                float r2 = x * x + Y * Y;
                R2a[i] = r2;
                R0a[i] = std::sqrt(r2);
            }
            for (int i = 0; i < gw; ++i) {
                float x = X[i], R0 = R0a[i], R2 = R2a[i];
                float ir = 1.f / std::max(R0, 1e-6f);
                float ca = x * ir, sa = Y * ir;
                float c2a = ca * ca - sa * sa, s2a = 2.f * sa * ca;
                float c3a = c2a * ca - s2a * sa, s3a = s2a * ca + c2a * sa;
                float c4a = c3a * ca - s3a * sa, s4a = s3a * ca + c3a * sa;
                float c5a = c4a * ca - s4a * sa, s5a = s4a * ca + c4a * sa;
                float c6a = c5a * ca - s5a * sa, s6a = s5a * ca + c5a * sa;
                float c7a = c6a * ca - s6a * sa, s7a = s6a * ca + c6a * sa;
                float c9a = c7a * c2a - s7a * s2a, s9a = s7a * c2a + c7a * s2a;
                float clipR = clamp01(R0 * (1.f / 0.35f));
                float lobes = (L_lobe5 * (s5a * L_c5 + c5a * L_s5) + 0.12f * (s3a * L_c3 + c3a * L_s3) +
                               0.07f * (s7a * L_c7 + c7a * L_s7) + 0.05f * (s2a * L_c2 + c2a * L_s2)) * clipR + 1.f;
                float inv = 1.f / lobes;
                Xw[i] = x * inv;
                Yw[i] = Y * inv;
                float sinR = fsin(R0 * 2.2f), cosR = fcos(R0 * 2.2f);
                float ph = L_T * 0.45f + 1.4f * (sinR * L_ct13 - cosR * L_st13);
                float rays = 0.6f + 0.4f * (s4a * fcos(ph) + c4a * fsin(ph));
                rays *= rays;
                rays *= 0.85f + 0.15f * (s9a * L_c9 + c9a * L_s9);
                HALO[i] = fexp(R2 * L_k_halo) * (0.75f + 0.35f * rays);
                float q6 = 0.5f + 0.5f * (s6a * L_c6 + c6a * L_s6);
                q6 *= q6;
                q6 *= q6;
                RAYL[i] = fexp(R2 * L_k_rl) * q6 * L_rayl_k;
                SH[i] = 0.5f + 0.5f * shy * fsin(x * 2.2f + L_T * 0.9f);
                F[i] = 0.f;
                RS[i] = 0.f;
            }
            for (int b = 0; b < p.nblobs; ++b) {
                const float bx = p.blobs[b * 3], by = p.blobs[b * 3 + 1], ib = p.blobs[b * 3 + 2];
                for (int i = 0; i < gw; ++i) {
                    float dx = Xw[i] - bx, dy = Yw[i] - by;
                    F[i] += fexp(-(dx * dx + 1.1f * dy * dy) * ib);
                }
            }
            for (int r = 0; r < p.nrings; ++r) {
                const float rr = p.rings[r * 2], ra = p.rings[r * 2 + 1] * 0.7f;
                for (int i = 0; i < gw; ++i) {
                    float d = (R0a[i] - rr) * (1.f / 0.16f);
                    RS[i] += fexp(-(d * d)) * ra;
                }
            }
            for (int i = 0; i < gw; ++i) {
                float px = X[i] * 0.85f, py = Y * 0.85f;
                // прожилки: две «закрученные» сетки (как в шейдере телефона)
                float qx = px + 0.55f * fsin(py * 1.7f + L_vt * 0.6f) + 0.30f * fsin(py * 3.1f - L_vt * 0.4f);
                float qy = py + 0.55f * fsin(px * 1.9f - L_vt * 0.5f) + 0.30f * fsin(px * 2.7f + L_vt * 0.35f);
                float f1 = fsin((fsin(qx * 1.6f + fsin(qy * 1.3f + L_vt * 0.3f)) +
                                 fsin(qy * 1.4f - fsin(qx * 1.1f - L_vt * 0.25f))) * 2.4f);
                float ax = px * 1.25f + 5.f, ay = py * 1.25f - 3.f, t2 = L_vt * 1.2f + 7.f;
                float rx = ax + 0.55f * fsin(ay * 1.7f + t2 * 0.6f) + 0.30f * fsin(ay * 3.1f - t2 * 0.4f);
                float ry = ay + 0.55f * fsin(ax * 1.9f - t2 * 0.5f) + 0.30f * fsin(ax * 2.7f + t2 * 0.35f);
                float g1 = fsin((fsin(rx * 1.6f + fsin(ry * 1.3f + t2 * 0.3f)) +
                                 fsin(ry * 1.4f - fsin(rx * 1.1f - t2 * 0.25f))) * 2.f);
                const float iw = 1.f / 0.16f;
                float f2 = (f1 * iw) * (f1 * iw), g2 = (g1 * iw) * (g1 * iw);
                VN[i] = fexp(-f2) * 0.6f + fexp(-g2) * 0.42f + fexp(-f2 * (1.f / 16.f)) * 0.35f +
                        fexp(-g2 * (1.f / 16.f)) * 0.25f;
            }
            uint32_t* __restrict o = lowp + (size_t)j * gw;
            const float h0 = p.halo[0] * L_halo_m, h1 = p.halo[1] * L_halo_m, h2 = p.halo[2] * L_halo_m;
            const float y0 = p.ray[0], y1 = p.ray[1], y2 = p.ray[2];
            const float k0 = p.core[0], k1 = p.core[1], k2 = p.core[2];
            const float g0 = ring_c[0], g1 = ring_c[1], g2 = ring_c[2];
            const float e0 = vein_c[0], e1 = vein_c[1], e2 = vein_c[2];
            const float n0 = inner_c[0], n1 = inner_c[1], n2 = inner_c[2];
            for (int i = 0; i < gw; ++i) {
                float f = F[i] * L_blob_norm;
                float core = clamp01((f - 0.36f) * (1.f / 0.85f));
                core = core * core * (3.f - 2.f * core);
                float inner = clamp01(f - 0.95f) * L_inner_k;
                float halo = HALO[i], sh = SH[i];
                float vn = VN[i] * (0.25f + 0.75f * clamp01(halo * 1.4f) - 0.55f * core);
                const float rl = RAYL[i], rs = RS[i];
                const float cg = core * 0.92f;
                float v0 = (halo * h0 + rl * y0 + rs * g0) * (1.f - cg) + core * (k0 * (1.f - 0.25f * sh) + y0 * (0.25f * sh)) + vn * e0 + inner * n0;
                float v1 = (halo * h1 + rl * y1 + rs * g1) * (1.f - cg) + core * (k1 * (1.f - 0.25f * sh) + y1 * (0.25f * sh)) + vn * e1 + inner * n1;
                float v2 = (halo * h2 + rl * y2 + rs * g2) * (1.f - cg) + core * (k2 * (1.f - 0.25f * sh) + y2 * (0.25f * sh)) + vn * e2 + inner * n2;
                v0 = clamp01(v0); v1 = clamp01(v1); v2 = clamp01(v2);
                v0 *= 0.95f + 0.05f * v0; v1 *= 0.95f + 0.05f * v1; v2 *= 0.95f + 0.05f * v2;
                uint32_t pix = 0xFF000000u | ((uint32_t)(int)(v0 * 255.f + 0.5f) << 16) |
                               ((uint32_t)(int)(v1 * 255.f + 0.5f) << 8) | (uint32_t)(int)(v2 * 255.f + 0.5f);
                o[i] = pix;
            }
        }
    }, 2);
    // левее x0 — чистый чёрный (там список волн)
    parallel_rows(p.H, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y) {
            uint32_t* row = out + (size_t)y * stride_px;
            for (int x = 0; x < p.x0; ++x) row[x] = 0xFF000000u;
        }
    });
    upscale_u32(low.data(), p.gw, p.gh, p.gw, out + p.x0, cols, p.H, stride_px);
}
