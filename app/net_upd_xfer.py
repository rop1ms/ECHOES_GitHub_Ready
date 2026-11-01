"""Передача обновлений ECHOES кусками файлов — без лимита размера, с докачкой, сразу от нескольких раздающих.

Кусок = (номер файла в манифесте, смещение, длина) — сырые байты файла, в пути сжаты zlib. Любой, у кого
установлена та же сборка, отдаёт одинаковые байты, поэтому куски можно брать у разных людей; каждый файл
целиком проверяется по SHA-256 из подписанного манифеста.

Транспорт — лучший доступный до раздающего:
  udp    — прямой канал через пробитый NAT (net_udp, бинарные пакеты T_DATA), куски по 32 КБ, окно 24;
  tcp    — Tor / напрямую / LAN (обычное сообщение протокола), куски по 256 КБ, окно 6;
  nostr  — через релеи: медленно (лимиты частоты), куски по 10 КБ, окно 2 — только если больше никак.
Докачка: уже проверенные файлы в папке ожидания не качаются заново (и после перезапуска плеера).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import struct
import time
import zlib
from pathlib import Path

PIECE = {"udp": 32 * 1024, "tcp": 256 * 1024, "nostr": 10 * 1024}
WINDOW = {"udp": 40, "tcp": 6, "nostr": 2}
TIMEOUT = {"udp": 5.0, "tcp": 40.0, "nostr": 120.0}
UDP_FRAG = 1290                                                # полезная нагрузка одного UDP-пакета
UDP_RATE = 4 * 2 ** 20                                         # раздача по UDP — не больше 4 МБ/с на всех
UDP_BURST = 48 * 1024                                          # без пауз — не больше стольких байт подряд
MIRROR_AFTER = 6                                               # столько секунд без быстрых раздающих — к зеркалам
MIRROR_WORKERS = 3
NO_SOURCE_GIVEUP = 180                                         # столько секунд нет ни одного раздающего — ошибка


def files_order(man) -> list:
    """Порядок файлов манифеста (номер файла в куске) — одинаковый у всех."""
    return sorted(man["files"])


def piece_key(fi: int, off: int) -> bytes:
    return struct.pack(">H", fi) + int(off).to_bytes(6, "big")                 # 2 байта файл + 6 байт смещение


def parse_key(k: bytes):
    fi = struct.unpack(">H", k[:2])[0]
    off = struct.unpack(">Q", b"\x00\x00" + k[2:8])[0]
    return fi, off


# ══════════════════════════ раздача ══════════════════════════ #

class Server:
    """Отдаёт куски своей установленной сборки (её манифест — node._upd_mine)."""

    def __init__(self, node):
        self.node = node
        self.ok_files: dict = {}                               # (id, fi) → файл совпадает с манифестом
        self._bucket = (time.time(), float(UDP_BURST))
        self.queues: dict = {}                                 # onion → {(fi, off): просьба}
        self.workers: dict = {}

    async def _file_ok(self, uid, man, fi) -> Path | None:
        order = files_order(man)
        if not 0 <= fi < len(order):
            return None
        rel = order[fi]
        path = Path(self.node.app_dir) / rel
        key = (uid, fi)
        if key not in self.ok_files:
            loop = asyncio.get_running_loop()

            def sha():
                h = hashlib.sha256()
                try:
                    with open(path, "rb") as f:
                        for b in iter(lambda: f.read(1 << 20), b""):
                            h.update(b)
                except OSError:
                    return None
                return h.hexdigest()
            self.ok_files[key] = (await loop.run_in_executor(None, sha)) == man["files"][rel][0]
        return path if self.ok_files[key] else None

    async def _pace(self, n):
        t, tokens = self._bucket
        now = time.time()
        tokens = min(float(UDP_BURST), tokens + (now - t) * UDP_RATE) - n
        self._bucket = (now, tokens)
        if tokens < 0:
            await asyncio.sleep(-tokens / UDP_RATE)

    async def handle_req(self, p, m):
        nd = self.node
        uid = str(m.get("id") or "")[:16]
        man = await nd._upd_mine()
        if man is None or uid != nd._upd_mine_id:
            await p.send({"t": "upd_err", "id": uid, "why": "у раздающего уже другая сборка", "soft": 1})
            return
        if not nd.is_friend(p.onion) and not man.get("public"):
            await p.send({"t": "upd_err", "id": uid, "why": "эта сборка только для друзей", "soft": 1})
            return
        want_udp = bool(m.get("u")) and nd.udp_alive(p.onion)
        q = self.queues.setdefault(p.onion, {})
        if not nd.udp_alive(p.onion) and p.via == "nostr" and time.time() - getattr(p, "udp_offer_t", 0) > 30:
            asyncio.ensure_future(nd._udp_offer(p))           # качает через релеи — предложить прямой канал
        for item in (m.get("p") or [])[:64]:
            try:
                fi, off, ln = int(item[0]), int(item[1]), int(item[2])
                miss = [int(x) for x in item[3][:4096]] if len(item) > 3 and isinstance(item[3], list) else None
            except (TypeError, ValueError, IndexError):
                continue
            if off < 0 or not 0 < ln <= PIECE["tcp"]:
                continue
            q.pop((fi, off), None)                             # повторная просьба — заменяет прежнюю, в конец
            q[(fi, off)] = (uid, man, fi, off, ln, miss, want_udp)
        w = self.workers.get(p.onion)
        if w is None or w.done():
            self.workers[p.onion] = asyncio.ensure_future(self._worker(p))

    async def _worker(self, p):
        """Одна очередь на скачивающего: просьбы по порядку, дубли слиты — без лавины повторов."""
        nd = self.node
        q = self.queues.get(p.onion) or {}
        while q and p.alive:
            key = next(iter(q))
            uid, man, fi, off, ln, miss, want_udp = q.pop(key)
            path = await self._file_ok(uid, man, fi)
            if path is None:
                await p.send({"t": "upd_err", "id": uid, "why": "файл у раздающего изменён", "soft": 1})
                q.clear()
                return
            try:
                with open(path, "rb") as f:
                    f.seek(off)
                    raw = f.read(ln)
            except OSError:
                continue
            data = zlib.compress(raw, 6)
            if want_udp and nd.udp_alive(p.onion):
                await self._send_udp(p.onion, piece_key(fi, off), data, miss)
            else:
                await p.send({"t": "upd_pc", "id": uid, "f": fi, "o": off, "d": base64.b64encode(data).decode()})
                if p.via == "nostr":
                    await asyncio.sleep(1.7)                   # у релеев лимит частоты
                else:
                    await asyncio.sleep(0)

    async def _send_udp(self, onion, key, data, only=None):
        udp = self.node.udp
        frags = [data[i:i + UDP_FRAG] for i in range(0, len(data), UDP_FRAG)] or [b""]
        cnt = len(frags)
        idx = [i for i in (only or range(cnt)) if 0 <= i < cnt]
        for n_, i in enumerate(idx):
            fr = frags[i]
            udp.send_data(onion, key + struct.pack(">HH", i, cnt) + fr)
            await self._pace(len(fr) + 60)
            if n_ % 16 == 15:
                await asyncio.sleep(0)


# ══════════════════════════ скачивание ══════════════════════════ #

class Downloader:
    def __init__(self, node, u):
        self.node = node
        self.u = u
        self.man = u["man"]
        self.uid = u["id"]
        self.order = files_order(self.man)
        self.index = {rel: i for i, rel in enumerate(self.order)}
        import net_update
        self.stage = net_update.home_dir(node.upd_home) / "staged" / self.uid
        self.files_d = self.stage / "files"
        self.need = list(u["need"])
        self.total = sum(int(self.man["files"][r][1]) for r in self.need)
        self.done_bytes = 0
        self.pending: list = []                                # [fi, off, ln] — ещё не запрошено
        self.inflight: dict = {}                               # (fi, off) → (src, t, ln, kind)
        self.got: dict = {}                                    # fi → set(off) полученных кусков
        self.left: dict = {}                                   # fi → байт осталось
        self.verified: set = set()
        self.frags: dict = {}                                  # udp: key → {cnt, parts}
        self.bad_src: dict = {}
        self.t_src = time.time()
        self.t_fast = time.time()                              # когда последний раз был быстрый раздающий
        self.mirror_tasks: list = []
        self.mirror_fail: set = set()
        self.task = None
        self.stopped = False

    # ── подготовка: что уже скачано раньше (докачка) ── #

    def prepare(self):
        self.files_d.mkdir(parents=True, exist_ok=True)
        for rel in self.need:
            fi = self.index[rel]
            sha, size = self.man["files"][rel]
            path = self.files_d / rel
            if path.exists() and path.stat().st_size == size and _sha(path) == sha:
                self.verified.add(fi)
                self.done_bytes += size
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                f.truncate(size)
            self.got[fi] = set()
            self.left[fi] = size
            if size == 0:
                continue
            self.pending.append([fi, 0, size])
        random.shuffle(self.pending)                           # разные скачивающие просят разное — равномерно

    # ── источники ── #

    def _kind(self, p) -> str:
        if self.node.udp_alive(p.onion):
            return "udp"
        return "tcp" if p.via != "nostr" else "nostr"

    def _sources(self):
        nd = self.node
        out = []
        for o in list(self.u["from"]):
            p = nd.peers.get(o)
            if p is None or not p.alive or self.bad_src.get(o, 0) > time.time():
                continue
            out.append((p, self._kind(p)))
        rank = {"udp": 0, "tcp": 1, "nostr": 2}
        out.sort(key=lambda x: (rank[x[1]], x[0].onion == self.u["pub"], random.random()))
        has_fast = any(k != "nostr" for _, k in out)
        if has_fast:                                           # есть быстрый путь — релеи не мучаем
            out = [x for x in out if x[1] != "nostr"]
        return out

    async def _connect_more(self):
        """Подключиться к раздающим: Tor/напрямую (и незнакомцам), Nostr + пробивание UDP."""
        nd = self.node
        now = time.time()
        tried = self.u.setdefault("conn_t", {})
        for o in list(self.u["from"])[:12]:
            if now - tried.get(o, 0) < 45:
                continue
            p = nd.peers.get(o)
            if p is not None and p.alive and p.via != "nostr":
                continue
            tried[o] = now
            if o not in nd._dialing:
                asyncio.ensure_future(nd._dial(o))             # Tor / напрямую — работает и для незнакомцев
            npk = (self.u.get("npk") or {}).get(o) or nd._npk_for(o)
            if p is None and npk:
                p = nd._nostr_attach(o, npk)
            if p is not None and not nd.udp_alive(o):
                asyncio.ensure_future(nd._udp_offer(p))

    # ── цикл ── #

    async def run(self):
        nd = self.node
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.prepare)
        self._progress()
        if self._all_verified():
            await self._finish()
            return
        while not self.stopped:
            now = time.time()
            resend = {}
            for key, (src, t, ln, kind) in list(self.inflight.items()):      # потерянные куски — заново
                if kind == "udp":
                    st = self.frags.get(piece_key(*key))
                    if st is not None and now - st["t"] > 0.5 and now - t > 1.5:
                        miss = [j for j in range(st["cnt"]) if j not in st["parts"]]
                        resend.setdefault(src, []).append([key[0], key[1], ln, miss])
                        self.inflight[key] = (src, now, ln, kind)      # только недостающие пакеты
                        continue
                    if st is None and now - t > 3.0:
                        resend.setdefault(src, []).append([key[0], key[1], ln])
                        self.inflight[key] = (src, now, ln, kind)
                        continue
                if now - t > TIMEOUT[kind]:
                    self.inflight.pop(key, None)
                    self.pending.insert(0, [key[0], key[1], ln])
            for src, items in resend.items():
                p = nd.peers.get(src)
                if p is not None and nd.udp_alive(src):
                    for i0 in range(0, len(items), 6):        # мелко — каждая просьба в один UDP-пакет
                        await p.send({"t": "upd_req", "id": self.uid, "p": items[i0:i0 + 6], "u": 1})
                else:
                    for it in items:
                        self.inflight.pop((it[0], it[1]), None)
                        self.pending.insert(0, it[:3])
            srcs = self._sources()
            if not srcs or all(k == "nostr" for _, k in srcs):
                await self._connect_more()
            else:
                self.t_fast = now
            # быстрых раздающих нет (автор и все обновившиеся не в сети) — целые файлы с зеркал Blossom
            self.mirror_tasks = [t for t in self.mirror_tasks if not t.done()]
            if now - self.t_fast > MIRROR_AFTER and len(self.mirror_tasks) < MIRROR_WORKERS and self._mirror_can():
                self.mirror_tasks.append(asyncio.ensure_future(self._mirror()))
            if srcs or self.mirror_tasks:
                self.t_src = now
            elif now - self.t_src > NO_SOURCE_GIVEUP:
                nd._upd_fail("никого с обновлением нет в сети — докачается, когда кто-то появится")
                return
            self._progress()
            for p, kind in srcs:
                busy = sum(1 for v in self.inflight.values() if v[0] == p.onion)
                room = WINDOW[kind] - busy
                if room <= 0 or not self.pending:
                    continue
                batch = []
                while room > 0 and self.pending:
                    fi, off, ln = self.pending.pop(0)
                    size = PIECE[kind]
                    if ln > size:
                        self.pending.insert(0, [fi, off + size, ln - size])
                        ln = size
                    batch.append([fi, off, ln])
                    self.inflight[(fi, off)] = (p.onion, now, ln, kind)
                    room -= 1
                step = 12 if kind == "udp" else 64
                for i0 in range(0, len(batch), step):
                    await p.send({"t": "upd_req", "id": self.uid, "p": batch[i0:i0 + step],
                                  "u": 1 if kind == "udp" else 0})
            if self._all_verified():
                await self._finish()
                return
            await asyncio.sleep(0.15)

    # ── зеркала Blossom ── #

    def _mirror_can(self) -> bool:
        return any(self._mirror_ok(fi) for fi in (self.index[r] for r in self.need))

    def _mirror_ok(self, fi) -> bool:
        # куски, ждущие медленные релеи Nostr, не в счёт: зеркало по HTTPS быстрее
        return fi not in self.verified and fi not in self.mirror_fail and not self.got.get(fi)             and not any(k[0] == fi and v[3] != "nostr" for k, v in self.inflight.items())

    def _mirror_bytes(self, n):
        self.done_bytes += n                                   # из потока загрузки: только счётчик

    async def _mirror(self):
        import net_mirror
        loop = asyncio.get_running_loop()
        while not self.stopped:
            fi = next((self.index[r] for r in self.need if self._mirror_ok(self.index[r])), None)
            if fi is None:
                return
            self.pending = [x for x in self.pending if x[0] != fi]          # файл целиком наш — у людей не просим
            for k in [k for k in self.inflight if k[0] == fi]:
                self.inflight.pop(k, None)
            self.mirror_fail.add(fi)                           # занят (второй поток его не возьмёт)
            rel = self.order[fi]
            sha, size = self.man["files"][rel]
            ok = await loop.run_in_executor(None, net_mirror.fetch, sha, int(size), self.files_d / rel, None,
                                            self._mirror_bytes, lambda: self.stopped)
            if ok:
                self.mirror_fail.discard(fi)
                self.verified.add(fi)
                self.left[fi] = 0
            else:                                              # на зеркалах нет — вернуть файл раздающим
                self.node.log(f"[net] обновление: {rel} нет на зеркалах")
                with open(self.files_d / rel, "ab") as f:
                    f.truncate(int(size))
                self.pending.append([fi, 0, int(size)])
                self.left[fi] = int(size)

    def _all_verified(self) -> bool:
        return all(self.index[r] in self.verified for r in self.need)

    # ── приём кусков ── #

    def on_udp(self, src, data: bytes):
        if len(data) < 12:
            return
        key, (i, cnt) = data[:8], struct.unpack(">HH", data[8:12])
        if not 0 < cnt <= 4096 or i >= cnt:
            return
        st = self.frags.setdefault(key, {"cnt": cnt, "parts": {}, "t": time.time()})
        st["parts"][i] = data[12:]
        st["t"] = time.time()
        if len(st["parts"]) == st["cnt"]:
            self.frags.pop(key, None)
            fi, off = parse_key(key)
            blob = b"".join(st["parts"][j] for j in range(st["cnt"]))
            asyncio.ensure_future(self.on_piece(src, fi, off, blob))
        if len(self.frags) > 400:                              # недособранные старые — выбросить
            for k in sorted(self.frags, key=lambda k: self.frags[k]["t"])[:100]:
                self.frags.pop(k, None)

    async def on_piece(self, src, fi, off, data: bytes):
        key = (fi, off)
        info = self.inflight.pop(key, None)
        if info is None or fi in self.verified:
            return
        try:
            raw = zlib.decompress(data)
        except zlib.error:
            self.pending.insert(0, [fi, off, info[2]])
            return
        if len(raw) != info[2]:
            self.pending.insert(0, [fi, off, info[2]])
            return
        rel = self.order[fi]
        path = self.files_d / rel
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write_at, path, off, raw)
        self.got[fi].add(off)
        self.left[fi] -= len(raw)
        self.done_bytes += len(raw)
        self._progress()
        if self.left[fi] <= 0:
            sha = await loop.run_in_executor(None, _sha, path)
            if sha == self.man["files"][rel][0]:
                self.verified.add(fi)
            else:                                              # испорчен — весь файл заново, у других
                self.node.log(f"[net] обновление: {rel} не совпал с подписью — качаю заново")
                self.bad_src[src] = time.time() + 300
                size = int(self.man["files"][rel][1])
                self.done_bytes -= size
                self.left[fi] = size
                self.got[fi] = set()
                self.pending.append([fi, 0, size])

    def _progress(self):
        u = self.u
        u["done_bytes"], u["total"] = self.done_bytes, self.total
        now = time.time()
        if now - u.get("t_emit", 0) > 0.3:
            u["t_emit"] = now
            u["t_rx"] = now
            self.node.emit("update", self.node.upd_view())

    async def _finish(self):
        u = self.u
        files = sorted(self.need)
        (self.stage / "manifest.json").write_text(json.dumps(self.man, ensure_ascii=False), encoding="utf-8")
        (self.stage / "files.json").write_text(json.dumps(files), encoding="utf-8")
        u.update(state="ready", stage=str(self.stage), done_bytes=self.total, total=self.total)
        self.node.log(f"[net] обновление {self.uid} скачано и проверено ({self.total} Б)")
        self.node.emit("update", self.node.upd_view())


def _write_at(path, off, raw):
    with open(path, "r+b") as f:
        f.seek(off)
        f.write(raw)


def _sha(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()
