// Лента карточек Echoes Music (замена HRow + десятков виджетов Card из yamusic_theme.py).
//
// Один виджет на ряд: карточки — данные, а не QWidget. Рисование — порт Card._paint_card
// строка в строку (те же шрифты, отступы, градиенты, кэш картинки под экран), раскладка — как у
// QHBoxLayout внутри HRow (поля 0,0,0,6, шаг 16, карточка по центру по вертикали), каскад
// появления — как ya_decor._Cascade. Обложки, цвета палитры и иконку готовит Python (их кэши
// остаются там), сюда приходят готовые QPixmap/QIcon/цвета.
#include <QtWidgets/QScrollArea>
#include <QtWidgets/QScrollBar>
#include <QtWidgets/QWidget>
#include <QtGui/QFontMetrics>
#include <QtGui/QCursor>
#include <QtGui/QIcon>
#include <QtGui/QMouseEvent>
#include <QtGui/QPainter>
#include <QtGui/QPainterPath>
#include <QtGui/QPixmap>
#include <QtGui/QRadialGradient>
#include <QtCore/QElapsedTimer>
#include <QtCore/QStringList>
#include <QtCore/QVector>
#include <algorithm>
#include <cmath>

#define EUI_API extern "C" __declspec(dllexport)

typedef void (*eui_strip_cb)(int strip_id, int index);

namespace {

eui_strip_cb g_click = nullptr;     // щелчок по карточке
eui_strip_cb g_hover = nullptr;     // навели — Python присылает цвета наведения (eui_strip_set_hover)
eui_strip_cb g_gone = nullptr;      // виджет удалён (index = -1)
eui_strip_cb g_need = nullptr;      // карточку сейчас нарисуют — Python присылает обложку (как Card: только видимым)

double monotonic() {
    static QElapsedTimer t;
    if (!t.isValid()) t.start();
    return t.nsecsElapsed() / 1e9;
}

// ya_decor._Cascade: пачка карточек, показанных подряд, проявляется по очереди (шаг 18 мс).
struct Burst {
    double t = -1e9;
    int n = 0;
    double next_delay() {
        double now = monotonic();
        if (now - t > 0.12) n = 0;
        t = now;
        double d = std::min(0.3, n * 0.018);
        ++n;
        return d;
    }
} g_burst;

struct Card {
    QString title, subtitle;
    int size = 150;
    double rad = 10;
    bool circle = false;
    bool mix = false;
    QColor c1, c2;
    QPixmap cover;
    QVector<QPixmap> mix_covers;
    bool mix_covers_on = false;
    bool need = true;                                       // обложку ещё не спрашивали у Python
    // наведение
    bool glow = false;
    QColor glow_color, ring_color, accent;
    QIcon play;
    // состояние
    double appear = 1.0, appear_t0 = 0.0;
    bool cascade_pending = false;
    QPixmap buf, hover_buf;
    qreal buf_dpr = 0, hover_dpr = 0;
    int x = 0, y = 0;

    int width() const { return size; }
    int height() const { return size + (subtitle.isEmpty() ? 30 : 46); }
};

class Canvas;

class CardStrip final : public QScrollArea {
public:
    CardStrip(int id, bool grid = false);
    ~CardStrip() override { if (g_gone) g_gone(id_, -1); }

    int id_;
    QVector<Card> cards;
    QStringList families;
    QColor text = QColor(255, 255, 255), muted = QColor(138, 138, 138);
    bool cascade = false;
    bool layout_dirty = false;
    bool in_need = false;                                   // внутри g_need: присланное не перерисовывает
    // Сетка (CardGrid альбомов): вертикальная прокрутка, колонки по ширине, поля 0,6,0,6, шаг 22×20.
    bool grid = false;
    int cols = 0;
    Canvas *canvas;

    void grid_columns();

    void relayout();
    void ensure_layout() { if (layout_dirty) relayout(); }
    QFont font(int px, QFont::Weight w) const {
        QFont f;
        f.setFamilies(families);
        f.setPixelSize(px);
        f.setWeight(w);
        return f;
    }
    void render(Card &c, bool hover, QPixmap &out, qreal dpr) const;
    void resolve(int i) {
        Card &c = cards[i];
        if (!c.need || !g_need) return;
        c.need = false;
        in_need = true;
        g_need(id_, i);
        in_need = false;
    }

protected:
    void wheelEvent(QWheelEvent *e) override {
        if (grid) QScrollArea::wheelEvent(e);               // сетка листается сама
        else e->ignore();                                   // колесо — странице (как HRow)
    }
    void resizeEvent(QResizeEvent *e) override {
        QScrollArea::resizeEvent(e);
        if (grid) grid_columns();                           // CardGrid.resizeEvent → _relayout
    }
};

class Canvas final : public QWidget {
public:
    explicit Canvas(CardStrip *s) : strip(s) {
        setObjectName(QStringLiteral("YaPageInner"));
        setMouseTracking(true);
    }
    CardStrip *strip;
    int hovered = -1, pressed = -1;
    bool shown = false;
    int timer = 0;

    int at(const QPoint &p) const {
        strip->ensure_layout();
        for (int i = 0; i < strip->cards.size(); ++i) {
            const Card &c = strip->cards[i];
            if (QRect(c.x, c.y, c.width(), c.height()).contains(p)) return i;
        }
        return -1;
    }
    void set_hover(int i) {
        if (i == hovered) return;
        int old = hovered;
        hovered = i;
        if (i >= 0 && g_hover) g_hover(strip->id_, i);
        setCursor(i >= 0 ? Qt::PointingHandCursor : Qt::ArrowCursor);
        update_card(old);
        update_card(i);
    }
    // Qt шлёт Enter/Leave виджетам, которые появились или сдвинулись под неподвижным курсором (Card):
    // карточки здесь не виджеты — после раскладки наведение пересчитывается по курсору.
    void sync_hover() {
        if (!isVisible() || !underMouse()) return;
        set_hover(at(mapFromGlobal(QCursor::pos())));
    }
    void update_card(int i) {
        if (i < 0 || i >= strip->cards.size()) return;
        strip->ensure_layout();
        const Card &c = strip->cards[i];
        update(QRect(c.x, c.y, c.width(), c.height() + 13));
    }
    void start_cascade() {
        if (!strip->cascade) return;
        for (Card &c : strip->cards) {
            if (!c.cascade_pending) continue;
            c.cascade_pending = false;
            c.appear = 0.0;
            c.appear_t0 = monotonic() + g_burst.next_delay();
        }
        if (!timer) timer = startTimer(16, Qt::PreciseTimer);
    }

protected:
    QSize minimumSizeHint() const override {
        if (strip->grid) {                                  // QGridLayout: колонки × карточки, поля 0,6,0,6
            if (strip->cards.isEmpty() || strip->cols <= 0) return QSize(0, 12);
            int cw = strip->cards[0].width(), n = strip->cards.size(), c = strip->cols;
            int rows = (n + c - 1) / c, h = 12 + 20 * (rows - 1);
            for (int r = 0; r < rows; ++r) {
                int rh = 0;
                for (int k = r * c; k < std::min(n, (r + 1) * c); ++k) rh = std::max(rh, strip->cards[k].height());
                h += rh;
            }
            return QSize(c * cw + 22 * (c - 1), h);
        }
        int w = 0, h = 0;
        for (const Card &c : strip->cards) { w += c.width(); h = std::max(h, c.height()); }
        if (!strip->cards.isEmpty()) w += 16 * (strip->cards.size() - 1);
        return QSize(w, h + 6);
    }
    QSize sizeHint() const override { return minimumSizeHint(); }
    void resizeEvent(QResizeEvent *) override { strip->relayout(); }
    void showEvent(QShowEvent *) override {
        if (!shown) { shown = true; start_cascade(); }
    }
    // Card получает enterEvent и без движения мыши (лента появилась под курсором)
    void enterEvent(QEnterEvent *e) override { set_hover(at(e->position().toPoint())); }
    void leaveEvent(QEvent *) override { set_hover(-1); }
    void mouseMoveEvent(QMouseEvent *e) override { set_hover(at(e->position().toPoint())); }
    void mousePressEvent(QMouseEvent *e) override {
        // как QWidget у Card: нажатие не поглощаем (уходит родителям), отпускание всё равно придёт сюда
        pressed = e->button() == Qt::LeftButton ? at(e->position().toPoint()) : -1;
        e->ignore();
    }
    void mouseReleaseEvent(QMouseEvent *e) override {
        int i = pressed;
        pressed = -1;
        if (i >= 0 && e->button() == Qt::LeftButton && at(e->position().toPoint()) == i && g_click)
            g_click(strip->id_, i);
    }
    void timerEvent(QTimerEvent *) override {
        double now = monotonic();
        bool live = false;
        for (int i = 0; i < strip->cards.size(); ++i) {
            Card &c = strip->cards[i];
            if (c.appear >= 1.0) continue;
            double a = std::max(0.0, std::min(1.0, (now - c.appear_t0) / 0.22));
            a = 1 - std::pow(1 - a, 3);
            if (a != c.appear) { c.appear = a; update_card(i); }
            if (a < 1.0) live = true;
        }
        if (!live) { killTimer(timer); timer = 0; }
    }
    void paintEvent(QPaintEvent *e) override {
        strip->ensure_layout();
        QPainter p(this);
        qreal dpr = devicePixelRatioF();
        for (int i = 0; i < strip->cards.size(); ++i) {
            Card &c = strip->cards[i];
            QRect r(c.x, c.y, c.width(), c.height() + 13);
            if (!e->rect().intersects(r)) continue;
            bool hover = i == hovered;
            QPixmap &buf = hover ? c.hover_buf : c.buf;
            qreal &bd = hover ? c.hover_dpr : c.buf_dpr;
            strip->resolve(i);
            if (buf.isNull() || bd != dpr) { strip->render(c, hover, buf, dpr); bd = dpr; }
            p.save();
            p.translate(c.x, c.y);
            if (c.appear < 1.0) {
                p.setOpacity(c.appear);
                p.translate(0, (1.0 - c.appear) * 12);
            }
            p.drawPixmap(0, 0, buf);
            p.restore();
        }
    }
};

CardStrip::CardStrip(int id, bool grid_) : id_(id), grid(grid_) {
    setWidgetResizable(true);
    setVerticalScrollBarPolicy(grid ? Qt::ScrollBarAsNeeded : Qt::ScrollBarAlwaysOff);
    setHorizontalScrollBarPolicy(grid ? Qt::ScrollBarAlwaysOff : Qt::ScrollBarAsNeeded);
    setFrameShape(QFrame::NoFrame);
    canvas = new Canvas(this);
    if (grid) canvas->setObjectName(QStringLiteral("YaPage"));  // как inner у CardGrid
    setWidget(canvas);
    // переводить в ленте нечего (подписи приходят уже переведёнными): метка i18n._SEEN, чтобы
    // i18n.sweep не обходил её детей из Python при каждом addWidget/показе
    setProperty("_i18n_seen", true);
    for (QWidget *w : findChildren<QWidget *>()) w->setProperty("_i18n_seen", true);
}

// QHBoxLayout(поля 0,0,0,6; шаг 16) + растяжка в конце: x — подряд, y — по центру (как QWidgetItem).
// CardGrid._relayout: колонок столько, сколько влезает в ширину области (с шагом 22)
void CardStrip::grid_columns() {
    if (cards.isEmpty()) return;
    int cw = cards[0].width() + 22;
    int c = std::max(1, (viewport()->width() + 22) / std::max(1, cw));
    if (c == cols) return;
    cols = c;
    canvas->updateGeometry();
    layout_dirty = true;
    canvas->update();
}

void CardStrip::relayout() {
    layout_dirty = false;
    if (grid) {                                             // ячейки QGridLayout: строка — по самой высокой
        int n = cards.size(), c = std::max(1, cols), y = 6;
        for (int r = 0; r * c < n; ++r) {
            int rh = 0;
            for (int k = r * c; k < std::min(n, (r + 1) * c); ++k) {
                Card &cd = cards[k];
                cd.x = (k % c) * (cards[0].width() + 22);
                cd.y = y;
                rh = std::max(rh, cd.height());
            }
            y += rh + 20;
        }
        canvas->update();
        canvas->sync_hover();
        return;
    }
    int x = 0, avail = canvas->height() - 6;
    for (Card &c : cards) {
        c.x = x;
        c.y = (avail - c.height()) / 2;
        x += c.width() + 16;
    }
    canvas->update();
}

// Card._paint_card
void CardStrip::render(Card &c, bool hover, QPixmap &out, qreal dpr) const {
    out = QPixmap(std::max(1, int(c.width() * dpr)), std::max(1, int(c.height() * dpr)));
    out.setDevicePixelRatio(dpr);
    out.fill(Qt::transparent);
    QPainter p(&out);
    p.setRenderHint(QPainter::Antialiasing);
    p.setRenderHint(QPainter::SmoothPixmapTransform);
    const int s = c.size;
    QRectF r(0, 0, s, s);
    bool glow = hover && c.glow;
    if (glow) {                                             // ya_decor.card_glow
        QRadialGradient g(QPointF(s / 2.0, s * 0.98), s * 0.55);
        QColor col = c.glow_color;
        col.setAlpha(150);
        g.setColorAt(0, col);
        col.setAlpha(0);
        g.setColorAt(1, col);
        p.setPen(Qt::NoPen);
        p.setBrush(g);
        p.drawEllipse(QRectF(s * 0.05, s * 0.84, s * 0.9, s * 0.26));
    }
    if (c.mix) {
        QPainterPath path;
        qreal mr = std::max(4.0, c.rad + 2);
        path.addRoundedRect(r, mr, mr);
        p.setClipPath(path);
        p.fillRect(r, QColor(20, 20, 20));
        struct Spot { double cx, cy, rad; QColor col; };
        const Spot spots[3] = {{0.3, 0.35, 0.8, c.c1}, {0.8, 0.75, 0.7, c.c2}, {0.75, 0.2, 0.45, c.c1.lighter(130)}};
        for (const Spot &sp : spots) {
            QRadialGradient g(QPointF(sp.cx * s, sp.cy * s), sp.rad * s);
            QColor cc(sp.col);
            cc.setAlpha(230);
            g.setColorAt(0, cc);
            QColor cc2(sp.col);
            cc2.setAlpha(0);
            g.setColorAt(1, cc2);
            p.fillRect(r, g);
        }
        if (c.mix_covers_on) {                              // ya_decor.mix_paint
            int n = c.mix_covers.size();
            for (int i = 0; i < n; ++i) {
                int k = n - 1 - i;
                const QPixmap &pm = c.mix_covers[k];
                qreal sz = s * 0.36;
                p.save();
                p.translate(s * 0.86 - k * s * 0.12, s * 0.30 + k * s * 0.05);
                p.rotate(8 - k * 8);
                QRectF cr(-sz / 2, -sz / 2, sz, sz);
                QPainterPath sh;
                sh.addRoundedRect(cr.adjusted(-1, 2, 1, 4), 7, 7);
                p.fillPath(sh, QColor(0, 0, 0, 90));
                p.drawPixmap(cr, pm, QRectF(pm.rect()));
                p.restore();
            }
        }
        p.setClipping(false);
        p.setPen(QColor(255, 255, 255));
        p.setFont(font(int(s * 0.12), QFont::Black));
        p.drawText(r.adjusted(10, 10, -10, -10), Qt::AlignLeft | Qt::AlignBottom | Qt::TextWordWrap, c.title);
    } else {
        p.drawPixmap(QRectF(0, 0, s, s), c.cover, QRectF(c.cover.rect()));
    }
    if (hover) {
        QPainterPath path;
        if (c.circle) path.addEllipse(r);
        else path.addRoundedRect(r, c.rad, c.rad);
        p.fillPath(path, QColor(0, 0, 0, 70));
        if (glow) {                                         // ya_decor.card_ring
            QColor rc = c.ring_color;
            p.setPen(QPen(QColor(rc.red(), rc.green(), rc.blue(), 200), 1.6));
            p.setBrush(Qt::NoBrush);
            QRectF rr = r.adjusted(0.8, 0.8, -0.8, -0.8);
            if (c.circle) p.drawEllipse(rr);
            else p.drawRoundedRect(rr, c.rad, c.rad);
        }
        QRectF br = !c.circle ? QRectF(s - 52, s - 52, 42, 42) : QRectF(s / 2.0 - 22, s / 2.0 - 22, 44, 44);
        p.setPen(Qt::NoPen);
        p.setBrush(c.accent);
        p.drawEllipse(br);
        c.play.paint(&p, br.adjusted(11, 11, -10, -11).toRect());
    }
    if (!c.mix) {
        p.setPen(text);
        QFont f = font(13, QFont::DemiBold);
        p.setFont(f);
        Qt::Alignment al = c.circle ? Qt::AlignHCenter : Qt::AlignLeft;
        p.drawText(QRectF(0, s + 6, s, 18), al | Qt::AlignVCenter,
                   QFontMetrics(f).elidedText(c.title, Qt::ElideRight, s));
        if (!c.subtitle.isEmpty()) {
            QFont f2 = font(12, QFont::Normal);
            p.setFont(f2);
            p.setPen(muted);
            p.drawText(QRectF(0, s + 25, s, 16), al | Qt::AlignVCenter,
                       QFontMetrics(f2).elidedText(c.subtitle, Qt::ElideRight, s));
        }
    } else {
        p.setPen(text);
        QFont f = font(13, QFont::DemiBold);
        p.setFont(f);
        p.drawText(QRectF(0, s + 6, s, 18), Qt::AlignHCenter | Qt::AlignVCenter,
                   QFontMetrics(f).elidedText(c.subtitle, Qt::ElideRight, s));
    }
}

CardStrip *S(void *w) { return static_cast<CardStrip *>(static_cast<QWidget *>(w)); }
QString str(const wchar_t *s) { return s ? QString::fromWCharArray(s) : QString(); }
void dirty(Card &c) { c.buf = QPixmap(); c.hover_buf = QPixmap(); }

}  // namespace

EUI_API void eui_strip_callbacks(eui_strip_cb click, eui_strip_cb hover, eui_strip_cb gone, eui_strip_cb need) {
    g_click = click;
    g_hover = hover;
    g_gone = gone;
    g_need = need;
}

// Обложка поменялась (догрузилась) — спросить заново, когда карточку будут рисовать.
EUI_API void eui_strip_invalidate(void *w, int i) {
    CardStrip *s = S(w);
    if (i < 0 || i >= s->cards.size()) return;
    s->cards[i].need = true;
    dirty(s->cards[i]);
    s->canvas->update_card(i);
}

EUI_API void *eui_strip_new(int id) { return static_cast<QWidget *>(new CardStrip(id)); }
EUI_API void *eui_grid_new(int id) { return static_cast<QWidget *>(new CardStrip(id, true)); }

// CardGrid.set_cards: убрать все карточки (новые добавит eui_strip_add, затем eui_grid_done)
EUI_API void eui_strip_clear(void *w) {
    CardStrip *s = S(w);
    s->cards.clear();
    s->canvas->hovered = s->canvas->pressed = -1;
    s->cols = 0;
    s->layout_dirty = true;
    s->canvas->updateGeometry();
    s->canvas->update();
}

EUI_API void eui_grid_done(void *w) {
    CardStrip *s = S(w);
    s->cols = 0;
    s->grid_columns();
    s->relayout();
    s->verticalScrollBar()->setValue(0);
}

// text/muted — цвета подписей (TEXT/MUTED темы), families — FAMILIES через '\n'
EUI_API void eui_strip_style(void *w, const QColor *text, const QColor *muted, const wchar_t *families,
                             int cascade) {
    CardStrip *s = S(w);
    s->text = *text;
    s->muted = *muted;
    s->families = str(families).split(QLatin1Char('\n'), Qt::SkipEmptyParts);
    s->cascade = cascade != 0;
}

EUI_API int eui_strip_add(void *w, const wchar_t *title, const wchar_t *subtitle, int size, double rad,
                          int circle, const QColor *c1, const QColor *c2) {
    CardStrip *s = S(w);
    Card c;
    c.title = str(title);
    c.subtitle = str(subtitle);
    c.size = size;
    c.rad = rad;
    c.circle = circle != 0;
    c.mix = c1 && c2;                                       // QColor целиком: HSV-цвета без округления
    if (c.mix) { c.c1 = *c1; c.c2 = *c2; }
    c.cascade_pending = s->cascade;
    s->cards.push_back(c);
    // HRow.add: setFixedHeight(высота карточки + 16) — по последней добавленной
    if (!s->grid && (s->minimumHeight() != c.height() + 16 || s->maximumHeight() != c.height() + 16))
        s->setFixedHeight(c.height() + 16);
    s->canvas->updateGeometry();
    s->layout_dirty = true;                                 // раскладка — один раз, перед показом/рисованием
    s->canvas->update();
    if (s->canvas->shown) s->canvas->start_cascade();
    return s->cards.size() - 1;
}

EUI_API void eui_strip_text(void *w, int i, const wchar_t *title, const wchar_t *subtitle) {
    CardStrip *s = S(w);
    if (i < 0 || i >= s->cards.size()) return;
    Card &c = s->cards[i];
    c.title = str(title);
    c.subtitle = str(subtitle);
    dirty(c);
    s->canvas->update_card(i);
}

EUI_API void eui_strip_cover(void *w, int i, const QPixmap *pm) {
    CardStrip *s = S(w);
    if (i < 0 || i >= s->cards.size()) return;
    Card &c = s->cards[i];
    c.cover = pm ? *pm : QPixmap();
    c.need = false;
    dirty(c);
    if (!s->in_need) s->canvas->update_card(i);
}

EUI_API void eui_strip_mix_covers(void *w, int i, const QPixmap *const *pms, int n, int on) {
    CardStrip *s = S(w);
    if (i < 0 || i >= s->cards.size()) return;
    Card &c = s->cards[i];
    c.mix_covers.clear();
    for (int k = 0; k < n; ++k) c.mix_covers.push_back(pms[k] ? *pms[k] : QPixmap());
    c.mix_covers_on = on != 0 && n > 0;
    c.need = false;
    dirty(c);
    if (!s->in_need) s->canvas->update_card(i);
}

EUI_API void eui_strip_set_hover(void *w, int i, int glow, const QColor *glow_color, const QColor *ring_color,
                                 const QColor *accent, const QIcon *play) {
    CardStrip *s = S(w);
    if (i < 0 || i >= s->cards.size()) return;
    Card &c = s->cards[i];
    c.glow = glow != 0;
    c.glow_color = *glow_color;
    c.ring_color = *ring_color;
    c.accent = *accent;
    c.play = play ? *play : QIcon();
    c.hover_buf = QPixmap();
    s->canvas->update_card(i);
}

EUI_API int eui_strip_count(void *w) { return S(w)->cards.size(); }

// Положение карточки в координатах ленты (для «перелёта» обложки) и её картинка без каскада.
EUI_API void eui_strip_card_pos(void *w, int i, int *x, int *y) {
    CardStrip *s = S(w);
    *x = *y = 0;
    if (i < 0 || i >= s->cards.size()) return;
    s->ensure_layout();
    QPoint p = s->canvas->mapTo(s, QPoint(s->cards[i].x, s->cards[i].y));
    *x = p.x();
    *y = p.y();
}

EUI_API void eui_strip_card_pixmap(void *w, int i, QPixmap *out) {
    CardStrip *s = S(w);
    if (i < 0 || i >= s->cards.size() || !out) return;
    Card &c = s->cards[i];
    bool hover = s->canvas->hovered == i;
    s->resolve(i);
    QPixmap &buf = hover ? c.hover_buf : c.buf;
    qreal dpr = s->canvas->devicePixelRatioF();
    if (buf.isNull()) { s->render(c, hover, buf, dpr); (hover ? c.hover_dpr : c.buf_dpr) = dpr; }
    *out = buf;
}
