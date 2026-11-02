# gpu_scroll.py
"""
Плавная прокрутка колесом на видеокарте (слой scroll_gl.cpp из echoes_ui.dll).

Раньше каждый кадр доводки колеса перерисовывал всю область прокрутки слоями (окно, оболочка,
стекло панели, строки/карточки) и отправлял в видеокарту картинку размером с окно — на экране
144–240 Гц кадров не хватало, прокрутка шла рывками.

Теперь на время доводки:
  * первые кадры жеста идут растром, как раньше, а тем временем (не дольше PREP_BUDGET_S за кадр)
    готовятся видимые полосы; когда готовы — дальше рисует видеокарта (полосы — в координатах
    содержимого, подмена без скачка). Раньше их рисовали сразу, и первый кадр жеста ждал 50–70 мс;
  * фон под содержимым (стекло панели — так же, как его рисует AmbientFrame) — одной текстурой
    (запоминается на панели, пока не сменится стекло или место области);
    в скруглённых углах панели слой прозрачен, там виден растр окна;
  * содержимое — полосами по TILE пикселей: видимые — сразу, следующие по ходу — заранее, понемногу
    в каждом кадре (не дольше BUDGET_S), чтобы новая полоса не давала рывка;
  * кадр — только сдвиг полос в шейдере; полоса прокрутки двигается, а сама страница стоит
    (сигналы полосы придержаны) — растровое окно в кадре почти не перерисовывается. У списка треков
    (строки рисуем сами) перерисовка области на это время ещё и выключена: обложки, которые
    догружаются, не перерисовывают её под слоем. Страницу так не выключить — QWidget.render
    не рисует виджеты с выключенной перерисовкой.
Догрузилась обложка под слоем (_CoverLoader) — полосы с ней перерисовываются (видимые — не чаще
REFRESH_S, остальные — впрок, в рамках BUDGET_S).
Когда прокрутка доехала (или её перехватили: перетащили ползунок, сменили страницу, изменили
размер), страница один раз встаёт в итоговое место, слой прячется.

Только там, где окно уже на GPU-совмещении (ya_wave.gpu_anchor), область лежит на стеклянной
панели (AmbientFrame) и поверх неё ничего нет; иначе — прежняя растровая прокрутка.
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QImage, QPainter, QPainterPath, QRegion
from PyQt6.QtWidgets import QScrollArea, QStyle, QStyleOptionViewItem, QWidget

TILE = 256                                                     # высота полосы содержимого, лог. пиксели
AHEAD = 3                                                      # столько полос готовить впрок по ходу прокрутки
BUDGET_S = 0.003                                               # на заготовку впрок — не дольше за кадр
PREP_BUDGET_S = 0.004                                          # на видимые полосы, пока кадры идут растром
KEEP_SCREENS = 3                                               # полосы дальше стольких экранов — освобождаются
REFRESH_S = 0.05                                               # видимые полосы с новыми обложками — не чаще

ACTIVE = None                                                  # Session, пока идёт прокрутка на GPU

_R = QWidget.RenderFlag


def enabled(win) -> bool:
    if os.environ.get("ECHOES_NO_GPU_SCROLL") == "1":
        return False
    anchor = getattr(win, "_gpu_anchor", None)
    try:
        return anchor is not None and anchor.ok()
    except RuntimeError:
        return False


def _live_gl(content) -> bool:
    """На странице живой GL-холст (арена дуэли: свойство echoes_live, сглаживание MSAA) — его снимок через
    QWidget.render выходит мусором, да и он всё время анимирован — такую страницу листаем растром.
    (format().samples() не годится: после показа это формат контекста, а MSAA — у буфера кадра.)"""
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget
    for gl in content.findChildren(QOpenGLWidget):
        try:
            if gl.isVisible() and gl.property("echoes_live"):
                return True
        except RuntimeError:
            pass
    return False


def _kind(area):
    if isinstance(area, QScrollArea):
        return "area" if area.widget() is not None and not _live_gl(area.widget()) else None
    from yamusic_theme import TrackDelegate, TrackListView
    if isinstance(area, TrackListView) and not area._auto_height and isinstance(area.itemDelegate(), TrackDelegate):
        return "list"
    return None


def _panel(area):
    """Стеклянная панель (AmbientFrame), на которой лежит область, — её стекло и есть фон под содержимым."""
    w = area.parentWidget()
    while w is not None and not w.isWindow():
        if type(w).__name__ == "AmbientFrame":
            return w if getattr(w, "_surface", None) is not None else None
        w = w.parentWidget()
    return None


def _covered(area, vr: QRect) -> bool:
    """Лежит ли что-то видимое поверх области (выше по z-порядку) — тогда слой его закрыл бы."""
    win = area.window()
    w = area
    while w is not None and w is not win:
        par = w.parentWidget()
        if par is None:
            break
        above = False
        for c in par.children():
            if c is w:
                above = True
                continue
            if above and isinstance(c, QWidget) and c.isVisible() and not c.isWindow():
                if QRect(c.mapTo(win, QPoint(0, 0)), c.size()).intersects(vr):
                    return True
        w = par
    return False


def _layer(win):
    """Один GL-слой на окно (поверх всего; на время прокрутки ставится на место её области)."""
    lay = win.__dict__.get("_gpu_scroll")
    if lay is None:
        import native_ui
        lay = native_ui.ScrollGL()
        lay.widget.setParent(win)
        lay.widget.hide()
        win._gpu_scroll = lay
    return lay


def _image(w, h, dpr):
    img = QImage(max(1, int(round(w * dpr))), max(1, int(round(h * dpr))), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    return img


def warm(win):
    """Один раз в простое после запуска (warmup.start), чтобы первый переход и первая прокрутка были без рывка:
      * первый QWidget.render отдаёт всем скрытым виджетам окна (собранные заранее страницы)
        отложенные Move/Resize — ~0.2–0.8 с; раньше это доставалось первому клику по разделу;
      * GL-слою нужен контекст и шейдеры (~0.1 с) — показываем его на кадр точкой 1×1 (прозрачной)."""
    if win.__dict__.get("_gpu_scroll_warm"):
        return
    win._gpu_scroll_warm = True
    img = _image(1, 1, 1.0)
    p = QPainter(img)
    win.render(p, QPoint(0, 0), QRegion(QRect(0, 0, 1, 1)), _R.DrawChildren)
    p.end()
    if not enabled(win):
        return
    lay = _layer(win)
    lay.widget.setGeometry(0, 0, 1, 1)
    lay.widget.show()

    def done():
        try:
            if not win.__dict__.get("_gpu_scroll_busy"):
                lay.widget.hide()
        except RuntimeError:
            pass
    QTimer.singleShot(400, done)


class Session:
    def __init__(self, area, bar, kind, layer, panel):
        self.area, self.bar, self.kind, self.layer, self.panel = area, bar, kind, layer, panel
        self.win = area.window()
        vp = area.viewport()
        self.vp = vp
        self.size = vp.size()
        self.dpr = vp.devicePixelRatioF()
        self.tiles: set = set()                                # готовые (или пустые) полосы
        self.stale: set = set()                                # готовые, но под ними догрузились обложки
        self.refreshed = 0.0
        self.job = None                                        # полоса, которая рисуется по частям
        self.live = False                                      # кадры уже рисует видеокарта
        self.last = float(bar.value())
        self.sections = []
        if kind == "area":
            from track_section import TrackSection
            self.content = area.widget()
            self.sections = [s for s in self.content.findChildren(TrackSection) if s.isVisible()]
            self.extent = self.content.height()
        else:
            self.content = vp
            self.extent = bar.maximum() + vp.height()
            idx0 = area.indexAt(QPoint(1, 1))
            self.row_h = max(1, area.visualRect(idx0).height()) if idx0.isValid() else 56
        self.layer.backdrop(self._backdrop())
        self.layer.widget.setGeometry(QRect(vp.mapTo(self.win, QPoint(0, 0)), self.size))
        self.layer.frame(self.last, TILE)
        self.win._gpu_scroll_busy = True
        global ACTIVE
        ACTIVE = self

    def _go_live(self):
        """Видимые полосы готовы — кадры дальше рисует видеокарта, страница стоит."""
        self.live = True
        self.layer.widget.show()
        self.layer.widget.raise_()
        if self.kind == "list":
            self.vp.setUpdatesEnabled(False)                   # под слоем список не перерисовывается

    # ── что под содержимым: стекло панели, как в AmbientFrame.paintEvent (без пересборки стекла) ──
    def _backdrop(self):
        import ya_decor
        from ya_skin import interface_radius
        fr = self.panel
        o = self.vp.mapTo(fr, QPoint(0, 0))
        stretch = bool(getattr(fr, "_glass_stretch", False))
        if ya_decor.CURRENT is not None and not stretch:
            glass = ya_decor.decorate_surface(fr, fr._surface).cacheKey()
        else:
            glass = fr._surface.cacheKey()
        key = (glass, ya_decor.CURRENT is None, stretch, o.x(), o.y(), self.size.width(), self.size.height(),
               self.dpr, interface_radius())
        hit = fr.__dict__.get("_gs_back")
        if hit is not None and hit[0] == key and o.y() > 3:   # у верхнего края — кант за мышью, не запоминаем
            return hit[1]
        img = _image(self.size.width(), self.size.height(), self.dpr)
        p = QPainter(img)
        p.translate(-o.x(), -o.y())
        clip = QPainterPath()
        r = interface_radius()
        clip.addRoundedRect(QRectF(fr.rect()), r, r)
        p.setClipPath(clip)
        if ya_decor.CURRENT is not None:
            ya_decor.paint_frame(fr, p, fr._surface)
        else:
            p.drawPixmap(0, 0, fr._surface)
        p.end()
        fr._gs_back = (key, img)
        return img

    # ── кусок содержимого [y0, y0+h) ──
    def _band(self, y0, h):
        img = _image(self.size.width(), h, self.dpr)
        p = QPainter(img)
        if self.kind == "area":
            moved = self._cover_sections(y0, y0 + h)
            x0 = self.area.horizontalScrollBar().value()
            self.content.render(p, QPoint(0, 0), QRegion(QRect(x0, y0, self.size.width(), h)), _R.DrawChildren)
            if not self.live:                                  # страница ещё на экране — списки обратно
                for sec in moved:
                    sec._sync()
        else:
            for r in self._rows(y0, h):
                self._paint_row(p, r, y0)
        p.end()
        return img

    def _rows(self, y0, h):
        rh = self.row_h
        return range(max(0, y0 // rh - 1), min(self.area.model().rowCount(), (y0 + h) // rh + 2))

    def _paint_row(self, p, r, y0):
        v = self.area
        idx = v.model().index(r, 0)
        rect = v.visualRect(idx)
        rect.translate(0, self.bar.value() - y0)
        if rect.bottom() < 0 or rect.top() >= int(round(p.device().height() / self.dpr)):
            return
        opt = QStyleOptionViewItem()
        v.initViewItemOption(opt)
        opt.rect = rect
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        sel = v.selectionModel()
        if sel is not None and sel.isSelected(idx):
            opt.state |= QStyle.StateFlag.State_Selected
        v.itemDelegate().paint(p, opt, idx)

    def _cover_sections(self, y0, y1):
        """Списки «высотой в страницу» держат строки только у видимой части — на время снимка полосы
        растягиваем окно списка на неё (после прокрутки TrackSection._sync вернёт как было)."""
        moved = []
        for s in self.sections:
            try:
                top = s.mapTo(self.content, QPoint(0, 0)).y()
            except RuntimeError:
                continue
            a, b = max(0, y0 - top), min(s.height(), y1 - top)
            if a >= b:
                continue
            rh = s.row_height
            start = (a // rh) * rh
            end = min(s.height(), -(-b // rh) * rh)
            v = s.view
            if v.isVisible() and v.y() <= a and v.y() + v.height() >= b:
                continue
            s._syncing = True
            try:
                v.setGeometry(0, start, s.width(), max(rh, end - start))
                v.show()
                v.verticalScrollBar().setValue(start)
            finally:
                s._syncing = False
            moved.append(s)
        return moved

    def _need(self, pos, direction=0.0):
        vh = self.size.height()
        first, last = int(pos // TILE), int((pos + vh - 1) // TILE)
        miss = [i for i in range(max(0, first), last + 1) if i not in self.tiles]
        if miss:                                               # видимое — сразу, одним снимком
            if self.job is not None and self.job[0] in miss:
                self.job[2].end()
                self.job = None
            self._put(miss)
        if direction:
            self._ahead(first, last, direction)
        far = KEEP_SCREENS * vh
        for i in [i for i in self.tiles if i * TILE + TILE < pos - far or i * TILE > pos + vh + far]:
            self.tiles.discard(i)
            self.layer.drop(i)

    def _ahead(self, first, last, direction):
        """Следующие по ходу полосы — заранее и понемногу, пока не вышло время кадра."""
        t_end = time.perf_counter() + BUDGET_S
        order = range(last + 1, last + 1 + AHEAD) if direction > 0 else range(first - 1, first - 1 - AHEAD, -1)
        self._build_some(order, t_end)

    def _build_some(self, order, t_end) -> bool:
        """Полосы order по порядку, пока есть время; True — все готовы."""
        order = [i for i in order if 0 <= i and i * TILE < self.extent]
        for i in order:
            if i in self.tiles:
                continue
            if not self._build(i, t_end) or time.perf_counter() >= t_end:
                break
        return all(i in self.tiles for i in order)

    def _build(self, i, t_end) -> bool:
        """Полоса i: страница — снимком целиком (~2 мс), список — по строке, пока есть время. True — готова."""
        if self.kind == "area":
            self._put([i])
            return True
        if self.job is None or self.job[0] != i:
            if self.job is not None:
                self.job[2].end()
            y0 = i * TILE
            h = min(TILE, self.extent - y0)
            img = _image(self.size.width(), h, self.dpr)
            self.job = [i, img, QPainter(img), iter(self._rows(y0, h)), y0]
        i_, img, p, rows, y0 = self.job
        for r in rows:
            self._paint_row(p, r, y0)
            if time.perf_counter() >= t_end:
                return False
        p.end()
        self.job = None
        self.tiles.add(i)
        self.layer.tile(i, img)
        return True

    def _prepare(self, pos, direction) -> bool:
        """Пока кадры идут растром: видимые полосы (и одна по ходу) — понемногу. True — можно на GPU."""
        vh = self.size.height()
        first, last = int(pos // TILE), int((pos + vh - 1) // TILE)
        order = list(range(max(0, first), last + 1))
        if direction:
            order.append(last + 1 if direction > 0 else first - 1)
        return self._build_some(order, time.perf_counter() + PREP_BUDGET_S)

    def _put(self, idx):
        """Недостающие полосы: каждый непрерывный кусок — одним снимком (у QWidget.render свой обход
        всего окна, ~1–2 мс на вызов), потом режется на полосы."""
        idx = sorted(idx)
        while idx:
            run = [idx.pop(0)]
            while idx and idx[0] == run[-1] + 1:
                run.append(idx.pop(0))
            y0 = run[0] * TILE
            h = min((run[-1] + 1) * TILE, self.extent) - y0
            self.tiles.update(run)
            if h <= 0:
                continue
            img = self._band(y0, h)
            for i in run:
                ty = int(round((i * TILE - y0) * self.dpr))
                th = min(int(round(TILE * self.dpr)), img.height() - ty)
                if th > 0:
                    self.layer.tile(i, img.copy(0, ty, img.width(), th))

    def touch(self, w):
        """w перерисовался бы (догрузилась обложка): какие полосы под ним — перерисовать."""
        try:
            if self.kind == "list":
                if w is self.vp:
                    self.stale |= self.tiles
                return
            strip = getattr(getattr(w, "row", None), "strip", None)
            if strip is not None and strip.widget is self.area:    # сетка альбомов: сама область — лента карточек
                _, top = strip.card_pos(w.index)
                self.stale |= {i for i in self.tiles if i * TILE < top + w.size_ + 60 and (i + 1) * TILE > top}
                return
            qw = w if isinstance(w, QWidget) else getattr(strip, "widget", None)
            if qw is None or not self.content.isAncestorOf(qw):
                return
            for s in self.sections:                            # строки списка-страницы — вся его высота
                if s.isAncestorOf(qw):
                    qw = s
                    break
            top = qw.mapTo(self.content, QPoint(0, 0)).y()
            self.stale |= {i for i in self.tiles if i * TILE < top + qw.height() and (i + 1) * TILE > top}
        except RuntimeError:
            pass

    def _refresh(self, pos):
        now = time.perf_counter()
        if now - self.refreshed < REFRESH_S:
            return
        self.refreshed = now
        self.tiles -= self.stale                               # старые текстуры видны, пока не заменены
        self.stale.clear()

    def alive(self) -> bool:
        try:
            return self.area.isVisible() and self.vp.size() == self.size and not self.win.isMinimized()
        except RuntimeError:
            return False

    def frame(self, pos: float):
        d = pos - self.last
        self.last = pos
        self.layer.frame(pos, TILE)
        if self.stale:
            self._refresh(pos)
        if not self.live:
            if self._prepare(pos, d):
                self._go_live()
            return
        self._need(pos, d)

    def end(self):
        """Страница встаёт в итоговое место (сигнал полосы, который придерживали), слой прячется."""
        if self.__dict__.get("done"):
            return
        self.done = True
        global ACTIVE
        if ACTIVE is self:
            ACTIVE = None
        if self.job is not None:
            self.job[2].end()
            self.job = None
        try:
            if not self.vp.updatesEnabled():
                self.vp.setUpdatesEnabled(True)
        except RuntimeError:
            pass
        if self.live:
            try:
                bar = self.bar
                bar.valueChanged.emit(bar.value())
            except RuntimeError:
                pass
        try:
            self.win._gpu_scroll_busy = False
            self.layer.widget.hide()
            self.layer.release()
        except RuntimeError:
            pass


def begin(area, bar):
    """Session для вертикальной доводки колесом или None — тогда прокручивать как раньше."""
    try:
        win = area.window()
        if bar is not area.verticalScrollBar() or not enabled(win) or win.__dict__.get("_gpu_scroll_busy"):
            return None
        kind = _kind(area)
        if kind is None:
            return None
        vp = area.viewport()
        if vp.width() < 2 or vp.height() < 2:
            return None
        vr = QRect(vp.mapTo(win, QPoint(0, 0)), vp.size())
        panel = _panel(area)
        if panel is None or _covered(area, vr):
            return None
        return Session(area, bar, kind, _layer(win), panel)
    except Exception as e:                                 # noqa: BLE001
        print("[gpu_scroll]", e)
        return None
