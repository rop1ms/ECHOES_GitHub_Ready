# osu_theme.py
"""
Тема «esu!» — весь плеер в виде osu!:

  * главное меню: «печенька» esu!, пульсирующая в такт, визуализатор вокруг неё, треугольники,
    выезжающие кнопки (Играть → Соло / Карты osu! / Назад, Редактор, Настройки, Выход),
    панель игрока (pp, точность, уровень), «сейчас играет» и громкость колесом;
  * выбор песни: карусель библиотеки и наборов карт из настоящего osu! (коллекции — плейлисты),
    сложности Easy…Expert от генератора (osu_mapgen) или сложности маппера (osu_beatmap), звёзды,
    информация карты, локальные рекорды с повторами, моды (F1), случайный трек (F2), F5 — обновить
    карты из osu!, поиск набором текста, предпросмотр с припева; .osz можно бросить в окно;
  * игра (osu_game.OsuGame) — фон: обложка, фон/видео карты или клип песни;
  * результаты: оценка SS…D, счёт, попадания, pp, UR, график HP, повтор, подгонка смещения;
  * настройки: чувствительность, raw input, клавиши, затемнение, фон, эффекты, звук, скин osu!,
    карты из osu!, параметры генератора карт, экспорт в .osz, настройки плеера.

Всё рисуется кодом; по желанию — скин из файлов настоящего osu! (.osk / папка скина, osu_skin_import).
Тяжёлое (анализ трека, разбор карт osu!) — в отдельном процессе (bgproc), размытие фонов — в рабочем
потоке (blur_fx).
"""
from __future__ import annotations

import bisect
import gzip
import json
import math
import os
import random
import re
import time
from pathlib import Path

import threading

import numpy as np
from img_load import load_pixmap, read_image
from PyQt6.QtCore import QEvent, QLineF, QObject, QPointF, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                         QPen, QPixmap, QPolygonF, QRadialGradient)
from PyQt6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QFileDialog,
                             QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSlider, QVBoxLayout,
                             QWidget)

import osu_beatmap as OB
import osu_mapgen as G
import osu_sfx
import osu_skin as SK
import search_util as SU
from frameclock import FrameTimer, animation_delta
from osu_game import paint_redirected
from osu_game import (KEY_BINDS, KEY_DEFAULTS, MOD_NAMES, OSU_DEFAULTS, Digits, OsuGame, _font, bind_of, blit,
                      key_from_event, key_is, key_label, mods_mult)

THEME_NAME = "esu!"
PINK = QColor(255, 102, 170)
SCORES = G.OSU_DIR / "scores.json"
REPLAYS = G.OSU_DIR / "replays"
MOD_ROWS = [("Понижение сложности", ["EZ", "NF", "HT"]),
            ("Повышение сложности", ["HR", "SD", "PF", "DT", "NC", "HD", "FL"]),
            ("Особые", ["RX", "AP", "AU", "CN", "SO"])]
MOD_EXCL = [{"EZ", "HR"}, {"HT", "DT", "NC"}, {"SD", "PF", "NF"}, {"RX", "AU", "AP", "CN"}, {"SO", "AU", "CN"},
            {"NF", "AU", "CN"}, {"SD", "PF", "AU", "CN"}]
TIPS = ["Ctrl+Enter в выборе песни — смотреть, как карту проходит Auto",
        "F1 — моды, F2 — случайная песня, просто печатайте для поиска",
        "Плюс и минус во время игры двигают смещение этой карты на 5 мс",
        "~ (тильда) — мгновенный перезапуск карты",
        "Скачайте клип песни (Ctrl+Shift+K) — и он станет фоном карты",
        "Карты строит модель, выученная на ranked-картах osu!: ритм, слайдеры и прыжки — как у мапперов",
        "Скин osu! (.osk) можно просто перетащить в окно — ноты, курсор и звуки станут как в нём",
        "Колесо мыши в меню — громкость",
        "Карты из osu! подтягиваются сами из папки Songs; F5 в выборе песни — обновить",
        "Файл .osz можно просто перетащить в окно — карты появятся в выборе песни",
        "В настройках можно включить raw input и подобрать чувствительность"]


def osu_theme_dict() -> dict:
    return {"bg": "#0e0a10", "panel": "#1a1420", "panel2": "#231a2b", "glass": "rgba(255,255,255,0.05)",
            "glass2": "rgba(255,255,255,0.09)", "text": "#ffffff", "muted": "#a99fb3", "accent": "#ff66aa",
            "accent2": "#ffb3d4", "border": "rgba(255,255,255,0.10)", "border2": "rgba(255,255,255,0.18)",
            "danger": "#ff5c5c", "glow": "#ff66aa", "osu": True}


# ------------------------------------------------------------------ #
#  Вспомогательное                                                    #
# ------------------------------------------------------------------ #

_STAR_PTS = [(0.1, (66, 144, 251)), (1.25, (79, 192, 255)), (2.0, (79, 255, 213)), (2.5, (124, 255, 79)),
             (3.3, (246, 240, 92)), (4.2, (255, 128, 104)), (4.9, (255, 78, 111)), (5.8, (198, 69, 184)),
             (6.7, (101, 99, 222)), (7.7, (24, 21, 142)), (9.0, (0, 0, 0))]


def star_color(s: float) -> QColor:
    if s <= _STAR_PTS[0][0]:
        return QColor(*_STAR_PTS[0][1])
    for (a, ca), (b, cb) in zip(_STAR_PTS[:-1], _STAR_PTS[1:]):
        if s <= b:
            k = (s - a) / (b - a)
            return QColor(*(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3)))
    return QColor(0, 0, 0)


def fmt_time(ms: float) -> str:
    s = max(0, int(ms / 1000))
    return f"{s // 60}:{s % 60:02d}"


def track_title(t) -> str:
    return (t or {}).get("title") or Path(str((t or {}).get("path", ""))).stem or "—"


def n_tracks(n: int) -> str:
    w = "трек" if n % 10 == 1 and n % 100 != 11 else \
        "трека" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "треков"
    return f"{n} {w}"


def load_scores() -> dict:
    try:
        return json.loads(SCORES.read_text("utf-8"))
    except Exception:                                                 # noqa: BLE001
        return {}


def map_key(m: dict) -> str:
    if m.get("osu_md5"):                               # карта из osu! — как в самой osu!, по MD5 файла
        return f"osu:{m['osu_md5']}|{m['osu_md5']}|osu"
    return f"{G._key(m.get('path', ''))}|{m.get('diff')}|{G._opts_sig(m.get('opts'))}"


def is_osu_track(t) -> bool:
    return bool((t or {}).get("esu_set"))


def diff_name(m: dict | None, key: str) -> str:
    """Название сложности: у карт osu! — как у маппера, у сгенерированных — Easy…Expert."""
    if m and m.get("osu"):
        return m.get("name") or "?"
    return G.diff_info(key)["name"]


def save_score(m: dict, res: dict, player: str) -> int:
    """Сохранить результат (топ-10 карты). Возвращает место (1…) или 0."""
    if res.get("watched") or res.get("failed") or "AU" in res.get("mods", []):
        return 0
    d = load_scores()
    key = map_key(m)
    lst = d.get(key, [])
    rid = f"{int(time.time() * 1000)}"
    e = {k: res[k] for k in ("score", "acc", "combo", "c300", "c100", "c50", "c0", "grade", "mods", "pp", "ur",
                             "time")}
    e["player"] = player
    e["id"] = rid
    lst.append(e)
    lst.sort(key=lambda x: -x["score"])
    rank = next((i + 1 for i, x in enumerate(lst) if x is e), 0)
    drop = lst[10:]
    lst = lst[:10]
    d[key] = lst
    G.OSU_DIR.mkdir(parents=True, exist_ok=True)
    SCORES.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    try:
        REPLAYS.mkdir(parents=True, exist_ok=True)
        if rank and rank <= 10 and res.get("replay"):
            rp = res["replay"]
            # все кадры: с прореженными повтор при просмотре расходился со счётом (слайдеры рвались)
            rp = {"frames": rp["frames"], "presses": rp["presses"], "mods": rp.get("mods", [])}
            with gzip.open(REPLAYS / f"{rid}.json.gz", "wt", encoding="utf-8") as f:
                json.dump(rp, f)
        for x in drop:
            (REPLAYS / f"{x['id']}.json.gz").unlink(missing_ok=True)
    except Exception as ex:                                           # noqa: BLE001
        print("[osu] replay save:", ex)
    return rank if rank <= 10 else 0


# ── попытки: сколько раз начинали каждую карту и с какой попытки прошли ── #
ATTEMPTS = G.OSU_DIR / "attempts.json"


def load_attempts() -> dict:
    try:
        return json.loads(ATTEMPTS.read_text("utf-8"))
    except Exception:                                                 # noqa: BLE001
        pass
    # учёта ещё не было: прохождения из сохранённых рекордов (провалы и выходы раньше не писались)
    d = {}
    for key, lst in load_scores().items():
        if lst:
            d[key] = {"plays": len(lst), "passes": len(lst), "fails": 0, "quits": 0, "first_pass": 0,
                      "best_acc": max(x.get("acc", 0) for x in lst), "best_score": max(x.get("score", 0) for x in lst),
                      "last": max(x.get("time", 0) for x in lst), "seeded": True, "title": "", "artist": "",
                      "diff": key.split("|")[1] if "|" in key else ""}
    return d


def _save_attempts(d: dict):
    try:
        G.OSU_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ATTEMPTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        os.replace(tmp, ATTEMPTS)
    except Exception as e:                                            # noqa: BLE001
        print("[osu] attempts:", e)


def attempt_begin(m: dict, track: dict) -> tuple[str, int]:
    """Карту начали: +1 попытка. → (ключ карты, номер попытки)."""
    d = load_attempts()
    key = map_key(m)
    e = d.setdefault(key, {"plays": 0, "passes": 0, "fails": 0, "quits": 0, "first_pass": 0,
                           "best_acc": 0.0, "best_score": 0})
    e["plays"] = int(e.get("plays", 0)) + 1
    e.update({"title": track_title(track), "artist": track.get("artist") or "", "path": str(track.get("path", "")),
              "diff": m.get("diff", ""), "diff_name": m.get("name", ""), "stars": float(m.get("stars", 0)),
              "last": time.time()})
    _save_attempts(d)
    return key, e["plays"]


def attempt_end(key: str, outcome: str, res: dict | None = None) -> dict:
    """Исход попытки: pass / fail / quit. Возвращает запись карты."""
    d = load_attempts()
    e = d.get(key)
    if e is None:
        return {}
    if outcome == "pass":
        e["passes"] = int(e.get("passes", 0)) + 1
        if not e.get("first_pass"):
            e["first_pass"] = int(e.get("plays", 1))
        if res:
            e["best_acc"] = max(float(e.get("best_acc", 0)), float(res.get("acc", 0)))
            e["best_score"] = max(int(e.get("best_score", 0)), int(res.get("score", 0)))
    elif outcome == "fail":
        e["fails"] = int(e.get("fails", 0)) + 1
    else:
        e["quits"] = int(e.get("quits", 0)) + 1
    _save_attempts(d)
    return e


def attempts_line(e: dict) -> str:
    """«14 попыток · пройдено 3 · впервые — с 9-й» для выбора песни и результатов."""
    if not e or not e.get("plays"):
        return "Попыток на этой сложности пока нет"
    n, ps = int(e["plays"]), int(e.get("passes", 0))
    s = f"Попыток: {n}  ·  пройдено: {ps}"
    if e.get("first_pass"):
        s += f"  ·  впервые — с {e['first_pass']}-й попытки"
    elif ps == 0:
        s += "  ·  ещё не пройдена"
    return s


def load_replay(rid: str):
    try:
        with gzip.open(REPLAYS / f"{rid}.json.gz", "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                                 # noqa: BLE001
        return None


def player_stats() -> dict:
    """Общий профиль: pp (взвешенно, как в osu!), точность, игры, уровень."""
    d = load_scores()
    best = sorted((max(lst, key=lambda x: x.get("pp", 0)) for lst in d.values() if lst),
                  key=lambda x: -x.get("pp", 0))
    pp = sum(x.get("pp", 0) * 0.95 ** i for i, x in enumerate(best))
    acc = (sum(x["acc"] * 0.95 ** i for i, x in enumerate(best)) / sum(0.95 ** i for i in range(len(best)))) if best else 0
    total = sum(x["score"] for lst in d.values() for x in lst)
    plays = sum(len(lst) for lst in d.values())
    lvl = 1 + int((total / 50000) ** 0.5)
    frac = (total / 50000) ** 0.5 % 1
    return {"pp": pp, "acc": acc, "plays": plays, "level": lvl, "frac": frac, "total": total}


class _ThumbLoader(QObject):
    """Миниатюры карусели декодируются в рабочем потоке (QImageReader с уменьшением)."""
    ready = pyqtSignal(object, object, object)

    def __init__(self):
        super().__init__()
        self.cv = threading.Condition()
        self.q = []
        self.ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self.alive = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="esu-thumbs")
        self.thread.start()

    def stop(self):
        with self.cv:
            self.alive = False
            self.q.clear()
            self.cv.notify_all()
        self.thread.join(timeout=2)

    def load(self, path, w, h, cb):
        with self.cv:
            self.q.append((path, w, h, cb))
            if len(self.q) > 24:                           # пролистали далеко — старые не нужны
                self.q = self.q[-24:]
            self.cv.notify()

    def _run(self):
        while True:
            with self.cv:
                while self.alive and not self.q:
                    self.cv.wait()
                if not self.alive:
                    return
                path, w, h, cb = self.q.pop()
            img = read_image(path, max(w, h) * 2)
            if not img.isNull():
                img = img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            try:
                self.ready.emit(cb, path, img)
            except RuntimeError:
                return

    def _deliver(self, cb, path, img):
        try:
            cb(path, img)
        except RuntimeError:
            pass


class _BgWorker(QObject):
    """Один рабочий поток на все фоны: обложка → размер окна (+ гауссово размытие). В GUI-поток
    возвращается готовая картинка — смена трека в выборе песни больше не подвешивает кадр."""
    ready = pyqtSignal(object, object, object)            # (кэш, ключ, QImage)

    def __init__(self):
        super().__init__()
        self.cv = threading.Condition()
        self.jobs = {}                                     # кэш → (ключ, путь, W, H, dpr, blur)
        self.ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=self._run, daemon=True, name="esu-bg").start()

    def submit(self, cache, key, path, W, H, dpr, blur):
        with self.cv:
            self.jobs[id(cache)] = (cache, key, path, W, H, dpr, blur)
            self.cv.notify()

    def _run(self):
        import blur_fx
        while True:
            with self.cv:
                while not self.jobs:
                    self.cv.wait()
                _k, job = self.jobs.popitem()
            cache, key, path, W, H, dpr, blur = job
            img = QImage()
            try:
                if path and os.path.exists(path):
                    Wd, Hd = max(1, int(W * 1.06 * dpr)), max(1, int(H * 1.06 * dpr))
                    if blur:
                        img = blur_fx.blurred_cover(path, Wd, Hd, max(Wd, Hd) / 70.0)
                    else:
                        src = read_image(path, max(Wd, Hd) + 64)
                        if not src.isNull():
                            img = blur_fx.cover_crop(src, Wd, Hd).scaled(
                                Wd, Hd, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            except Exception as e:                         # noqa: BLE001
                print("[esu bg]", e)
            try:
                self.ready.emit(cache, key, img)
            except RuntimeError:
                return

    def _deliver(self, cache, key, img):
        try:
            cache._done(key, img)
        except RuntimeError:                               # экран уже удалён
            pass


_BGW = None


def _bg_worker() -> _BgWorker:
    global _BGW
    if _BGW is None:
        _BGW = _BgWorker()
    return _BGW


class BgCache:
    """Обложка → картинка фона под размер (затемнение рисуют сами экраны). Готовится в фоне;
    пока готовится — виден прежний фон, новый проявляется плавно, как в osu!."""

    FADE = 0.35

    def __init__(self):
        self.key = None
        self.pm = None
        self.prev = None
        self.t_switch = 0.0
        self._want = None

    def get(self, path, W, H, dpr, blur=True):
        key = (path, int(W), int(H), round(float(dpr), 2), bool(blur))
        if key != self._want:
            self._want = key
            if path and os.path.exists(path):
                _bg_worker().submit(self, key, path, W, H, dpr, blur)
            else:
                self._done(key, QImage())
        return self.pm

    def _done(self, key, img):
        if key != self._want:
            return                                         # пока считалось — уже выбран другой трек
        self.prev = self.pm
        if img is None or img.isNull():
            self.pm = None
        else:
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(key[3])
            self.pm = pm
        self.key = key
        self.t_switch = time.perf_counter()

    def fade(self) -> float:
        return min(1.0, (time.perf_counter() - self.t_switch) / self.FADE)


def _blit_cover(p: QPainter, pm, W, H, par=(0.0, 0.0)):
    d = pm.devicePixelRatio()
    w, h = pm.width() / d, pm.height() / d
    if w < W or h < H:                                     # окно растянули, новый фон ещё считается
        k = max(W / w, H / h)
        w, h = w * k, h * k
    p.drawPixmap(QRectF((W - w) / 2 + par[0], (H - h) / 2 + par[1], w, h), pm, QRectF(pm.rect()))


def draw_bg(p: QPainter, pm, W, H, dim=0.55, par=(0.0, 0.0), cache: BgCache | None = None):
    if cache is not None and cache.prev is not None and pm is not None and cache.fade() < 1.0:
        _blit_cover(p, cache.prev, W, H, par)
        p.setOpacity(cache.fade())
        _blit_cover(p, pm, W, H, par)
        p.setOpacity(1.0)
    elif pm is None:
        g = QLinearGradient(0, 0, W, H)
        g.setColorAt(0, QColor(48, 22, 58))
        g.setColorAt(1, QColor(12, 8, 20))
        p.fillRect(QRectF(0, 0, W, H), QBrush(g))
        if cache is not None and cache.prev is not None and cache.fade() < 1.0:
            p.setOpacity(1.0 - cache.fade())
            _blit_cover(p, cache.prev, W, H, par)
            p.setOpacity(1.0)
    else:
        _blit_cover(p, pm, W, H, par)
    p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, int(255 * dim)))


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _uptime(sec: int) -> str:
    """«16 секунд», «3 минуты», «1 час 5 минут» — как «Время работы osu!»."""
    if sec < 60:
        return f"{sec} {_plural(sec, 'секунда', 'секунды', 'секунд')}"
    m = sec // 60
    if m < 60:
        return f"{m} {_plural(m, 'минута', 'минуты', 'минут')}"
    h, m = divmod(m, 60)
    return f"{h} {_plural(h, 'час', 'часа', 'часов')} {m} {_plural(m, 'минута', 'минуты', 'минут')}"


def _osu_icon(p: QPainter, kind: str, r: QRectF, color: QColor):
    """Значки кнопок как в osu!: меню (Play — «①②», Edit — цель с курсором, Options — галочка и крестик,
    Exit — дверь, Solo — прицел, Back — шеврон) и музыка (стоп, «i», список). Прочие — SK.draw_icon."""
    u = min(r.width(), r.height())
    cx, cy = r.center().x(), r.center().y()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    lw = max(1.5, u * 0.075)
    pen = QPen(color, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def num(x, y, rad, s):
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x, y), rad, rad)
        p.setFont(_font(rad * 1.25, QFont.Weight.Bold))
        p.drawText(QRectF(x - rad, y - rad, 2 * rad, 2 * rad), Qt.AlignmentFlag.AlignCenter, s)
    if kind == "o_play":
        num(cx - u * 0.2, cy + u * 0.12, u * 0.24, "1")
        num(cx + u * 0.24, cy - u * 0.12, u * 0.26, "2")
    elif kind == "o_edit":
        num(cx - u * 0.06, cy - u * 0.06, u * 0.22, "1")
        p.setPen(QPen(color, lw * 0.8, Qt.PenStyle.DashLine))
        p.drawRect(QRectF(cx - u * 0.42, cy - u * 0.42, u * 0.72, u * 0.72))
        arrow = QPolygonF([QPointF(cx + u * 0.08, cy + u * 0.02), QPointF(cx + u * 0.46, cy + u * 0.32),
                           QPointF(cx + u * 0.27, cy + u * 0.34), QPointF(cx + u * 0.36, cy + u * 0.5),
                           QPointF(cx + u * 0.3, cy + u * 0.53), QPointF(cx + u * 0.21, cy + u * 0.37),
                           QPointF(cx + u * 0.08, cy + u * 0.46)])
        p.setPen(QPen(QColor(0, 0, 0, 160), lw * 0.6))
        p.setBrush(color)
        p.drawPolygon(arrow)
    elif kind == "o_options":
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        a = QRectF(cx - u * 0.46, cy - u * 0.46, u * 0.48, u * 0.48)
        b = QRectF(cx - u * 0.02, cy - u * 0.02, u * 0.48, u * 0.48)
        p.drawRect(a)
        p.drawRect(b)
        p.drawPolyline([QPointF(a.left() + a.width() * 0.18, a.center().y()),
                        QPointF(a.left() + a.width() * 0.42, a.bottom() - a.height() * 0.2),
                        QPointF(a.right() + a.width() * 0.1, a.top() - a.height() * 0.15)])
        p.drawLine(QPointF(b.left() + b.width() * 0.22, b.top() + b.height() * 0.22),
                   QPointF(b.right() - b.width() * 0.22, b.bottom() - b.height() * 0.22))
        p.drawLine(QPointF(b.right() - b.width() * 0.22, b.top() + b.height() * 0.22),
                   QPointF(b.left() + b.width() * 0.22, b.bottom() - b.height() * 0.22))
    elif kind == "o_exit":
        d = QRectF(cx - u * 0.22, cy - u * 0.48, u * 0.44, u * 0.96)
        p.setPen(QPen(QColor(0, 0, 0, 90), lw * 0.6))
        p.setBrush(color)
        p.drawRect(d)
        p.setBrush(QColor(120, 120, 130))
        p.drawEllipse(QPointF(d.right() - u * 0.08, cy + u * 0.04), u * 0.035, u * 0.035)
    elif kind in ("o_solo", "o_multi"):
        xs = [cx] if kind == "o_solo" else [cx - u * 0.22, cx + u * 0.22]
        for x in xs:
            rad = u * (0.4 if kind == "o_solo" else 0.26)
            p.setPen(QPen(color, lw * 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x, cy), rad, rad)
            p.drawArc(QRectF(x - rad * 0.55, cy - rad * 0.55, rad * 1.1, rad * 1.1), 30 * 16, 300 * 16)
            p.drawLine(QPointF(x - rad * 0.22, cy), QPointF(x + rad * 0.22, cy))
            p.drawLine(QPointF(x, cy - rad * 0.22), QPointF(x, cy + rad * 0.22))
    elif kind == "o_back":
        p.setPen(QPen(color, lw * 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(cx + u * 0.12, cy - u * 0.3), QPointF(cx - u * 0.16, cy), QPointF(cx + u * 0.12, cy + u * 0.3)])
    elif kind == "m_stop":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRect(QRectF(cx - u * 0.36, cy - u * 0.36, u * 0.72, u * 0.72))
    elif kind == "m_info":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy - u * 0.36), u * 0.1, u * 0.1)
        p.drawRect(QRectF(cx - u * 0.08, cy - u * 0.16, u * 0.16, u * 0.56))
        p.drawRect(QRectF(cx - u * 0.18, cy - u * 0.16, u * 0.12, u * 0.1))
        p.drawRect(QRectF(cx - u * 0.18, cy + u * 0.34, u * 0.36, u * 0.08))
    elif kind == "m_list":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for k in (-1, 0, 1):
            p.drawRect(QRectF(cx - u * 0.42, cy + k * u * 0.28 - u * 0.06, u * 0.84, u * 0.12))
    elif kind == "m_prev" or kind == "m_next":
        s = 1 if kind == "m_next" else -1                # как в osu!: треугольник и черта
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(cx - s * u * 0.3, cy - u * 0.38), QPointF(cx + s * u * 0.22, cy),
                                 QPointF(cx - s * u * 0.3, cy + u * 0.38)]))
        p.drawRect(QRectF(cx + s * u * 0.22 - (u * 0.12 if s > 0 else 0), cy - u * 0.38, u * 0.12, u * 0.76))
    elif kind == "m_play":
        SK.draw_icon(p, "play", r, color)
    elif kind == "m_pause":
        SK.draw_icon(p, "pause", r, color)
    else:
        SK.draw_icon(p, {"o_import": "import", "o_music": "music"}.get(kind, kind), r, color)
    p.restore()


_RAY_PM = {}


def _ray_pixmap(dpr: float) -> QPixmap:
    """Белая полоска для лучей визуализатора (мягкие края по ширине) — растягивается на каждый луч."""
    key = round(dpr, 2)
    pm = _RAY_PM.get(key)
    if pm is None:
        img = QImage(32, 8, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        for y, a in enumerate((0.0, 0.45, 1.0, 1.0, 1.0, 1.0, 0.45, 0.0)):
            v = int(255 * a)
            for x in range(32):
                img.setPixel(x, y, (v << 24) | (v << 16) | (v << 8) | v)
        pm = QPixmap.fromImage(img)
        _RAY_PM[key] = pm
    return pm


def draw_rays(p, cx, cy, R, r0k, lk, vis, rot, width, alpha, reps=3):
    """Лучи визуализатора вокруг печеньки: n полос × reps повторов по кругу. В кадре C++ — одной
    командой (sprites_affine), иначе — drawLines, как раньше."""
    n = len(vis)
    idx = np.nonzero(vis >= 0.03)[0]
    if not len(idx):
        return
    a = rot + (np.concatenate([idx + r * n for r in range(reps)])) / (reps * n) * 2 * math.pi
    L = R * lk * np.tile(vis[idx], reps)
    c, s_ = np.cos(a), np.sin(a)
    x0, y0 = cx + c * R * r0k, cy + s_ * R * r0k
    if hasattr(p, "sprites_affine"):
        pm = _ray_pixmap(p.device().devicePixelRatioF() if hasattr(p.device(), "devicePixelRatioF") else 1.0)
        w = width * 8.0 / 4.0                                  # в картинке 4 сплошных ряда из 8
        ku, kv = L / pm.width(), w / pm.height()
        if p.sprites_affine(pm, c * ku, s_ * ku, -s_ * kv, c * kv, x0 + s_ * w / 2, y0 - c * w / 2,
                            alpha / 255.0):
            return
    x1, y1 = x0 + c * L, y0 + s_ * L
    lines = [QLineF(float(a0), float(b0), float(a1), float(b1)) for a0, b0, a1, b1 in zip(x0, y0, x1, y1)]
    p.setPen(QPen(QColor(255, 255, 255, alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    p.drawLines(lines)


_FLASH_PM = []


def side_flash(p, W, H, a):
    """Вспышки kiai по бокам экрана: градиент — готовой картинкой-полоской, растянутой на четверть экрана
    (заливка градиентом на весь рост окна стоила ~1 мс на кадр)."""
    if not _FLASH_PM:
        for flip in (False, True):
            img = QImage(256, 1, QImage.Format.Format_ARGB32_Premultiplied)
            for x in range(256):
                v = int(round(255 * (1 - x / 255.0)))
                img.setPixel(255 - x if flip else x, 0, (v << 24) | (v << 16) | (v << 8) | v)
            _FLASH_PM.append(QPixmap.fromImage(img))
    left, right = _FLASH_PM
    p.setOpacity(max(0.0, min(1.0, a)))
    p.drawPixmap(QRectF(0, 0, W * 0.25, H), left, QRectF(left.rect()))
    p.drawPixmap(QRectF(W * 0.75, 0, W * 0.25, H), right, QRectF(right.rect()))
    p.setOpacity(1.0)


def rrect(p: QPainter, r: QRectF, rad, fill, pen=None):
    p.setPen(pen if pen is not None else Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(r, rad, rad)


def text(p: QPainter, r: QRectF, s: str, px, color=QColor(255, 255, 255), weight=QFont.Weight.DemiBold,
         align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, italic=False, elide=True):
    p.setFont(_font(px, weight, italic))
    p.setPen(color)
    if elide:
        s = p.fontMetrics().elidedText(s, Qt.TextElideMode.ElideRight, int(r.width()))
    p.drawText(r, align, s)


# ------------------------------------------------------------------ #
#  Экраны                                                             #
# ------------------------------------------------------------------ #

class Screen(QWidget):
    KEYS_OWNED = True

    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self.win = shell.win
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.t_enter = time.perf_counter()

    def event(self, e):
        if e.type() == QEvent.Type.Paint:
            # кадр экрана собирает C++-компоновщик (osu_game.paint_redirected); стили Y2K/Classic,
            # подменяющие paintEvent, ускоряются так же — их QPainter(self) перенаправляется
            return paint_redirected(self, super().event, e)
        if e.type() == QEvent.Type.ShortcutOverride and self.KEYS_OWNED and e.key() != Qt.Key.Key_F11:
            if not (e.modifiers() & Qt.KeyboardModifier.ControlModifier) or e.key() in (Qt.Key.Key_Return,
                                                                                          Qt.Key.Key_Enter):
                e.accept()
                return True
        return super().event(e)

    def enter(self):
        self.t_enter = time.perf_counter()

    def leave(self):
        pass

    def step(self, dt, now):
        pass

    def sfx(self, name, vol=1.0):
        self.shell.sfx.play(name, vol)


class Fader(QWidget):
    """Затемнение при смене экранов и всплывающие сообщения — поверх всего."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.a = 0.0

    def paintEvent(self, _e):
        toasts = getattr(self.parent(), "toasts", [])
        if self.a <= 0.003 and not toasts:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.a > 0.003:
            p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * min(1.0, self.a))))
        now = time.perf_counter()
        y = self.height() - 150
        for s, t0 in toasts[-3:]:
            e = now - t0
            a = min(1.0, e / 0.15) if e < 2.6 else max(0.0, 1 - (e - 2.6) / 0.6)
            p.setOpacity(a)
            p.setFont(_font(15, QFont.Weight.DemiBold))
            w = min(self.width() - 40, p.fontMetrics().horizontalAdvance(s) + 48)
            r = QRectF((self.width() - w) / 2, y, w, 44)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(30, 22, 36, 235))
            p.drawRoundedRect(r, 22, 22)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, s)
            y -= 54
        p.end()


# ── главное меню ── #

class MenuScreen(Screen):
    # (подпись, значок для SK.draw_icon, цвет) — как в osu!: Играть / Редактор / Настройки / Выход,
    # «Играть» раскрывается во второй ряд: Соло / Карты osu! / Музыка / Назад
    ITEMS_MAIN = [("Играть", "play", QColor(118, 70, 220)), ("Редактор", "edit", QColor(232, 150, 40)),
                  ("Настройки", "settings", QColor(96, 96, 120)), ("Выход", "exit", QColor(230, 60, 110))]
    ITEMS_PLAY = [("Соло", "solo", QColor(150, 100, 240)), ("Карты osu!", "import", QColor(40, 130, 220)),
                  ("Музыка", "music", QColor(30, 165, 150)), ("Назад", "back", QColor(110, 110, 130))]

    # как в osu!: подписи кнопок по-английски, при наведении — пояснение (значок рисует _osu_icon)
    OSU_MAIN = [("Play", "o_play", "Сыграть карту: своя музыка и карты osu!"), ("Edit", "o_edit", "Редактор карт"),
                ("Options", "o_options", "Настройки esu!"), ("Exit", "o_exit", "Выйти из esu!")]
    OSU_PLAY = [("Solo", "o_solo", "Свободная игра — выбор песни"), ("Beatmaps", "o_import", "Карты из osu!: обновить, "
                "открыть .osz"), ("Music", "o_music", "Библиотека ECHOES"), ("Back", "o_back", "Назад")]

    @property
    def ITEMS(self):
        if self.shell.osu_look():
            return self.OSU_PLAY if self.level == 1 else self.OSU_MAIN
        return self.ITEMS_PLAY if self.level == 1 else self.ITEMS_MAIN

    def __init__(self, shell):
        super().__init__(shell)
        self.KEYS_OWNED = False
        self.expanded = False
        self.level = 0
        self._ck = {}
        self._item_pm = {}
        self.ek = 0.0
        self.hover = None
        self.last_input = time.perf_counter()
        self.tris = []
        self.vis = np.zeros(128)
        self.vis_rot = 0.0
        self.cookie_hover = 0.0
        self.beat = 0.0
        self.vol_t = -10.0
        self.bg = BgCache()
        self.intro_t = None
        self.tip = random.choice(TIPS)
        self.flash_side = 0.0
        self._last_beat_i = -1
        self.mouse = QPointF(0, 0)
        self._stats = None
        self._avatar = None

    WELCOME = 1.7                                       # заставка «welcome», с

    def enter(self):
        super().enter()
        self._stats = player_stats()
        self.tip = random.choice(TIPS)
        if self.intro_t is None:
            self.intro_t = time.perf_counter()
            if self.shell.osu_look() and not self.shell.S().get("intro", True):
                self.intro_t -= self.WELCOME           # заставка выключена — сразу меню
            self.sfx("whoosh", 0.8)
        QTimer.singleShot(0, self.setFocus)

    def _welcome_left(self) -> float:
        """Сколько ещё идёт заставка welcome (0 — уже меню)."""
        if self.intro_t is None or not self.shell.osu_look():
            return 0.0
        return max(0.0, self.WELCOME - (time.perf_counter() - self.intro_t))

    def _skip_welcome(self) -> bool:
        if self._welcome_left() > 0:
            self.intro_t = time.perf_counter() - self.WELCOME
            return True
        return False

    # геометрия
    def _toolbar(self):
        if self.shell.osu_look():
            return int(round(self.height() * 0.105))  # верхняя полоса как в osu!
        return 46

    def _cookie(self):
        W, H = self.width(), self.height()
        e = self.ek * self.ek * (3 - 2 * self.ek)
        if self.shell.osu_look():                      # как в osu!: большая печенька, раскрываясь — влево
            cx = W / 2 - H * 0.26 * e
            return cx, H / 2, H * (0.375 - 0.042 * e)
        cx = W / 2 + (W * 0.30 - W / 2) * e
        cy = H / 2 + 14
        R = H * 0.25 * (1 - 0.18 * e)
        return cx, cy, R

    def _items(self):
        W, H = self.width(), self.height()
        cx, cy, R = self._cookie()
        e = self.ek
        out = []
        if self.shell.osu_look():
            # столбик скошенных фиолетовых кнопок, выезжающих из-за печеньки
            bh, gap = H * 0.11, H * 0.025
            n = len(self.ITEMS)
            top = cy - (n * bh + (n - 1) * gap) / 2
            bw = H * 0.68
            x0 = cx + R * 0.3
            ease = 1 - (1 - min(1.0, e)) ** 3
            for i, it in enumerate(self.ITEMS):
                grow = H * 0.035 if self.hover == ("item", i) else 0.0
                x = x0 - (1 - ease) * bw * 0.85
                out.append((i, it, QRectF(x, top + i * (bh + gap), bw + grow, bh)))
            return out
        bh = H * 0.19
        bw = W * 0.115
        x0 = cx + R * 0.55
        for i, it in enumerate(self.ITEMS):
            x = x0 + (i * (bw + 6) + R * 0.4) * e
            grow = 1.18 if self.hover == ("item", i) else 1.0
            out.append((i, it, QRectF(x, cy - bh / 2, bw * grow, bh)))
        return out

    def _tb_buttons(self):
        W = self.width()
        h = self._toolbar()
        if self.shell.osu_look():
            # справа сверху — управление музыкой, как в osu!: назад, играть, пауза, стоп, вперёд, о треке, список
            H = self.height()
            s, step = H * 0.04, H * 0.042
            y = H * 0.062 - s / 2
            b = {}
            for i, k in enumerate(reversed(("prev", "play", "pause", "stop", "next", "info", "list"))):
                b[k] = QRectF(W - H * 0.018 - s - i * step, y, s, s)
            # справа снизу — как «Online Users» / «Show Chat»: смена темы и музыка
            bh2 = H * 0.032
            fh = max(11.0, H * 0.016)
            w1, w2 = fh * 9.6, fh * 6.4
            b["music"] = QRectF(W - 6 - w2, H - bh2 - 6, w2, bh2)
            b["themes"] = QRectF(W - 12 - w2 - w1, H - bh2 - 6, w1, bh2)
            return b
        b = {"settings": QRectF(8, 4, h - 8, h - 8), "home": QRectF(h + 4, 4, h - 8, h - 8),
             "themes": QRectF(2 * h + 10, 6, 196, h - 12)}
        x = W - 140
        b["next"] = QRectF(x - 44, 4, 38, h - 8)
        b["play"] = QRectF(x - 88, 4, 38, h - 8)
        b["prev"] = QRectF(x - 132, 4, 38, h - 8)
        b["artist"] = QRectF(x - 176, 4, 38, h - 8)
        return b

    def step(self, dt, now):
        tgt = 1.0 if self.expanded else 0.0
        self.ek += (tgt - self.ek) * min(1.0, dt * 9)
        if self.expanded and now - self.last_input > 12:
            self.expanded = False
            self.level = 0
        cx, cy, R = self._cookie()
        hov = (self.mouse.x() - cx) ** 2 + (self.mouse.y() - cy) ** 2 <= R * R
        self.cookie_hover += ((1.0 if hov else 0.0) - self.cookie_hover) * min(1.0, dt * 10)
        pulse = self.shell.pulse
        an = self.shell.cur_analysis()
        kiai = False
        if an is not None:
            try:
                pos = float(self.win.engine.get_position_smooth()) * 1000
                b = an["beats"]
                i = bisect.bisect_right(b, pos) - 1
                if i != self._last_beat_i and self.win.engine.is_playing():
                    self._last_beat_i = i
                    self.beat = 1.0
                    kiai = any(a <= pos < bb for a, bb in an.get("kiai", []))
                    if kiai:
                        self.flash_side = 1.0
            except Exception:                                         # noqa: BLE001
                pass
        else:
            self.beat = max(self.beat, pulse.beat)
        if not self.win.engine.is_playing():
            self.beat=max(self.beat,math.exp(-7*(now % 1.0)))
        self.beat *= math.exp(-dt * 7)
        self.flash_side *= math.exp(-dt * 4)
        # визуализатор
        try:
            s = self.win.engine.get_visual_samples(2048) if self.win.engine.is_playing() else None
        except Exception:                                             # noqa: BLE001
            s = None
        if s is not None and len(s) == 2048:
            if getattr(self, "_vis_edges", None) is None:
                e = np.geomspace(1, 518, 129).astype(int)
                self._vis_edges = (e[:-1], np.maximum(e[:-1] + 1, e[1:]))
                self._hann = np.hanning(2048).astype(np.float32)
            spec = np.abs(np.fft.rfft(np.asarray(s, np.float32) * self._hann))[2:520]
            lo_i, hi_i = self._vis_edges
            cs = np.concatenate([[0.0], np.cumsum(spec)])
            bands = (cs[hi_i] - cs[lo_i]) / (hi_i - lo_i)             # средние по полосам без цикла
            bands = np.log1p(bands * 2) / 4.5
            self.vis = np.maximum(self.vis * math.exp(-dt * 9), np.clip(bands, 0, 1.2))
        else:
            self.vis *= math.exp(-dt * 6)
        self.vis_rot += dt * 0.12
        if self.shell.osu_look():
            return                                      # треугольники фона — только у Y2K (там это звёздочки)
        # треугольники
        W, H = self.width(), self.height()
        sp = 1.0 + 2.5 * self.beat
        for tr in self.tris:
            tr[1] -= tr[3] * dt * sp
        self.tris = [tr for tr in self.tris if tr[1] + tr[2] > -10]
        while len(self.tris) < 46:
            s = random.uniform(30, 160) * H / 900
            self.tris.append([random.uniform(-s, W + s), H + s + random.uniform(0, H) * (len(self.tris) < 10),
                              s, random.uniform(12, 40), random.uniform(0.06, 0.2)])

    def paintEvent(self, _e):
        if self.shell.osu_look():
            return self._paint_osu()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        now = time.perf_counter()
        t = self.shell.cur_track()
        par = (0.0, 0.0)
        if self.shell.S().get("menu_parallax", True):
            par = (-(self.mouse.x() / max(1, W) - 0.5) * W * 0.03, -(self.mouse.y() / max(1, H) - 0.5) * H * 0.03)
        draw_bg(p, self.bg.get(self.shell.menu_bg_path(t), W, H, self.devicePixelRatioF(), blur=False), W, H, 0.6, par,
                cache=self.bg)
        # треугольники: одна заливка на группу прозрачности вместо 46 отдельных многоугольников
        groups = {}
        for x, y, s, _v, a in self.tris:
            q = min(3, int(a * 20))
            path = groups.get(q)
            if path is None:
                path = groups[q] = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(x, y - s * 0.58), QPointF(x - s / 2, y + s * 0.29),
                                       QPointF(x + s / 2, y + s * 0.29)]))
        p.setPen(Qt.PenStyle.NoPen)
        for q, path in groups.items():
            p.fillPath(path, QColor(255, 102, 170, int(255 * (q + 1.5) / 20 * 0.6)))
        # вспышки по бокам в kiai
        if self.flash_side > 0.02:
            side_flash(p, W, H, 70 * self.flash_side / 255.0)
        cx, cy, R = self._cookie()
        # полоса меню (кнопки — готовые картинки)
        if self.ek > 0.01:
            bh = H * 0.19
            p.setOpacity(self.ek)
            p.fillRect(QRectF(0, cy - bh / 2, W, bh), QColor(0, 0, 0, 150))
            for i, it, r in self._items():
                hov = self.hover == ("item", i)
                pm = self._item_pix(i, it, r, hov)
                d = pm.devicePixelRatio()
                p.drawPixmap(QPointF(round(r.left() - r.height() * 0.12 - 2), round(r.top() - 2)), pm)
                _ = d
            p.setOpacity(1.0)
        # визуализатор вокруг печеньки — все полоски одной командой
        draw_rays(p, cx, cy, R, 0.98, 0.55, self.vis, self.vis_rot, max(2.0, R * 0.022), 70)
        # печенька
        intro = 1.0
        if self.intro_t is not None:
            e = (now - self.intro_t) / 1.4
            if e < 1:
                intro = 1 - (1 - e) ** 3 * math.cos(e * 6) if e > 0 else 0.0
        sc = (1 + 0.035 * self.beat + 0.05 * self.cookie_hover) * max(0.05, intro)
        self._draw_cookie(p, cx, cy, R * sc)
        # тулбар
        self._draw_toolbar(p, W, H, t)
        # профиль
        self._draw_user(p, W, H)
        # подсказка и подпись
        text(p, QRectF(16, H - 34, W * 0.6, 24), "Совет: " + self.tip, 13, QColor(255, 255, 255, 160),
             QFont.Weight.Normal)
        text(p, QRectF(W - 330, H - 34, 314, 24), f"ECHOES · {THEME_NAME}", 13, QColor(255, 255, 255, 120),
             QFont.Weight.Normal, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # громкость
        e = now - self.vol_t
        if e < 1.6:
            self._draw_volume(p, W, H, 1.0 if e < 1.2 else 1 - (e - 1.2) / 0.4)
        # вступление
        if self.intro_t is not None and now - self.intro_t < 1.0:
            p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * max(0.0, 1 - (now - self.intro_t) / 1.0))))
            if now - self.intro_t < 0.8:
                text(p, QRectF(0, H * 0.78, W, 40), f"добро пожаловать в {THEME_NAME}", 22,
                     QColor(255, 255, 255, int(255 * (1 - (now - self.intro_t) / 0.8))), QFont.Weight.Light,
                     Qt.AlignmentFlag.AlignCenter)
        p.end()

    # ── классика: главное меню как в osu! ── #
    def _paint_osu(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        now = time.perf_counter()
        if self._welcome_left() > 0:
            self._paint_welcome(p, W, H, now)
            p.end()
            return
        t = self.shell.cur_track()
        par = (0.0, 0.0)
        if self.shell.S().get("menu_parallax", True):
            par = (-(self.mouse.x() / max(1, W) - 0.5) * W * 0.02, -(self.mouse.y() / max(1, H) - 0.5) * H * 0.02)
        draw_bg(p, self.bg.get(self.shell.menu_bg_path(t), W, H, self.devicePixelRatioF(), blur=False), W, H, 0.2,
                par, cache=self.bg)
        if self.flash_side > 0.02:                      # kiai: вспышки по бокам
            side_flash(p, W, H, 60 * self.flash_side / 255.0)
        tb = self._toolbar()
        p.fillRect(QRectF(0, 0, W, tb), QColor(0, 0, 0, 115))           # верхняя и нижняя полосы, как в osu!
        p.fillRect(QRectF(0, H - tb, W, tb), QColor(0, 0, 0, 105))
        cx, cy, R = self._cookie()
        # визуализатор: тонкие белые лучи вокруг печеньки — одной командой
        draw_rays(p, cx, cy, R, 0.97, 0.62, self.vis, self.vis_rot, max(1.5, R * 0.011), 60)
        # кнопки — из-за печеньки (она рисуется поверх)
        if self.ek > 0.01:
            p.setOpacity(min(1.0, self.ek * 1.6))
            for i, it, r in self._items():
                pm = self._item_pix(i, it, r, self.hover == ("item", i))
                p.drawPixmap(QPointF(round(r.left() - r.height() * 0.3), round(r.top() - r.height() * 0.3)), pm)
            p.setOpacity(1.0)
        # печенька: появляется после заставки с отскоком, пульсирует в такт
        intro = 1.0
        if self.intro_t is not None:
            e = (now - self.intro_t - self.WELCOME) / 0.9
            if e < 1:
                intro = 1 - (1 - e) ** 3 * math.cos(e * 6) if e > 0 else 0.0
        sc = (1 + 0.03 * self.beat + 0.04 * self.cookie_hover) * max(0.05, intro)
        self._draw_cookie(p, cx, cy, R * sc)
        self._draw_user(p, W, H)
        if self.ek > 0.02:
            p.setOpacity(min(1.0, self.ek * 1.4))
            self._draw_info_text(p, W, H)
            p.setOpacity(1.0)
        self._draw_toolbar(p, W, H, t)
        self._draw_corner(p, W, H)
        e = now - self.vol_t
        if e < 1.6:
            self._draw_volume(p, W, H, 1.0 if e < 1.2 else 1 - (e - 1.2) / 0.4)
        e = now - (self.intro_t or 0) - self.WELCOME     # после заставки — вспышка и проявление
        if 0 <= e < 0.5:
            p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * max(0.0, 1 - e / 0.35))))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.fillRect(self.rect(), QColor(255, 255, 255, int(70 * (1 - e / 0.5))))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.end()

    def _welcome_pm(self, H):
        """Надпись «welcome» с синим свечением (картинка — один раз на размер)."""
        dpr = self.devicePixelRatioF()
        key = (int(H), round(dpr, 2))
        c = getattr(self, "_wl_cache", None)
        if c is not None and c[0] == key:
            return c[1]
        f = _font(H * 0.075, QFont.Weight.Light)
        from PyQt6.QtGui import QFontMetricsF
        fm = QFontMetricsF(f)
        path = QPainterPath()
        x = 0.0
        for ch in "welcome":                            # разрядка букв, как в osu!
            path.addText(x, 0, f, ch)
            x += fm.horizontalAdvance(ch) + H * 0.018
        br = path.boundingRect()
        pad = H * 0.05
        w, h = br.width() + pad * 2, br.height() + pad * 2
        sil = SK._pm(w, h, 1.0)
        q = SK._painter(sil)
        q.translate(pad - br.left(), pad - br.top())
        q.strokePath(path, QPen(QColor(60, 150, 255), H * 0.008))
        q.fillPath(path, QColor(60, 150, 255))
        q.end()
        import blur_fx
        glow = blur_fx.blur_image(sil.toImage(), max(1.5, H * 0.008))
        pm = SK._pm(w, h, dpr)
        q = SK._painter(pm)
        q.drawImage(QRectF(0, 0, w, h), glow)
        q.drawImage(QRectF(0, 0, w, h), glow)
        q.translate(pad - br.left(), pad - br.top())
        q.fillPath(path, QColor(235, 245, 255))
        q.end()
        self._wl_cache = (key, pm)
        return pm

    def _paint_welcome(self, p, W, H, now):
        """Заставка при входе, как в osu!: чёрный экран, «welcome» с синим свечением и тусклое кольцо."""
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0))
        e = now - self.intro_t
        a = min(1.0, e / 0.35) * (1 - max(0.0, (e - 1.25) / 0.45))
        rr = H * 0.34 * (0.92 + 0.08 * e / self.WELCOME)
        p.setPen(QPen(QColor(70, 90, 170, int(60 * a)), max(1.5, H * 0.003)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(W / 2, H / 2), rr, rr)
        blit(p, self._welcome_pm(H), W / 2, H / 2, 1.0 + 0.03 * e, a)
        p.setOpacity(1.0)

    def _draw_info_text(self, p, W, H):
        """«Доступно N карт. / Время работы esu! / Текущее время» — посередине сверху, как в osu!."""
        try:
            n = len(self.win.library) + sum(len(s.get("diffs", [])) for s in OB.load_index())
        except Exception:                                             # noqa: BLE001
            n = len(getattr(self.win, "library", []))
        up = int(time.time() - getattr(self.shell, "t_start", time.time()))
        s = (f"Доступно {n} {_plural(n, 'карта', 'карты', 'карт')}.\nВремя работы esu!: {_uptime(up)}\n"
             f"Текущее время: {time.strftime('%H:%M').lstrip('0') or '0'}")
        key = (int(W), int(H), s, round(self.devicePixelRatioF(), 2))
        c = getattr(self, "_info_cache", None)
        if c is None or c[0] != key:
            fs = H * 0.026
            pm = SK._pm(W * 0.32, fs * 4.2, self.devicePixelRatioF())
            q = SK._painter(pm)
            for i, line in enumerate(s.split("\n")):
                r = QRectF(0, i * fs * 1.25, W * 0.32, fs * 1.3)
                text(q, r.translated(1.5, 1.5), line, fs, QColor(0, 0, 0, 150), QFont.Weight.Normal)
                text(q, r, line, fs, QColor(255, 255, 255), QFont.Weight.Normal)
            q.end()
            self._info_cache = c = (key, pm)
        p.drawPixmap(QPointF(round(W * 0.247), round(H * 0.003)), c[1])

    def _draw_corner(self, p, W, H):
        """Низ: слева — подпись ECHOES (как «ppy powered»), справа — «Сменить тему» и «Музыка»."""
        b = self._tb_buttons()
        hv = self.hover[1] if self.hover and self.hover[0] == "tb" else None
        key = (int(W), int(H), hv, round(self.devicePixelRatioF(), 2))
        c = getattr(self, "_corner_cache", None)
        if c is None or c[0] != key:
            dpr = self.devicePixelRatioF()
            fs = H * 0.044
            left = SK._pm(W * 0.3, H * 0.07, dpr)
            q = SK._painter(left)
            text(q, QRectF(0, 0, fs * 2.6, H * 0.07), "esu!", fs, QColor(255, 255, 255), QFont.Weight.Bold,
                 elide=False)
            text(q, QRectF(fs * 2.55, H * 0.012, W * 0.2, H * 0.024), "ECHOES powered 2026", H * 0.016,
                 QColor(255, 255, 255), QFont.Weight.Bold)
            text(q, QRectF(fs * 2.55, H * 0.034, W * 0.2, H * 0.024), "osu! в плеере ECHOES", H * 0.016,
                 QColor(255, 255, 255, 220), QFont.Weight.Normal)
            q.end()
            right = SK._pm(W, H * 0.05, dpr)
            q = SK._painter(right)
            y0 = H - H * 0.05
            for k, label, ico in (("themes", "Сменить тему", "themes"),
                                  ("music", "Музыка", "music")):
                r = b[k].translated(0, -y0)
                hov = hv == k
                q.setPen(QPen(QColor(255, 255, 255, 230 if hov else 110), 1))
                q.setBrush(QColor(60, 60, 80, 220) if hov else QColor(20, 20, 30, 190))
                q.drawRect(r)
                SK.draw_icon(q, ico, QRectF(r.left() + 4, r.top() + 3, r.height() - 6, r.height() - 6),
                             QColor(255, 255, 255))
                text(q, r.adjusted(r.height() + 2, 0, -4, 0), label, r.height() * 0.5, QColor(255, 255, 255),
                     QFont.Weight.DemiBold)
            q.end()
            self._corner_cache = c = (key, left, right)
        p.drawPixmap(QPointF(8, round(H - H * 0.068)), c[1])
        p.drawPixmap(QPointF(0, round(H - H * 0.05)), c[2])

    def _draw_now_playing(self, p, W, H, t):
        """Справа сверху: «Now Playing ♪ исполнитель - название» и кнопки музыки с полоской позиции."""
        b = self._tb_buttons()
        playing = False
        try:
            playing = bool(self.win.engine.is_playing())
        except Exception:                                             # noqa: BLE001
            pass
        title = f"{(t or {}).get('artist') or '—'} - {track_title(t)}" if t else "ничего не играет"
        hv = self.hover[1] if self.hover and self.hover[0] == "tb" else None
        key = (int(W), int(H), title, playing, hv, round(self.devicePixelRatioF(), 2))
        c = getattr(self, "_np_cache", None)
        if c is None or c[0] != key:
            dpr = self.devicePixelRatioF()
            pm = SK._pm(W * 0.62, H * 0.1, dpr)
            q = SK._painter(pm)
            ox = W - W * 0.62
            fs = H * 0.026
            q.setFont(_font(fs, QFont.Weight.Normal))
            tw = min(W * 0.44, q.fontMetrics().horizontalAdvance(title) + 4)
            x1 = W * 0.62 - H * 0.016
            tr = QRectF(x1 - tw, 0, tw, fs * 1.5)
            text(q, tr.translated(1.5, 1.5), title, fs, QColor(0, 0, 0, 150), QFont.Weight.Normal)
            text(q, tr, title, fs, QColor(255, 255, 255), QFont.Weight.Normal)
            SK.draw_icon(q, "music", QRectF(tr.left() - fs * 1.15, fs * 0.15, fs * 1.1, fs * 1.1), QColor(255, 255, 255))
            lx = tr.left() - fs * 1.2 - fs * 2.6
            text(q, QRectF(lx, 0, fs * 2.6, fs * 0.75), "Now", fs * 0.6, QColor(255, 255, 255), QFont.Weight.DemiBold)
            text(q, QRectF(lx, fs * 0.7, fs * 2.6, fs * 0.8), "Playing", fs * 0.6, QColor(255, 255, 255),
                 QFont.Weight.DemiBold)
            for k in ("prev", "play", "pause", "stop", "next", "info", "list"):
                r = b[k].translated(-ox, 0)
                col = QColor(255, 255, 255) if hv == k else QColor(255, 255, 255, 225)
                if hv == k:
                    q.setPen(Qt.PenStyle.NoPen)
                    q.setBrush(QColor(255, 255, 255, 40))
                    q.drawRoundedRect(r, 6, 6)
                _osu_icon(q, "m_" + k, r.adjusted(r.width() * 0.18, r.height() * 0.18, -r.width() * 0.18,
                                                  -r.height() * 0.18), col)
            q.end()
            self._np_cache = c = (key, pm, ox)
        p.drawPixmap(QPointF(round(c[2]), 0), c[1])
        # позиция трека — тонкая полоска под кнопками
        x0, x1 = b["prev"].left(), b["list"].right()
        y = b["prev"].bottom() + H * 0.006
        try:
            dur = float(getattr(self.win.engine, "duration", 0) or 0)
            pos = float(self.win.engine.get_position_smooth() or 0)
        except Exception:                                             # noqa: BLE001
            dur, pos = 0.0, 0.0
        p.fillRect(QRectF(x0, y, x1 - x0, 3), QColor(255, 255, 255, 50))
        if dur > 0:
            p.fillRect(QRectF(x0, y, (x1 - x0) * max(0.0, min(1.0, pos / dur)), 3), QColor(255, 255, 255, 200))

    def _item_pix_osu(self, i, it, r, hov):
        """Кнопка меню osu!: фиолетовая скошенная плашка (под курсором — розовая и длиннее), курсивная
        подпись, значок справа; под курсором — пояснение."""
        name, icon, sub = it
        dpr = self.devicePixelRatioF()
        key = ("osu", self.level, i, hov, int(r.width()), int(r.height()), round(dpr, 2))
        pm = self._item_pm.get(key)
        if pm is not None:
            return pm
        if len(self._item_pm) > 40:
            self._item_pm.clear()
        h, w = r.height(), r.width()
        pad = h * 0.3
        pm = SK._pm(w + pad * 2, h + pad * 2, dpr)
        q = SK._painter(pm)
        sk = h * 0.22
        x, y = pad, pad
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(x, y), QPointF(x + w - sk, y), QPointF(x + w, y + h), QPointF(x, y + h)]))
        path.closeSubpath()
        q.fillPath(path.translated(0, h * 0.05), QColor(0, 0, 0, 70))
        g = QLinearGradient(x, 0, x + w, 0)
        if hov:
            g.setColorAt(0, QColor(222, 80, 150))
            g.setColorAt(1, QColor(255, 118, 182))
        else:
            g.setColorAt(0, QColor(104, 76, 204))
            g.setColorAt(1, QColor(146, 118, 240))
        q.fillPath(path, QBrush(g))
        hl = QLinearGradient(0, y, 0, y + h)
        hl.setColorAt(0, QColor(255, 255, 255, 50))
        hl.setColorAt(0.5, QColor(255, 255, 255, 0))
        q.fillPath(path, QBrush(hl))
        # подпись — сразу правее раскрытой печеньки (кнопка выезжает вместе с подписью)
        tx = pad + self.height() * 0.333 * 0.7 + h * 0.55
        lab = QRectF(tx, y + (h * 0.04 if hov else h * 0.1), w * 0.6, h * (0.62 if hov else 0.8))
        text(q, lab.translated(2, 2), name, h * 0.48, QColor(0, 0, 0, 90), QFont.Weight.DemiBold, italic=True,
             elide=False)
        text(q, lab, name, h * 0.48, QColor(255, 255, 255), QFont.Weight.DemiBold, italic=True, elide=False)
        if hov and sub:
            text(q, QRectF(tx, y + h * 0.64, w - (tx - x) - h * 0.2, h * 0.26), sub, h * 0.15,
                 QColor(255, 255, 255, 220), QFont.Weight.Normal, italic=True)
        _osu_icon(q, icon, QRectF(x + w - sk - h * 1.05, y + h * 0.18, h * 0.64, h * 0.64), QColor(255, 255, 255))
        q.end()
        self._item_pm[key] = pm
        return pm

    def _item_pix(self, i, it, r, hov):
        """Кнопка меню (скошенная плашка, значок, подпись) — картинкой, рисуется один раз."""
        if self.shell.osu_look():
            return self._item_pix_osu(i, it, r, hov)
        name, icon, col = it
        dpr = self.devicePixelRatioF()
        key = (self.level, i, hov, int(r.width()), int(r.height()), round(dpr, 2))
        pm = self._item_pm.get(key)
        if pm is None:
            if len(self._item_pm) > 40:
                self._item_pm.clear()
            sk = r.height() * 0.12
            w, h = r.width() + sk * 2 + 4, r.height() + 4
            pm = SK._pm(w, h, dpr)
            q = SK._painter(pm)
            rr = QRectF(sk + 2, 2, r.width(), r.height())
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(rr.left() + sk, rr.top()), QPointF(rr.right() + sk, rr.top()),
                                       QPointF(rr.right() - sk, rr.bottom()), QPointF(rr.left() - sk, rr.bottom())]))
            g = QLinearGradient(rr.topLeft(), rr.bottomLeft())
            g.setColorAt(0, SK.lighter(col, 0.25 if hov else 0.05))
            g.setColorAt(1, SK.darker(col, 0.15))
            q.fillPath(path, QBrush(g))
            if hov:
                q.strokePath(path, QPen(QColor(255, 255, 255, 200), 2))
            SK.draw_icon(q, icon, QRectF(rr.left(), rr.top() + rr.height() * 0.14, rr.width(), rr.height() * 0.4),
                         QColor(255, 255, 255))
            text(q, QRectF(rr.left(), rr.top() + rr.height() * 0.6, rr.width(), rr.height() * 0.3), name,
                 rr.height() * 0.12, QColor(255, 255, 255), QFont.Weight.Bold, Qt.AlignmentFlag.AlignCenter)
            q.end()
            self._item_pm[key] = pm
        return pm

    def _cookie_layers(self, R0):
        """Неподвижные слои «печеньки» (тень, белый обод, розовый круг, блик, надпись) — один раз на размер."""
        dpr = self.devicePixelRatioF()
        osu = self.shell.osu_look()
        key = (int(R0), round(dpr, 2), osu)
        c = self._ck.get(key)
        if c is not None:
            return c
        self._ck.clear()
        R = float(int(R0))
        S = R * 2.5
        base = SK._pm(S, S, dpr)
        q = SK._painter(base)
        cx = cy = S / 2
        sh = QRadialGradient(QPointF(cx, cy + R * 0.06), R * 1.12)
        sh.setColorAt(0.85, QColor(0, 0, 0, 120))
        sh.setColorAt(1, QColor(0, 0, 0, 0))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(QBrush(sh))
        q.drawEllipse(QPointF(cx, cy + R * 0.06), R * 1.12, R * 1.12)
        q.setBrush(QColor(255, 255, 255))
        q.drawEllipse(QPointF(cx, cy), R, R)
        g = QLinearGradient(cx, cy - R, cx, cy + R)
        if osu:                                         # как печенька osu!: ровный розовый
            g.setColorAt(0, QColor(252, 120, 172))
            g.setColorAt(1, QColor(236, 96, 154))
        else:
            g.setColorAt(0, QColor(255, 128, 190))
            g.setColorAt(1, QColor(230, 60, 140))
        q.setBrush(QBrush(g))
        Ri = R * 0.9
        q.drawEllipse(QPointF(cx, cy), Ri, Ri)
        q.end()
        top = SK._pm(S, S, dpr)
        q = SK._painter(top)
        if not osu:
            hl = QRadialGradient(QPointF(cx - Ri * 0.3, cy - Ri * 0.45), Ri * 0.9)
            hl.setColorAt(0, QColor(255, 255, 255, 60))
            hl.setColorAt(1, QColor(255, 255, 255, 0))
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QBrush(hl))
            q.drawEllipse(QPointF(cx, cy), Ri, Ri)
        if osu:                                         # крупная надпись с мягкой тенью
            path = SK.text_path(THEME_NAME, R * 0.66, QFont.Weight.Black)
            br = path.boundingRect()
            path.translate(cx - br.center().x(), cy - br.center().y() - R * 0.02)
            q.fillPath(path.translated(0, R * 0.025), QColor(0, 0, 0, 45))
            q.fillPath(path, QColor(255, 255, 255))
        else:
            q.setFont(_font(R * 0.52, QFont.Weight.Black))
            q.setPen(QColor(255, 255, 255))
            q.drawText(QRectF(cx - R, cy - R * 0.45, 2 * R, R * 0.8), Qt.AlignmentFlag.AlignCenter, THEME_NAME)
        q.end()
        mask = SK._pm(2 * Ri + 2, 2 * Ri + 2, dpr * 0.5)     # мягкий узор — в половинном разрешении
        q = SK._painter(mask)
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(QColor(255, 255, 255))
        q.drawEllipse(QPointF(Ri + 1, Ri + 1), Ri, Ri)
        q.end()
        tri = SK._pm(2 * Ri + 2, 2 * Ri + 2, dpr * 0.5)
        c = self._ck[key] = {"base": base, "top": top, "mask": mask, "tri": tri, "R": R, "Ri": Ri}
        return c

    def _draw_cookie(self, p, cx, cy, R):
        R0 = max(8.0, round(self.height() * (0.375 if self.shell.osu_look() else 0.25)))
        if R < R0 * 0.6:                               # маленькая печенька (в углу выбора песни) — свои слои
            last = self.__dict__.get("_ck_small")
            if last is None or not (R * 0.95 <= last <= R * 1.45):     # пульс и наведение — без пересборки
                last = max(8.0, float(int(R * 1.2 / 8 + 1) * 8))
                self._ck_small = last
            R0 = last
        L = self._cookie_layers(R0)
        sc = R / L["R"]
        blit(p, L["base"], cx, cy, sc, 1.0)
        # узор из треугольников внутри: рисуется в маленькую картинку и обрезается кругом (без пути отсечения);
        # двигается медленно (10–30 px/с) — перерисовывать чаще 40 раз в секунду незачем
        Ri = L["Ri"]
        tri = L["tri"]
        now_t = time.perf_counter()
        if now_t - L.get("tri_t", 0.0) < 0.025:
            blit(p, tri, cx, cy, sc, 1.0)
            blit(p, L["top"], cx, cy, sc, 1.0)
            return
        L["tri_t"] = now_t
        tri.fill(Qt.GlobalColor.transparent)
        q = QPainter(tri)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setPen(Qt.PenStyle.NoPen)
        rnd = random.Random(7)
        tt = time.perf_counter()
        c0 = Ri + 1
        for i in range(22):
            s = Ri * rnd.uniform(0.15, 0.5)
            x = c0 + rnd.uniform(-Ri, Ri)
            y = c0 + Ri - ((tt * rnd.uniform(10, 30) + rnd.uniform(0, 2 * Ri)) % (2 * Ri + s)) + s / 2
            q.setBrush(QColor(255, 255, 255, rnd.randint(10, 34)))
            q.drawPolygon(QPolygonF([QPointF(x, y - s * 0.58), QPointF(x - s / 2, y + s * 0.29),
                                     QPointF(x + s / 2, y + s * 0.29)]))
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.drawPixmap(0, 0, L["mask"])
        q.end()
        blit(p, tri, cx, cy, sc, 1.0)
        blit(p, L["top"], cx, cy, sc, 1.0)

    def _draw_toolbar(self, p, W, H, t):
        """Верхняя панель — картинкой: перерисовывается, только когда меняется её содержимое."""
        if self.shell.osu_look():
            return self._draw_now_playing(p, W, H, t)
        h = self._toolbar()
        playing = False
        try:
            playing = self.win.engine.is_playing()
        except Exception:                                             # noqa: BLE001
            pass
        now_s = f"{(t or {}).get('artist', '')} — {track_title(t)}" if t else "ничего не играет"
        hv = self.hover if self.hover and self.hover[0] == "tb" else None
        key = (int(W), playing, hv, now_s, time.strftime("%H:%M"), round(self.devicePixelRatioF(), 2))
        c = getattr(self, "_tb_cache", None)
        if c is None or c[0] != key:
            pm = SK._pm(W, h, self.devicePixelRatioF())
            q = SK._painter(pm)
            self._paint_toolbar(q, W, H, t)
            q.end()
            self._tb_cache = c = (key, pm)
        p.drawPixmap(0, 0, c[1])

    def _paint_toolbar(self, p, W, H, t):
        h = self._toolbar()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor(0, 0, 0, 200))
        g.setColorAt(1, QColor(0, 0, 0, 120))
        p.fillRect(QRectF(0, 0, W, h), QBrush(g))
        b = self._tb_buttons()
        playing = False
        try:
            playing = self.win.engine.is_playing()
        except Exception:                                             # noqa: BLE001
            pass
        for k, ico in (("settings", "settings"), ("home", "home"), ("artist", "solo"), ("prev", "prev"),
                       ("play", "pause" if playing else "play"), ("next", "next")):
            r = b[k]
            if self.hover == ("tb", k):
                rrect(p, r, 8, QColor(255, 255, 255, 30))
            c = r.center()
            SK.draw_icon(p, ico, QRectF(c.x() - h * 0.27, c.y() - h * 0.27, h * 0.54, h * 0.54), QColor(255, 255, 255))
        # заметная кнопка смены темы
        r = b["themes"]
        hov = self.hover == ("tb", "themes")
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor(255, 140, 195) if hov else QColor(255, 102, 170))
        g.setColorAt(1, QColor(225, 60, 140))
        rrect(p, r, r.height() / 2, QBrush(g), QPen(QColor(255, 255, 255, 230 if hov else 120), 1.5))
        text(p, r, "Сменить тему ▾", 14, QColor(255, 255, 255), QFont.Weight.Bold, Qt.AlignmentFlag.AlignCenter,
             elide=False)
        now_s = f"{(t or {}).get('artist', '')} — {track_title(t)}" if t else "ничего не играет"
        text(p, QRectF(W * 0.25, 0, W - 140 - 184 - W * 0.25, h), now_s, 14, QColor(255, 255, 255, 220),
             QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        text(p, QRectF(W - 130, 0, 118, h), time.strftime("%H:%M"), 15, QColor(255, 255, 255),
             QFont.Weight.Bold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _draw_user(self, p, W, H):
        st = self._stats or {}
        av_path = (getattr(self.win, "profile", {}) or {}).get("avatar") or ""
        key = (self.shell.player_name(), av_path, round(st.get("pp", 0)), round(st.get("acc", 0), 4),
               st.get("plays", 0), st.get("level", 1), round(st.get("frac", 0), 3), round(self.devicePixelRatioF(), 2))
        osu = self.shell.osu_look()
        key = key + (osu, int(H))
        c = getattr(self, "_user_cache", None)
        if c is None or c[0] != key:
            pm = SK._pm(max(360, H * 0.45) if osu else 360, self._toolbar() + (4 if osu else 110),
                        self.devicePixelRatioF())
            q = SK._painter(pm)
            (self._paint_user_osu if osu else self._paint_user)(q, W, H)
            q.end()
            self._user_cache = c = (key, pm)
        p.drawPixmap(0, 0, c[1])

    def _paint_user_osu(self, p, W, H):
        """Профиль слева сверху, как в osu!: квадратная аватарка, имя, Performance, Accuracy, Lv и полоса уровня."""
        st = self._stats or {}
        tb = self._toolbar()
        s = tb - 12
        ar = QRectF(6, 6, s, s)
        av = self._avatar_pm()
        if av is not None:
            p.drawPixmap(ar, av, QRectF(av.rect()))
        else:
            p.fillRect(ar, PINK)
            text(p, ar, (self.shell.player_name()[:1] or "?").upper(), s * 0.55, QColor(255, 255, 255),
                 QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(ar)
        x = ar.right() + H * 0.01
        fs = H * 0.019
        w = H * 0.32
        sh = QColor(0, 0, 0, 150)
        for i, (line, px) in enumerate(((self.shell.player_name(), H * 0.026),
                                        (f"Performance:{st.get('pp', 0):,.0f}pp".replace(",", " "), fs),
                                        (f"Accuracy:{st.get('acc', 0) * 100:.2f}%", fs))):
            y = [0.0, H * 0.033, H * 0.055][i] + 4
            r = QRectF(x, y, w, px * 1.35)
            text(p, r.translated(1.2, 1.2), line, px, sh, QFont.Weight.Normal)
            text(p, r, line, px, QColor(255, 255, 255), QFont.Weight.Normal)
        lv = st.get("level", 1)
        r = QRectF(x, H * 0.077 + 4, H * 0.06, fs * 1.35)
        text(p, r, f"Lv{lv}", fs, QColor(255, 255, 255), QFont.Weight.Normal)
        bar = QRectF(x + H * 0.055, tb - H * 0.022, H * 0.255, H * 0.009)
        rrect(p, bar, bar.height() / 2, QColor(0, 0, 0, 140), QPen(QColor(255, 255, 255, 60), 1))
        f = max(0.0, min(1.0, st.get("frac", 0)))
        if f > 0.01:
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            g.setColorAt(0, QColor(255, 220, 90))
            g.setColorAt(1, QColor(255, 160, 40))
            rrect(p, QRectF(bar.left(), bar.top(), bar.width() * f, bar.height()), bar.height() / 2, QBrush(g))

    def _paint_user(self, p, W, H):
        st = self._stats or {}
        x, y = 16, self._toolbar() + 14
        r = QRectF(x, y, 330, 86)
        rrect(p, r, 12, QColor(0, 0, 0, 120))
        av = self._avatar_pm()
        ar = QRectF(x + 10, y + 10, 66, 66)
        if av is not None:
            p.save()
            path = QPainterPath()
            path.addRoundedRect(ar, 12, 12)
            p.setClipPath(path)
            p.drawPixmap(ar, av, QRectF(av.rect()))
            p.restore()
        else:
            rrect(p, ar, 12, PINK)
            text(p, ar, (self.shell.player_name()[:1] or "?").upper(), 30, QColor(255, 255, 255), QFont.Weight.Black,
                 Qt.AlignmentFlag.AlignCenter)
        text(p, QRectF(x + 88, y + 8, 230, 24), self.shell.player_name(), 18, QColor(255, 255, 255), QFont.Weight.Bold)
        text(p, QRectF(x + 88, y + 32, 230, 18), f"Производительность: {st.get('pp', 0):,.0f}pp".replace(",", " "), 12,
             QColor(255, 255, 255, 200), QFont.Weight.Normal)
        text(p, QRectF(x + 88, y + 48, 230, 18), f"Точность: {st.get('acc', 0) * 100:.2f}%  ·  игр: {st.get('plays', 0)}",
             12, QColor(255, 255, 255, 200), QFont.Weight.Normal)
        lv = st.get("level", 1)
        text(p, QRectF(x + 88, y + 64, 50, 16), f"ур. {lv}", 11, QColor(255, 220, 120), QFont.Weight.Bold)
        rrect(p, QRectF(x + 140, y + 69, 176, 6), 3, QColor(255, 255, 255, 40))
        rrect(p, QRectF(x + 140, y + 69, 176 * st.get("frac", 0), 6), 3, QColor(255, 220, 120))

    def _avatar_pm(self):
        path = (getattr(self.win, "profile", {}) or {}).get("avatar") or ""
        if self._avatar is None or self._avatar[0] != path:
            pm = load_pixmap(path, 400) if path and os.path.exists(path) else None
            if pm is not None and pm.isNull():
                pm = None
            if pm is not None:
                pm = pm.scaled(132, 132, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
            self._avatar = (path, pm)
        return self._avatar[1]

    def _draw_volume(self, p, W, H, a):
        try:
            v = self.win.vol.value() / 100
        except Exception:                                             # noqa: BLE001
            v = 0.5
        c = QPointF(W - 110, H - 130)
        p.setOpacity(a)
        rrect(p, QRectF(c.x() - 80, c.y() - 80, 160, 160), 80, QColor(0, 0, 0, 170))
        p.setPen(QPen(QColor(255, 255, 255, 50), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - 60, c.y() - 60, 120, 120), 90 * 16, -360 * 16)
        p.setPen(QPen(PINK, 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(c.x() - 60, c.y() - 60, 120, 120), 90 * 16, int(-360 * 16 * v))
        text(p, QRectF(c.x() - 60, c.y() - 20, 120, 40), f"{int(v * 100)}%", 26, QColor(255, 255, 255),
             QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
        p.setOpacity(1.0)

    # ввод
    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        old = self.hover
        self.hover = None
        for k, r in self._tb_buttons().items():
            if r.contains(self.mouse):
                self.hover = ("tb", k)
        if self.ek > 0.5 and self.hover is None:
            for i, _it, r in self._items():
                if r.contains(self.mouse):
                    self.hover = ("item", i)
        if self.hover != old and self.hover is not None:
            self.sfx("hover", 0.5)
        self.last_input = time.perf_counter()

    def mousePressEvent(self, e):
        self.last_input = time.perf_counter()
        if self._skip_welcome():
            return
        pos = e.position()
        osu = self.shell.osu_look()
        for k, r in self._tb_buttons().items():
            if r.contains(pos):
                self.sfx("menuclick")
                eng = self.win.engine
                if k == "settings":
                    self.shell.toggle_settings()
                elif k == "home":
                    self.expanded = False
                    self.level = 0
                elif k == "themes":
                    self.shell.theme_menu(self.mapToGlobal((r.topLeft() if osu else r.bottomLeft()).toPoint()))
                elif k == "prev":
                    self.win.prev_track()
                elif k == "play":
                    if osu:                            # как в osu!: «играть» — играть (с начала, если стоял стоп)
                        if not eng.is_playing():
                            self.win.toggle_play()
                    else:
                        self.win.toggle_play()
                elif k == "pause":
                    if eng.is_playing():
                        self.win.toggle_play()
                elif k == "stop":
                    try:
                        if eng.is_playing():
                            self.win.toggle_play()
                        eng.seek(0.0)
                    except Exception:                                 # noqa: BLE001
                        pass
                elif k == "next":
                    self.win.next_track()
                elif k == "info":
                    t = self.shell.cur_track()
                    if t:
                        dur = float(t.get("duration") or 0)
                        self.shell.toast(f"{t.get('artist') or '—'} — {track_title(t)}" +
                                         (f"  ·  {fmt_time(dur * 1000)}" if dur else ""))
                elif k in ("list", "music"):
                    self.win._open_hub()
                elif k == "artist":
                    if e.button() == Qt.MouseButton.RightButton:
                        self.win.artist_shuffle_menu(self.mapToGlobal(r.topLeft().toPoint()))
                    else:
                        self.win.artist_shuffle()
                return
        cx, cy, R = self._cookie()
        if (pos.x() - cx) ** 2 + (pos.y() - cy) ** 2 <= R * R:
            if not self.expanded:
                self.expanded = True
                self.sfx("menuhit")
            else:
                self._choose(0)
            return
        if self.ek > 0.5:
            for i, _it, r in self._items():
                if r.contains(pos):
                    self._choose(i, self.mapToGlobal(r.bottomLeft().toPoint()))
                    return
            self.expanded = False
            self.level = 0

    def _set_level(self, lv):
        self.level = lv
        self.ek = min(self.ek, 0.35)                   # кнопки нового ряда выезжают заново, как в osu!
        self._item_pm = {}

    def _choose(self, i, gpos=None):
        self.sfx("menuhit")
        if self.level == 0:
            if i == 0:
                self._set_level(1)
            elif i == 1:
                self.shell.select.edit_mode = True     # «Редактор»: выбор песни, Enter открывает редактор
                self.shell.show_screen("select")
            elif i == 2:
                self.shell.toggle_settings()
            else:
                self.sfx("menuback")
                self.shell.leave_theme()
        else:
            if i == 0:
                self.shell.select.edit_mode = False
                self.shell.show_screen("select")
            elif i == 1:
                self.shell.osu_menu(gpos)
            elif i == 2:
                self.win._open_hub()
            else:
                self._set_level(0)

    def wheelEvent(self, e):
        d = 5 if e.angleDelta().y() > 0 else -5
        try:
            self.win.vol.setValue(max(0, min(100, self.win.vol.value() + d)))
        except Exception:                                             # noqa: BLE001
            pass
        self.vol_t = time.perf_counter()

    def keyPressEvent(self, e):
        k = e.key()
        self.last_input = time.perf_counter()
        if self._skip_welcome():
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_P):
            if not self.expanded:
                self.expanded = True
                self.sfx("menuhit")
            else:
                self._choose(0)
        elif k == Qt.Key.Key_Escape:
            if self.level == 1:
                self._set_level(0)
                self.sfx("menuback")
            elif self.expanded:
                self.expanded = False
                self.sfx("menuback")
        elif k == Qt.Key.Key_E and self.level == 0:
            self.expanded = True
            self._choose(1)
        elif k == Qt.Key.Key_O:
            self.shell.toggle_settings()
        elif k == Qt.Key.Key_Up:
            self.wheelEvent_like(5)
        elif k == Qt.Key.Key_Down:
            self.wheelEvent_like(-5)
        else:
            super().keyPressEvent(e)

    def wheelEvent_like(self, d):
        try:
            self.win.vol.setValue(max(0, min(100, self.win.vol.value() + d)))
        except Exception:                                             # noqa: BLE001
            pass
        self.vol_t = time.perf_counter()


# ── выбор песни ── #

class SelectScreen(Screen):
    """Выбор песни как в osu!: справа карусель (моя музыка + наборы карт из osu!), под выбранным —
    сложности; слева сведения о карте и рекорды; снизу — Назад / Моды / Случайно / Карта."""

    COLL_ALL, COLL_OSU, COLL_MINE = None, "__osu__", "__mine__"

    def __init__(self, shell):
        super().__init__(shell)
        self.tracks = []
        self.sel = 0
        self.diff = shell.S().get("diff", "hard")
        self.osu_diff = dict(shell.S().get("osu_diff") or {})       # набор → выбранная сложность (MD5)
        self.scroll = 0.0
        self.scroll_tgt = 0.0
        self.search = ""
        self.coll = shell.S().get("coll") if shell.S().get("coll") in (self.COLL_OSU, self.COLL_MINE) else None
        self.sort = "title"
        self.mods_open = False
        self.mk = 0.0
        self.hover = None
        self.mouse = QPointF(0, 0)
        self.thumbs = {}
        self._thumb_q = []
        self.bg = BgCache()
        self._preview_due = None
        self._drag = None
        self._scores = []
        self._scores_key = None
        self._lib_sig = None
        self._hays = {}
        self._req_due = None
        self._panels = {}                                  # кэш картинок панелей карусели
        self.edit_mode = False                             # вошли через «Редактор» главного меню
        self.mods = set(shell.S().get("mods", []))

    # данные
    @staticmethod
    def _norm(s) -> str:
        """Для поиска: регистр, «ё» = «е», знаки препинания не мешают («ac/dc» = «ac dc»)."""
        return " ".join(re.sub(r"[\W_]+", " ", str(s or "").lower().replace("ё", "е")).split())

    def _hay(self, t) -> str:
        key = str(t.get("path"))
        h = self._hays.get(key)
        if h is None:
            h = self._norm(f"{t.get('artist', '')} {track_title(t)} {t.get('title', '')} {t.get('album', '')} "
                           f"{Path(key).stem} {t.get('creator', '')} {t.get('tags', '')}")
            if SU._CYR.search(h):
                h += " " + SU.translit(h)              # «serega» находит «Серега»
            self._hays[key] = h
        return h

    def _source(self):
        w = self.win
        if self.coll is self.COLL_ALL or self.coll is None:
            return list(w.library) + OB.library_tracks()
        if self.coll == self.COLL_OSU:
            return OB.library_tracks()
        if self.coll == self.COLL_MINE:
            return list(w.library)
        return w.playlists.get(self.coll, {}).get("tracks", [])

    def _src_sig(self):
        return (len(self.win.library), self.coll, OB._idx_cache.get("sig"))

    def refresh(self):
        sig = self._src_sig()
        if sig != self._lib_sig:
            self._hays = {}
        src = self._source()
        # каждое слово запроса — где угодно в исполнителе/названии/альбоме/имени файла, в любом порядке;
        # и в другой раскладке («дштлшт» = «linkin»), и кириллицей вместо латиницы («линкин»)
        import osu_query
        query,filters=osu_query.parse(self.search)
        words = [SU.variants(wd) for wd in self._norm(query).split()]
        out = []
        seen = set()
        for t in src:
            p = t.get("path")
            if not p or p in seen:
                continue
            if filters:
                entry=self._osu_entry(t) if is_osu_track(t) else None
                if not osu_query.matches(t,filters,(entry or {}).get('diffs')):continue
            if words:
                h = self._hay(t)
                if not all(any(v in h for v in vs) for vs in words):
                    continue
            seen.add(p)
            out.append(t)
        key = {"title": lambda t: track_title(t).lower(), "artist": lambda t: (t.get("artist") or "").lower(),
               "length": lambda t: float(t.get("duration") or 0), "added": None}[self.sort]
        if key is not None:
            out.sort(key=key)
        cur = self.cur()
        self.tracks = out
        idx = None
        if cur is not None:
            idx = next((i for i, t in enumerate(out) if t.get("path") == cur.get("path")), None)
        if idx is not None:
            self.sel = idx
        elif words:
            self.sel = 0                               # выбранный трек отфильтрован — первый найденный
        else:
            self.sel = min(self.sel, max(0, len(out) - 1))
        self._lib_sig = sig

    def cur(self):
        return self.tracks[self.sel] if 0 <= self.sel < len(self.tracks) else None

    # ── сложности выбранного трека ── #
    def _osu_entry(self, t):
        return OB.set_by_id(t.get("esu_set")) if is_osu_track(t) else None

    def _diff_keys(self, t=None) -> list:
        t = self.cur() if t is None else t
        if is_osu_track(t):
            e = self._osu_entry(t)
            return [d["md5"] for d in (e or {}).get("diffs", [])] or ["?"]
        return list(DIFF_KEYS)

    def _cur_diff(self, t=None) -> str:
        t = self.cur() if t is None else t
        keys = self._diff_keys(t)
        if is_osu_track(t):
            k = self.osu_diff.get(t.get("esu_set"))
            if k in keys:
                return k
            # как в osu!: сложность, ближайшая по звёздам к той, что выбрана у сгенерированных
            want = {"easy": 1.6, "normal": 2.4, "hard": 3.4, "insane": 4.7, "expert": 6.0, "extra": 7.0,
                    "extreme": 8.3}.get(self.diff, 3.4)
            e = self._osu_entry(t)
            ds = (e or {}).get("diffs", [])
            if ds:
                return min(ds, key=lambda d: abs(d["stars"] - want))["md5"]
            return keys[0]
        return self.diff if self.diff in keys else keys[min(2, len(keys) - 1)]

    def _set_diff(self, key):
        t = self.cur()
        if is_osu_track(t):
            self.osu_diff[t.get("esu_set")] = key
        else:
            self.diff = key

    def _diff_meta(self, t, key) -> dict:
        """Название и звёзды сложности — даже пока карты не загружены (у osu! — из индекса)."""
        if is_osu_track(t):
            e = self._osu_entry(t)
            d = next((d for d in (e or {}).get("diffs", []) if d["md5"] == key), None)
            if d:
                return {"name": d["version"], "stars": d["stars"], "creator": d.get("creator", "")}
            return {"name": "?", "stars": 0.0}
        st = self.shell.sets.get(str(t.get("path")))
        m = st["data"]["maps"].get(key) if st and st.get("data") else None
        return {"name": G.diff_info(key)["name"], "stars": m["stars"] if m else None}

    def enter(self):
        super().enter()
        self.refresh()
        now_t = self.shell.cur_track()
        if now_t is not None:
            idx = next((i for i, t in enumerate(self.tracks) if t.get("path") == now_t.get("path")), None)
            if idx is not None:
                self.sel = idx
        self._select(self.sel, sound=False, preview=False)
        self.scroll = self.scroll_tgt
        QTimer.singleShot(0, self.setFocus)

    def leave(self):
        self.edit_mode = False
        self._panels = {}

    def _select(self, i, sound=True, preview=True, lazy=False):
        if not self.tracks:
            self._preview_due = self._req_due = None
            return
        i = max(0, min(len(self.tracks) - 1, i))
        changed = i != self.sel
        self.sel = i
        now = time.perf_counter()
        if lazy:
            # пока печатают в поиске — карту и превью не трогаем (раньше каждая буква
            # запускала анализ нового трека и прыжок музыки)
            self._req_due = now + 0.4
        else:
            # при быстрой прокрутке карта строится для того трека, на котором остановились
            self._req_due = now + (0.18 if changed else 0.0)
        if sound and changed:
            self.sfx("menuclick", 0.7)
        if preview:
            self._preview_due = now + (0.6 if lazy else 0.35)
        self._center()

    def _search_changed(self):
        self.refresh()
        self._select(self.sel, sound=False, preview=True, lazy=True)
        if not self.tracks:
            self.scroll_tgt = -self.height() / 2

    def _center(self):
        ph, dh, gap = self._sizes()
        keys = self._diff_keys()
        k = keys.index(self._cur_diff()) if self._cur_diff() in keys else 0
        y = self._y_track(self.sel) + ph + gap + k * (dh + gap)
        self.scroll_tgt = y - self.height() / 2 + dh / 2

    def _sizes(self):
        H = self.height()
        ph = max(62.0, H * 0.088)
        return ph, ph * 0.78, 6.0

    def _y_track(self, j):
        ph, dh, gap = self._sizes()
        y = j * (ph + gap)
        if j > self.sel:
            y += len(self._diff_keys()) * (dh + gap)
        return y

    def _layout(self):
        W, H = self.width(), self.height()
        top = H * 0.15
        bot = H * 0.1
        return W, H, top, bot

    def _entries(self):
        """Видимые панели: (вид, индекс/ключ, QRectF)."""
        W, H, top, bot = self._layout()
        ph, dh, gap = self._sizes()
        out = []
        n = len(self.tracks)
        if not n:
            return out
        keys = self._diff_keys()
        j0 = max(0, int((self.scroll - ph) / (ph + gap)) - len(keys) - 1)
        for j in range(j0, n):
            y = self._y_track(j) - self.scroll
            if y > H:
                break
            if y + ph >= 0:
                out.append(("track", j, self._rect(y, ph, j == self.sel)))
            if j == self.sel:
                for k, key in enumerate(keys):
                    yy = y + ph + gap + k * (dh + gap)
                    if -dh <= yy <= H:
                        out.append(("diff", key, self._rect(yy, dh, True, diff=True)))
        return out

    def _rect(self, y, h, sel, diff=False):
        W, H = self.width(), self.height()
        c = (y + h / 2 - H / 2) / H
        x = W * 0.5 + (c * c) * W * 0.28 - (W * 0.035 if sel else 0) + (W * 0.03 if diff else 0)
        return QRectF(x, y, W - x + 40, h)

    def step(self, dt, now):
        self.scroll += (self.scroll_tgt - self.scroll) * min(1.0, dt * 10)
        self.mk += ((1.0 if self.mods_open else 0.0) - self.mk) * min(1.0, dt * 12)
        if self._req_due and now >= self._req_due:
            self._req_due = None
            t = self.cur()
            if t is not None:
                self.shell.request_set(t)
        if self._preview_due and now >= self._preview_due and not self._req_due:
            self._preview_due = None
            t = self.cur()
            if t is not None:
                self.shell.preview(t)
        if self._lib_sig != self._src_sig():
            self.refresh()
        # миниатюры — по одной за кадр, декод в рабочем потоке (без рывков при прокрутке)
        for _ in range(3):
            if not self._thumb_q:
                break
            path = self._thumb_q.pop(0)
            if path in self.thumbs:
                continue
            self.thumbs[path] = "busy"
            ph, _, _ = self._sizes()
            self.shell.thumbs.load(path, int(ph * 1.8 * 2), int(ph * 2), self._thumb_ready)
            # в памяти — только недавние миниатюры (вся библиотека — это сотни мегабайт картинок)
            if len(self.thumbs) > 160:
                for k in list(self.thumbs)[:32]:
                    if self.thumbs[k] != "busy":
                        del self.thumbs[k]

    def _thumb_ready(self, path, img):
        pm = QPixmap.fromImage(img) if img is not None and not img.isNull() else None
        self.thumbs[path] = pm
        self._panels = {k: v for k, v in self._panels.items() if k[1] != path}

    def _thumb(self, t):
        c = self.shell.cover_of(t)
        if not c:
            return None
        v = self.thumbs.get(c)
        if v is None and c not in self.thumbs:
            if c not in self._thumb_q:
                self._thumb_q.append(c)
            return None
        return v if isinstance(v, QPixmap) else None

    def _set(self):
        return self.shell.sets.get(str((self.cur() or {}).get("path", "")))

    def _map(self):
        st = self._set()
        if st and st.get("data"):
            return st["data"]["maps"].get(self._cur_diff())
        return None

    # отрисовка
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H, top, bot = self._layout()
        t = self.cur()
        draw_bg(p, self.bg.get(self.shell.bg_path(t), W, H, self.devicePixelRatioF(), blur=True), W, H, 0.45,
                cache=self.bg)
        self._draw_carousel(p, W, H)
        self._draw_info(p, W, H, top)
        self._draw_scores(p, W, H, top, bot)
        self._draw_bottom(p, W, H, bot)
        if self.mk > 0.01:
            self._draw_mods(p, W, H)
        p.end()

    def _panel(self, key, w, h, build):
        """Панель карусели целиком в картинке: текст и миниатюра рисуются один раз, кадр — один блит."""
        k = key + (int(w), int(h), round(self.devicePixelRatioF(), 2))
        pm = self._panels.get(k)
        if pm is None:
            if len(self._panels) > 90:
                for kk in list(self._panels)[:30]:
                    del self._panels[kk]
            pm = SK._pm(w, h, self.devicePixelRatioF())
            q = SK._painter(pm)
            build(q, QRectF(0, 0, w, h))
            q.end()
            self._panels[k] = pm
        return pm

    def _draw_carousel(self, p, W, H):
        ph, dh, gap = self._sizes()
        cur = self.cur()
        st = self._set()
        # ширина панелей постоянна (правый край всё равно за окном) — картинки не пересоздаются,
        # пока карусель едет по дуге
        pw = int(W * 0.56 + 48)
        for kind, key, r in self._entries():
            r = QRectF(r.left(), r.top(), pw, r.height())
            if kind == "track":
                t = self.tracks[key]
                sel = key == self.sel
                hov = self.hover == ("track", key)
                dots = ()
                if is_osu_track(t):
                    e = self._osu_entry(t)
                    dots = tuple(round(d["stars"], 1) for d in (e or {}).get("diffs", [])[:12])
                else:
                    s2 = self.shell.sets.get(str(t.get("path")))
                    if s2 and s2.get("data"):
                        dots = tuple(round(s2["data"]["maps"][k2]["stars"], 1) for k2 in DIFF_KEYS
                                     if s2["data"]["maps"].get(k2))
                th = self._thumb(t)
                pk = ("track", self.shell.cover_of(t), str(t.get("path")), sel, hov, dots, th is not None)
                pm = self._panel(pk, r.width(), r.height(),
                                 lambda q, rr, t=t, sel=sel, hov=hov, dots=dots, th=th: self._paint_track(
                                     q, rr, t, sel, hov, dots, th, W))
                p.drawPixmap(QPointF(round(r.left()), round(r.top())), pm)
            else:
                t = cur
                sel = key == self._cur_diff()
                hov = self.hover == ("diff", key)
                meta = self._diff_meta(t, key)
                state = ""
                if meta.get("stars") is None and st:
                    if st.get("state") == "busy":
                        f, s = st.get("prog", (0.0, ""))
                        state = f"{s} {int(f * 100)}%"
                    elif st.get("state") == "error":
                        state = "не удалось: " + st.get("err", "")
                    elif st.get("state") == "idle":
                        state = "карты нет — «Сгенерировать карту» (F4)"
                pk = ("diff", str(t.get("path")), key, sel, hov, meta.get("name"), meta.get("stars"), state)
                pm = self._panel(pk, r.width(), r.height(),
                                 lambda q, rr, meta=meta, sel=sel, hov=hov, state=state, t=t: self._paint_diff(
                                     q, rr, meta, sel, hov, state, t, W))
                p.drawPixmap(QPointF(round(r.left()), round(r.top())), pm)

    def _paint_track(self, p, r, t, sel, hov, dots, th, W):
        ph = r.height()
        g = QLinearGradient(r.topLeft(), r.topRight())
        base = QColor(30, 22, 40, 230) if not sel else QColor(60, 40, 75, 240)
        g.setColorAt(0, SK.lighter(base, 0.08) if hov else base)
        g.setColorAt(1, QColor(base.red(), base.green(), base.blue(), 120))
        pen = QPen(QColor(255, 255, 255, 230 if sel else 60), 2 if sel else 1)
        rrect(p, r.adjusted(1, 1, -1, -1), 10, QBrush(g), pen)
        tw = ph * 1.8
        if th is not None:
            p.save()
            path = QPainterPath()
            path.addRoundedRect(QRectF(r.left() + 1, r.top() + 1, tw, r.height() - 2), 10, 10)
            p.setClipPath(path)
            p.setOpacity(0.9)
            tr = QRectF(r.left(), r.top(), tw, r.height())
            p.drawPixmap(tr, th, QRectF(0, 0, th.width(), th.height()))
            gg = QLinearGradient(tr.topLeft(), tr.topRight())
            gg.setColorAt(0.5, QColor(0, 0, 0, 0))
            gg.setColorAt(1, QColor(base.red(), base.green(), base.blue(), 255))
            p.fillRect(tr, QBrush(gg))
            p.restore()
            p.setOpacity(1.0)
        x = r.left() + (tw * 0.75 if th is not None else 16)
        tw_ = r.width() - x - 24 - (W * 0.0)
        text(p, QRectF(x, r.top() + r.height() * 0.1, tw_, r.height() * 0.42), track_title(t),
             r.height() * 0.27, QColor(255, 255, 255), QFont.Weight.Bold)
        art = t.get("artist") or "—"
        by = (t.get("creator") or "osu!") if is_osu_track(t) else "ECHOES AI"
        text(p, QRectF(x, r.top() + r.height() * 0.5, tw_, r.height() * 0.3),
             f"{art}  //  {by}", r.height() * 0.18, QColor(255, 255, 255, 190), QFont.Weight.Normal)
        xx = x
        for s in dots:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(star_color(s))
            p.drawEllipse(QPointF(xx + 6, r.bottom() - r.height() * 0.13), 5, 5)
            xx += 15

    def _paint_diff(self, p, r, meta, sel, hov, state, t, W):
        s = meta.get("stars")
        col = star_color(s) if s is not None else QColor(120, 120, 140)
        base = QColor(255, 255, 255, 235) if sel else QColor(20, 16, 28, 225)
        if hov and not sel:
            base = QColor(40, 32, 55, 235)
        rrect(p, r.adjusted(1, 1, -1, -1), 9, base, QPen(PINK if sel else QColor(255, 255, 255, 50), 2 if sel else 1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(r.left() + 26, r.center().y()), r.height() * 0.24, r.height() * 0.24)
        fg = QColor(20, 20, 30) if sel else QColor(255, 255, 255)
        name = meta.get("name") or "?"
        text(p, QRectF(r.left() + 52, r.top() + 4, min(W * 0.36, r.width() - 70), r.height() * 0.5), name,
             r.height() * 0.26, fg, QFont.Weight.Bold)
        if s is not None:
            text(p, QRectF(r.left() + 52, r.top() + r.height() * 0.5, 120, r.height() * 0.42), f"★ {s:.2f}",
                 r.height() * 0.22, col if not sel else SK.darker(col, 0.25), QFont.Weight.Bold)
            for i in range(min(10, int(math.ceil(s)))):
                f = min(1.0, s - i)
                rad = r.height() * 0.11 * (0.45 + 0.55 * f)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 200))
                p.drawPolygon(SK.star_shape(r.left() + 190 + i * r.height() * 0.3, r.top() + r.height() * 0.71,
                                            rad, rad * 0.45, 5, -math.pi / 2))
        elif state:
            text(p, QRectF(r.left() + 52, r.top() + r.height() * 0.5, W * 0.4, r.height() * 0.42), state,
                 r.height() * 0.2, QColor(255, 120, 120) if state.startswith("не удалось") else
                 QColor(fg.red(), fg.green(), fg.blue(), 200), QFont.Weight.Normal)

    def _draw_info(self, p, W, H, top):
        t = self.cur()
        g = QLinearGradient(0, 0, 0, top)
        g.setColorAt(0, QColor(0, 0, 0, 210))
        g.setColorAt(1, QColor(0, 0, 0, 140))
        p.fillRect(QRectF(0, 0, W, top), QBrush(g))
        if t is None:
            # строка поиска и выбор коллекции остаются — иначе не видно, что напечатано, и не вернуться
            text(p, QRectF(20, 10, W * 0.6, 40), self.empty_text(), 20)
            self._draw_search(p, W, top)
            return
        m = self._map()
        key = self._cur_diff()
        meta = self._diff_meta(t, key)
        text(p, QRectF(20, 8, W * 0.62, top * 0.34), f"{t.get('artist') or '—'} - {track_title(t)} [{meta['name']}]",
             top * 0.22, QColor(255, 255, 255), QFont.Weight.Bold)
        if is_osu_track(t):
            who = meta.get("creator") or t.get("creator") or "—"
            sub = f"Карта: {who}  ·  из osu!"
        else:
            seg = self.shell.segment_of(t)
            sub = "Карта создана моделью ECHOES (по ударам и частям песни)" + (
                f"  ·  отрывок {fmt_time(seg[0])}–{fmt_time(seg[1])}" if seg else "")
            if m is None and (self._set() or {}).get("state") != "busy":
                sub = "Карты ещё нет — нажмите «Сгенерировать карту» (F4): весь трек или только отрывок"
            if self.edit_mode:
                sub = "Режим редактора: Enter или щелчок по сложности откроет редактор карты"
        text(p, QRectF(20, top * 0.36, W * 0.6, top * 0.18), sub, top * 0.11, QColor(255, 255, 255, 190),
             QFont.Weight.Normal)
        if m:
            rate = 1.5 if (self.mods & {"DT", "NC"}) else 0.75 if "HT" in self.mods else 1.0
            cs, ar, od, hp = G.apply_mods(m["cs"], m["ar"], m["od"], m["hp"], self.mods)
            ar2, od2 = G.effective_ar_od(ar, od, rate)
            n = m["n_circles"] + m["n_sliders"] + m["n_spinners"]
            bpm = f"{m['bpm'] * rate:.0f}"
            if m.get("bpm_min") and m.get("bpm_max") and abs(m["bpm_max"] - m["bpm_min"]) > 0.5:
                bpm = f"{m['bpm_min'] * rate:.0f}-{m['bpm_max'] * rate:.0f} ({m['bpm'] * rate:.0f})"
            text(p, QRectF(20, top * 0.54, W * 0.6, top * 0.18),
                 f"Длина: {fmt_time(m['length'] / rate)}   BPM: {bpm}   Объектов: {n}   "
                 f"Макс. комбо: {m.get('max_combo', 0)}x", top * 0.12, QColor(255, 255, 255), QFont.Weight.Bold)
            text(p, QRectF(20, top * 0.72, W * 0.6, top * 0.18),
                 f"Кругов: {m['n_circles']}   Слайдеров: {m['n_sliders']}   Спиннеров: {m['n_spinners']}   ·   "
                 f"CS {cs:.1f}  AR {ar2:.1f}  OD {od2:.1f}  HP {hp:.1f}   ★ {m['stars']:.2f}",
                 top * 0.11, QColor(255, 255, 255, 210), QFont.Weight.Normal)
        else:
            st = self._set()
            if st and st.get("state") == "busy":
                f, s = st.get("prog", (0.0, ""))
                label = "Открываю карту" if is_osu_track(t) else "Модель слушает трек"
                text(p, QRectF(20, top * 0.56, W * 0.6, top * 0.2), f"{label}: {s}", top * 0.12)
                rrect(p, QRectF(20, top * 0.8, W * 0.3, 6), 3, QColor(255, 255, 255, 40))
                rrect(p, QRectF(20, top * 0.8, W * 0.3 * f, 6), 3, PINK)
        self._draw_search(p, W, top)

    def empty_text(self) -> str:
        if self.search.strip():
            return f"Ничего не найдено по «{self.search.strip()}» — Esc очистит поиск"
        if self.coll == self.COLL_OSU:
            return "Карт из osu! пока нет — F3 → «Импорт карт osu!» или перетащите .osz в окно"
        if self.coll not in (None, self.COLL_MINE):
            return f"В «{self.coll}» нет треков — выберите другую коллекцию справа"
        return "Библиотека пуста — добавьте музыку (кнопка «Музыка» в меню) или карты osu!"

    def _coll_label(self) -> str:
        return {None: "всё", self.COLL_OSU: "карты osu!", self.COLL_MINE: "моя музыка"}.get(self.coll, self.coll)

    def _draw_search(self, p, W, top):
        # справа: поиск, сортировка, коллекция
        self._btn_rects = {}
        r = QRectF(W * 0.64, 10, W * 0.34, top * 0.3)
        rrect(p, r, 8, QColor(0, 0, 0, 150), QPen(QColor(255, 255, 255, 60), 1))
        text(p, r.adjusted(12, 0, -12, 0), ("Поиск: " + self.search + ("▏" if int(time.perf_counter() * 2) % 2 else ""))
             if self.search else "Печатайте для поиска…", top * 0.12,
             QColor(255, 255, 255, 230 if self.search else 140), QFont.Weight.Normal)
        sort_names = {"title": "по названию", "artist": "по исполнителю", "length": "по длине", "added": "по добавлению"}
        r1 = QRectF(W * 0.64, top * 0.5, W * 0.165, top * 0.3)
        r2 = QRectF(W * 0.64 + W * 0.175, top * 0.5, W * 0.165, top * 0.3)
        for rr, label, key in ((r1, "Сортировка: " + sort_names[self.sort] + " ▾", "sort"),
                               (r2, "Коллекция: " + self._coll_label() + " ▾", "coll")):
            hov = self.hover == ("btn", key)
            rrect(p, rr, 8, QColor(255, 102, 170, 160 if hov else 90))
            text(p, rr.adjusted(10, 0, -8, 0), label, top * 0.1, QColor(255, 255, 255), QFont.Weight.DemiBold)
            self._btn_rects[key] = rr
        text(p, QRectF(W * 0.64, top * 0.82, W * 0.34, top * 0.16), n_tracks(len(self.tracks)), top * 0.09,
             QColor(255, 255, 255, 160), QFont.Weight.Normal, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _draw_scores(self, p, W, H, top, bot):
        m = self._map()
        x, y, w = 16, top + 14, W * 0.4
        # фон-клип (у карт osu! — свой фон и видео карты)
        t = self.cur()
        if t is not None and not is_osu_track(t):
            # генерация карты — только по кнопке (листание ничего не строит)
            busy = (self._set() or {}).get("state") == "busy"
            rg = QRectF(x, y, w, 44 if m is None else 32)
            hov = self.hover == ("btn", "gen")
            if m is None:
                rrect(p, rg, 10, QColor(255, 102, 170, 120 if busy else (255 if hov else 220)))
                label = "Карта строится…" if busy else "Сгенерировать карту  F4"
                text(p, rg, label, 17, QColor(255, 255, 255), QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
            else:
                rrect(p, rg, 8, QColor(255, 102, 170, 90 if hov else 50))
                text(p, rg, "Сгенерировать заново / выбрать отрывок  F4", 13, QColor(255, 255, 255, 230),
                     QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignCenter)
            if not busy:
                self._btn_rects["gen"] = rg
            y += rg.height() + 10
        r = QRectF(x, y, w, 34)
        S = self.shell.S()
        if is_osu_track(t):
            mm = m or {}
            msg = ("У карты есть видео — " + ("оно будет фоном" if S.get("map_video", True) else "выключено в настройках")
                   ) if mm.get("video") else "Фон карты — из её папки osu!"
            key = "mapvideo"
        else:
            ent = self.shell.clip_entry(t) if t else None
            if ent and (ent.get("path") or ent.get("stream")):
                msg = ("Клип скачан — " if ent.get("path") else "Клип онлайн — ") + (
                    "он будет фоном карты" if S.get("bg") == "clip" else "фон: обложка (смените в настройках)")
                key = "bgmode"
            else:
                svc = self.shell.clip_state(t)
                msg = svc or "Скачать клип песни для фона карты"
                key = "clipdl"
        hov = self.hover == ("btn", key)
        rrect(p, r, 8, QColor(255, 255, 255, 40 if hov else 22))
        text(p, r.adjusted(12, 0, -8, 0), msg, 13, QColor(255, 255, 255, 220), QFont.Weight.DemiBold)
        self._btn_rects[key] = r
        y += 46
        # попытки на этой сложности — щелчок открывает «Попытки по картам»
        mk = map_key(m) if m else None
        if mk != getattr(self, "_attempts_key", None):
            self._attempts_key = mk
            self._attempts = load_attempts().get(mk, {}) if mk else {}
        if m:
            ra = QRectF(x, y, w, 30)
            hov = self.hover == ("btn", "attempts")
            rrect(p, ra, 8, QColor(255, 102, 170, 70 if hov else 38))
            text(p, ra.adjusted(12, 0, -110, 0), attempts_line(self._attempts), 13, QColor(255, 255, 255, 230),
                 QFont.Weight.DemiBold)
            text(p, ra.adjusted(0, 0, -12, 0), "все карты", 12, QColor(255, 179, 212), QFont.Weight.Bold,
                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._btn_rects["attempts"] = ra
            y += 40
        text(p, QRectF(x, y, w, 26), "Лучшие результаты (на этом компьютере)", 15, QColor(255, 255, 255, 220),
             QFont.Weight.Bold)
        y += 32
        key = map_key(m) if m else None
        if key != self._scores_key:
            self._scores_key = key
            self._scores = load_scores().get(key, []) if key else []
        if not self._scores:
            text(p, QRectF(x, y, w, 30), "Пока нет — сыграйте первым!" if m else "", 13, QColor(255, 255, 255, 140),
                 QFont.Weight.Normal)
        self._score_rects = []
        rows = max(1, int((H - bot - y - 10) // 52))
        for i, s in enumerate(self._scores[:min(8, rows)]):
            rr = QRectF(x, y + i * 52, w, 46)
            hov = self.hover == ("score", i)
            rrect(p, rr, 8, QColor(0, 0, 0, 150 if not hov else 200), QPen(QColor(255, 255, 255, 40), 1))
            gp = self.shell.grade_pix(s["grade"], 30, self.devicePixelRatioF())
            blit(p, gp, rr.left() + 28, rr.center().y(), 1.0, 1.0)
            text(p, QRectF(rr.left() + 52, rr.top() + 3, w * 0.5, 22), s.get("player") or "игрок", 14,
                 QColor(255, 255, 255), QFont.Weight.Bold)
            text(p, QRectF(rr.left() + 52, rr.top() + 23, w * 0.6, 20),
                 f"Счёт {s['score']:,}  ({s['combo']}x)".replace(",", " ") + ("  " + "".join(s["mods"]) if s["mods"] else ""),
                 12, QColor(255, 255, 255, 190), QFont.Weight.Normal)
            text(p, QRectF(rr.right() - 160, rr.top(), 150, 46), f"{s['acc'] * 100:.2f}%  ·  {s.get('pp', 0):.0f}pp", 13,
                 QColor(255, 220, 120), QFont.Weight.Bold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._score_rects.append(rr)

    def _bottom_buttons(self):
        W, H, top, bot = self._layout()
        y = H - bot
        h = bot * 0.62
        yy = y + (bot - h) / 2
        return {"back": QRectF(0, yy, 150, h), "mods": QRectF(170, yy, 130, h), "random": QRectF(310, yy, 130, h),
                "options": QRectF(450, yy, 150, h), "themes": QRectF(610, yy, 150, h),
                "artist": QRectF(770, yy, 170, h),
                "play": QRectF(W - bot * 1.6, H - bot * 1.5, bot * 1.4, bot * 1.4)}

    def _draw_bottom(self, p, W, H, bot):
        g = QLinearGradient(0, H - bot, 0, H)
        g.setColorAt(0, QColor(0, 0, 0, 160))
        g.setColorAt(1, QColor(0, 0, 0, 220))
        p.fillRect(QRectF(0, H - bot, W, bot), QBrush(g))
        b = self._bottom_buttons()
        for key, label, col in (("back", "Назад", PINK), ("mods", "Моды  F1", QColor(150, 90, 230)),
                                ("random", "Случайно  F2", QColor(240, 180, 40)),
                                ("options", "Карта…  F3", QColor(50, 150, 230)),
                                ("themes", "Темы", QColor(230, 60, 140)),
                                ("artist", "Исполнитель", QColor(40, 190, 150))):
            r = b[key]
            hov = self.hover == ("bottom", key)
            path = QPainterPath()
            sk = r.height() * 0.25
            path.addPolygon(QPolygonF([QPointF(r.left(), r.top()), QPointF(r.right() + sk, r.top()),
                                       QPointF(r.right() - sk * 0.2, r.bottom()), QPointF(r.left(), r.bottom())]))
            p.fillPath(path, SK.lighter(col, 0.2) if hov else col)
            text(p, r.adjusted(14, 0, 0, 0), label, r.height() * 0.28, QColor(255, 255, 255), QFont.Weight.Bold)
        x = b["artist"].right() + 30
        for m in sorted(self.mods):
            pm = self.shell.mod_pix(m, bot * 0.42, self.devicePixelRatioF())
            p.drawPixmap(QPointF(x, H - bot / 2 - bot * 0.24), pm)
            x += pm.width() / pm.devicePixelRatio() + 4
        # печенька «играть»
        r = b["play"]
        hov = self.hover == ("bottom", "play")
        c = r.center()
        R = r.width() / 2 * (1.07 if hov else 1.0) * (1 + 0.03 * self.shell.pulse.beat)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(c, R, R)
        gg = QLinearGradient(c.x(), c.y() - R, c.x(), c.y() + R)
        gg.setColorAt(0, QColor(255, 128, 190))
        gg.setColorAt(1, QColor(230, 60, 140))
        p.setBrush(QBrush(gg))
        p.drawEllipse(c, R * 0.88, R * 0.88)
        text(p, QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), THEME_NAME, R * 0.48, QColor(255, 255, 255),
             QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter, elide=False)

    def _mod_rects(self):
        W, H = self.width(), self.height()
        out = []
        y0 = H * 0.42 + (1 - self.mk) * H * 0.6
        size = min(64.0, H * 0.065)
        for ri, (_title, mods) in enumerate(MOD_ROWS):
            y = y0 + 60 + ri * (size + 46)
            for mi, m in enumerate(mods):
                out.append((m, QRectF(W * 0.08 + 230 + mi * (size * 1.25 + 26), y, size * 1.25 + 4, size + 4)))
        return out, y0, size

    def _draw_mods(self, p, W, H):
        rects, y0, size = self._mod_rects()
        p.setOpacity(self.mk)
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, 120))
        rrect(p, QRectF(W * 0.05, y0, W * 0.9, H * 0.52), 18, QColor(24, 18, 32, 245),
              QPen(QColor(255, 255, 255, 60), 1))
        text(p, QRectF(W * 0.08, y0 + 12, W * 0.5, 40), "Модификаторы", 26, QColor(255, 255, 255), QFont.Weight.Black)
        text(p, QRectF(W * 0.5, y0 + 12, W * 0.42, 40), f"Множитель очков: ×{mods_mult(self.mods):.2f}", 18,
             QColor(255, 220, 120), QFont.Weight.Bold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for ri, (title, _mods) in enumerate(MOD_ROWS):
            y = y0 + 60 + ri * (size + 46)
            text(p, QRectF(W * 0.08, y, 220, size), title, 15, QColor(255, 255, 255, 200), QFont.Weight.DemiBold)
        for m, r in rects:
            on = m in self.mods
            pm = self.shell.mod_pix(m, size, self.devicePixelRatioF(), on)
            hov = self.hover == ("mod", m)
            blit(p, pm, r.center().x(), r.center().y() - (6 if hov or on else 0), 1.08 if hov else 1.0, self.mk)
            text(p, QRectF(r.left() - 20, r.bottom() + 2, r.width() + 40, 18), MOD_NAMES.get(m, m), 11,
                 QColor(255, 255, 255, 220 if on else 140), QFont.Weight.Normal, Qt.AlignmentFlag.AlignCenter)
        rb = QRectF(W * 0.95 - 330, y0 + H * 0.52 - 64, 150, 44)
        rc = QRectF(W * 0.95 - 170, y0 + H * 0.52 - 64, 150, 44)
        self._mods_btns = {"reset": rb, "close": rc}
        for r, label, col in ((rb, "Сбросить", QColor(240, 80, 90)), (rc, "Закрыть", PINK)):
            rrect(p, r, 22, col)
            text(p, r, label, 16, QColor(255, 255, 255), QFont.Weight.Bold, Qt.AlignmentFlag.AlignCenter)
        p.setOpacity(1.0)

    # ввод
    def _hit(self, pos):
        if self.mods_open:
            rects, _, _ = self._mod_rects()
            for m, r in rects:
                if r.contains(pos):
                    return ("mod", m)
            for k, r in getattr(self, "_mods_btns", {}).items():
                if r.contains(pos):
                    return ("modbtn", k)
            return ("modbg", None)
        for k, r in self._bottom_buttons().items():
            if r.contains(pos):
                return ("bottom", k)
        for k, r in getattr(self, "_btn_rects", {}).items():
            if r.contains(pos):
                return ("btn", k)
        for i, r in enumerate(getattr(self, "_score_rects", [])):
            if r.contains(pos):
                return ("score", i)
        for kind, key, r in reversed(self._entries()):
            if r.contains(pos):
                return (kind, key)
        return None

    def mouseMoveEvent(self, e):
        self.mouse = e.position()
        if self._drag is not None:
            dy = e.position().y() - self._drag[0]
            if abs(dy) > 6:
                self._drag[2] = True
            if self._drag[2]:
                self.scroll_tgt = self.scroll = self._drag[1] - dy
                return
        old = self.hover
        self.hover = self._hit(self.mouse)
        if self.hover != old and self.hover is not None and self.hover[0] in ("bottom", "diff", "mod", "btn"):
            self.sfx("hover", 0.4)

    def mousePressEvent(self, e):
        pos = e.position()
        h = self._hit(pos)
        if e.button() == Qt.MouseButton.RightButton and not self.mods_open:
            self._drag = [pos.y(), self.scroll, True]
            self._rmb = (pos, h)
            return
        if h is None:
            if pos.x() > self.width() * 0.5:
                self._drag = [pos.y(), self.scroll, False]
            return
        kind, key = h
        if kind in ("track", "diff"):
            self._drag = [pos.y(), self.scroll, False]
            self._press_hit = h
            return
        self._click(h)

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        rmb, self._rmb = getattr(self, "_rmb", None), None
        if d is not None and d[2]:
            if rmb is not None and rmb[1] is not None and rmb[1][0] == "track" and \
                    (e.position() - rmb[0]).manhattanLength() < 6:
                self._track_menu(rmb[1][1])                # правый клик на месте — меню трека
                return
            # отпустили после прокрутки — выбрать ближайший к центру трек
            self._snap_to_center()
            return
        h = getattr(self, "_press_hit", None)
        self._press_hit = None
        if h is not None and self._hit(e.position()) == h:
            self._click(h)

    def _track_menu(self, i):
        if not 0 <= i < len(self.tracks):
            return
        from PyQt6.QtGui import QCursor
        t = self.tracks[i]
        m = QMenu(self)
        m.addAction("Выбрать", lambda: self._select(i))
        self.win.reveal_action(m, t)
        m.addAction("Теги, обложка и текст…", lambda: self.win.edit_track_tags(t))
        m.exec(QCursor.pos())

    def _snap_to_center(self):
        ph, dh, gap = self._sizes()
        mid = self.scroll + self.height() / 2
        best = min(range(len(self.tracks)), key=lambda j: abs(self._y_track(j) + ph / 2 - mid), default=None)
        if best is not None and best != self.sel:
            self._select(best)
        else:
            self._center()

    def _click(self, h):
        kind, key = h
        if kind == "track":
            if key == self.sel:
                self._center()
            else:
                self._select(key)
        elif kind == "diff":
            if key == self._cur_diff():
                self.open_editor() if self.edit_mode else self.start_play()
            else:
                self._set_diff(key)
                self.sfx("menuclick", 0.7)
                self._center()
                self._save_prefs()
        elif kind == "bottom":
            self.sfx("menuclick")
            if key == "back":
                self.shell.show_screen("menu")
            elif key == "mods":
                self.mods_open = True
            elif key == "random":
                self._random()
            elif key == "options":
                self._options_menu()
            elif key == "themes":
                self.shell.theme_menu()
            elif key == "artist":                    # шаффл по исполнителю выбранной песни
                t = self.cur()
                self.win.artist_shuffle((self.win._artist_names((t or {}).get("artist")) or [None])[0])
            elif key == "play":
                self.open_editor() if self.edit_mode else self.start_play()
        elif kind == "btn":
            if key == "sort":
                self._sort_menu()
            elif key == "coll":
                self._coll_menu()
            elif key == "gen":
                self.open_generate()
            elif key == "clipdl":
                self.shell.download_clip(self.cur())
            elif key == "attempts":
                self.shell.attempts_dialog()
            elif key == "bgmode":
                S = self.shell.S()
                S["bg"] = "cover" if S.get("bg") == "clip" else "clip"
                self.shell.save_settings()
            elif key == "mapvideo":
                S = self.shell.S()
                S["map_video"] = not S.get("map_video", True)
                self.shell.save_settings()
        elif kind == "score":
            s = self._scores[key]
            rp = load_replay(s.get("id", ""))
            if rp is None:
                self.shell.toast("Повтор этого результата не сохранился")
            else:
                self.start_play(replay=rp)
        elif kind == "mod":
            self._toggle_mod(key)
        elif kind == "modbtn":
            if key == "reset":
                self.mods.clear()
                self.sfx("menuback")
                self._save_prefs()
            else:
                self.mods_open = False
        elif kind == "modbg":
            self.mods_open = False

    def _toggle_mod(self, m):
        if m in self.mods:
            self.mods.discard(m)
        else:
            for grp in MOD_EXCL:
                if m in grp:
                    self.mods -= grp - {m}
            self.mods.add(m)
        self.sfx("menuhit", 0.6)
        self._save_prefs()

    def _save_prefs(self):
        S = self.shell.S()
        S["mods"] = sorted(self.mods)
        S["diff"] = self.diff
        S["osu_diff"] = dict(list(self.osu_diff.items())[-400:])
        S["coll"] = self.coll if self.coll in (self.COLL_OSU, self.COLL_MINE) else None
        self.shell.save_settings()

    def _random(self):
        if self.tracks:
            self._random_previous=(self.cur() or {}).get('path')
            self._select(random.randrange(len(self.tracks)))

    def _undo_random(self):
        path=getattr(self,'_random_previous',None)
        index=next((i for i,t in enumerate(self.tracks) if t.get('path')==path),None)
        if index is not None:
            self._select(index)
            state=getattr(self,'_oc',None)
            if state:state['spin']=None

    def _sort_menu(self):
        m = QMenu(self)
        for key, name in (("title", "По названию"), ("artist", "По исполнителю"), ("length", "По длине"),
                          ("added", "По добавлению")):
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(key == self.sort)
            a.triggered.connect(lambda _=False, k=key: (setattr(self, "sort", k), self.refresh(), self._center()))
        m.exec(self.cursor().pos())

    def _coll_menu(self):
        m = QMenu(self)
        for key, name in ((self.COLL_ALL, "Вся музыка и карты osu!"), (self.COLL_MINE, "Только моя музыка"),
                          (self.COLL_OSU, f"Только карты osu!  ({len(OB.library_tracks())})")):
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(self.coll == key)
            a.triggered.connect(lambda _=False, k=key: self._set_coll(k))
        pls = list(self.win.playlists.keys())
        if pls:
            m.addSeparator()
        for name in pls:
            a = m.addAction(f"{name}  ({len(self.win.playlists[name].get('tracks', []))})")
            a.triggered.connect(lambda _=False, n=name: self._set_coll(n))
        m.exec(self.cursor().pos())

    def _set_coll(self, name):
        self.coll = name
        self.sel = 0
        self.refresh()
        self._select(self.sel)
        self._save_prefs()

    def _options_menu(self):
        t = self.cur()
        m = QMenu(self)
        if t is not None and not is_osu_track(t):
            m.addAction("Редактор карты (Ctrl+E)", self.open_editor)
        m.addAction("Попытки по картам…", self.shell.attempts_dialog)
        if t is not None and not is_osu_track(t):
            m.addAction("Сгенерировать карту / выбрать отрывок…  (F4)", self.open_generate)
            m.addAction("Сгенерировать заново (другой вариант)", lambda: self.shell.regenerate(t))
            m.addAction("Экспорт в настоящий osu! (.osz)…", lambda: self.shell.export_osz(t))
            m.addAction("Открыть папку карт", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(G.CACHE))))
        elif t is not None:
            m.addAction("Открыть папку этой карты", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(t.get("esu_dir") or ""))))
            m.addAction("Убрать набор из esu! (файлы osu! не трогаются)", lambda: self.shell.forget_set(t))
        m.addSeparator()
        imp = m.addMenu("Импорт карт osu!")
        imp.addAction("Обновить из папки osu! (F5)", lambda: self.shell.scan_osu(force=True))
        imp.addAction("Открыть файлы .osz / .osu…", self.shell.import_files_dialog)
        imp.addAction("Выбрать папку Songs…", self.shell.pick_songs_dir)
        if t is not None:
            m.addSeparator()
            m.addAction("Смотреть Auto (Ctrl+Enter)", lambda: self.start_play(auto=True))
        m.exec(self.cursor().pos())

    def start_play(self, auto=False, replay=None):
        t = self.cur()
        if t is None:
            return
        self._preview_due = None                     # отложенное превью не должно сработать во время карты
        if self._req_due:
            self._req_due = None
            self.shell.request_set(t)
        m = self._map()
        if m is None:
            if not is_osu_track(t) and (self._set() or {}).get("state") in ("idle", "error"):
                self.open_generate()
                return
            self.shell.toast("Карта ещё открывается — подождите секунду" if is_osu_track(t) else
                             "Карта ещё строится — подождите пару секунд")
            return
        mods = set(self.mods)
        if auto:
            mods = (mods - {"RX", "AP", "CN"}) | {"AU"}
        self.sfx("menuhit")
        self.shell.start_game(m, t, mods, replay)

    def open_generate(self):
        """Окно генерации: весь трек или отрывок (с прослушиванием выделенного)."""
        t = self.cur()
        if t is None or is_osu_track(t):
            return
        self._preview_due = None
        from osu_gen_dialog import GenerateDialog
        d = GenerateDialog(self.shell, t, self)
        if d.exec():
            self.shell.generate(t, d.result_segment())

    def open_editor(self):
        t = self.cur()
        if t is None:
            return
        if is_osu_track(t):
            self.shell.toast("Редактор — для карт ECHOES; карты из osu! правьте в самой osu!")
            return
        if self._req_due:
            self._req_due = None
            self.shell.request_set(t)
        m = self._map()
        if m is None:
            self.shell.toast("Карта ещё строится — подождите пару секунд")
            return
        self.sfx("menuhit")
        self.shell.open_editor(m, t, self._cur_diff())

    def wheelEvent(self, e):
        if self.mods_open:
            return
        ph, dh, gap = self._sizes()
        self.scroll_tgt -= e.angleDelta().y() / 120 * (ph + gap) * 1.5
        top = -self.height() / 2
        bottom = self._y_track(len(self.tracks)) - self.height() / 2
        self.scroll_tgt = max(top, min(bottom, self.scroll_tgt))

    def keyPressEvent(self, e):
        k = e.key()
        mod = e.modifiers()
        if self.mods_open:
            if k in (Qt.Key.Key_Escape, Qt.Key.Key_F1, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.mods_open = False
            return
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key_from_event(e) == "E":                            # редактор карты (и в русской раскладке)
            self.open_editor()
            return
        if ctrl and k == Qt.Key.Key_V:                                   # вставить запрос
            from PyQt6.QtWidgets import QApplication
            s = " ".join((QApplication.clipboard().text() or "").split())[:80]
            if s:
                self.search = (self.search + s)[:80]
                self._search_changed()
            return
        if k == Qt.Key.Key_Escape:
            if self.search:
                self.search = ""
                self._search_changed()
            else:
                self.sfx("menuback")
                self.shell.show_screen("menu")
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.edit_mode and not ctrl:
                self.open_editor()
            else:
                self.start_play(auto=ctrl)
        elif k == Qt.Key.Key_F1:
            self.mods_open = True
        elif k == Qt.Key.Key_F2:
            if mod & Qt.KeyboardModifier.ShiftModifier:self._undo_random()
            else:self._random()
        elif k == Qt.Key.Key_F3:
            self._options_menu()
        elif k == Qt.Key.Key_F5:
            self.shell.scan_osu(force=True)
        elif k == Qt.Key.Key_F4:
            self.open_generate()
        elif k in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            keys = self._diff_keys()
            cur = self._cur_diff()
            i = keys.index(cur) if cur in keys else 0
            step = 1 if k == Qt.Key.Key_Down else -1
            if 0 <= i + step < len(keys):
                self._set_diff(keys[i + step])
                self.sfx("menuclick", 0.6)
                self._center()
            else:
                self._select(self.sel + step)
                nk = self._diff_keys()
                self._set_diff(nk[0] if step > 0 else nk[-1])
                self._center()
            self._save_prefs()
        elif k == Qt.Key.Key_Right:
            self._select(self.sel + 1)
        elif k == Qt.Key.Key_Left:
            self._select(self.sel - 1)
        elif k == Qt.Key.Key_PageDown:
            self._select(self.sel + 8)
        elif k == Qt.Key.Key_PageUp:
            self._select(self.sel - 8)
        elif k == Qt.Key.Key_Backspace:
            if self.search:
                # Ctrl+Backspace — стереть слово целиком
                self.search = self.search.rstrip()[:self.search.rstrip().rfind(" ") + 1] if ctrl else self.search[:-1]
                self._search_changed()
        else:
            ch = e.text()
            if ch and ch.isprintable() and not (mod & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                if ch == " " and (not self.search or self.search.endswith(" ")):
                    return                                               # пробелы в начале и двойные — не нужны
                if len(self.search) < 80:
                    self.search += ch
                    self._search_changed()


DIFF_KEYS = G.DIFF_KEYS


# ── результаты ── #

class ResultScreen(Screen):
    def __init__(self, shell):
        super().__init__(shell)
        self.res = None
        self.m = None
        self.track = None
        self.rank = 0
        self.bg = BgCache()
        self.hover = None
        self.digits = None

    def show_result(self, res, m, track, rank):
        self.res, self.m, self.track, self.rank = res, m, track, rank
        self.t_enter = time.perf_counter()
        self._applause = False
        self.digits = None

    def enter(self):
        self.t_enter = time.perf_counter()
        QTimer.singleShot(0, self.setFocus)

    def _buttons(self):
        H = self.height()
        h = 52
        y = H - 74
        return {"back": QRectF(24, y, 170, h), "retry": QRectF(210, y, 170, h), "replay": QRectF(396, y, 230, h),
                "offset": QRectF(642, y, 260, h)}

    def step(self, dt, now):
        if self.res and not self._applause and now - self.t_enter > 0.9:
            self._applause = True
            if self.res["grade"] in ("SS", "SSH", "S", "SH", "A") and not self.res.get("failed"):
                self.sfx("applause", 0.9)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        draw_bg(p, self.bg.get(self.shell.bg_path(self.track), W, H, self.devicePixelRatioF(), blur=True), W, H, 0.6,
                cache=self.bg)
        if not self.res:
            p.end()
            return
        r, m, t = self.res, self.m, self.track
        now = time.perf_counter()
        e = now - self.t_enter
        dpr = self.devicePixelRatioF()
        if self.digits is None or self.digits.size != H * 0.09:
            self.digits = Digits(H * 0.09, dpr=dpr)
            self.digits_s = Digits(H * 0.045, dpr=dpr)
        # шапка
        p.fillRect(QRectF(0, 0, W, 96), QColor(0, 0, 0, 190))
        text(p, QRectF(24, 8, W * 0.7, 40), f"{t.get('artist') or '—'} - {track_title(t)} [{m['name']}]", 26,
             QColor(255, 255, 255), QFont.Weight.Bold)
        by = f"Карта: {m.get('creator') or '—'} (osu!)" if m.get("osu") else "Карта ECHOES AI"
        text(p, QRectF(24, 48, W * 0.7, 20), f"{by}  ·  ★ {r.get('stars', m['stars']):.2f}", 14,
             QColor(255, 255, 255, 200), QFont.Weight.Normal)
        who = "Auto" if r.get("auto") else self.shell.player_name()
        att = ""
        if r.get("attempt"):                           # счётчик попыток
            att = f"  ·  попытка {r['attempt']}"
            if r.get("first_pass") == r.get("attempt"):
                att += " — первое прохождение!"
            elif r.get("first_pass"):
                att += f"  ·  впервые пройдена с {r['first_pass']}-й"
        text(p, QRectF(24, 68, W * 0.7, 20), f"Игрок {who}  ·  {time.strftime('%d.%m.%Y %H:%M', time.localtime(r['time']))}"
             + att, 13, QColor(255, 255, 255, 170), QFont.Weight.Normal)
        # панель
        pr = QRectF(24, 116, W * 0.56, H - 116 - 96)
        rrect(p, pr, 16, QColor(0, 0, 0, 160), QPen(QColor(255, 255, 255, 40), 1))
        k = min(1.0, e / 1.0)
        sc = int(r["score"] * (1 - (1 - k) ** 3))
        self.digits.draw(p, f"{sc:08d}", pr.center().x(), pr.top() + H * 0.07, "c")
        sk = self.shell.skin_for(H * 0.035)
        rows = [((300, r["c300"]), ("geki", r["geki"])), ((100, r["c100"]), ("katu", r["katu"])),
                ((50, r["c50"]), (0, r["c0"]))]
        y = pr.top() + H * 0.16
        for left, right in rows:
            for col, (kind, val) in enumerate((left, right)):
                x = pr.left() + 40 + col * pr.width() * 0.5
                if isinstance(kind, int):
                    blit(p, sk.judgement(kind), x + 30, y + H * 0.035, 1.0, 1.0)
                else:
                    text(p, QRectF(x, y, 70, H * 0.07), "激" if kind == "geki" else "喝", H * 0.04,
                         QColor(255, 220, 120), QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
                self.digits_s.draw(p, f"{val}x", x + 80, y + H * 0.035, "l")
            y += H * 0.085
        y += 6
        text(p, QRectF(pr.left() + 40, y, 300, 22), "Макс. комбо", 14, QColor(255, 255, 255, 190), QFont.Weight.Normal)
        text(p, QRectF(pr.left() + pr.width() * 0.5 + 40, y, 300, 22), "Точность", 14, QColor(255, 255, 255, 190),
             QFont.Weight.Normal)
        self.digits_s.draw(p, f"{r['combo']}x", pr.left() + 40, y + 46, "l")
        self.digits_s.draw(p, f"{r['acc'] * 100:.2f}%", pr.left() + pr.width() * 0.5 + 40, y + 46, "l")
        y += 80
        text(p, QRectF(pr.left() + 40, y, pr.width() - 80, 22),
             f"{r['pp']:.0f}pp   ·   UR {r['ur']:.1f}   ·   в среднем {abs(r['mean_err']):.1f} мс "
             f"{'позже' if r['mean_err'] > 0 else 'раньше'}   ·   макс. комбо карты {r.get('max_combo', 0)}x",
             15, QColor(255, 220, 120), QFont.Weight.Bold)
        # график HP
        gr = QRectF(pr.left() + 30, pr.bottom() - H * 0.15, pr.width() - 60, H * 0.11)
        rrect(p, gr, 8, QColor(255, 255, 255, 18))
        hh = r.get("hp_hist") or []
        if len(hh) > 1:
            t0, t1 = hh[0][0], hh[-1][0]
            path = QPainterPath()
            for i, (tt, hpv) in enumerate(hh):
                x = gr.left() + (tt - t0) / max(1.0, t1 - t0) * gr.width()
                yv = gr.bottom() - hpv * gr.height()
                path.moveTo(x, yv) if i == 0 else path.lineTo(x, yv)
            p.setOpacity(min(1.0, e / 0.6))
            p.strokePath(path, QPen(QColor(120, 230, 90), 2.5))
            p.setOpacity(1.0)
        text(p, QRectF(gr.left() + 8, gr.top() + 4, 200, 16), "здоровье по ходу карты", 11, QColor(255, 255, 255, 140),
             QFont.Weight.Normal)
        # оценка
        gk = min(1.0, max(0.0, (e - 0.35) / 0.5))
        gp = self.shell.grade_pix(r["grade"], H * 0.42, dpr)
        if gk > 0:
            blit(p, gp, W * 0.8, H * 0.45, 2.2 - 1.2 * (1 - (1 - gk) ** 3), gk, (1 - gk) * -12)
            if gk < 1:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                p.fillRect(self.rect(), QColor(255, 255, 255, int(40 * (1 - gk))))
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if r.get("failed"):
            text(p, QRectF(W * 0.62, H * 0.12, W * 0.36, 40), "ПРОВАЛ", 30, QColor(255, 90, 90), QFont.Weight.Black,
                 Qt.AlignmentFlag.AlignCenter)
        elif self.rank == 1 and e > 1.0:
            text(p, QRectF(W * 0.62, H * 0.12, W * 0.36, 40), "НОВЫЙ РЕКОРД!", 26, QColor(255, 220, 120),
                 QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
        x = W * 0.8 - len(r["mods"]) * 30
        for mm in r["mods"]:
            pm = self.shell.mod_pix(mm, 40, dpr)
            p.drawPixmap(QPointF(x, H * 0.75), pm)
            x += 60
        # кнопки
        for key, label, col in (("back", "Назад", PINK), ("retry", "Заново", QColor(240, 180, 40)),
                                ("replay", "Смотреть повтор", QColor(60, 160, 230)),
                                ("offset", "Подогнать смещение", QColor(120, 90, 220))):
            rr = self._buttons()[key]
            if key == "replay" and not r.get("replay"):
                continue
            if key == "offset" and (r.get("watched") or abs(r["mean_err"]) < 3):
                continue
            hov = self.hover == key
            rrect(p, rr, rr.height() / 2, SK.lighter(col, 0.2) if hov else col)
            text(p, rr, label, 17, QColor(255, 255, 255), QFont.Weight.Bold, Qt.AlignmentFlag.AlignCenter)
        p.end()

    def mouseMoveEvent(self, e):
        old = self.hover
        self.hover = next((k for k, r in self._buttons().items() if r.contains(e.position())), None)
        if self.hover != old and self.hover:
            self.sfx("hover", 0.4)

    def mousePressEvent(self, e):
        k = next((k for k, r in self._buttons().items() if r.contains(e.position())), None)
        if k:
            self._do(k)

    def _do(self, k):
        self.sfx("menuhit", 0.7)
        if k == "back":
            self.shell.show_screen("select")
        elif k == "retry":
            self.shell.start_game(self.m, self.track, set(self.res["mods"]) - {"AU"} if not self.res.get("auto")
                                  else set(self.res["mods"]))
        elif k == "replay" and self.res.get("replay"):
            self.shell.start_game(self.m, self.track, set(self.res["mods"]), self.res["replay"])
        elif k == "offset":
            S = self.shell.S()
            S["offset"] = round(float(S.get("offset", 0)) + self.res["mean_err"])
            self.shell.save_settings()
            self.shell.toast(f"Общее смещение теперь {S['offset']:+.0f} мс")
            self.res["mean_err"] = 0.0

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._do("back")
        elif key_is(e, bind_of(self.shell.S(), "key_retry")):
            self._do("retry")


# ------------------------------------------------------------------ #
#  Настройки                                                          #
# ------------------------------------------------------------------ #

class KeyButton(QPushButton):
    """Кнопка-бинд: нажать → нажать клавишу. Esc — отмена, Backspace/Delete — снять клавишу.
    Клавиша запоминается по месту на клавиатуре, поэтому работает и в русской раскладке."""

    def __init__(self, shell, key, on_change=None):
        super().__init__()
        self.shell, self.key = shell, key
        self.on_change = on_change
        self.waiting = False
        self.setMinimumWidth(96)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._arm)
        self.sync()

    def sync(self):
        self.setText(key_label(bind_of(self.shell.S(), self.key)))

    def _arm(self):
        self.waiting = True
        self.setText("нажмите клавишу…")
        self.setFocus()
        self.grabKeyboard()

    def _disarm(self):
        self.waiting = False
        self.releaseKeyboard()
        self.sync()

    def event(self, e):
        if self.waiting:
            if e.type() == QEvent.Type.ShortcutOverride:            # пробел/стрелки не уходят плееру
                e.accept()
                return True
            if e.type() == QEvent.Type.KeyPress:                    # и Tab тоже можно назначить
                self.keyPressEvent(e)
                return True
        return super().event(e)

    def focusOutEvent(self, e):
        if self.waiting:
            self._disarm()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if not self.waiting:
            return super().keyPressEvent(e)
        if e.isAutoRepeat():
            return
        k = e.key()
        if k in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return                                                  # ждём настоящую клавишу
        S = self.shell.S()
        if k == Qt.Key.Key_Escape and self.key != "key_pause":
            self._disarm()
            return
        name = "" if k in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) else key_from_event(e)
        if self.key in ("k1", "k2") and not name:
            self._disarm()                                          # без кнопок нот играть нельзя
            return
        old = bind_of(S, self.key)
        # клавиша уже занята другим действием — меняем их местами
        for other, _label, _d in KEY_BINDS:
            if other != self.key and name and bind_of(S, other) == name:
                S[other] = old
        S[self.key] = name
        self.shell.save_settings()
        self._disarm()
        if self.on_change:
            self.on_change()


SETTINGS_QSS = """
QFrame#OsuSettings { background: #1d1621; border-right: 1px solid rgba(255,255,255,0.08); }
QFrame#OsuSettings QLabel { color: #ffffff; background: transparent; font-family: 'Segoe UI'; font-size: 13px; }
QFrame#OsuSettings QLabel#H1 { font-size: 28px; font-weight: 900; color: #ffffff; }
QFrame#OsuSettings QLabel#H2 { font-size: 13px; color: #ff66aa; font-weight: 800; letter-spacing: 2px;
    padding-top: 14px; }
QFrame#OsuSettings QLabel#Sub { color: #b9adc4; font-size: 12px; }
QFrame#OsuSettings QLabel#Val { color: #ffb3d4; font-weight: 700; }
QFrame#OsuSettings QScrollArea { background: transparent; border: none; }
QFrame#OsuSettings QWidget#Body { background: transparent; }
QFrame#OsuSettings QCheckBox { color: #ffffff; font-size: 13px; spacing: 10px; background: transparent; }
QFrame#OsuSettings QCheckBox::indicator { width: 34px; height: 18px; border-radius: 9px; background: #4a3d52;
    border: 1px solid #6b5a75; }
QFrame#OsuSettings QCheckBox::indicator:checked { background: #ff66aa; border: 1px solid #ffb3d4; }
QFrame#OsuSettings QSlider::groove:horizontal { height: 4px; background: #4a3d52; border-radius: 2px; }
QFrame#OsuSettings QSlider::sub-page:horizontal { background: #ff66aa; border-radius: 2px; }
QFrame#OsuSettings QSlider::handle:horizontal { width: 16px; height: 16px; margin: -7px 0; border-radius: 8px;
    background: #ffffff; border: 2px solid #ff66aa; }
QFrame#OsuSettings QPushButton { background: #ff66aa; color: #ffffff; border: none; border-radius: 15px;
    padding: 7px 16px; font-weight: 700; font-size: 13px; }
QFrame#OsuSettings QPushButton:hover { background: #ff85bb; }
QFrame#OsuSettings QPushButton#Ghost { background: rgba(255,255,255,0.08); }
QFrame#OsuSettings QPushButton#Ghost:hover { background: rgba(255,255,255,0.16); }
QFrame#OsuSettings QComboBox { background: #2c2233; color: #ffffff; border: 1px solid #4a3d52; border-radius: 8px;
    padding: 5px 10px; font-size: 13px; }
QFrame#OsuSettings QComboBox QAbstractItemView { background: #2c2233; color: #ffffff;
    selection-background-color: #ff66aa; }
"""


def _switch_assets() -> dict | None:
    """Картинки тумблера (розовый бегунок) для QCheckBox::indicator."""
    d = Path.home() / ".neon_player" / "ui_cache"
    out = {k: d / f"osu_{k}.png" for k in ("sw_off", "sw_on")}
    if all(p.exists() for p in out.values()):
        return {k: p.as_posix() for k, p in out.items()}
    try:
        d.mkdir(parents=True, exist_ok=True)
        W, H, S = 40, 22, 4
        for key, on in (("sw_off", False), ("sw_on", True)):
            pm = QPixmap(W * S, H * S)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PINK if on else QColor(74, 61, 82))
            p.drawRoundedRect(QRectF(0, 0, W * S, H * S), H * S / 2, H * S / 2)
            r = (H - 6) * S
            x = (W - 3) * S - r if on else 3 * S
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QRectF(x, 3 * S, r, r))
            p.end()
            pm.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation).save(str(out[key]))
        return {k: p.as_posix() for k, p in out.items()}
    except Exception:                                                 # noqa: BLE001
        return None


class _WheelGuard(QObject):
    """Колесо над ползунком или списком крутит панель настроек, а не значение: раньше при
    прокрутке настроек незаметно менялись чувствительность, затемнение, громкость…
    Значение колесом меняется, только если по элементу сначала щёлкнули."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Wheel and isinstance(obj, QWidget) and not obj.hasFocus() \
                and self.panel.isVisible() and self.panel.isAncestorOf(obj):
            dy = e.angleDelta().y()
            w = obj.parentWidget()
            while w is not None:                       # ближайшая панель, которой есть куда крутиться
                if isinstance(w, QAbstractScrollArea):
                    sb = w.verticalScrollBar()
                    if (dy > 0 and sb.value() > sb.minimum()) or (dy < 0 and sb.value() < sb.maximum()):
                        QApplication.sendEvent(sb, e)
                        break
                w = w.parentWidget()
            return True
        return False


class SettingsPanel(QFrame):
    W = 440

    def guard_wheel(self):
        g = getattr(self, "_wheel_guard", None)
        if g is None:
            g = self._wheel_guard = _WheelGuard(self)
        for w in self.findChildren(QWidget):
            if isinstance(w, (QSlider, QComboBox, QAbstractSpinBox)) and not w.property("_osu_wheel"):
                w.setProperty("_osu_wheel", True)
                if w.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                    w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                w.installEventFilter(g)

    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self.setObjectName("OsuSettings")
        qss = SETTINGS_QSS
        sw = _switch_assets()
        if sw:
            qss += (f"QFrame#OsuSettings QCheckBox::indicator {{ width: 40px; height: 22px; border: none; "
                    f"background: transparent; image: url({sw['sw_off']}); }}"
                    f"QFrame#OsuSettings QCheckBox::indicator:checked {{ image: url({sw['sw_on']}); "
                    f"background: transparent; border: none; }}")
        self.setStyleSheet(qss)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Body")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(26, 22, 26, 30)
        self.v.setSpacing(8)
        sc.setWidget(body)
        lay.addWidget(sc)
        self._build()
        self.hide()

    def _h1(self, s, sub=None):
        lb = QLabel(s)
        lb.setObjectName("H1")
        self.v.addWidget(lb)
        if sub:
            l2 = QLabel(sub)
            l2.setObjectName("Sub")
            l2.setWordWrap(True)
            self.v.addWidget(l2)

    def _h2(self, s):
        lb = QLabel(s.upper())
        lb.setObjectName("H2")
        self.v.addWidget(lb)

    def _note(self, s):
        lb = QLabel(s)
        lb.setObjectName("Sub")
        lb.setWordWrap(True)
        self.v.addWidget(lb)

    def _check(self, key, label, cb=None, sub=None):
        S = self.shell.S()
        c = QCheckBox(label)
        c.setChecked(bool(S.get(key, OSU_DEFAULTS.get(key))))

        def ch(v, k=key):
            self.shell.S()[k] = bool(v)
            self.shell.save_settings()
            if cb:
                cb(bool(v))
        c.toggled.connect(ch)
        self.v.addWidget(c)
        if sub:
            self._note(sub)

        def pull(k=key):
            c.blockSignals(True)
            c.setChecked(bool(self.shell.S().get(k, OSU_DEFAULTS.get(k))))
            c.blockSignals(False)
        self._pulls().append(pull)
        return c

    def _slider(self, key, label, lo, hi, step, fmt, cb=None, store=None, load=None):
        S = self.shell.S()
        row = QWidget()
        h = QVBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(2)
        top = QHBoxLayout()
        lb = QLabel(label)
        val = QLabel()
        val.setObjectName("Val")
        top.addWidget(lb)
        top.addStretch(1)
        top.addWidget(val)
        h.addLayout(top)
        sl = QSlider(Qt.Orientation.Horizontal)
        n = int(round((hi - lo) / step))
        sl.setRange(0, n)
        v0 = S.get(key, OSU_DEFAULTS.get(key, lo))
        cur = load() if load else float(lo if v0 is None else v0)
        sl.setValue(int(round((cur - lo) / step)))
        val.setText(fmt(cur))

        def ch(i, k=key):
            v = round(lo + i * step, 4)
            val.setText(fmt(v))
            if store:
                store(v)
            else:
                self.shell.S()[k] = v
            self.shell.save_settings()
            if cb:
                cb(v)
        sl.valueChanged.connect(ch)
        h.addWidget(sl)
        self.v.addWidget(row)

        def pull(k=key):
            v1 = load() if load else self.shell.S().get(k, OSU_DEFAULTS.get(k, lo))
            v1 = float(lo if v1 is None else v1)
            sl.blockSignals(True)
            sl.setValue(int(round((v1 - lo) / step)))
            sl.blockSignals(False)
            val.setText(fmt(v1))
        self._pulls().append(pull)
        return sl

    def convert_sens(self):
        from osu_sens_convert import SensConvertDialog
        d = SensConvertDialog(self.shell, self)
        if d.exec():
            self.resync()
            self.shell.toast(f"Чувствительность: {float(self.shell.S().get('sens', 1.0)) * 100:.0f}%")

    def refresh_osu_status(self):
        lab = getattr(self, "osu_status", None)
        if lab is None:
            return
        sets = OB.load_index()
        n = sum(len(s.get("diffs", [])) for s in sets)
        songs = self.shell.songs_dir()
        st = getattr(self.shell, "_osu_job_text", "")
        lab.setText((f"Наборов: {len(sets)}, карт: {n}" if sets else "Карт из osu! пока нет") +
                    (f"  ·  папка: {songs}" if songs else "  ·  osu! не найден") + (f"\n{st}" if st else ""))

    def _pulls(self) -> list:
        if not hasattr(self, "_pull_fns"):
            self._pull_fns = []
        return self._pull_fns

    def resync(self):
        """Значения могли поменяться в обход панели (панели на паузе игры) — перечитать."""
        for fn in self._pulls():
            try:
                fn()
            except Exception:                                         # noqa: BLE001
                pass

    def _combo(self, key, label, items, cb=None):
        S = self.shell.S()
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch(1)
        c = QComboBox()
        for k, name in items:
            c.addItem(name, k)
        i = next((n for n, (k, _) in enumerate(items) if k == S.get(key, OSU_DEFAULTS.get(key))), 0)
        c.setCurrentIndex(i)

        def ch(_i, k=key):
            self.shell.S()[k] = c.currentData()
            self.shell.save_settings()
            if cb:
                cb(c.currentData())
        c.currentIndexChanged.connect(ch)
        row.addWidget(c)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 2, 0, 2)
        self.v.addWidget(w)

        def pull(k=key):
            j = next((n for n, (kk, _) in enumerate(items) if kk == self.shell.S().get(k, OSU_DEFAULTS.get(k))), 0)
            c.blockSignals(True)
            c.setCurrentIndex(j)
            c.blockSignals(False)
        self._pulls().append(pull)
        return c

    # ── скин osu! ── #
    def _skin_section(self):
        self._h2("Скин osu!")
        self._note("Внешний вид из файлов настоящего osu!: ноты, слайдеры, спиннер, курсор и его след, цифры, "
                   "оценки, полоска здоровья, фон меню, цвета комбо и звуки скина. Подходят файлы .osk (с сайта "
                   "osu! или экспорт из osu!lazer: Настройки → Скин → Экспорт) и папки скинов из osu!/Skins; "
                   ".osk можно просто перетащить в окно. Чего в скине нет — остаётся своим.")
        row = QHBoxLayout()
        row.addWidget(QLabel("Скин"))
        row.addStretch(1)
        self.skin_combo = QComboBox()
        self.skin_combo.setMinimumWidth(190)
        self.skin_combo.currentIndexChanged.connect(self._skin_chosen)
        row.addWidget(self.skin_combo)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 2, 0, 2)
        self.v.addWidget(w)
        row2 = QHBoxLayout()
        for label, fn in (("Файл .osk…", self._skin_osk), ("Папка скина…", self._skin_folder),
                          ("Из osu!…", self._skin_from_osu)):
            b = QPushButton(label)
            b.setObjectName("Ghost")
            b.clicked.connect(fn)
            row2.addWidget(b)
        w2 = QWidget()
        w2.setLayout(row2)
        row2.setContentsMargins(0, 2, 0, 2)
        self.v.addWidget(w2)
        self.skin_del = QPushButton("Удалить выбранный скин")
        self.skin_del.setObjectName("Ghost")
        self.skin_del.clicked.connect(self._skin_delete)
        self.v.addWidget(self.skin_del)
        self._check("skin_menu_bg", "Фон главного меню из скина (menu-background)",
                    cb=lambda _v: self.shell.menu.update())
        self.refresh_skins()
        self._pulls().append(self.refresh_skins)

    def refresh_skins(self):
        import osu_skin_import as SI
        c = getattr(self, "skin_combo", None)
        if c is None:
            return
        cur = self.shell.skin_name()
        c.blockSignals(True)
        c.clear()
        c.addItem("Встроенный (esu!)", "")
        for s in SI.list_skins():
            c.addItem(s["title"] + (f" — {s['author']}" if s["author"] else ""), s["name"])
        i = c.findData(cur)
        c.setCurrentIndex(max(0, i))
        c.blockSignals(False)
        self.skin_del.setEnabled(bool(cur) and i > 0)

    def _skin_chosen(self, _i):
        name = self.skin_combo.currentData() or ""
        if name == self.shell.skin_name():
            return
        sf = self.shell.set_skin(name)
        self.shell.toast(f"Скин osu!: {sf.name}" if sf else "Скин: встроенный esu!")
        self.resync()

    def _skin_osk(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Скин osu!", os.path.expanduser("~"),
                                                "Скины osu! (*.osk *.zip)")
        if files:
            self.shell.import_skin_files(files)

    def _skin_folder(self):
        import osu_skin_import as SI
        start = SI.osu_skins_dir()
        d = QFileDialog.getExistingDirectory(self, "Папка скина osu!", str(start) if start else os.path.expanduser("~"))
        if d:
            self.shell.import_skin_files([d])

    def _skin_from_osu(self):
        import osu_skin_import as SI
        from PyQt6.QtGui import QCursor
        lst = SI.osu_installed_skins()
        if not lst:
            self.shell.toast("В папке Skins установленного osu! скинов нет — нужен файл .osk или папка скина")
            return
        m = QMenu(self)
        for p in lst:
            m.addAction(p.stem if p.is_file() else p.name, lambda p=p: self.shell.import_skin_files([str(p)]))
        m.exec(QCursor.pos())

    def _skin_delete(self):
        import osu_skin_import as SI
        from PyQt6.QtWidgets import QMessageBox
        name = self.shell.skin_name()
        if not name:
            return
        ok = QMessageBox.question(self, "Скин osu!", f"Удалить скин «{self.skin_combo.currentText()}» из esu!? "
                                  "Файлы в osu! (если скин оттуда) не трогаются.")
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.shell.set_skin(None)
        SI.remove_skin(name)
        self.refresh_skins()
        self.resync()
        self.shell.toast("Скин удалён — снова встроенный esu!")

    def _gen(self, k, default):
        return lambda: float(self.shell.S().setdefault("mapgen", {}).get(k, default))

    def _gen_store(self, k):
        def st(v):
            self.shell.S().setdefault("mapgen", {})[k] = v
            self.shell.mapgen_changed()
        return st

    def _build(self):
        sh = self.shell
        self._h1("Настройки", f"Тема {THEME_NAME} · ECHOES")
        self._h2("Ввод")
        self._slider("sens", "Чувствительность", 0.01, 5.0, 0.01, lambda v: f"{v * 100:.0f}%")
        cb_ = QPushButton("Перенести чувствительность из игры (Roblox, RIVALS, CS2, Valorant…)")
        cb_.setObjectName("Ghost")
        cb_.clicked.connect(self.convert_sens)
        self.v.addWidget(cb_)
        self._check("raw", "Raw input (без ускорения Windows)", sub="Смещения мыши читаются напрямую, без "
                    "ускорения и скорости указателя Windows, — чувствительность работает как в osu!, и на 100% "
                    "тоже. Планшет определяется сам (для него оставьте 100%).")
        self._check("confine", "Не выпускать курсор из окна во время игры")
        self._check("mouse_buttons", "Кнопки мыши тоже нажимают ноты")
        self._h2("Клавиши")
        self._note("Нажмите на кнопку, затем нужную клавишу. Esc — отмена, Backspace — убрать клавишу. "
                   "Клавиши узнаются по месту на клавиатуре, поэтому работают и в русской раскладке. "
                   "Если клавиша уже занята, действия меняются местами.")
        self._key_btns = []

        def resync():
            for b in self._key_btns:
                b.sync()
        for key, label, _d in KEY_BINDS:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch(1)
            kb = KeyButton(sh, key, resync)
            self._key_btns.append(kb)
            row.addWidget(kb)
            w = QWidget()
            w.setLayout(row)
            row.setContentsMargins(0, 1, 0, 1)
            self.v.addWidget(w)

        def reset_keys():
            S = sh.S()
            for k, d in KEY_DEFAULTS.items():
                S[k] = d
            sh.save_settings()
            resync()
            sh.toast("Клавиши: как в osu! по умолчанию")
        rb = QPushButton("Сбросить клавиши")
        rb.setObjectName("Ghost")
        rb.clicked.connect(reset_keys)
        self.v.addWidget(rb)
        self._h2("Игра")
        self._slider("dim", "Затемнение фона", 0.0, 1.0, 0.05, lambda v: f"{int(v * 100)}%")
        self._combo("bg", "Фон карты", [("cover", "Обложка"), ("clip", "Клип песни (если скачан)"), ("none", "Без фона")])
        self._check("blur", "Размыть фон")
        self._check("countdown", "Отсчёт «3, 2, 1, GO!» перед картой")
        self._check("leaderboard", "Таблица рекордов слева во время игры")
        self._check("fail_fx", "Замедление музыки при провале")
        self._slider("offset", "Смещение звука (+ — ноты позже)", -150, 150, 1, lambda v: f"{v:+.0f} мс")
        self._h2("Графика")
        self._check("hit_light", "Подсветка попаданий")
        self._check("particles", "Искры при попаданиях")
        self._check("snaking", "Слайдеры выползают «змейкой»")
        self._check("followpoints", "Дорожки между нотами (follow points)")
        self._check("kiai_fx", "Вспышки и фонтаны звёзд в припевах (kiai)")
        self._check("bg_pulse", "Фон пульсирует в такт в kiai")
        self._check("show300", "Показывать «300»")
        self._check("combo_burst", "Вспышка счётчика комбо")
        self._check("cursor_trail", "След курсора")
        self._check("ripples", "Волны от нажатий")
        self._slider("cursor_size", "Размер курсора", 0.5, 2.0, 0.05, lambda v: f"{v:.2f}×")
        self._check("hud", "Показывать интерфейс в игре (счёт, HP, комбо)")
        self._check("hit_error", "Шкала ошибок попадания")
        self._check("key_overlay", "Счётчик нажатий клавиш")
        self._check("menu_parallax", "Параллакс фона в меню")
        self._combo("palette", "Цвета нот", [(SK.SKIN_PALETTE, "Из скина osu!")] +
                    [(k, v[0]) for k, v in SK.PALETTES.items()], cb=lambda _v: sh.apply_palette())
        self._skin_section()
        self._h2("Звук")
        self._slider("sfx_vol", "Громкость хитсаундов", 0.0, 1.0, 0.05, lambda v: f"{int(v * 100)}%",
                     cb=lambda v: setattr(sh.sfx, "volume", v))
        if not sh.sfx.ok:
            self._note("Отдельный вывод звука для хитсаундов недоступен (нет sounddevice или устройства).")
        self._h2("Карты из osu!")
        self._note("Карты osu!standard из установленного osu! (папка Songs) появляются в выборе песни сами; "
                   "файлы .osz и .osu можно открыть кнопкой ниже или просто перетащить в окно. "
                   "Файлы osu! не меняются и не копируются (кроме распакованных .osz).")
        self.osu_status = QLabel("")
        self.osu_status.setObjectName("Val")
        self.osu_status.setWordWrap(True)
        self.v.addWidget(self.osu_status)
        self._check("osu_scan", "Искать новые карты в папке osu! при входе в тему")
        row = QHBoxLayout()
        for label, fn in (("Обновить сейчас", lambda: sh.scan_osu(force=True)),
                          ("Открыть .osz / .osu…", sh.import_files_dialog), ("Папка Songs…", sh.pick_songs_dir)):
            b = QPushButton(label)
            b.setObjectName("Ghost")
            b.clicked.connect(fn)
            row.addWidget(b)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 2, 0, 2)
        self.v.addWidget(w)
        self._check("map_hitsounds", "Хитсаунды из папки карты (свои звуки маппера)")
        self._check("map_colours", "Цвета комбо из карты")
        self._check("map_skin", "Скин карты (свои картинки нот в папке карты — поверх скина)",
                    cb=lambda _v: setattr(sh.game, "_layout_sig", None))
        self._check("map_video", "Видео карты как фон (если есть)")
        self._check("auto_offset", "Подгонять смещение карт osu! по звуку",
                    sub="Время нот в osu! отсчитывается по её декодеру звука; esu! сверяет ноты карты с ударами "
                        "в треке и сдвигает карту, чтобы ноты совпадали со звуком в ECHOES.")
        self.refresh_osu_status()
        self._h2("Генератор карт")
        self._note("Модель слушает трек (темп с точностью до сотых BPM, начала ударов, триоли, ударные отдельно "
                   "от мелодии, голос отдельно нейросетью — слоги и флоу, протяжные звуки, части песни, припевы, повторы) "
                   "и ставит ноты так, как их "
                   "ставят живые мапперы: ритм, слайдеры, дистанции, углы, стопки, комбо и хитсаунды выучены по "
                   "ranked-картам osu!. Изменения применяются при следующей генерации карты.")
        self._slider("g_dens", "Плотность нот", 0.5, 1.6, 0.05, lambda v: f"{v:.2f}×",
                     store=self._gen_store("density"), load=self._gen("density", 1.0))
        self._slider("g_jump", "Размах прыжков", 0.5, 1.6, 0.05, lambda v: f"{v:.2f}×",
                     store=self._gen_store("jumps"), load=self._gen("jumps", 1.0))
        self._slider("g_sl", "Слайдеры", 0.0, 2.0, 0.1, lambda v: f"{v:.1f}×",
                     store=self._gen_store("sliders"), load=self._gen("sliders", 1.0))
        for k, label in (("streams", "Стримы на сложных картах"), ("spinners", "Спиннеры"),
                         ("symmetry", "Симметричные паттерны")):
            c = QCheckBox(label)
            c.setChecked(bool(sh.S().setdefault("mapgen", {}).get(k, True)))
            c.toggled.connect(lambda v, kk=k: (sh.S().setdefault("mapgen", {}).__setitem__(kk, bool(v)),
                                               sh.mapgen_changed()))
            self.v.addWidget(c)
        b = QPushButton("Сбросить настройки генератора")
        b.setObjectName("Ghost")
        b.clicked.connect(lambda: (sh.S().__setitem__("mapgen", {}), sh.mapgen_changed(), sh.toast(
            "Генератор: настройки по умолчанию")))
        self.v.addWidget(b)
        self._h2("Плеер")
        self._note("Эквалайзер, эффекты, таймеры и остальные настройки плеера:")
        self.player_slot = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.player_slot)
        holder.setMinimumHeight(620)
        self.v.addWidget(holder)
        b2 = QPushButton("Сменить тему плеера…")
        b2.clicked.connect(lambda: sh.theme_menu())
        self.v.addWidget(b2)
        b3 = QPushButton("Куда класть файлы тем и плейлистов?")
        b3.setObjectName("Ghost")
        b3.clicked.connect(lambda: getattr(sh.win, "files_help", lambda: None)())
        self.v.addWidget(b3)
        self.v.addStretch(1)


# ------------------------------------------------------------------ #
#  Оболочка                                                           #
# ------------------------------------------------------------------ #

class OsuShell(QWidget):
    """Весь интерфейс темы. win — MainWindow."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setObjectName("OsuShell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#OsuShell { background: #000000; }")
        S = self.S()
        if int(S.get("look_v", 0)) < 2:                  # esu! как в osu!: цвета комбо osu! вместо прежних «ярких»
            if S.get("palette") == "bright":
                S["palette"] = "classic"
            S["look_v"] = 2
        try:                                              # скин osu! — до палитры: «цвета из скина» читают его
            import osu_skin_import
            osu_skin_import.activate(S.get("skin") or None)
        except Exception as e:                            # noqa: BLE001
            print("[esu skin]", e)
        if S.get("style", "classic") != "y2k":           # у Y2K своя пастель (osu_y2k._apply_palette)
            SK.set_palette(S.get("palette"))
        self.apply_look()
        self.sfx = osu_sfx.get()
        self.sfx.volume = float(S.get("sfx_vol", 0.6))
        from theme_layers import AudioPulse
        self.pulse = AudioPulse(win.engine)
        self.sets = {}
        self.builders = {}
        self._an_cache = (None, None)
        self._settings_host = None
        self._pix = {}                                   # оценки и значки модов — картинки из кэша
        self._exists = {}
        self._track_by_path = (None, {})
        self.thumbs = _ThumbLoader()
        self._osu_job = None
        self._osu_job_text = ""
        self.setAcceptDrops(True)
        self.menu = MenuScreen(self)
        self.select = SelectScreen(self)
        self.game = OsuGame(self, self)
        self.results = ResultScreen(self)
        self.screens = {"menu": self.menu, "select": self.select, "game": self.game, "results": self.results}
        for s in self.screens.values():
            s.hide()
        self.game.finished.connect(self._game_finished)
        self.game.quit.connect(self._game_quit)
        self.game.retry.connect(self._retry)
        self.scrim = QWidget(self)
        self.scrim.setStyleSheet("background: rgba(0,0,0,110);")
        self.scrim.hide()
        self.scrim.mousePressEvent = lambda _e: self.toggle_settings(False)
        self.settings_panel = SettingsPanel(self)
        self.fader = Fader(self)
        self.toasts = []
        self.cur = None
        self._pending = None
        self._fade_dir = 0
        self._skins = {}
        self._last = time.perf_counter()
        self.timer = FrameTimer(self)
        self.timer.setInterval(1)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.t_start = time.time()
        self.show_screen("menu", instant=True)
        QTimer.singleShot(120, self._menu_music)
        if self.S().get("osu_scan", True):
            QTimer.singleShot(2500, lambda: self.scan_osu(force=False))

    # ── музыка при входе в esu! (как «circles!» в osu!) ── #
    def menu_music_track(self):
        """Трек, выбранный для меню (из библиотеки или карт osu!), или None."""
        path = str(self.S().get("menu_track") or "")
        return self._track_for_path(path) if path else None

    def _start_sec(self, t) -> float:
        """С какого места играть трек меню: с припева (как превью карты) или с начала."""
        if not self.S().get("menu_track_chorus", True):
            return 0.0
        try:
            if is_osu_track(t):
                return float((OB.set_by_id(t.get("esu_set")) or {}).get("preview", 0)) / 1000.0
            d = G.load_cached(str(t.get("path")), self.S().get("mapgen"))
            if d and d.get("an"):
                return G.preview_time(d["an"]) / 1000.0
        except Exception:                                             # noqa: BLE001
            pass
        return float(t.get("duration") or 0) * 0.4

    def _menu_music(self):
        S = self.S()
        mode = S.get("menu_music", "keep")
        if mode == "keep":
            return
        if mode == "random":
            pool = list(self.win.library) + OB.library_tracks()
            t = random.choice(pool) if pool else None
        else:
            t = self.menu_music_track()
        if t is None or not os.path.exists(str(t.get("path", ""))):
            return
        try:
            self._light_load(t, self._start_sec(t), autoplay=True)
        except Exception as e:                                        # noqa: BLE001
            print("[esu] menu music:", e)

    # ── настройки темы ── #
    def S(self) -> dict:
        s = self.win.settings.get("osu")
        if s is not None and s is getattr(self, "_s_filled", None):
            return s                                  # зовётся десятки раз за кадр — дополняем один раз
        s = self.win.settings.setdefault("osu", {})
        for k, v in OSU_DEFAULTS.items():
            s.setdefault(k, v if not isinstance(v, (dict, list)) else type(v)(v))
        self._s_filled = s
        return s

    osu_settings = S

    def save_settings(self):
        try:
            self.win.save_settings()
        except Exception:                                             # noqa: BLE001
            pass

    def player_name(self) -> str:
        return (self.S().get("player") or (getattr(self.win, "profile", {}) or {}).get("nickname") or "игрок")

    def apply_palette(self):
        """Сменили цвета нот: новые скины у превью, игры и редактора."""
        if self.S().get("style", "classic") != "y2k":
            SK.set_palette(self.S().get("palette"))
        self._skins = {}
        self.game._layout_sig = None
        for s in self.screens.values():
            s.update()

    def osu_look(self) -> bool:
        """Экраны как в osu! (классика); в стиле Y2K — своё оформление."""
        return self.S().get("style", "classic") != "y2k"

    def apply_look(self):
        """Вид нот встроенного скина (как в osu! / минимализм; в Y2K — минимализм под его стекло) и шар
        слайдера из скина — новые спрайты у превью, игры и редактора."""
        S = self.S()
        SK.set_look("minimal" if S.get("style", "classic") == "y2k" else S.get("note_style", "osu"))
        SK.SKIN_BALL = bool(S.get("skin_ball", False))
        self._skins = {}
        g = getattr(self, "game", None)
        if g is not None:
            g._layout_sig = None
        for s in getattr(self, "screens", {}).values():
            s.update()

    def skin_for(self, R):
        key = round(R)
        if key not in self._skins:
            self._skins[key] = SK.make_skin(R, self.devicePixelRatioF())
        return self._skins[key]

    def grade_pix(self, g, size, dpr):
        """Буква оценки со свечением (гаусс) — считается один раз, раньше — каждый кадр на каждый рекорд.
        Со скином osu! — его картинка ranking-*."""
        key = ("grade", g, int(size), round(dpr, 2), id(SK.ACTIVE))
        pm = self._pix.get(key)
        if pm is None:
            pm = None
            if SK.ACTIVE is not None:
                try:
                    import osu_skin_import
                    pm = osu_skin_import.grade(g, size, dpr)
                except Exception as e:                                # noqa: BLE001
                    print("[esu skin] grade:", e)
            self._pix[key] = pm = pm or SK.grade_pix(g, size, dpr)
        return pm

    # ── скины osu! ── #
    def skin_name(self) -> str:
        return str(self.S().get("skin") or "")

    def set_skin(self, name: str | None, palette_from_skin: bool = True):
        """Сменить скин osu! (None/"" — встроенный): спрайты, цвета комбо из skin.ini, звуки."""
        import osu_skin_import as SI
        sf = SI.activate(name or None)
        S = self.S()
        S["skin"] = name or ""
        if sf is not None and sf.combo and palette_from_skin:
            S["palette"] = SK.SKIN_PALETTE
        elif sf is None and S.get("palette") == SK.SKIN_PALETTE:
            S["palette"] = SK.DEFAULT_PALETTE
        self.save_settings()
        self._pix = {}
        self.apply_palette()
        try:
            import osu_y2k
            osu_y2k.apply_custom(self.sfx, self.win.settings)           # звуки скина (свои — поверх)
        except Exception as e:                                        # noqa: BLE001
            print("[esu skin] sounds:", e)
        return sf

    def menu_bg_path(self, t):
        """Фон главного меню: menu-background из скина osu!, если есть, иначе обложка трека."""
        if SK.ACTIVE is not None and self.S().get("skin_menu_bg", True):
            try:
                import osu_skin_import
                p = osu_skin_import.menu_background()
                if p:
                    return p
            except Exception:                                         # noqa: BLE001
                pass
        return self.bg_path(t)

    def import_skin_files(self, paths):
        """Импорт скинов (.osk / папки) в фоне; последний — сразу включается."""
        paths = [str(p) for p in paths if p]
        if not paths:
            return
        self.toast("Импортирую скин osu!…")

        def work(paths=paths):
            import osu_skin_import as SI
            last, errs = None, []
            for p in paths:
                try:
                    last = SI.import_any(p)
                except Exception as e:                                # noqa: BLE001
                    errs.append(str(e))

            def fin(last=last, errs=errs):
                if errs and not last:
                    self.toast(f"Скин не импортирован: {errs[0]}")
                    return
                sf = self.set_skin(last)
                self.toast(f"Скин osu!: {sf.name if sf else last}")
                sp = getattr(self, "settings_panel", None)
                if sp is not None:
                    sp.refresh_skins()
            self._ui(fin)
        threading.Thread(target=work, daemon=True, name="esu-skin-import").start()

    def mod_pix(self, m, size, dpr, on=True):
        key = ("mod", m, int(size), round(dpr, 2), bool(on))
        pm = self._pix.get(key)
        if pm is None:
            if len(self._pix) > 300:
                self._pix.clear()
            pm = self._pix[key] = SK.mod_icon(m, size, dpr, on)
        return pm

    # ── треки ── #
    def _track_for_path(self, path):
        """Трек (из библиотеки или набор карт osu!) по пути файла — с индексом, который
        пересобирается, только когда меняется библиотека или список карт."""
        sig = (len(self.win.library), OB._idx_cache.get("sig"))
        if self._track_by_path[0] != sig:
            d = {}
            for t in OB.library_tracks():
                d[os.path.normcase(str(t.get("path", "")))] = t
            for t in self.win.library:
                d[os.path.normcase(str(t.get("path", "")))] = t
            self._track_by_path = (sig, d)
        return self._track_by_path[1].get(os.path.normcase(str(path or "")))

    def cur_track(self):
        """То, что сейчас играет: превью выбора песни грузит трек в обход плеера, поэтому —
        по пути файла в движке, а не по номеру трека в окне плеера."""
        try:
            cp = self.win.engine.current_path
        except Exception:                                             # noqa: BLE001
            cp = None
        if cp:
            t = self._track_for_path(cp)
            if t is not None:
                return t
        try:
            return self.win._current_track()
        except Exception:                                             # noqa: BLE001
            return None

    def cover_of(self, t):
        c = (t or {}).get("cover") or ""
        if not c:
            return ""
        now = time.monotonic()
        hit = self._exists.get(c)
        if hit is None or now - hit[1] > 10.0:       # раньше — проверка файла на диске каждый кадр
            if len(self._exists) > 2000:
                self._exists.clear()
            hit = self._exists[c] = (os.path.exists(c), now)
        return c if hit[0] else ""

    def bg_path(self, t):
        return self.cover_of(t)

    def clip_entry(self, t):
        if not t:
            return None
        try:
            import clip_fetch
            return clip_fetch.lookup(t)
        except Exception:                                             # noqa: BLE001
            return None

    def clip_state(self, t):
        try:
            from clip_mode import service
            import clip_fetch
            svc = service(self.win)
            st = svc.state.get(clip_fetch.track_key(t))
            if svc.busy(t) and st:
                return f"Клип: {st[1]} {int(st[0] * 100)}%"
            if st and st[2]:
                return "Клип не найден: " + st[2]
        except Exception:                                             # noqa: BLE001
            pass
        return None

    def download_clip(self, t):
        if not t:
            return
        try:
            from clip_mode import service
            service(self.win).start(t, force=True)
            self.toast("Ищу и качаю официальный клип…")
        except Exception as e:                                        # noqa: BLE001
            self.toast(f"Клип: {e}")

    def _find(self, t):
        path = str(t.get("path", ""))
        for i, x in enumerate(self.win.library):
            if str(x.get("path", "")) == path:
                return i, "library"
        for name, pl in self.win.playlists.items():
            for i, x in enumerate(pl.get("tracks", [])):
                if str(x.get("path", "")) == path:
                    return i, name
        return None, None

    def _is_current(self, t) -> bool:
        try:
            return os.path.normcase(str(self.win.engine.current_path or "")) == os.path.normcase(str(t.get("path", "")))
        except Exception:                                             # noqa: BLE001
            return False

    def _light_load(self, t, start_sec=0.0, autoplay=False):
        """Загрузить трек в движок без «полной смены трека» плеера (тексты, клипы, история,
        предзагрузка соседей…) — превью в выборе песни раньше запускало всё это на каждый трек
        и подвешивало интерфейс. Номер трека в плеере обновляется, если трек из библиотеки."""
        eng = self.win.engine
        if getattr(self.win, "_preview", None) is not None:
            try:
                self.win._preview.stop(resume=False)
            except Exception:                                         # noqa: BLE001
                pass
        eng.load(t["path"], autoplay=autoplay, start_sec=max(0.0, float(start_sec)))
        i, ctx = self._find(t)
        if i is not None:
            try:
                self.win.current_index, self.win.queue_context = i, ctx
                self.win._track_finished_guard = False
            except Exception:                                         # noqa: BLE001
                pass
        self._synced = False

    def ensure_track(self, t):
        if self._is_current(t):
            return
        self._light_load(t, 0.0, autoplay=False)

    def preview(self, t):
        if self._is_current(t):
            if not self.win.engine.is_playing():
                self.win.engine.play()
            return
        st = self.sets.get(str(t.get("path")))
        an = st["data"]["an"] if st and st.get("data") else None
        dur = float(t.get("duration") or 0) * 1000
        if an:
            pv = G.preview_time(an)
        elif is_osu_track(t):
            pv = float((OB.set_by_id(t.get("esu_set")) or {}).get("preview", dur * 0.4))
        else:
            d = G.load_cached(str(t.get("path")), self.S().get("mapgen")) if self.cur is self.select else None
            pv = G.preview_time(d["an"]) if d and d.get("an") else dur * 0.4
        # сразу с припева: позиция старта — опцией при открытии (без прыжка звука после начала)
        self._light_load(t, pv / 1000.0 if pv > 0 else 0.0, autoplay=True)

    def sync_player(self):
        """Выход из темы: окно плеера показывает то, что реально играет (превью грузились в обход)."""
        if getattr(self, "_synced", True):
            return
        self._synced = True
        try:
            t = self.cur_track()
            i, ctx = self._find(t) if t else (None, None)
            if i is None:
                return
            w = self.win
            w.current_index, w.queue_context = i, ctx
            w._update_track_info(t)
            w._set_playing_ui(bool(w.engine.is_playing()))
            w._apply_cover_theme(t.get("cover", ""))
            w._load_lyrics()
        except Exception as e:                                        # noqa: BLE001
            print("[esu] sync:", e)

    def cur_analysis(self):
        t = self.cur_track()
        if not t:
            return None
        path = str(t.get("path"))
        st = self.sets.get(path)
        if st and st.get("data"):
            return st["data"]["an"]
        if self._an_cache[0] != path:
            d = G.load_cached(path, self.S().get("mapgen"))
            self._an_cache = (path, d["an"] if d else None)
        return self._an_cache[1]

    # ── карты ── #
    def segment_of(self, t):
        """Выбранный для карты отрывок трека [начало, конец] в мс или None — весь трек."""
        seg = (self.S().get("segments") or {}).get(str((t or {}).get("path", "")))
        return [int(seg[0]), int(seg[1])] if seg and len(seg) == 2 and seg[1] > seg[0] else None

    def set_segment(self, t, seg):
        segs = self.S().setdefault("segments", {})
        path = str(t.get("path", ""))
        if seg:
            segs[path] = [int(seg[0]), int(seg[1])]
        else:
            segs.pop(path, None)
        if len(segs) > 600:
            for k in list(segs)[:len(segs) - 600]:
                segs.pop(k, None)
        self.save_settings()

    def gen_opts(self, t) -> dict:
        o = dict(self.S().get("mapgen") or {})
        seg = self.segment_of(t)
        if seg:
            o["seg"] = seg
        return o

    def request_set(self, t, force=False, generate=False):
        """Карта трека. generate=False — только готовая из кэша (при листании ничего не строится,
        генерация — кнопкой «Сгенерировать карту»)."""
        if t is None:
            return
        path = str(t.get("path"))
        st = self.sets.get(path)
        if st and not force and st.get("state") in ("busy", "ready"):
            return
        if st and not force and not generate and st.get("state") == "idle":
            return
        if is_osu_track(t):
            self._request_osu_set(t, path)
            return
        # строится только выбранный трек: пролистанные отменяются (их анализ всё равно
        # докладывается в кэш на диске, а в памяти не висит)
        for p2, b2 in list(self.builders.items()):
            # запущенную кнопкой генерацию листание не отменяет — карта достроится и ляжет в кэш
            gen2 = not getattr(b2, "cache_only", True)
            if b2.is_alive() and ((p2 != path and not gen2) or (p2 == path and force)):
                b2.cancel.set()
                if (self.sets.get(p2) or {}).get("state") == "busy":
                    self.sets.pop(p2, None)
            if not b2.is_alive() or b2.cancel.is_set():
                self.builders.pop(p2, None)
        # в памяти — только последние карты; остальные мгновенно читаются из кэша на диске
        ready = [p2 for p2, s2 in self.sets.items() if s2.get("state") != "busy" and p2 != path]
        for p2 in ready[:max(0, len(ready) - 6)]:
            self.sets.pop(p2, None)
        self.sets[path] = {"state": "busy", "prog": (0.0, "Открываю карту…"), "data": None, "gen": generate}

        def prog(f, s, pth=path):
            self._ui(lambda: self._set_prog(pth, f, s))

        def done(res, err, pth=path):
            self._ui(lambda: self._set_done(pth, res, err))
        b = G.Builder(t, self.gen_opts(t), prog, done, cache_only=not generate)
        self.builders[path] = b
        b.start()

    def _request_osu_set(self, t, path):
        """Набор карт osu!: карты уже разобраны (osu_beatmap) — читаются с диска в фоне."""
        s = OB.set_by_id(t.get("esu_set"))
        if s is None:
            self.sets[path] = {"state": "error", "err": "набор не найден", "data": None}
            return
        ready = [p2 for p2, s2 in self.sets.items() if s2.get("state") != "busy" and p2 != path]
        for p2 in ready[:max(0, len(ready) - 6)]:
            self.sets.pop(p2, None)
        self.sets[path] = {"state": "busy", "prog": (0.3, "Открываю карты…"), "data": None}

        def work(s=dict(s), pth=path):
            try:
                maps = OB.load_set_maps(s)
                if not maps:
                    raise RuntimeError("карты набора не прочитались")
                head = max(maps.values(), key=lambda m: len(m.get("beats") or []))
                an = {"beats": head.get("beats", []), "kiai": head.get("kiai", []), "bpm": head.get("bpm", 120),
                      "dur": head.get("length", 0), "preview": s.get("preview", 0), "breaks": head.get("breaks", [])}
                res = {"an": an, "maps": maps, "offset": 0.0, "osu": True}
                self._ui(lambda: self._set_done(pth, res, ""))
            except Exception as e:                                    # noqa: BLE001
                err = str(e)
                self._ui(lambda: self._set_done(pth, None, err))
        threading.Thread(target=work, daemon=True, name="esu-osu-set").start()

    def _osu_offsets(self, res, path):
        """Смещение карт osu! по звуку: местное (клавиши +/−) и подогнанное по атакам (в фоне, один раз)."""
        auto = OB.get_offset("auto:" + path)
        for m in res["maps"].values():
            m["_local_offset"] = OB.get_offset(m.get("osu_md5", ""))
            m["_auto_offset"] = auto
        if OB.get_offset("auto_done:" + path) or not self.S().get("auto_offset", True):
            return
        notes = []
        hard = max(res["maps"].values(), key=lambda m: len(m.get("objects", [])))
        notes = [o["t"] for o in hard.get("objects", []) if o.get("k") != 2][:4000]

        def work(pth=path, notes=notes):
            try:
                import bgproc
                bgproc.idle_wait(600)
                lag = bgproc.call("osu_beatmap", "auto_offset_job", pth, notes, priority="idle")
            except Exception as e:                                    # noqa: BLE001
                print("[esu] auto offset:", e)
                return
            if lag is None:
                return
            OB.set_offset("auto:" + pth, float(lag))
            OB.set_offset("auto_done:" + pth, 1.0)

            def apply(pth=pth, lag=float(lag)):
                st = self.sets.get(pth)
                if st and st.get("data"):
                    for m in st["data"]["maps"].values():
                        m["_auto_offset"] = lag
            self._ui(apply)
        threading.Thread(target=work, daemon=True, name="esu-autooffset").start()

    def leaderboard_for(self, m) -> list:
        return [{"player": s.get("player") or "игрок", "score": s.get("score", 0), "combo": s.get("combo", 0)}
                for s in load_scores().get(map_key(m), [])[:6]]

    def _ui(self, fn):
        try:
            self.win._ui_bridge.call.emit(fn)
        except Exception:                                             # noqa: BLE001
            pass

    def _set_prog(self, path, f, s):
        st = self.sets.get(path)
        if st and st.get("state") == "busy":
            st["prog"] = (f, s)

    def _set_done(self, path, res, err):
        self.sets.pop(path, None)                       # в конец очереди «недавних»
        if res is None and err == G.Builder.NO_MAP:
            self.sets[path] = {"state": "idle", "data": None}
            return
        if res is None:
            self.sets[path] = {"state": "error", "err": err, "data": None}
            return
        if res.get("osu"):
            self._osu_offsets(res, path)
            self.sets[path] = {"state": "ready", "data": res}
            return
        for dk in list(res["maps"]):                   # правки из редактора — вместо сгенерированных
            e = G.load_edited(path, dk)
            if e is not None:
                res["maps"][dk] = e
        for m in res["maps"].values():
            m["_local_offset"] = float(res.get("offset", 0.0))
        self.sets[path] = {"state": "ready", "data": res}
        try:
            import mem_trim                          # анализ трека (весь звук в памяти) закончился
            mem_trim.trim_later(4000)
        except Exception:                            # noqa: BLE001
            pass

    def mapgen_changed(self):
        """Настройки генератора меняются (ползунок тянут): карты перестраиваются один раз,
        когда пользователь отпустил — и с последними значениями, а не с промежуточными."""
        self.save_settings()
        tm = getattr(self, "_mapgen_timer", None)
        if tm is None:
            tm = self._mapgen_timer = QTimer(self)
            tm.setSingleShot(True)
            tm.timeout.connect(self._mapgen_apply)
        tm.start(700)

    def _mapgen_apply(self):
        for path, st in list(self.sets.items()):
            if st.get("state") in ("ready", "busy"):
                del self.sets[path]
        for b in self.builders.values():
            b.cancel.set()
        self.builders.clear()
        t = self.select.cur() if self.cur is self.select else None
        if t is not None:
            self.request_set(t, force=True)

    def generate(self, t, seg=None, keep_seg=False):
        """Кнопка «Сгенерировать карту»: отрывок seg ([мс, мс] или None — весь трек)."""
        if t is None or is_osu_track(t):
            return
        if not keep_seg:
            self.set_segment(t, seg)
        self.sets.pop(str(t.get("path")), None)
        self.request_set(t, force=True, generate=True)
        seg = self.segment_of(t)
        self.toast("Модель строит карту" + (f" по отрывку {fmt_time(seg[0])}–{fmt_time(seg[1])}…" if seg else
                                            " по всему треку…"))

    def regenerate(self, t):
        g = self.S().setdefault("mapgen", {})
        g["seed"] = int(g.get("seed", 0)) + 1
        self.save_settings()
        self.sets.pop(str(t.get("path")), None)
        self.request_set(t, force=True, generate=True)
        self.toast("Модель строит другой вариант карт…")

    def export_osz(self, t):
        st = self.sets.get(str(t.get("path")))
        if not st or not st.get("data"):
            self.toast("Сначала дождитесь, пока карта построится")
            return
        d = QFileDialog.getExistingDirectory(self, "Куда сохранить .osz")
        if not d:
            return
        try:
            out = G.export_osz(t, st["data"]["maps"], d, self.cover_of(t) or None)
            self.toast(f"Готово: {out.name} — откройте его двойным щелчком, и osu! импортирует карты")
        except Exception as e:                                        # noqa: BLE001
            self.toast(f"Экспорт не удался: {e}")

    def local_offset_changed(self, m, d):
        if m.get("osu_md5"):                            # карта osu!: своё смещение у каждой сложности
            m["_local_offset"] = float(m.get("_local_offset", 0)) + d
            OB.set_offset(m["osu_md5"], m["_local_offset"])
            return
        try:
            for mm in self.sets.get(str(m.get("path")), {}).get("data", {}).get("maps", {}).values():
                mm["_local_offset"] = float(mm.get("_local_offset", 0)) + d
            st = self.sets.get(str(m.get("path")))
            off = next(iter(st["data"]["maps"].values()))["_local_offset"] if st and st.get("data") else 0
            G.set_local_offset(str(m.get("path")), off)
        except Exception:                                             # noqa: BLE001
            pass

    # ── экраны ── #
    def _run_timer(self):
        if not self.timer.isActive():
            self._last = time.perf_counter()
            self.timer.start()

    def show_screen(self, name, instant=False):
        if instant or self.cur is None:
            self._switch(name)
            return
        self._pending = name
        self._fade_dir = 1
        self._run_timer()

    def _switch(self, name):
        old = self.cur
        if old is not None:
            old.leave()
            old.hide()
        s = self.screens[name]
        s.setGeometry(self.rect())
        s.show()
        s.lower()
        self.cur = s
        if s is not self.game:
            s.enter()
        self._run_timer()                       # во время игры часы оболочки останавливаются после затемнения
        self.fader.raise_()
        s.setFocus()

    def start_game(self, m, t, mods, replay=None, start_at=None):
        self.toggle_settings(False)
        self._last_play = (m, t, set(mods))
        self._last_start = start_at

        def go():
            self._switch("game")
            self.game.start(m, t, mods, replay)
            self._attempt_begin(m, t, mods, replay)
            g = self.game
            if start_at is not None and g.play is not None and start_at > g.t + 500:
                g.skip_to = start_at                   # проверка из редактора — с текущего места
                g._skip()
        self._pending = go
        self._fade_dir = 1
        self._run_timer()

    # ── попытки ── #
    def _attempt_begin(self, m, t, mods, replay):
        self.attempt_outcome("quit")                   # прошлая попытка так и не закончилась
        self._attempt = None
        if replay is not None or "AU" in set(mods) or getattr(self, "_editor_test", False):
            return                                     # просмотр повтора / Auto / проверка из редактора — не в счёт
        try:
            key, n = attempt_begin(m, t)
        except Exception as e:                                        # noqa: BLE001
            print("[osu] attempt:", e)
            return
        self._attempt = (key, n)
        self.game._toast(f"Попытка {n}")

    def attempt_outcome(self, outcome, res=None) -> dict:
        a, self._attempt = getattr(self, "_attempt", None), None
        if not a:
            return {}
        try:
            e = attempt_end(a[0], outcome, res)
        except Exception as ex:                                       # noqa: BLE001
            print("[osu] attempt:", ex)
            return {}
        self.select._attempts_key = None               # строка в выборе песни — перечитать
        e = dict(e)
        e["_n"] = a[1]
        return e

    def attempts_dialog(self):
        from osu_attempts import AttemptsDialog
        AttemptsDialog(self).exec()

    def goto_map(self, path, dk):
        """Из списка попыток — этот трек и сложность в выборе песни."""
        sel = self.select
        if self.cur is not sel:
            self.show_screen("select")
        sel.search = ""
        sel.refresh()
        i = next((k for k, t in enumerate(sel.tracks) if str(t.get("path")) == str(path)), None)
        if i is None:
            self.toast("Этого трека уже нет в коллекции")
            return
        if dk in DIFF_KEYS:
            sel.diff = dk
        elif is_osu_track(sel.tracks[i]):
            sel.osu_diff[sel.tracks[i].get("esu_set")] = dk
        sel._select(i)

    # ── редактор карт ── #
    def open_editor(self, m, t, dk):
        ed = self.screens.get("editor")
        if ed is None:
            from osu_editor import EditorScreen
            ed = EditorScreen(self)
            ed.hide()
            ed.setGeometry(self.rect())
            self.screens["editor"] = ed
        ed.open(m, t, dk)
        self.toggle_settings(False)
        self.show_screen("editor")

    def test_map(self, m, t, start_ms, mods):
        """F5 в редакторе: сыграть карту с текущего места, после — обратно в редактор."""
        self._editor_test = True
        self.start_game(m, t, mods, start_at=max(0.0, start_ms - 1500))

    def _back_to_editor(self) -> bool:
        if getattr(self, "_editor_test", False) and "editor" in self.screens:
            self._editor_test = False
            self.show_screen("editor")
            return True
        return False

    def _game_quit(self):
        self.attempt_outcome("quit")
        if self._back_to_editor():
            return
        self.show_screen("select")
        try:
            if not self.win.engine.is_playing():
                self.win.engine.play()
        except Exception:                                             # noqa: BLE001
            pass

    def _retry(self):
        m, t, mods = getattr(self, "_last_play", (None, None, None))
        if m is None:
            return
        self.attempt_outcome("quit")                     # рестарт — эта попытка не дошла до конца
        self.game.stop(silent=True)
        start = getattr(self, "_last_start", None) if getattr(self, "_editor_test", False) else None
        self.start_game(m, t, mods, start_at=start)

    def _game_finished(self, res):
        if self._back_to_editor():                     # проверка из редактора: без рекордов, сразу назад
            return
        m, t, _ = self._last_play
        e = self.attempt_outcome("pass", res) if not res.get("watched") else {}
        if e:
            res["attempt"] = e.get("_n", 0)
            res["first_pass"] = int(e.get("first_pass") or 0)
        rank = save_score(m, res, self.player_name())
        self.results.show_result(res, m, t, rank)
        self.select._scores_key = None
        self.show_screen("results")
        try:
            if not self.win.engine.is_playing():       # трек доиграл вместе с картой — снова с припева
                self.on_track_end()
        except Exception:                                             # noqa: BLE001
            pass

    def holds(self) -> bool:
        return self.cur in (self.game, self.select, self.results) or \
            (self.cur is not None and self.cur is self.screens.get("editor"))

    def on_track_end(self):
        """Трек доиграл, пока открыт выбор песни / результаты / игра: повторить с припева."""
        t = self.cur_track()
        if self.cur is self.game and self.game.state not in ("idle",):
            return
        ed = self.screens.get("editor")
        if ed is not None and self.cur is ed:
            ed.on_track_end()                          # в редакторе — просто остановиться в конце
            return
        if t is None:
            return
        an = self.cur_analysis()
        pv = G.preview_time(an) / 1000 if an else float(t.get("duration") or 0) * 0.4
        try:
            self.win.engine.seek(pv)
            self.win.engine.play()
        except Exception:                                             # noqa: BLE001
            pass

    def toggle_settings(self, on=None):
        p = self.settings_panel
        on = (not p.isVisible()) if on is None else on
        if on:
            self._adopt_into_settings()
            p.guard_wheel()
            p.resync()
            self.scrim.setGeometry(self.rect())
            self.scrim.show()
            self.scrim.raise_()
            p.setGeometry(0, 0, SettingsPanel.W, self.height())
            p.show()
            p.raise_()
            self.fader.raise_()
            self.sfx.play("menuhit", 0.6)
        else:
            if p.isVisible():
                self.sfx.play("menuback", 0.5)
            p.hide()
            self.scrim.hide()
            if self.cur is not None:
                self.cur.setFocus()

    def leave_theme(self):
        self.toggle_settings(False)
        QTimer.singleShot(0, lambda: self.win._apply_theme("Echoes Music"))

    # ── карты из osu!: импорт ── #
    def songs_dir(self) -> str | None:
        d = self.S().get("osu_songs") or ""
        if d and os.path.isdir(d):
            return d
        return OB.find_osu_songs()

    def scan_osu(self, force=False):
        """Папка Songs osu! → новые и изменённые наборы (в отдельном процессе, интерфейс не ждёт)."""
        d = self.songs_dir()
        if not d:
            if force:
                self.toast("osu! не найден — укажите папку Songs в настройках или откройте .osz")
            return
        if self._osu_job is not None and self._osu_job.is_alive():
            if force:
                self.toast("Карты из osu! уже обновляются…")
            return
        known = OB.known_dirs()
        if force:
            self.toast("Ищу новые карты в папке osu!…")

        def work(d=d, known=known, force=force):
            import bgproc
            try:
                r = bgproc.call("osu_beatmap", "scan_songs_job", d, known,
                                progress=lambda f, s: setattr(self, "_osu_job_text", f"{s} {int(f * 100)}%"))
                added = OB.merge_index(r.get("entries", []), d, r.get("seen", []))
                n_new = len(r.get("entries", []))
                msg = (f"Карты osu!: новых наборов {added}" if added else
                       ("Карты osu!: обновлено наборов " + str(n_new) if n_new else "Карты osu!: новых нет"))
            except Exception as e:                                    # noqa: BLE001
                msg = f"Карты osu! не обновились: {e}"
                added = 0
            self._osu_job_text = ""

            def fin(msg=msg, added=added, force=force):
                try:                                       # тему успели закрыть, пока шло сканирование
                    if force or added:
                        self.toast(msg)
                    self.select._lib_sig = None
                    self.settings_panel.refresh_osu_status()
                except RuntimeError:
                    pass
            self._ui(fin)
        self._osu_job = threading.Thread(target=work, daemon=True, name="esu-osu-scan")
        self._osu_job.start()

    def import_files(self, paths):
        paths = [str(x) for x in paths if str(x).lower().endswith((".osz", ".osu")) or os.path.isdir(str(x))]
        if not paths:
            return
        self.toast("Добавляю карты osu!…")

        def work(paths=list(paths)):
            import bgproc
            try:
                entries = bgproc.call("osu_beatmap", "import_files_job", paths)
                added = OB.merge_index(entries)
                n = sum(len(e.get("diffs", [])) for e in entries)
                bad = [s for e in entries for s in e.get("skipped", [])]
                msg = f"Добавлено наборов: {added}, карт: {n}" if n else \
                    ("В файлах нет карт osu!standard" + (f" ({bad[0]})" if bad else ""))
                first = next((e for e in entries if e.get("diffs")), None)
            except Exception as e:                                    # noqa: BLE001
                msg, first = f"Импорт не удался: {e}", None

            def fin(msg=msg, first=first):
                self.toast(msg)
                self.select._lib_sig = None
                self.settings_panel.refresh_osu_status()
                if first is not None:
                    self.goto_set(first.get("id"))
            self._ui(fin)
        threading.Thread(target=work, daemon=True, name="esu-osu-import").start()

    def import_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Карты osu!", os.path.expanduser("~"),
                                                "Карты osu! (*.osz *.osu)")
        if files:
            self.import_files(files)

    def pick_songs_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Папка Songs установленного osu!", self.songs_dir() or "")
        if d:
            self.S()["osu_songs"] = d
            self.save_settings()
            self.scan_osu(force=True)

    def forget_set(self, t):
        d = t.get("esu_dir")
        if d:
            OB.remove_set(d)
            self.sets.pop(str(t.get("path")), None)
            self.select._lib_sig = None
            self.toast("Набор убран из esu! (в osu! он остался)")

    def goto_set(self, sid):
        sel = self.select
        if self.cur is not sel:
            self.show_screen("select")
        sel.search = ""
        sel.refresh()
        i = next((k for k, t in enumerate(sel.tracks) if t.get("esu_set") == sid), None)
        if i is not None:
            sel._select(i)

    def osu_menu(self, gpos=None):
        from PyQt6.QtGui import QCursor
        m = QMenu(self)
        m.addAction("Обновить из папки osu!", lambda: self.scan_osu(force=True))
        m.addAction("Открыть файлы .osz / .osu…", self.import_files_dialog)
        m.addAction("Выбрать папку Songs…", self.pick_songs_dir)
        m.addSeparator()
        m.addAction("Показать только карты osu!", lambda: (self.select._set_coll(SelectScreen.COLL_OSU),
                                                           self.show_screen("select")))
        m.exec(gpos or QCursor.pos())

    # перетаскивание .osz / .osu (карты) и .osk (скины) в окно
    @staticmethod
    def _drop_files(e):
        md = e.mimeData()
        if md is None or not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()
                and u.toLocalFile().lower().endswith((".osz", ".osu", ".osk"))]

    def dragEnterEvent(self, e):
        if self._drop_files(e):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self._drop_files(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = self._drop_files(e)
        if files:
            e.acceptProposedAction()
            skins = [f for f in files if f.lower().endswith(".osk")]
            maps = [f for f in files if not f.lower().endswith(".osk")]
            if maps:
                self.import_files(maps)
            if skins:
                self.import_skin_files(skins)

    def theme_menu(self, gpos=None):
        """Список всех тем плеера (встроенные и свои) — выбрать и сразу переключиться."""
        from PyQt6.QtGui import QCursor
        try:
            from themes import theme_groups
            groups = theme_groups()
        except Exception:                                             # noqa: BLE001
            groups = []
        m = QMenu(self)
        m.setStyleSheet("QMenu { background: #231a2b; color: #fff; border: 1px solid #ff66aa; border-radius: 10px;"
                        " padding: 6px; font-size: 14px; } QMenu::item { padding: 7px 26px 7px 14px; border-radius: 6px; }"
                        " QMenu::item:selected { background: #ff66aa; } QMenu::item:disabled { color: #ff9fc8;"
                        " font-weight: 800; font-size: 11px; } QMenu::separator { height: 1px; background: #4a3d52;"
                        " margin: 5px 8px; }")
        cur = self.win.settings.get("theme", "")
        for gi, (title, _hint, names) in enumerate(groups):
            if gi:
                m.addSeparator()
            h = m.addAction(title)
            h.setEnabled(False)
            for n in names:
                a = m.addAction(("●  " if n == cur else "    ") + n)
                a.triggered.connect(lambda _=False, n=n: (self.toggle_settings(False),
                                                          QTimer.singleShot(0, lambda: self.win._apply_theme(n))))
        m.addSeparator()
        m.addAction("Конструктор тем…", lambda: self.win._open_theme_studio())
        self.sfx.play("menuclick", 0.7)
        m.exec(gpos or QCursor.pos())

    def toast(self, s):
        self.toasts.append([s, time.perf_counter()])
        self.update()

    # ── кадр ── #
    def _tick(self):
        now = time.perf_counter()
        dt = animation_delta(now - self._last)
        self._last = now
        if self.win.isMinimized():
            return
        self.pulse.update(dt)
        if self._fade_dir:
            self.fader.a += self._fade_dir * dt / 0.18
            if self._fade_dir > 0 and self.fader.a >= 1.0:
                self.fader.a = 1.0
                pend, self._pending = self._pending, None
                if callable(pend):
                    pend()
                elif pend:
                    self._switch(pend)
                self._fade_dir = -1
            elif self._fade_dir < 0 and self.fader.a <= 0.0:
                self.fader.a = 0.0
                self._fade_dir = 0
                if self.cur is self.game:
                    self.timer.stop()
            self.fader.update()
        cur = self.cur
        if cur is not None and cur is not self.game:
            cur.step(dt, now)
            cur.update()
        self.toasts = [t for t in self.toasts if now - t[1] < 3.2]
        if self.toasts or getattr(self, "_toast_vis", False):
            self._toast_vis = bool(self.toasts)
            self.fader.update()
        # пустое затемнение не держим видимым: прозрачный слой поверх экрана перерисовывался бы с каждым кадром
        vis = self.fader.a > 0.003 or bool(self.toasts)
        if vis != self.fader.isVisible():
            self.fader.setVisible(vis)
            if vis:
                self.fader.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for s in self.screens.values():
            s.setGeometry(self.rect())
        self.fader.setGeometry(self.rect())
        self.scrim.setGeometry(self.rect())
        if self.settings_panel.isVisible():
            self.settings_panel.setGeometry(0, 0, SettingsPanel.W, self.height())

    # ── настройки плеера (правая панель) ── #
    def adopt_settings(self, panel):
        self._settings_host = panel
        panel.hide()

    def _adopt_into_settings(self):
        p = self._settings_host
        if p is not None and p.parent() is not self.settings_panel:
            self.settings_panel.player_slot.addWidget(p)
            p.show()

    def release_settings(self):
        p = self._settings_host
        if p is not None:
            try:
                self.settings_panel.player_slot.removeWidget(p)
            except Exception:                                         # noqa: BLE001
                pass
            p.setParent(None)
        self._settings_host = None
        return p

    def shutdown(self):
        self.attempt_outcome("quit")
        self.thumbs.stop()
        self.sync_player()
        self.timer.stop()
        try:
            self.game.stop(silent=True)
        except Exception:                                             # noqa: BLE001
            pass
        for b in self.builders.values():
            b.cancel.set()
        tm = getattr(self, "_mapgen_timer", None)
        if tm is not None:
            tm.stop()
        osu_sfx.release()
