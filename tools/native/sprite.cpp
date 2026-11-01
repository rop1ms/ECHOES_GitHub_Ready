// sprite.cpp — компоновщик спрайтов для темы esu! (и любых QPainter-сцен из картинок).
//
// Python копит за кадр список команд «нарисовать картинку с аффинным преобразованием,
// прозрачностью и режимом смешения» (blit/drawPixmap/fillRect) и отдаёт их сюда одним вызовом.
// Кадр делится на горизонтальные полосы, каждая полоса проходит весь список по порядку
// (порядок наложения сохраняется) — в несколько потоков, без GIL.
//
// Пиксели: 32 бита, премультиплицированные (QImage ARGB32_Premultiplied / RGB32 — альфа 0xFF).
// Картинки НЕ копируются: Python держит QImage живой и передаёт указатель на её память.
#include "fx_common.h"

namespace {
using namespace fx;

struct Sprite {
    const uint32_t* px = nullptr;
    int w = 0, h = 0, stride = 0;   // stride — в пикселях
    bool live = false;
    uint32_t* own = nullptr;        // своя копия пикселей (spr_register_copy)
};

std::vector<Sprite> g_spr;
std::vector<int> g_free;
SRWLOCK g_mx = SRWLOCK_INIT;

enum { OP_SPRITE = 0, OP_FILL = 1 };
enum { BL_OVER = 0, BL_PLUS = 1, BL_SOURCE = 2 };
constexpr int REC = 16;

struct Cmd {
    int op, blend;
    const Sprite* s;
    // обратное преобразование: устройство → пиксели спрайта
    float ia, ib, ic, id, itx, ity;
    float sx0, sy0, sx1, sy1;          // прямоугольник-источник
    int bx0, by0, bx1, by1;            // охватывающий прямоугольник на устройстве (включ./искл.)
    uint32_t alpha;                    // 0..256
    bool simple;                       // без масштаба/поворота, целый сдвиг
    int ox, oy;                        // для simple: устройство (x,y) ↔ спрайт (x-ox, y-oy)
    uint32_t color;                    // для OP_FILL: премультиплицированный 0xAARRGGBB
};

static inline uint32_t mul_px(uint32_t p, uint32_t a256) {   // p · a/256 по всем 4 каналам
    uint32_t rb = ((p & 0xFF00FF) * a256 >> 8) & 0xFF00FF;
    uint32_t ag = (((p >> 8) & 0xFF00FF) * a256 >> 8) & 0xFF00FF;
    return rb | (ag << 8);
}

static inline uint32_t over_px(uint32_t s, uint32_t d) {    // s + d·(1 − αs)
    uint32_t ia = 255 - (s >> 24);
    uint32_t rb = (d & 0xFF00FF) * ia;
    uint32_t ag = ((d >> 8) & 0xFF00FF) * ia;
    rb = ((rb + ((rb >> 8) & 0xFF00FF) + 0x800080) >> 8) & 0xFF00FF;
    ag = ((ag + ((ag >> 8) & 0xFF00FF) + 0x800080) >> 8) & 0xFF00FF;
    return s + (rb | (ag << 8));
}

static inline uint32_t plus_px(uint32_t s, uint32_t d) {    // насыщающее сложение 4 каналов (SWAR)
    uint32_t rb = (s & 0xFF00FF) + (d & 0xFF00FF);
    uint32_t ag = ((s >> 8) & 0xFF00FF) + ((d >> 8) & 0xFF00FF);
    rb |= 0x1000100 - ((rb >> 8) & 0x10001);                  // переполнение канала → 0xFF
    ag |= 0x1000100 - ((ag >> 8) & 0x10001);
    return (rb & 0xFF00FF) | ((ag & 0xFF00FF) << 8);
}

static inline uint32_t bilerp(const Sprite* s, float u, float v, float sx0, float sy0, float sx1, float sy1) {
    // u, v — координаты центра выборки в пикселях спрайта; зажать внутри источника
    u -= 0.5f;
    v -= 0.5f;
    float umax = sx1 - 1.f, vmax = sy1 - 1.f;
    u = u < sx0 ? sx0 : (u > umax ? umax : u);
    v = v < sy0 ? sy0 : (v > vmax ? vmax : v);
    int x0 = (int)u, y0 = (int)v;
    uint32_t fx = (uint32_t)((u - x0) * 256.f), fy = (uint32_t)((v - y0) * 256.f);
    int x1 = x0 + 1 < (int)sx1 ? x0 + 1 : x0;
    int y1 = y0 + 1 < (int)sy1 ? y0 + 1 : y0;
    const uint32_t* r0 = s->px + (size_t)y0 * s->stride;
    const uint32_t* r1 = s->px + (size_t)y1 * s->stride;
    uint32_t a = r0[x0], b = r0[x1], c = r1[x0], d = r1[x1];
    uint32_t rb_t = ((a & 0xFF00FF) * (256 - fx) + (b & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
    uint32_t ag_t = (((a >> 8) & 0xFF00FF) * (256 - fx) + ((b >> 8) & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
    uint32_t rb_b = ((c & 0xFF00FF) * (256 - fx) + (d & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
    uint32_t ag_b = (((c >> 8) & 0xFF00FF) * (256 - fx) + ((d >> 8) & 0xFF00FF) * fx) >> 8 & 0xFF00FF;
    uint32_t rb = (rb_t * (256 - fy) + rb_b * fy) >> 8 & 0xFF00FF;
    uint32_t ag = (ag_t * (256 - fy) + ag_b * fy) >> 8 & 0xFF00FF;
    return rb | (ag << 8);
}


#ifdef __AVX2__
#include <immintrin.h>
// ── AVX2: 8 пикселей за раз ──
// Каналы раскладываются как в Qt: rb = p & 0x00FF00FF, ag = (p >> 8) & 0x00FF00FF — в каждой
// 16-битной дорожке значение < 256, поэтому умножение на вес ≤ 256 помещается в 16 бит.
static inline __m256i k_mask() { return _mm256_set1_epi32(0x00FF00FF); }

static inline __m256i v_lerp(__m256i a, __m256i b, __m256i w, __m256i iw) {   // (a·iw + b·w) >> 8, 4 канала
    const __m256i M = k_mask();
    __m256i rb = _mm256_add_epi16(_mm256_mullo_epi16(_mm256_and_si256(a, M), iw),
                                  _mm256_mullo_epi16(_mm256_and_si256(b, M), w));
    __m256i ag = _mm256_add_epi16(_mm256_mullo_epi16(_mm256_and_si256(_mm256_srli_epi16(a, 8), M), iw),
                                  _mm256_mullo_epi16(_mm256_and_si256(_mm256_srli_epi16(b, 8), M), w));
    return _mm256_or_si256(_mm256_srli_epi16(rb, 8), _mm256_andnot_si256(M, ag));
}

static inline __m256i v_mul(__m256i p, __m256i a16) {     // p · a/256 (a16: вес в обеих 16-битных половинах)
    const __m256i M = k_mask();
    __m256i rb = _mm256_srli_epi16(_mm256_mullo_epi16(_mm256_and_si256(p, M), a16), 8);
    __m256i ag = _mm256_mullo_epi16(_mm256_and_si256(_mm256_srli_epi16(p, 8), M), a16);
    return _mm256_or_si256(rb, _mm256_andnot_si256(M, ag));
}

static inline __m256i v_over(__m256i s, __m256i d) {      // s + d·(255 − αs)/255
    const __m256i M = k_mask();
    __m256i ia = _mm256_sub_epi32(_mm256_set1_epi32(255), _mm256_srli_epi32(s, 24));
    ia = _mm256_or_si256(ia, _mm256_slli_epi32(ia, 16));
    __m256i rb = _mm256_mullo_epi16(_mm256_and_si256(d, M), ia);
    __m256i ag = _mm256_mullo_epi16(_mm256_and_si256(_mm256_srli_epi16(d, 8), M), ia);
    const __m256i h = _mm256_set1_epi16(0x80);
    rb = _mm256_srli_epi16(_mm256_add_epi16(_mm256_add_epi16(rb, _mm256_srli_epi16(rb, 8)), h), 8);
    ag = _mm256_add_epi16(_mm256_add_epi16(ag, _mm256_srli_epi16(ag, 8)), h);
    return _mm256_add_epi32(s, _mm256_or_si256(rb, _mm256_andnot_si256(M, ag)));
}

static inline __m256i v_blend(__m256i p, __m256i d, int blend) {
    if (blend == BL_PLUS) return _mm256_adds_epu8(p, d);
    if (blend == BL_SOURCE) return p;
    return v_over(p, d);
}

static void row_simple_avx2(const uint32_t* src, uint32_t* o, int xa, int xb, uint32_t alpha, int blend) {
    const __m256i a16 = _mm256_set1_epi32((int)(alpha | (alpha << 16)));
    int x = xa;
    for (; x + 8 <= xb; x += 8) {
        __m256i p = _mm256_loadu_si256((const __m256i*)(src + x));
        if (alpha < 256) p = v_mul(p, a16);
        __m256i d = _mm256_loadu_si256((const __m256i*)(o + x));
        _mm256_storeu_si256((__m256i*)(o + x), v_blend(p, d, blend));
    }
    for (; x < xb; ++x) {
        uint32_t p = src[x];
        if (alpha < 256) p = mul_px(p, alpha);
        o[x] = blend == BL_PLUS ? plus_px(p, o[x]) : (blend == BL_SOURCE ? p : over_px(p, o[x]));
    }
}

static void row_fill_avx2(uint32_t* o, int xa, int xb, uint32_t col, int blend) {
    const __m256i c = _mm256_set1_epi32((int)col);
    int x = xa;
    for (; x + 8 <= xb; x += 8) {
        __m256i d = _mm256_loadu_si256((const __m256i*)(o + x));
        _mm256_storeu_si256((__m256i*)(o + x), v_blend(c, d, blend));
    }
    for (; x < xb; ++x) o[x] = blend == BL_PLUS ? plus_px(col, o[x]) : (blend == BL_SOURCE ? col : over_px(col, o[x]));
}

// одна строка преобразованного спрайта: U, V — 16.16 у центра пикселя xa
static void row_xform_avx2(const uint32_t* spx, int sst, uint32_t* o, int xa, int xb, int32_t U, int32_t V,
                           int32_t DU, int32_t DV, int32_t SX0, int32_t SY0, uint32_t SW, uint32_t SH, int isx0,
                           int isy0, int isx1, int isy1, uint32_t alpha, int blend) {
    const __m256i lane = _mm256_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7);
    const __m256i vDU8 = _mm256_set1_epi32(DU * 8), vDV8 = _mm256_set1_epi32(DV * 8);
    __m256i vU = _mm256_add_epi32(_mm256_set1_epi32(U), _mm256_mullo_epi32(lane, _mm256_set1_epi32(DU)));
    __m256i vV = _mm256_add_epi32(_mm256_set1_epi32(V), _mm256_mullo_epi32(lane, _mm256_set1_epi32(DV)));
    const __m256i sgn = _mm256_set1_epi32((int)0x80000000);
    const __m256i vSX0 = _mm256_set1_epi32(SX0), vSY0 = _mm256_set1_epi32(SY0);
    const __m256i vSW = _mm256_xor_si256(_mm256_set1_epi32((int)SW), sgn);
    const __m256i vSH = _mm256_xor_si256(_mm256_set1_epi32((int)SH), sgn);
    const __m256i half = _mm256_set1_epi32(32768), m255 = _mm256_set1_epi32(255);
    const __m256i lo_x = _mm256_set1_epi32(isx0), hi_x = _mm256_set1_epi32(isx1);
    const __m256i lo_y = _mm256_set1_epi32(isy0), hi_y = _mm256_set1_epi32(isy1);
    const __m256i vst = _mm256_set1_epi32(sst), one = _mm256_set1_epi32(1);
    const __m256i w256 = _mm256_set1_epi32(256 | (256 << 16));
    const __m256i a16 = _mm256_set1_epi32((int)(alpha | (alpha << 16)));
    int x = xa;
    for (; x + 8 <= xb; x += 8, vU = _mm256_add_epi32(vU, vDU8), vV = _mm256_add_epi32(vV, vDV8)) {
        // внутри источника: (U − SX0) < SW беззнаково
        __m256i in_u = _mm256_cmpgt_epi32(vSW, _mm256_xor_si256(_mm256_sub_epi32(vU, vSX0), sgn));
        __m256i in_v = _mm256_cmpgt_epi32(vSH, _mm256_xor_si256(_mm256_sub_epi32(vV, vSY0), sgn));
        __m256i in = _mm256_and_si256(in_u, in_v);
        if (_mm256_testz_si256(in, in)) continue;
        __m256i uu = _mm256_sub_epi32(vU, half), vv = _mm256_sub_epi32(vV, half);
        __m256i x0 = _mm256_srai_epi32(uu, 16), y0 = _mm256_srai_epi32(vv, 16);
        __m256i fx = _mm256_and_si256(_mm256_srli_epi32(uu, 8), m255);
        __m256i fy = _mm256_and_si256(_mm256_srli_epi32(vv, 8), m255);
        // края: зажать и обнулить долю
        __m256i cx = _mm256_or_si256(_mm256_cmpgt_epi32(lo_x, x0), _mm256_cmpgt_epi32(x0, _mm256_sub_epi32(hi_x, one)));
        __m256i cy = _mm256_or_si256(_mm256_cmpgt_epi32(lo_y, y0), _mm256_cmpgt_epi32(y0, _mm256_sub_epi32(hi_y, one)));
        fx = _mm256_andnot_si256(cx, fx);
        fy = _mm256_andnot_si256(cy, fy);
        x0 = _mm256_min_epi32(_mm256_max_epi32(x0, lo_x), hi_x);
        y0 = _mm256_min_epi32(_mm256_max_epi32(y0, lo_y), hi_y);
        __m256i x1 = _mm256_min_epi32(_mm256_add_epi32(x0, one), hi_x);
        __m256i y1 = _mm256_min_epi32(_mm256_add_epi32(y0, one), hi_y);
        __m256i r0 = _mm256_mullo_epi32(y0, vst), r1 = _mm256_mullo_epi32(y1, vst);
        const __m256i z = _mm256_setzero_si256();
        __m256i t00 = _mm256_mask_i32gather_epi32(z, (const int*)spx, _mm256_add_epi32(r0, x0), in, 4);
        __m256i t01 = _mm256_mask_i32gather_epi32(z, (const int*)spx, _mm256_add_epi32(r0, x1), in, 4);
        __m256i t10 = _mm256_mask_i32gather_epi32(z, (const int*)spx, _mm256_add_epi32(r1, x0), in, 4);
        __m256i t11 = _mm256_mask_i32gather_epi32(z, (const int*)spx, _mm256_add_epi32(r1, x1), in, 4);
        __m256i any = _mm256_or_si256(_mm256_or_si256(t00, t01), _mm256_or_si256(t10, t11));
        if (_mm256_testz_si256(any, any)) continue;               // прозрачная область
        __m256i wx = _mm256_or_si256(fx, _mm256_slli_epi32(fx, 16));
        __m256i iwx = _mm256_sub_epi16(w256, wx);
        __m256i wy = _mm256_or_si256(fy, _mm256_slli_epi32(fy, 16));
        __m256i iwy = _mm256_sub_epi16(w256, wy);
        __m256i top = v_lerp(t00, t01, wx, iwx);
        __m256i bot = v_lerp(t10, t11, wx, iwx);
        __m256i p = v_lerp(top, bot, wy, iwy);
        if (alpha < 256) p = v_mul(p, a16);
        __m256i d = _mm256_loadu_si256((const __m256i*)(o + x));
        __m256i r = v_blend(p, d, blend);
        _mm256_storeu_si256((__m256i*)(o + x), _mm256_blendv_epi8(d, r, in));
    }
    // хвост — скалярно
    U += (x - xa) * DU;
    V += (x - xa) * DV;
    for (; x < xb; ++x, U += DU, V += DV) {
        if ((uint32_t)(U - SX0) >= SW || (uint32_t)(V - SY0) >= SH) continue;
        int32_t uu = U - 32768, vv = V - 32768;
        int x0 = uu >> 16, y0 = vv >> 16;
        uint32_t fx8 = (uint32_t)(uu >> 8) & 255, fy8 = (uint32_t)(vv >> 8) & 255;
        if (x0 < isx0) { x0 = isx0; fx8 = 0; }
        if (y0 < isy0) { y0 = isy0; fy8 = 0; }
        if (x0 >= isx1) { x0 = isx1; fx8 = 0; }
        if (y0 >= isy1) { y0 = isy1; fy8 = 0; }
        const uint32_t* q0 = spx + (size_t)y0 * sst + x0;
        const uint32_t* q1 = fy8 ? q0 + sst : q0;
        uint32_t a = q0[0], b = fx8 ? q0[1] : a, cc = q1[0], dd = fx8 ? q1[1] : cc;
        uint32_t ifx = 256 - fx8, ify = 256 - fy8;
        uint32_t rb_t = ((a & 0xFF00FF) * ifx + (b & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
        uint32_t ag_t = (((a >> 8) & 0xFF00FF) * ifx + ((b >> 8) & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
        uint32_t rb_b = ((cc & 0xFF00FF) * ifx + (dd & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
        uint32_t ag_b = (((cc >> 8) & 0xFF00FF) * ifx + ((dd >> 8) & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
        uint32_t p = (((rb_t * ify + rb_b * fy8) >> 8) & 0xFF00FF) | ((((ag_t * ify + ag_b * fy8) >> 8) & 0xFF00FF) << 8);
        if (alpha < 256) p = mul_px(p, alpha);
        o[x] = blend == BL_PLUS ? plus_px(p, o[x]) : (blend == BL_SOURCE ? p : over_px(p, o[x]));
    }
}
#endif

// Масштаб без поворота (самый частый случай: кружки подхода, вспышки, растянутые картинки).
// Столбцы источника для каждого столбца назначения не зависят от строки — таблица строится
// один раз на полосу; в строке сначала смешиваются две строки источника (непрерывные загрузки),
// потом — соседние пиксели по таблице.
struct Scratch {   // рабочие буферы полосы (без thread_local: он тянет winpthreads)
    std::vector<int32_t> tx0, tx1;
    std::vector<uint16_t> tfx;
    std::vector<uint32_t> tmp;
};

static void draw_scaled(const Cmd& c, uint32_t* dst, int dstride, int xa, int xb, int ya, int yb, Scratch& sc) {
    const Sprite* s = c.s;
    const int isx0 = (int)c.sx0, isy0 = (int)c.sy0;
    const int isx1 = (int)std::ceil(c.sx1) - 1, isy1 = (int)std::ceil(c.sy1) - 1;
    const int32_t SX0 = (int32_t)(c.sx0 * 65536.f), SY0 = (int32_t)(c.sy0 * 65536.f);
    const uint32_t SW = (uint32_t)((c.sx1 - c.sx0) * 65536.f), SH = (uint32_t)((c.sy1 - c.sy0) * 65536.f);
    const int32_t DU = (int32_t)(c.ia * 65536.f);
    int32_t U = (int32_t)((c.ia * (xa + 0.5f) + c.itx) * 65536.f);
    std::vector<int32_t>& tx0 = sc.tx0;
    std::vector<int32_t>& tx1 = sc.tx1;
    std::vector<uint16_t>& tfx = sc.tfx;
    std::vector<uint32_t>& tmp = sc.tmp;
    const int n = xb - xa;
    if ((int)tx0.size() < n) {
        tx0.resize(n);
        tx1.resize(n);
        tfx.resize(n);
    }
    int ia_ = -1, ib_ = -1;                 // столбцы назначения внутри источника [ia_, ib_]
    int s_lo = 1 << 30, s_hi = -1;
    for (int i = 0; i < n; ++i, U += DU) {
        if ((uint32_t)(U - SX0) >= SW) continue;
        if (ia_ < 0) ia_ = i;
        ib_ = i;
        int32_t uu = U - 32768;
        int x0 = uu >> 16;
        uint32_t f = (uint32_t)(uu >> 8) & 255;
        if (x0 < isx0) { x0 = isx0; f = 0; }
        if (x0 >= isx1) { x0 = isx1; f = 0; }
        int x1 = f ? x0 + 1 : x0;
        tx0[i] = x0;
        tx1[i] = x1;
        tfx[i] = (uint16_t)f;
        s_lo = std::min(s_lo, std::min(x0, x1));
        s_hi = std::max(s_hi, std::max(x0, x1));
    }
    if (ia_ < 0) return;
    for (int i = ia_; i <= ib_; ++i) {      // в индексы буфера tmp
        tx0[i] -= s_lo;
        tx1[i] -= s_lo;
    }
    const int span = s_hi - s_lo + 1;
    if ((int)tmp.size() < span + 8) tmp.resize(span + 8);
    const int32_t DV = (int32_t)(c.id * 65536.f);
    int32_t V = (int32_t)((c.id * (ya + 0.5f) + c.ity) * 65536.f);
    for (int y = ya; y < yb; ++y, V += DV) {
        if ((uint32_t)(V - SY0) >= SH) continue;
        int32_t vv = V - 32768;
        int y0 = vv >> 16;
        uint32_t fy = (uint32_t)(vv >> 8) & 255;
        if (y0 < isy0) { y0 = isy0; fy = 0; }
        if (y0 >= isy1) { y0 = isy1; fy = 0; }
        const uint32_t* r0 = s->px + (size_t)y0 * s->stride + s_lo;
        const uint32_t* r1 = fy ? r0 + s->stride : r0;
        uint32_t* T = tmp.data();
        int k = 0;
#ifdef __AVX2__
        if (fy) {
            const __m256i w = _mm256_set1_epi32((int)(fy | (fy << 16)));
            const __m256i iw = _mm256_set1_epi32((int)((256 - fy) | ((256 - fy) << 16)));
            for (; k + 8 <= span; k += 8)
                _mm256_storeu_si256((__m256i*)(T + k),
                                    v_lerp(_mm256_loadu_si256((const __m256i*)(r0 + k)),
                                           _mm256_loadu_si256((const __m256i*)(r1 + k)), w, iw));
        }
#endif
        if (fy) {
            for (; k < span; ++k) {
                uint32_t a = r0[k], b = r1[k];
                uint32_t rb = (((a & 0xFF00FF) * (256 - fy) + (b & 0xFF00FF) * fy) >> 8) & 0xFF00FF;
                uint32_t ag = ((((a >> 8) & 0xFF00FF) * (256 - fy) + ((b >> 8) & 0xFF00FF) * fy) >> 8) & 0xFF00FF;
                T[k] = rb | (ag << 8);
            }
        } else {
            std::memcpy(T, r0, (size_t)span * 4);
        }
        uint32_t* o = dst + (size_t)y * dstride + xa;
        int i = ia_;
#ifdef __AVX2__
        const __m256i a16 = _mm256_set1_epi32((int)(c.alpha | (c.alpha << 16)));
        const __m256i w256 = _mm256_set1_epi32(256 | (256 << 16));
        for (; i + 8 <= ib_ + 1; i += 8) {
            __m256i i0 = _mm256_loadu_si256((const __m256i*)(tx0.data() + i));
            __m256i i1 = _mm256_loadu_si256((const __m256i*)(tx1.data() + i));
            __m256i t0 = _mm256_i32gather_epi32((const int*)T, i0, 4);
            __m256i t1 = _mm256_i32gather_epi32((const int*)T, i1, 4);
            __m256i any = _mm256_or_si256(t0, t1);
            if (_mm256_testz_si256(any, any)) continue;
            __m256i f = _mm256_cvtepu16_epi32(_mm_loadu_si128((const __m128i*)(tfx.data() + i)));
            __m256i wx = _mm256_or_si256(f, _mm256_slli_epi32(f, 16));
            __m256i p = v_lerp(t0, t1, wx, _mm256_sub_epi16(w256, wx));
            if (c.alpha < 256) p = v_mul(p, a16);
            __m256i d = _mm256_loadu_si256((const __m256i*)(o + i));
            _mm256_storeu_si256((__m256i*)(o + i), v_blend(p, d, c.blend));
        }
#endif
        for (; i <= ib_; ++i) {
            uint32_t a = T[tx0[i]], b = T[tx1[i]];
            if ((a | b) == 0) continue;
            uint32_t f = tfx[i];
            uint32_t rb = (((a & 0xFF00FF) * (256 - f) + (b & 0xFF00FF) * f) >> 8) & 0xFF00FF;
            uint32_t ag = ((((a >> 8) & 0xFF00FF) * (256 - f) + ((b >> 8) & 0xFF00FF) * f) >> 8) & 0xFF00FF;
            uint32_t p = rb | (ag << 8);
            if (c.alpha < 256) p = mul_px(p, c.alpha);
            o[i] = c.blend == BL_PLUS ? plus_px(p, o[i]) : (c.blend == BL_SOURCE ? p : over_px(p, o[i]));
        }
    }
}

static void draw_band(const std::vector<Cmd>& cmds, uint32_t* dst, int dw, int dstride, int y0, int y1) {
    Scratch sc;
    for (const Cmd& c : cmds) {
        int ya = std::max(y0, c.by0), yb = std::min(y1, c.by1);
        int xa = std::max(0, c.bx0), xb = std::min(dw, c.bx1);
        if (ya >= yb || xa >= xb) continue;
        if (c.op == OP_FILL) {
            uint32_t col = c.color;
            for (int y = ya; y < yb; ++y) {
                uint32_t* o = dst + (size_t)y * dstride;
#ifdef __AVX2__
                if (!(c.blend == BL_SOURCE || (c.blend == BL_OVER && (col >> 24) == 255))) {
                    row_fill_avx2(o, xa, xb, col, c.blend);
                    continue;
                }
#endif
                if (c.blend == BL_SOURCE || (c.blend == BL_OVER && (col >> 24) == 255)) {
                    for (int x = xa; x < xb; ++x) o[x] = col;
                } else if (c.blend == BL_PLUS) {
                    for (int x = xa; x < xb; ++x) o[x] = plus_px(col, o[x]);
                } else {
                    for (int x = xa; x < xb; ++x) o[x] = over_px(col, o[x]);
                }
            }
            continue;
        }
        const Sprite* s = c.s;
        if (c.simple) {
            // целый сдвиг без масштаба: прямое копирование/смешение строк
            for (int y = ya; y < yb; ++y) {
                const uint32_t* src = s->px + (size_t)(y - c.oy) * s->stride - c.ox;
                uint32_t* o = dst + (size_t)y * dstride;
#ifdef __AVX2__
                if (!(c.blend == BL_SOURCE && c.alpha >= 256)) {
                    row_simple_avx2(src, o, xa, xb, c.alpha, c.blend);
                    continue;
                }
#endif
                if (c.blend == BL_SOURCE) {
                    if (c.alpha >= 256) std::memcpy(o + xa, src + xa, (size_t)(xb - xa) * 4);
                    else for (int x = xa; x < xb; ++x) o[x] = mul_px(src[x], c.alpha);
                } else if (c.blend == BL_PLUS) {
                    if (c.alpha >= 256) for (int x = xa; x < xb; ++x) o[x] = plus_px(src[x], o[x]);
                    else for (int x = xa; x < xb; ++x) o[x] = plus_px(mul_px(src[x], c.alpha), o[x]);
                } else if (c.alpha >= 256) {
                    for (int x = xa; x < xb; ++x) {
                        uint32_t p = src[x];
                        uint32_t a = p >> 24;
                        if (a == 255) o[x] = p;
                        else if (a) o[x] = over_px(p, o[x]);
                    }
                } else {
                    for (int x = xa; x < xb; ++x) {
                        uint32_t p = src[x];
                        if (p >> 24) o[x] = over_px(mul_px(p, c.alpha), o[x]);
                    }
                }
            }
            continue;
        }
        if (std::fabs(c.ib) < 1e-7f && std::fabs(c.ic) < 1e-7f) {   // без поворота
            draw_scaled(c, dst, dstride, xa, xb, ya, yb, sc);
            continue;
        }
        // общий случай: масштаб/поворот, билинейная выборка в фиксированной точке 16.16
        const int isx0 = (int)c.sx0, isy0 = (int)c.sy0;
        const int isx1 = (int)std::ceil(c.sx1) - 1, isy1 = (int)std::ceil(c.sy1) - 1;   // последний пиксель
        const int32_t SX0 = (int32_t)(c.sx0 * 65536.f), SY0 = (int32_t)(c.sy0 * 65536.f);
        const uint32_t SW = (uint32_t)((c.sx1 - c.sx0) * 65536.f), SH = (uint32_t)((c.sy1 - c.sy0) * 65536.f);
        const int32_t DU = (int32_t)(c.ia * 65536.f), DV = (int32_t)(c.ib * 65536.f);
        const uint32_t* spx = s->px;
        const int sst = s->stride;
        for (int y = ya; y < yb; ++y) {
            uint32_t* o = dst + (size_t)y * dstride;
            float fy = y + 0.5f, fx = xa + 0.5f;
            int32_t U = (int32_t)((c.ia * fx + c.ic * fy + c.itx) * 65536.f);
            int32_t V = (int32_t)((c.ib * fx + c.id * fy + c.ity) * 65536.f);
#ifdef __AVX2__
            row_xform_avx2(spx, sst, o, xa, xb, U, V, DU, DV, SX0, SY0, SW, SH, isx0, isy0, isx1, isy1, c.alpha, c.blend);
            continue;
#endif
            for (int x = xa; x < xb; ++x, U += DU, V += DV) {
                if ((uint32_t)(U - SX0) >= SW || (uint32_t)(V - SY0) >= SH) continue;
                int32_t uu = U - 32768, vv = V - 32768;          // центр выборки −0.5
                int x0 = uu >> 16, y0 = vv >> 16;
                uint32_t fx8 = (uint32_t)(uu >> 8) & 255, fy8 = (uint32_t)(vv >> 8) & 255;
                if (x0 < isx0) { x0 = isx0; fx8 = 0; }
                if (y0 < isy0) { y0 = isy0; fy8 = 0; }
                if (x0 >= isx1) { x0 = isx1; fx8 = 0; }
                if (y0 >= isy1) { y0 = isy1; fy8 = 0; }
                const uint32_t* r0 = spx + (size_t)y0 * sst + x0;
                const uint32_t* r1 = fy8 ? r0 + sst : r0;
                uint32_t a = r0[0], b = fx8 ? r0[1] : a, cc = r1[0], d = fx8 ? r1[1] : cc;
                if ((a | b | cc | d) == 0) continue;               // прозрачная область спрайта
                uint32_t ifx = 256 - fx8, ify = 256 - fy8;
                uint32_t rb_t = ((a & 0xFF00FF) * ifx + (b & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
                uint32_t ag_t = (((a >> 8) & 0xFF00FF) * ifx + ((b >> 8) & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
                uint32_t rb_b = ((cc & 0xFF00FF) * ifx + (d & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
                uint32_t ag_b = (((cc >> 8) & 0xFF00FF) * ifx + ((d >> 8) & 0xFF00FF) * fx8) >> 8 & 0xFF00FF;
                uint32_t p = (((rb_t * ify + rb_b * fy8) >> 8) & 0xFF00FF) | ((((ag_t * ify + ag_b * fy8) >> 8) & 0xFF00FF) << 8);
                if (c.alpha < 256) p = mul_px(p, c.alpha);
                if (c.blend == BL_PLUS) o[x] = plus_px(p, o[x]);
                else if (c.blend == BL_SOURCE) o[x] = p;
                else if ((p >> 24) == 255) o[x] = p;
                else if (p >> 24) o[x] = over_px(p, o[x]);
            }
        }
    }
}

}  // namespace

FX_API int spr_register(const void* bits, int w, int h, int stride_bytes) {
    SrwGuard lk(&g_mx);
    int id;
    if (!g_free.empty()) {
        id = g_free.back();
        g_free.pop_back();
    } else {
        id = (int)g_spr.size();
        g_spr.emplace_back();
    }
    Sprite& s = g_spr[id];
    s.px = (const uint32_t*)bits;
    s.w = w;
    s.h = h;
    s.stride = stride_bytes / 4;
    s.live = true;
    return id;
}

FX_API void spr_unregister(int id) {
    SrwGuard lk(&g_mx);
    if (id < 0 || id >= (int)g_spr.size() || !g_spr[id].live) return;
    std::free(g_spr[id].own);
    g_spr[id] = Sprite();
    g_free.push_back(id);
}

// то же, что spr_register, но пиксели копируются в собственную память библиотеки:
// исходную картинку можно сразу отпустить (Qt не придётся копировать её при перерисовке)
FX_API int spr_register_copy(const void* bits, int w, int h, int stride_bytes) {
    if (w <= 0 || h <= 0) return -1;
    uint32_t* mem = (uint32_t*)std::malloc((size_t)w * h * 4);
    if (!mem) return -1;
    for (int y = 0; y < h; ++y)
        std::memcpy(mem + (size_t)y * w, (const uint8_t*)bits + (size_t)y * stride_bytes, (size_t)w * 4);
    int id = spr_register(mem, w, h, w * 4);
    SrwGuard lk(&g_mx);
    g_spr[id].own = mem;
    return id;
}

// cmds: n записей по 16 float.
//  SPRITE: [0, id, a, b, c, d, tx, ty, sx, sy, sw, sh, alpha, blend, 0, 0]
//          (a b c d tx ty) переводит пиксель спрайта (u, v) в устройство: X = a·u + c·v + tx, Y = b·u + d·v + ty
//  FILL:   [1, 0, x, y, w, h, 0, 0, r, g, b, a, alpha, blend, 0, 0]  (цвет 0..1 непремультиплицированный)
// clip — прямоугольник устройства (x0, y0, x1, y1) или nullptr.
FX_API int spr_render(uint32_t* dst, int dw, int dh, int dstride_bytes, const float* rec, int n, const int* clip) {
    const int dstride = dstride_bytes / 4;
    int cx0 = 0, cy0 = 0, cx1 = dw, cy1 = dh;
    if (clip) {
        cx0 = std::max(0, clip[0]);
        cy0 = std::max(0, clip[1]);
        cx1 = std::min(dw, clip[2]);
        cy1 = std::min(dh, clip[3]);
    }
    std::vector<Cmd> cmds;
    cmds.reserve(n);
    {
        SrwGuard lk(&g_mx);
        for (int i = 0; i < n; ++i) {
            const float* r = rec + (size_t)i * REC;
            Cmd c{};
            c.op = (int)r[0];
            c.blend = (int)r[13];
            float al = r[12];
            if (al <= 0.002f) continue;
            c.alpha = al >= 0.998f ? 256u : (uint32_t)(al * 256.f + 0.5f);
            if (c.op == OP_FILL) {
                float x = r[2], y = r[3], w = r[4], h = r[5];
                c.bx0 = std::max(cx0, (int)std::floor(x + 0.5f));
                c.by0 = std::max(cy0, (int)std::floor(y + 0.5f));
                c.bx1 = std::min(cx1, (int)std::floor(x + w + 0.5f));
                c.by1 = std::min(cy1, (int)std::floor(y + h + 0.5f));
                float a = clamp01(r[11] * al);
                uint32_t A = (uint32_t)(a * 255.f + 0.5f);
                uint32_t R = (uint32_t)(clamp01(r[8]) * a * 255.f + 0.5f);
                uint32_t G = (uint32_t)(clamp01(r[9]) * a * 255.f + 0.5f);
                uint32_t B = (uint32_t)(clamp01(r[10]) * a * 255.f + 0.5f);
                c.color = (A << 24) | (R << 16) | (G << 8) | B;
                if (c.bx0 < c.bx1 && c.by0 < c.by1) cmds.push_back(c);
                continue;
            }
            int id = (int)r[1];
            if (id < 0 || id >= (int)g_spr.size() || !g_spr[id].live) continue;
            c.s = &g_spr[id];
            float a = r[2], b = r[3], cc = r[4], d = r[5], tx = r[6], ty = r[7];
            float sx = r[8], sy = r[9], sw = r[10], sh = r[11];
            sx = std::max(0.f, sx);
            sy = std::max(0.f, sy);
            sw = std::min(sw, (float)c.s->w - sx);
            sh = std::min(sh, (float)c.s->h - sy);
            if (sw <= 0.f || sh <= 0.f) continue;
            float det = a * d - b * cc;
            if (std::fabs(det) < 1e-9f) continue;
            float inv = 1.f / det;
            c.ia = d * inv;
            c.ib = -b * inv;
            c.ic = -cc * inv;
            c.id = a * inv;
            c.itx = -(c.ia * tx + c.ic * ty);
            c.ity = -(c.ib * tx + c.id * ty);
            c.sx0 = sx;
            c.sy0 = sy;
            c.sx1 = sx + sw;
            c.sy1 = sy + sh;
            float xs[4], ys[4];
            float us[4] = {sx, sx + sw, sx, sx + sw}, vs[4] = {sy, sy, sy + sh, sy + sh};
            float mnx = 1e30f, mny = 1e30f, mxx = -1e30f, mxy = -1e30f;
            for (int k = 0; k < 4; ++k) {
                xs[k] = a * us[k] + cc * vs[k] + tx;
                ys[k] = b * us[k] + d * vs[k] + ty;
                mnx = std::min(mnx, xs[k]);
                mxx = std::max(mxx, xs[k]);
                mny = std::min(mny, ys[k]);
                mxy = std::max(mxy, ys[k]);
            }
            c.bx0 = std::max(cx0, (int)std::floor(mnx));
            c.by0 = std::max(cy0, (int)std::floor(mny));
            c.bx1 = std::min(cx1, (int)std::ceil(mxx));
            c.by1 = std::min(cy1, (int)std::ceil(mxy));
            if (c.bx0 >= c.bx1 || c.by0 >= c.by1) continue;
            // без масштаба и поворота, на целых пикселях — быстрый путь
            float rtx = std::nearbyint(tx + a * sx) - a * sx, rty = std::nearbyint(ty + d * sy) - d * sy;
            if (std::fabs(a - 1.f) < 1e-4f && std::fabs(d - 1.f) < 1e-4f && std::fabs(b) < 1e-5f &&
                std::fabs(cc) < 1e-5f && std::fabs(tx - rtx) < 0.02f && std::fabs(ty - rty) < 0.02f) {
                c.simple = true;
                c.ox = (int)std::nearbyint(tx);
                c.oy = (int)std::nearbyint(ty);
                // строго внутри источника
                c.bx0 = std::max(c.bx0, (int)sx + c.ox);
                c.by0 = std::max(c.by0, (int)sy + c.oy);
                c.bx1 = std::min(c.bx1, (int)(sx + sw) + c.ox);
                c.by1 = std::min(c.by1, (int)(sy + sh) + c.oy);
                if (c.bx0 >= c.bx1 || c.by0 >= c.by1) continue;
            }
            cmds.push_back(c);
        }
    }
    if (cmds.empty()) return 0;
    // мелкая пачка (пара спрайтов) — прямо в вызывающем потоке: будить пул дороже самой работы
    double area = 0;
    int ylo = dh, yhi = 0;
    for (const Cmd& c : cmds) {
        area += (double)(c.bx1 - c.bx0) * (c.by1 - c.by0);
        ylo = std::min(ylo, c.by0);
        yhi = std::max(yhi, c.by1);
    }
    if (area < 120000.0 || yhi - ylo < 32) {
        draw_band(cmds, dst, dw, dstride, std::max(0, ylo), std::min(dh, yhi));
        return (int)cmds.size();
    }
    // полосы по 24 строки — достаточно мелко для балансировки, крупно для кэша
    int band = 24;
    int nb = (yhi - ylo + band - 1) / band;
    std::function<void(int)> fn = [&](int i) {
        int y0 = ylo + i * band, y1 = std::min(yhi, y0 + band);
        draw_band(cmds, dst, dw, dstride, y0, y1);
    };
    Pool::get().run(nb, fn);
    return (int)cmds.size();
}
