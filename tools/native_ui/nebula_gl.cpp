// Туманность «Моей волны» на видеокарте (QOpenGLWidget + фрагментный шейдер).
//
// Формулы — те же, что у tools/native/nebula.cpp (пятна в закрученном поле течения, нити света,
// кольца ударов, неровная граница облака, сияние по краю, лучи вбок, зерно), только считаются для
// каждого пикселя на GPU, без сетки и растяжения. Параметры кадра (время, пятна, кольца, цвета)
// по-прежнему готовит Python (ya_wave.NebulaField) и передаёт той же структурой NebParams.
// Поверх туманности рисуется «накладка» (чёрная полоса под списком, градиент, заголовок) —
// QPixmap, который Python обновляет только когда она меняется.
//
// Виджет сам по себе на GPU, поэтому кадр туманности не перерисовывает ни список волн, ни кнопки
// поверх неё: их совмещает видеокарта.
#include <QtOpenGL/QOpenGLShaderProgram>
#include <QtOpenGL/QOpenGLBuffer>
#include <QtOpenGL/QOpenGLTexture>
#include <QtOpenGLWidgets/QOpenGLWidget>
#include <QtGui/QOpenGLFunctions>
#include <QtGui/QPainter>
#include <QtGui/QPixmap>
#include <QtGui/QRegion>
#include <QtGui/QVector2D>
#include <QtGui/QVector3D>
#include <QtGui/QVector4D>
#include <algorithm>
#include <cstring>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

constexpr int MAXB = 12;
constexpr int MAXR = 6;

struct NebParams {                 // как в tools/native/nebula.cpp и native_fx.NebParams
    int W, H, x0;
    int gw, gh;
    float cx, cy, R;
    float T, flow, energy, kick;
    float gain, fil_amt, ray_amt, grain;
    float fil_col[3];
    int nblobs;
    float blobs[MAXB * 7];
    int nrings;
    float rings[MAXR * 2];
    float ring_col[3];
    int seed;
    float env_r, lobe;
};

const char *VERT = R"(
attribute vec2 pos;
void main() { gl_Position = vec4(pos, 0.0, 1.0); }
)";

// «Моя волна» на GPU (в духе экрана Яндекс Музыки): яркие насыщенные цветовые поля, перетекающие
// в закрученном поле течения, тёмные «провалы» между ними, мягкие световые складки, ядро-звезда в центре. uBurst (1 → 0 за пару секунд после включения волны): лучи вылетают длинными
// и яркими, поле расширяется и течёт быстрее, потом всё успокаивается. Цвета и положения пятен
// приходят из Python (NebulaField: схема настроения / обложки, орбиты, удар баса, кольца).
const char *FRAG = R"(
#ifdef GL_ES
precision highp float;
#endif
uniform vec2 uSize;
uniform float uX0;
uniform vec3 uCenter;          // cx, cy, R
uniform vec4 uTime;            // t, energy, kick, gain
uniform vec4 uAmt;             // fil_amt, ray_amt, grain, env_r
uniform float uLobe;
uniform vec3 uFilCol;
uniform vec3 uRingCol;
uniform int uNB;
uniform vec3 uBPos[12];
uniform vec4 uBCol[12];
uniform int uNR;
uniform vec2 uRings[6];
uniform float uSeed;
uniform float uBurst;
uniform float uScale;          // >1 — облако компактнее (страница «Моя волна»), 1 — как на баннере

float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233)) + uSeed) * 43758.5453); }
float n2(vec2 p, float t) {
    return sin(p.x * 1.7 + t * 0.6 + sin(p.y * 1.3 - t * 0.4)) * cos(p.y * 1.9 - t * 0.5 + sin(p.x * 1.1 + t * 0.3));
}
vec2 warp(vec2 p, float t) {
    vec2 q = p + 0.34 * vec2(n2(p * 1.05, t), n2(p * 1.05 + 5.2, t * 1.1));
    q += 0.16 * vec2(n2(q * 2.2 + 1.7, t * 1.3), n2(q * 2.2 + 9.1, t * 0.9));
    return q;
}

// Один слой туманности по координатам q (уже с течением): цвет масс, провалы, складки.
vec3 nebulaLayer(vec2 q, float t, float energy, float kick, out float dens) {
    vec3 acc = vec3(0.0);
    float wsum = 0.0, w2sum = 0.0;
    for (int b = 0; b < 12; ++b) {
        if (b >= uNB) break;
        vec2 d = q - uBPos[b].xy * 1.5;
        float w = exp(-dot(d, d) * uBPos[b].z * 0.40) * uBCol[b].w;
        acc += w * w * uBCol[b].rgb;
        wsum += w;
        w2sum += w * w;
    }
    vec3 hue = acc / max(w2sum, 1e-4);
    float lum = dot(hue, vec3(0.299, 0.587, 0.114));
    hue = clamp(mix(vec3(lum), hue, 1.5), 0.0, 1.0);    // насыщеннее
    dens = 1.0 - exp(-wsum * 1.8);
    float pocket = 0.5 + 0.5 * n2(q * 1.35 + 3.0, t * 0.6);
    dens *= mix(0.88, 1.0, smoothstep(0.18, 0.80, pocket));   // лёгкая неоднородность, без тёмных провалов
    float band = 0.5 + 0.5 * sin(q.x * 2.3 + q.y * 1.5 + t * 0.32 + 2.2 * n2(q * 1.25, t * 0.8));
    float fold = pow(band, 6.0) * smoothstep(0.40, 0.90, 0.5 + 0.5 * n2(q * 0.9 + 7.0, t * 0.5));
    return hue * dens + mix(hue, vec3(1.0), 0.5) * fold * dens * (0.22 + uAmt.x * 0.8);
}

void main() {
    float px = gl_FragCoord.x;
    float py = uSize.y - gl_FragCoord.y;
    float t = uTime.x, energy = uTime.y, kick = uTime.z;
    float R = max(1.0, uCenter.z);
    vec2 p = vec2(px - uCenter.x, py - uCenter.y) / R;
    p *= uScale;
    p /= 1.0 + 0.35 * uBurst;                       // при включении — поле шире и ближе
    // Течение из ядра: два слоя узора непрерывно расходятся из центра наружу (масштаб растёт вдвое
    // за цикл) и сменяют друг друга без скачков — узор рождается в ядре и гаснет у краёв.
    float tf = t * 0.18;                                // своё перетекание — слабое, главное движение — из ядра
    float flow = t * 0.07;                              // скорость вылета из ядра
    vec3 lay = vec3(0.0);
    float wl = 0.0, dl = 0.0;
    for (int i = 0; i < 2; ++i) {
        float cyc = flow + 0.5 * float(i);
        float ph = fract(cyc);
        float w = sin(3.14159 * ph);
        float d;                                        // новый цикл — другой узор (в момент смены вес слоя 0)
        vec3 c = nebulaLayer(warp(p / exp2(ph), tf + floor(cyc) * 11.0), tf, energy, kick, d);
        lay += c * w;
        dl += d * w;
        wl += w;
    }
    lay /= max(wl, 1e-3);
    dl /= max(wl, 1e-3);
    float r = length(p);
    vec3 hue = lay / max(dl, 1e-3);
    float dens = max(dl, exp(-r * r * 0.9) * 0.85);              // внутри туманности темно не бывает — гаснет только край
    float edge = exp(-pow(r / (1.5 + 0.2 * energy + 0.5 * uBurst), 3.0));
    vec3 col = max(lay, hue * dens) * edge * (1.0 + 0.4 * energy + 0.5 * kick);
    // центр — как ядро звезды: широкое светлое свечение в цвет волны и горячая почти белая середина
    float pulse = 1.0 + 0.25 * energy + 0.35 * kick + 0.6 * uBurst;
    col += mix(hue, vec3(1.0), 0.35) * exp(-r * r * 2.4) * 0.55 * pulse;
    col += mix(hue, vec3(1.0), 0.75) * exp(-r * r * 9.0) * 0.95 * pulse;
    col += vec3(1.0) * exp(-r * r * 40.0) * 0.8 * pulse;
    float ang = atan(p.y, p.x);
    // яркие пучки: тонкие лучи разной силы из самого центра, медленно плавают;
    // при включении волны — длинные и яркие, потом гаснут
    float beams = 0.0;
    for (int k = 0; k < 9; ++k) {
        float fk = float(k);
        float a0 = fk * 2.39996 + 0.45 * sin(t * (0.04 + fk * 0.011) + fk * 2.1);   // золотой угол — без «цветка»
        float c = max(cos(ang - a0), 0.0);
        float sharp = 140.0 + 900.0 * pow(0.5 + 0.5 * sin(fk * 3.7 + 1.1), 2.0);
        float wgt = 0.15 + 0.85 * pow(0.5 + 0.5 * sin(fk * 12.9 + t * 0.07), 3.0);
        beams += (pow(c, sharp) + 0.25 * pow(c, sharp * 0.12)) * wgt;              // ядро луча + мягкий ореол
    }
    float len = 0.55 + 2.8 * uBurst + 0.4 * kick + 0.25 * energy;
    float rfall = smoothstep(0.22, 0.55, r) * exp(-max(r - 0.35, 0.0) / len * 1.25);
    vec3 rcol = mix(vec3(1.0), hue, 0.45);
    col += rcol * beams * rfall * (0.06 + 0.5 * uAmt.y + 2.4 * uBurst);
    for (int k = 0; k < 6; ++k) {                   // кольца на удар баса
        if (k >= uNR) break;
        float d = (r - uRings[k].x) / 0.13;
        col += uRingCol * exp(-d * d) * uRings[k].y * edge * 0.8;
    }
    col *= 1.0 + 0.6 * uBurst;
    col = vec3(1.0) - exp(-col * (1.35 + 0.15 * energy));          // мягкое сжатие яркости
    float n = (hash(gl_FragCoord.xy) + hash(gl_FragCoord.yx + 17.0) - 1.0) * uAmt.z;
    gl_FragColor = vec4(clamp(col + n, 0.0, 1.0), 1.0);
}
)";

// Накладка (QImage, RGBA, премультипл.) — текстурой поверх туманности внутри того же кадра GL:
// так её видят и экран, и снимки виджета (QPainter на QOpenGLWidget при снимке уходит под кадр).
const char *OVL_FRAG = R"(
#ifdef GL_ES
precision mediump float;
#endif
uniform sampler2D uTex;
uniform vec2 uSize;
void main() {
    vec2 uv = vec2(gl_FragCoord.x / uSize.x, 1.0 - gl_FragCoord.y / uSize.y);
    gl_FragColor = texture2D(uTex, uv);
}
)";

class NebulaGL final : public QOpenGLWidget, protected QOpenGLFunctions {
public:
    explicit NebulaGL(QWidget *parent = nullptr) : QOpenGLWidget(parent) {
        std::memset(&p_, 0, sizeof p_);
        setAttribute(Qt::WA_TransparentForMouseEvents);     // щелчки — странице под ним
        setUpdateBehavior(QOpenGLWidget::NoPartialUpdate);
    }
    ~NebulaGL() override {
        makeCurrent();
        vbo_.destroy();
        delete prog_;
        delete ovl_prog_;
        delete tex_;
        doneCurrent();
    }
    NebParams p_;
    bool have_ = false;
    QImage overlay_;
    bool overlay_dirty_ = false;
    float burst_ = 0.f;                                     // вспышка при включении волны (1 → 0)
    float scale_ = 1.f;
    bool ok_ = true;                                        // шейдер собрался

protected:
    void initializeGL() override {
        initializeOpenGLFunctions();
        prog_ = new QOpenGLShaderProgram;
        ok_ = prog_->addShaderFromSourceCode(QOpenGLShader::Vertex, VERT) &&
              prog_->addShaderFromSourceCode(QOpenGLShader::Fragment, FRAG);
        prog_->bindAttributeLocation("pos", 0);
        ok_ = ok_ && prog_->link();
        if (!ok_) qWarning("[echoes_ui] nebula shader: %s", qPrintable(prog_->log()));
        ovl_prog_ = new QOpenGLShaderProgram;
        bool ook = ovl_prog_->addShaderFromSourceCode(QOpenGLShader::Vertex, VERT) &&
                   ovl_prog_->addShaderFromSourceCode(QOpenGLShader::Fragment, OVL_FRAG);
        ovl_prog_->bindAttributeLocation("pos", 0);
        ok_ = ok_ && ook && ovl_prog_->link();
        const float quad[] = {-1, -1, 1, -1, -1, 1, 1, 1};
        vbo_.create();
        vbo_.bind();
        vbo_.allocate(quad, sizeof quad);
        vbo_.release();
    }
    void paintGL() override {
        const qreal dpr = devicePixelRatioF();
        const int W = int(width() * dpr), H = int(height() * dpr);
        glViewport(0, 0, W, H);
        glClearColor(0, 0, 0, 1);
        glClear(GL_COLOR_BUFFER_BIT);
        if (ok_ && have_) {
            prog_->bind();
            prog_->setUniformValue("uSize", QVector2D(W, H));
            prog_->setUniformValue("uX0", float(p_.x0));
            prog_->setUniformValue("uCenter", QVector3D(p_.cx, p_.cy, p_.R));
            prog_->setUniformValue("uTime", QVector4D(p_.T * p_.flow, p_.energy, p_.kick, p_.gain));
            prog_->setUniformValue("uAmt", QVector4D(p_.fil_amt, p_.ray_amt, p_.grain, p_.env_r));
            prog_->setUniformValue("uLobe", p_.lobe);
            prog_->setUniformValue("uFilCol", QVector3D(p_.fil_col[0], p_.fil_col[1], p_.fil_col[2]));
            prog_->setUniformValue("uRingCol", QVector3D(p_.ring_col[0], p_.ring_col[1], p_.ring_col[2]));
            const int nb = std::clamp(p_.nblobs, 0, MAXB), nr = std::clamp(p_.nrings, 0, MAXR);
            QVector3D bpos[MAXB];
            QVector4D bcol[MAXB];
            for (int b = 0; b < nb; ++b) {
                const float *B = p_.blobs + b * 7;
                bpos[b] = QVector3D(B[0], B[1], B[2]);
                bcol[b] = QVector4D(B[3], B[4], B[5], B[6]);
            }
            QVector2D rings[MAXR];
            for (int k = 0; k < nr; ++k) rings[k] = QVector2D(p_.rings[k * 2], p_.rings[k * 2 + 1]);
            prog_->setUniformValue("uNB", nb);
            prog_->setUniformValueArray("uBPos", bpos, MAXB);
            prog_->setUniformValueArray("uBCol", bcol, MAXB);
            prog_->setUniformValue("uNR", nr);
            prog_->setUniformValueArray("uRings", rings, MAXR);
            prog_->setUniformValue("uSeed", float(p_.seed % 997) * 0.618f);
            prog_->setUniformValue("uBurst", burst_);
            prog_->setUniformValue("uScale", scale_);
            vbo_.bind();
            prog_->enableAttributeArray(0);
            prog_->setAttributeBuffer(0, GL_FLOAT, 0, 2);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
            prog_->disableAttributeArray(0);
            vbo_.release();
            prog_->release();
        }
        if (overlay_dirty_) {
            overlay_dirty_ = false;
            delete tex_;
            tex_ = nullptr;
            if (!overlay_.isNull()) {
                QImage img = overlay_.convertToFormat(QImage::Format_RGBA8888_Premultiplied);
                tex_ = new QOpenGLTexture(QOpenGLTexture::Target2D);
                tex_->setFormat(QOpenGLTexture::RGBA8_UNorm);
                tex_->setSize(img.width(), img.height());
                tex_->setMinMagFilters(QOpenGLTexture::Nearest, QOpenGLTexture::Nearest);
                tex_->setWrapMode(QOpenGLTexture::ClampToEdge);
                tex_->allocateStorage();
                tex_->setData(QOpenGLTexture::RGBA, QOpenGLTexture::UInt8, img.constBits());
            }
        }
        if (ok_ && tex_) {                                  // чёрная полоса, градиент, заголовок
            glEnable(GL_BLEND);
            glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
            ovl_prog_->bind();
            tex_->bind(0);
            ovl_prog_->setUniformValue("uTex", 0);
            ovl_prog_->setUniformValue("uSize", QVector2D(W, H));
            vbo_.bind();
            ovl_prog_->enableAttributeArray(0);
            ovl_prog_->setAttributeBuffer(0, GL_FLOAT, 0, 2);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
            ovl_prog_->disableAttributeArray(0);
            vbo_.release();
            tex_->release();
            ovl_prog_->release();
            glDisable(GL_BLEND);
        }
    }

private:
    QOpenGLShaderProgram *prog_ = nullptr;
    QOpenGLShaderProgram *ovl_prog_ = nullptr;
    QOpenGLTexture *tex_ = nullptr;
    QOpenGLBuffer vbo_{QOpenGLBuffer::VertexBuffer};
};

NebulaGL *N(void *w) { return static_cast<NebulaGL *>(static_cast<QWidget *>(w)); }

}  // namespace

EUI_API void *eui_nebula_new() { return static_cast<QWidget *>(new NebulaGL); }

// Готов ли OpenGL: контекст создан и шейдер собрался (проверять после первого показа).
EUI_API int eui_nebula_ok(void *w) {
    NebulaGL *n = N(w);
    return n->isValid() && n->ok_ ? 1 : 0;
}

EUI_API void eui_nebula_params(void *w, const void *params) {
    NebulaGL *n = N(w);
    std::memcpy(&n->p_, params, sizeof(NebParams));
    n->have_ = true;
    n->update();
}

EUI_API void eui_nebula_burst(void *w, float burst) { N(w)->burst_ = burst; }
EUI_API void eui_nebula_scale(void *w, float scale) { N(w)->scale_ = scale; N(w)->update(); }

EUI_API void eui_nebula_overlay(void *w, const QPixmap *pm) {
    NebulaGL *n = N(w);
    n->overlay_ = pm ? pm->toImage() : QImage();
    n->overlay_dirty_ = true;
    n->update();
}
