// Native catalogue parser QA. Link spoken_catalog.cpp with Qt6 Core, Gui and Network.
// Default: deterministic RSS/Atom fixtures. Add --live to verify public APIs and playable URLs.
#include "../tools/native_ui/spoken_catalog.h"
#include <QtGui/QGuiApplication>
#include <QtCore/QTimer>
#include <QtCore/QJsonDocument>
#include <QtCore/QDebug>
#include <cstdlib>
#include <cstdio>
int main(int argc,char **argv) {
 QGuiApplication app(argc,argv); qInstallMessageHandler([](QtMsgType,const QMessageLogContext&,const QString &s){ std::fprintf(stdout, "%s\n",s.toUtf8().constData()); std::fflush(stdout); });
 const QByteArray rss=R"(<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><item><title>Chapter &amp; first</title><enclosure url="audio/1.mp3" type="audio/mpeg"/><itunes:duration>01:02:03</itunes:duration></item><item><title>Duplicate</title><enclosure url="audio/1.mp3" type="audio/mpeg"/></item><item><title>Video</title><enclosure url="movie.mp4" type="video/mp4"/></item><item><title>Unsafe</title><enclosure url="file:///music.mp3" type="audio/mpeg"/></item></channel></rss>)";
 QJsonObject e{{"title","Example"},{"author","Author"},{"kind","podcasts"}};
 auto p=SpokenCatalog::parseFeed(rss,QUrl("https://example.org/feed.xml"),e);
 if(p.size()!=1 || p[0].toObject().value("duration").toDouble()!=3723 || p[0].toObject().value("url").toString()!="https://example.org/audio/1.mp3") return 2;
 const QByteArray atom=R"(<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Episode</title><author><name>Host</name></author><link rel="enclosure" type="audio/mpeg" href="https://example.org/ep.mp3"/></entry></feed>)";
 p=SpokenCatalog::parseFeed(atom,QUrl("https://example.org/feed"),e);
 if(p.size()!=1 || p[0].toObject().value("author").toString()!="Host")return 3;
 if(!SpokenCatalog::parseFeed("<rss><item>",QUrl("https://example.org/feed"),e).isEmpty())return 4;
 qInfo()<<"PASS RSS / Atom / dedupe / duration / unsafe URL / malformed XML";
 if(!app.arguments().contains("--live")) return 0;
 SpokenCatalog c;
 QTimer::singleShot(90000,&app,[&]{qCritical()<<"timeout";app.exit(5);});
 c.browse("audiobooks","ru","",[&](QJsonArray books,QString error){
  qInfo()<<"Russian audiobook catalogue:"<<books.size()<<error;
  if(books.isEmpty()){app.exit(6);return;}
  c.episodes(books[0].toObject(),[&](QJsonArray chapters,QString err){
   qInfo()<<"Full audiobook chapters:"<<chapters.size()<<err;
   if(chapters.isEmpty()){app.exit(7);return;}
   qInfo()<<chapters[0].toObject().value("url").toString();
   c.browse("podcasts","en","science",[&](QJsonArray shows,QString err){
    qInfo()<<"Podcast catalogue:"<<shows.size()<<err;
    if(shows.isEmpty()){app.exit(8);return;}
    c.episodes(shows[0].toObject(),[&](QJsonArray eps,QString err){
     qInfo()<<"Publisher audio episodes:"<<eps.size()<<err;
     if(eps.isEmpty()){app.exit(9);return;}
     qInfo()<<eps[0].toObject().value("url").toString();
     app.exit(0);
    });
   });
  });
 });
 return app.exec();
}
