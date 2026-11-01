# net_collab.py
"""
Совместные плейлисты: правят несколько друзей.

Плейлист → «⋯» → «Сделать совместным…» → выбрать друзей. Они получают приглашение; у каждого участника своя
копия, и любое изменение (добавить, убрать, переставить) уходит остальным. Копии сливаются по трекам: у каждого
трека в плейлисте есть свой id, место и время последней правки — побеждает свежая правка, удаление — тоже правка
(поэтому удалённый у одного не «воскресает» у другого). Кто был не в сети — получит состояние позже
(состояние напоминается раз в полторы минуты, пока плейлист кто-то правит или открыт).

Сеть: spl_inv / spl_join / spl_state / spl_leave (только между друзьями). Треки — только описания (без путей):
у себя каждый слушает свой файл, если трек есть в коллекции, иначе поток.
"""
from __future__ import annotations

import secrets
import time

from PyQt6.QtCore import QObject, QTimer

MAX_ENTRIES = 400
_TIMER = None


def _key(meta) -> tuple:
    from net_page import tkey
    return tkey(meta)


def _meta(t) -> dict:
    from net_page import track_meta
    return track_meta(t) or {}


def shared_playlists(win) -> dict:
    return {n: p for n, p in win.playlists.items() if isinstance(p, dict) and isinstance(p.get("shared"), dict)}


def by_pid(win, pid):
    for n, p in shared_playlists(win).items():
        if p["shared"].get("pid") == pid:
            return n, p
    return None, None


def state(win, name) -> dict:
    sh = win.playlists[name]["shared"]
    return {"t": "spl_state", "pid": sh["pid"], "name": name, "owner": sh.get("owner", ""),
            "members": list(sh.get("members") or []), "entries": list(sh.get("entries") or [])[:MAX_ENTRIES]}


def _rebuild(win, name):
    """Треки плейлиста — из записей (живые, по месту): свой файл или поток."""
    import net_music
    import stream_core
    pl = win.playlists[name]
    ents = sorted((e for e in pl["shared"].get("entries") or [] if not e.get("del")), key=lambda e: e.get("pos", 0))
    tracks = []
    for e in ents:
        m = e.get("meta") or {}
        _i, local = net_music.library_match(win, m)
        t = dict(local) if local is not None else stream_core.make_track(m)
        t["_spl"] = e["id"]
        tracks.append(t)
    pl["tracks"] = tracks


def _save(win, name=None):
    try:
        win._save_playlists()
        win._refresh_playlists()
    except Exception:                                          # noqa: BLE001
        pass
    sh = getattr(win, "_ya", None)
    if sh is not None:
        try:
            sh.refresh_sidebar()
            if name and getattr(sh, "_cur_page", "") == "playlist" and getattr(sh, "_pl_name", "") == name:
                sh.open_playlist(name, keep_history=True)
        except Exception:                                      # noqa: BLE001
            pass


def local_changed(win, name):
    """Плейлист поменяли у себя → записи (добавить / убрать / место) → разослать участникам."""
    pl = win.playlists.get(name)
    if not pl or not isinstance(pl.get("shared"), dict):
        return
    sh = pl["shared"]
    ents = {e["id"]: e for e in sh.get("entries") or []}
    by_key = {}
    for e in ents.values():
        if not e.get("del"):
            by_key.setdefault(_key(e.get("meta")), e)
    now = time.time()
    me = _me(win)
    seen = set()
    for i, t in enumerate(pl.get("tracks") or []):
        e = ents.get(t.get("_spl")) or by_key.get(_key(t))
        if e is None or e.get("del"):
            e = {"id": secrets.token_hex(6), "meta": _meta(t), "by": me, "pos": float(i), "ts": now, "del": 0}
            ents[e["id"]] = e
        elif e.get("pos") != float(i):
            e["pos"], e["ts"] = float(i), now
        t["_spl"] = e["id"]
        seen.add(e["id"])
    for eid, e in ents.items():
        if eid not in seen and not e.get("del"):
            e["del"], e["ts"] = now, now                       # удалён у нас
    sh["entries"] = sorted(ents.values(), key=lambda e: e.get("pos", 0))[-MAX_ENTRIES:]
    sh["dirty"] = now
    broadcast(win, name)


def merge(win, name, entries) -> bool:
    """Слить чужие записи: у каждой записи побеждает более свежая правка."""
    sh = win.playlists[name]["shared"]
    ents = {e["id"]: e for e in sh.get("entries") or []}
    changed = False
    for e in entries or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        cur = ents.get(e["id"])
        if cur is None or float(e.get("ts", 0)) > float(cur.get("ts", 0)):
            ents[e["id"]] = dict(e)
            changed = True
    if changed:
        sh["entries"] = sorted(ents.values(), key=lambda e: e.get("pos", 0))[-MAX_ENTRIES:]
        _rebuild(win, name)
    return changed


def _me(win) -> str:
    net = _net(win)
    return net.node.me.onion if net is not None and net.node is not None else ""


def _net(win):
    sh = getattr(win, "_ya", None)
    return sh.pages.get("net") if sh is not None else None


def broadcast(win, name, only=None):
    net = _net(win)
    if net is None or net.node is None or not net.running():
        return
    st = state(win, name)
    me = _me(win)
    for o in (only or st["members"] + [st["owner"]]):
        if o and o != me:
            net.node.call(net.node.quiz_send, o, st)


# ── приглашения ── #

def make_shared(shell, name):
    """Плейлист → совместный: выбрать друзей и позвать."""
    win = shell.win
    net = shell.pages.get("net")
    if net is None or net.node is None:
        return
    from net_quiz_live import InviteDialog
    d = InviteDialog(net)
    d.setWindowTitle("Совместный плейлист")
    if not d.exec():
        return
    who = d.chosen()
    if not who:
        return
    pl = win.playlists[name]
    me = net.node.me.onion
    if not isinstance(pl.get("shared"), dict):
        pl["shared"] = {"pid": secrets.token_hex(8), "owner": me, "members": [], "entries": []}
        local_changed(win, name)                               # записи из текущих треков
    sh = pl["shared"]
    for o in who:
        if o not in sh["members"]:
            sh["members"].append(o)
        st = dict(state(win, name), t="spl_inv", inviter=net.node._name())
        net.node.call(net.node.quiz_send, o, st)
    _save(win, name)
    net.toast(f"Приглашения отправлены: {len(who)}")


def leave(shell, name):
    win = shell.win
    pl = win.playlists.get(name)
    if not pl or not isinstance(pl.get("shared"), dict):
        return
    net = shell.pages.get("net")
    sh = pl.pop("shared")
    if net is not None and net.node is not None and net.running():
        for o in sh.get("members", []) + [sh.get("owner", "")]:
            if o and o != net.node.me.onion:
                net.node.call(net.node.quiz_send, o, {"t": "spl_leave", "pid": sh["pid"]})
    for t in pl.get("tracks") or []:
        t.pop("_spl", None)
    _save(win, name)


def on_message(net, o, m):
    win = net.win
    t = m.get("t")
    pid = m.get("pid") or ""
    name, pl = by_pid(win, pid)
    if t == "spl_inv" and pl is None:
        _offer(net, o, m)
    elif t == "spl_join" and pl is not None:
        sh = pl["shared"]
        if o not in sh["members"]:
            sh["members"].append(o)
        _save(win, name)
        broadcast(win, name)                                   # всем — новый состав
        net.toast(f"{net._contact_label(o)} теперь в совместном плейлисте «{name}»")
    elif t == "spl_state" and pl is not None:
        sh = pl["shared"]
        members = set(sh.get("members") or []) | set(m.get("members") or [])
        me = net.node.me.onion
        sh["members"] = [x for x in members if x != me and x != sh.get("owner")]
        if merge(win, name, m.get("entries")):
            _save(win, name)
            if sh.get("owner") == me:                          # владелец — узел связи: участники могут не
                broadcast(win, name, only=[x for x in sh["members"] if x != o])   # дружить между собой
    elif t == "spl_leave" and pl is not None:
        sh = pl["shared"]
        if o in sh.get("members", []):
            sh["members"].remove(o)
            _save(win, name)
            net.toast(f"{net._contact_label(o)} вышел из совместного плейлиста «{name}»")


def _offer(net, o, m):
    from PyQt6.QtWidgets import QMessageBox
    who = m.get("inviter") or net._contact_label(o)
    title = m.get("name") or "Плейлист"
    box = QMessageBox(net.win)
    box.setWindowTitle("Совместный плейлист")
    box.setText(f"{who} зовёт в совместный плейлист «{title}» — правьте вместе.")
    yes = box.addButton("Принять", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Нет", QMessageBox.ButtonRole.RejectRole)
    net._spl_box = box

    def done(_btn=None):
        if box.clickedButton() is yes:
            accept(net, o, m)
    box.buttonClicked.connect(done)
    box.open()


def accept(net, o, m):
    win = net.win
    name = m.get("name") or "Совместный плейлист"
    base, k = name, 2
    while name in win.playlists:
        name = f"{base} ({k})"
        k += 1
    me = net.node.me.onion
    win.playlists[name] = {"tracks": [], "desc": "", "cover": "",
                           "shared": {"pid": m.get("pid"), "owner": m.get("owner") or o,
                                      "members": [x for x in (m.get("members") or []) if x != me], "entries": []}}
    merge(win, name, m.get("entries"))
    _rebuild(win, name)
    _save(win, name)
    net.node.call(net.node.quiz_send, o, {"t": "spl_join", "pid": m.get("pid")})
    net.toast(f"Совместный плейлист «{name}» — в ваших плейлистах")


def label(pl) -> str:
    sh = pl.get("shared") if isinstance(pl, dict) else None
    if not isinstance(sh, dict):
        return ""
    n = len(set(sh.get("members") or []) | {sh.get("owner")})
    return f"Совместный плейлист  ·  {n} участн."


class _Pulse(QObject):
    """Раз в полторы минуты — напомнить состояние участникам (кто был не в сети, догонит)."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.t = QTimer(self)
        self.t.setInterval(90000)
        self.t.timeout.connect(self.tick)
        self.t.start()

    def tick(self):
        for name in list(shared_playlists(self.win)):
            broadcast(self.win, name)


def install(win):
    global _TIMER
    if _TIMER is None:
        _TIMER = _Pulse(win)
