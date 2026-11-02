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

// Перенос nebula_render: u, v — координаты в долях R от центра облака.
const char *FRAG = R"(
#ifdef GL_ES
precision highp float;
#endif
uniform vec2 uSize;            // пиксели устройства
uniform float uX0;
uniform vec3 uCenter;          // cx, cy, R
uniform vec4 uTime;            // t = T*flow, energy, kick, gain
uniform vec4 uAmt;             // fil_amt, ray_amt, grain, env_r
uniform float uLobe;
uniform vec3 uFilCol;
uniform vec3 uRingCol;
uniform int uNB;
uniform vec3 uBPos[12];        // x, y, 1/r^2
uniform vec4 uBCol[12];        // r, g, b, сила
uniform int uNR;
uniform vec2 uRings[6];        // радиус, сила
uniform float uSeed;

float c01(float x) { return clamp(x, 0.0, 1.0); }
float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233)) + uSeed) * 43758.5453); }

void main() {
    float px = gl_FragCoord.x;
    float py = uSize.y - gl_FragCoord.y;
    if (px < uX0) { gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }
    float invR = 1.0 / max(1.0, uCenter.z);
    float u = (px - uCenter.x) * invR;
    float v = (py - uCenter.y) * invR;
    float t = uTime.x;
    float w1x = u + 0.22 * sin(v * 1.9 + t * 0.70) + 0.11 * sin(v * 3.7 - t * 0.50 + u * 1.3);
    float w1y = v + 0.22 * sin(u * 1.7 - t * 0.60) + 0.11 * sin(u * 3.3 + t * 0.45 - v * 1.1);
    float w2x = w1x + 0.10 * sin(w1y * 5.1 + t * 0.90);
    float w2y = w1y + 0.10 * sin(w1x * 4.7 - t * 0.80);
    vec3 a = vec3(0.0);
    for (int b = 0; b < 12; ++b) {
        if (b >= uNB) break;
        float dx = (u + (w2x - u) * 0.55) - uBPos[b].x;
        float dy = (v + (w2y - v) * 0.55) - uBPos[b].y;
        float e = exp(-(dx * dx + dy * dy) * uBPos[b].z) * uBCol[b].w;
        a += e * uBCol[b].rgb;
    }
    float lum = max(a.r, max(a.g, a.b));
    float f = sin((sin(w1x * 2.3 + sin(w1y * 1.7 + t * 0.3)) + sin(w1y * 2.1 - sin(w1x * 1.9 - t * 0.25))) * 2.6);
    float fq = f * (1.0 / 0.16);
    float fw = f * (1.0 / 0.55);
    float fil = exp(-fq * fq) * 0.6 + exp(-fw * fw) * 0.25;
    fil *= c01(lum * 1.6) * (1.0 - 0.45 * c01(lum - 0.7)) * uAmt.x;
    float inv = 1.0 / max(lum, 0.05);
    a += fil * (0.4 * uFilCol + 0.6 * clamp(a * inv, 0.0, 1.0));
    float r = sqrt(u * u + v * v);
    for (int k = 0; k < 6; ++k) {
        if (k >= uNR) break;
        float d = (r - uRings[k].x) * (1.0 / 0.14);
        a += exp(-d * d) * uRings[k].y * uRingCol;
    }
    float ang = atan(v, u);
    float rs = uAmt.w * (1.0 + uLobe * (0.55 * sin(3.0 * ang + t * 0.30) + 0.35 * sin(5.0 * ang - t * 0.42) +
                                       0.25 * sin(2.0 * ang + t * 0.21 + 1.7)));
    rs = max(rs, 0.05);
    float e2 = r / rs;
    e2 *= e2;
    float vig = exp(-e2 * e2 * 1.1);
    vec3 bb = (vec3(1.0) - exp(-a * uTime.w)) * vig;
    float rw = sqrt(w1x * w1x + w1y * w1y) / rs;
    float m1 = 0.5 + 0.5 * sin(2.0 * ang + t * 0.35 + 1.3 * sin(3.0 * ang - t * 0.2));
    float m2 = 0.5 + 0.5 * sin(3.0 * ang - t * 0.28 + 2.1);
    float d1 = (rw - 0.86) * (1.0 / 0.045), d1w = (rw - 0.86) * (1.0 / 0.17);
    float d2 = (rw - 1.04) * (1.0 / 0.04), d2w = (rw - 1.04) * (1.0 / 0.14);
    float rim = (exp(-d1 * d1) * 0.45 + exp(-d1w * d1w) * 0.30) * (0.15 + 0.85 * m1 * m1) +
                (exp(-d2 * d2) * 0.22 + exp(-d2w * d2w) * 0.14) * m2 * m2 * m2;
    rim *= uAmt.x * 2.2;
    float ray = 0.0;
    if (uAmt.y > 0.0) {
        float q = 0.5 + 0.5 * sin(ang * 5.0 + 2.0 * sin(ang * 2.0 + t * 0.10) + t * 0.03);
        q *= q; q *= q; q *= q; q *= q; q *= q;
        float q2 = 0.5 + 0.5 * sin(ang * 11.0 - 1.5 * sin(ang * 3.0 - t * 0.08) - t * 0.05);
        q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2; q2 *= q2;
        float ca = abs(u) / max(r, 1e-4);
        float hw = ca * ca; hw *= hw; hw *= hw; hw *= ca;
        float dr = r - rs * 0.65;
        float fall = dr > 0.0 ? c01(dr * 2.0) * exp(-dr * 0.85) : 0.0;
        ray = (q + 0.35 * q2) * hw * fall * uAmt.y * 2.2;
    }
    vec3 xc = 0.55 + 0.45 * uFilCol;
    vec3 ex = rim * (0.3 * xc + 0.7 * clamp(a * inv + 0.1, 0.0, 1.0)) + ray * (0.6 * xc + 0.4 * clamp(a * inv, 0.0, 1.0));
    vec3 col = vec3(1.0) - (vec3(1.0) - bb) * exp(-ex * 1.4);
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

EUI_API void eui_nebula_overlay(void *w, const QPixmap *pm) {
    NebulaGL *n = N(w);
    n->overlay_ = pm ? pm->toImage() : QImage();
    n->overlay_dirty_ = true;
    n->update();
}
