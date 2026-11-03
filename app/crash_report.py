"""Автоотчёты о вылетах (по согласию): чтобы автор узнавал о проблемах у друзей без ручных баг-репортов.

При запуске ECHOES смотрит в crash.log только то, что появилось с прошлого раза: вылеты (Fatal Python error /
Windows fatal exception) и необработанные ошибки (те самые «кнопка не сработала»). Если есть новое —
маленькая карточка в углу: «ECHOES вылетел — отправить отчёт автору?» [Отправить] [Не сейчас]
[Всегда отправлять]. Отчёт уходит обычным приватным баг-репортом Echoes.NET (net_bugs) со сводкой
diagnostics; одинаковые ошибки склеиваются по «подписи» (где упало + тип ошибки) и второй раз не шлются.

Личное вычищается: пути с именем пользователя, имя пользователя, строки в кавычках, похожие на пути
к файлам (названия треков), — остаётся только код ECHOES.
"""
from __future__ import annotations

import getpass
import hashlib
import os
import re
from pathlib import Path

MAX_SEND = 5
_TB = re.compile(r"Traceback \(most recent call last\):\n(?P<body>(?:[ \t].*\n)+?)(?P<exc>\w[\w.]*(?:Error|Exception|Exit|Interrupt)\b[^\n]*)")
_FATAL = re.compile(r"(?:Fatal Python error|Windows fatal exception)[^\n]*\n(?:.*\n){0,400}?"
                    r"Current thread[^\n]*\n(?:[ \t]+File[^\n]*\n)+")
_FRAME = re.compile(r'File "([^"]+)", line (\d+),? in (\S+)')


def scrub(text: str) -> str:
    # кадры стека — только имя файла кода (yamusic_theme.py), без папок с именем пользователя
    text = re.sub(r'File "([^"]+)"', lambda m: f'File "{Path(m.group(1)).name}"', text)
    home = str(Path.home())
    for h in {home, home.replace("\\", "/"), home.lower()}:
        text = text.replace(h, "~")
    try:
        user = getpass.getuser()
        if len(user) >= 3:
            text = re.sub(re.escape(user), "<user>", text, flags=re.I)
    except Exception:                                      # noqa: BLE001
        pass
    # строки в кавычках с путями/расширениями файлов — это названия треков, папки и т. п.
    text = re.sub(r"(['\"])(?:[A-Za-z]:[\\/]|[\\/]{1,2}|~[\\/])[^'\"\n]{0,300}\1", r"\1<путь>\1", text)
    text = re.sub(r"(['\"])[^'\"\n]{1,200}\.(?:mp3|flac|wav|m4a|ogg|opus|webm|mp4|jpg|png|json)\1", r"\1<файл>\1",
                  text, flags=re.I)
    return text


def _short(path: str) -> str:
    return Path(path).name


def signature(text: str) -> str:
    frames = [(_short(f), fn) for f, _ln, fn in _FRAME.findall(text)]
    exc = text.strip().splitlines()[-1].split(":")[0] if text.strip() else ""
    key = "|".join(f"{a}:{b}" for a, b in frames[-3:]) + "|" + exc
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:12]


def scan(log: Path, offset: int):
    """Новые ошибки в журнале с позиции offset → ([{kind, text, sig, title}], новая позиция)."""
    try:
        size = log.stat().st_size
    except OSError:
        return [], 0
    if offset > size:                                      # журнал обрезали/пересоздали
        offset = 0
    with open(log, "rb") as f:
        f.seek(offset)
        chunk = f.read(2_000_000).decode("utf-8", "replace").replace("\r\n", "\n")   # журнал Windows — CRLF
    found, seen = [], set()
    for m in _FATAL.finditer(chunk):
        body = m.group(0)
        cur = re.search(r"Current thread[^\n]*\n((?:\s+File[^\n]*\n){1,12})", body)
        text = (body.splitlines()[0] + "\n" + (cur.group(1) if cur else body[:1500])).strip()
        frame = _FRAME.search(cur.group(1)) if cur else None
        title = "Вылет ECHOES" + (f" в {_short(frame.group(1))}:{frame.group(3)}" if frame else "")
        sig = signature(text)
        if sig not in seen:
            seen.add(sig)
            found.append({"kind": "fatal", "text": text, "sig": sig, "title": title})
    for m in _TB.finditer(chunk):
        text = m.group(0).strip()
        frames = _FRAME.findall(m.group("body"))
        last = frames[-1] if frames else ("?", "", "?")
        exc = m.group("exc").split(":")[0]
        title = f"Ошибка {exc} в {_short(last[0])}:{last[2]}"
        sig = signature(text)
        if sig not in seen:
            seen.add(sig)
            found.append({"kind": "error", "text": text, "sig": sig, "title": title})
    return found, size


def payload(item: dict) -> dict:
    """Что уходит автору: заголовок, вычищенный стек, подпись."""
    text = scrub(item["text"])[-2900:]
    details = ("Автоотчёт ECHOES (с согласия пользователя).\n\n" + text).strip()
    return {"title": item["title"][:118], "details": details[:3000], "auto": item["sig"]}


# ── отправка и карточка ──

def _node(win):
    try:
        page = win._ya.pages.get("net") if getattr(win, "_ya", None) else None
        return page._ensure_node(start=False) if page is not None else None
    except Exception as e:                                 # noqa: BLE001
        print("[crash] node:", e)
        return None


def send(win, items: list) -> int:
    """Отправить автору (в очередь Echoes.NET — уйдёт, когда будет сеть). → сколько поставлено.
    Echoes.NET ещё ни разу не включали (нет получателя) — отчёты ждут следующего запуска."""
    win.settings["crash_pending"] = [dict(i) for i in items[:MAX_SEND]]
    import net_bugs
    try:
        import diagnostics
        diag = diagnostics.report(win)
    except Exception:                                      # noqa: BLE001
        diag = ""
    node = _node(win)
    if node is None:
        return 0
    if node.my_role() in ("admin", "mod"):                 # автор — сам себе не шлёт, у него свой журнал
        return 0
    peers = net_bugs.recipients(node)
    if not peers:
        return 0
    try:
        ver = (Path(__file__).with_name("version.txt")).read_text("utf-8").strip()
    except OSError:
        ver = ""
    n = 0
    sent = win.settings.setdefault("crash_sent", [])
    win.settings["crash_pending"] = []
    for it in items[:MAX_SEND]:
        p = payload(it)
        try:
            net_bugs.queue(node, peers[0], p["title"], p["details"], "", ver, "bug", diag, auto=p["auto"])
            sent.append(it["sig"])
            n += 1
        except ValueError as e:
            print("[crash] queue:", e)
    win.settings["crash_sent"] = sent[-200:]
    return n


def check_on_start(win):
    """При запуске: новые ошибки с прошлого раза → отправить сами («всегда») или спросить карточкой."""
    import data_home
    log = data_home.root() / "crash.log"
    s = win.settings
    if "crash_log_offset" not in s:                       # первый запуск с автоотчётами — старую историю не трогаем
        try:
            s["crash_log_offset"] = log.stat().st_size
        except OSError:
            s["crash_log_offset"] = 0
        return []
    node = _node(win) if os.environ.get("QT_QPA_PLATFORM") != "offscreen" else None
    try:
        if node is not None and node.my_role() in ("admin", "mod"):
            return []                                     # автору — свой журнал, карточка не нужна
    except Exception:                                      # noqa: BLE001
        pass
    items, end = scan(log, int(s.get("crash_log_offset") or 0))
    s["crash_log_offset"] = end
    done = set(s.get("crash_sent") or []) | set(s.get("crash_declined") or [])
    pend = [i for i in (s.get("crash_pending") or []) if isinstance(i, dict) and i.get("sig")]
    items = [i for i in pend + items if i["sig"] not in done]
    items = list({i["sig"]: i for i in items}.values())
    # «Ошибки» без вылета — только те, что повторялись (одиночный сбой сети и т. п. не дёргаем человека)
    fatal = [i for i in items if i["kind"] == "fatal"]
    errors = [i for i in items if i["kind"] == "error"]
    items = (fatal + errors)[:MAX_SEND]
    if not items or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return items
    if s.get("crash_auto"):
        send(win, items)
        return items
    CrashCard(win, items).show()
    return items


def _decline(win, items):
    d = win.settings.setdefault("crash_declined", [])
    d.extend(i["sig"] for i in items)
    win.settings["crash_declined"] = d[-200:]


from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout  # noqa: E402


class CrashCard(QFrame):
    """Карточка в правом нижнем углу окна — не мешает, сама уходит через минуту."""

    def __init__(self, win, items):
        super().__init__(win)
        self.win, self.items = win, items
        self.setObjectName("CrashCard")
        self.setStyleSheet("QFrame#CrashCard{background:#1b1b20;border:1px solid rgba(255,255,255,0.12);"
                           "border-radius:14px;} QLabel{color:#eee;background:transparent;}"
                           "QPushButton{background:rgba(255,255,255,0.08);color:#fff;border:none;border-radius:9px;"
                           "padding:6px 10px;font-weight:700;} QPushButton:hover{background:rgba(255,255,255,0.16);}"
                           "QPushButton#Go{background:#ffdb1a;color:#000;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        fatal = any(i["kind"] == "fatal" for i in items)
        t = QLabel("ECHOES вылетел в прошлый раз" if fatal else "В прошлый раз в ECHOES были ошибки")
        t.setStyleSheet("font-size:14px;font-weight:800;")
        v.addWidget(t)
        d = QLabel("Отправить автору короткий отчёт? Только техническое: где сломалось и сведения о ПК — "
                   "без имён файлов и личных данных.")
        d.setWordWrap(True)
        d.setStyleSheet("font-size:12px;color:#aaa;")
        v.addWidget(d)
        row = QHBoxLayout()
        go = QPushButton("Отправить")
        go.setObjectName("Go")
        go.clicked.connect(self._send)
        no = QPushButton("Не сейчас")
        no.clicked.connect(self._no)
        always = QPushButton("Всегда отправлять")
        always.clicked.connect(self._always)
        for b in (go, no, always):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        v.addLayout(row)
        self.setFixedWidth(400)
        self.adjustSize()
        self._place()
        win.installEventFilter(self)
        QTimer.singleShot(60000, self._no)

    def _place(self):
        w = self.win
        self.move(max(8, w.width() - self.width() - 20), max(8, w.height() - self.height() - 110))
        self.raise_()

    def eventFilter(self, obj, e):
        from PyQt6.QtCore import QEvent
        if obj is self.win and e.type() == QEvent.Type.Resize:
            self._place()
        return False

    def _close(self):
        try:
            self.win.removeEventFilter(self)
            self.hide()
            self.deleteLater()
            self.win.save_settings()
        except RuntimeError:
            pass

    def _send(self):
        n = send(self.win, self.items)
        try:
            self.win._toast("Отчёт отправлен автору — спасибо!" if n else
                            "Отчёт сохранён и уйдёт, когда включится Echoes.NET")
        except Exception:                                  # noqa: BLE001
            pass
        self._close()

    def _no(self):
        try:
            if self.isVisible():
                _decline(self.win, self.items)
        except RuntimeError:
            return
        self._close()

    def _always(self):
        self.win.settings["crash_auto"] = True
        self._send()
