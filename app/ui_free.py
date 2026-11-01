# ui_free.py
"""
Родные элементы интерфейса плеера — как слои конструктора тем.

Пластинка, её фон с обложкой, визуализатор, название, кнопки (каждая отдельно), перемотка,
громкость, боковые панели и всё, что в них, — настоящие виджеты Qt, а не нарисованные копии.
Тема из конструктора хранит для них раскладку (t["native"]):

    {"vinyl": {"free": true, "x": 0.1, "y": 0.2, "w": 0.4, "h": 0.5, "hidden": false}, ...}

  free     — элемент вынут из обычной раскладки и стоит где сказано (x, y, w, h — доли
             центральной области окна), любого размера;
  hidden   — элемент убран (и не появится, даже если плеер сам попробует его показать);
  collapse — место вынутого/убранного элемента отдаётся соседям (по умолчанию нет: соседи
             остаются ровно там и такого размера, как были).

Как вынимается: на место виджета в его раскладке ставится невидимая заглушка того же
размера и с теми же правилами растяжения — раскладка не замечает подмены, и остальные
элементы не двигаются и не растягиваются. Сам виджет переносится в корневой виджет окна и
ставится по координатам. Вернуть — заглушка меняется обратно на виджет: порядок и
растяжение в раскладке не теряются, сколько бы элементов ни вынимали и в каком порядке ни
возвращали.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QRect, QRectF, QSize, QTimer
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QSizePolicy, QWidget

QMAX = 16777215


class _Placeholder(QWidget):
    """Невидимая заглушка на месте вынутого элемента: для раскладки она — тот же элемент
    (размер, мин/макс, правила растяжения), поэтому соседи не сдвигаются и не растут."""

    def __init__(self, w: QWidget, keep: bool):
        super().__init__()
        self.setObjectName("echoesPlaceholder")
        self._hint = QSize(w.sizeHint())
        self._min_hint = QSize(w.minimumSizeHint())
        if w.isVisible() and w.width() > 0 and w.height() > 0:
            # сейчас на экране — держим ровно тот размер, который был (раскладка его и дала)
            self._hint = QSize(w.size())
        self.setMinimumSize(w.minimumSize())
        self.setMaximumSize(w.maximumSize())
        self._policy = QSizePolicy(w.sizePolicy())
        self.hide()
        self.set_keep(keep)

    def set_keep(self, keep: bool):
        sp = QSizePolicy(self._policy)
        sp.setRetainSizeWhenHidden(bool(keep))          # спрятанная, но место держит
        self.setSizePolicy(sp)
        self.updateGeometry()

    def sizeHint(self):                                  # noqa: N802
        return self._hint

    def minimumSizeHint(self):                           # noqa: N802
        return self._min_hint


def _find_layout(lay, w):
    """Раскладка (любой вложенности), в которой непосредственно лежит виджет w."""
    if lay is None:
        return None
    for i in range(lay.count()):
        it = lay.itemAt(i)
        if it is None:
            continue
        if it.widget() is w:
            return lay
        sub = it.layout()
        if sub is not None:
            r = _find_layout(sub, w)
            if r is not None:
                return r
    return None


class NativeLayout(QObject):
    """Раскладка родных элементов по теме. items() — [(ключ, название, группа, виджет)]."""

    def __init__(self, win, items_fn, delegate=None):
        super().__init__(win)
        self.win = win
        self.items_fn = items_fn
        # delegate(key, spec) → True: элементом управляет его хозяин (оверлей режима текста),
        # а не перенос в корневой виджет
        self.delegate = delegate
        self.after_edit = None                      # после правки одного элемента из редактора
        self.spec: dict = {}
        self._free: dict = {}                       # ключ → {"w", "ph", "lay", "parent", "min", "max"}
        self._hidden: set = set()
        self._retain: dict = {}                     # убранные в раскладке: ключ → (виджет, прежний флаг)
        self._watch: set = set()
        self._root_watch = False

    # ── справочник ──
    def items(self):
        out = []
        for key, title, group, w in self.items_fn():
            try:
                if w is not None:
                    w.objectName()                       # виджет ещё жив (иначе RuntimeError)
                    out.append((key, title, group, w))
            except RuntimeError:
                pass
        return out

    def widget(self, key):
        for k, _t, _g, w in self.items():
            if k == key:
                return w
        return None

    def root(self):
        return self.win.centralWidget()

    def is_free(self, key) -> bool:
        return key in self._free

    def is_hidden(self, key) -> bool:
        return key in self._hidden

    def faded(self, w) -> bool:
        """Элемент погашен (прозрачность ~0 у него или у панели, где он лежит) — например, под режимом текста."""
        root = self.root()
        x = w
        while x is not None and x is not root:
            eff = x.graphicsEffect()
            if isinstance(eff, QGraphicsOpacityEffect) and eff.opacity() < 0.05:
                return True
            x = x.parentWidget()
        return False

    def rect_in_window(self, key, faded_too=False) -> QRect | None:
        """Где элемент сейчас (координаты окна) — свободный или в раскладке. Погашенные — None
        (их не видно), если не faded_too."""
        w = self.widget(key)
        if w is None:
            return None
        try:
            if w.isHidden() or not w.isVisibleTo(self.win):
                return None
            if not faded_too and self.faded(w):
                return None
            tl = w.mapTo(self.win, w.rect().topLeft())
            return QRect(tl, w.size())
        except RuntimeError:
            return None

    def frac_from_window(self, r: QRectF) -> dict:
        """Прямоугольник в координатах окна → доли центральной области."""
        root = self.root()
        g = root.geometry()
        W, H = max(1, g.width()), max(1, g.height())
        return {"x": round((r.x() - g.x()) / W, 4), "y": round((r.y() - g.y()) / H, 4),
                "w": round(max(4.0, r.width()) / W, 4), "h": round(max(4.0, r.height()) / H, 4)}

    # ── применить раскладку темы ──
    def apply(self, spec: dict | None):
        """Раскладка из темы (None/{} — всё на своих местах)."""
        spec = dict(spec or {})
        self.spec = spec
        for key in list(self._free):
            s = spec.get(key) or {}
            if not s.get("free"):
                self._restore(key)
        self._hidden = {k for k, s in spec.items() if isinstance(s, dict) and s.get("hidden")}
        for key, _t, _g, w in self.items():
            s = spec.get(key) or {}
            if self.delegate is not None and self.delegate(key, s):
                continue
            if s.get("free"):
                self._make_free(key, w)
                self.place(key)
                ent = self._free.get(key)
                if ent is not None and ent["ph"] is not None:
                    ent["ph"].set_keep(not s.get("collapse"))
            # убранный элемент в раскладке держит своё место (соседи не растягиваются), если не сказано иначе
            self._set_retain(key, w, key in self._hidden and not s.get("free") and not s.get("collapse"))
            self._watch_widget(w)
            if key in self._hidden:
                w.hide()
            elif s.get("free") or w.property("echoesNativeHidden"):
                if not w.property("echoesHidden"):
                    w.show()
            w.setProperty("echoesNativeHidden", key in self._hidden)
        if not self._root_watch and self.root() is not None:
            self.root().installEventFilter(self)
            self._root_watch = True

    def apply_one(self, key, s: dict):
        """Правка одного элемента из редактора (быстро, без пересборки остальных)."""
        spec = dict(self.spec)
        spec[key] = dict(s)
        self.apply(spec)
        if self.after_edit is not None:
            self.after_edit()

    def reset(self):
        """Тема из конструктора выключена: всё обратно в раскладки, всё видно."""
        for key in list(self._free):
            self._restore(key)
        for key in list(self._retain):
            self._set_retain(key, None, False)
        for _k, _t, _g, w in self.items():
            if w.property("echoesNativeHidden"):
                w.setProperty("echoesNativeHidden", False)
                if not w.property("echoesHidden"):
                    w.show()
        self._hidden = set()
        self.spec = {}

    def _set_retain(self, key, w, on: bool):
        """Спрятанный элемент в раскладке держит место (on) или отдаёт его соседям (как было до темы)."""
        ent = self._retain.get(key)
        if on:
            if ent is None:
                self._retain[key] = (w, w.sizePolicy().retainSizeWhenHidden())
                sp = w.sizePolicy()
                sp.setRetainSizeWhenHidden(True)
                w.setSizePolicy(sp)
            return
        if ent is None:
            return
        self._retain.pop(key, None)
        ww, orig = ent
        try:
            sp = ww.sizePolicy()
            sp.setRetainSizeWhenHidden(orig)
            ww.setSizePolicy(sp)
        except RuntimeError:
            pass

    # ── вынуть / вернуть ──
    def _make_free(self, key, w):
        if key in self._free:
            return
        self._set_retain(key, w, False)
        parent = w.parentWidget()
        lay = _find_layout(parent.layout() if parent is not None else None, w)
        ph = None
        if lay is not None:
            # заглушка — двойник элемента для раскладки: соседи остаются как были
            keep = not w.isHidden() and not bool((self.spec.get(key) or {}).get("collapse"))
            ph = _Placeholder(w, keep)
            lay.replaceWidget(w, ph)
        self._free[key] = {"w": w, "ph": ph, "lay": lay, "parent": parent,
                           "min": w.minimumSize(), "max": w.maximumSize()}
        w.setParent(self.root())
        w.setProperty("echoesFree", True)
        w.setMinimumSize(0, 0)
        w.setMaximumSize(QMAX, QMAX)
        try:
            QWidget.style(w).unpolish(w)
            QWidget.style(w).polish(w)
        except Exception:                            # noqa: BLE001
            pass

    def _restore(self, key):
        ent = self._free.pop(key, None)
        if ent is None:
            return
        w, ph, lay, parent = ent["w"], ent["ph"], ent["lay"], ent["parent"]
        try:
            w.setProperty("echoesFree", False)
            w.setParent(parent)
            w.setMinimumSize(ent["min"])
            w.setMaximumSize(ent["max"])
            if lay is not None and ph is not None:
                lay.replaceWidget(ph, w)
                ph.deleteLater()
            QWidget.style(w).unpolish(w)
            QWidget.style(w).polish(w)
            if key not in self._hidden and not w.property("echoesHidden"):
                w.show()
        except RuntimeError:
            pass

    def place(self, key):
        ent = self._free.get(key)
        s = self.spec.get(key) or {}
        root = self.root()
        if ent is None or root is None:
            return
        W, H = max(1, root.width()), max(1, root.height())
        x, y = float(s.get("x", 0.1)) * W, float(s.get("y", 0.1)) * H
        w, h = max(4.0, float(s.get("w", 0.2)) * W), max(4.0, float(s.get("h", 0.2)) * H)
        try:
            if ent["w"].parentWidget() is not root:
                return                                # его на время забрал режим текста
            ent["w"].setGeometry(int(round(x)), int(round(y)), int(round(w)), int(round(h)))
            ent["w"].raise_()
        except RuntimeError:
            self._free.pop(key, None)

    def free_widgets(self):
        return [e["w"] for e in self._free.values()]

    def raise_free(self):
        """Вынутые элементы — поверх всего в окне (режим текста поднимает свой оверлей)."""
        for e in list(self._free.values()):
            try:
                if e["w"].parentWidget() is self.root() and not e["w"].isHidden():
                    e["w"].raise_()
            except RuntimeError:
                pass

    # ── события ──
    def _watch_widget(self, w):
        if id(w) not in self._watch:
            w.installEventFilter(self)
            self._watch.add(id(w))

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Show and obj.property("echoesNativeHidden"):
            QTimer.singleShot(0, obj.hide)                # плеер сам показал спрятанный элемент
        elif t == QEvent.Type.Resize and obj is self.root() and self._free:
            for key in list(self._free):
                self.place(key)
        return False
