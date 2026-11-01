// body.cpp — тело слайдера esu! по полю расстояний (как шейдер настоящего osu!).
//
// Раньше тело рисовалось 10–26 обводками QPainterPath толстым пером (тень, рамка, слои градиента);
// при «змейке» это повторялось на каждой ступени. Здесь — одно прохождение: для каждого пикселя
// расстояние до оси слайдера, цвет — из таблицы «цвет от расстояния» (её строит Python под
// стиль/скин), все переходы сглажены интегралом таблицы по ширине пикселя.
#include "fx_common.h"

namespace {
using namespace fx;

// упростить ломаную: выбросить точки, отклонение которых от хорды меньше tol (Дуглас — Пекер)
static void simplify(const float* pts, int n, float tol, std::vector<float>& out) {
    out.clear();
    if (n <= 0) return;
    std::vector<char> keep(n, 0);
    keep[0] = keep[n - 1] = 1;
    std::vector<std::pair<int, int>> st;
    st.emplace_back(0, n - 1);
    const float tol2 = tol * tol;
    while (!st.empty()) {
        auto [a, b] = st.back();
        st.pop_back();
        if (b - a < 2) continue;
        float ax = pts[a * 2], ay = pts[a * 2 + 1], bx = pts[b * 2], by = pts[b * 2 + 1];
        float vx = bx - ax, vy = by - ay, l2 = vx * vx + vy * vy;
        float best = -1.f;
        int bi = -1;
        for (int i = a + 1; i < b; ++i) {
            float wx = pts[i * 2] - ax, wy = pts[i * 2 + 1] - ay;
            float t = l2 > 1e-9f ? (wx * vx + wy * vy) / l2 : 0.f;
            t = t < 0 ? 0 : (t > 1 ? 1 : t);
            float dx = wx - t * vx, dy = wy - t * vy, d2 = dx * dx + dy * dy;
            if (d2 > best) {
                best = d2;
                bi = i;
            }
        }
        if (best > tol2 && bi > 0) {
            keep[bi] = 1;
            st.emplace_back(a, bi);
            st.emplace_back(bi, b);
        }
    }
    for (int i = 0; i < n; ++i)
        if (keep[i]) {
            out.push_back(pts[i * 2]);
            out.push_back(pts[i * 2 + 1]);
        }
}

}  // namespace

// out — ARGB32_Premultiplied W×H (stride в пикселях, заранее прозрачный); pts — n точек оси
// (пиксели устройства, в координатах картинки); R — радиус тела; prof — N×4 float (премульт.
// r, g, b, a в 0..1) — цвет на расстоянии d = i/(N−1)·R от оси (дальше R — прозрачно).
FX_API void slider_body(uint32_t* out, int W, int H, int stride, const float* pts, int n, float R,
                        const float* prof, int N) {
    if (W <= 0 || H <= 0 || n <= 0 || N < 2 || R <= 0.5f) return;
    std::vector<float> P;
    simplify(pts, n, 0.3f, P);
    const int m = (int)P.size() / 2;
    // интеграл таблицы по d (в долях таблицы) — для сглаживания пикселем шириной 1
    std::vector<double> cum((size_t)(N + 1) * 4, 0.0);
    for (int i = 0; i < N; ++i)
        for (int k = 0; k < 4; ++k) cum[(size_t)(i + 1) * 4 + k] = cum[(size_t)i * 4 + k] + prof[i * 4 + k];
    auto integ = [&](double u, int k) -> double {   // ∫ prof от 0 до u (u в единицах таблицы, ≥ 0)
        if (u <= 0) return u * prof[k];             // левее оси — цвет оси (симметрия)
        if (u >= N) return cum[(size_t)N * 4 + k];  // правее края — прозрачно
        int i = (int)u;
        return cum[(size_t)i * 4 + k] + (u - i) * prof[i * 4 + k];
    };
    const float reach = R + 1.0f;
    const double scale = (double)(N - 1) / R;
    std::vector<float> dist((size_t)W * H, 1e9f);
    parallel_rows(H, [&](int y0, int y1) {
        for (int s = 0; s < std::max(1, m - 1); ++s) {
            float ax = P[s * 2], ay = P[s * 2 + 1];
            float bx = m > 1 ? P[s * 2 + 2] : ax, by = m > 1 ? P[s * 2 + 3] : ay;
            int ry0 = std::max(y0, (int)std::floor(std::min(ay, by) - reach));
            int ry1 = std::min(y1, (int)std::ceil(std::max(ay, by) + reach) + 1);
            if (ry0 >= ry1) continue;
            int rx0 = std::max(0, (int)std::floor(std::min(ax, bx) - reach));
            int rx1 = std::min(W, (int)std::ceil(std::max(ax, bx) + reach) + 1);
            float vx = bx - ax, vy = by - ay, l2 = vx * vx + vy * vy;
            float il2 = l2 > 1e-9f ? 1.f / l2 : 0.f;
            for (int y = ry0; y < ry1; ++y) {
                float* drow = dist.data() + (size_t)y * W;
                float wy = y + 0.5f - ay;
                for (int x = rx0; x < rx1; ++x) {
                    float wx = x + 0.5f - ax;
                    float t = (wx * vx + wy * vy) * il2;
                    t = t < 0 ? 0 : (t > 1 ? 1 : t);
                    float dx = wx - t * vx, dy = wy - t * vy;
                    float d2 = dx * dx + dy * dy;
                    if (d2 < drow[x]) drow[x] = d2;
                }
            }
        }
        for (int y = y0; y < y1; ++y) {
            float* drow = dist.data() + (size_t)y * W;
            uint32_t* o = out + (size_t)y * stride;
            for (int x = 0; x < W; ++x) {
                float d2 = drow[x];
                if (d2 >= reach * reach) continue;
                double d = std::sqrt((double)d2);
                double u0 = (d - 0.5) * scale, u1 = (d + 0.5) * scale;
                double inv = 1.0 / (u1 - u0);
                double c[4];
                for (int k = 0; k < 4; ++k) c[k] = (integ(u1, k) - integ(u0, k)) * inv;
                double a = c[3] < 0 ? 0 : (c[3] > 1 ? 1 : c[3]);
                int A = (int)(a * 255.0 + 0.5);
                if (A <= 0) continue;
                int Rr = std::min(A, (int)(c[0] * 255.0 + 0.5)), G = std::min(A, (int)(c[1] * 255.0 + 0.5));
                int B = std::min(A, (int)(c[2] * 255.0 + 0.5));
                o[x] = ((uint32_t)A << 24) | ((uint32_t)std::max(0, Rr) << 16) | ((uint32_t)std::max(0, G) << 8) |
                       (uint32_t)std::max(0, B);
            }
        }
    }, 4);
}
