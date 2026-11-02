// Диагностика: сколько стоит отрисовка каждого виджета (вместе с C++-частью Qt, которую не видно
// из Python). Фильтр событий приложения ставит отметку времени на каждом Paint: время до следующего
// Paint (или до конца кадра — UpdateRequest/любого другого события) записывается предыдущему виджету.
// Только для замеров (qa, профилировщики), в обычной работе не включается.
#include <QtWidgets/QApplication>
#include <QtWidgets/QWidget>
#include <QtCore/QElapsedTimer>
#include <QtCore/QHash>
#include <QtCore/QPointer>
#include <cstdio>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

struct Stat { qint64 ns = 0; int n = 0; qint64 max = 0; };

class PaintProbe final : public QObject {
public:
    QHash<QByteArray, Stat> stats;
    QElapsedTimer clock;
    QByteArray cur;
    qint64 t0 = 0;
    bool open = false;

    PaintProbe() { clock.start(); }
    void close_slice() {
        if (!open) return;
        qint64 d = clock.nsecsElapsed() - t0;
        Stat &s = stats[cur];
        s.ns += d;
        s.n += 1;
        if (d > s.max) s.max = d;
        open = false;
    }

protected:
    bool eventFilter(QObject *o, QEvent *e) override {
        if (e->type() == QEvent::Paint && o->isWidgetType()) {
            close_slice();
            QWidget *w = static_cast<QWidget *>(o);
            cur = QByteArray(w->metaObject()->className());
            if (!w->objectName().isEmpty()) cur += '#' + w->objectName().toUtf8();
            if (w->graphicsEffect()) cur += " [effect]";
            t0 = clock.nsecsElapsed();
            open = true;
        } else if (open && e->type() != QEvent::Paint) {
            close_slice();
        }
        return false;
    }
};

PaintProbe *g_probe = nullptr;

}  // namespace

EUI_API void eui_paint_probe(int on) {
    if (on && !g_probe) {
        g_probe = new PaintProbe;
        qApp->installEventFilter(g_probe);
    } else if (!on && g_probe) {
        qApp->removeEventFilter(g_probe);
        delete g_probe;
        g_probe = nullptr;
    }
}

// Отчёт: «класс#имя\tвсего_мс\tраз\tмакс_мс» построчно, по убыванию времени. Строка живёт до следующего вызова.
EUI_API const char *eui_paint_probe_report(int reset) {
    static QByteArray out;
    out.clear();
    if (!g_probe) return out.constData();
    g_probe->close_slice();
    QList<QPair<qint64, QByteArray>> rows;
    for (auto it = g_probe->stats.cbegin(); it != g_probe->stats.cend(); ++it) rows.append({it->ns, it.key()});
    std::sort(rows.begin(), rows.end(), [](auto &a, auto &b) { return a.first > b.first; });
    for (auto &r : rows) {
        const Stat &s = g_probe->stats[r.second];
        char buf[128];
        std::snprintf(buf, sizeof buf, "\t%.1f\t%d\t%.1f\n", s.ns / 1e6, s.n, s.max / 1e6);
        out += r.second + buf;
    }
    if (reset) g_probe->stats.clear();
    return out.constData();
}
