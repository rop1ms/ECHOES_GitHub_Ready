// Кадры смены страниц (ui_anim._Transition) на видеокарте.
//
// Два снимка — уходящая и новая страница — загружаются текстурами один раз за переход; дальше каждый
// кадр видеокарта сама сдвигает и смешивает их так же, как _Transition.paintEvent на QPainter:
//   slide — старая на месте, новая проявляется (непрозрачность e) с небольшим сдвигом по x;
//   up    — старая темнеет (до 110/255), новая выезжает снизу шторкой с тенью над краем;
//   down  — открытая шторка уезжает вниз, под ней светлеет прежняя.
// Процессор в кадре не рисует и не закачивает в видеокарту страницу целиком.
#include <QtOpenGL/QOpenGLBuffer>
#include <QtOpenGL/QOpenGLShaderProgram>
#include <QtOpenGL/QOpenGLTexture>
#include <QtOpenGL/QOpenGLPixelTransferOptions>
#include <QtOpenGLWidgets/QOpenGLWidget>
#include <QtGui/QOpenGLFunctions>
#include <QtGui/QImage>
#include <QtGui/QPixmap>
#include <QtGui/QVector2D>
#include <QtGui/QVector4D>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

const char *TVERT = R"(
attribute vec2 pos;
void main() { gl_Position = vec4(pos, 0.0, 1.0); }
)";

const char *TFRAG = R"(
#ifdef GL_ES
precision mediump float;
#endif
uniform sampler2D uOld;
uniform sampler2D uNew;
uniform vec2 uSize;            // пиксели устройства
uniform vec4 uP;               // kind (0 slide, 1 up, 2 down, 3 только старая, 4 старая тает над живой), e, dx, dy
uniform float uDpr;

vec4 tex(sampler2D s, vec2 px) {          // px — пиксели устройства сверху-слева
    vec2 uv = px / uSize;
    if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) return vec4(0.0);
    return texture2D(s, uv);
}

void main() {
    vec2 px = vec2(gl_FragCoord.x, uSize.y - gl_FragCoord.y);
    float kind = uP.x, e = uP.y;
    vec3 col;
    if (kind > 3.5) {                                          // новая страница уже живая под слоем:
        gl_FragColor = tex(uOld, px - vec2(uP.z, 0.0)) * (1.0 - e);   // старая тает и уходит в сторону
        return;
    }
    if (kind > 2.5) {
        col = tex(uOld, px).rgb;
    } else if (kind < 0.5) {                                   // slide
        vec4 n = tex(uNew, px - vec2(uP.z, 0.0));
        col = tex(uOld, px).rgb * (1.0 - e * n.a) + n.rgb * e;
    } else {                                                   // up / down: шторка
        bool up = kind < 1.5;
        vec3 base = up ? tex(uOld, px).rgb : tex(uNew, px).rgb;
        float dark = (up ? e : 1.0 - e) * 110.0 / 255.0;
        base *= 1.0 - dark;
        float y = uP.w;                                        // верхний край шторки
        float sh = 18.0 * uDpr;                                // тень над краем (как _Transition._sheet)
        if (y > 0.0 && px.y < y && px.y >= y - sh)
            base *= 1.0 - (90.0 / 255.0) * ((px.y - (y - sh)) / sh);
        if (px.y >= y) {
            vec4 s = up ? tex(uNew, px - vec2(0.0, y)) : tex(uOld, px - vec2(0.0, y));
            base = base * (1.0 - s.a) + s.rgb;
        }
        col = base;
    }
    gl_FragColor = vec4(col, 1.0);
}
)";

class TransitionGL final : public QOpenGLWidget, protected QOpenGLFunctions {
public:
    TransitionGL() {
        setAttribute(Qt::WA_TransparentForMouseEvents);
        setAttribute(Qt::WA_AlwaysStackOnTop);              // полупрозрачный слой над живой страницей
        setUpdateBehavior(QOpenGLWidget::NoPartialUpdate);
    }
    ~TransitionGL() override {
        makeCurrent();
        vbo_.destroy();
        delete prog_;
        delete told_;
        delete tnew_;
        doneCurrent();
    }
    QImage old_img_, new_img_;
    bool old_dirty_ = false, new_dirty_ = false;
    float kind_ = 3, e_ = 0, dx_ = 0, dy_ = 0;
    bool ok_ = true;

protected:
    void initializeGL() override {
        initializeOpenGLFunctions();
        prog_ = new QOpenGLShaderProgram;
        ok_ = prog_->addShaderFromSourceCode(QOpenGLShader::Vertex, TVERT) &&
              prog_->addShaderFromSourceCode(QOpenGLShader::Fragment, TFRAG);
        prog_->bindAttributeLocation("pos", 0);
        ok_ = ok_ && prog_->link();
        const float quad[] = {-1, -1, 1, -1, -1, 1, 1, 1};
        vbo_.create();
        vbo_.bind();
        vbo_.allocate(quad, sizeof quad);
        vbo_.release();
    }
    static QOpenGLTexture *upload(QOpenGLTexture *t, const QImage &src) {
        delete t;
        if (src.isNull()) return nullptr;
        // RGB32 / ARGB32 (премультипл.) в памяти — это BGRA: грузим как есть, без перекодирования снимка
        QImage img = (src.format() == QImage::Format_RGB32 || src.format() == QImage::Format_ARGB32_Premultiplied)
                         ? src : src.convertToFormat(QImage::Format_ARGB32_Premultiplied);
        auto *tex = new QOpenGLTexture(QOpenGLTexture::Target2D);
        tex->setFormat(QOpenGLTexture::RGBA8_UNorm);
        tex->setSize(img.width(), img.height());
        tex->setMinMagFilters(QOpenGLTexture::Linear, QOpenGLTexture::Linear);
        tex->setWrapMode(QOpenGLTexture::ClampToEdge);
        tex->allocateStorage();
        QOpenGLPixelTransferOptions opt;
        opt.setRowLength(img.bytesPerLine() / 4);
        tex->setData(QOpenGLTexture::BGRA, QOpenGLTexture::UInt8, img.constBits(), &opt);
        return tex;
    }
    void paintGL() override {
        const qreal dpr = devicePixelRatioF();
        const int W = int(width() * dpr), H = int(height() * dpr);
        glViewport(0, 0, W, H);
        if (kind_ > 3.5f) glClearColor(0, 0, 0, 0);            // режим «над живой страницей» — прозрачный
        else glClearColor(0.043f, 0.051f, 0.063f, 1);          // #0b0d10 — фон снимков _grab_area
        glClear(GL_COLOR_BUFFER_BIT);
        if (old_dirty_) { told_ = upload(told_, old_img_); old_dirty_ = false; old_img_ = QImage(); }
        if (new_dirty_) { tnew_ = upload(tnew_, new_img_); new_dirty_ = false; new_img_ = QImage(); }
        if (!ok_ || !told_) return;
        prog_->bind();
        told_->bind(0);
        (tnew_ ? tnew_ : told_)->bind(1);
        prog_->setUniformValue("uOld", 0);
        prog_->setUniformValue("uNew", 1);
        prog_->setUniformValue("uSize", QVector2D(W, H));
        prog_->setUniformValue("uDpr", float(dpr));
        float kind = (tnew_ || kind_ > 3.5f) ? kind_ : 3.f;
        prog_->setUniformValue("uP", QVector4D(kind, e_, dx_ * dpr, dy_ * dpr));
        vbo_.bind();
        prog_->enableAttributeArray(0);
        prog_->setAttributeBuffer(0, GL_FLOAT, 0, 2);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        prog_->disableAttributeArray(0);
        vbo_.release();
        prog_->release();
    }

private:
    QOpenGLShaderProgram *prog_ = nullptr;
    QOpenGLBuffer vbo_{QOpenGLBuffer::VertexBuffer};
    QOpenGLTexture *told_ = nullptr, *tnew_ = nullptr;
};

TransitionGL *T(void *w) { return static_cast<TransitionGL *>(static_cast<QWidget *>(w)); }

}  // namespace

EUI_API void *eui_trans_new() { return static_cast<QWidget *>(new TransitionGL); }
EUI_API int eui_trans_ok(void *w) { return T(w)->isValid() && T(w)->ok_ ? 1 : 0; }

// снимок уходящей страницы (nullptr — убрать); новый снимок при этом сбрасывается
EUI_API void eui_trans_old(void *w, const QPixmap *pm) {
    TransitionGL *t = T(w);
    t->old_img_ = pm ? pm->toImage() : QImage();
    t->old_dirty_ = true;
    t->new_img_ = QImage();
    t->new_dirty_ = true;
    t->update();
}

// снимок новой страницы (готов после раскладки)
EUI_API void eui_trans_new_image(void *w, const QPixmap *pm) {
    TransitionGL *t = T(w);
    t->new_img_ = pm ? pm->toImage() : QImage();
    t->new_dirty_ = true;
    t->update();
}

// kind: 0 slide, 1 up, 2 down; e — сглаженный прогресс; dx, dy — сдвиг новой/шторки (логические пиксели)
EUI_API void eui_trans_frame(void *w, int kind, float e, float dx, float dy) {
    TransitionGL *t = T(w);
    t->kind_ = float(kind);
    t->e_ = e;
    t->dx_ = dx;
    t->dy_ = dy;
    t->update();
}
