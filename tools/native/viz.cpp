// viz.cpp — фон MilkdropVisualizer (visualizer.py): плазма с доменным варпом + «обратная связь»
// (прошлый кадр плазмы, увеличенный и повёрнутый, под новым) + виньетка + скруглённые углы.
// Раньше это были numpy-плазма и два полноэкранных сглаженных растяжения QPainter в GUI-потоке.
#include "fx_common.h"

namespace {
using namespace fx;

struct PlasmaParams {
    int pw, ph;
    float t, eb, em, eh, warp, swirl, hue_speed;
    float ph0, ph1, ph2, ph3, ph4;   // фазы слоёв
};

struct ComposeParams {
    int W, H;              // выход (пиксели устройства)
    int pw, ph;            // размер плазмы
    float zoom, rot_deg;   // прошлый кадр: увеличение и поворот
    float radius;          // скругление углов (пиксели устройства)
    int has_prev;
};

static inline float hue2(float p, float q, float t) {
    t -= std::floor(t);
    if (t < 1.f / 6) return p + (q - p) * 6.f * t;
    if (t < 0.5f) return q;
    if (t < 2.f / 3) return p + (q - p) * (2.f / 3 - t) * 6.f;
    return p;
}

static inline void hsl(float h, float s, float l, float* r, float* g, float* b) {   // h в долях
    if (s <= 0.f) {
        *r = *g = *b = l;
        return;
    }
    float q = l < 0.5f ? l * (1 + s) : l + s - l * s;
    float p = 2 * l - q;
    *r = hue2(p, q, h + 1.f / 3);
    *g = hue2(p, q, h);
    *b = hue2(p, q, h - 1.f / 3);
}

}  // namespace

// плазма pw×ph → 0xAARRGGBB премультиплицированный, альфа 230 (как у numpy-версии)
FX_API void vizm_plasma(const PlasmaParams* P, uint32_t* out) {
    const PlasmaParams p = *P;
    const int pw = p.pw, ph = p.ph;
    if (pw < 2 || ph < 2) return;
    std::vector<float> v((size_t)pw * ph);
    const float t = p.t, eb = p.eb, em = p.em, eh = p.eh;
    const float warp = p.warp * (0.9f + eb * 1.3f);
    const float s1 = std::sin(t * 1.6f + p.ph0), c13 = std::cos(t * 1.3f + p.ph1);
    const float kx = 4.f * 3.14159265f / (pw - 1), ky = 4.f * 3.14159265f / (ph - 1);
    const float pi = 3.14159265f;
    parallel_rows(ph, [&](int y0, int y1) {
        for (int j = y0; j < y1; ++j) {
            float Y = j * ky;
            for (int i = 0; i < pw; ++i) {
                float X = i * kx;
                float Xw = X + fsin(Y * 0.5f + t * 0.37f) * warp;
                float Yw = Y + fcos(X * 0.5f + t * 0.31f) * warp;
                float Xw2 = Xw + fsin(Yw * 0.33f + t * 0.53f) * (warp * 0.5f);
                float Yw2 = Yw + fcos(Xw * 0.29f - t * 0.47f) * (warp * 0.5f);
                float p1 = s1 * fsin(X * 1.1f + t * 0.55f) + fsin(Y * 0.85f + t * 0.7f);
                float p2 = fsin(X * 0.75f + c13 * (1 + eb)) + fsin(Y * 1.0f + t * 0.9f + eb * 0.8f);
                float dx = Xw - pi * (1 + eb * 0.4f), dy = Yw - pi * (1 + em * 0.4f);
                float p3 = fsin(std::sqrt(dx * dx + dy * dy) * 1.1f + t * 1.9f);
                float p4 = fsin(Xw2 * 0.55f * (1 + eh * 1.5f) + t * 1.1f + p.ph2) + fcos(Yw2 * 0.65f * (1 + em * 1.2f) + t * 0.95f);
                float p5 = fsin((Xw + Yw) * 0.6f * (1 + p.swirl * 0.5f) + t * 1.4f + p.ph3 + eb * 1.2f);
                float p6 = fcos(Xw2 * 0.38f * (1 + eb * 3) - Yw2 * 0.5f + t * 1.7f + p.ph4);
                v[(size_t)j * pw + i] = (p1 + p2 + p3 + p4 + p5 + p6) / 6.f;
            }
        }
    }, 2);
    float mn = 1e30f, mx = -1e30f;
    for (float x : v) {
        mn = std::min(mn, x);
        mx = std::max(mx, x);
    }
    const float inv = 1.f / (mx - mn + 1e-8f);
    float hue_base = std::fmod(t * p.hue_speed + eb * 120.f, 360.f);
    const float S = clamp01(0.75f + em * 0.25f + eb * 0.1f);
    parallel_rows(ph, [&](int y0, int y1) {
        for (int j = y0; j < y1; ++j)
            for (int i = 0; i < pw; ++i) {
                float n = (v[(size_t)j * pw + i] - mn) * inv;
                float hh = std::fmod(hue_base + n * 300.f + em * 80.f, 360.f) / 360.f;
                float L = clamp01(0.10f + n * 0.52f + eh * 0.20f + eb * 0.08f);
                float r, g, b;
                hsl(hh, S, L, &r, &g, &b);
                const float a = 230.f / 255.f;   // премультипликация
                uint32_t R = (uint32_t)(clamp01(r) * a * 255.f + 0.5f), G = (uint32_t)(clamp01(g) * a * 255.f + 0.5f);
                uint32_t B = (uint32_t)(clamp01(b) * a * 255.f + 0.5f);
                out[(size_t)j * pw + i] = (230u << 24) | (R << 16) | (G << 8) | B;
            }
    });
}

// Кадр фона W×H: тёмная подложка, прошлая плазма (зум+поворот, 0.86), новая (0.6), виньетка,
// прозрачные скруглённые углы. out — ARGB32_Premultiplied.
FX_API void vizm_compose(const ComposeParams* P, const uint32_t* prev, const uint32_t* cur, uint32_t* out,
                         int stride_px) {
    const ComposeParams c = *P;
    const int W = c.W, H = c.H, pw = c.pw, ph = c.ph;
    const float cx = W * 0.5f, cy = H * 0.5f;
    const float r = c.rot_deg * 3.14159265f / 180.f, cr = std::cos(r), sr = std::sin(r);
    // прошлый кадр растянут до (W·zoom)×(H·zoom) и повёрнут вокруг центра
    const float sx_prev = pw / (W * c.zoom), sy_prev = ph / (H * c.zoom);
    const float sx_cur = (float)pw / W, sy_cur = (float)ph / H;
    const float vr = std::max(W, H) * 0.65f, ivr = 1.f / vr;
    const uint32_t a_prev = (uint32_t)(0.86f * 256.f), a_cur = (uint32_t)(0.6f * 256.f);
    const float R = c.radius;
    auto sample = [&](const uint32_t* img, float u, float v) -> uint32_t {   // u, v в пикселях плазмы
        u -= 0.5f;
        v -= 0.5f;
        u = u < 0 ? 0 : (u > pw - 1 ? pw - 1 : u);
        v = v < 0 ? 0 : (v > ph - 1 ? ph - 1 : v);
        int x0 = (int)u, y0 = (int)v;
        int x1 = std::min(x0 + 1, pw - 1), y1 = std::min(y0 + 1, ph - 1);
        uint32_t fx = (uint32_t)((u - x0) * 256.f), fy = (uint32_t)((v - y0) * 256.f);
        uint32_t a = img[(size_t)y0 * pw + x0], b = img[(size_t)y0 * pw + x1];
        uint32_t cc = img[(size_t)y1 * pw + x0], d = img[(size_t)y1 * pw + x1];
        uint32_t rb_t = ((a & 0xFF00FF) * (256 - fx) + (b & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
        uint32_t ag_t = (((a >> 8) & 0xFF00FF) * (256 - fx) + ((b >> 8) & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
        uint32_t rb_b = ((cc & 0xFF00FF) * (256 - fx) + (d & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
        uint32_t ag_b = (((cc >> 8) & 0xFF00FF) * (256 - fx) + ((d >> 8) & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
        return (((rb_t * (256 - fy) + rb_b * fy) >> 8) & 0xFF00FF) | ((((ag_t * (256 - fy) + ag_b * fy) >> 8) & 0xFF00FF) << 8);
    };
    auto mul = [](uint32_t p, uint32_t a) {
        return (((p & 0xFF00FF) * a >> 8) & 0xFF00FF) | (((((p >> 8) & 0xFF00FF) * a) >> 8 & 0xFF00FF) << 8);
    };
    auto over = [](uint32_t s, uint32_t d) {
        uint32_t ia = 255 - (s >> 24);
        uint32_t rb = (d & 0xFF00FF) * ia, ag = ((d >> 8) & 0xFF00FF) * ia;
        rb = ((rb + ((rb >> 8) & 0xFF00FF) + 0x800080) >> 8) & 0xFF00FF;
        ag = ((ag + ((ag >> 8) & 0xFF00FF) + 0x800080) >> 8) & 0xFF00FF;
        return s + (rb | (ag << 8));
    };
    parallel_rows(H, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y) {
            uint32_t* o = out + (size_t)y * stride_px;
            float fy = y + 0.5f - cy;
            for (int x = 0; x < W; ++x) {
                float fx = x + 0.5f - cx;
                uint32_t px = 0xFF0A0204u;                                     // тёмная подложка
                if (c.has_prev) {
                    float u = fx * cr + fy * sr, v = -fx * sr + fy * cr;       // обратный поворот
                    float hw = W * c.zoom * 0.5f, hh = H * c.zoom * 0.5f;
                    if (u >= -hw && u < hw && v >= -hh && v < hh)
                        px = over(mul(sample(prev, (u + hw) * sx_prev, (v + hh) * sy_prev), a_prev), px);
                }
                px = over(mul(sample(cur, (x + 0.5f) * sx_cur, (y + 0.5f) * sy_cur), a_cur), px);
                // виньетка: чёрный с альфой 0 в центре → 160 на 0.65·max(W, H)
                float d = std::sqrt(fx * fx + fy * fy) * ivr;
                uint32_t va = (uint32_t)(clamp01(d) * 160.f);
                px = over(va << 24, px);
                // скруглённые углы со сглаживанием
                float qx = std::max(0.f, std::max(R - (x + 0.5f), (x + 0.5f) - (W - R)));
                float qy = std::max(0.f, std::max(R - (y + 0.5f), (y + 0.5f) - (H - R)));
                if (qx > 0.f && qy > 0.f) {
                    float cov = clamp01(R - std::sqrt(qx * qx + qy * qy) + 0.5f);
                    px = mul(px, (uint32_t)(cov * 256.f));
                }
                o[x] = px;
            }
        }
    });
}

// ── сглаженные «капсулы»: отрезок с круглыми концами (кружок — при совпадающих концах) ──
// caps: n×5 float (x0, y0, x1, y1, радиус) в пикселях устройства; colors — премультиплицированные
// 0xAARRGGBB на каждую капсулу. polyline=1 — одна линия из многих отрезков: покрытие берётся
// максимумом (стыки не «пухнут»), цвет — colors[0].
FX_API void fx_capsules(uint32_t* out, int W, int H, int stride, const float* caps, const uint32_t* colors, int n,
                        int polyline) {
    if (n <= 0) return;
    auto over_cov = [](uint32_t* d, uint32_t col, float cov) {
        uint32_t a = (uint32_t)(cov * 256.f);
        uint32_t s = (((col & 0xFF00FF) * a >> 8) & 0xFF00FF) | (((((col >> 8) & 0xFF00FF) * a) >> 8 & 0xFF00FF) << 8);
        uint32_t ia = 255 - (s >> 24);
        uint32_t rb = (*d & 0xFF00FF) * ia, ag = ((*d >> 8) & 0xFF00FF) * ia;
        rb = ((rb + ((rb >> 8) & 0xFF00FF) + 0x800080) >> 8) & 0xFF00FF;
        ag = ((ag + ((ag >> 8) & 0xFF00FF) + 0x800080) >> 8) & 0xFF00FF;
        *d = s + (rb | (ag << 8));
    };
    auto dist = [](float px, float py, const float* c) {
        float vx = c[2] - c[0], vy = c[3] - c[1];
        float wx = px - c[0], wy = py - c[1];
        float l2 = vx * vx + vy * vy;
        float t = l2 > 1e-9f ? (wx * vx + wy * vy) / l2 : 0.f;
        t = t < 0 ? 0 : (t > 1 ? 1 : t);
        float dx = wx - t * vx, dy = wy - t * vy;
        return std::sqrt(dx * dx + dy * dy);
    };
    if (polyline) {
        float mnx = 1e30f, mny = 1e30f, mxx = -1e30f, mxy = -1e30f;
        for (int i = 0; i < n; ++i) {
            const float* c = caps + i * 5;
            mnx = std::min(mnx, std::min(c[0], c[2]) - c[4] - 1);
            mxx = std::max(mxx, std::max(c[0], c[2]) + c[4] + 1);
            mny = std::min(mny, std::min(c[1], c[3]) - c[4] - 1);
            mxy = std::max(mxy, std::max(c[1], c[3]) + c[4] + 1);
        }
        int x0 = std::max(0, (int)mnx), x1 = std::min(W, (int)mxx + 1);
        int y0 = std::max(0, (int)mny), y1 = std::min(H, (int)mxy + 1);
        if (x0 >= x1 || y0 >= y1) return;
        int bw = x1 - x0, bh = y1 - y0;
        std::vector<float> cov((size_t)bw * bh, 0.f);
        for (int i = 0; i < n; ++i) {
            const float* c = caps + i * 5;
            int ax = std::max(x0, (int)(std::min(c[0], c[2]) - c[4] - 1)), bx = std::min(x1, (int)(std::max(c[0], c[2]) + c[4] + 2));
            int ay = std::max(y0, (int)(std::min(c[1], c[3]) - c[4] - 1)), by = std::min(y1, (int)(std::max(c[1], c[3]) + c[4] + 2));
            for (int y = ay; y < by; ++y)
                for (int x = ax; x < bx; ++x) {
                    float v = clamp01(c[4] - dist(x + 0.5f, y + 0.5f, c) + 0.5f);
                    float& q = cov[(size_t)(y - y0) * bw + (x - x0)];
                    if (v > q) q = v;
                }
        }
        for (int y = y0; y < y1; ++y)
            for (int x = x0; x < x1; ++x) {
                float v = cov[(size_t)(y - y0) * bw + (x - x0)];
                if (v > 0.002f) over_cov(out + (size_t)y * stride + x, colors[0], v);
            }
        return;
    }
    for (int i = 0; i < n; ++i) {
        const float* c = caps + i * 5;
        if (c[4] <= 0.f || (colors[i] >> 24) == 0) continue;
        int ax = std::max(0, (int)(std::min(c[0], c[2]) - c[4] - 1)), bx = std::min(W, (int)(std::max(c[0], c[2]) + c[4] + 2));
        int ay = std::max(0, (int)(std::min(c[1], c[3]) - c[4] - 1)), by = std::min(H, (int)(std::max(c[1], c[3]) + c[4] + 2));
        for (int y = ay; y < by; ++y) {
            uint32_t* row = out + (size_t)y * stride;
            for (int x = ax; x < bx; ++x) {
                float v = clamp01(c[4] - dist(x + 0.5f, y + 0.5f, c) + 0.5f);
                if (v > 0.002f) over_cov(row + x, colors[i], v);
            }
        }
    }
}

// ── плазма живых обоев конструктора тем (theme_layers.LiveWallpaper._plasma) ──
// lut — 256 цветов 0xFFRRGGBB; out — w×h, stride в пикселях
FX_API void lw_plasma(uint32_t* out, int w, int h, int stride, float t, float boost, const uint32_t* lut) {
    const float kx = 6.f / std::max(1, w), ky = 6.f / std::max(1, w);   // y — в тех же единицах, что x
    const float sx = 3.f - std::sin(t * 0.3f);
    const float amp = 0.25f + 0.05f * boost, off = 0.5f + t * 0.03f;
    parallel_rows(h, [=](int y0, int y1) {
        for (int j = y0; j < y1; ++j) {
            const float y = j * ky;
            const float sy = fsin(y * 1.3f + t * 1.1f);
            const float dy = (y - 2.f) * (y - 2.f);
            uint32_t* o = out + (size_t)j * stride;
            for (int i = 0; i < w; ++i) {
                const float x = i * kx;
                const float dx = x - sx;
                float v = fsin(x + t) + sy + fsin((x + y) * 0.7f + t * 0.7f) +
                          fsin(std::sqrt(dx * dx + dy) * 1.6f - t * 1.4f);
                v = v * amp + off;
                v -= std::floor(v);
                int k = (int)(v * 255.f);
                o[i] = lut[k < 0 ? 0 : (k > 255 ? 255 : k)];
            }
        }
    }, 4);
}
