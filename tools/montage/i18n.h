#pragma once
#include <QtCore>

// Stable UTF-8 storage: translated captions may also be used in catalog structs.
// Read once, outside paint paths; the Python host passes its selected language.
static QString EchoesText(const char *source) {
    static const bool english = qEnvironmentVariable("ECHOES_LANGUAGE", "en") != "ru";
    if (!english) return QString::fromUtf8(source);
    static const QHash<QByteArray, QByteArray> catalog = [] {
        QHash<QByteArray, QByteArray> values;
        QFile file(QCoreApplication::applicationDirPath() + "/../assets/i18n_native_en.json");
        if (file.open(QIODevice::ReadOnly)) {
            const auto object = QJsonDocument::fromJson(file.readAll()).object();
            for (auto it = object.begin(); it != object.end(); ++it)
                values.insert(it.key().toUtf8(), it.value().toString().toUtf8());
        }
        return values;
    }();
    const auto found = catalog.constFind(QByteArray(source));
    return QString::fromUtf8(found == catalog.cend() ? source : found.value().constData());
}
