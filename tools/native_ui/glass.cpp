// Фон окна Echoes Music и снимки для переходов страниц.
//
// eui_liquid_surface — порт echo_glass._liquid_surface строка в строку. Зерно стекла (до 16000 точек)
// раньше рисовалось по точке из Python: ~120 мс на окно 1920×1040 при каждом новом размере. Случайные
// координаты — тот же генератор, что random.Random(73) в Python (MT19937, init_by_array, random()),
// поэтому точки ложатся туда же.
//
// eui_grab_from_window — снимок области виджета из буфера окна (то, что уже нарисовано на экране),
// без повторной отрисовки всей страницы (ui_anim._grab_area для уходящей страницы).
#include <QtWidgets/QWidget>
#include <QtGui/QBackingStore>
#include <QtGui/QLinearGradient>
#include <QtGui/QPainter>
#include <QtGui/QPainterPath>
#include <QtGui/QPixmap>
#include <QtGui/QRadialGradient>
#include <QtGui/QWindow>
#include <QtCore/QCoreApplication>
#include <QtCore/QHash>
#include <QtCore/QMutex>
#include <QtCore/QThreadPool>
#include <algorithm>
#include <cstdint>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

// CPython Modules/_randommodule.c: MT19937 + init_by_array + random() (53 бита)
class PyRandom {
    uint32_t mt[624];
    int mti = 625;

    void init_genrand(uint32_t s) {
        mt[0] = s;
        for (mti = 1; mti < 624; mti++)
            mt[mti] = 1812433253U * (mt[mti - 1] ^ (mt[mti - 1] >> 30)) + uint32_t(mti);
    }
    uint32_t genrand() {
        static const uint32_t mag01[2] = {0x0U, 0x9908b0dfU};
        uint32_t y;
        if (mti >= 624) {
            int kk;
            for (kk = 0; kk < 624 - 397; kk++) {
                y = (mt[kk] & 0x80000000U) | (mt[kk + 1] & 0x7fffffffU);
                mt[kk] = mt[kk + 397] ^ (y >> 1) ^ mag01[y & 0x1U];
            }
            for (; kk < 623; kk++) {
                y = (mt[kk] & 0x80000000U) | (mt[kk + 1] & 0x7fffffffU);
                mt[kk] = mt[kk + (397 - 624)] ^ (y >> 1) ^ mag01[y & 0x1U];
            }
            y = (mt[623] & 0x80000000U) | (mt[0] & 0x7fffffffU);
            mt[623] = mt[396] ^ (y >> 1) ^ mag01[y & 0x1U];
            mti = 0;
        }
        y = mt[mti++];
        y ^= (y >> 11);
        y ^= (y << 7) & 0x9d2c5680U;
        y ^= (y << 15) & 0xefc60000U;
        y ^= (y >> 18);
        return y;
    }

public:
    // random.Random(n) для небольшого неотрицательного целого: ключ — одно 32-битное слово
    explicit PyRandom(uint32_t seed) {
        const uint32_t key[1] = {seed};
        const size_t key_length = 1;
        init_genrand(19650218U);
        size_t i = 1, j = 0;
        size_t k = 624 > key_length ? 624 : key_length;
        for (; k; k--) {
            mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525U)) + key[j] + uint32_t(j);
            i++;
            j++;
            if (i >= 624) { mt[0] = mt[623]; i = 1; }
            if (j >= key_length) j = 0;
        }
        for (k = 623; k; k--) {
            mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941U)) - uint32_t(i);
            i++;
            if (i >= 624) { mt[0] = mt[623]; i = 1; }
        }
        mt[0] = 0x80000000U;
    }
    double random() {
        uint32_t a = genrand() >> 5, b = genrand() >> 6;
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
    }
};

}  // namespace

namespace {

// Рисуем в QImage (не QPixmap): так можно и в фоновом потоке, а результат тот же — растровый QPixmap
// в Windows и есть ARGB32_Premultiplied-картинка.
QImage render_surface(int w, int h, double radius) {
    w = std::max(1, w);
    h = std::max(1, h);
    QImage pm(w, h, QImage::Format_ARGB32_Premultiplied);
    pm.fill(Qt::transparent);
    QPainter p(&pm);
    p.setRenderHint(QPainter::Antialiasing);
    QRectF rect(.5, .5, w - 1, h - 1);
    QPainterPath shape;
    shape.addRoundedRect(rect, radius, radius);
    p.setClipPath(shape);
    QLinearGradient base(0, 0, w * .7, h);
    base.setColorAt(0, QColor(QStringLiteral("#0b0c0e")));
    base.setColorAt(.45, QColor(QStringLiteral("#040506")));
    base.setColorAt(1, QColor(QStringLiteral("#08090b")));
    p.fillPath(shape, base);
    const struct { double cx, cy, r; int alpha; } glows[2] = {{.08, -.38, .65, 20}, {1.15, .9, .48, 11}};
    for (const auto &g0 : glows) {
        QRadialGradient glow(QPointF(w * g0.cx, h * g0.cy), std::max(w, h) * g0.r);
        glow.setColorAt(0, QColor(230, 237, 246, g0.alpha));
        glow.setColorAt(1, QColor(230, 237, 246, 0));
        p.fillPath(shape, glow);
    }
    QLinearGradient sheen(0, 0, w, h * .65);
    const struct { double pos; int alpha; } sheen_stops[9] = {
        {0, 0}, {.055, 1}, {.095, 14}, {.115, 3}, {.16, 0}, {.76, 0}, {.82, 5}, {.85, 0}, {1, 0}};
    for (const auto &s : sheen_stops) sheen.setColorAt(s.pos, QColor(238, 244, 255, s.alpha));
    p.fillPath(shape, sheen);
    PyRandom rng(73);
    p.setPen(QPen(QColor(231, 238, 250, 6), .65));
    const long long n = std::min<long long>(16000, (long long)w * h / 65);
    for (long long i = 0; i < n; ++i) {
        double x = rng.random() * w;                        // порядок вызовов — как в Python: x, затем y
        double y = rng.random() * h;
        p.drawPoint(QPointF(x, y));
    }
    for (int bottom = 0; bottom < 2; ++bottom) {
        double y = bottom ? h - 9 : 9;
        int direction = bottom ? -1 : 1;
        QPainterPath curve(QPointF(-w * .03, y + direction * 12));
        curve.cubicTo(w * .18, y - direction * 15, w * .5, y + direction * 14, w * 1.04, y);
        QLinearGradient light(0, 0, w, 0);
        const struct { double pos; int alpha; } ls[6] = {{0, 0}, {.12, 26}, {.3, 5}, {.66, 2}, {.93, 20}, {1, 0}};
        for (const auto &s : ls) light.setColorAt(s.pos, QColor(229, 240, 255, s.alpha));
        p.setPen(QPen(QBrush(light), 1.1));
        p.drawPath(curve);
    }
    QLinearGradient rim(0, 0, w * .35, h);
    const struct { double pos; int alpha; } rs[6] = {{0, 95}, {.10, 24}, {.3, 9}, {.7, 6}, {.94, 18}, {1, 48}};
    for (const auto &s : rs) rim.setColorAt(s.pos, QColor(236, 243, 255, s.alpha));
    p.setBrush(Qt::NoBrush);
    p.setPen(QPen(QBrush(rim), 1));
    p.drawPath(shape);
    QPainterPath inner;
    double ir = std::max(0.0, radius - 2);
    inner.addRoundedRect(rect.adjusted(2, 2, -2, -2), ir, ir);
    p.setPen(QPen(QColor(0, 0, 0, 190), 1));
    p.drawPath(inner);
    p.end();
    return pm;
}

typedef void (*eui_ticket_cb)(int ticket, int ok);
eui_ticket_cb g_glass_ready = nullptr;
QMutex g_glass_mutex;
QHash<int, QImage> g_glass_done;

}  // namespace

EUI_API void eui_liquid_surface(int w, int h, double radius, QPixmap *out) {
    *out = QPixmap::fromImage(render_surface(w, h, radius));
}

// То же в фоновом потоке: готово — g_glass_ready(ticket) в потоке интерфейса, картинку забрать
// eui_liquid_surface_take. Окно не ждёт стекло при отпускании края (раньше — до ~150 мс).
EUI_API void eui_liquid_surface_callback(eui_ticket_cb cb) { g_glass_ready = cb; }

EUI_API void eui_liquid_surface_async(int ticket, int w, int h, double radius) {
    QThreadPool::globalInstance()->start([ticket, w, h, radius] {
        QImage img = render_surface(w, h, radius);
        {
            QMutexLocker lock(&g_glass_mutex);
            g_glass_done.insert(ticket, img);
        }
        QMetaObject::invokeMethod(QCoreApplication::instance(), [ticket] {
            if (g_glass_ready) g_glass_ready(ticket, 1);
        }, Qt::QueuedConnection);
    });
}

EUI_API int eui_liquid_surface_take(int ticket, QPixmap *out) {
    QImage img;
    {
        QMutexLocker lock(&g_glass_mutex);
        img = g_glass_done.take(ticket);
    }
    if (img.isNull()) return 0;
    *out = QPixmap::fromImage(img);
    return 1;
}

// Область виджета так, как она сейчас нарисована в окне (логические пиксели, как ui_anim._grab_area
// при масштабе 1): поверх того же фона #0b0d10. 0 — снять нельзя (окно ещё не рисовалось, масштаб
// экрана не 100 %, буфер не картинка): тогда Python рендерит страницу по-старому.
EUI_API int eui_grab_from_window(QWidget *w, QPixmap *out) {
    if (!w || !out || !w->isVisible()) return 0;
    QWidget *top = w->window();
    QBackingStore *bs = top ? top->backingStore() : nullptr;
    QPaintDevice *dev = bs ? bs->paintDevice() : nullptr;
    if (!dev || dev->devType() != QInternal::Image) return 0;
    if (!qFuzzyCompare(top->devicePixelRatioF(), 1.0)) return 0;
    const QImage &img = *static_cast<QImage *>(dev);
    QRect r(w->mapTo(top, QPoint(0, 0)), w->size());
    if (img.isNull() || !QRect(QPoint(0, 0), img.size()).contains(r)) return 0;
    QPixmap pm(r.size());
    pm.fill(QColor(QStringLiteral("#0b0d10")));
    QPainter p(&pm);
    p.drawImage(QPoint(0, 0), img, r);
    p.end();
    *out = pm;
    return 1;
}
