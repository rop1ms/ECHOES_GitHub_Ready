# button_editor.py
"""
Редактор вида кнопки для конструктора тем ECHOES.

Кнопку собирают на маленьком холсте: форма и заливка (круг, скруглённый квадрат, таблетка…,
градиент, контур), рисунок кистью (и ластик), надписи, символы (▶ ❚❚ ▶▶ ♥ …) и картинки.
Каждый элемент можно выбрать, перетащить, увеличить колесом, перекрасить или удалить.

Готовая кнопка — это слой-картинка темы с поведением: клик выполняет выбранное действие
(играть/пауза, следующий трек, перемешать, текст песни…), при наведении кнопка чуть
увеличивается, при нажатии — «проседает». Сам рисунок хранится в слое (поле "design"),
поэтому кнопку можно открыть в редакторе снова (двойной клик по ней на холсте или
«Изменить вид кнопки…» в меню) и переделать.

Редактор — обычный виджет: конструктор показывает его вкладкой внутри своего окна
или отдельным окном (кнопка ⇱ / ⇲ в шапке редактора).
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QKeySequence, QLinearGradient, QPainter,
                         QPainterPath, QPen, QShortcut)
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFileDialog, QFontComboBox, QFrame, QGridLayout,
                             QHBoxLayout, QInputDialog, QLabel, QPushButton, QScrollArea, QSlider, QVBoxLayout,
                             QWidget)

IMAGE_FILTER = "Картинки (*.png *.jpg *.jpeg *.gif *.webp *.bmp)"

SIZES = [("square", "Квадрат", 256, 256), ("wide", "Широкая 2:1", 384, 192), ("tall", "Высокая 1:2", 192, 384),
         ("bar", "Полоска 4:1", 512, 128)]
SHAPES = [("circle", "Круг"), ("rounded", "Скруглённый"), ("square", "Квадрат"), ("pill", "Таблетка"),
          ("none", "Без фона")]
# действие по клику: ключ, название, вызов EchoScript
ACTIONS = [("play", "Играть / пауза", "toggle()"), ("next", "Следующий трек", "next()"),
           ("prev", "Предыдущий трек", "prev()"), ("shuffle", "Перемешать", "toggle_shuffle()"),
           ("artist", "Шаффл по исполнителю", "artist_shuffle()"),
           ("repeat", "Повтор", "cycle_repeat()"), ("lyrics", "Текст песни", "lyrics_mode()"),
           ("queue", "Очередь", "show_queue()"), ("cover", "Обложка", "cover_mode()"),
           ("clip", "Клип песни", "clip_mode()"),
           ("music", "Музыка", "music_hub()"), ("mute", "Звук вкл / выкл", "mute()"),
           ("fullscreen", "Полный экран", "fullscreen()"), ("files", "Добавить файлы", "add_files()"),
           ("download", "Скачать музыку", "downloader()"), ("themes", "Меню тем", "theme_menu()"),
           ("none", "Без действия (украшение)", "")]
# только одноцветные символы (никаких цветных эмодзи)
SYMBOLS = ("▶ ❚❚ ◀ ▶▶ ◀◀ ▶| |◀ ■ ▲ ▼ ⤮ ↻ ⟲ ♥ ♡ ★ ☆ ♪ ♫ ♬ ☰ ≡ ✕ ✓ ＋ − "
           "⛶ ⌂ ☾ ✦ ✧ ✱ ❖ ◆ ● ○ □ ▣").split()
TOOLS = [("select", "✥", "Выбрать и двигать (колесо — размер, Del — удалить)"), ("brush", "✎", "Кисть"),
         ("eraser", "⌫", "Ластик"), ("text", "T", "Надпись: щёлкните на холсте"),
         ("image", "▣", "Картинка из файла")]

_BG, _PANEL, _PANEL2, _LINE = "#0c0d12", "#14161f", "#1c1f2b", "#2a2e3e"
_TEXT, _MUTED, _ACC = "#eef0f6", "#8d93a5", "#8b7bff"


def default_design() -> dict:
    return {"size": "square", "w": 256, "h": 256, "shape": "circle", "fill": "#8b7bff", "fill2": "#38d6ff",
            "border": "#ffffff", "border_w": 0, "clip": True, "action": "play", "hover": True,
            "items": [{"t": "text", "text": "▶", "x": 136, "y": 128, "size": 110, "color": "#0b0b12",
                       "font": "Segoe UI Symbol", "bold": True}]}


def normalize(d) -> dict:
    out = default_design()
    if isinstance(d, dict):
        for k in out:
            if k in d:
                out[k] = copy.deepcopy(d[k])
    if out["size"] not in {s[0] for s in SIZES}:
        out["size"] = "square"
    out["w"] = int(max(32, min(1024, float(out.get("w") or 256))))
    out["h"] = int(max(32, min(1024, float(out.get("h") or 256))))
    if out["shape"] not in dict(SHAPES):
        out["shape"] = "circle"
    if out["action"] not in {a[0] for a in ACTIONS}:
        out["action"] = "play"
    out["items"] = [it for it in (out.get("items") or []) if isinstance(it, dict) and it.get("t") in
                    ("stroke", "text", "image")][:400]
    return out


def behavior_code(d: dict) -> str:
    """Поведение слоя-кнопки на EchoScript: действие по клику, подсветка при наведении, «нажатие»."""
    call = dict((a[0], a[2]) for a in ACTIONS).get(d.get("action"), "")
    hover = bool(d.get("hover", True))
    if not call and not hover:
        return ""
    lines = ["// кнопка из редактора кнопок (вид — «Изменить вид кнопки…»)", "state hv = 0", "state fl = 0"]
    if call:
        lines += ["on click {", f"  {call}", "  fl = 1", "}"]
    lines += ["on frame(dt) {"]
    if hover:
        lines += ["  hv = approach(hv, hover ? 1 : 0, 14, dt)"]
    lines += ["  fl = approach(fl, 0, 9, dt)",
              "  scale = 1 + hv * 0.07 - fl * 0.06" if hover else "  scale = 1 - fl * 0.06", "}"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ #
#  Рисование кнопки                                                   #
# ------------------------------------------------------------------ #

def _font(it) -> QFont:
    f = QFont(it.get("font") or "Segoe UI")
    f.setPixelSize(int(max(4, it.get("size", 48))))
    f.setBold(bool(it.get("bold", True)))
    return f


def item_rect(it, resolve=None) -> QRectF:
    """Рамка элемента в координатах холста кнопки."""
    t = it.get("t")
    if t == "text":
        fm = QFontMetricsF(_font(it))
        br = fm.boundingRect(QRectF(0, 0, 4000, 4000), int(Qt.AlignmentFlag.AlignCenter), it.get("text") or " ")
        return QRectF(it["x"] - br.width() / 2, it["y"] - br.height() / 2, br.width(), br.height())
    if t == "image":
        return QRectF(it["x"] - it["w"] / 2, it["y"] - it["h"] / 2, it["w"], it["h"])
    pts = it.get("pts") or []
    if not pts:
        return QRectF()
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    m = it.get("width", 6) / 2 + 2
    return QRectF(min(xs) - m, min(ys) - m, max(xs) - min(xs) + 2 * m, max(ys) - min(ys) + 2 * m)


def shape_path(d) -> QPainterPath:
    w, h = d["w"], d["h"]
    bw = float(d.get("border_w", 0))
    r = QRectF(bw / 2, bw / 2, w - bw, h - bw)
    path = QPainterPath()
    s = d.get("shape")
    if s == "circle":
        path.addEllipse(r)
    elif s == "rounded":
        rad = min(w, h) * 0.22
        path.addRoundedRect(r, rad, rad)
    elif s == "pill":
        rad = min(r.width(), r.height()) / 2
        path.addRoundedRect(r, rad, rad)
    elif s == "square":
        path.addRect(r)
    else:
        path.addRect(QRectF(0, 0, w, h))
    return path


class _Images:
    """Кэш картинок для элементов «картинка» (по пути файла)."""

    def __init__(self):
        self._c = {}

    def get(self, path):
        if not path:
            return None
        img = self._c.get(path)
        if img is None:
            img = QImage(path)
            if len(self._c) > 32:
                self._c.clear()
            self._c[path] = img
        return None if img.isNull() else img


_IMAGES = _Images()


def render(d: dict, scale: float = 1.0, resolve=None) -> QImage:
    """Кнопка целиком → картинка (прозрачный фон) в scale раз больше холста."""
    w, h = d["w"], d["h"]
    img = QImage(max(1, int(round(w * scale))), max(1, int(round(h * scale))), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(scale, scale)
    path = shape_path(d)
    if d.get("shape") != "none":
        if d.get("fill2"):
            g = QLinearGradient(0, 0, w * 0.35, h)
            g.setColorAt(0, QColor(d.get("fill") or "#000000"))
            g.setColorAt(1, QColor(d["fill2"]))
            p.fillPath(path, QBrush(g))
        elif d.get("fill"):
            p.fillPath(path, QColor(d["fill"]))
    # рисунок, надписи и картинки — на своём слое (ластик стирает только их, не форму)
    art = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    art.fill(Qt.GlobalColor.transparent)
    q = QPainter(art)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    q.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    q.scale(scale, scale)
    for it in d.get("items") or []:
        t = it.get("t")
        if t == "stroke":
            pts = it.get("pts") or []
            if not pts:
                continue
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear if it.get("erase")
                                 else QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(QColor(it.get("color") or "#ffffff"), float(it.get("width", 6)), Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            q.setPen(pen)
            q.setBrush(Qt.BrushStyle.NoBrush)
            if len(pts) == 1:
                q.drawPoint(QPointF(*pts[0]))
            else:
                sp = QPainterPath(QPointF(*pts[0]))
                for a, b in zip(pts[:-1], pts[1:]):
                    sp.quadTo(QPointF(*a), QPointF((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
                sp.lineTo(QPointF(*pts[-1]))
                q.drawPath(sp)
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        elif t == "text":
            q.setPen(QColor(it.get("color") or "#ffffff"))
            q.setFont(_font(it))
            q.drawText(QRectF(it["x"] - 2000, it["y"] - 2000, 4000, 4000), int(Qt.AlignmentFlag.AlignCenter),
                       it.get("text") or "")
        elif t == "image":
            src = resolve(it.get("src")) if resolve is not None else it.get("src")
            im = _IMAGES.get(src)
            if im is not None:
                q.drawImage(QRectF(it["x"] - it["w"] / 2, it["y"] - it["h"] / 2, it["w"], it["h"]), im)
    q.end()
    p.save()
    if d.get("clip", True) and d.get("shape") != "none":
        p.setClipPath(path)                                # рисунок не вылезает за форму кнопки
    p.resetTransform()
    p.drawImage(0, 0, art)
    p.restore()
    if d.get("shape") != "none" and float(d.get("border_w", 0)) > 0.1:
        p.setPen(QPen(QColor(d.get("border") or "#ffffff"), float(d["border_w"])))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.end()
    return img


# ------------------------------------------------------------------ #
#  Холст                                                              #
# ------------------------------------------------------------------ #

class DesignCanvas(QWidget):
    changed = pyqtSignal()            # рисунок поменялся (и уже записан в design)
    selected = pyqtSignal()

    def __init__(self, editor):
        super().__init__(editor)
        self.ed = editor
        self.setMinimumSize(260, 260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.sel = -1
        self._drag = None
        self._img = None
        self._img_key = None

    @property
    def d(self):
        return self.ed.design

    def _frame(self) -> tuple[float, QPointF]:
        """Масштаб холста кнопки на экране и его левый верхний угол."""
        w, h = self.d["w"], self.d["h"]
        k = min((self.width() - 24) / w, (self.height() - 24) / h)
        k = max(0.1, k)
        return k, QPointF((self.width() - w * k) / 2, (self.height() - h * k) / 2)

    def to_design(self, pos) -> QPointF:
        k, o = self._frame()
        return QPointF((pos.x() - o.x()) / k, (pos.y() - o.y()) / k)

    def invalidate(self):
        self._img_key = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(_BG))
        k, o = self._frame()
        w, h = self.d["w"], self.d["h"]
        r = QRectF(o.x(), o.y(), w * k, h * k)
        # шахматка — видно прозрачность
        p.save()
        p.setClipRect(r)
        c = 12
        for yy in range(int(r.height() // c) + 2):
            for xx in range(int(r.width() // c) + 2):
                p.fillRect(QRectF(r.x() + xx * c, r.y() + yy * c, c, c),
                           QColor("#22242e") if (xx + yy) % 2 else QColor("#2c2f3b"))
        p.restore()
        dpr = self.devicePixelRatioF()
        key = (json.dumps(self.d, sort_keys=True), round(k * dpr, 3))
        if key != self._img_key:
            self._img_key = key
            self._img = render(self.d, k * dpr, self.ed.resolve)
            self._img.setDevicePixelRatio(dpr)
        p.drawImage(r.topLeft(), self._img)
        p.setPen(QPen(QColor(_LINE), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        items = self.d.get("items") or []
        if 0 <= self.sel < len(items):
            br = item_rect(items[self.sel], self.ed.resolve)
            sr = QRectF(o.x() + br.x() * k, o.y() + br.y() * k, br.width() * k, br.height() * k)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(_ACC), 1.5, Qt.PenStyle.DashLine))
            p.drawRect(sr)
            p.setBrush(QColor(_TEXT))
            p.setPen(QPen(QColor(_ACC), 1.2))
            p.drawRect(QRectF(sr.right() - 5, sr.bottom() - 5, 10, 10))
        tool = self.ed.tool
        if tool in ("brush", "eraser") and self.underMouse():
            pos = self.mapFromGlobal(self.cursor().pos())
            rad = self.ed.width_val() * k / 2
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 160), 1))
            p.drawEllipse(QPointF(pos), rad, rad)
        p.end()

    def _hit(self, pt: QPointF) -> int:
        items = self.d.get("items") or []
        for i in range(len(items) - 1, -1, -1):
            if items[i].get("t") == "stroke" and items[i].get("erase"):
                continue
            if item_rect(items[i], self.ed.resolve).adjusted(-3, -3, 3, 3).contains(pt):
                return i
        return -1

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self.to_design(e.position())
        tool = self.ed.tool
        items = self.d.setdefault("items", [])
        if tool in ("brush", "eraser"):
            self.ed.snapshot()
            items.append({"t": "stroke", "pts": [[round(pt.x(), 1), round(pt.y(), 1)]],
                          "color": self.ed.color, "width": self.ed.width_val(), "erase": tool == "eraser"})
            self._drag = {"kind": "stroke"}
            self.sel = -1
            self.invalidate()
            return
        if tool == "text":
            txt, ok = QInputDialog.getText(self, "Надпись", "Текст на кнопке:")
            if ok and txt.strip():
                self.ed.snapshot()
                items.append({"t": "text", "text": txt[:60], "x": round(pt.x(), 1), "y": round(pt.y(), 1),
                              "size": self.ed.text_size(), "color": self.ed.color, "font": self.ed.font_family(),
                              "bold": self.ed.bold()})
                self.sel = len(items) - 1
                self.ed.set_tool("select")
                self.changed.emit()
                self.selected.emit()
                self.invalidate()
            return
        # выбор
        i = self._hit(pt)
        self.sel = i
        self.selected.emit()
        if i >= 0:
            it = items[i]
            br = item_rect(it, self.ed.resolve)
            corner = QPointF(br.right(), br.bottom())
            k, _o = self._frame()
            resize = (pt - corner).manhattanLength() * k <= 12 and it["t"] != "stroke"
            self.ed.snapshot()
            self._drag = {"kind": "resize" if resize else "move", "start": pt, "it": copy.deepcopy(it), "br": br}
        self.update()

    def mouseMoveEvent(self, e):
        d = self._drag
        if d is None:
            if self.ed.tool in ("brush", "eraser"):
                self.update()
            return
        pt = self.to_design(e.position())
        items = self.d.get("items") or []
        if d["kind"] == "stroke":
            items[-1]["pts"].append([round(pt.x(), 1), round(pt.y(), 1)])
            self.invalidate()
            return
        if not (0 <= self.sel < len(items)):
            return
        it, it0 = items[self.sel], d["it"]
        dx, dy = pt.x() - d["start"].x(), pt.y() - d["start"].y()
        if d["kind"] == "move":
            if it["t"] == "stroke":
                it["pts"] = [[round(x + dx, 1), round(y + dy, 1)] for x, y in it0["pts"]]
            else:
                it["x"], it["y"] = round(it0["x"] + dx, 1), round(it0["y"] + dy, 1)
        else:                                              # угол — размер (центр на месте)
            br = d["br"]
            k = max(0.05, (br.width() + dx * 2) / max(1.0, br.width()))
            if it["t"] == "text":
                it["size"] = round(max(4, it0["size"] * k), 1)
            elif it["t"] == "image":
                it["w"], it["h"] = round(max(4, it0["w"] * k), 1), round(max(4, it0["h"] * k), 1)
        self.invalidate()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.changed.emit()
            self.invalidate()

    def wheelEvent(self, e):
        items = self.d.get("items") or []
        if not (0 <= self.sel < len(items)):
            return
        self.ed.scale_selected(1.08 ** (e.angleDelta().y() / 120.0))

    def keyPressEvent(self, e):
        items = self.d.get("items") or []
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and 0 <= self.sel < len(items):
            self.ed.delete_selected()
            return
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                 Qt.Key.Key_Down: (0, step)}
        if e.key() in moves and 0 <= self.sel < len(items):
            self.ed.snapshot()
            dx, dy = moves[e.key()]
            it = items[self.sel]
            if it["t"] == "stroke":
                it["pts"] = [[x + dx, y + dy] for x, y in it["pts"]]
            else:
                it["x"] += dx
                it["y"] += dy
            self.changed.emit()
            self.invalidate()
            return
        super().keyPressEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


# ------------------------------------------------------------------ #
#  Редактор                                                           #
# ------------------------------------------------------------------ #

class _Swatch(QPushButton):
    picked = pyqtSignal(str)

    def __init__(self, color, allow_empty=False, parent=None):
        super().__init__(parent)
        self.allow_empty = allow_empty
        self.setFixedSize(56, 28)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self.c = c or ""
        self.setText("" if self.c else "нет")
        self.setStyleSheet(f"QPushButton {{ background: {self.c or _PANEL2}; border: 1px solid {_LINE}; "
                           f"border-radius: 8px; color: {_MUTED}; }}")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.c or "#ffffff"), self, "Цвет",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            v = c.name(QColor.NameFormat.HexArgb) if c.alpha() < 255 else c.name()
            if c.alpha() < 255:                            # #AARRGGBB → #RRGGBBAA как в темах
                v = "#" + v[3:] + v[1:3]
            self.set_color(v)
            self.picked.emit(v)

    def contextMenuEvent(self, e):
        if self.allow_empty:
            self.set_color("")
            self.picked.emit("")


class ButtonEditor(QWidget):
    """Редактор вида кнопки. on_apply(design, png_path) — «Готово» (конструктор кладёт кнопку в тему).
    import_image(path) → ссылка для темы, resolve(ссылка) → путь к файлу (картинки внутри кнопки)."""

    def __init__(self, design=None, on_apply=None, import_image=None, resolve=None, editing=False, parent=None,
                 dock_toggle=None, docked=True, on_close=None):
        super().__init__(parent)
        self.setObjectName("Studio")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Редактор кнопки — ECHOES")
        self.tab_title = "Кнопка"
        self.resize(980, 640)
        self.design = normalize(design)
        self.on_apply = on_apply
        self.on_close = on_close
        self._import = import_image
        self._resolve = resolve
        self.editing = editing
        self.tool = "select"
        self.color = "#ffffff"
        self._undo, self._redo = [], []
        self._build(dock_toggle, docked)
        self._sync_form()
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)

    # ── связь с темой ──
    def resolve(self, ref):
        if not ref:
            return ""
        if self._resolve is not None:
            try:
                return self._resolve(ref) or ref
            except Exception:                              # noqa: BLE001
                return ref
        return ref

    # ── интерфейс ──
    def _build(self, dock_toggle, docked):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Редактор кнопки")
        title.setObjectName("H1")
        head.addWidget(title)
        head.addStretch(1)
        self.b_undo = QPushButton("↶")
        self.b_undo.setObjectName("Tool")
        self.b_undo.setToolTip("Отменить (Ctrl+Z)")
        self.b_undo.clicked.connect(self.undo)
        self.b_redo = QPushButton("↷")
        self.b_redo.setObjectName("Tool")
        self.b_redo.setToolTip("Повторить (Ctrl+Y)")
        self.b_redo.clicked.connect(self.redo)
        head.addWidget(self.b_undo)
        head.addWidget(self.b_redo)
        if dock_toggle is not None:
            self.b_dock = QPushButton("Отдельным окном" if docked else "Встроить")
            self.b_dock.setToolTip("Показывать редактор вкладкой внутри конструктора или отдельным окном")
            self.b_dock.clicked.connect(dock_toggle)
            head.addWidget(self.b_dock)
        self.b_apply = QPushButton("Обновить кнопку" if self.editing else "Добавить кнопку в тему")
        self.b_apply.setObjectName("Primary")
        self.b_apply.clicked.connect(self.apply)
        head.addWidget(self.b_apply)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(10)
        # инструменты слева
        tools = QVBoxLayout()
        tools.setSpacing(6)
        self._tool_btns = {}
        for key, icon, tip in TOOLS:
            b = QPushButton(icon)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFixedSize(42, 38)
            b.clicked.connect(lambda _c=False, k=key: self.set_tool(k))
            tools.addWidget(b)
            self._tool_btns[key] = b
        tools.addSpacing(6)
        self.sw_color = _Swatch(self.color)
        self.sw_color.setToolTip("Цвет кисти и надписей (и выбранного элемента)")
        self.sw_color.picked.connect(self._color_picked)
        tools.addWidget(self.sw_color)
        tools.addStretch(1)
        body.addLayout(tools)
        # холст
        self.canvas = DesignCanvas(self)
        self.canvas.changed.connect(self._changed)
        self.canvas.selected.connect(self._on_select)
        body.addWidget(self.canvas, 1)
        # свойства справа
        side = QWidget()
        side.setMinimumWidth(250)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)

        def lab(t):
            x = QLabel(t.upper())
            x.setObjectName("H2")
            return x

        def row(*ws):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            for x in ws:
                if x is None:
                    h.addStretch(1)
                else:
                    h.addWidget(x)
            return w

        sv.addWidget(lab("Кнопка"))
        self.cb_size = QComboBox()
        for k, t, _w, _h in SIZES:
            self.cb_size.addItem(t, k)
        self.cb_size.currentIndexChanged.connect(self._size_changed)
        sv.addWidget(row(QLabel("Холст"), None, self.cb_size))
        self.cb_shape = QComboBox()
        for k, t in SHAPES:
            self.cb_shape.addItem(t, k)
        self.cb_shape.currentIndexChanged.connect(lambda _i: self._set("shape", self.cb_shape.currentData()))
        sv.addWidget(row(QLabel("Форма"), None, self.cb_shape))
        self.sw_fill = _Swatch("", True)
        self.sw_fill.picked.connect(lambda c: self._set("fill", c))
        self.sw_fill2 = _Swatch("", True)
        self.sw_fill2.setToolTip("Второй цвет градиента (правая кнопка мыши — убрать)")
        self.sw_fill2.picked.connect(lambda c: self._set("fill2", c))
        sv.addWidget(row(QLabel("Заливка"), None, self.sw_fill, self.sw_fill2))
        self.sw_border = _Swatch("#ffffff")
        self.sw_border.picked.connect(lambda c: self._set("border", c))
        self.sl_border = QSlider(Qt.Orientation.Horizontal)
        self.sl_border.setRange(0, 24)
        self.sl_border.valueChanged.connect(lambda v: self._set("border_w", int(v), snap=False))
        self.sl_border.sliderPressed.connect(self.snapshot)
        sv.addWidget(row(QLabel("Контур"), self.sl_border, self.sw_border))
        self.ch_clip = QCheckBox("Обрезать рисунок по форме")
        self.ch_clip.toggled.connect(lambda on: self._set("clip", bool(on)))
        sv.addWidget(self.ch_clip)

        sv.addWidget(lab("Кисть и текст"))
        self.sl_width = QSlider(Qt.Orientation.Horizontal)
        self.sl_width.setRange(1, 80)
        self.sl_width.setValue(10)
        sv.addWidget(row(QLabel("Толщина кисти"), self.sl_width))
        self.font_cb = QFontComboBox()
        self.font_cb.setCurrentFont(QFont("Segoe UI Symbol"))
        self.font_cb.currentFontChanged.connect(lambda f: self._sel_set("font", f.family()))
        sv.addWidget(self.font_cb)
        self.sl_tsize = QSlider(Qt.Orientation.Horizontal)
        self.sl_tsize.setRange(8, 400)
        self.sl_tsize.setValue(90)
        self.sl_tsize.sliderPressed.connect(self.snapshot)
        self.sl_tsize.valueChanged.connect(lambda v: self._sel_set("size", int(v), snap=False))
        self.ch_bold = QCheckBox("Жирный")
        self.ch_bold.setChecked(True)
        self.ch_bold.toggled.connect(lambda on: self._sel_set("bold", bool(on)))
        sv.addWidget(row(QLabel("Размер"), self.sl_tsize, self.ch_bold))

        sv.addWidget(lab("Символы"))
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        for n, s in enumerate(SYMBOLS):
            b = QPushButton(s)
            b.setFixedSize(QSize(32, 30))
            b.setToolTip("Добавить символ на кнопку")
            b.setStyleSheet("QPushButton { padding: 0; font-size: 15px; }")
            b.clicked.connect(lambda _c=False, s=s: self.add_symbol(s))
            grid.addWidget(b, n // 7, n % 7)
        sv.addWidget(grid_w)

        sv.addWidget(lab("Выбранный элемент"))
        self.b_front = QPushButton("Наверх")
        self.b_front.clicked.connect(lambda: self.reorder(1))
        self.b_back = QPushButton("Вниз")
        self.b_back.clicked.connect(lambda: self.reorder(-1))
        self.b_del = QPushButton("Удалить")
        self.b_del.setObjectName("Danger")
        self.b_del.clicked.connect(self.delete_selected)
        sv.addWidget(row(self.b_front, self.b_back, self.b_del))

        sv.addWidget(lab("Что делает"))
        self.cb_action = QComboBox()
        for k, t, _c in ACTIONS:
            self.cb_action.addItem(t, k)
        self.cb_action.currentIndexChanged.connect(lambda _i: self._set("action", self.cb_action.currentData()))
        sv.addWidget(self.cb_action)
        self.ch_hover = QCheckBox("Увеличивается при наведении")
        self.ch_hover.toggled.connect(lambda on: self._set("hover", bool(on)))
        sv.addWidget(self.ch_hover)
        clear = QPushButton("Очистить рисунок")
        clear.clicked.connect(self.clear_items)
        sv.addWidget(row(clear, None))
        hint = QLabel("✥ — выбрать и двигать, угол рамки или колесо — размер, Del — удалить, стрелки — сдвиг. "
                      "Цвет: сначала выберите элемент, потом цвет. Правая кнопка по образцу цвета — «нет».")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        sv.addWidget(hint)
        sv.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(side)
        sa.setMinimumWidth(270)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        body.addWidget(sa)
        root.addLayout(body, 1)
        self.set_tool("select")

    def _sync_form(self):
        d = self.design
        for w in (self.cb_size, self.cb_shape, self.cb_action, self.ch_clip, self.ch_hover, self.sl_border):
            w.blockSignals(True)
        self.cb_size.setCurrentIndex(max(0, self.cb_size.findData(d["size"])))
        self.cb_shape.setCurrentIndex(max(0, self.cb_shape.findData(d["shape"])))
        self.cb_action.setCurrentIndex(max(0, self.cb_action.findData(d["action"])))
        self.ch_clip.setChecked(bool(d.get("clip", True)))
        self.ch_hover.setChecked(bool(d.get("hover", True)))
        self.sl_border.setValue(int(d.get("border_w", 0)))
        for w in (self.cb_size, self.cb_shape, self.cb_action, self.ch_clip, self.ch_hover, self.sl_border):
            w.blockSignals(False)
        self.sw_fill.set_color(d.get("fill", ""))
        self.sw_fill2.set_color(d.get("fill2", ""))
        self.sw_border.set_color(d.get("border", "#ffffff"))
        self._update_undo()

    # ── значения инструментов ──
    def width_val(self) -> int:
        return int(self.sl_width.value())

    def text_size(self) -> int:
        return int(self.sl_tsize.value())

    def font_family(self) -> str:
        return self.font_cb.currentFont().family()

    def bold(self) -> bool:
        return self.ch_bold.isChecked()

    def set_tool(self, key):
        if key == "image":
            self.add_image()
            key = "select"
        self.tool = key
        for k, b in self._tool_btns.items():
            b.setChecked(k == key)
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if key in ("brush", "eraser", "text")
                              else Qt.CursorShape.ArrowCursor)
        self.canvas.update()

    # ── правка ──
    def snapshot(self):
        self._undo.append(json.dumps(self.design))
        del self._undo[:-80]
        self._redo.clear()
        self._update_undo()

    def _update_undo(self):
        self.b_undo.setEnabled(bool(self._undo))
        self.b_redo.setEnabled(bool(self._redo))

    def undo(self):
        if not self._undo:
            return
        self._redo.append(json.dumps(self.design))
        self.design = json.loads(self._undo.pop())
        self.canvas.sel = -1
        self._sync_form()
        self.canvas.invalidate()

    def redo(self):
        if not self._redo:
            return
        self._undo.append(json.dumps(self.design))
        self.design = json.loads(self._redo.pop())
        self.canvas.sel = -1
        self._sync_form()
        self.canvas.invalidate()

    def _changed(self):
        self._update_undo()

    def _set(self, key, val, snap=True):
        if self.design.get(key) == val:
            return
        if snap:
            self.snapshot()
        self.design[key] = val
        self.canvas.invalidate()

    def _size_changed(self, _i):
        k = self.cb_size.currentData()
        w, h = next(((sw, sh) for kk, _t, sw, sh in SIZES if kk == k), (256, 256))
        if (w, h) == (self.design["w"], self.design["h"]):
            return
        self.snapshot()
        kx, ky = w / self.design["w"], h / self.design["h"]
        for it in self.design.get("items") or []:           # рисунок — на тех же местах относительно кнопки
            if it["t"] == "stroke":
                it["pts"] = [[round(x * kx, 1), round(y * ky, 1)] for x, y in it["pts"]]
            else:
                it["x"], it["y"] = round(it["x"] * kx, 1), round(it["y"] * ky, 1)
        self.design.update(size=k, w=w, h=h)
        self.canvas.invalidate()

    def _selected(self):
        items = self.design.get("items") or []
        i = self.canvas.sel
        return items[i] if 0 <= i < len(items) else None

    def _sel_set(self, key, val, snap=True):
        it = self._selected()
        if it is None or it["t"] != "text" or it.get(key) == val:
            return
        if snap:
            self.snapshot()
        it[key] = val
        self.canvas.invalidate()

    def _color_picked(self, c):
        self.color = c or "#ffffff"
        it = self._selected()
        if it is not None and it["t"] in ("text", "stroke") and not it.get("erase"):
            self.snapshot()
            it["color"] = self.color
            self.canvas.invalidate()

    def _on_select(self):
        it = self._selected()
        if it is None:
            return
        if it["t"] == "text":
            for w in (self.sl_tsize, self.ch_bold, self.font_cb):
                w.blockSignals(True)
            self.sl_tsize.setValue(int(it.get("size", 90)))
            self.ch_bold.setChecked(bool(it.get("bold", True)))
            self.font_cb.setCurrentFont(QFont(it.get("font") or "Segoe UI"))
            for w in (self.sl_tsize, self.ch_bold, self.font_cb):
                w.blockSignals(False)
        if it.get("color"):
            self.color = it["color"]
            self.sw_color.set_color(self.color)

    def add_symbol(self, s):
        self.snapshot()
        d = self.design
        it = {"t": "text", "text": s, "x": d["w"] / 2, "y": d["h"] / 2, "size": int(min(d["w"], d["h"]) * 0.42),
              "color": self.color, "font": "Segoe UI Symbol", "bold": True}
        d.setdefault("items", []).append(it)
        self.canvas.sel = len(d["items"]) - 1
        self.set_tool("select")
        self._on_select()
        self.canvas.invalidate()

    def add_image(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Картинка на кнопку", "", IMAGE_FILTER)
        if not fn:
            return
        ref = fn
        if self._import is not None:
            try:
                ref = self._import(fn) or fn
            except Exception:                              # noqa: BLE001
                ref = fn
        img = QImage(self.resolve(ref))
        if img.isNull():
            return
        d = self.design
        k = min(d["w"] * 0.7 / img.width(), d["h"] * 0.7 / img.height())
        self.snapshot()
        d.setdefault("items", []).append({"t": "image", "src": ref, "x": d["w"] / 2, "y": d["h"] / 2,
                                          "w": round(img.width() * k, 1), "h": round(img.height() * k, 1)})
        self.canvas.sel = len(d["items"]) - 1
        self.canvas.invalidate()

    def scale_selected(self, k):
        it = self._selected()
        if it is None:
            return
        self.snapshot()
        if it["t"] == "text":
            it["size"] = round(max(4, min(800, it["size"] * k)), 1)
        elif it["t"] == "image":
            it["w"], it["h"] = round(max(4, it["w"] * k), 1), round(max(4, it["h"] * k), 1)
        else:
            r = item_rect(it)
            c = r.center()
            it["pts"] = [[round(c.x() + (x - c.x()) * k, 1), round(c.y() + (y - c.y()) * k, 1)] for x, y in it["pts"]]
            it["width"] = round(max(1, it.get("width", 6) * k), 1)
        self.canvas.invalidate()

    def delete_selected(self):
        items = self.design.get("items") or []
        i = self.canvas.sel
        if 0 <= i < len(items):
            self.snapshot()
            items.pop(i)
            self.canvas.sel = -1
            self.canvas.invalidate()

    def reorder(self, d):
        items = self.design.get("items") or []
        i = self.canvas.sel
        j = i + d
        if 0 <= i < len(items) and 0 <= j < len(items):
            self.snapshot()
            items[i], items[j] = items[j], items[i]
            self.canvas.sel = j
            self.canvas.invalidate()

    def clear_items(self):
        if self.design.get("items"):
            self.snapshot()
            self.design["items"] = []
            self.canvas.sel = -1
            self.canvas.invalidate()

    # ── результат ──
    def render_png(self) -> str:
        """Кнопка в PNG (в 2 раза крупнее холста — чёткая и на больших размерах) → путь к временному файлу."""
        img = render(self.design, 2.0, self.resolve)
        path = os.path.join(tempfile.gettempdir(), f"echoes_button_{uuid.uuid4().hex[:8]}.png")
        img.save(path, "PNG")
        return path

    def apply(self):
        if self.on_apply is None:
            return
        path = self.render_png()
        try:
            self.on_apply(copy.deepcopy(self.design), path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if not self.editing:
            self.editing = True
            self.b_apply.setText("Обновить кнопку")

    def set_docked(self, docked: bool):
        b = getattr(self, "b_dock", None)
        if b is not None:
            b.setText("Отдельным окном" if docked else "Встроить")

    def closeEvent(self, e):
        if self.on_close is not None:
            try:
                self.on_close(self)
            except RuntimeError:
                pass
        super().closeEvent(e)
