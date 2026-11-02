// Огонь вокруг карточки «Дуэли треков» на видеокарте (music_duel.Arena на GL-холсте).
//
// Раньше каждый кадр Python строил 36–88 языков пламени кривыми и заливал их градиентами на процессоре —
// ~34 мс на карточку, 11 кадров/с. Здесь — один четырёхугольник вокруг карточки и процедурный шейдер:
// языки пламени растут из краёв скруглённого прямоугольника (расстояние до контура + шум, текущий вверх),
// снизу ниже и загибаются вверх, искры летят от краёв. Рисуется между QPainter::beginNativePainting и
// endNativePainting — с тем же преобразованием, что у карточки (наклон в 3D, масштаб, поворот).
#include <QtGui/QOpenGLContext>
#include <QtGui/QOpenGLFunctions>
#include <QtGui/QTransform>
#include <QtGui/QColor>
#include <QtGui/QVector2D>
#include <QtGui/QVector3D>
#include <QtGui/QVector4D>
#include <QtGui/QMatrix3x3>
#include <QtOpenGL/QOpenGLShaderProgram>
#include <QtOpenGL/QOpenGLBuffer>
#include <QtCore/QHash>
#include <QtCore/QObject>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

const char *FVERT = R"(
attribute vec2 pos;              // 0..1 по прямоугольнику вокруг карточки
uniform vec4 uBox;               // x, y, w, h — в координатах карточки (центр 0,0), лог. пиксели
uniform mat3 uT;                 // карточка → пиксели устройства (как у QPainter, вместе с dpr)
uniform vec2 uFb;                // размер кадра, пиксели устройства
varying vec2 q;
void main() {
    q = uBox.xy + pos * uBox.zw;
    vec3 d = uT * vec3(q, 1.0);
    vec2 px = d.xy / d.z;
    gl_Position = vec4(px.x / uFb.x * 2.0 - 1.0, 1.0 - px.y / uFb.y * 2.0, 0.0, 1.0);
}
)";

const char *FFRAG = R"(
#ifdef GL_ES
precision highp float;
#endif
varying vec2 q;
uniform vec2 uHalf;              // половина карточки
uniform vec3 uCol;
uniform float uTime, uReach, uAlpha;
uniform int uOct;

float h21(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float n2(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h21(i), h21(i + vec2(1, 0)), f.x), mix(h21(i + vec2(0, 1)), h21(i + vec2(1, 1)), f.x), f.y);
}
float fbm(vec2 p) {
    float s = 0.0, a = 0.5;
    for (int i = 0; i < 5; ++i) {
        if (i >= uOct) break;
        s += a * n2(p); p = p * 2.03 + vec2(1.7, -0.6); a *= 0.5;
    }
    return s;
}
float sdRound(vec2 p, vec2 b, float r) {
    vec2 d = abs(p) - b + vec2(r);
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - r;
}
void main() {
    float d = sdRound(q, uHalf, 24.0);
    if (d < -2.0) { gl_FragColor = vec4(0.0); return; }
    // длина вдоль контура ~ угол вокруг центра, растянутый по периметру
    float ang = atan(q.y / uHalf.y, q.x / uHalf.x);
    float along = ang * (uHalf.x + uHalf.y) * 0.64;
    // сверху языки выше, по бокам — средние, снизу короткие и загибаются вверх
    float side = clamp(q.y / uHalf.y, -1.0, 1.0);
    float reach = uReach * mix(1.35, 0.42, smoothstep(-0.3, 1.0, side));
    float t = uTime;
    float k = clamp(d / reach, 0.0, 1.3);
    // языки: узкие полосы вдоль контура, колышутся и бегут наружу; верх языка рвётся шумом
    float sway = fbm(vec2(along / 40.0, t * 0.7)) * 2.0 - 1.0;
    float lane = fbm(vec2(along / 11.0 + sway * 0.9 + k * 0.8, k * 1.6 - t * 1.9));
    float tear = fbm(vec2(along / 7.0 - t * 0.3, d / 9.0 - t * 3.2));
    float body = lane * 1.6 + tear * 0.55 - k * 1.55 - 0.18;
    float flame = smoothstep(0.0, 0.32, body) * smoothstep(-2.0, 2.0, d);
    float hot = smoothstep(0.32, 0.8, body) * (1.0 - k);
    // мягкое свечение у самого края — огонь «сидит» на карточке
    float rim = exp(-max(d, 0.0) / 7.0) * 0.28 * smoothstep(-2.0, 0.5, d);
    // искры: редкие точки, летят наружу и вверх
    vec2 g = vec2(along / 10.0, (d - t * 52.0) / 10.0);
    float sp = step(0.988, h21(floor(g))) * (1.0 - smoothstep(0.15, 1.5, k)) * step(2.0, d);
    sp *= smoothstep(0.24, 0.0, length(fract(g) - 0.5));
    vec3 c = mix(uCol, mix(uCol, vec3(1.0), 0.6), hot);
    float a = clamp(flame * 0.5 + hot * 0.3 + rim + sp * 0.9, 0.0, 1.0) * uAlpha;
    vec3 rgb = c * a + vec3(1.0) * sp * 0.3 * uAlpha;
    gl_FragColor = vec4(rgb, a);                              // премультиплицированный
}
)";

struct Prog {
    QOpenGLShaderProgram *prog = nullptr;
    QOpenGLBuffer *vbo = nullptr;
    bool ok = false;
};
QHash<QOpenGLContext *, Prog> g_progs;

Prog &prog_for(QOpenGLContext *ctx) {
    auto it = g_progs.find(ctx);
    if (it != g_progs.end()) return it.value();
    Prog &pr = g_progs[ctx];
    pr.prog = new QOpenGLShaderProgram;
    pr.ok = pr.prog->addShaderFromSourceCode(QOpenGLShader::Vertex, FVERT) &&
            pr.prog->addShaderFromSourceCode(QOpenGLShader::Fragment, FFRAG);
    pr.prog->bindAttributeLocation("pos", 0);
    pr.ok = pr.ok && pr.prog->link();
    pr.vbo = new QOpenGLBuffer(QOpenGLBuffer::VertexBuffer);
    pr.vbo->create();
    pr.vbo->bind();
    const float quad[] = {0, 0, 1, 0, 0, 1, 1, 1};
    pr.vbo->allocate(quad, sizeof quad);
    pr.vbo->release();
    QObject::connect(ctx, &QOpenGLContext::aboutToBeDestroyed, [ctx]() {
        Prog p = g_progs.take(ctx);
        delete p.prog;
        if (p.vbo) { p.vbo->destroy(); delete p.vbo; }
    });
    return pr;
}

}  // namespace

// t — QPainter::combinedTransform() (карточка с центром в 0,0 → лог. пиксели виджета); dpr; fbw/fbh — кадр
// в пикселях устройства; cw/ch — размер карточки; margin — сколько пламени вокруг; quality 0..2.
EUI_API int eui_fire_draw(const QTransform *t, float dpr, float fbw, float fbh, float cw, float ch, float margin,
                          const QColor *color, int tier, float time, float alpha, int quality) {
    QOpenGLContext *ctx = QOpenGLContext::currentContext();
    if (!ctx || !t || !color) return 0;
    Prog &pr = prog_for(ctx);
    if (!pr.ok) return 0;
    QOpenGLFunctions *f = ctx->functions();
    const QTransform &m = *t;
    // QTransform: x' = m11 x + m21 y + dx; w = m13 x + m23 y + m33 — в mat3 по столбцам (GLSL), с dpr
    const float s = dpr;
    QMatrix3x3 mm;
    mm(0, 0) = float(m.m11()) * s; mm(0, 1) = float(m.m21()) * s; mm(0, 2) = float(m.m31()) * s;
    mm(1, 0) = float(m.m12()) * s; mm(1, 1) = float(m.m22()) * s; mm(1, 2) = float(m.m32()) * s;
    mm(2, 0) = float(m.m13());     mm(2, 1) = float(m.m23());     mm(2, 2) = float(m.m33());
    pr.prog->bind();
    pr.prog->setUniformValue("uT", mm);
    pr.prog->setUniformValue("uFb", QVector2D(fbw, fbh));
    pr.prog->setUniformValue("uBox", QVector4D(-cw / 2 - margin, -ch / 2 - margin, cw + 2 * margin, ch + 2 * margin));
    pr.prog->setUniformValue("uHalf", QVector2D(cw / 2, ch / 2));
    pr.prog->setUniformValue("uCol", QVector3D(float(color->redF()), float(color->greenF()), float(color->blueF())));
    pr.prog->setUniformValue("uTime", time);
    pr.prog->setUniformValue("uReach", 22.0f + 3.5f * float(tier));
    pr.prog->setUniformValue("uAlpha", alpha);
    pr.prog->setUniformValue("uOct", quality <= 0 ? 2 : quality == 1 ? 3 : 4);
    f->glEnable(GL_BLEND);
    f->glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
    f->glDisable(GL_DEPTH_TEST);
    f->glDisable(GL_SCISSOR_TEST);
    pr.vbo->bind();
    pr.prog->enableAttributeArray(0);
    pr.prog->setAttributeBuffer(0, GL_FLOAT, 0, 2);
    f->glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    pr.prog->disableAttributeArray(0);
    pr.vbo->release();
    pr.prog->release();
    return 1;
}
