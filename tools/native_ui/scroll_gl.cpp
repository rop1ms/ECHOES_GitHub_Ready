// Плавная прокрутка списков и страниц на видеокарте (wheel_guard + gpu_scroll.py).
//
// Пока колесо доводит прокрутку, растровая страница стоит на месте, а поверх её области прокрутки
// лежит этот GL-слой: фон под содержимым (стекло панели) — одной текстурой, содержимое — полосами
// (плитками) высотой tile_h, отрисованными один раз. Каждый кадр видеокарта только сдвигает плитки:
// процессор не перерисовывает страницу и не перекачивает в видеокарту окно целиком. Когда прокрутка
// доехала, страница один раз встаёт в новое место, слой прячется.
#include <QtOpenGL/QOpenGLBuffer>
#include <QtOpenGL/QOpenGLShaderProgram>
#include <QtOpenGL/QOpenGLTexture>
#include <QtOpenGL/QOpenGLPixelTransferOptions>
#include <QtOpenGLWidgets/QOpenGLWidget>
#include <QtGui/QOpenGLFunctions>
#include <QtGui/QImage>
#include <QtGui/QVector4D>
#include <QtCore/QHash>
#include <cmath>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

const char *SVERT = R"(
attribute vec2 pos;
uniform vec4 uDst;             // x, y, w, h — в долях области (0..1, сверху вниз)
varying vec2 uv;
void main() {
    uv = pos;
    vec2 p = uDst.xy + pos * uDst.zw;
    gl_Position = vec4(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
}
)";

const char *SFRAG = R"(
#ifdef GL_ES
precision mediump float;
#endif
uniform sampler2D uTex;
varying vec2 uv;
void main() { gl_FragColor = texture2D(uTex, uv); }
)";

class ScrollGL final : public QOpenGLWidget, protected QOpenGLFunctions {
public:
    ScrollGL() {
        setAttribute(Qt::WA_TransparentForMouseEvents);
        setAttribute(Qt::WA_AlwaysStackOnTop);              // поверх растровой страницы (она стоит под слоем)
        setUpdateBehavior(QOpenGLWidget::NoPartialUpdate);
    }
    ~ScrollGL() override {
        makeCurrent();
        drop_textures();
        vbo_.destroy();
        delete prog_;
        doneCurrent();
    }
    QImage back_img_;
    bool back_dirty_ = false;
    QHash<int, QImage> pending_;
    bool clear_ = false;
    float off_ = 0;                                         // y содержимого у верхнего края области, лог. пиксели
    int tile_h_ = 256;
    bool ok_ = true;

    void drop_textures() {
        for (QOpenGLTexture *t : tiles_) delete t;
        tiles_.clear();
        delete back_;
        back_ = nullptr;
    }
    bool has_tile(int i) const { return tiles_.contains(i); }
    void drop_tile(int i) { delete tiles_.take(i); }

protected:
    void initializeGL() override {
        initializeOpenGLFunctions();
        prog_ = new QOpenGLShaderProgram;
        ok_ = prog_->addShaderFromSourceCode(QOpenGLShader::Vertex, SVERT) &&
              prog_->addShaderFromSourceCode(QOpenGLShader::Fragment, SFRAG);
        prog_->bindAttributeLocation("pos", 0);
        ok_ = ok_ && prog_->link();
        const float quad[] = {0, 0, 1, 0, 0, 1, 1, 1};
        vbo_.create();
        vbo_.bind();
        vbo_.allocate(quad, sizeof quad);
        vbo_.release();
    }
    static QOpenGLTexture *upload(const QImage &src) {
        if (src.isNull()) return nullptr;
        // RGB32 / ARGB32 (премультипл.) в памяти — это BGRA: грузим как есть, без перекодирования
        QImage img = (src.format() == QImage::Format_RGB32 || src.format() == QImage::Format_ARGB32_Premultiplied)
                         ? src : src.convertToFormat(QImage::Format_ARGB32_Premultiplied);
        auto *tex = new QOpenGLTexture(QOpenGLTexture::Target2D);
        tex->setFormat(QOpenGLTexture::RGBA8_UNorm);
        tex->setSize(img.width(), img.height());
        tex->setMinMagFilters(QOpenGLTexture::Nearest, QOpenGLTexture::Nearest);   // пиксель в пиксель — текст не мылится
        tex->setWrapMode(QOpenGLTexture::ClampToEdge);
        tex->allocateStorage();
        QOpenGLPixelTransferOptions opt;
        opt.setRowLength(img.bytesPerLine() / 4);
        tex->setData(QOpenGLTexture::BGRA, QOpenGLTexture::UInt8, img.constBits(), &opt);
        return tex;
    }
    void quad(QOpenGLTexture *t, float x, float y, float w, float h, float W, float H) {
        t->bind(0);
        prog_->setUniformValue("uDst", QVector4D(x / W, y / H, w / W, h / H));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    }
    void paintGL() override {
        const qreal dpr = devicePixelRatioF();
        const float W = float(width() * dpr), H = float(height() * dpr);
        glViewport(0, 0, int(W), int(H));
        glClearColor(0, 0, 0, 0);
        glClear(GL_COLOR_BUFFER_BIT);
        if (clear_) {
            for (QOpenGLTexture *t : tiles_) delete t;
            tiles_.clear();
            clear_ = false;
        }
        if (back_dirty_) {
            delete back_;
            back_ = upload(back_img_);
            back_img_ = QImage();
            back_dirty_ = false;
        }
        for (auto it = pending_.cbegin(); it != pending_.cend(); ++it) {
            delete tiles_.value(it.key(), nullptr);
            tiles_.insert(it.key(), upload(it.value()));
        }
        pending_.clear();
        if (!ok_ || !back_) return;
        prog_->bind();
        prog_->setUniformValue("uTex", 0);
        vbo_.bind();
        prog_->enableAttributeArray(0);
        prog_->setAttributeBuffer(0, GL_FLOAT, 0, 2);
        glDisable(GL_BLEND);
        quad(back_, 0, 0, W, H, W, H);
        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);         // плитки — премультиплицированные, поверх стекла
        const float th = float(tile_h_ * dpr);
        const float off = std::round(off_ * float(dpr));     // целые пиксели устройства: без «мыла» на тексте
        const int first = int(std::floor(off / th)), last = int(std::floor((off + H) / th));
        for (int i = first; i <= last; ++i) {
            QOpenGLTexture *t = tiles_.value(i, nullptr);
            if (t) quad(t, 0, i * th - off, float(t->width()), float(t->height()), W, H);
        }
        glDisable(GL_BLEND);
        prog_->disableAttributeArray(0);
        vbo_.release();
        prog_->release();
    }

private:
    QOpenGLShaderProgram *prog_ = nullptr;
    QOpenGLBuffer vbo_{QOpenGLBuffer::VertexBuffer};
    QOpenGLTexture *back_ = nullptr;
    QHash<int, QOpenGLTexture *> tiles_;
};

ScrollGL *S(void *w) { return static_cast<ScrollGL *>(static_cast<QWidget *>(w)); }

}  // namespace

EUI_API void *eui_scroll_new() { return static_cast<QWidget *>(new ScrollGL); }
EUI_API int eui_scroll_ok(void *w) { return S(w)->isValid() && S(w)->ok_ ? 1 : 0; }

// фон под содержимым (размер области × dpr); вместе с ним сбрасываются все плитки
EUI_API void eui_scroll_backdrop(void *w, const QImage *img) {
    ScrollGL *s = S(w);
    s->back_img_ = img ? *img : QImage();
    s->back_dirty_ = true;
    s->pending_.clear();
    s->clear_ = true;
    s->update();
}

// плитка i — полоса содержимого [i*tile_h, (i+1)*tile_h) в лог. пикселях
EUI_API void eui_scroll_tile(void *w, int i, const QImage *img) {
    ScrollGL *s = S(w);
    if (img) s->pending_.insert(i, *img);
    s->update();
}

// плитка далеко позади — освободить
EUI_API void eui_scroll_drop(void *w, int i) {
    ScrollGL *s = S(w);
    s->pending_.remove(i);
    if (s->isValid() && s->has_tile(i)) {
        s->makeCurrent();
        s->drop_tile(i);
        s->doneCurrent();
    }
}

// прокрутка доехала — текстуры не держим до следующей
EUI_API void eui_scroll_release(void *w) {
    ScrollGL *s = S(w);
    s->back_img_ = QImage();
    s->back_dirty_ = false;
    s->pending_.clear();
    s->clear_ = false;
    if (s->isValid()) {
        s->makeCurrent();
        s->drop_textures();
        s->doneCurrent();
    }
}

EUI_API void eui_scroll_frame(void *w, float off, int tile_h) {
    ScrollGL *s = S(w);
    s->off_ = off;
    s->tile_h_ = tile_h > 0 ? tile_h : 256;
    s->update();
}
