// fluid.cpp — симуляция жидкости тем Fluid / Fluid Dark (порт fluid_core.FluidSim).
//
// Модель та же: «краска» d — координата цветовой зоны [0,1); скорость = curl(ψ) от медленно
// плывущего шумового потенциала + затухающие импульсы (мышь, бит); полулагранжев перенос с
// заворотом краёв; подпитка к дрейфующему мраморному эталону; перетяжка к центрам зон.
// Отличие от numpy-версии — кадр раскрашивается сразу в итоговом размере постера
// (билинейная «краска» → LUT), без промежуточного растяжения Qt и в несколько потоков.
#include "fx_common.h"

#include <random>

namespace {

using namespace fx;

constexpr int LAT = 256;

static inline float lat_at(const float* lat, int x, int y) { return lat[(y & 255) * LAT + (x & 255)]; }

static inline float vnoise(const float* lat, float x, float y) {
    float fx = std::floor(x), fy = std::floor(y);
    int xi = (int)fx, yi = (int)fy;
    float xf = x - fx, yf = y - fy;
    float u = xf * xf * (3.f - 2.f * xf), v = yf * yf * (3.f - 2.f * yf);
    float a = lat_at(lat, xi, yi), b = lat_at(lat, xi + 1, yi);
    float c = lat_at(lat, xi, yi + 1), d = lat_at(lat, xi + 1, yi + 1);
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v;
}

static inline float fbm(const float* lat, float x, float y, int oct) {
    float s = 0.f, amp = 0.5f, f = 1.f, tot = 0.f;
    for (int i = 0; i < oct; ++i) {
        s += amp * vnoise(lat, x * f, y * f);
        tot += amp;
        amp *= 0.5f;
        f *= 2.f;
    }
    return s / tot;
}

static inline float lerp_wrap(float a, float b, float t) {
    float d = b - a;
    d -= std::nearbyint(d);
    return a + t * d;
}

static inline float wrap1(float v) { return v - std::floor(v); }

struct Fluid {
    int gw = 0, gh = 0;
    std::vector<float> d, target, vx, vy, psi, tmp;
    std::vector<float> lat1, lat2;
    float scale = 1.f, warp = 3.f, bands = 1.5f, off[6] = {0};
    float t = 0.f, level = 0.f, prev_level = 0.f, drift_ang = 0.f, unit = 1.f;
    int psi_age = 0, target_age = 0;
    bool psi_ok = false;
    uint32_t lut[1024];
    std::mt19937 rng{12345};

    static constexpr int BANDS = 8;
    static constexpr float FEED_RATE = 0.030f, DRIFT_SPEED = 0.08f;
    static constexpr int TARGET_EVERY = 45;

    Fluid() {
        for (int i = 0; i < 1024; ++i) lut[i] = 0xFF000000u | (i / 4) * 0x010101u;
        lat1.assign(LAT * LAT, 0.5f);
        lat2.assign(LAT * LAT, 0.5f);
    }

    void alloc(int w, int h) {
        gw = w;
        gh = h;
        size_t n = (size_t)w * h;
        d.assign(n, 0.f);
        target.assign(n, 0.f);
        vx.assign(n, 0.f);
        vy.assign(n, 0.f);
        psi.assign(n, 0.f);
        tmp.assign(n, 0.f);
        unit = gh * (9.f / 16.f);
        psi_ok = false;
        psi_age = 0;
        target_age = 0;
    }

    // мраморное поле (domain-warped fBm + выравнивание по рангу)
    void marble(std::vector<float>& out, float drift) {
        const float dx_[6] = {0.11f, -0.07f, 0.05f, 0.09f, -0.06f, 0.04f};
        float o[6];
        for (int i = 0; i < 6; ++i) o[i] = off[i] + drift * dx_[i];
        const float* L = lat1.data();
        float sc = scale, wp = warp, inv = 1.f / unit;
        parallel_rows(gh, [&](int y0, int y1) {
            for (int yy = y0; yy < y1; ++yy) {
                float y = yy * inv * sc + drift * 0.21f;
                for (int xx = 0; xx < gw; ++xx) {
                    float x = xx * inv * sc + drift * 0.37f;
                    float q1 = fbm(L, x + o[0], y + o[1], 3);
                    float q2 = fbm(L, x + o[2] + 5.2f, y + o[3] + 1.3f, 3);
                    float r1 = fbm(L, x + wp * q1 + 1.7f, y + wp * q2 + 9.2f, 3);
                    float r2 = fbm(L, x + wp * q1 + 8.3f, y + wp * q2 + 2.8f, 3);
                    out[(size_t)yy * gw + xx] = fbm(L, x + wp * r1 + o[4], y + wp * r2 + o[5], 3);
                }
            }
        }, 4);
        // ранги: всем зонам — равная доля площади
        size_t n = out.size();
        std::vector<int> idx(n);
        for (size_t i = 0; i < n; ++i) idx[i] = (int)i;
        std::stable_sort(idx.begin(), idx.end(), [&](int a, int b) { return out[a] < out[b]; });
        std::vector<float> r(n);
        float invn = 1.f / (float)n;
        for (size_t k = 0; k < n; ++k) r[idx[k]] = k * invn;
        for (size_t i = 0; i < n; ++i) out[i] = wrap1(r[i] * bands);
    }

    void reset_dye() {
        marble(d, 0.f);
        target = d;
        target_age = 0;
        std::fill(vx.begin(), vx.end(), 0.f);
        std::fill(vy.begin(), vy.end(), 0.f);
        psi_ok = false;
        psi_age = 0;
    }

    void regrid(int w, int h) {
        if (w == gw && h == gh) return;
        std::vector<float> old = d;
        int ow = gw, oh = gh;
        alloc(w, h);
        for (int y = 0; y < h; ++y) {
            int sy = std::min((int)((long long)y * oh / h), oh - 1);
            for (int x = 0; x < w; ++x) {
                int sx = std::min((int)((long long)x * ow / w), ow - 1);
                d[(size_t)y * w + x] = old[(size_t)sy * ow + sx];
            }
        }
        marble(target, t * DRIFT_SPEED);
    }

    void splash(float gx, float gy, float power) {
        float sig = std::max(4.f, std::min((float)gw, (float)gh) / 7.f);
        float amp = std::min(50.f, 16.f * power + level * 26.f);
        float k2 = 1.f / (2.f * sig * sig), is = 1.f / sig;
        int r = (int)(sig * 4.f) + 1;
        int x0 = std::max(0, (int)gx - r), x1 = std::min(gw, (int)gx + r + 1);
        int y0 = std::max(0, (int)gy - r), y1 = std::min(gh, (int)gy + r + 1);
        for (int y = y0; y < y1; ++y)
            for (int x = x0; x < x1; ++x) {
                float ddx = x - gx, ddy = y - gy;
                float g = std::exp(-(ddx * ddx + ddy * ddy) * k2);
                float rx = ddx * is, ry = ddy * is;
                size_t i = (size_t)y * gw + x;
                vx[i] += g * rx * amp + g * ry * amp * 0.6f;
                vy[i] += g * ry * amp - g * rx * amp * 0.6f;
            }
    }

    void step(float dt) {
        dt = std::min(0.05f, std::max(0.001f, dt));
        t += dt;
        const int W = gw, H = gh;
        const float inv = 1.f / unit;

        if (!psi_ok || ++psi_age >= 6) {
            psi_age = 0;
            psi_ok = true;
            float ox1 = 3.1f + 1.9f * std::sin(t * 0.190f), oy1 = 7.7f + 1.9f * std::cos(t * 0.150f);
            float ox2 = 11.3f + 2.6f * std::sin(t * 0.110f + 1.3f), oy2 = 5.9f + 2.6f * std::cos(t * 0.130f + 0.4f);
            const float *L1 = lat1.data(), *L2 = lat2.data();
            parallel_rows(H, [&](int y0, int y1) {
                for (int y = y0; y < y1; ++y) {
                    float yn = y * inv;
                    for (int x = 0; x < W; ++x) {
                        float xn = x * inv;
                        psi[(size_t)y * W + x] = fbm(L1, xn * 0.55f + ox1, yn * 0.55f + oy1, 2) +
                                                 0.35f * fbm(L2, xn * 1.35f + ox2, yn * 1.35f + oy2, 1);
                    }
                }
            });
        }
        float s = 0.13f * H * (1.f + level * 2.4f);
        float ang = drift_ang + 0.9f * std::sin(t * 0.047f);
        float m = 0.055f * H * (1.f + level * 1.5f);
        float mdx = m * std::cos(ang), mdy = m * std::sin(ang) * 0.7f;

        if (++target_age >= TARGET_EVERY) {
            target_age = 0;
            marble(target, t * DRIFT_SPEED);
        }
        float feed = FEED_RATE * dt * 30.f;

        parallel_rows(H, [&](int y0, int y1) {
            for (int y = y0; y < y1; ++y) {
                int yp = (y + 1) % H, ym = (y + H - 1) % H;
                for (int x = 0; x < W; ++x) {
                    int xp = (x + 1) % W, xm = (x + W - 1) % W;
                    size_t i = (size_t)y * W + x;
                    float vX = (psi[(size_t)yp * W + x] - psi[(size_t)ym * W + x]) * s + vx[i] + mdx;
                    float vY = -(psi[(size_t)y * W + xp] - psi[(size_t)y * W + xm]) * s + vy[i] + mdy;
                    float px = x - vX * dt, py = y - vY * dt;
                    px -= W * std::floor(px / W);
                    py -= H * std::floor(py / H);
                    int x0 = std::min((int)px, W - 1), yy0 = std::min((int)py, H - 1);
                    float fx = px - x0, fy = py - yy0;
                    int x1 = (x0 + 1) % W, yy1 = (yy0 + 1) % H;
                    float top = lerp_wrap(d[(size_t)yy0 * W + x0], d[(size_t)yy0 * W + x1], fx);
                    float bot = lerp_wrap(d[(size_t)yy1 * W + x0], d[(size_t)yy1 * W + x1], fx);
                    float v = wrap1(lerp_wrap(top, bot, fy));
                    float diff = target[i] - v;
                    diff -= std::nearbyint(diff);
                    v = wrap1(v + diff * feed);
                    float center = (std::floor(v * BANDS) + 0.5f) / BANDS;
                    tmp[i] = wrap1(v + 0.035f * (center - v));
                }
            }
        });
        d.swap(tmp);

        float dec = std::exp(-dt * 1.7f);
        for (size_t i = 0; i < vx.size(); ++i) {
            vx[i] *= dec;
            vy[i] *= dec;
        }
        if (level - prev_level > 0.10f) {
            std::uniform_real_distribution<float> ux(W * 0.15f, W * 0.85f), uy(H * 0.15f, H * 0.85f);
            float gx = ux(rng), gy = uy(rng);
            splash(gx, gy, 0.6f + level);
        }
        prev_level = level;
    }

    // кадр out_w×out_h: краска билинейно (по кольцу) растягивается и раскрашивается LUT
    void render(uint32_t* out, int ow, int oh, int stride) {
        if (ow < 1 || oh < 1) return;
        const int W = gw, H = gh;
        std::vector<int> xi0(ow), xi1(ow);
        std::vector<float> xf(ow);
        float kx = ow > 1 ? (float)(W - 1) / (ow - 1) : 0.f;
        for (int x = 0; x < ow; ++x) {
            float s = x * kx;
            int i = std::min((int)s, W - 1);
            xi0[x] = i;
            xi1[x] = std::min(i + 1, W - 1);
            xf[x] = s - i;
        }
        float ky = oh > 1 ? (float)(H - 1) / (oh - 1) : 0.f;
        parallel_rows(oh, [&](int y0, int y1) {
            for (int y = y0; y < y1; ++y) {
                float s = y * ky;
                int j = std::min((int)s, H - 1);
                int j1 = std::min(j + 1, H - 1);
                float fy = s - j;
                const float* r0 = d.data() + (size_t)j * W;
                const float* r1 = d.data() + (size_t)j1 * W;
                uint32_t* o = out + (size_t)y * stride;
                for (int x = 0; x < ow; ++x) {
                    float top = lerp_wrap(r0[xi0[x]], r0[xi1[x]], xf[x]);
                    float bot = lerp_wrap(r1[xi0[x]], r1[xi1[x]], xf[x]);
                    float v = wrap1(lerp_wrap(top, bot, fy));
                    int idx = (int)(v * 1023.999f);
                    idx = idx < 0 ? 0 : (idx > 1023 ? 1023 : idx);
                    o[x] = lut[idx];
                }
            }
        });
    }
};

}  // namespace

FX_API void* fluid_new() { return new Fluid(); }

FX_API void fluid_free(void* h) { delete (Fluid*)h; }

// lat1/lat2 — решётки шума 256×256 (как fluid_core._lattice), params — scale, warp, bands, off[6]
FX_API void fluid_set_seed(void* h, const float* lat1, const float* lat2, const float* params, float drift_ang,
                           int reset) {
    Fluid* f = (Fluid*)h;
    std::memcpy(f->lat1.data(), lat1, sizeof(float) * LAT * LAT);
    std::memcpy(f->lat2.data(), lat2, sizeof(float) * LAT * LAT);
    f->scale = params[0];
    f->warp = params[1];
    f->bands = params[2];
    for (int i = 0; i < 6; ++i) f->off[i] = params[3 + i];
    f->drift_ang = drift_ang;
    f->rng.seed((uint32_t)(params[0] * 1e6f) ^ 0x9E3779B9u);
    if (reset && f->gw > 0) f->reset_dye();
}

FX_API void fluid_resize(void* h, int gw, int gh) {
    Fluid* f = (Fluid*)h;
    f->alloc(gw, gh);
    f->reset_dye();
}

FX_API void fluid_regrid(void* h, int gw, int gh) { ((Fluid*)h)->regrid(gw, gh); }

FX_API void fluid_set_lut(void* h, const uint32_t* lut) { std::memcpy(((Fluid*)h)->lut, lut, sizeof(uint32_t) * 1024); }

FX_API void fluid_splash(void* h, float gx, float gy, float power) { ((Fluid*)h)->splash(gx, gy, power); }

FX_API void fluid_step(void* h, float dt, float level) {
    Fluid* f = (Fluid*)h;
    f->level = level;
    f->step(dt);
}

FX_API void fluid_render(void* h, uint32_t* out, int ow, int oh, int stride_px) {
    ((Fluid*)h)->render(out, ow, oh, stride_px);
}

FX_API float fluid_time(void* h) { return ((Fluid*)h)->t; }

// поле краски (gw×gh float) — для отладки/сравнения с numpy
FX_API void fluid_get_dye(void* h, float* out) {
    Fluid* f = (Fluid*)h;
    std::memcpy(out, f->d.data(), sizeof(float) * f->d.size());
}
