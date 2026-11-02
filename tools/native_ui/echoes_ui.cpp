// echoes_ui.dll — виджеты интерфейса ECHOES на C++ (Qt Widgets, тот же Qt, что у PyQt6).
//
// Python создаёт виджет через ctypes (eui_*), оборачивает указатель sip.wrapinstance и дальше
// работает с ним как с обычным QWidget: кладёт в layout, показывает, удаляет. Рисование и
// раскладка — здесь, без Python в кадре. Сборка: tools/native_ui/build.ps1 (MSVC 2022 + Qt 6.11
// той же версии, что PyQt6 — иначе ABI не совпадёт; native_ui.py это проверяет).
#include <QtWidgets/QWidget>
#include <QtGui/QPainter>
#include <QtCore/QtGlobal>
#include <cstring>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

class ProbeWidget final : public QWidget {
public:
    explicit ProbeWidget(QWidget *parent) : QWidget(parent) {}
protected:
    void paintEvent(QPaintEvent *) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing);
        p.fillRect(rect(), QColor(20, 20, 20));
        p.setBrush(QColor(255, 214, 10));
        p.setPen(Qt::NoPen);
        p.drawRoundedRect(QRectF(10, 10, width() - 20, height() - 20), 12, 12);
    }
};

}  // namespace

// Версия Qt, с которой собрана DLL, — Python сверяет её с qVersion() до создания виджетов.
EUI_API const char *eui_qt_version() { return QT_VERSION_STR; }
EUI_API int eui_version() { return 1; }

EUI_API void *eui_probe_new(void *parent) {
    return static_cast<QWidget *>(new ProbeWidget(static_cast<QWidget *>(parent)));
}
