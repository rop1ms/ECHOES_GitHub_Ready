// eui_gate_* — фильтр событий всего приложения, который пускает в Python только нужные типы событий.
//
// В ECHOES около десятка фильтров на QApplication (оформление, перевод, кэш кнопок, колесо, рамка окна…),
// каждый написан на Python. Фильтр приложения получает КАЖДОЕ событие программы — отрисовку, таймеры,
// раскладку, — и каждое пересекало границу C++ → Python ~10 раз, хотя каждому фильтру нужны 2–7 типов.
// Шлюз проверяет тип (и, если задано, точный класс объекта) в C++ и вызывает eventFilter Python-объекта
// только для своих событий; цель — обычный QObject из Python (его eventFilter — виртуальный, sip
// отдаёт вызов в Python-метод).
#include <QtCore/QCoreApplication>
#include <QtCore/QEvent>
#include <QtCore/QObject>
#include <QtCore/QPointer>
#include <QtCore/QByteArray>
#include <cstdint>
#include <cstring>

#define EUI_API extern "C" __declspec(dllexport)

namespace {

class EventGate : public QObject {
public:
    EventGate(QObject *target, const int *types, int n, const char *cls) : target_(target) {
        std::memset(mask_, 0, sizeof(mask_));
        for (int i = 0; i < n; ++i)
            if (types[i] >= 0 && types[i] < 1024) mask_[types[i] >> 6] |= (uint64_t(1) << (types[i] & 63));
        if (cls && *cls) cls_ = QByteArray(cls);
    }

    bool eventFilter(QObject *obj, QEvent *ev) override {
        const int t = int(ev->type());
        if (t < 0 || t >= 1024 || !((mask_[t >> 6] >> (t & 63)) & 1)) return false;
        if (!cls_.isEmpty() && (!obj || std::strcmp(obj->metaObject()->className(), cls_.constData()) != 0))
            return false;
        QObject *target = target_.data();
        return target ? target->eventFilter(obj, ev) : false;
    }

private:
    QPointer<QObject> target_;
    uint64_t mask_[16];
    QByteArray cls_;
};

}  // namespace

EUI_API void *eui_gate_install(QObject *target, const int *types, int n, const char *cls) {
    QCoreApplication *app = QCoreApplication::instance();
    if (!app || !target || !types || n <= 0) return nullptr;
    auto *gate = new EventGate(target, types, n, cls);
    gate->setParent(target);                 // уходит вместе с целью
    app->installEventFilter(gate);
    return gate;
}

EUI_API void eui_gate_remove(void *gate) {
    QCoreApplication *app = QCoreApplication::instance();
    if (!gate) return;
    if (app) app->removeEventFilter(static_cast<QObject *>(gate));
    static_cast<QObject *>(gate)->deleteLater();
}
