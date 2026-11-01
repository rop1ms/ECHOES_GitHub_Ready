// milk.cpp — тяжёлые операции MilkDrop (milkdrop_core): буфер обратной связи h×w×3 float,
// которым владеет numpy-массив Python (сюда приходит указатель).
//
// Логика пресетов, звук и геометрия волн остаются в Python (это скаляры и сотни точек),
// а всё попиксельное — здесь: сетка движения + билинейная выборка + затухание, рисование
// линий/точек, плазма, «штампы» слов, цветовой ремап и растяжение кадра до размера окна.
#include "fx_common.h"

namespace {
using namespace fx;

struct WarpParams {
    float aspect, cx, cy, lz, zexp, rot_c, rot_s, an, wa, ws, wt;
    int wmode;
    float addx, addy;        // cx + dx-дрейф, cy + dy-дрейф
    float decay, sub, flash; // множитель затухания, вычитание, вспышка (1 + flash*0.5)
};

}  // namespace

// src → dst (оба h×w×3 float32): один кадр обратной связи
FX_API void milk_warp(const float* src, float* dst, int w, int h, const WarpParams* P) {
    const WarpParams p = *P;
    const float kx = 2.f / (w - 1), ky = 2.f / (h - 1);
    const float zk = -p.lz * p.zexp, z0 = -p.lz + p.lz * p.zexp * 0.6f;
    const float pxk = 1.f / p.aspect * 0.5f * (w - 1), pyk = 0.5f * (h - 1);
    const float xmax = w - 1.001f, ymax = h - 1.001f;
    parallel_rows(h, [&](int y0, int y1) {
        for (int yy = y0; yy < y1; ++yy) {
            const float Y = yy * ky - 1.f;
            const float Yc = Y - p.cy;
            float* o = dst + (size_t)yy * w * 3;
            for (int xx = 0; xx < w; ++xx) {
                const float X = (xx * kx - 1.f) * p.aspect;
                const float Xc = X - p.cx;
                const float R = std::sqrt(Xc * Xc + Yc * Yc);
                const float sc = fexp(R * zk + z0);
                float u = (Xc * p.rot_c - Yc * p.rot_s) * sc * p.an;
                float v = (Xc * p.rot_s + Yc * p.rot_c) * sc / p.an;
                const float wa = p.wa, ws = p.ws, wt = p.wt;
                switch (p.wmode) {
                    case 1: {
                        float ang = (wa * 6.f) * fexp(-R * R * 1.6f);
                        float ca = fcos(ang), sa = fsin(ang);
                        float nu = u * ca - v * sa, nv = u * sa + v * ca;
                        u = nu + wa * 0.4f * fsin(Yc * ws + wt);
                        v = nv;
                        break;
                    }
                    case 2:
                        u += wa * fsin(Y * ws + X * 0.6f * ws + wt * 1.1f);
                        v += wa * 0.7f * fcos(X * ws * 1.3f - wt * 0.6f) + wa * 0.15f;
                        break;
                    case 3: {
                        float rip = wa * 1.4f * fsin(R * ws * 3.f - wt * 3.f) / (R + 0.15f);
                        u += Xc * rip;
                        v += Yc * rip;
                        break;
                    }
                    case 4:
                        u += wa * (fsin(Y * ws * 1.17f + wt * 1.3f + 0.7f) +
                                   0.5f * fsin(X * ws * 2.71f - wt * 0.9f + Y * 1.3f));
                        v += wa * (fcos(X * ws * 0.93f - wt * 1.07f) +
                                   0.5f * fcos(Y * ws * 2.33f + wt * 1.6f + X * 0.8f));
                        break;
                    default:
                        u += wa * fsin(Y * ws + wt * 1.13f + 0.9f);
                        v += wa * fcos(X * ws * 0.9f + wt * 0.87f + 0.3f);
                }
                u += p.addx;
                v += p.addy;
                float px = (u * pxk) + 0.5f * (w - 1);
                float py = (v * pyk) + 0.5f * (h - 1);
                px = px < 0.f ? 0.f : (px > xmax ? xmax : px);
                py = py < 0.f ? 0.f : (py > ymax ? ymax : py);
                int x0 = (int)px, y0i = (int)py;
                float fx = px - x0, fy = py - y0i;
                const float* a = src + ((size_t)y0i * w + x0) * 3;
                const float* c = a + (size_t)w * 3;
                for (int k = 0; k < 3; ++k) {
                    float t = a[k] + (a[k + 3] - a[k]) * fx;
                    float b = c[k] + (c[k + 3] - c[k]) * fx;
                    float r = (t + (b - t) * fy) * p.decay - p.sub;
                    o[k] = r < 0.f ? 0.f : r * p.flash;
                }
                o += 3;
            }
        }
    });
}

// Точки (нормированные: x∈[-aspect,aspect], y∈[-1,1]) квадратиками thick×thick, max-смешение.
// cols — один цвет (ncol=1) или цвет на точку. dense=1 — соединять соседние точки отрезками.
FX_API void milk_plot(float* buf, int w, int h, float aspect, const float* xs, const float* ys, int n,
                      const float* cols, int ncol, int thick, int dense) {
    if (n <= 0) return;
    const float kx = 1.f / aspect * 0.5f * (w - 1), ky = 0.5f * (h - 1);
    // крупные точки (сетка больше исходной) — кружками, а не квадратами
    const bool round = thick >= 4;
    const float rc = (thick - 1) * 0.5f, rr = (thick * 0.5f + 0.15f) * (thick * 0.5f + 0.15f);
    auto dot = [&](float fx, float fy, const float* c) {
        int px = (int)(fx * kx + 0.5f * (w - 1));
        int py = (int)(fy * ky + 0.5f * (h - 1));
        if (fx * kx + 0.5f * (w - 1) < 0.f || fy * ky + 0.5f * (h - 1) < 0.f) return;   // int() к нулю
        if (px < 0 || py < 0 || px >= w - thick || py >= h - thick) return;
        for (int oy = 0; oy < thick; ++oy) {
            float* row = buf + ((size_t)(py + oy) * w + px) * 3;
            for (int ox = 0; ox < thick; ++ox) {
                if (round && (ox - rc) * (ox - rc) + (oy - rc) * (oy - rc) > rr) continue;   // круглая точка
                float* q = row + ox * 3;
                if (c[0] > q[0]) q[0] = c[0];
                if (c[1] > q[1]) q[1] = c[1];
                if (c[2] > q[2]) q[2] = c[2];
            }
        }
    };
    if (dense && n > 1) {
        // шаг вдоль отрезка — не больше половины толщины: линия без «бусин» на любом разрешении
        float step = std::max(0.5f, thick * 0.5f);
        for (int i = 0; i + 1 < n; ++i) {
            const float* c = cols + (ncol > 1 ? i * 3 : 0);
            float dxp = (xs[i + 1] - xs[i]) * kx, dyp = (ys[i + 1] - ys[i]) * ky;
            float len = std::sqrt(dxp * dxp + dyp * dyp);
            int m = std::max(1, std::min(4096, (int)(len / step) + 1));
            for (int k = 0; k < m; ++k) {
                float f = (float)k / m;
                dot(xs[i] + (xs[i + 1] - xs[i]) * f, ys[i] + (ys[i + 1] - ys[i]) * f, c);
            }
        }
        dot(xs[n - 1], ys[n - 1], cols + (ncol > 1 ? (n - 1) * 3 : 0));
    } else {
        for (int i = 0; i < n; ++i) dot(xs[i], ys[i], cols + (ncol > 1 ? i * 3 : 0));
    }
}

// плазма (пресеты «[full] plasma …»): поле на сетке /4, растянутое ближайшим соседом, max-смешение
FX_API void milk_plasma(float* buf, int w, int h, float aspect, float t, float cx, float cy, float k,
                        const float* col) {
    int pw = std::max(8, w / 4), ph = std::max(6, h / 4);
    std::vector<float> band((size_t)pw * ph);
    float st = std::sin(t * 0.3f) * 1.2f;
    for (int j = 0; j < ph; ++j) {
        float PY = (float)j / (ph - 1) * 2.f - 1.f;
        for (int i = 0; i < pw; ++i) {
            float PX = ((float)i / (pw - 1) * 2.f - 1.f) * aspect;
            float fld = std::sin(PX * 2.1f + t * 0.9f) + std::sin(PY * 3.3f - t * 1.2f + PX * 0.7f) +
                        std::sin((PX + PY * 1.4f) * 2.6f + t * 0.6f) +
                        std::sin(std::sqrt((PX - cx) * (PX - cx) + (PY - cy) * (PY - cy)) * 5.f - t * 2.f);
            float b = 1.f - std::fabs(fld - st) * 2.2f;
            band[(size_t)j * pw + i] = b < 0 ? 0 : (b > 1 ? 1 : b);
        }
    }
    float c0 = col[0] * k, c1 = col[1] * k, c2 = col[2] * k;
    parallel_rows(h, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y) {
            int ry = std::min(y * ph / h, ph - 1);
            float* row = buf + (size_t)y * w * 3;
            for (int x = 0; x < w; ++x) {
                int rx = std::min(x * pw / w, pw - 1);
                float b = band[(size_t)ry * pw + rx];
                float* q = row + x * 3;
                float v0 = b * c0, v1 = b * c1, v2 = b * c2;
                if (v0 > q[0]) q[0] = v0;
                if (v1 > q[1]) q[1] = v1;
                if (v2 > q[2]) q[2] = v2;
            }
        }
    });
}

// маска слова (mh×mw, 0..1) с левым верхним углом в пикселе (x0, y0), max-смешение
FX_API void milk_stamp(float* buf, int w, int h, const float* mask, int mw, int mh, int x0, int y0,
                       const float* col) {
    int xa = std::max(0, x0), ya = std::max(0, y0);
    int xb = std::min(w, x0 + mw), yb = std::min(h, y0 + mh);
    for (int y = ya; y < yb; ++y) {
        const float* m = mask + (size_t)(y - y0) * mw;
        float* row = buf + (size_t)y * w * 3;
        for (int x = xa; x < xb; ++x) {
            float a = m[x - x0];
            float* q = row + x * 3;
            for (int k = 0; k < 3; ++k) {
                float v = a * col[k];
                if (v > q[k]) q[k] = v;
            }
        }
    }
}

FX_API void milk_clip(float* buf, int n) {
    parallel_rows(n / 4096 + 1, [&](int a, int b) {
        size_t i0 = (size_t)a * 4096, i1 = std::min((size_t)n, (size_t)b * 4096);
        for (size_t i = i0; i < i1; ++i) buf[i] = buf[i] < 0.f ? 0.f : (buf[i] > 1.f ? 1.f : buf[i]);
    }, 1);
}

// Цветовой ремап кадра → 0xFFRRGGBB (w×h). mode: 0 чистый, 2 соляризация, иначе LUT (512×3) по
// сумме каналов + c·addk (как milkdrop_core.frame_argb32).
FX_API void milk_remap(const float* buf, int w, int h, int mode, const float* lut, int lut_n, float addk,
                       uint32_t* out) {
    parallel_rows(h, [&](int y0, int y1) {
        for (int y = y0; y < y1; ++y) {
            const float* s = buf + (size_t)y * w * 3;
            uint32_t* o = out + (size_t)y * w;
            for (int x = 0; x < w; ++x, s += 3) {
                float c0 = std::min(s[0], 1.f), c1 = std::min(s[1], 1.f), c2 = std::min(s[2], 1.f);
                float r, g, b;
                if (mode == 0) {
                    r = std::sqrt(c0) * 0.35f + c0 * 0.7f;
                    g = std::sqrt(c1) * 0.35f + c1 * 0.7f;
                    b = std::sqrt(c2) * 0.35f + c2 * 0.7f;
                } else if (mode == 2) {
                    r = (1.f - std::fabs(2.f * c0 - 1.f)) * 1.25f + c0 * 0.25f;
                    g = (1.f - std::fabs(2.f * c1 - 1.f)) * 1.25f + c1 * 0.25f;
                    b = (1.f - std::fabs(2.f * c2 - 1.f)) * 1.25f + c2 * 0.25f;
                } else {
                    int idx = (int)((c0 + c1 + c2) * ((lut_n - 1) / 3.f));
                    idx = idx < 0 ? 0 : (idx > lut_n - 1 ? lut_n - 1 : idx);
                    const float* L = lut + idx * 3;
                    r = L[0] + c0 * addk;
                    g = L[1] + c1 * addk;
                    b = L[2] + c2 * addk;
                }
                int R = (int)(clamp01(r) * 255.f), G = (int)(clamp01(g) * 255.f), B = (int)(clamp01(b) * 255.f);
                o[x] = 0xFF000000u | ((uint32_t)R << 16) | ((uint32_t)G << 8) | (uint32_t)B;
            }
        }
    });
}
