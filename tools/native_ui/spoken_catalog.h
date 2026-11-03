#pragma once

#include <QtCore/QByteArray>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonObject>
#include <QtCore/QObject>
#include <QtCore/QString>
#include <QtCore/QUrl>
#include <functional>

class QNetworkAccessManager;

// Public catalogue metadata and publisher audio feeds. All I/O is asynchronous.
class SpokenCatalog final : public QObject {
public:
    using Done = std::function<void(QJsonArray, QString)>;
    explicit SpokenCatalog(QObject *parent = nullptr);
    void browse(QString kind, QString language, QString query, Done done);
    void episodes(QJsonObject entry, Done done);

    // RSS/Atom parser shared by live requests and deterministic fixture QA.
    static QJsonArray parseFeed(const QByteArray &data, const QUrl &baseUrl,
                               const QJsonObject &entry);

private:
    using BytesDone = std::function<void(QByteArray, QString)>;
    QNetworkAccessManager *network_;
    QString cacheDir_;
    void get(QUrl url, BytesDone done, int attempt = 0);
    void libriVox(QString language, QString query, Done done);
};
