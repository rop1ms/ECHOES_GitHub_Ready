# net_quiz_live.py
"""
«Угадай трек вместе» — живая игра с другом или друзьями в Echoes.NET.

Ведущий зовёт друзей (кто в сети). Каждый, кто согласился, присылает ~30 случайных треков своей коллекции
(только названия). Раунды берут треки по очереди из коллекций ВСЕХ игроков и перемешиваются: никто не знает,
чей трек сейчас. У всех одновременно звучат 30 секунд вслепую (свой файл — сразу, чужой — превью из сети),
ответ пишется в поле; кто быстрее угадал — больше очков (100 → 10). Раунд кончается, когда угадали все
или вышло время; потом — ответ и чья это коллекция. В конце — победитель, рекорды идут в профиль
(«побед в Угадай трек», «лучший счёт»).

Сеть: сообщения quiz_* (net_core.QUIZ_TYPES) ходят только между ведущим и игроками; ответы проверяет ведущий.
"""
from __future__ import annotations

import random
import secrets
import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QLinearGradient
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

POOL = 30                    # сколько своих треков отдаёт каждый игрок
REVEAL = 4.5                 # секунд показывать ответ между раундами


def my_pool(win, n=POOL) -> list:
    """Случайные треки своей коллекции → описания для сети (без путей)."""
    from net_page import track_meta
    lib = [t for t in win.library if (t.get("title") or "").strip()]
    random.shuffle(lib)
    out, seen = [], set()
    from net_page import tkey
    for t in lib:
        k = tkey(t)
        if k in seen:
            continue
        seen.add(k)
        m = track_meta(t)
        if m:
            out.append(m)
        if len(out) >= n:
            break
    return out


def points(sec, secs) -> int:
    return max(10, int(round(100 - 90 * min(1.0, sec / max(1.0, secs)))))


# ══════════════════════════ визуал ══════════════════════════ #

class _Stage(QWidget):
    """Сцена раунда: «слушайте…» с живыми столбиками или ответ (обложка, название, чья коллекция)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.mode = "wait"           # wait | listen | reveal | end
        self.text = "Ждём игроков…"
        self.sub = ""
        self.cover = None
        self.left = 0.0
        self.secs = 30.0
        self._t0 = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.update)
        self.timer.start()

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        try:
            import ya_decor
            acc = ya_decor.accent()
        except Exception:                                      # noqa: BLE001
            acc = QColor("#ffdb1a")
        r = QRectF(0, 0, W, H)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        g = QLinearGradient(0, 0, W, H)
        c0 = QColor(acc).darker(380)
        g.setColorAt(0, c0)
        g.setColorAt(1, QColor(10, 10, 12))
        p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawPath(path)
        t = time.monotonic() - self._t0
        if self.mode == "listen":                              # живые столбики
            n = 24
            bw = min(14.0, W * 0.6 / n)
            x0 = W / 2 - n * bw * 1.4 / 2
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(n):
                v = 0.25 + 0.75 * abs(math.sin(t * (2.4 + (i % 5) * 0.6) + i * 0.9))
                h = 18 + v * (H * 0.42)
                col = QColor(acc)
                col.setAlpha(int(140 + 100 * v))
                p.setBrush(col)
                p.drawRoundedRect(QRectF(x0 + i * bw * 1.4, H * 0.5 - h / 2, bw, h), bw / 2, bw / 2)
        elif self.mode == "reveal" and self.cover is not None:
            cs = H - 60
            cr = QRectF(30, 30, cs, cs)
            cp = QPainterPath()
            cp.addRoundedRect(cr, 14, 14)
            p.save()
            p.setClipPath(cp)
            p.drawPixmap(cr, self.cover, QRectF(self.cover.rect()))
            p.restore()
        f = QFont()
        f.setPixelSize(28 if self.mode != "reveal" else 24)
        f.setWeight(QFont.Weight.Black)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        tx = 30 + (H - 60) + 24 if self.mode == "reveal" and self.cover is not None else 0
        tr = QRectF(tx, H * 0.08 if self.mode == "listen" else 0, W - tx - 20, H * 0.84 if self.mode != "listen" else 50)
        al = (Qt.AlignmentFlag.AlignLeft if tx else Qt.AlignmentFlag.AlignHCenter) | Qt.AlignmentFlag.AlignVCenter
        if self.mode == "listen":
            p.drawText(QRectF(0, 12, W, 40), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self.text)
        else:
            p.drawText(tr.adjusted(0, -16, 0, -16), al | Qt.TextFlag.TextWordWrap, self.text)
        if self.sub:
            f2 = QFont()
            f2.setPixelSize(14)
            f2.setBold(True)
            p.setFont(f2)
            p.setPen(QColor(255, 255, 255, 170))
            if self.mode == "listen":
                p.drawText(QRectF(0, H - 44, W, 30), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                           self.sub)
            else:
                p.drawText(tr.adjusted(0, 26, 0, 26), al | Qt.TextFlag.TextWordWrap, self.sub)
        if self.mode == "listen" and self.secs > 0:            # таймер раунда
            k = max(0.0, min(1.0, self.left / self.secs))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(QRectF(20, H - 10, W - 40, 5), 2.5, 2.5)
            p.setBrush(acc if k > 0.25 else QColor("#ff5c5c"))
            p.drawRoundedRect(QRectF(20, H - 10, (W - 40) * k, 5), 2.5, 2.5)
        p.end()


# ══════════════════════════ игра ══════════════════════════ #

class QuizLive(QDialog):
    """Окно игры. role="host" — ведущий (зовёт, ведёт раунды, проверяет); "guest" — игрок."""

    def __init__(self, net, role, gid, host="", host_name="", rounds=10, secs=30):
        super().__init__(net.win)
        self.net, self.win, self.nd = net, net.win, net.node
        self.role, self.gid = role, gid
        self.host, self.host_name = host, host_name
        self.rounds, self.secs = rounds, float(secs)
        self.me = self.nd.me.onion
        self.my_name = self.nd._name()
        self.players = {self.me: self.my_name}                 # onion → имя (в игре)
        self.invited = {}                                      # ведущий: onion → состояние
        self.pools = {}                                        # ведущий: onion → [треки]
        self.scores = {}                                       # onion → очки
        self.queue = []                                        # ведущий: [(трек, onion владельца)]
        self.n = 0
        self.cur = None                                        # текущий трек
        self.cur_owner = ""
        self.solved = {}                                       # onion → секунды в этом раунде
        self.t_round = 0.0                                     # свой старт раунда (звук пошёл здесь)
        self.waiting = set()                                   # ведущий: кто ещё не начал раунд у себя
        self.t_sent = 0.0
        self.t_clock = 0.0                                     # ведущий: отсчёт раунда (после «готов» от всех)
        self.state = "lobby"
        self.setWindowTitle("Угадай трек вместе")
        self.resize(880, 600)
        import net_frames
        self.setStyleSheet(net_frames._QSS + "QLineEdit{background:#1d1d20;color:#f0f0f0;border:1px solid #2c2c30;"
                           "border-radius:12px;padding:9px 14px;font-size:15px;}")
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(10)
        hd = QHBoxLayout()
        t = QLabel("Угадай трек вместе")
        t.setStyleSheet("font-size:22px;font-weight:900;")
        hd.addWidget(t)
        hd.addStretch(1)
        self.round_lbl = QLabel("")
        self.round_lbl.setObjectName("Sub")
        hd.addWidget(self.round_lbl)
        left.addLayout(hd)
        self.stage = _Stage()
        left.addWidget(self.stage, 1)
        ar = QHBoxLayout()
        self.ans = QLineEdit()
        self.ans.setPlaceholderText("Название трека…")
        self.ans.returnPressed.connect(self._answer)
        ar.addWidget(self.ans, 1)
        self.b_ans = QPushButton("Ответить")
        self.b_ans.setObjectName("Accent")
        self.b_ans.clicked.connect(self._answer)
        ar.addWidget(self.b_ans)
        left.addLayout(ar)
        self.log_lbl = QLabel("")
        self.log_lbl.setObjectName("Sub")
        self.log_lbl.setWordWrap(True)
        left.addWidget(self.log_lbl)
        root.addLayout(left, 1)
        # ── справа: игроки / лобби ──
        right = QVBoxLayout()
        right.setSpacing(8)
        self.side_title = QLabel("ИГРОКИ")
        self.side_title.setObjectName("Sec")
        right.addWidget(self.side_title)
        self.board = QVBoxLayout()
        self.board.setSpacing(4)
        right.addLayout(self.board)
        self.lobby = QWidget()
        lv = QVBoxLayout(self.lobby)
        lv.setContentsMargins(0, 8, 0, 0)
        lv.setSpacing(6)
        if role == "host":
            lv.addWidget(net_frames._sec("РАУНДЫ"))
            rr = QHBoxLayout()
            g1 = QButtonGroup(self)
            for n in (5, 10, 15):
                b = QPushButton(str(n))
                b.setCheckable(True)
                b.setChecked(n == rounds)
                b.clicked.connect(lambda _=False, n=n: setattr(self, "rounds", n))
                g1.addButton(b)
                rr.addWidget(b)
            lv.addLayout(rr)
            lv.addWidget(net_frames._sec("СЕКУНД НА ОТВЕТ"))
            sr = QHBoxLayout()
            g2 = QButtonGroup(self)
            for n in (15, 30, 45):
                b = QPushButton(str(n))
                b.setCheckable(True)
                b.setChecked(n == int(secs))
                b.clicked.connect(lambda _=False, n=n: setattr(self, "secs", float(n)))
                g2.addButton(b)
                sr.addWidget(b)
            lv.addLayout(sr)
            self.b_start = QPushButton("Начать игру")
            self.b_start.setObjectName("Accent")
            self.b_start.setEnabled(False)
            self.b_start.clicked.connect(self.start)
            lv.addWidget(self.b_start)
        else:
            self.inv_lbl = QLabel(f"{host_name} зовёт угадывать треки — из ваших коллекций вперемешку.")
            self.inv_lbl.setWordWrap(True)
            lv.addWidget(self.inv_lbl)
            self.b_join = QPushButton("Играть")
            self.b_join.setObjectName("Accent")
            self.b_join.clicked.connect(self.join)
            self.b_no = QPushButton("Не сейчас")
            self.b_no.clicked.connect(self.decline)
            jr = QHBoxLayout()
            jr.addWidget(self.b_join)
            jr.addWidget(self.b_no)
            lv.addLayout(jr)
        right.addWidget(self.lobby)
        right.addStretch(1)
        self.rec_lbl = QLabel(self._record_text())
        self.rec_lbl.setObjectName("Sub")
        self.rec_lbl.setWordWrap(True)
        right.addWidget(self.rec_lbl)
        self.b_again = QPushButton("Ещё раз")
        self.b_again.hide()
        self.b_again.clicked.connect(self._again)
        right.addWidget(self.b_again)
        b_close = QPushButton("Выйти")
        b_close.clicked.connect(self.close)
        right.addWidget(b_close)
        side = QWidget()
        side.setFixedWidth(240)
        side.setLayout(right)
        root.addWidget(side)
        self.ans.setEnabled(False)
        self.b_ans.setEnabled(False)
        self.tick = QTimer(self)
        self.tick.setInterval(100)
        self.tick.timeout.connect(self._tick)
        self.tick.start()
        self.stage.text = "Ждём игроков…" if role == "host" else f"{host_name} зовёт в игру"
        self._board()

    # ── сеть ── #

    def send(self, onion, msg):
        msg = dict(msg, gid=self.gid)
        self.nd.call(self.nd.quiz_send, onion, msg)

    def broadcast(self, msg):
        for o in self.players:
            if o != self.me:
                self.send(o, msg)

    def on_message(self, o, m):
        t = m.get("t")
        if self.role == "host":
            if t == "quiz_join" and o in self.invited:
                self.invited[o] = "в игре"
                self.players[o] = m.get("name") or self.net._contact_label(o)
                self.pools[o] = m.get("pool") or []
                self.scores.setdefault(o, 0)
                self._log(f"{self.players[o]} в игре — принёс {len(self.pools[o])} треков")
                if self.state == "lobby":
                    self.b_start.setEnabled(True)
            elif t == "quiz_no" and o in self.invited:
                self.invited[o] = "отказался"
            elif t == "quiz_leave":
                if o in self.invited:
                    self.invited[o] = "не в сети" if m.get("offline") else "вышел"
                if o in self.players and o != self.me:
                    self._log(f"{self.players.pop(o)} вышел")
                    if self.state == "lobby" and not any(v == "в игре" for v in self.invited.values()):
                        self.b_start.setEnabled(False)
            elif t == "quiz_ready" and int(m.get("n") or 0) == self.n:
                self.waiting.discard(o)
                if not self.waiting and not self.t_clock:
                    self.t_clock = time.monotonic()            # раунд начался у всех — пошёл отсчёт
            elif t == "quiz_ans" and self.state == "round" and int(m.get("n") or 0) == self.n:
                self._check(o, m.get("text") or "", m.get("sec"))
            self._board()
            return
        # игрок
        if t == "quiz_start":
            self.players = {k: (v if isinstance(v, str) else v[0]) for k, v in (m.get("players") or {}).items()}
            for k in self.players:
                self.scores.setdefault(k, 0)
            if self.state == "lobby":                          # сообщения могут прийти не по порядку
                self.state = "playing"
            self.lobby.hide()
            self._log("Игра началась")
        elif t == "quiz_round":
            self.n = int(m.get("n") or 0)
            self.rounds = int(m.get("total") or self.rounds)
            self.secs = float(m.get("secs") or 30)
            self._begin_round(m.get("track"))
        elif t == "quiz_hit":
            who = m.get("who") or ""
            self.solved[who] = float(m.get("sec") or 0)
            self.scores[who] = self.scores.get(who, 0) + int(m.get("pts") or 0)
            self._log(f"{m.get('name') or '?'} угадал за {float(m.get('sec') or 0):.0f} с  +{int(m.get('pts') or 0)}")
        elif t == "quiz_reveal":
            self._apply_scores(m.get("scores"))
            self._reveal(m.get("track"), m.get("owner") or "")
        elif t == "quiz_end":
            self._apply_scores(m.get("scores"))
            self._end()
        elif t == "quiz_leave" and o == self.host:
            self._log("Ведущий вышел — игра окончена" if not m.get("offline") else "Ведущий не в сети")
            self._end()
        self._board()

    # ── ведущий ── #

    def invite(self, onions):
        for o in onions:
            self.invited[o] = "ждём ответа"
            self.send(o, {"t": "quiz_inv", "name": self.my_name, "total": self.rounds, "secs": self.secs})
        self._board()

    def start(self):
        joined = [o for o, s in self.invited.items() if s == "в игре"]
        if not joined:
            return
        self.pools[self.me] = my_pool(self.win)
        self.scores = {o: 0 for o in self.players}
        owners = [o for o in self.players if self.pools.get(o)]
        for o in owners:
            random.shuffle(self.pools[o])
        from net_page import tkey
        seen, q, i = set(), [], 0
        while len(q) < self.rounds and any(self.pools.get(o) for o in owners):
            o = owners[i % len(owners)]                        # по очереди из коллекций всех
            i += 1
            while self.pools.get(o):
                tr = self.pools[o].pop()
                if tkey(tr) not in seen:
                    seen.add(tkey(tr))
                    q.append((tr, o))
                    break
        random.shuffle(q)                                      # чья очередь — не угадать по порядку
        self.queue = q
        self.rounds = len(q)
        if not q:
            self._log("Нет треков: коллекции пустые")
            return
        self.state = "playing"
        self.lobby.hide()
        self.broadcast({"t": "quiz_start", "players": dict(self.players)})
        QTimer.singleShot(600, self._next_round)

    def _next_round(self):
        if self.role != "host" or self.state == "end":
            return
        if not self.queue:
            self.broadcast({"t": "quiz_end", "scores": self._scores_msg()})
            self._end()
            return
        tr, owner = self.queue.pop(0)
        self.n += 1
        self.cur_owner = owner
        self.waiting = {o for o in self.players if o != self.me}
        self.t_sent, self.t_clock = time.monotonic(), 0.0
        self.broadcast({"t": "quiz_round", "n": self.n, "total": self.rounds, "secs": self.secs, "track": tr})
        self._begin_round(tr)
        if not self.waiting:
            self.t_clock = time.monotonic()

    def _check(self, o, text, sec=None):
        if o in self.solved or not self.cur:
            return
        import net_music
        if not net_music.guess_ok(text, self.cur):
            if o == self.me:
                self._log("Мимо")
            return
        own = time.monotonic() - self.t_round                  # у игрока — его время с начала звука у него
        sec = max(0.0, min(self.secs, float(sec))) if sec is not None and o != self.me else own
        pts = points(sec, self.secs)
        self.solved[o] = sec
        self.scores[o] = self.scores.get(o, 0) + pts
        name = self.players.get(o, "?")
        self.broadcast({"t": "quiz_hit", "n": self.n, "who": o, "name": name, "sec": round(sec, 1), "pts": pts})
        self._log(f"{name} угадал за {sec:.0f} с  +{pts}")
        if len(self.solved) >= len(self.players):
            self._end_round()

    def _end_round(self):
        if self.state != "round":
            return
        self.state = "reveal"
        owner = self.players.get(self.cur_owner, "?")
        self.broadcast({"t": "quiz_reveal", "n": self.n, "track": self.cur, "owner": owner,
                        "scores": self._scores_msg()})
        self._reveal(self.cur, owner)
        QTimer.singleShot(int(REVEAL * 1000), self._next_round)

    def _scores_msg(self) -> dict:
        return {o: [self.players.get(o, "?"), self.scores.get(o, 0)] for o in self.players}

    # ── игрок ── #

    def join(self):
        self.send(self.host, {"t": "quiz_join", "name": self.my_name, "pool": my_pool(self.win)})
        self.b_join.setEnabled(False)
        self.b_no.hide()
        self.inv_lbl.setText("Вы в игре. Ждём, когда ведущий начнёт…")
        self.stage.text = "Ждём начала…"

    def decline(self):
        self.send(self.host, {"t": "quiz_no"})
        self.close()

    # ── общее ── #

    def _begin_round(self, tr):
        self.cur = tr
        self.solved = {}
        self.t_round = time.monotonic()
        self.state = "round"
        self.round_lbl.setText(f"Раунд {self.n} из {self.rounds}")
        self.stage.mode = "listen"
        self.stage.text = "Что это за трек?"
        self.stage.sub = "слушайте — у всех играет одно и то же"
        self.stage.cover = None
        self.stage.secs = self.secs
        self.ans.setEnabled(True)
        self.b_ans.setEnabled(True)
        self.ans.clear()
        self.ans.setFocus()
        self._play(tr)
        if self.role == "guest":                               # «у меня пошло» — ведущий начнёт отсчёт
            self.send(self.host, {"t": "quiz_ready", "n": self.n})

    def _play(self, tr):
        """Свой файл — сразу; чужой — превью из сети (как в «Угадай трек» в ленте)."""
        try:
            import net_music
            from preview import get_preview
            _i, local = net_music.library_match(self.win, tr)
            url = str(local.get("path")) if local and local.get("path") else net_music.preview_url(tr)
            self._pv = getattr(self, "_pv", None) or (lambda st, left: None)
            get_preview(self.win).play(url, int((tr or {}).get("duration") or 0), self._pv)
        except Exception as e:                                 # noqa: BLE001
            self._log(f"Не удалось включить: {e}")

    def _stop_audio(self):
        try:
            from preview import get_preview
            pv = get_preview(self.win)
            if pv.active_for(getattr(self, "_pv", None)):
                pv.stop()
        except Exception:                                      # noqa: BLE001
            pass

    def _answer(self):
        text = self.ans.text().strip()
        if not text or self.state != "round" or self.me in self.solved:
            return
        self.ans.clear()
        if self.role == "host":
            self._check(self.me, text)
        else:
            import net_music
            if net_music.guess_ok(text, self.cur):             # сразу видно своё; очки считает ведущий
                self._log("Верно! Ждём остальных…")
                self.ans.setEnabled(False)
                self.b_ans.setEnabled(False)
            else:
                self._log("Мимо")
            self.send(self.host, {"t": "quiz_ans", "n": self.n, "text": text,
                                  "sec": round(time.monotonic() - self.t_round, 1)})

    def _reveal(self, tr, owner):
        self._stop_audio()
        self.state = "reveal"
        self.ans.setEnabled(False)
        self.b_ans.setEnabled(False)
        tr = tr or {}
        self.stage.mode = "reveal"
        self.stage.text = f"{tr.get('title', '')}"
        self.stage.sub = f"{tr.get('artist', '')}\nиз коллекции: {owner}"
        try:
            import net_music
            from yamusic_theme import cover_pixmap
            _i, local = net_music.library_match(self.win, tr)
            cov = (local or {}).get("cover") or self.net._local_cover(tr) or ""
            self.stage.cover = cover_pixmap(cov, 220, 14, seed=tr.get("title", ""))
        except Exception:                                      # noqa: BLE001
            self.stage.cover = None

    def _apply_scores(self, sc):
        if isinstance(sc, dict):
            for o, v in sc.items():
                if isinstance(v, list) and len(v) >= 2:
                    self.players.setdefault(o, v[0])
                    self.scores[o] = int(v[1])

    def _end(self):
        if self.state == "end":
            return
        self._stop_audio()
        self.state = "end"
        self.ans.setEnabled(False)
        self.b_ans.setEnabled(False)
        ranked = sorted(self.scores.items(), key=lambda kv: -kv[1])
        if ranked:
            best = ranked[0]
            win = self.players.get(best[0], "?")
            self.stage.mode = "end"
            self.stage.cover = None
            self.stage.text = "Победа!" if best[0] == self.me else f"Победил {win}"
            self.stage.sub = "  ·  ".join(f"{self.players.get(o, '?')}: {s}" for o, s in ranked)
            self._save_record(self.scores.get(self.me, 0), best[0] == self.me and best[1] > 0)
        self.round_lbl.setText("Игра окончена")
        if self.role == "host":
            self.b_again.show()
        self._board()

    def _again(self):
        self.close()
        self.net.quiz_live_start(list(o for o in self.players if o != self.me))

    def _save_record(self, score, won):
        s = self.win.settings.setdefault("quiz_live", {})
        s["games"] = int(s.get("games", 0)) + 1
        s["wins"] = int(s.get("wins", 0)) + (1 if won else 0)
        s["best"] = max(int(s.get("best", 0)), int(score))
        try:
            self.win.save_settings()
        except Exception:                                      # noqa: BLE001
            pass
        self.rec_lbl.setText(self._record_text())
        try:
            self.net.push_profile(force=True)                  # рекорды — в профиль
        except Exception:                                      # noqa: BLE001
            pass

    def _record_text(self) -> str:
        s = self.win.settings.get("quiz_live") or {}
        return f"Ваши рекорды: побед {s.get('wins', 0)} из {s.get('games', 0)} игр · лучший счёт {s.get('best', 0)}"

    def _log(self, text):
        lines = (self.__dict__.get("_lines") or [])[-3:] + [text]
        self._lines = lines
        self.log_lbl.setText("\n".join(lines))

    def _tick(self):
        if self.state != "round":
            return
        now = time.monotonic()
        self.stage.left = max(0.0, self.secs - (now - self.t_round))
        if self.role != "host":
            return
        if self.waiting and now - self.t_sent > 10:            # кто-то молчит — не ждать его вечно
            self.waiting.clear()
        if not self.waiting and not self.t_clock:
            self.t_clock = now
        if self.t_clock and now - self.t_clock >= self.secs + 1.0:   # +1 с — ответы ещё в пути
            self._end_round()

    def _board(self):
        while self.board.count():
            it = self.board.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        rows = []
        if self.state == "lobby" and self.role == "host":
            rows.append((self.my_name + " (вы)", "ведущий", True))
            for o, st in self.invited.items():
                rows.append((self.net._contact_label(o), st, st == "в игре"))
        else:
            for o, s in sorted(self.scores.items() or [(k, 0) for k in self.players], key=lambda kv: -kv[1]):
                mark = "  ★" if o in self.solved and self.state == "round" else ""
                rows.append((self.players.get(o, "?") + (" (вы)" if o == self.me else "") + mark, str(s), True))
        for name, val, on in rows:
            f = QFrame()
            f.setObjectName("QRow")
            f.setStyleSheet("QFrame#QRow{background:rgba(255,255,255,%s);border-radius:12px;}" % ("0.07" if on else "0.03"))
            h = QHBoxLayout(f)
            h.setContentsMargins(12, 8, 12, 8)
            a = QLabel(name)
            a.setStyleSheet("font-weight:800;" if on else "color:#888;")
            b = QLabel(val)
            b.setStyleSheet("font-weight:900;color:#ffdb1a;" if val.isdigit() else "color:#8a8a8a;font-size:11px;")
            h.addWidget(a, 1)
            h.addWidget(b)
            self.board.addWidget(f)

    def closeEvent(self, e):
        self._stop_audio()
        if self.state != "end":
            if self.role == "host":
                for o in list(self.invited):
                    self.send(o, {"t": "quiz_leave"})
            elif self.host:
                self.send(self.host, {"t": "quiz_leave"})
        self.state = "end"
        self.tick.stop()
        games = self.net.__dict__.get("_quiz_games") or {}
        games.pop(self.gid, None)
        super().closeEvent(e)


class InviteDialog(QDialog):
    """Кого позвать: друзья в сети (галочки)."""

    def __init__(self, net, preselect=()):
        super().__init__(net.win)
        import net_frames
        self.setWindowTitle("Угадай трек вместе")
        self.setStyleSheet(net_frames._QSS)
        self.resize(420, 480)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        t = QLabel("Кого позвать?")
        t.setStyleSheet("font-size:20px;font-weight:900;")
        v.addWidget(t)
        s = QLabel("Треки возьмутся из ваших коллекций вперемешку. Играть можно с теми, кто сейчас в сети.")
        s.setObjectName("Sub")
        s.setWordWrap(True)
        v.addWidget(s)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        lv = QVBoxLayout(inner)
        self.checks = {}
        friends = net.node.store.contacts("friend")
        online = [c for c in friends if net._online(c["onion"])]
        for c in online:
            cb = QCheckBox(c.get("name") or c["onion"][:8])
            cb.setChecked(c["onion"] in preselect or len(online) == 1)
            self.checks[c["onion"]] = cb
            lv.addWidget(cb)
        if not online:
            e = QLabel("Сейчас никого из друзей нет в сети.")
            e.setObjectName("Sub")
            lv.addWidget(e)
        lv.addStretch(1)
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        bot = QHBoxLayout()
        bot.addStretch(1)
        c = QPushButton("Отмена")
        c.clicked.connect(self.reject)
        ok = QPushButton("Позвать")
        ok.setObjectName("Accent")
        ok.setEnabled(bool(online))
        ok.clicked.connect(self.accept)
        bot.addWidget(c)
        bot.addWidget(ok)
        v.addLayout(bot)

    def chosen(self) -> list:
        return [o for o, cb in self.checks.items() if cb.isChecked()]


def start(net, preselect=()):
    """Ведущий: выбрать друзей → окно игры, приглашения ушли."""
    if net.node is None:
        return None
    if preselect:
        chosen = [o for o in preselect if net._online(o)]
    else:
        d = InviteDialog(net, preselect)
        if not d.exec():
            return None
        chosen = d.chosen()
    if not chosen:
        return None
    gid = secrets.token_hex(6)
    g = QuizLive(net, "host", gid)
    net.__dict__.setdefault("_quiz_games", {})[gid] = g
    g.show()
    g.invite(chosen)
    return g


def on_message(net, o, m):
    """Входящее quiz_* из сети."""
    games = net.__dict__.setdefault("_quiz_games", {})
    gid = m.get("gid") or ""
    g = games.get(gid)
    if g is not None:
        try:
            g.on_message(o, m)
        except RuntimeError:
            games.pop(gid, None)
        return
    if m.get("t") == "quiz_inv":                               # позвали — окно с «Играть / Не сейчас»
        name = m.get("name") or net._contact_label(o)
        g = QuizLive(net, "guest", gid, o, name, int(m.get("total") or 10), int(m.get("secs") or 30))
        games[gid] = g
        g.show()
        g.raise_()
        try:
            net.alert("room", f"{name} зовёт угадывать треки", "Угадай трек вместе — из ваших коллекций",
                      onion=o, force=True)
        except Exception:                                      # noqa: BLE001
            pass
