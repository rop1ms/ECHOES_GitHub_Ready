// clipfx.cpp — эффекты «Режима клипа» (порт clip_fx.ClipFX.process) над кадром видео.
//
// Кадр — 32-битные пиксели QImage RGB32 (байты B, G, R, 0xFF), как у numpy-версии каналы
// 0 = B, 1 = G, 2 = R. Случайные решения кадра (тряска, строки глитча, дрожание VHS…) и
// маленькие таблицы (LUT кислоты, матрица радуги, смещения волн/плавления) готовит Python —
// здесь только попиксельная работа, в несколько потоков и прямо в памяти кадра.
#include "fx_common.h"

namespace {
using namespace fx;

enum {
    E_KALEIDO, E_MIRROR, E_WAVES, E_MELT, E_DROSTE, E_ZOOM, E_SHAKE, E_PIXEL, E_GLITCH, E_RGB, E_ECHO,
    E_RAINBOW, E_ACID, E_THERMAL, E_EDGES, E_POSTER, E_SOLAR, E_MATRIX, E_VHS, E_TRAILS, E_TUNNEL,
    E_INVERT, E_FLICKER, E_STROBE
};

struct ClipParams {
    int w, h;
    uint32_t on;                 // биты E_*
    float t, k, bass, beat;
    int shake_dx, shake_dy;
    float zoom_s;
    int kal_n;
    float kal_rot, kal_zoom;
    int droste_flip;
    int pixel_b;
    int glitch_n;
    int glitch[16 * 5];          // y, высота, сдвиг, канал1, канал2 (канал1 < 0 — без обмена)
    int rgb_s, echo_s;
    float hue_m[9];              // матрица радуги (B, G, R)
    float edge_gain, edge_col[3];
    int poster_step, solar_thr;
    int vhs_shift, vhs_band_y, vhs_band_h, vhs_band_v, vhs_noise_max;
    uint32_t seed;
    float tunnel_s, tunnel_deg, tunnel_keep;
    float trails_keep;
    int invert;                  // чётность инверсий (негатив на бит + мигание)
    float strobe_mix;
    int strobe_v;                // −1 — без стробоскопа
};

static inline uint8_t ch(uint32_t p, int c) { return (uint8_t)(p >> (8 * c)); }
static inline uint32_t pack(int b, int g, int r) {
    b = b < 0 ? 0 : (b > 255 ? 255 : b);
    g = g < 0 ? 0 : (g > 255 ? 255 : g);
    r = r < 0 ? 0 : (r > 255 ? 255 : r);
    return 0xFF000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)b;
}
static inline float lum(uint32_t p) { return ch(p, 0) * 0.114f + ch(p, 1) * 0.587f + ch(p, 2) * 0.299f; }

struct Frame {
    int w, h;
    std::vector<uint32_t> a, b;   // текущий и рабочий
    uint32_t* cur() { return a.data(); }
    uint32_t* tmp() { return b.data(); }
    void swap() { a.swap(b); }
};

template <class F>
static void remap(Frame& f, F&& src_of) {   // b[y][x] = a[src_of(x, y)]; потом обмен
    const uint32_t* A = f.cur();
    uint32_t* B = f.tmp();
    const int w = f.w;
    parallel_rows(f.h, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y)
            for (int x = 0; x < w; ++x) {
                int sx, sy;
                src_of(x, y, sx, sy);
                B[(size_t)y * w + x] = A[(size_t)sy * w + sx];
            }
    });
    f.swap();
}

template <class F>
static void pointwise(Frame& f, F&& fn) {
    uint32_t* A = f.cur();
    const int w = f.w;
    parallel_rows(f.h, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y)
            for (int x = 0; x < w; ++x) A[(size_t)y * w + x] = fn(A[(size_t)y * w + x], x, y);
    });
}

static inline int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
static inline int wrapi(int v, int n) { v %= n; return v < 0 ? v + n : v; }

struct Roto {   // поворот+масштаб вокруг центра (синус/косинус — один раз, а не на каждый пиксель)
    float ca, sa, inv, cx, cy;
    int w, h;
    Roto(int w_, int h_, float scale, float deg) : w(w_), h(h_) {
        float a = deg * 3.14159265f / 180.f;
        ca = std::cos(a);
        sa = std::sin(a);
        inv = 1.f / scale;
        cx = w / 2.f;
        cy = h / 2.f;
    }
    inline void src(int x, int y, int& sx, int& sy) const {
        float dx = (x - cx) * inv, dy = (y - cy) * inv;
        float fx = cx + dx * ca - dy * sa, fy = cy + dx * sa + dy * ca;
        fx = fx < 0 ? 0 : (fx > w - 1 ? w - 1 : fx);
        fy = fy < 0 ? 0 : (fy > h - 1 ? h - 1 : fy);
        sx = (int)fx;
        sy = (int)fy;
    }
};

// atan2 с ошибкой ~1e-4 рад (полином на [0, 1] + октанты)
static inline float fatan2(float y, float x) {
    float ax = std::fabs(x), ay = std::fabs(y);
    float mx = std::max(ax, ay), mn = std::min(ax, ay);
    float a = mn / (mx + 1e-30f);
    float s = a * a;
    float r = ((-0.0464964749f * s + 0.15931422f) * s - 0.327622764f) * s * a + a;
    if (ay > ax) r = 1.57079637f - r;
    if (x < 0) r = 3.14159274f - r;
    return y < 0 ? -r : r;
}

static void rotozoom_src(int w, int h, float scale, float deg, int x, int y, int& sx, int& sy) {
    float a = deg * 3.14159265f / 180.f, ca = std::cos(a), sa = std::sin(a);
    float cx = w / 2.f, cy = h / 2.f;
    float dx = (x - cx) / scale, dy = (y - cy) / scale;
    float fx = cx + dx * ca - dy * sa, fy = cy + dx * sa + dy * ca;
    fx = fx < 0 ? 0 : (fx > w - 1 ? w - 1 : fx);
    fy = fy < 0 ? 0 : (fy > h - 1 ? h - 1 : fy);
    sx = (int)fx;
    sy = (int)fy;
}

}  // namespace

// px — кадр (stride в пикселях), обрабатывается на месте. prev — буфер «памяти кадров» w×h
// (шлейф/туннель; содержимое обновляется), prev_valid — есть ли в нём прошлый кадр.
// wave_dx[h], wave_dy[w], melt[w] — смещения; acid_lut[3*256]; thermal/matrix — 256 цветов;
// vhs_jit[h] — дрожание строк.
FX_API void clipfx_process(uint32_t* px, int stride, const ClipParams* P, uint32_t* prev, int prev_valid,
                           const int* wave_dx, const int* wave_dy, const int* melt, const uint8_t* acid_lut,
                           const uint32_t* thermal, const uint32_t* matrix, const int* vhs_jit) {
    const ClipParams p = *P;
    const int w = p.w, h = p.h;
    if (w < 2 || h < 2) return;
    Frame f;
    f.w = w;
    f.h = h;
    f.a.resize((size_t)w * h);
    f.b.resize((size_t)w * h);
    for (int y = 0; y < h; ++y) std::memcpy(f.a.data() + (size_t)y * w, px + (size_t)y * stride, (size_t)w * 4);
    auto on = [&](int e) { return (p.on >> e) & 1u; };

    // ── геометрия ──
    if (on(E_SHAKE) && (p.shake_dx || p.shake_dy))
        remap(f, [&](int x, int y, int& sx, int& sy) { sx = wrapi(x - p.shake_dx, w); sy = wrapi(y - p.shake_dy, h); });
    if (on(E_ZOOM)) {
        const Roto R(w, h, p.zoom_s, 0.f);
        remap(f, [&](int x, int y, int& sx, int& sy) { R.src(x, y, sx, sy); });
    }
    if (on(E_KALEIDO)) {
        const float seg = 2.f * 3.14159265f / p.kal_n;
        remap(f, [&](int x, int y, int& sx, int& sy) {
            float dx = x - w / 2.f, dy = y - h / 2.f;
            float rr = std::sqrt(dx * dx + dy * dy), th = fatan2(dy, dx);
            float m = th + p.kal_rot;
            m -= seg * std::floor(m / seg);
            float tt = std::fabs(m - seg / 2.f);
            sx = clampi((int)(w / 2.f + rr * p.kal_zoom * fcos(tt)), 0, w - 1);
            sy = clampi((int)(h / 2.f + rr * p.kal_zoom * fsin(tt)), 0, h - 1);
        });
    }
    if (on(E_MIRROR)) {
        uint32_t* A = f.cur();
        int hw = w / 2, hh = h / 2;
        for (int y = 0; y < h; ++y)
            for (int i = 0; i < hw; ++i) A[(size_t)y * w + (w - hw) + i] = A[(size_t)y * w + (hw - 1 - i)];
        for (int i = 0; i < hh; ++i)
            std::memcpy(A + (size_t)(h - hh + i) * w, A + (size_t)(hh - 1 - i) * w, (size_t)w * 4);
    }
    if (on(E_DROSTE)) {
        const uint32_t* A = f.cur();
        uint32_t* B = f.tmp();
        std::memcpy(B, A, (size_t)w * h * 4);
        for (int lvl = 1; lvl < 5; ++lvl) {
            int fct = 1 << lvl;
            int pw = (w + fct - 1) / fct, ph = (h + fct - 1) / fct;
            if (pw < 4 || ph < 4) break;
            bool flip = ((lvl + p.droste_flip) % 2) != 0;
            int y0 = (h - ph) / 2, x0 = (w - pw) / 2;
            for (int j = 0; j < ph; ++j)
                for (int i = 0; i < pw; ++i) {
                    int si = flip ? pw - 1 - i : i, sj = flip ? ph - 1 - j : j;
                    B[(size_t)(y0 + j) * w + x0 + i] = A[(size_t)(sj * fct) * w + si * fct];
                }
        }
        f.swap();
    }
    if (on(E_WAVES)) {
        remap(f, [&](int x, int y, int& sx, int& sy) { sx = wrapi(x + wave_dx[y], w); sy = y; });
        remap(f, [&](int x, int y, int& sx, int& sy) { sx = x; sy = wrapi(y + wave_dy[x], h); });
    }
    if (on(E_MELT))
        remap(f, [&](int x, int y, int& sx, int& sy) { sx = x; sy = clampi(y - melt[x], 0, h - 1); });
    if (on(E_PIXEL) && p.pixel_b > 1) {
        const int b = p.pixel_b;
        remap(f, [&](int x, int y, int& sx, int& sy) { sx = (x / b) * b; sy = (y / b) * b; });
    }
    if (on(E_GLITCH)) {
        uint32_t* A = f.cur();
        std::vector<uint32_t> row(w);
        for (int g = 0; g < p.glitch_n && g < 16; ++g) {
            const int* G = p.glitch + g * 5;
            for (int y = std::max(0, G[0]); y < std::min(h, G[0] + G[1]); ++y) {
                uint32_t* r = A + (size_t)y * w;
                for (int x = 0; x < w; ++x) row[x] = r[wrapi(x - G[2], w)];
                if (G[3] >= 0) {
                    for (int x = 0; x < w; ++x) {
                        uint32_t q = row[x];
                        uint8_t c1 = ch(q, G[3]), c2 = ch(q, G[4]);
                        q &= ~((0xFFu << (8 * G[3])) | (0xFFu << (8 * G[4])));
                        q |= ((uint32_t)c2 << (8 * G[3])) | ((uint32_t)c1 << (8 * G[4]));
                        row[x] = q;
                    }
                }
                std::memcpy(r, row.data(), (size_t)w * 4);
            }
        }
    }
    if (on(E_RGB)) {
        const uint32_t* A = f.cur();
        uint32_t* B = f.tmp();
        const int s = p.rgb_s, s2 = p.rgb_s / 2;
        parallel_rows(h, [&](int y0, int y1) {
            for (int y = y0; y < y1; ++y)
                for (int x = 0; x < w; ++x) {
                    uint32_t b = ch(A[(size_t)y * w + wrapi(x - s, w)], 0);
                    uint32_t g = ch(A[(size_t)wrapi(y - s2, h) * w + x], 1);
                    uint32_t r = ch(A[(size_t)y * w + wrapi(x + s, w)], 2);
                    B[(size_t)y * w + x] = 0xFF000000u | (r << 16) | (g << 8) | b;
                }
        });
        f.swap();
    }
    if (on(E_ECHO)) {
        const uint32_t* A = f.cur();
        uint32_t* B = f.tmp();
        const int s = p.echo_s;
        parallel_rows(h, [&](int y0, int y1) {
            for (int y = y0; y < y1; ++y)
                for (int x = 0; x < w; ++x) {
                    uint32_t a = A[(size_t)y * w + x];
                    uint32_t l = A[(size_t)y * w + wrapi(x - s, w)];
                    uint32_t u = A[(size_t)wrapi(y + s, h) * w + x];
                    int c[3];
                    for (int k = 0; k < 3; ++k) c[k] = std::max<int>(ch(a, k), std::max(ch(l, k) / 2, ch(u, k) / 2));
                    B[(size_t)y * w + x] = pack(c[0], c[1], c[2]);
                }
        });
        f.swap();
    }

    // ── цвет ──
    if (on(E_RAINBOW)) {
        const float* m = p.hue_m;
        pointwise(f, [&](uint32_t q, int, int) {
            float b = ch(q, 0), g = ch(q, 1), r = ch(q, 2);
            return pack((int)(m[0] * b + m[1] * g + m[2] * r), (int)(m[3] * b + m[4] * g + m[5] * r),
                        (int)(m[6] * b + m[7] * g + m[8] * r));
        });
    }
    if (on(E_ACID))
        pointwise(f, [&](uint32_t q, int, int) {
            return pack(acid_lut[ch(q, 0)], acid_lut[256 + ch(q, 1)], acid_lut[512 + ch(q, 2)]);
        });
    if (on(E_THERMAL)) pointwise(f, [&](uint32_t q, int, int) { return thermal[(int)lum(q) & 255]; });
    if (on(E_MATRIX)) {
        const float gain = 1.1f + p.bass;
        pointwise(f, [&](uint32_t q, int, int y) {
            float l = lum(q);
            if (y % 3 == 1) l *= 0.55f;
            int v = (int)(l * gain);
            return matrix[v < 0 ? 0 : (v > 255 ? 255 : v)];
        });
    }
    if (on(E_EDGES)) {
        const uint32_t* A = f.cur();
        uint32_t* B = f.tmp();
        parallel_rows(h, [&](int y0, int y1) {
            for (int y = y0; y < y1; ++y)
                for (int x = 0; x < w; ++x) {
                    uint32_t q = A[(size_t)y * w + x];
                    float l = lum(q);
                    float gx = x + 1 < w ? std::fabs(lum(A[(size_t)y * w + x + 1]) - l) : 0.f;
                    float gy = y + 1 < h ? std::fabs(lum(A[(size_t)(y + 1) * w + x]) - l) : 0.f;
                    float e = std::min(255.f, (gx + gy) * p.edge_gain);
                    int c[3];
                    for (int k = 0; k < 3; ++k) c[k] = (int)std::min(255.f, e * p.edge_col[k] * (1 + p.bass) + ch(q, k) * 0.15f);
                    B[(size_t)y * w + x] = pack(c[0], c[1], c[2]);
                }
        });
        f.swap();
    }
    if (on(E_POSTER)) {
        const int st = p.poster_step;
        pointwise(f, [&](uint32_t q, int, int) {
            return pack(ch(q, 0) / st * st + st / 2, ch(q, 1) / st * st + st / 2, ch(q, 2) / st * st + st / 2);
        });
    }
    if (on(E_SOLAR)) {
        const int thr = p.solar_thr;
        pointwise(f, [&](uint32_t q, int, int) {
            int c[3];
            for (int k = 0; k < 3; ++k) {
                int v = ch(q, k);
                c[k] = v > thr ? 255 - v : v;
            }
            return pack(c[0], c[1], c[2]);
        });
    }
    if (on(E_VHS)) {
        remap(f, [&](int x, int y, int& sx, int& sy) { sx = wrapi(x + vhs_jit[y], w); sy = y; });
        const uint32_t* A = f.cur();
        uint32_t* B = f.tmp();
        const int s = p.vhs_shift;
        parallel_rows(h, [&](int y0, int y1) {
            uint32_t rs = p.seed ^ (uint32_t)(y0 * 2654435761u);
            for (int y = y0; y < y1; ++y) {
                bool band = p.vhs_band_y >= 0 && y >= p.vhs_band_y && y < p.vhs_band_y + p.vhs_band_h;
                for (int x = 0; x < w; ++x) {
                    uint32_t q = A[(size_t)y * w + x];
                    int c[3] = {ch(A[(size_t)y * w + wrapi(x - s, w)], 0), ch(q, 1), ch(q, 2)};
                    if (y % 2 == 0)
                        for (int k = 0; k < 3; ++k) c[k] = (int)(c[k] * 0.78f);
                    if (band) c[0] = c[1] = c[2] = p.vhs_band_v;
                    rs = rs * 1664525u + 1013904223u;
                    int nz = (int)((rs >> 8) % (uint32_t)std::max(1, p.vhs_noise_max)) - 10;
                    B[(size_t)y * w + x] = pack(c[0] + nz, c[1] + nz, c[2] + nz);
                }
            }
        });
        f.swap();
    }

    // ── память кадров ──
    if (on(E_TUNNEL) && prev) {
        if (prev_valid) {
            uint32_t* A = f.cur();
            const float keep = p.tunnel_keep;
            const Roto R(w, h, p.tunnel_s, p.tunnel_deg);
            parallel_rows(h, [&](int y0, int y1) {
                for (int y = y0; y < y1; ++y)
                    for (int x = 0; x < w; ++x) {
                        int sx, sy;
                        R.src(x, y, sx, sy);
                        uint32_t wq = prev[(size_t)sy * w + sx], q = A[(size_t)y * w + x];
                        int c[3];
                        for (int k = 0; k < 3; ++k) c[k] = std::max<int>(ch(q, k), (int)(ch(wq, k) * keep));
                        A[(size_t)y * w + x] = pack(c[0], c[1], c[2]);
                    }
            });
        }
        std::memcpy(prev, f.cur(), (size_t)w * h * 4);
    } else if (on(E_TRAILS) && prev) {
        if (prev_valid) {
            const float keep = p.trails_keep;
            pointwise(f, [&](uint32_t q, int x, int y) {
                uint32_t o = prev[(size_t)y * w + x];
                return pack((int)(ch(q, 0) * (1 - keep) + ch(o, 0) * keep), (int)(ch(q, 1) * (1 - keep) + ch(o, 1) * keep),
                            (int)(ch(q, 2) * (1 - keep) + ch(o, 2) * keep));
            });
        }
        std::memcpy(prev, f.cur(), (size_t)w * h * 4);
    }

    // ── вспышки ──
    if (p.invert & 1) pointwise(f, [](uint32_t q, int, int) { return (~q & 0x00FFFFFFu) | 0xFF000000u; });
    if (p.strobe_v >= 0) {
        const float mix = p.strobe_mix, vv = p.strobe_v * mix;
        pointwise(f, [&](uint32_t q, int, int) {
            return pack((int)(ch(q, 0) * (1 - mix) + vv), (int)(ch(q, 1) * (1 - mix) + vv), (int)(ch(q, 2) * (1 - mix) + vv));
        });
    }
    for (int y = 0; y < h; ++y) std::memcpy(px + (size_t)y * stride, f.a.data() + (size_t)y * w, (size_t)w * 4);
}
