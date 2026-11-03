#include "spoken_catalog.h"

#include <QtCore/QCollator>
#include <QtCore/QCryptographicHash>
#include <QtCore/QDateTime>
#include <QtCore/QDir>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QJsonDocument>
#include <QtCore/QMap>
#include <QtCore/QPointer>
#include <QtCore/QRegularExpression>
#include <QtCore/QSaveFile>
#include <QtCore/QSet>
#include <QtCore/QStandardPaths>
#include <QtCore/QTimer>
#include <QtCore/QUrlQuery>
#include <QtCore/QXmlStreamReader>
#include <QtGui/QTextDocumentFragment>
#include <QtNetwork/QNetworkAccessManager>
#include <QtNetwork/QNetworkReply>
#include <QtNetwork/QNetworkRequest>
#include <algorithm>
#include <memory>
#include <utility>

namespace {
constexpr qint64 kMaxBytes = 20 * 1024 * 1024;
constexpr qint64 kCacheSeconds = 6 * 3600;

bool webUrl(const QUrl &url) {
    return url.isValid() && (url.scheme() == "https" || url.scheme() == "http")
           && !url.host().isEmpty() && url.userInfo().isEmpty();
}

QString clean(const QString &html, int limit = 10000) {
    return QTextDocumentFragment::fromHtml(html.left(60000)).toPlainText().simplified().left(limit);
}

QString stringValue(const QJsonValue &value) {
    if (value.isString()) return value.toString();
    if (value.isDouble()) return QString::number(value.toInteger());
    if (value.isArray()) {
        QStringList out;
        for (const auto &v : value.toArray()) {
            const auto s = stringValue(v);
            if (!s.isEmpty()) out << s;
        }
        return out.join(", ");
    }
    return {};
}

QString imageUrl(const QString &value, const QUrl &base = {}) {
    auto url = base.resolved(QUrl(value.trimmed()));
    return webUrl(url) ? url.toString() : QString();
}

double duration(const QString &value) {
    const auto parts = value.trimmed().split(':');
    double seconds = 0;
    if (parts.size() > 3) return 0;
    for (const auto &part : parts) {
        bool ok = false;
        const double n = part.toDouble(&ok);
        if (!ok || n < 0) return 0;
        seconds = seconds * 60 + n;
    }
    return seconds;
}

bool audioEnclosure(QString mime, const QUrl &url) {
    mime = mime.toLower();
    if (mime.startsWith("audio/")) return true;
    const auto ext = QFileInfo(url.path()).suffix().toLower();
    return (mime.isEmpty() || mime == "application/octet-stream")
        && QStringList{"mp3", "m4a", "m4b", "aac", "ogg", "opus", "wav", "flac"}.contains(ext);
}

QString escapeQuery(QString query) {
    query = query.simplified().left(180);
    query.replace('\\', "\\\\").replace('"', "\\\"");
    return query;
}

QJsonArray archiveEpisodes(const QJsonObject &document, const QJsonObject &entry) {
    struct Candidate { QJsonObject value; QString sort; int preference; };
    QMap<QString, Candidate> tracks;
    const QString identifier = entry.value("archive_id").toString();
    const auto files = document.value("files").toArray();
    for (const auto &value : files) {
        const auto file = value.toObject();
        const QString name = file.value("name").toString();
        if (!name.endsWith(".mp3", Qt::CaseInsensitive)) continue;
        const auto fileFormat = file.value("format").toString();
        if (fileFormat.contains("Sample", Qt::CaseInsensitive)) continue;
        QString key = file.value("original").toString();
        if (key.isEmpty()) key = name;
        key.remove(QRegularExpression("(?:_64kb|_128kb)?\\.[^.]+$", QRegularExpression::CaseInsensitiveOption));
        const int preference = file.value("source").toString() == "original" ? 3
                              : fileFormat.contains("VBR", Qt::CaseInsensitive) ? 2 : 1;
        if (tracks.contains(key) && tracks.value(key).preference >= preference) continue;
        const QUrl audio("https://archive.org/download/" + QString::fromUtf8(QUrl::toPercentEncoding(identifier))
                         + '/' + QString::fromUtf8(QUrl::toPercentEncoding(name, "/")));
        QString title = clean(stringValue(file.value("title")), 300);
        if (title.isEmpty()) { title = QFileInfo(name).completeBaseName(); title.replace('_', ' '); }
        const auto track = stringValue(file.value("track"));
        QJsonObject episode{{"id", identifier + '/' + name}, {"title", title},
                            {"author", entry.value("author")}, {"cover", entry.value("cover")},
                            {"url", audio.toString()}, {"duration", duration(stringValue(file.value("length")))},
                            {"kind", "audiobooks"}, {"source", "LibriVox"},
                            {"album", entry.value("title")}};
        QString order = track.section('/', 0, 0);
        bool numeric = false;
        const int number = order.toInt(&numeric);
        if (numeric) order = QString::number(number).rightJustified(6, '0') + ':' + name;
        else order = name;
        tracks.insert(key, {episode, order, preference});
    }
    QList<Candidate> ordered = tracks.values();
    QCollator collator;
    collator.setNumericMode(true);
    std::sort(ordered.begin(), ordered.end(), [&collator](const Candidate &a, const Candidate &b) {
        return collator.compare(a.sort, b.sort) < 0;
    });
    QJsonArray out;
    for (const auto &track : ordered) { out.append(track.value); if (out.size() >= 1000) break; }
    return out;
}
} // namespace

SpokenCatalog::SpokenCatalog(QObject *parent) : QObject(parent), network_(new QNetworkAccessManager(this)) {
    cacheDir_ = QStandardPaths::writableLocation(QStandardPaths::GenericCacheLocation) + "/ECHOES/spoken";
    QDir().mkpath(cacheDir_);
}

void SpokenCatalog::get(QUrl url, BytesDone done, int attempt) {
    if (!webUrl(url)) { done({}, "unsupported_url"); return; }
    const auto key = QCryptographicHash::hash(url.toEncoded(), QCryptographicHash::Sha256).toHex();
    const auto path = cacheDir_ + '/' + QString::fromLatin1(key) + ".cache";
    QFile cached(path);
    QByteArray stale;
    if (cached.open(QIODevice::ReadOnly) && cached.size() <= kMaxBytes) {
        stale = cached.readAll();
        if (!stale.isEmpty() && QFileInfo(cached).lastModified().secsTo(QDateTime::currentDateTime()) < kCacheSeconds) {
            QTimer::singleShot(0, this, [done, stale] { done(stale, {}); });
            return;
        }
    }
    QNetworkRequest request(url);
    request.setRawHeader("User-Agent", "ECHOES/2026.11.03 (+https://github.com/rop1ms/Echoes)");
    request.setRawHeader("Accept", "application/json, application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1");
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy);
    request.setMaximumRedirectsAllowed(5);
    request.setTransferTimeout(20000);
    auto *reply = network_->get(request);
    QTimer::singleShot(25000, reply, [reply] {
        if (!reply->isFinished()) reply->abort();
    });
    auto body = std::make_shared<QByteArray>();
    auto tooLarge = std::make_shared<bool>(false);
    connect(reply, &QNetworkReply::readyRead, this, [reply, body, tooLarge] {
        body->append(reply->readAll());
        if (body->size() > kMaxBytes) { *tooLarge = true; reply->abort(); }
    });
    connect(reply, &QNetworkReply::finished, this, [this, reply, body, tooLarge, url, done, attempt, path, stale] {
        body->append(reply->readAll());
        const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const bool failed = reply->error() != QNetworkReply::NoError || status >= 400;
        reply->deleteLater();
        if (*tooLarge || body->size() > kMaxBytes) { done({}, "response_too_large"); return; }
        if (failed) {
            if (attempt == 0 && (status == 0 || status == 429 || status >= 500)) {
                QTimer::singleShot(700, this, [this, url, done] { get(url, done, 1); });
                return;
            }
            if (!stale.isEmpty()) { done(stale, {}); return; }
            done({}, status == 404 ? "not_found" : "network_error");
            return;
        }
        if (body->isEmpty()) { done({}, "invalid_response"); return; }
        QSaveFile file(path);
        if (file.open(QIODevice::WriteOnly)) { file.write(*body); file.commit(); }
        done(*body, {});
    });
}

void SpokenCatalog::browse(QString kind, QString language, QString query, Done done) {
    query = query.simplified().left(180);
    if (kind == "podcasts") {
        QUrl url("https://itunes.apple.com/search");
        QUrlQuery params;
        params.addQueryItem("term", query.isEmpty() ? (language == "ru" ? QString::fromUtf8("подкасты") : "podcast") : query);
        params.addQueryItem("media", "podcast");
        params.addQueryItem("entity", "podcast");
        params.addQueryItem("country", language == "ru" ? "RU" : "US");
        params.addQueryItem("limit", "36");
        url.setQuery(params);
        get(url, [done](const QByteArray &body, const QString &error) {
            if (!error.isEmpty()) { done({}, error); return; }
            const auto document = QJsonDocument::fromJson(body);
            if (!document.isObject() || !document.object().value("results").isArray()) {
                done({}, "invalid_response"); return;
            }
            QJsonArray out;
            QSet<QString> seen;
            for (const auto &value : document.object().value("results").toArray()) {
                const auto item = value.toObject();
                const QString feed = imageUrl(item.value("feedUrl").toString());
                const QString title = clean(item.value("collectionName").toString(), 300);
                if (feed.isEmpty() || title.isEmpty() || seen.contains(feed)) continue;
                seen.insert(feed);
                QJsonObject entry{{"id", "podcast:" + stringValue(item.value("collectionId"))}, {"title", title},
                                  {"author", clean(item.value("artistName").toString(), 200)},
                                  {"description", clean(item.value("primaryGenreName").toString(), 500)},
                                  {"cover", imageUrl(item.value("artworkUrl600").toString(item.value("artworkUrl100").toString()))},
                                  {"url", imageUrl(item.value("collectionViewUrl").toString())}, {"feed", feed},
                                  {"kind", "podcasts"}, {"source", "Apple Podcasts"}};
                out.append(entry);
            }
            done(out, {});
        });
        return;
    }
    if (kind != "audiobooks") { done({}, "unsupported_kind"); return; }
    // Internet Archive hosts the actual LibriVox recordings and indexes languages.
    // LibriVox's latest-project API includes unfinished projects and cannot filter language.
    QUrl url("https://archive.org/advancedsearch.php");
    QString search = "collection:librivoxaudio AND mediatype:audio";
    search += language == "ru" ? " AND language:(Russian OR rus)" : " AND language:(English OR eng)";
    if (!query.isEmpty()) search += " AND (\"" + escapeQuery(query) + "\")";
    QUrlQuery params;
    params.addQueryItem("q", search);
    params.addQueryItem("output", "json");
    params.addQueryItem("rows", "36");
    params.addQueryItem("sort[]", "downloads desc");
    for (const auto &field : {"identifier", "title", "creator", "description", "language"}) params.addQueryItem("fl[]", field);
    url.setQuery(params);
    get(url, [this, language, query, done](const QByteArray &body, const QString &error) {
        const auto document = QJsonDocument::fromJson(body);
        if (!error.isEmpty() || !document.isObject()
            || !document.object().value("response").toObject().value("docs").isArray()) {
            libriVox(language, query, done); return;
        }
        QJsonArray out;
        for (const auto &value : document.object().value("response").toObject().value("docs").toArray()) {
            const auto item = value.toObject();
            const auto id = item.value("identifier").toString();
            const auto title = clean(stringValue(item.value("title")), 300);
            if (id.isEmpty() || title.isEmpty()) continue;
            const auto encoded = QString::fromUtf8(QUrl::toPercentEncoding(id));
            QJsonObject entry{{"id", "ia:" + id}, {"archive_id", id}, {"title", title},
                              {"author", clean(stringValue(item.value("creator")), 200)},
                              {"description", clean(stringValue(item.value("description")))},
                              {"cover", "https://archive.org/services/img/" + encoded},
                              {"url", "https://archive.org/details/" + encoded}, {"feed", ""},
                              {"kind", "audiobooks"}, {"source", "LibriVox · Internet Archive"}};
            out.append(entry);
        }
        done(out, {});
    });
}

void SpokenCatalog::libriVox(QString language, QString query, Done done) {
    QUrl url("https://librivox.org/api/feed/audiobooks");
    QUrlQuery params;
    params.addQueryItem("format", "json");
    params.addQueryItem("limit", query.isEmpty() ? "500" : "100");
    params.addQueryItem("coverart", "1");
    params.addQueryItem("sort_order", "desc");
    if (!query.isEmpty()) params.addQueryItem("title", query);
    url.setQuery(params);
    get(url, [language, done](const QByteArray &body, const QString &error) {
        if (!error.isEmpty()) { done({}, error); return; }
        const auto document = QJsonDocument::fromJson(body);
        if (!document.isObject()) { done({}, "invalid_response"); return; }
        const auto expected = language == "ru" ? "Russian" : "English";
        QJsonArray out;
        for (const auto &value : document.object().value("books").toArray()) {
            const auto book = value.toObject();
            if (book.value("language").toString() != expected || book.value("url_librivox").toString().isEmpty()) continue;
            const auto feed = imageUrl(book.value("url_rss").toString());
            if (feed.isEmpty()) continue;
            QStringList authors;
            for (const auto &a : book.value("authors").toArray()) {
                const auto author = a.toObject();
                authors << (author.value("first_name").toString() + ' ' + author.value("last_name").toString()).simplified();
            }
            out.append(QJsonObject{{"id", "lv:" + stringValue(book.value("id"))}, {"title", clean(book.value("title").toString(), 300)},
                                   {"author", authors.join(", ")}, {"description", clean(book.value("description").toString())},
                                   {"cover", imageUrl(book.value("coverart_jpg").toString())},
                                   {"url", imageUrl(book.value("url_librivox").toString())}, {"feed", feed},
                                   {"kind", "audiobooks"}, {"source", "LibriVox"}});
            if (out.size() >= 36) break;
        }
        done(out, {});
    });
}

void SpokenCatalog::episodes(QJsonObject entry, Done done) {
    const QString archive = entry.value("archive_id").toString();
    if (!archive.isEmpty()) {
        const QUrl url("https://archive.org/metadata/" + QString::fromUtf8(QUrl::toPercentEncoding(archive)));
        get(url, [entry, done](const QByteArray &body, const QString &error) {
            if (!error.isEmpty()) { done({}, error); return; }
            const auto doc = QJsonDocument::fromJson(body);
            if (!doc.isObject()) { done({}, "invalid_response"); return; }
            const auto out = archiveEpisodes(doc.object(), entry);
            done(out, out.isEmpty() ? "empty_feed" : QString());
        });
        return;
    }
    const QUrl url(entry.value("feed").toString());
    get(url, [url, entry, done](const QByteArray &body, const QString &error) {
        if (!error.isEmpty()) { done({}, error); return; }
        const auto out = parseFeed(body, url, entry);
        done(out, out.isEmpty() ? "empty_feed" : QString());
    });
}

QJsonArray SpokenCatalog::parseFeed(const QByteArray &data, const QUrl &baseUrl, const QJsonObject &entry) {
    QJsonArray out;
    QSet<QString> seen;
    QXmlStreamReader xml(data);
    // External entities are not resolved; QXmlStreamReader has no network resolver.
    QJsonObject current;
    bool inItem = false;
    QString channelCover = entry.value("cover").toString();
    QString channelAuthor = entry.value("author").toString();
    QString channelTitle = entry.value("title").toString();
    int itemDepth = 0;
    int depth = 0;
    while (!xml.atEnd()) {
        xml.readNext();
        if (xml.isStartElement()) {
            ++depth;
            const QString name = xml.name().toString().toLower();
            if ((name == "item" || name == "entry") && !inItem) {
                inItem = true;
                itemDepth = depth;
                current = QJsonObject{{"author", channelAuthor}, {"cover", channelCover},
                                      {"kind", entry.value("kind")}, {"source", entry.value("source")},
                                      {"album", channelTitle}};
                continue;
            }
            const auto attrs = xml.attributes();
            if (name == "image" && attrs.hasAttribute("href")) {
                const auto image = imageUrl(attrs.value("href").toString(), baseUrl);
                if (inItem) current.insert("cover", image); else channelCover = image;
            }
            if (inItem && (name == "enclosure" || name == "content" || (name == "link" && attrs.value("rel") == "enclosure"))) {
                const QString raw = attrs.hasAttribute("url") ? attrs.value("url").toString() : attrs.value("href").toString();
                const QUrl audio = baseUrl.resolved(QUrl(raw));
                if (webUrl(audio) && audioEnclosure(attrs.value("type").toString(), audio)
                    && current.value("url").toString().isEmpty()) current.insert("url", audio.toString());
            }
            if (inItem && (name == "title" || name == "description" || name == "summary" || name == "duration"
                           || name == "author" || name == "creator" || name == "guid" || name == "id" || name == "pubdate" || name == "published")) {
                const QString text = xml.readElementText(QXmlStreamReader::IncludeChildElements);
                --depth;
                if (name == "title") current.insert("title", clean(text, 500));
                else if (name == "description" || name == "summary") current.insert("description", clean(text));
                else if (name == "duration") current.insert("duration", duration(text));
                else if (name == "author" || name == "creator") current.insert("author", clean(text, 200));
                else if (name == "guid" || name == "id") current.insert("id", text.trimmed().left(1000));
                else current.insert("published", text.trimmed().left(100));
            }
        } else if (xml.isEndElement()) {
            if (inItem && depth == itemDepth) {
                const QString audio = current.value("url").toString();
                if (!audio.isEmpty() && !current.value("title").toString().isEmpty() && !seen.contains(audio)) {
                    seen.insert(audio);
                    if (current.value("id").toString().isEmpty()) current.insert("id", audio);
                    out.append(current);
                    if (out.size() >= 300) break;
                }
                inItem = false;
            }
            --depth;
        }
    }
    // Never offer a truncated/malformed document as a valid playlist.
    if (xml.hasError()) return {};
    return out;
}
