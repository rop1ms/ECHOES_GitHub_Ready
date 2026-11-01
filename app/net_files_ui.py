# net_files_ui.py
"""
Echoes.NET: файлы в чатах и постах — карточки с предпросмотром и окно просмотра.

Как в Telegram/Discord: превью видно сразу (его прислал отправитель вместе с сообщением),
картинки, PDF, текст и небольшие аудио скачиваются сами, видео — по клику; поверх
превью — кольцо загрузки. Клик по скачанному — просмотр внутри ECHOES:
картинки и GIF, видеоплеер (Qt Multimedia), PDF постранично (QtPdf), текст и код, содержимое
архива; аудио играет в самом плеере. Программы (.exe, .bat…) не открываются молча —
сначала предупреждение.
"""
from __future__ import annotations

import base64
import math
import os
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QTimer, QUrl
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap, QPen, QFontMetrics, QMovie, QIcon
from PyQt6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialog, QSlider,
                             QPlainTextEdit, QListWidget, QFileDialog, QMessageBox, QScrollArea, QSizePolicy)

from net_files import KIND_COLOR, DANGER, human, fmt_dur
from yamusic_theme import YELLOW, ya_font, ya_icon
from echo_glass import GlassFrame, surface_for, liquid_surface
from net_preview_window import PreviewDialog

AUTO_MAX = {"image": 15 * 2 ** 20, "audio": 12 * 2 ** 20, "pdf": 10 * 2 ** 20, "text": 2 * 2 ** 20,
            "video": 12 * 2 ** 20}


def _thumb_img(meta) -> QImage:
    th = (meta.get("pv") or {}).get("thumb")
    img = QImage()
    if th:
        try:
            img.loadFromData(base64.b64decode(th))
        except ValueError:
            pass
    return img


def open_external(path, parent=None):
    """Открыть файл программой Windows; программы и скрипты — только после предупреждения."""
    if Path(path).suffix.lower() in DANGER:
        if QMessageBox.warning(parent, "Это программа", f"«{Path(path).name}» — исполняемый файл.\n"
                               "Запускайте, только если доверяете отправителю. Открыть?",
                               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != \
                QMessageBox.StandardButton.Yes:
            return
    try:
        os.startfile(str(path))                                # noqa: S606
    except OSError as e:
        QMessageBox.information(parent, "Не открылось", str(e))


def reveal(path):
    try:
        subprocess.Popen(["explorer", "/select,", str(Path(path))])
    except OSError:
        pass


def save_as(path, parent=None):
    fn, _ = QFileDialog.getSaveFileName(parent, "Сохранить как", str(Path.home() / "Downloads" /
                                                                     Path(path).name.split("_", 1)[-1]))
    if fn:
        shutil.copyfile(path, fn)


# ══════════════════════════════════════════════════════════════════════════ #
#  Превью картинки/видео/PDF: скруглённый кадр, бейджи, кольцо загрузки
# ══════════════════════════════════════════════════════════════════════════ #

class MediaThumb(QWidget):
    def __init__(self, meta, max_w=340, max_h=280, parent=None):
        super().__init__(parent)
        self.meta = meta
        self.kind = meta["kind"]
        pv = meta.get("pv") or {}
        self.img = _thumb_img(meta)
        w, h = pv.get("w") or self.img.width() or 16, pv.get("h") or self.img.height() or 9
        k = min(max_w / max(1, w), max_h / max(1, h), 1.0 if self.kind == "image" and w < max_w else 9)
        self.setFixedSize(max(140, int(w * k)), max(90, int(h * k)))
        self.progress = None                                   # 0..1 — качается
        self.state = ""                                        # "", want, waiting, have
        self._spin = 0.0
        self._t = QTimer(self)
        self._t.setInterval(40)
        self._t.timeout.connect(self._tick)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.on_click = None
        self.blurred = False                                   # «чувствительный контент» — размыто до клика
        self._blur_img = None

    def set_blurred(self, on):
        self.blurred = bool(on)
        self._blur_img = None
        self.update()

    def _blurred(self):
        """Сильно размытая копия кадра: уменьшить до ~14 px и растянуть обратно (дважды — мягче)."""
        if self._blur_img is None and not self.img.isNull():
            small = self.img.scaled(14, 14, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            mid = small.scaled(60, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._blur_img = mid.scaled(self.img.width() or 60, self.img.height() or 60,
                                        Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return self._blur_img

    def set_local(self, path):
        if self.kind == "image":
            from PyQt6.QtGui import QImageReader
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and (size.width() > 1024 or size.height() > 840):
                # Cards only need a thumbnail. Keep full-resolution decoding for
                # the actual viewer, never for every card in a scrolling feed.
                size.scale(QSize(1024, 840), Qt.AspectRatioMode.KeepAspectRatio)
                reader.setScaledSize(size)
            img = reader.read()
        else:
            img = QImage()
        if not img.isNull():
            self.img = img
            self._blur_img = None
        self.state = "have"
        self.progress = None
        self._t.stop()
        self.update()

    def set_progress(self, frac, waiting=False):
        self.progress = frac
        self.state = "waiting" if waiting else "want"
        if self.isVisible() and not self._t.isActive():
            self._t.start()
        self.update()

    def _tick(self):
        self._spin = (self._spin + 9) % 360
        self.update()

    def hideEvent(self, e):
        self._t.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        if self.progress is not None and self.state != "have":
            self._t.start()
        super().showEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.on_click:
            self.on_click()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        p.fillRect(r, QColor("#202022"))
        if not self.img.isNull():                              # cover: заполнить кадр, обрезав лишнее
            iw, ih = self.img.width(), self.img.height()
            k = max(r.width() / iw, r.height() / ih)
            dw, dh = iw * k, ih * k
            src = self._blurred() if self.blurred else self.img
            p.drawImage(QRectF((r.width() - dw) / 2, (r.height() - dh) / 2, dw, dh), src)
        if self.blurred:                                       # чувствительный контент: размыто + подпись
            p.fillRect(r, QColor(0, 0, 0, 120))
            c = r.center()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(QRectF(c.x() - 118, c.y() - 30, 236, 60), 18, 18)
            p.setPen(QColor("#ffffff"))
            p.setFont(ya_font(13, QFont.Weight.Bold))
            p.drawText(QRectF(c.x() - 118, c.y() - 28, 236, 30), Qt.AlignmentFlag.AlignCenter, "Скрыто размытием")
            p.setPen(QColor("#cfcfcf"))
            p.setFont(ya_font(11))
            p.drawText(QRectF(c.x() - 118, c.y() - 2, 236, 26), Qt.AlignmentFlag.AlignCenter, "Нажмите, чтобы показать")
            p.end()
            return
        pv = self.meta.get("pv") or {}
        if self.kind == "video":
            p.fillRect(r, QColor(0, 0, 0, 60))
        if self.state != "have" and self.kind == "image" and self.progress is None:
            p.fillRect(r, QColor(0, 0, 0, 40))
        c = r.center()
        if self.progress is not None and self.state != "have":   # кольцо загрузки
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawEllipse(c, 28, 28)
            p.setPen(QPen(QColor(255, 255, 255, 60), 4))
            p.drawEllipse(c, 20, 20)
            pen = QPen(QColor(YELLOW), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            rr = QRectF(c.x() - 20, c.y() - 20, 40, 40)
            if self.state == "waiting" or self.progress <= 0:
                p.drawArc(rr, int(-self._spin * 16), 90 * 16)
            else:
                p.drawArc(rr, 90 * 16, int(-360 * 16 * max(0.03, self.progress)))
        elif self.kind == "video" or (self.state != "have" and self.kind in ("image", "pdf")):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawEllipse(c, 26, 26)
            if self.kind == "video" and self.state == "have":
                path = QPainterPath()
                path.moveTo(c.x() - 8, c.y() - 12); path.lineTo(c.x() + 13, c.y()); path.lineTo(c.x() - 8, c.y() + 12)
                path.closeSubpath()
                p.fillPath(path, QColor("#ffffff"))
            else:
                ya_icon("download" if self.kind != "video" else "play", 24, "#ffffff").paint(
                    p, QRectF(c.x() - 12, c.y() - 12, 24, 24).toRect())
        # бейджи: длительность / страницы / GIF / размер
        badges = []
        if self.kind == "video" and pv.get("dur"):
            badges.append(fmt_dur(pv["dur"]))
        if self.kind == "pdf" and pv.get("pages"):
            badges.append(f"PDF · {pv['pages']} стр.")
        if pv.get("gif"):
            badges.append("GIF")
        if self.state != "have":
            badges.append(human(self.meta.get("size")))
        x = 8.0
        f = ya_font(11, QFont.Weight.Bold)
        p.setFont(f)
        fm = QFontMetrics(f)
        for b in badges:
            bw = fm.horizontalAdvance(b) + 14
            br = QRectF(x, r.height() - 28, bw, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(br, 10, 10)
            p.setPen(QColor("#ffffff"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, b)
            x += bw + 6
        p.end()


class WaveBar(QWidget):
    """«Волна» аудиофайла; уже скачанная/проигранная часть — жёлтая."""

    def __init__(self, wave, parent=None):
        super().__init__(parent)
        self.wave = wave or [0.3] * 40
        self.frac = 0.0
        self.setFixedHeight(30)
        self.setMinimumWidth(160)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.wave)
        bw = self.width() / n
        for i, v in enumerate(self.wave):
            h = max(3.0, v * (self.height() - 4))
            col = QColor(YELLOW) if (i + 0.5) / n <= self.frac else QColor(255, 255, 255, 90)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(i * bw + 1, (self.height() - h) / 2, max(2.0, bw - 2), h), 1.5, 1.5)
        p.end()


# ══════════════════════════════════════════════════════════════════════════ #
#  Карточка файла в сообщении / посте
# ══════════════════════════════════════════════════════════════════════════ #

class FileCard(QFrame):
    def __init__(self, page, meta, owner, mine=False, max_w=340, parent=None):
        super().__init__(parent)
        self.page, self.meta, self.owner, self.mine = page, meta, owner, mine
        self.fid = meta["fid"]
        self.kind = meta["kind"]
        self.path = ""
        self.setObjectName("NetFile")
        self.setStyleSheet("QFrame#NetFile{background:transparent;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        pv = meta.get("pv") or {}
        self.thumb = None
        self.wave = None
        self.status = QLabel("")
        self.status.setStyleSheet("font-size:11px;color:#9a9a9a;background:transparent;")
        if self.kind in ("image", "video") or (self.kind == "pdf" and pv.get("thumb")):
            self.thumb = MediaThumb(meta, max_w, 280 if self.kind != "pdf" else 220)
            self.thumb.on_click = self.activate
            self.thumb.set_blurred(not mine and page.blur_media(self.fid))
            v.addWidget(self.thumb)
            v.addWidget(self._name_row())                       # у фото тоже: «⋯» — открыть, сохранить, размыть
        elif self.kind == "audio" and __import__("net_voicemsg").is_voice(meta):   # голосовое сообщение
            import net_voicemsg
            self.voice = net_voicemsg.VoiceRow(self, pv)
            v.addWidget(self.voice)
        elif self.kind == "audio":
            v.addWidget(self._audio_row(pv))
        else:
            v.addWidget(self._doc_box(pv, max_w))
        v.addWidget(self.status)
        page.register_file_card(self)
        self.refresh()

    # ── части ── #

    def _name_row(self):
        w = QWidget(); w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w); h.setContentsMargins(2, 0, 0, 0); h.setSpacing(6)
        n = QLabel(self.meta["name"])
        n.setStyleSheet("font-weight:700;font-size:12px;background:transparent;")
        h.addWidget(n, 1)
        h.addWidget(self._more_btn())
        return w

    def _more_btn(self):
        b = QPushButton("⋯")
        b.setFixedSize(28, 24)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet("QPushButton{background:rgba(255,255,255,0.08);border-radius:11px;padding:0;font-weight:900;}"
                        "QPushButton:hover{background:rgba(255,255,255,0.16);}")
        b.clicked.connect(lambda: self._menu(b))
        return b

    def _audio_row(self, pv):
        f = QFrame(); f.setObjectName("NetAud")
        f.setStyleSheet("QFrame#NetAud{background:rgba(0,0,0,0.35);border-radius:14px;}")
        h = QHBoxLayout(f); h.setContentsMargins(10, 8, 10, 8); h.setSpacing(10)
        cov = QLabel(); cov.setFixedSize(46, 46)
        img = _thumb_img(self.meta)
        pm = QPixmap(46, 46); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(); path.addRoundedRect(QRectF(0, 0, 46, 46), 10, 10); p.setClipPath(path)
        if not img.isNull():
            p.drawImage(QRectF(0, 0, 46, 46), img)
        else:
            p.fillRect(QRectF(0, 0, 46, 46), QColor("#2a2a2a"))
            ya_icon("note", 22, YELLOW).paint(p, QRectF(12, 12, 22, 22).toRect())
        p.end()
        cov.setPixmap(pm)
        h.addWidget(cov)
        col = QVBoxLayout(); col.setSpacing(2)
        full = pv.get("title") or Path(self.meta["name"]).stem
        t = QLabel(QFontMetrics(ya_font(13, QFont.Weight.Bold)).elidedText(full, Qt.TextElideMode.ElideRight, 230))
        t.setToolTip(full)
        t.setStyleSheet("font-weight:800;background:transparent;")
        col.addWidget(t)
        self.wave = WaveBar(pv.get("wave"))
        col.addWidget(self.wave)
        sub = QLabel(" · ".join(x for x in (pv.get("artist"), fmt_dur(pv["dur"]) if pv.get("dur") else "",
                                            human(self.meta["size"])) if x))
        sub.setStyleSheet("font-size:11px;color:#bdbdbd;background:transparent;")
        col.addWidget(sub)
        h.addLayout(col, 1)
        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(38, 38)
        self.play_btn.setIcon(ya_icon("play", 18, "#000000"))
        self.play_btn.setStyleSheet(f"QPushButton{{background:{YELLOW};border-radius:19px;padding:0;}}")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip("Слушать в плеере")
        self.play_btn.clicked.connect(self.activate)
        h.addWidget(self.play_btn)
        h.addWidget(self._more_btn())
        return f

    def _doc_box(self, pv, max_w):
        f = QFrame(); f.setObjectName("NetDoc")
        f.setStyleSheet("QFrame#NetDoc{background:rgba(0,0,0,0.35);border-radius:14px;}")
        f.setMaximumWidth(max_w + 60)
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        f.mouseReleaseEvent = lambda e: self.activate()
        v = QVBoxLayout(f); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(6)
        h = QHBoxLayout(); h.setSpacing(10)
        ext = (Path(self.meta["name"]).suffix[1:] or "?").upper()[:4]
        tile = QLabel(ext)
        tile.setFixedSize(46, 52)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(f"background:{KIND_COLOR.get(self.kind, '#9a9a9a')};color:#111;border-radius:10px;"
                           "font-weight:900;font-size:12px;")
        h.addWidget(tile)
        col = QVBoxLayout(); col.setSpacing(2)
        n = QLabel(QFontMetrics(ya_font(13)).elidedText(self.meta["name"], Qt.TextElideMode.ElideMiddle, max_w - 90))
        n.setStyleSheet("font-weight:800;background:transparent;")
        n.setToolTip(self.meta["name"])
        col.addWidget(n)
        extra = {"text": f"{pv.get('lines', 0)} строк" if pv.get("lines") else "",
                 "archive": f"{pv.get('count', 0)} файлов внутри" if pv.get("count") else "",
                 "pdf": f"{pv.get('pages', 0)} стр." if pv.get("pages") else ""}.get(self.kind, "")
        sub = QLabel(" · ".join(x for x in (human(self.meta["size"]), extra) if x))
        sub.setStyleSheet("font-size:11px;color:#bdbdbd;background:transparent;")
        col.addWidget(sub)
        h.addLayout(col, 1)
        h.addWidget(self._more_btn())
        v.addLayout(h)
        if self.kind == "text" and pv.get("text"):
            code = QLabel("\n".join(pv["text"].splitlines()[:8]))
            code.setStyleSheet("font-family:Consolas,'Cascadia Mono',monospace;font-size:11px;color:#d8d8d8;"
                               "background:rgba(255,255,255,0.05);border-radius:8px;padding:8px;")
            code.setTextFormat(Qt.TextFormat.PlainText)
            code.setMaximumWidth(max_w)
            v.addWidget(code)
        if self.kind == "archive" and pv.get("items"):
            items = pv["items"][:5]
            more = (pv.get("count") or len(items)) - len(items)
            lst = QLabel("\n".join("Файл: " + x for x in items) + (f"\n…и ещё {more}" if more > 0 else ""))
            lst.setStyleSheet("font-size:11px;color:#cfcfcf;background:transparent;")
            v.addWidget(lst)
        return f

    # ── состояние ── #

    def refresh(self):
        nd = self.page.node
        st = nd.file_state(self.fid) if nd else {}
        if st.get("state") == "have" and st.get("path") and Path(st["path"]).exists():
            self.on_done(st["path"])
            return
        if st.get("state") == "part":
            self.on_progress(st.get("got", 0))
        elif st.get("state") == "want":
            self.on_waiting()
        else:
            self.status.setText("" if self.kind in ("image", "video") else "Нажмите, чтобы скачать")
            if self._auto() and nd is not None and self.page.running():
                nd.call(nd.fetch, self.meta, self.owner)

    def _auto(self) -> bool:
        return self.meta["size"] <= AUTO_MAX.get(self.kind, 0)

    def on_progress(self, got):
        frac = got / max(1, self.meta["size"])
        if self.thumb:
            self.thumb.set_progress(frac)
        if self.wave:
            self.wave.frac = frac
            self.wave.update()
        self.status.setText(f"Скачивается через Tor · {human(got)} из {human(self.meta['size'])}")

    def on_waiting(self):
        if self.thumb:
            self.thumb.set_progress(0, waiting=True)
        self.status.setText("Скачается, когда отправитель будет в сети")

    def on_error(self, why):
        if self.thumb:
            self.thumb.progress = None
            self.thumb.update()
        self.status.setText(f"Не скачалось: {why}. Нажмите ещё раз")

    def on_done(self, path):
        if self.path == path:
            return
        self.path = path
        if self.thumb:
            self.thumb.set_local(path)
        if self.wave:
            self.wave.frac = 1.0
            self.wave.update()
        self.status.setText("")

    # ── действия ── #

    def activate(self):
        if self.thumb is not None and self.thumb.blurred:      # сначала — подтверждение
            if QMessageBox.question(self, "Показать?", "Это превью скрыто размытием: там может быть откровенный "
                                    "или шокирующий контент. Показать?") != QMessageBox.StandardButton.Yes:
                return
            self.page.reveal_media(self.fid)
            self.thumb.set_blurred(False)
            return
        if self.path and Path(self.path).exists():
            if self.kind == "audio" and getattr(self, "voice", None) is not None:
                self.voice.toggle()                            # голосовое — в своей плашке, не в плеере
            elif self.kind == "audio":
                self.page.play_local(self.path, self.meta)
            else:
                MediaViewer(self.page, self.path, self.meta, gallery_of(self.page)).exec()
            return
        nd = self.page.node
        if not self.page.running():
            self.page.toast("Включите сеть, чтобы скачать файл")
            return
        self.on_progress(0)
        nd.call(nd.fetch, self.meta, self.owner)
        self._open_when_done = self.kind != "image"

    def _menu(self, btn):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        have = bool(self.path and Path(self.path).exists())
        a = m.addAction("Открыть", self.activate)
        a = m.addAction("Открыть в программе Windows", lambda: open_external(self.path, self))
        a.setEnabled(have)
        a = m.addAction("Показать в папке", lambda: reveal(self.path))
        a.setEnabled(have)
        a = m.addAction("Сохранить как…", lambda: save_as(self.path, self))
        a.setEnabled(have)
        if not have:
            m.addAction("Скачать", self.activate)
        if self.thumb is not None and not self.thumb.blurred and not self.mine:
            m.addSeparator()
            m.addAction("Скрыть снова (размыть)", lambda: (self.page.reveal_media(self.fid, False),
                                                           self.thumb.set_blurred(True)))
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))


# ══════════════════════════════════════════════════════════════════════════ #
#  Просмотр
# ══════════════════════════════════════════════════════════════════════════ #

class _Canvas(QWidget):
    """Картинка: вписана в экран; колесо — масштаб к курсору, перетаскивание — сдвиг,
    двойной клик — «вписать» ⇄ 100 %, R — поворот. GIF анимируется."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm = QPixmap()
        self.movie = None
        self.scale = 1.0
        self.fit = True
        self.rot = 0
        self.off = QPointF(0, 0)
        self._drag = None
        self._display_cache = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.on_zoom = None
        self.on_background = None

    def load(self, path, gif=False):
        self._display_cache = None
        if self.movie is not None:
            self.movie.stop()
            self.movie.deleteLater()
            self.movie = None
        self.pm = QPixmap()
        if gif:
            self.movie = QMovie(path)
            from performance import configure_movie,config
            from PyQt6.QtGui import QImageReader
            original=QImageReader(path).size();limit=config()['preview_side']
            if original.isValid() and max(original.width(),original.height())>limit:
                self.movie.setScaledSize(original.scaled(QSize(limit,limit),Qt.AspectRatioMode.KeepAspectRatio))
            self.movie.frameChanged.connect(lambda _: self.update())
            configure_movie(self.movie,self)
            self.pm = self.movie.currentPixmap()
        else:
            from PyQt6.QtGui import QImageReader
            r = QImageReader(path)
            r.setAutoTransform(True)                         # поворот из EXIF (фото с телефона)
            from performance import config
            limit=config()['preview_side'];size=r.size()
            if size.isValid() and max(size.width(),size.height())>limit:
                r.setScaledSize(size.scaled(QSize(limit,limit),Qt.AspectRatioMode.KeepAspectRatio))
            img = r.read()
            self.pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap()
        self.rot = 0
        self.reset()

    def _src(self) -> QPixmap:
        return self.movie.currentPixmap() if self.movie is not None else self.pm

    def img_size(self):
        s = self._src().size()
        return (s.height(), s.width()) if self.rot % 180 else (s.width(), s.height())

    def fit_scale(self) -> float:
        w, h = self.img_size()
        if w <= 0 or h <= 0 or self.width() < 80 or self.height() < 80:
            return 1.0
        return min((self.width() - 40) / w, (self.height() - 40) / h, 8.0)

    def reset(self):
        self.fit = True
        self.scale = self.fit_scale()
        self.off = QPointF(0, 0)
        self._zoomed()
        self.update()

    def _zoomed(self):
        if self.on_zoom:
            self.on_zoom(self.scale)

    def zoom_to(self, s, anchor: QPointF | None = None):
        s = max(0.05, min(16.0, s))
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        c = QPointF(self.width() / 2, self.height() / 2) + self.off
        k = s / self.scale
        self.off = self.off + (anchor - c) * (1 - k)        # точка под курсором остаётся на месте
        self.scale = s
        self.fit = False
        self._clamp()
        self._zoomed()
        self.update()

    def rotate(self):
        self.rot = (self.rot + 90) % 360
        self.reset()

    def _clamp(self):
        w, h = self.img_size()
        mx = max(0.0, (w * self.scale - self.width()) / 2 + 40)
        my = max(0.0, (h * self.scale - self.height()) / 2 + 40)
        self.off = QPointF(max(-mx, min(mx, self.off.x())), max(-my, min(my, self.off.y())))

    def image_rect(self) -> QRectF:
        w, h = self.img_size()
        cw, ch = w * self.scale, h * self.scale
        c = QPointF(self.width() / 2, self.height() / 2) + self.off
        return QRectF(c.x() - cw / 2, c.y() - ch / 2, cw, ch)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit:
            self.scale = self.fit_scale()
            self._zoomed()
        self._clamp()

    def paintEvent(self, _):
        src = self._src()
        if src.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.scale < 2.5)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.image_rect()
        p.translate(r.center())
        p.rotate(self.rot)
        sw, sh = src.width() * self.scale, src.height() * self.scale
        dpr = self.devicePixelRatioF()
        width, height = max(1, round(sw * dpr)), max(1, round(sh * dpr))
        if self.movie is None and width * height <= 4_000_000:
            key = (src.cacheKey(), width, height, dpr, self.scale < 2.5)
            cache = self._display_cache
            if cache is None or cache[0] != key:
                mode = Qt.TransformationMode.SmoothTransformation if self.scale < 2.5 else Qt.TransformationMode.FastTransformation
                raster = src.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, mode)
                raster.setDevicePixelRatio(dpr)
                self._display_cache = cache = (key, raster)
            p.drawPixmap(QPointF(-sw / 2, -sh / 2), cache[1])
        else:
            self._display_cache = None
            p.drawPixmap(QRectF(-sw / 2, -sh / 2, sw, sh), src, QRectF(src.rect()))
        p.end()

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            self.zoom_to(self.scale * (1.18 if d > 0 else 1 / 1.18), e.position())

    def mouseDoubleClickEvent(self, e):
        if self.fit or abs(self.scale - self.fit_scale()) < 1e-3:
            self.zoom_to(1.0 if self.fit_scale() < 0.99 else 2.0, e.position())
        else:
            self.reset()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = (e.position(), QPointF(self.off), self.image_rect().contains(e.position()))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.off = self._drag[1] + (e.position() - self._drag[0])
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            moved = (e.position() - self._drag[0]).manhattanLength()
            on_image = self._drag[2]
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if moved < 4 and not on_image and self.on_background:
                self.on_background()                         # клик мимо картинки — закрыть, как в Telegram


def _tool_icon(name, size=18, color="#ffffff") -> "QIcon":
    """Значки панели просмотра (тонкие линии, как во всём плеере)."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    p.setPen(QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name in ("zoom_in", "zoom_out"):
        p.drawEllipse(QRectF(3.5, 3.5, 13, 13))
        p.drawLine(QPointF(14.5, 14.5), QPointF(20.5, 20.5))
        p.drawLine(QPointF(6.8, 10), QPointF(13.2, 10))
        if name == "zoom_in":
            p.drawLine(QPointF(10, 6.8), QPointF(10, 13.2))
    elif name == "fit":
        for a, b, c in (((3, 9), (3, 3), (9, 3)), ((15, 3), (21, 3), (21, 9)),
                        ((21, 15), (21, 21), (15, 21)), ((9, 21), (3, 21), (3, 15))):
            path = QPainterPath(QPointF(*a))
            path.lineTo(QPointF(*b))
            path.lineTo(QPointF(*c))
            p.drawPath(path)
    elif name == "rotate":
        path = QPainterPath()
        path.arcMoveTo(QRectF(4, 4, 16, 16), 60)
        path.arcTo(QRectF(4, 4, 16, 16), 60, 280)
        p.drawPath(path)
        p.drawLine(QPointF(16, 2.8), QPointF(16.2, 6.4))
        p.drawLine(QPointF(16.2, 6.4), QPointF(12.6, 6.8))
    elif name == "external":
        path = QPainterPath(QPointF(11, 4))
        path.lineTo(QPointF(4, 4))
        path.lineTo(QPointF(4, 20))
        path.lineTo(QPointF(20, 20))
        path.lineTo(QPointF(20, 13))
        p.drawPath(path)
        p.drawLine(QPointF(13, 11), QPointF(20, 4))
        p.drawLine(QPointF(14, 4), QPointF(20, 4))
        p.drawLine(QPointF(20, 4), QPointF(20, 10))
    elif name == "folder":
        path = QPainterPath(QPointF(3, 6.5))
        path.lineTo(QPointF(9.5, 6.5))
        path.lineTo(QPointF(11.5, 8.8))
        path.lineTo(QPointF(21, 8.8))
        path.lineTo(QPointF(21, 19))
        path.lineTo(QPointF(3, 19))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "save":
        p.drawLine(QPointF(12, 3.5), QPointF(12, 15))
        p.drawLine(QPointF(7, 10.5), QPointF(12, 15.5))
        p.drawLine(QPointF(17, 10.5), QPointF(12, 15.5))
        p.drawLine(QPointF(4.5, 20.5), QPointF(19.5, 20.5))
    elif name == "close":
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif name == "maximize":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 1.5, 1.5)
        p.drawLine(QPointF(4, 8), QPointF(20, 8))
    elif name == "fullscreen":
        for a, b, c in (((3, 9), (3, 3), (9, 3)), ((15, 3), (21, 3), (21, 9)),
                        ((21, 15), (21, 21), (15, 21)), ((9, 21), (3, 21), (3, 15))):
            corner = QPainterPath(QPointF(*a))
            corner.lineTo(QPointF(*b))
            corner.lineTo(QPointF(*c))
            p.drawPath(corner)
    elif name in ("prev", "next"):
        s = -1 if name == "prev" else 1
        p.setPen(QPen(QColor(color), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath(QPointF(12 - 3.5 * s, 5))
        path.lineTo(QPointF(12 + 3.5 * s, 12))
        path.lineTo(QPointF(12 - 3.5 * s, 19))
        p.drawPath(path)
    p.end()
    return QIcon(pm)


VIEWER_QSS = """
QLabel { color: #fff; background: transparent; }
QFrame#VBar { background: transparent; border: none; }
QPushButton#VBtn { background: transparent; border: none; border-radius: 17px; color: #fff; font-weight: 800;
                   font-size: 12px; }
QPushButton#VBtn:hover { background: rgba(255,255,255,0.12); }
QPushButton#VBtn:pressed { background: rgba(255,255,255,0.2); }
QPushButton#VNav { background: rgba(28,28,32,0.72); border: 1px solid rgba(255,255,255,0.08); border-radius: 27px; }
QPushButton#VNav:hover { background: rgba(60,60,66,0.9); }
QPushButton#VCtl { background: rgba(255,255,255,0.10); color: #fff; border: none; border-radius: 17px;
                   font-weight: 800; padding: 0 14px; }
QPushButton#VCtl:hover { background: rgba(255,255,255,0.18); }
QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.18); border-radius: 2px; }
QSlider::sub-page:horizontal { background: %s; border-radius: 2px; }
QSlider::handle:horizontal { background: #fff; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
QFrame#VPanel { background: transparent; border: none; }
""" % YELLOW


class VideoSurface(QWidget):
    """Кадры видео (QVideoSink) — вписаны с сохранением пропорций, скруглённые углы; клик — пауза."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.img = None
        self.clicked = None
        self.setMinimumSize(320, 200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_frame(self, frame):
        try:
            im = frame.toImage()
        except Exception:                                      # noqa: BLE001
            return
        if not im.isNull():
            self.img = im
            self.update()

    def mousePressEvent(self, e):
        if self.clicked:
            self.clicked()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self.img is not None:
            iw, ih = self.img.width(), self.img.height()
            k = min(self.width() / max(1, iw), self.height() / max(1, ih))
            w, h = iw * k, ih * k
            p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.img)
        p.end()


class MediaViewer(PreviewDialog):
    """Растягиваемое окно просмотра: можно развернуть на экран, картинка
    вписана; колесо — приблизить к курсору, перетащить — сдвинуть, двойной клик — 100 %,
    ← → — соседние фото из этого чата/ленты, Esc или клик мимо — закрыть. Видео, PDF, текст и
    архивы — в панели по центру с той же верхней полосой."""

    def __init__(self, page, path, meta, gallery=None):
        top = page.window() if page is not None else None
        super().__init__(top)
        self.page = page
        self.player = None
        self.gallery = [g for g in (gallery or []) if g[0] and Path(g[0]).exists()] or [(path, meta)]
        self.idx = next((i for i, g in enumerate(self.gallery) if g[0] == path), 0)
        if self.gallery[self.idx][0] != path:
            self.gallery.insert(0, (path, meta))
            self.idx = 0
        self.setStyleSheet(VIEWER_QSS)
        self._surface = None
        self.canvas = _Canvas(self)
        self.canvas.on_zoom = self._zoom_label
        self.canvas.on_background = None
        self.panel = None

        # верхняя полоса: имя слева, инструменты справа
        self.info = QLabel(self)
        self.info.setStyleSheet("font-size:14px;font-weight:800;")
        self.sub = QLabel(self)
        self.sub.setStyleSheet("font-size:12px;color:rgba(255,255,255,0.6);")
        self.bar = GlassFrame(self, 21)
        self.bar.setObjectName("VBar")
        h = QHBoxLayout(self.bar)
        h.setContentsMargins(4, 4, 4, 4)
        h.setSpacing(2)
        self.img_tools = []

        def btn(icon, tip, fn, img=False, text=None):
            b = QPushButton(text or "")
            b.setObjectName("VBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(QSize(52 if text else 34, 34))
            if icon:
                b.setIcon(_tool_icon(icon))
                b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            h.addWidget(b)
            if img:
                self.img_tools.append(b)
            return b
        btn("zoom_out", "Отдалить  (−)", lambda: self.canvas.zoom_to(self.canvas.scale / 1.25), True)
        self.zoom_btn = btn(None, "Вписать / 100 %  (0)", self._toggle_fit, True, "100%")
        btn("zoom_in", "Приблизить  (+)", lambda: self.canvas.zoom_to(self.canvas.scale * 1.25), True)
        btn("rotate", "Повернуть  (R)", self.canvas.rotate, True)
        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("background:rgba(255,255,255,0.14);")
        h.addWidget(sep)
        self.img_tools.append(sep)
        btn("save", "Сохранить как…  (Ctrl+S)", lambda: save_as(self.path, self))
        btn("folder", "Показать в папке", lambda: reveal(self.path))
        btn("external", "Открыть в программе Windows", lambda: open_external(self.path, self))
        btn("maximize", "Развернуть / восстановить окно", self.toggle_maximized)
        btn("fullscreen", "На весь экран  (F11)", self.toggle_fullscreen)
        btn("close", "Закрыть  (Esc)", self.accept)

        self.prev_b = self._nav("prev", lambda: self.step(-1))
        self.next_b = self._nav("next", lambda: self.step(1))
        self._show()

    def _nav(self, icon, fn):
        b = QPushButton(self)
        b.setObjectName("VNav")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(54, 54)
        b.setIcon(_tool_icon(icon, 24))
        b.setIconSize(QSize(24, 24))
        b.clicked.connect(fn)
        return b

    @property
    def path(self):
        return self.gallery[self.idx][0]

    @property
    def meta(self):
        return self.gallery[self.idx][1]

    # ── показ ── #

    def _show(self):
        self._stop_media()
        if self.panel is not None:
            self.panel.deleteLater()
            self.panel = None
        meta, path = self.meta, self.path
        kind = meta.get("kind")
        is_img = kind == "image"
        self.canvas.setVisible(is_img)
        for w in self.img_tools:
            w.setVisible(is_img)
        if is_img:
            self.canvas.load(path, gif=bool((meta.get("pv") or {}).get("gif")) or path.lower().endswith(".gif"))
        else:
            self.panel = GlassFrame(self, 16)
            self.panel.setObjectName("VPanel")
            v = QVBoxLayout(self.panel)
            v.setContentsMargins(14, 14, 14, 14)
            if kind == "video":
                v.addLayout(self._video(), 1)
            elif kind == "pdf":
                v.addWidget(self._pdf(), 1)
            elif kind == "text":
                v.addWidget(self._text(), 1)
            elif kind == "archive":
                v.addWidget(self._archive(), 1)
            else:
                info = QLabel(f"{meta['name']}\n{human(meta.get('size', 0))}\n\nПредпросмотр для этого типа файла "
                              "недоступен —\nоткройте его программой Windows.")
                info.setAlignment(Qt.AlignmentFlag.AlignCenter)
                info.setStyleSheet("font-size:15px;color:#cfcfcf;")
                v.addWidget(info, 1)
            self.panel.show()
        n = len(self.gallery)
        self.prev_b.setVisible(n > 1)
        self.next_b.setVisible(n > 1)
        self.info.setText(meta.get("name", ""))
        self.setWindowTitle(meta.get("name", "Просмотр вложения") + " — ECHOES.NET")
        self._sub_text()
        self._layout()

    def _sub_text(self):
        meta = self.meta
        bits = [human(meta.get("size", 0))] if meta.get("size") else []
        if meta.get("kind") == "image":
            w, h = self.canvas.img_size()
            if w and h:
                bits.append(f"{w}×{h}")
        if len(self.gallery) > 1:
            bits.append(f"{self.idx + 1} из {len(self.gallery)}")
        self.sub.setText("  ·  ".join(bits))

    def _zoom_label(self, s):
        if hasattr(self, "zoom_btn"):
            self.zoom_btn.setText(f"{round(s * 100)}%")

    def _toggle_fit(self):
        if self.canvas.fit:
            self.canvas.zoom_to(1.0)
        else:
            self.canvas.reset()

    def step(self, d):
        if len(self.gallery) < 2:
            return
        self.idx = (self.idx + d) % len(self.gallery)
        self._show()

    def _layout(self):
        if not hasattr(self, "bar"):
            return
        W, H = self.width(), self.height()
        self.bar.adjustSize()
        self.bar.move(W - self.bar.width() - 18, 16)
        # On smaller screens place the toolbar below the filename instead of
        # clipping either half of the header. Media always gets the free area.
        compact = W < self.bar.width() + 280
        tw = max(1, W - 48 if compact else W - self.bar.width() - 60)
        self.info.setGeometry(24, 12 if compact else 16, tw, 22)
        self.info.setText(self.info.fontMetrics().elidedText(self.meta.get("name", ""), Qt.TextElideMode.ElideMiddle, tw))
        self.sub.setGeometry(24, 34 if compact else 38, tw, 18)
        if compact:
            self.bar.move(max(8, (W - self.bar.width()) // 2), 56)
        top = 108 if compact else 70
        if not self.canvas.isHidden():
            self.canvas.setGeometry(8, top, max(1, W - 16), max(1, H - top - 16))
        if self.panel is not None:
            gutter = 64 if len(self.gallery) > 1 else 12
            self.panel.setGeometry(gutter, top, max(1, W - gutter * 2), max(1, H - top - 16))
        self.prev_b.move(22, H // 2 - 27)
        self.next_b.move(W - 22 - 54, H // 2 - 27)
        self.prev_b.raise_()
        self.next_b.raise_()
        self.bar.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def showEvent(self, e):
        super().showEvent(e)
        self._layout()

    def mousePressEvent(self, e):
        super().mousePressEvent(e)

    def paintEvent(self, _):
        if getattr(self, "_glass_stretch", False) and not (getattr(self, "_glass_timer", None) and
                                                           self._glass_timer.isActive()):
            self._glass_stretch = False
            self._surface = liquid_surface(self.size(), 0)
        else:
            self._surface = surface_for(self, self._surface, 0)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._surface)
        p.end()

    def keyPressEvent(self, e):
        k = e.key()
        K = Qt.Key
        if k == K.Key_Escape:
            self.toggle_fullscreen() if self.isFullScreen() else self.accept()
        elif k == K.Key_F11:
            self.toggle_fullscreen()
        elif k in (K.Key_Left, K.Key_PageUp):
            self.step(-1)
        elif k in (K.Key_Right, K.Key_PageDown, K.Key_Space) and self.player is None:
            self.step(1)
        elif not self.canvas.isHidden() and k in (K.Key_Plus, K.Key_Equal):
            self.canvas.zoom_to(self.canvas.scale * 1.25)
        elif not self.canvas.isHidden() and k == K.Key_Minus:
            self.canvas.zoom_to(self.canvas.scale / 1.25)
        elif not self.canvas.isHidden() and k == K.Key_0:
            self.canvas.reset()
        elif not self.canvas.isHidden() and k == K.Key_R:
            self.canvas.rotate()
        elif k == K.Key_S and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            save_as(self.path, self)
        elif k == K.Key_Space and self.player is not None:
            self._toggle()
        else:
            super().keyPressEvent(e)

    # ── не картинки ── #

    def _video(self):
        from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
        lay = QVBoxLayout()
        # A real preview window can use Qt's native video rendering path. This
        # avoids mapping every decoded frame back to a CPU QImage at video FPS.
        try:
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            class Output(QVideoWidget):
                def mouseReleaseEvent(output, e):
                    if e.button() == Qt.MouseButton.LeftButton:
                        self._toggle()

                def mouseDoubleClickEvent(output, e):
                    if e.button() == Qt.MouseButton.LeftButton:
                        self.toggle_fullscreen()

            vw = Output(self.panel)
            vw.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
            vw.setMinimumSize(160, 90)
            vw.setCursor(Qt.CursorShape.PointingHandCursor)
            native_video = True
        except ImportError:
            vw = VideoSurface(self.panel)
            vw.clicked = self._toggle
            native_video = False
        lay.addWidget(vw, 1)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.9)
        self.player.setAudioOutput(self.audio)
        if native_video:
            self.player.setVideoOutput(vw)
        else:
            self.sink = QVideoSink(self.player)
            self.sink.videoFrameChanged.connect(vw.set_frame)
            self.player.setVideoOutput(self.sink)
        self.player.setSource(QUrl.fromLocalFile(self.path))
        bar = QHBoxLayout()
        self.pp = QPushButton("Пауза")
        self.pp.setObjectName("VCtl")
        self.pp.setFixedHeight(34)
        self.pp.clicked.connect(self._toggle)
        bar.addWidget(self.pp)
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.sliderMoved.connect(lambda v: self.player.setPosition(v))
        bar.addWidget(self.sl, 1)
        self.tl = QLabel("0:00 / 0:00")
        self.tl.setStyleSheet("color:rgba(255,255,255,0.7);")
        bar.addWidget(self.tl)
        lay.addLayout(bar)
        self.player.durationChanged.connect(lambda d: self.sl.setRange(0, d))
        self.player.positionChanged.connect(self._pos)
        try:                                                   # музыка плеера — на паузу, пока смотрим видео
            if not getattr(self, "_resume_music", False) and self.page.shell.is_playing():
                self.page.win.toggle_play()
                self._resume_music = True
        except Exception:                                      # noqa: BLE001
            pass
        self.player.play()
        return lay

    def _toggle(self):
        from PyQt6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.pp.setText("Играть")
        else:
            self.player.play()
            self.pp.setText("Пауза")

    def _pos(self, ms):
        if not self.sl.isSliderDown():
            self.sl.setValue(ms)
        self.tl.setText(f"{fmt_dur(ms / 1000)} / {fmt_dur(self.player.duration() / 1000)}")

    def _pdf(self):
        from PyQt6.QtPdf import QPdfDocument
        from PyQt6.QtPdfWidgets import QPdfView
        self.doc = QPdfDocument(self)
        self.doc.load(self.path)
        view = QPdfView(self)
        view.setDocument(self.doc)
        view.setPageMode(QPdfView.PageMode.MultiPage)
        view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        view.setStyleSheet("background:#1a1a1a;")
        return view

    def _text(self):
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        try:
            ed.setPlainText(Path(self.path).read_bytes()[:2 * 2 ** 20].decode("utf-8", errors="replace"))
        except OSError as e:
            ed.setPlainText(str(e))
        ed.setStyleSheet("QPlainTextEdit{background:#101012;color:#e8e8e8;border:none;border-radius:12px;"
                         "padding:12px;font-family:Consolas,'Cascadia Mono',monospace;font-size:13px;}")
        return ed

    def _archive(self):
        lst = QListWidget()
        lst.setStyleSheet("QListWidget{background:#101012;color:#e8e8e8;border:none;border-radius:12px;padding:8px;}")
        try:
            import zipfile
            with zipfile.ZipFile(self.path) as z:
                for i in z.infolist()[:5000]:
                    lst.addItem(f"{i.filename}    {human(i.file_size)}")
        except Exception as e:                                 # noqa: BLE001
            lst.addItem(f"Не zip-архив или повреждён: {e}")
        return lst

    def _stop_media(self):
        if self.player is not None:
            self.player.stop()
            self.player.setVideoOutput(None)
            self.player.setAudioOutput(None)
            self.player.deleteLater()
            self.player = None
        audio = getattr(self, "audio", None)
        if audio is not None:
            audio.deleteLater()
            self.audio = None
        document = getattr(self, "doc", None)
        if document is not None:
            document.close()  # release Windows' file lock as soon as the preview closes
            document.deleteLater()
            self.doc = None
        if self.canvas.movie is not None:
            self.canvas.movie.stop()
            self.canvas.movie.setFileName("")
            self.canvas.movie.deleteLater()
            self.canvas.movie = None

    def done(self, r):
        self._stop_media()
        self.canvas.pm=QPixmap();self.canvas._display_cache=None;self._surface=None
        if getattr(self, "_resume_music", False):
            self._resume_music = False
            try:
                self.page.win.toggle_play()
            except Exception:                                  # noqa: BLE001
                pass
        super().done(r)


def gallery_of(page, card=None) -> list:
    """Скачанные картинки (и видео), видимые сейчас в чате/ленте, — для листания стрелками."""
    out = []
    try:
        for c in page.findChildren(FileCard):
            if c.path and c.kind in ("image", "video") and Path(c.path).exists() and c.isVisibleTo(c.window()):
                if c.thumb is not None and c.thumb.blurred:
                    continue
                if all(c.path != g[0] for g in out):
                    out.append((c.path, c.meta))
    except RuntimeError:
        pass
    return out
