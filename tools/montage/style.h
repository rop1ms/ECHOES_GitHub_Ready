#pragma once

// DaVinci Resolve-inspired palette: graphite panels, thin dark seams, orange accent, red playhead.
namespace pal {
static const char *window="#1c1c1f",*deep="#141416",*panel="#232326",*header="#2a2a2e",*control="#323237",*hover="#3c3c42",*seam="#0d0d0f";
static const char *text="#d7d7da",*muted="#8b8b92",*accent="#e8822c",*accentHi="#f39a4c",*playhead="#e5332a";
static const char *video="#36618c",*audio="#2d6a46",*title="#6e4f96",*adjust="#8a6a2c",*wave="#8fe3b0";
}

static QString editorStyle(){return QString(R"QSS(
QWidget{background:%1;color:%2;font-family:'Segoe UI';font-size:12px;}
QMainWindow::separator{background:%7;width:2px;height:2px;}
QFrame#panel{background:%3;border:0;border-radius:0;}
QFrame#strip{background:%4;border:0;border-bottom:1px solid %7;}
QFrame#pagebar{background:%8;border-top:1px solid %7;}
QLabel{background:transparent;} QLabel#brand{font-size:15px;font-weight:800;letter-spacing:3px;color:#f2f2f2;}
QLabel#muted{color:%6;} QLabel#section{color:%6;font-size:11px;font-weight:700;letter-spacing:1px;}
QLabel#paneltitle{color:#e6e6e8;font-weight:700;font-size:12px;} QLabel#timecode{font-family:'Consolas';font-size:18px;color:#f2f2f2;letter-spacing:1px;}
QLabel#hint{color:%6;font-size:11px;}
QPushButton{background:%5;border:1px solid %7;border-radius:4px;padding:6px 11px;color:%2;}
QPushButton:hover{background:%9;} QPushButton:pressed{background:#4a4a52;} QPushButton:disabled{color:#5c5c62;background:#2a2a2e;}
QPushButton:checked{background:#4a3523;border-color:%10;color:%11;}
QPushButton#primary{background:%10;border-color:%10;color:#151515;font-weight:700;} QPushButton#primary:hover{background:%11;}
QPushButton#flat{background:transparent;border:0;padding:5px 8px;} QPushButton#flat:hover{background:%9;} QPushButton#flat:checked{background:#4a3523;color:%11;}
QToolButton{background:transparent;border:0;border-radius:4px;padding:4px 6px;color:%2;}
QToolButton:hover{background:%9;} QToolButton:checked{background:#4a3523;color:%11;} QToolButton:disabled{color:#5c5c62;}
QToolButton#rail{padding:7px 2px;font-size:10px;color:%6;border-radius:6px;} QToolButton#rail:checked{background:#313136;color:%11;}
QToolButton#page{padding:5px 14px;font-size:11px;color:%6;border-radius:6px;} QToolButton#page:checked{color:%11;background:#2b2b30;}
QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox,QPlainTextEdit,QFontComboBox{background:%8;border:1px solid %7;border-radius:4px;padding:4px 6px;selection-background-color:%10;selection-color:#111;}
QLineEdit:focus,QDoubleSpinBox:focus,QSpinBox:focus,QPlainTextEdit:focus{border-color:%10;}
QComboBox::drop-down{border:0;width:18px;} QComboBox QAbstractItemView{background:%3;border:1px solid %7;selection-background-color:#4a3523;selection-color:%11;}
QListWidget,QTableWidget{background:%8;border:0;outline:0;alternate-background-color:#1e1e21;}
QListWidget::item{padding:6px;border-radius:4px;} QListWidget::item:selected{background:#4a3523;color:%11;} QListWidget::item:hover{background:#2c2c31;}
QTabWidget::pane{border:0;background:%3;} QTabBar::tab{background:transparent;color:%6;padding:6px 10px;border-bottom:2px solid transparent;}
QTabBar::tab:selected{color:%11;border-bottom-color:%10;}
QScrollArea{border:0;background:transparent;} QScrollArea>QWidget>QWidget{background:transparent;}
QGroupBox{border:0;border-top:1px solid #303035;margin-top:14px;padding-top:16px;font-weight:700;color:%6;}
QGroupBox::title{subcontrol-origin:margin;left:0;color:#a9a9b0;font-size:11px;letter-spacing:1px;}
QSplitter::handle{background:%7;} QSplitter::handle:horizontal{width:3px;} QSplitter::handle:vertical{height:3px;}
QCheckBox{background:transparent;spacing:7px;} QCheckBox::indicator{width:14px;height:14px;border:1px solid #55555c;border-radius:3px;background:%8;}
QCheckBox::indicator:checked{background:%10;border-color:%10;} QHeaderView::section{background:%4;border:0;padding:5px;color:%6;}
QSlider::groove:horizontal{background:#3a3a40;height:3px;border-radius:1px;} QSlider::sub-page:horizontal{background:#6a5240;border-radius:1px;}
QSlider::handle:horizontal{background:#e8e8ea;width:11px;height:11px;margin:-5px 0;border-radius:6px;} QSlider::handle:horizontal:hover{background:%11;}
QScrollBar:vertical{background:%8;width:10px;} QScrollBar:horizontal{background:%8;height:10px;}
QScrollBar::handle{background:#45454c;border-radius:4px;min-height:25px;min-width:25px;} QScrollBar::handle:hover{background:#56565e;} QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;}
QMenuBar{background:%8;border-bottom:1px solid %7;padding:1px;} QMenuBar::item{padding:4px 10px;background:transparent;} QMenuBar::item:selected{background:%9;}
QMenu{background:%3;border:1px solid %7;padding:4px;} QMenu::item{padding:6px 26px 6px 22px;} QMenu::item:selected{background:#4a3523;color:%11;} QMenu::separator{height:1px;background:#36363c;margin:4px 8px;}
QToolTip{background:#2f2f34;color:#eee;border:1px solid %7;padding:5px;}
QProgressBar{background:%8;border:1px solid %7;border-radius:3px;height:8px;text-align:center;color:transparent;} QProgressBar::chunk{background:%10;border-radius:2px;}
)QSS").arg(pal::window,pal::text,pal::panel,pal::header,pal::control,pal::muted,pal::seam,pal::deep,pal::hover).arg(pal::accent,pal::accentHi);}

// Small vector icons; they stay crisp at any DPI and need no image resources.
static QIcon drawnIcon(std::function<void(QPainter&,QRectF)> draw,QColor color=QColor(pal::text),int size=20){
    QPixmap pix(size*2,size*2);pix.fill(Qt::transparent);QPainter p(&pix);p.setRenderHint(QPainter::Antialiasing);p.setPen(QPen(color,2.4,Qt::SolidLine,Qt::RoundCap,Qt::RoundJoin));p.setBrush(Qt::NoBrush);draw(p,QRectF(4,4,size*2-8,size*2-8));p.end();return QIcon(pix);
}
static QIcon glyphIcon(QString glyph,QColor color=QColor("#dedee2"),int size=20){
    return drawnIcon([glyph,color](QPainter &p,QRectF r){QFont f("Segoe UI Symbol");f.setPixelSize(int(r.height()*1.05));p.setFont(f);p.setPen(color);p.drawText(r,Qt::AlignCenter,glyph);},color,size);
}
static void drawEye(QPainter &p,QRectF r,bool off){QPainterPath e;e.moveTo(r.left(),r.center().y());e.quadTo(r.center().x(),r.top()-r.height()*.35,r.right(),r.center().y());e.quadTo(r.center().x(),r.bottom()+r.height()*.35,r.left(),r.center().y());p.drawPath(e);p.drawEllipse(r.center(),r.height()*.18,r.height()*.18);if(off)p.drawLine(r.topLeft(),r.bottomRight());}
static void drawSpeaker(QPainter &p,QRectF r,bool off){QPolygonF s{{r.left(),r.top()+r.height()*.35},{r.left()+r.width()*.28,r.top()+r.height()*.35},{r.left()+r.width()*.55,r.top()},{r.left()+r.width()*.55,r.bottom()},{r.left()+r.width()*.28,r.bottom()-r.height()*.35},{r.left(),r.bottom()-r.height()*.35}};p.drawPolygon(s);
    if(off){p.drawLine(QPointF(r.right()-r.width()*.3,r.top()+r.height()*.3),QPointF(r.right(),r.bottom()-r.height()*.3));p.drawLine(QPointF(r.right(),r.top()+r.height()*.3),QPointF(r.right()-r.width()*.3,r.bottom()-r.height()*.3));}
    else p.drawArc(QRectF(r.left()+r.width()*.45,r.top()+r.height()*.15,r.width()*.45,r.height()*.7),-60*16,120*16);}
static void drawLock(QPainter &p,QRectF r,bool on){QRectF body(r.left()+r.width()*.15,r.top()+r.height()*.45,r.width()*.7,r.height()*.55);p.drawRoundedRect(body,1.5,1.5);QRectF arc(r.left()+r.width()*.28,r.top(),r.width()*.44,r.height()*.9);p.drawArc(arc,on?0:30*16,on?180*16:150*16);}
