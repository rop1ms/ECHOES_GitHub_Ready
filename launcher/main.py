"""Standalone GUI launcher; build with tools/build_launcher.ps1."""
import os
import sys
from pathlib import Path
from PyQt6.QtCore import Qt,QThread,pyqtSignal,QSettings,QTimer,QUrl,QRectF,QLockFile,QStandardPaths
from PyQt6.QtGui import QIcon,QDesktopServices,QPainter,QColor,QLinearGradient,QFont
from PyQt6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QFileDialog,QProgressBar,QPlainTextEdit,QFrame,QMessageBox)
import core
sys.path.insert(0, str(Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'app'))
import i18n
i18n.install(i18n.read_language(Path.home() / '.neon_player' / 'settings.json'))

ROOT=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]

class Hero(QWidget):
    def __init__(self):
        super().__init__();self.setFixedHeight(160)
    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g=QLinearGradient(0,0,self.width(),self.height());g.setColorAt(0,QColor('#34331a'));g.setColorAt(1,QColor('#19191c'))
        p.setBrush(g);p.setPen(Qt.PenStyle.NoPen);p.drawRoundedRect(QRectF(self.rect()),20,20)
        p.setPen(QColor('#ffe32b'));p.setFont(QFont('Segoe UI',32,QFont.Weight.Bold));p.drawText(28,65,'✺ ECHOES')
        p.setPen(QColor('#ffffff'));p.setFont(QFont('Segoe UI',13));p.drawText(30,103,'Ваша музыка. Ваше пространство.')
        p.setPen(QColor('#b6b6b0'));p.setFont(QFont('Segoe UI',10));p.drawText(30,132,'ЗАПУСК  /  ОБНОВЛЕНИЯ')

class UpdateJob(QThread):
    progress=pyqtSignal(int,str);result=pyqtSignal(bool,str)
    def __init__(self,source,target):super().__init__();self.source=source;self.target=target
    def run(self):
        try:
            backup=core.update(self.source,self.target,self.progress.emit)
            self.result.emit(True,'Готово. Предыдущая версия сохранена: '+str(backup))
        except Exception as e:self.result.emit(False,str(e))

class Launcher(QWidget):
    def __init__(self):
        super().__init__();self.setWindowTitle('ECHOES • Лаунчер');self.resize(850,800);self.setMinimumSize(700,780)
        self.store=QSettings('ECHOES','Launcher');self.job=None
        self.target=str(self.store.value('installation','') or core.discover() or '')
        self.source=str(ROOT if (ROOT/'app/main.py').exists() else self.store.value('source',''))
        icon=Path(getattr(sys,'_MEIPASS',ROOT))/'icon.ico'
        if not icon.exists():icon=ROOT/'app/icon.ico'
        self.setWindowIcon(QIcon(str(icon)))
        lay=QVBoxLayout(self);lay.setContentsMargins(24,22,24,22);lay.setSpacing(15);lay.addWidget(Hero())
        row=QHBoxLayout();title=QLabel('Готовы слушать?');title.setObjectName('heading');row.addWidget(title);row.addStretch()
        self.badge=QLabel();row.addWidget(self.badge);lay.addLayout(row)
        self.path=QLabel();self.path.setWordWrap(True);self.path.setTextFormat(Qt.TextFormat.PlainText);lay.addWidget(self.path)
        row=QHBoxLayout();self.play=QPushButton('▶  Запустить ECHOES');self.play.setObjectName('primary');self.play.setMinimumHeight(52);self.play.clicked.connect(self.launch);row.addWidget(self.play,1)
        self.select=QPushButton('Папка плеера…');self.select.clicked.connect(self.choose_target);row.addWidget(self.select);lay.addLayout(row)
        line=QFrame();line.setFrameShape(QFrame.Shape.HLine);lay.addWidget(line)
        title=QLabel('Обновления');title.setObjectName('heading');lay.addWidget(title)
        self.build=QLabel();self.build.setWordWrap(True);self.build.setTextFormat(Qt.TextFormat.PlainText);lay.addWidget(self.build)
        row=QHBoxLayout();self.choose=QPushButton('Выбрать новую сборку…');self.choose.clicked.connect(self.choose_source);row.addWidget(self.choose)
        self.install=QPushButton('Установить обновление');self.install.clicked.connect(self.update);row.addWidget(self.install);lay.addLayout(row)
        note=QLabel('Выберите распакованную сборку с папкой app. Перед обновлением закройте плеер.\nМузыка и настройки сохраняются; предыдущая версия остаётся в резервной папке.');note.setWordWrap(True);note.setObjectName('muted');lay.addWidget(note)
        self.bar=QProgressBar();self.bar.setValue(0);lay.addWidget(self.bar)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);lay.addWidget(self.status)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumHeight(90);self.log.setPlaceholderText('Здесь появится ход обновления');self.log.document().setMaximumBlockCount(150);lay.addWidget(self.log)
        row=QHBoxLayout()
        for text,path in [('Открыть папку плеера',lambda:self.target),('Настройки и журналы',lambda:str(Path.home()/'.neon_player'))]:
            b=QPushButton(text);b.clicked.connect(lambda _,fn=path:self.open_folder(fn()));row.addWidget(b)
        lay.addLayout(row);self.refresh()
        self.setStyleSheet('''QWidget {background:#101012;color:#efefef;font-family:"Segoe UI";font-size:13px;} QLabel#heading {font-size:21px;font-weight:700;} QLabel#muted {color:#9c9ca4;} QPushButton {background:#27272c;border:1px solid #36363c;border-radius:10px;padding:12px 16px;font-weight:600;} QPushButton:hover {background:#37373e;border-color:#6b6850;} QPushButton:pressed {background:#48483e;} QPushButton:disabled {color:#707078;background:#1b1b20;} QPushButton#primary {background:#ffe32b;color:#141414;border:0;font-size:18px;} QPushButton#primary:hover {background:#fff06a;} QPushButton#primary:disabled {background:#54503a;color:#999;} QProgressBar {border:0;background:#28282d;border-radius:5px;height:10px;text-align:center;color:transparent;} QProgressBar::chunk {background:#ffe32b;border-radius:5px;} QPlainTextEdit {background:#19191d;border:1px solid #303035;border-radius:8px;padding:7px;color:#bcbcc5;} QFrame[frameShape="4"] {color:#303035;}''')
    def refresh(self):
        valid=bool(self.target and core.installation(self.target));busy=self.job is not None
        self.badge.setText('●  '+core.version(self.target) if valid else '○  Плеер не выбран')
        self.path.setText(self.target if valid else 'Укажите папку установленного ECHOES (внутри должны быть app и python).')
        self.build.setText(('Сборка '+core.version(self.source)+'\n'+self.source) if self.source else 'Новая сборка пока не выбрана. Обновление работает из локальной папки.')
        self.play.setEnabled(valid and not busy);self.install.setEnabled(valid and bool(self.source) and not busy)
        self.choose.setEnabled(not busy);self.select.setEnabled(not busy)
    def choose_target(self):
        p=QFileDialog.getExistingDirectory(self,'Папка установленного ECHOES',self.target)
        if not p:return
        if not core.installation(p):self.status.setText('Нет app/main.py или python/pythonw.exe. Выберите папку установленного плеера.');return
        self.target=p;self.store.setValue('installation',p);self.refresh()
    def choose_source(self):
        p=QFileDialog.getExistingDirectory(self,'Распакованная новая сборка',self.source)
        if not p:return
        if not (Path(p)/'app/main.py').is_file():self.status.setText('В выбранной папке нет app/main.py.');return
        self.source=p;self.store.setValue('source',p);self.refresh()
    def launch(self):
        try:core.launch(self.target);self.status.setText('ECHOES запускается…')
        except Exception as e:self.status.setText(str(e))
    def update(self):
        self.bar.setValue(0);self.log.clear();self.status.setText('Подготавливаю обновление…')
        self.job=UpdateJob(self.source,self.target);self.job.progress.connect(self.progress);self.job.result.connect(self.result)
        self.job.finished.connect(self.finished);self.refresh();self.job.start()
    def progress(self,n,t):self.bar.setValue(n);self.log.appendPlainText(i18n.tr(t))
    def result(self,ok,text):self.status.setText(text);self.log.appendPlainText(('✓ ' if ok else i18n.tr('Ошибка: '))+i18n.tr(text))
    def finished(self):self.job.deleteLater();self.job=None;self.refresh()
    def open_folder(self,path):
        if path and Path(path).is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:self.status.setText('Папка пока не создана или не выбрана.')
    def closeEvent(self,event):
        if self.job is not None:self.status.setText('Дождитесь завершения обновления — сейчас заменяются файлы.');event.ignore()
        else:event.accept()

if __name__=='__main__':
    app=QApplication(sys.argv);app.setStyle('Fusion');i18n.start(app)
    lock=QLockFile(str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))/'echoes-launcher.lock'))
    if not lock.tryLock(0):
        QMessageBox.information(None,'ECHOES','Лаунчер уже открыт. Перейдите к его окну.');sys.exit(0)
    w=Launcher();w.show()
    if '--smoke-test' in sys.argv:
        def capture():
            w.grab().save(str(ROOT/'launcher-preview.png'));app.quit()
        QTimer.singleShot(800,capture)
    sys.exit(app.exec())

