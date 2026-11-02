// Текст экрана «Текст песни» (lyrics_view.LyricsScrollPanel) без повторной раскладки в каждом кадре.
//
// QPainter::drawText каждый раз заново раскладывает и шейпит строку. Панель текста перерисовывается
// с частотой экрана, а «жидкая» строка рисуется по буквам (до 5 вызовов на букву с ореолом) — это
// 100–150 вызовов за кадр. Здесь раскладка и глифы считаются один раз и рисуются готовыми:
//   * eui_draw_wrapped — то же, что drawText(rect, AlignTop|AlignLeft/HCenter|TextWordWrap, text):
//     QTextLayout с теми же переносами, высотами строк и выравниванием, что у qt_format_text;
//   * eui_draw_liquid — порт LyricsScrollPanel._draw_liquid: буквы — готовые QGlyphRun (как
//     drawText(QPointF, ch)), математика волны/заливки/ореола — строка в строку.
// Пиксель в пиксель с исходником (qa/test_native_lyrics.py). Что не покрыто (выравнивание вправо,
// текст не влезает в rect) — рисуется обычным drawText.
#include <QtGui/QFontMetricsF>
#include <QtGui/QGlyphRun>
#include <QtGui/QPainter>
#include <QtGui/QTextLayout>
#include <QtCore/QHash>
#include <QtCore/QList>
#include <algorithm>
#include <cmath>
#include <memory>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

struct Wrapped {
    std::unique_ptr<QTextLayout> layout;
    qreal width = 0, height = 0;
};

struct Glyphs {
    QList<QGlyphRun> runs;
    qreal ascent = 0;
};

struct TextCache {
    QString font_key;
    QHash<QString, std::shared_ptr<Wrapped>> wrapped;
    QHash<QString, Glyphs> glyphs;

    void check_font(const QFont &f) {
        QString k = f.key();
        if (k != font_key) {
            font_key = k;
            wrapped.clear();
            glyphs.clear();
        }
    }
    const Glyphs &glyph(const QString &ch, const QFont &f) {
        auto it = glyphs.find(ch);
        if (it != glyphs.end()) return *it;
        if (glyphs.size() > 4000) glyphs.clear();
        QTextLayout lay(ch, f);
        lay.beginLayout();
        QTextLine line = lay.createLine();
        lay.endLayout();
        Glyphs g;
        g.runs = lay.glyphRuns();
        g.ascent = line.isValid() ? line.ascent() : 0;
        return *glyphs.insert(ch, g);
    }
};

QString qs(const wchar_t *s) { return s ? QString::fromWCharArray(s) : QString(); }

// Раскладка как у qt_format_text для отрисовки: строки шириной rect, высота с leading между строками.
std::shared_ptr<Wrapped> layout_for(TextCache *c, const QFont &f, const QString &text, qreal width, int flags) {
    QString key = QString::number(width, 'g', 17) + QLatin1Char('|') + QString::number(flags) + QLatin1Char('|') + text;
    auto it = c->wrapped.find(key);
    if (it != c->wrapped.end()) return *it;
    if (c->wrapped.size() > 400) c->wrapped.clear();
    auto w = std::make_shared<Wrapped>();
    QString t = text;
    t.replace(QLatin1Char('\n'), QChar::LineSeparator);
    w->layout = std::make_unique<QTextLayout>(t, f);
    QTextOption opt;
    opt.setWrapMode(QTextOption::WordWrap);
    w->layout->setTextOption(opt);
    w->layout->setCacheEnabled(true);
    QFontMetricsF fm(f);
    qreal leading = fm.leading();
    qreal height = -leading, maxw = 0;
    w->layout->beginLayout();
    for (;;) {
        QTextLine l = w->layout->createLine();
        if (!l.isValid()) break;
        l.setLineWidth(width);
        height += leading;
        l.setPosition(QPointF(0., height));
        height += l.height();
        maxw = std::max(maxw, l.naturalTextWidth());
    }
    w->layout->endLayout();
    w->width = maxw;
    w->height = height;
    c->wrapped.insert(key, w);
    return w;
}

}  // namespace

EUI_API void *eui_text_cache_new() { return new TextCache; }
EUI_API void eui_text_cache_free(void *c) { delete static_cast<TextCache *>(c); }

// = p.drawText(QRect(x, y, w, h), flags, text) шрифтом и пером художника
EUI_API void eui_draw_wrapped(void *cache, QPainter *p, int x, int y, int w, int h, int flags, const wchar_t *text) {
    TextCache *c = static_cast<TextCache *>(cache);
    QString t = qs(text);
    QRect r(x, y, w, h);
    const int handled = Qt::AlignLeft | Qt::AlignHCenter | Qt::AlignTop | Qt::TextWordWrap;
    if ((flags & ~handled) || !(flags & Qt::TextWordWrap) || w <= 0) {
        p->drawText(r, flags, t);
        return;
    }
    QFont f = p->font();
    c->check_font(f);
    auto lay = layout_for(c, f, t, r.width(), flags);
    if (lay->height > r.height() || lay->width > r.width()) {   // не влезает — у Qt тут обрезка, рисуем как есть
        p->drawText(r, flags, t);
        return;
    }
    for (int i = 0; i < lay->layout->lineCount(); ++i) {
        QTextLine line = lay->layout->lineAt(i);
        qreal xoff = (flags & Qt::AlignHCenter) ? (r.width() - line.horizontalAdvance()) / 2 : 0;
        line.draw(p, QPointF(r.x() + xoff, r.y()));
    }
}

// LyricsScrollPanel._draw_liquid: буквы chars[k] в (ox + cx, oy + base), время заливки t0..t1
EUI_API void eui_draw_liquid(void *cache, QPainter *p, int ox, int oy, int n, const wchar_t *const *chars,
                             const double *cx_, const double *base_, const double *cw_, const double *t0_,
                             const double *t1_, double now, int px, const QColor *col, const QColor *dim_col,
                             double alpha) {
    TextCache *c = static_cast<TextCache *>(cache);
    QFont f = p->font();
    c->check_font(f);
    QColor lit(*col);
    lit.setAlphaF(std::min(1.0, alpha));
    QColor dim(*dim_col);
    dim.setAlphaF(std::min(1.0, alpha) * 0.32);
    QColor glow(*col);
    for (int k = 0; k < n; ++k) {
        QString ch = qs(chars[k]);
        if (ch == QLatin1String(" ")) continue;
        double cx = cx_[k], base = base_[k], cw = cw_[k], t0 = t0_[k], t1 = t1_[k];
        double fr = now <= t0 ? 0.0 : now >= t1 ? 1.0 : (now - t0) / std::max(1.0, t1 - t0);
        double tc = (t0 + t1) / 2;
        double d = (now - tc) / std::max(170.0, (t1 - t0) * 1.6);
        double bump = (-2.5 < d && d < 2.5) ? std::exp(-d * d) : 0.0;
        double settled = 0.03 * std::min(1.0, fr);
        double sc = 1.0 + 0.085 * bump + settled;
        double lift = -px * (0.15 * bump + 0.02 * std::min(1.0, fr));
        double x = ox + cx;
        double y = oy + base;
        double pivot_x = x + cw / 2;
        const Glyphs &g = c->glyph(ch, f);
        auto draw = [&](double gx, double gy) {
            for (const QGlyphRun &run : g.runs) p->drawGlyphRun(QPointF(gx, gy - g.ascent), run);
        };
        p->save();
        p->translate(pivot_x, y + lift);
        p->scale(sc, sc);
        p->translate(-pivot_x, -y);
        if (0.0 < fr && fr < 1.0) {
            double soft = std::max(2.0, cw * 0.45);
            double cut = x + cw * fr;
            QLinearGradient grad(cut - soft, 0, cut + soft, 0);
            grad.setColorAt(0.0, lit);
            grad.setColorAt(1.0, dim);
            p->setPen(QPen(QBrush(grad), 1));
            draw(x, y);
        } else {
            if (fr >= 1.0 || bump > 0.05) {
                QColor gc(glow);
                gc.setAlphaF(std::min(1.0, alpha) * (0.10 + 0.22 * bump) * (fr >= 1.0 ? 1.0 : 0.0));
                if (gc.alphaF() > 0.01) {
                    p->setPen(gc);
                    double r = std::max(1.0, px * 0.05);
                    const double offs[4][2] = {{r, 0}, {-r, 0}, {0, r}, {0, -r}};
                    for (const auto &o : offs) draw(x + o[0], y + o[1]);
                }
            }
            p->setPen(fr >= 1.0 ? lit : dim);
            draw(x, y);
        }
        p->restore();
    }
}
