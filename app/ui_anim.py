# ui_anim.py
"""
Анимации интерфейса ECHOES — короткие и уместные (160–220 мс), без нагрузки, когда окно скрыто.

  • сворачивание окна: окно уменьшается и уходит вниз, к панели задач; при разворачивании — поднимается
    оттуда же, вырастая до своего размера; первое появление окна при запуске — мягкий fade-in;
  • смена вкладок/страниц (QStackedWidget): новая страница наплывает в сторону перехода (вперёд — справа,
    назад — слева); «Текст» и «Очередь» выезжают снизу шторкой и уезжают вниз по стрелке «свернуть»
    (рисуются снимки страниц — сами страницы в каждом кадре не перерисовываются);
  • меню, выпадающие списки, подсказки — системное плавное появление Qt.

Отключается настройкой settings["ui_animations"] = False (Настройки → «Анимации интерфейса»).

Подключение: ui_anim.install(win) в MainWindow.__init__; переключение страниц — ui_anim.switch(stack, w, dir).
Свернуть окно — win.animated_minimize() (кнопка «—» в заголовке).
"""
from __future__ import annotations
import weakref
import time

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPropertyAnimation,
                          QRect, QSize, Qt, QTimer, QVariantAnimation, pyqtSlot)
from PyQt6 import sip
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QScrollArea, QStackedWidget, QWidget

DUR_PAGE = 230          # вкладки: новая страница наплывает
DUR_SHEET = 330         # «Текст» / «Очередь» выезжают снизу
DUR_SHEET_OUT = 270     # и уезжают вниз (по стрелке «свернуть»)
DUR_MIN = 190
DUR_RESTORE = 230


def enabled(win=None) -> bool:
    w = win or _WIN
    try:
        settings = getattr(w, "settings", None) or {}
        return bool(settings.get("ui_animations", True) and settings.get("motion_profile") != "reduced")
    except Exception:                                      # noqa: BLE001
        return True


_WIN = None


# ══════════════════════════════════════════════════════════════════════════ #
#  Смена страниц
# ══════════════════════════════════════════════════════════════════════════ #

def _ease_out(t: float) -> float:          # быстрый старт, мягкая остановка (как в iOS/Material)
    return 1 - (1 - t) ** 3


def _ease_out_quint(t: float) -> float:
    return 1 - (1 - t) ** 5


def _grab_area(stack: QWidget) -> QPixmap | None:
    """Снимок области стека вместе со всем, что под ним (фон окна) — непрозрачная картинка,
    поэтому старая и новая страница не просвечивают друг через друга."""
    try:
        budget=500_000 if getattr(_WIN,'settings',{}).get('motion_profile')=='eco' else 900_000
        scale=min(stack.devicePixelRatioF(),(budget/max(1,stack.width()*stack.height()))**.5)
        pm=QPixmap(max(1,round(stack.width()*scale)),max(1,round(stack.height()*scale)))
        pm.fill(QColor('#0b0d10'))
        painter=QPainter(pm);painter.scale(scale,scale)
        try:stack.render(painter)
        finally:painter.end()
        pm.setDevicePixelRatio(pm.width()/max(1,stack.width()))
        return pm
    except Exception:                                      # noqa: BLE001
        return None


class _Transition(QWidget):
    """Слой поверх стека на время перехода: рисует два снимка (старый и новый) — сами страницы в каждом
    кадре не перерисовываются, поэтому плавно даже на тяжёлых страницах.

      slide — новая страница наплывает поверх старой со сдвигом ~24 px в сторону перехода и проявляется;
      up    — новая выезжает снизу шторкой, старая под ней чуть темнеет («Текст», «Очередь»);
      down  — открытая шторка уезжает вниз (туда, куда показывает стрелка), под ней светлеет прежняя."""

    def __init__(self, stack, old_pm, new_pm, kind, direction):
        super().__init__(stack)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.old, self.new, self.kind, self.dir = old_pm, new_pm, kind, direction
        self.t = 0.0
        self.stack_key=id(stack)
        self.animation=None
        self.setGeometry(stack.rect())
        self.show()
        self.raise_()
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        # A snapshot must never conceal live feedback from a new gesture.
        kind = event.type()
        if kind in (QEvent.Type.Wheel, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress) or \
                (kind == QEvent.Type.MouseMove and event.buttons() != Qt.MouseButton.NoButton):
            self.finish()
        return False

    def set_t(self, v):
        self.t = float(v)
        self.update()

    @pyqtSlot()
    def finish(self):
        # QObject-bound slot is disconnected when its receiver is destroyed.
        # A closure capturing the stack/overlay could outlive Qt's parent teardown.
        QApplication.instance().removeEventFilter(self)
        if _OVERLAYS.get(self.stack_key) is self:
            _OVERLAYS.pop(self.stack_key,None)
        if self.animation is not None and not sip.isdeleted(self.animation):
            self.animation.stop()
            try:self.animation.finished.disconnect(self.finish)
            except (TypeError,RuntimeError):pass
        self.hide();self.old=self.new=QPixmap();self.deleteLater()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        W, H, t = self.width(), self.height(), self.t
        if self.kind == "up":
            e = _ease_out_quint(t)
            p.drawPixmap(0, 0, self.old)
            p.fillRect(self.rect(), QColor(0, 0, 0, int(110 * e)))
            y = int(round(H * (1 - e)))
            self._sheet(p, y, W)
            p.drawPixmap(0, y, self.new)
        elif self.kind == "down":
            e = _ease_out(t)
            p.drawPixmap(0, 0, self.new)
            p.fillRect(self.rect(), QColor(0, 0, 0, int(110 * (1 - e))))
            y = int(round(H * e))
            self._sheet(p, y, W)
            p.drawPixmap(0, y, self.old)
        else:
            e = _ease_out(t)
            import motion
            amount = motion.strength(_WIN)
            if amount > .7:
                # A small, bounded overshoot gives navigation weight.
                c = .9 * min(1.5, amount)
                e = 1 + (c + 1) * (t - 1)**3 + c * (t - 1)**2
            # без «двойной экспозиции»: старая страница быстро гаснет в тёмный фон, новая проявляется
            # почти сразу после — одна сквозь другую не просвечивает (раньше сквозь Echoes.NET была видна «Моя волна»)
            p.fillRect(self.rect(), QColor(13, 13, 15))
            fade_old = 1.0 - _ease_out(min(1.0, t / 0.22))
            if fade_old > 0.01:
                p.setOpacity(fade_old)
                p.drawPixmap(0, 0, self.old)
            a = max(0.0, min(1.0, (t - 0.08) / 0.5))
            p.setOpacity(_ease_out(a))
            if self.dir:
                p.drawPixmap(int(round((24 + 12 * amount) * self.dir * (1 - e))), 0, self.new)
            else:                                                   # без направления — лёгкое «приближение»
                k = 0.985 + 0.015 * e
                p.translate(W * (1 - k) / 2, H * (1 - k) / 2)
                p.scale(k, k)
                p.drawPixmap(0, 0, self.new)
        p.end()

    @staticmethod
    def _sheet(p, y, W):
        """Мягкая тень над краем шторки — видно, что страница лежит поверх."""
        if y <= 0:
            return
        g = QLinearGradient(0, y - 18, 0, y)
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 90))
        p.fillRect(QRect(0, y - 18, W, 18), g)


def _settle_layout(widget: QWidget):
    """Страницу показали впервые — Qt раскладывает её позже (отложенные LayoutRequest). Снимок до этого
    выходил с недоразложенными кнопками («общен», «Друзья» обрезаны), и после анимации всё перескакивало.
    Активируем раскладку самой страницы без обработки событий всего приложения."""
    widget.ensurePolished()
    layout=widget.layout()
    if layout is not None:layout.activate()
    inner=widget.widget() if isinstance(widget,QScrollArea) else None
    if inner is not None and inner.layout() is not None:inner.layout().activate()


def switch(stack: QStackedWidget, widget: QWidget, direction: int = 0, kind: str = "slide"):
    """Показать widget в stack с анимацией. direction: 1 — вперёд (новая приезжает справа), -1 — назад,
    0 — без сдвига. kind: "slide" | "up" (шторка снизу) | "down" (шторка уезжает вниз)."""
    old = stack.currentWidget()
    if stack.property("echoes_instant_navigation") or widget.property("echoes_instant_navigation") or \
            (old is not None and old.property("echoes_instant_navigation")):
        _finish(stack)
        stack.setCurrentWidget(widget)
        return
    if old is widget or old is None or not enabled() or not stack.isVisible() or stack.width() < 20:
        stack.setCurrentWidget(widget)
        return
    _finish(stack)                                       # прошлая анимация ещё идёт — завершить мгновенно
    if len(_OVERLAYS) >= 2:
        previous = next(iter(_OVERLAYS.values()))
        if not sip.isdeleted(previous):
            _finish(previous.parentWidget())
    old_pm = _grab_area(stack)
    stack.setCurrentWidget(widget)
    _settle_layout(widget)
    new_pm = _grab_area(stack) if old_pm is not None else None
    if old_pm is None or new_pm is None:
        return
    ov = _Transition(stack, old_pm, new_pm, kind, 1 if direction > 0 else (-1 if direction < 0 else 0))
    an = QVariantAnimation(ov)
    an.setDuration({"up": DUR_SHEET, "down": DUR_SHEET_OUT}.get(kind, DUR_PAGE))
    an.setStartValue(0.0)
    an.setEndValue(1.0)                                  # кривая — в paintEvent (своя для каждого вида)
    an.valueChanged.connect(ov.set_t)

    ov.animation=an
    an.finished.connect(ov.finish)
    _OVERLAYS[id(stack)] = ov
    an.start()


def switch_index(stack: QStackedWidget, index: int, direction: int = 0, kind: str = "slide"):
    w = stack.widget(index)
    if w is not None:
        switch(stack, w, direction, kind)


# слой, который сейчас анимируется, — по стеку (в Python, а не свойством Qt: после удаления объекта свойство
# указывало бы на освобождённую память → падение при быстром переключении вкладок)
_OVERLAYS = weakref.WeakValueDictionary()


def _finish(stack: QStackedWidget):
    ov = _OVERLAYS.pop(id(stack), None)
    if ov is not None and not sip.isdeleted(ov):
        ov.finish()


# ══════════════════════════════════════════════════════════════════════════ #
#  Окно: свернуть / развернуть / первое появление
# ══════════════════════════════════════════════════════════════════════════ #

class _WinAnim(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._busy = False
        self._was_min = False
        self._shown_once = False
        self._geo: QRect | None = None
        win.installEventFilter(self)

    @staticmethod
    def _docked(g: QRect, k: float = 0.86) -> QRect:
        """Окно «у панели задач»: уменьшено и опущено вниз — туда оно уходит и оттуда возвращается."""
        w2, h2 = int(g.width() * k), int(g.height() * k)
        return QRect(g.x() + (g.width() - w2) // 2, g.y() + int(g.height() * (1 - k)) + int(g.height() * 0.08), w2, h2)

    # «—» в заголовке: окно уменьшается и уходит вниз, к панели задач, растворяясь
    def minimize(self):
        w = self.win
        self._pre_min_state = w.windowState() & ~Qt.WindowState.WindowMinimized
        _cancel_state_animation(w)
        if self._busy or not enabled(w) or w.isMaximized() or w.isFullScreen():
            w.setWindowOpacity(1.0)
            self._geo = None                                   # развёрнутое — геометрию не трогаем
            w.showMinimized()
            return
        self._busy = True
        self._geo = QRect(w.geometry())
        g = self._geo
        grp = QParallelAnimationGroup(w)
        op = QPropertyAnimation(w, b"windowOpacity", w)
        op.setDuration(DUR_MIN)
        op.setStartValue(1.0)
        op.setEndValue(0.0)
        op.setEasingCurve(QEasingCurve.Type.InQuad)
        geo = QPropertyAnimation(w, b"geometry", w)
        geo.setDuration(DUR_MIN)
        geo.setStartValue(g)
        geo.setEndValue(self._docked(g))
        geo.setEasingCurve(QEasingCurve.Type.InCubic)
        grp.addAnimation(op)
        grp.addAnimation(geo)

        def done():
            w.showMinimized()
            # геометрию — сразу обратно (окно уже свёрнуто, скачка не видно); прозрачность вернёт restore
            if self._geo is not None:
                w.setGeometry(self._geo)
            self._busy = False
            grp.deleteLater()

        grp.finished.connect(done)
        grp.start()

    def _restore(self):
        """Из панели задач: окно поднимается снизу, вырастая до своего размера, и проявляется."""
        w = self.win
        _cancel_state_animation(w)
        previous = getattr(self, '_pre_min_state', None)
        self._pre_min_state = None
        if previous is not None and previous & Qt.WindowState.WindowFullScreen and not w.isFullScreen():
            w.showFullScreen()
        if w.isMaximized() or w.isFullScreen():
            # развёрнутое окно — без прозрачности: на весь экран Windows рисует «прозрачное» чёрным,
            # и если анимация не доиграла, окно так и оставалось чёрным
            self._solid()
            return
        if not enabled(w):
            self._fade_in()
            return
        g = QRect(w.geometry())
        w.setWindowOpacity(0.0)
        grp = QParallelAnimationGroup(w)
        op = QPropertyAnimation(w, b"windowOpacity", w)
        op.setDuration(DUR_RESTORE)
        op.setStartValue(0.0)
        op.setEndValue(1.0)
        op.setEasingCurve(QEasingCurve.Type.OutQuad)
        geo = QPropertyAnimation(w, b"geometry", w)
        geo.setDuration(DUR_RESTORE)
        geo.setStartValue(self._docked(g, 0.9))
        geo.setEndValue(g)
        geo.setEasingCurve(QEasingCurve.Type.OutCubic)
        grp.addAnimation(op)
        grp.addAnimation(geo)
        grp.finished.connect(lambda: (w.setGeometry(g), w.setWindowOpacity(1.0)))
        grp.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
        QTimer.singleShot(DUR_RESTORE + 250, self._solid)       # страховка: окно в любом случае видно

    def _solid(self):
        """Окно полностью видимо и перерисовано (после сворачивания/разворачивания)."""
        w = self.win
        try:
            w.setUpdatesEnabled(True)
            if w.windowOpacity() < 1.0:
                w.setWindowOpacity(1.0)
            w.update()
            for ch in w.findChildren(QWidget):
                if ch.isVisible() and ch.metaObject().className() in ("AmbientFrame", "GlassFrame"):
                    ch.update()
        except RuntimeError:
            pass

    def _fade_in(self, dur=DUR_RESTORE):
        w = self.win
        if not enabled(w) or w.isMaximized() or w.isFullScreen():
            self._solid()
            return
        w.setWindowOpacity(0.0)
        a = QPropertyAnimation(w, b"windowOpacity", w)
        a.setDuration(dur)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        QTimer.singleShot(dur + 250, self._solid)               # страховка: окно в любом случае видно

    def eventFilter(self, obj, ev):
        if obj is self.win:
            t = ev.type()
            if t == QEvent.Type.WindowStateChange:
                minimized = bool(self.win.windowState() & Qt.WindowState.WindowMinimized)
                if minimized:
                    if not self._was_min:
                        self._pre_min_state = ev.oldState() & ~Qt.WindowState.WindowMinimized
                    _cancel_state_animation(self.win)
                if self._was_min and not minimized:
                    QTimer.singleShot(0, self._restore)          # развернули из панели задач
                elif not minimized and self.win.windowOpacity() < 1.0 and not self._busy:
                    self.win.setWindowOpacity(1.0)
                self._was_min = minimized
            elif t == QEvent.Type.Show and not self._shown_once:
                self._shown_once = True
                QTimer.singleShot(0, lambda: self._fade_in(240))  # первое появление окна
        return False


def _cancel_state_animation(win):
    """A minimized window stops frame clocks; never leave its repaint disabled."""
    ghost = getattr(win, '_state_anim', None)
    if ghost is not None:
        cancel = getattr(ghost, 'cancel', None)
        if cancel is not None:
            cancel()
        else:
            timer = getattr(ghost, 'frame_timer', None)
            if timer is not None: timer.stop()
            ghost.hide(); ghost.deleteLater()
            win._state_anim = None
    preview = getattr(win, '_resize_preview', None)
    if preview is not None: preview.end()
    win.setUpdatesEnabled(True)
    win.setWindowOpacity(1.0)
    win.update()


def bring_front(win):
    """Показать окно поверх — в том же виде, в каком оно было (развёрнутым, на весь экран или обычным).
    showNormal() раньше всегда делал окно «обычным» — после клика по уведомлению оно открывалось маленьким."""
    st = win.windowState()
    if st & Qt.WindowState.WindowMinimized:
        win.setWindowState((st & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
    if not win.isVisible():
        win.show()
    win.raise_()
    win.activateWindow()


DUR_STATE = 300


class _Ghost(QWidget):
    """Неподвижный слой поверх области «было ∪ стало»: внутри него окно плавно меняет размер (рисуется,
    а не двигается как окно — без рывков), старый вид растворяется в новом, уже разложенном.
    Развернуть — под окном снимок рабочего стола (слой непрозрачный); восстановить — слой прозрачный,
    настоящее окно уже маленькое и всё время под рисунком."""

    def __init__(self, area: QRect, bg: QPixmap | None, old: QPixmap, r0: QRect, r1: QRect):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint |
                 Qt.WindowType.WindowTransparentForInput)
        super().__init__(None, flags)
        if bg is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, bg is not None)
        self.area, self.bg, self.old, self.new = area, bg, old, None
        self.r0, self.r1 = r0.translated(-area.topLeft()), r1.translated(-area.topLeft())
        self.t = 0.0
        self.setGeometry(area)

    def _rect(self):
        e = self.t
        a, b = self.r0, self.r1
        from PyQt6.QtCore import QRectF
        return QRectF(a.x() + (b.x() - a.x()) * e, a.y() + (b.y() - a.y()) * e,
                      a.width() + (b.width() - a.width()) * e, a.height() + (b.height() - a.height()) * e)

    def paintEvent(self, _):
        p = QPainter(self)
        if self.bg is not None:
            p.drawPixmap(self.rect(), self.bg)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self._rect()
        from PyQt6.QtCore import QRectF
        if self.new is None:
            p.drawPixmap(r, self.old, QRectF(self.old.rect()))
        else:                                                  # старое → новое, крест-накрест
            k = min(1.0, self.t * 1.6)
            p.drawPixmap(r, self.new, QRectF(self.new.rect()))
            if k < 1.0:
                p.setOpacity(1.0 - k)
                p.drawPixmap(r, self.old, QRectF(self.old.rect()))
        p.end()


def animate_state(win, target: str):
    """Развернуть / восстановить / на весь экран — плавно (target: "max" | "normal" | "full")."""
    def apply():
        if target == "max":
            win.showMaximized()
        elif target == "full":
            win.showFullScreen()
        else:
            win.showNormal()
    if not enabled(win) or not win.isVisible() or win.isMinimized() or getattr(win, "_state_anim", None):
        apply()
        return
    scr = win.screen() or QApplication.primaryScreen()
    g0 = QRect(win.geometry())
    if target == "max":
        g1 = scr.availableGeometry()
    elif target == "full":
        g1 = scr.geometry()
    else:
        g1 = QRect(win.normalGeometry())
        if not g1.isValid() or g1.width() < 200:
            a = scr.availableGeometry()
            g1 = QRect(a.x() + a.width() // 8, a.y() + a.height() // 8, a.width() * 3 // 4, a.height() * 3 // 4)
    if g0 == g1:
        apply()
        return
    growing = g1.width() * g1.height() > g0.width() * g0.height()
    area = g0.united(g1)
    try:
        old = win.grab()
        bg = None
        if growing:                                            # под окном — рабочий стол как есть
            sg = scr.geometry()
            bg = scr.grabWindow(0, area.x() - sg.x(), area.y() - sg.y(), area.width(), area.height())
            if bg.isNull():
                bg = None
    except Exception:                                          # noqa: BLE001
        apply()
        return
    if growing and bg is None:
        apply()
        return
    ghost = _Ghost(area, bg, old, g0, g1)
    win._state_anim = ghost
    ghost.show()
    ghost.raise_()
    QApplication.processEvents()
    apply()                                                    # окно меняется под слоем

    def finish():
        try:
            if hasattr(ghost, "frame_timer"):
                ghost.frame_timer.stop()
            ghost.hide()
            ghost.deleteLater()
        except RuntimeError:
            pass
        if getattr(win, '_state_anim', None) is ghost:
            win._state_anim = None
            win.setUpdatesEnabled(True)
            win.update()

    def start():
        if getattr(win, '_state_anim', None) is not ghost:
            return
        try:
            ghost.new = win.grab()                             # новый вид — уже в новом размере
        except Exception:                                      # noqa: BLE001
            ghost.new = None
        from frameclock import FrameTimer
        ghost.frame_timer = FrameTimer(ghost)
        began = time.perf_counter()
        def frame():
            progress = min(1.0, (time.perf_counter() - began) * 1000 / DUR_STATE)
            ghost.t = 1 - (1 - progress) ** 5
            ghost.update()
            if progress >= 1:
                finish()
        ghost.frame_timer.timeout.connect(frame)
        win.setUpdatesEnabled(False)
        ghost.frame_timer.start(8)
    ghost.cancel = finish
    QTimer.singleShot(0, start)
    QTimer.singleShot(DUR_STATE + 1200, lambda: getattr(win, "_state_anim", None) is ghost and finish())


# ══════════════════════════════════════════════════════════════════════════ #
#  Подключение
# ══════════════════════════════════════════════════════════════════════════ #

def install_effects(win):
    """Системные эффекты Qt для меню/подсказок выключены: на Windows они рисуются отдельными временными
    окнами, которые мелькают при наведении и кликах. Вместо них — своё проявление меню (_PopupFade)."""
    try:
        app = QApplication.instance()
        for eff in (Qt.UIEffect.UI_AnimateMenu, Qt.UIEffect.UI_FadeMenu, Qt.UIEffect.UI_AnimateCombo,
                    Qt.UIEffect.UI_AnimateTooltip, Qt.UIEffect.UI_FadeTooltip, Qt.UIEffect.UI_AnimateToolBox):
            app.setEffectEnabled(eff, False)
        _install_menu_fade()
    except Exception as e:                               # noqa: BLE001
        print("[ui_anim] effects:", e)


_MENU_FADE = False


def _install_menu_fade():
    """Меню проявляются за 120 мс — перехват QMenu.exec/popup (без фильтра на всё приложение)."""
    global _MENU_FADE
    if _MENU_FADE:
        return
    _MENU_FADE = True
    from PyQt6.QtWidgets import QMenu
    orig_exec, orig_popup = QMenu.exec, QMenu.popup

    def fade(m):
        if not enabled():
            return
        try:
            m.setWindowOpacity(0.0)
            a = QPropertyAnimation(m, b"windowOpacity", m)
            a.setDuration(120)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            QTimer.singleShot(0, lambda: a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped))
        except RuntimeError:
            pass

    def exec_(self, *a, **k):
        fade(self)
        return orig_exec(self, *a, **k)

    def popup(self, *a, **k):
        fade(self)
        return orig_popup(self, *a, **k)
    QMenu.exec = exec_
    QMenu.popup = popup


def morph(target: QWidget, fn, direction: int = 0, kind: str = "slide"):
    """Поменять содержимое target (fn()) с переходом: снимок «до» и «после» плывут слоем поверх,
    сама страница в кадрах не перерисовывается — плавно и без фризов. direction: 1/-1/0 как у switch."""
    if not enabled() or not target.isVisible() or target.width() < 20 or target.height() < 20:
        fn()
        return
    _finish(target)
    old_pm = _grab_area(target)
    fn()
    try:
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
    except Exception:                                    # noqa: BLE001
        pass
    new_pm = _grab_area(target) if old_pm is not None else None
    if old_pm is None or new_pm is None or sip.isdeleted(target):
        return
    ov = _Transition(target, old_pm, new_pm, kind, 1 if direction > 0 else (-1 if direction < 0 else 0))
    an = QVariantAnimation(ov)
    an.setDuration(DUR_PAGE)
    an.setStartValue(0.0)
    an.setEndValue(1.0)
    an.valueChanged.connect(ov.set_t)
    ov.animation = an
    an.finished.connect(ov.finish)
    _OVERLAYS[id(target)] = ov
    an.start()


class _ResizeSurface(QWidget):
    def __init__(self, parent, snapshot):
        super().__init__(parent)
        self.snapshot = snapshot
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(12, 13, 16))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self.snapshot)


class _ResizePreview(QObject):
    """Keep manual border resizing on a single cached surface until release."""
    def __init__(self, win):
        super().__init__(win)
        self.win, self.surface = win, None
        win.installEventFilter(self)

    def begin(self):
        if self.surface is not None or not enabled(self.win) or getattr(self.win, '_state_anim', None):
            return
        central = self.win.centralWidget()
        if central is None: return
        self.surface = _ResizeSurface(central, central.grab())
        central.installEventFilter(self)
        self.surface.show(); self.surface.raise_()

    def end(self):
        surface, self.surface = self.surface, None
        if surface is None: return
        surface.parentWidget().removeEventFilter(self)
        surface.hide(); surface.deleteLater()
        self.win.update()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and self.surface is not None:
            self.surface.setGeometry(self.surface.parentWidget().rect())
        elif obj is self.win and event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self.end()
        return False


def _install_resize_preview(win):
    import sys
    if sys.platform != 'win32' or getattr(win, '_resize_preview', None) is not None: return
    from PyQt6.QtCore import QAbstractNativeEventFilter
    from ctypes.wintypes import MSG
    preview = win._resize_preview = _ResizePreview(win)
    class NativeResize(QAbstractNativeEventFilter):
        def nativeEventFilter(self, kind, pointer):
            if kind not in ('windows_generic_MSG', 'windows_dispatcher_MSG'): return False, 0
            try:
                message = MSG.from_address(int(pointer))
                if message.message in (0x0231, 0x0232) and message.hwnd == int(win.winId()):
                    preview.begin() if message.message == 0x0231 else preview.end()
            except (RuntimeError, ValueError):
                pass
            return False, 0
    native = win._resize_native_filter = NativeResize()
    app = QApplication.instance()
    app.installNativeEventFilter(native)
    def cleanup():
        if not sip.isdeleted(app):
            app.removeNativeEventFilter(native)
    win.destroyed.connect(cleanup)


def install(win):
    global _WIN
    _WIN = win
    install_effects(win)
    wa = _WinAnim(win)
    win._ui_anim = wa
    win.animated_minimize = wa.minimize
    win.animate_switch = switch
    win.animate_switch_index = switch_index
    win.animate_morph = morph
    _install_resize_preview(win)
