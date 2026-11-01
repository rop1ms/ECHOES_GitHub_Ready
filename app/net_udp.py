# net_udp.py
"""
Echoes.NET: прямой UDP-канал между двумя плеерами через «пробивание» NAT — для голоса и живой комнаты.

Зачем: у многих Tor режут, а релеи Nostr не терпят частых событий (за поток комнаты/голоса банят). Голосу
и синхронизации комнаты нужен быстрый прямой путь. Его можно открыть почти всегда, даже если оба за роутером:
  1. каждый узнаёт свой внешний адрес у бесплатного STUN-сервера (Google / Cloudflare — как WebRTC);
  2. адреса («кандидаты») передаются через уже работающую связь (Nostr / Tor);
  3. оба одновременно шлют друг другу UDP-пакеты — роутеры открывают «дырку», пакеты начинают ходить.
Не выходит только при «симметричном» NAT (часть мобильных операторов) — тогда голос недоступен, а комната
работает медленнее через Nostr.

Каждый пакет зашифрован AES-GCM ключом пары (тем же, что у конвертов через Nostr) — подделать или прочитать
чужой нельзя. Внутри — одно сообщение протокола (JSON), не больше ~1.2 КБ; кадр голоса режется на половинки.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import socket
import struct
import time

MAGIC = b"EU1"
T_PUNCH, T_ACK, T_MSG, T_KA, T_DATA = b"P", b"A", b"M", b"K", b"D"   # D — куски обновлений (net_upd_xfer)
STUN_SERVERS = (("stun.l.google.com", 19302), ("stun1.l.google.com", 19302), ("stun.cloudflare.com", 3478))
ALIVE_FOR = 30.0                    # без пакетов дольше — канал считаем закрытым
KA_EVERY = 5.0                      # «я тут» — чтобы роутер не закрыл дырку (некоторые держат её 20–30 с)
PUNCH_FOR = 15.0
MAX_DGRAM = 1400


def _sid(onion: str) -> bytes:
    return hashlib.sha256(("echoes-udp|" + onion).encode()).digest()[:8]


def _stun_request() -> tuple[bytes, bytes]:
    tid = os.urandom(12)
    return struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid, tid


def _stun_parse(data: bytes, tid: bytes):
    if len(data) < 20 or data[8:20] != tid:
        return None
    i = 20
    while i + 4 <= len(data):
        atype, alen = struct.unpack("!HH", data[i:i + 4])
        val = data[i + 4:i + 4 + alen]
        if atype in (0x0020, 0x0001) and len(val) >= 8 and val[1] == 1:   # (XOR-)MAPPED-ADDRESS, IPv4
            port = struct.unpack("!H", val[2:4])[0]
            ip = bytes(val[4:8])
            if atype == 0x0020:
                port ^= 0x2112
                ip = bytes(b ^ m for b, m in zip(ip, struct.pack("!I", 0x2112A442)))
            return socket.inet_ntoa(ip), port
        i += 4 + alen + ((4 - alen % 4) % 4)
    return None


class UdpLink(asyncio.DatagramProtocol):
    def __init__(self, node):
        self.node = node
        self.transport = None
        self.port = 0
        self.public = None                                     # (ip, port) снаружи — от STUN
        self.peers: dict[str, dict] = {}                       # onion → {key, addr, last, cands, punch_until}
        self.by_sid: dict[bytes, str] = {}
        self._stun_wait: dict[bytes, asyncio.Future] = {}
        self._task = None

    # ── запуск ── #

    async def start(self, port=0):
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=("0.0.0.0", port))
        self.port = self.transport.get_extra_info("sockname")[1]
        sock = self.transport.get_extra_info("socket")
        for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):       # куски обновлений идут потоком — буферы побольше
            try:
                sock.setsockopt(socket.SOL_SOCKET, opt, 4 * 2 ** 20)
            except (OSError, AttributeError):
                pass
        self._task = asyncio.ensure_future(self._loop())
        asyncio.ensure_future(self.stun())
        return self.port

    def close(self):
        if self._task:
            self._task.cancel()
        if self.transport:
            self.transport.close()

    async def stun(self):
        """Свой внешний адрес у STUN-сервера (без него — только локальная сеть)."""
        loop = asyncio.get_running_loop()
        for host, port in STUN_SERVERS:
            try:
                infos = await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
                addr = infos[0][4]
                req, tid = _stun_request()
                fut = loop.create_future()
                self._stun_wait[tid] = fut
                self.transport.sendto(req, addr)
                res = await asyncio.wait_for(fut, 3)
                if res:
                    self.public = res
                    return res
            except Exception:                                  # noqa: BLE001
                continue
            finally:
                self._stun_wait.pop(locals().get("tid", b""), None)
        return None

    def candidates(self) -> list:
        out = []
        if self.public:
            out.append([self.public[0], self.public[1]])
        try:
            from net_direct import lan_ipv4
            for ip in (lan_ipv4() or [])[:3]:
                out.append([ip, self.port])
        except Exception:                                      # noqa: BLE001
            pass
        out.append(["127.0.0.1", self.port])                   # тот же компьютер (тесты, два плеера)
        return out

    # ── собеседники ── #

    def add_peer(self, onion, key: bytes, cands):
        p = self.peers.setdefault(onion, {"key": key, "addr": None, "last": 0.0, "cands": [], "punch_until": 0.0})
        p["key"] = key
        good = []
        for c in (cands or [])[:8]:
            try:
                ip, port = str(c[0]), int(c[1])
                socket.inet_aton(ip)
                if 0 < port < 65536:
                    good.append((ip, port))
            except (OSError, ValueError, TypeError, IndexError):
                continue
        if good:
            p["cands"] = good
        p["punch_until"] = time.time() + PUNCH_FOR
        self.by_sid[_sid(onion)] = onion

    def alive(self, onion) -> bool:
        p = self.peers.get(onion)
        return bool(p and p["addr"] and time.time() - p["last"] < ALIVE_FOR)

    # ── пакеты ── #

    def _pack(self, p, typ: bytes, body: bytes) -> bytes:
        from Cryptodome.Cipher import AES
        head = MAGIC + typ + _sid(self.node.me.onion)
        nonce = os.urandom(12)
        c = AES.new(p["key"], AES.MODE_GCM, nonce=nonce)
        c.update(head)
        ct, tag = c.encrypt_and_digest(body)
        return head + nonce + tag + ct

    def _send(self, onion, typ, body=b"", addr=None):
        p = self.peers.get(onion)
        if p is None or self.transport is None:
            return False
        pkt = self._pack(p, typ, body)
        if len(pkt) > MAX_DGRAM:
            return False
        targets = [addr] if addr else ([p["addr"]] if p["addr"] else p["cands"])
        for a in targets:
            try:
                self.transport.sendto(pkt, a)
            except OSError:
                pass
        return True

    def send_data(self, onion, body: bytes) -> bool:
        """Двоичный пакет (кусок обновления) — без JSON и base64."""
        if not self.alive(onion):
            return False
        return self._send(onion, T_DATA, body)

    def send(self, onion, obj: dict) -> bool:
        """Сообщение протокола по прямому UDP (если канал открыт). Кадр голоса — половинками."""
        if not self.alive(onion):
            return False
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        d = obj.get("d")
        if len(body) > MAX_DGRAM - 80 and obj.get("t") in ("voice", "vc") and isinstance(d, list) and len(d) > 1:
            half = (len(d) + 1) // 2                           # голос: кадры — двумя пакетами
            s0 = int(obj.get("s") or 0)
            return self.send(onion, dict(obj, d=d[:half])) and self.send(onion, dict(obj, d=d[half:], s=s0 + half))
        return self._send(onion, T_MSG, body)

    def datagram_received(self, data, addr):
        if len(data) >= 20 and data[4:8] == b"\x21\x12\xa4\x42":    # ответ STUN
            fut = self._stun_wait.get(data[8:20])
            if fut is not None and not fut.done():
                fut.set_result(_stun_parse(data, data[8:20]))
            return
        if len(data) < 3 + 1 + 8 + 12 + 16 or data[:3] != MAGIC:
            return
        typ, sid = data[3:4], data[4:12]
        onion = self.by_sid.get(sid)
        p = self.peers.get(onion) if onion else None
        if p is None:
            return
        from Cryptodome.Cipher import AES
        try:
            c = AES.new(p["key"], AES.MODE_GCM, nonce=data[12:24])
            c.update(data[:12])
            body = c.decrypt_and_verify(data[40:], data[24:40])
        except (ValueError, KeyError):
            return                                             # не от него / испорчено
        first = not self.alive(onion)
        p["addr"], p["last"] = addr, time.time()
        if typ == T_PUNCH:
            self._send(onion, T_ACK, addr=addr)
        if first:
            self.node._udp_opened(onion, addr)
        if typ == T_DATA:
            fn = getattr(self.node, "_udp_data", None)
            if fn is not None:
                fn(onion, body)
            return
        if typ == T_MSG:
            try:
                obj = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(obj, dict):
                self.node._udp_message(onion, obj)

    def error_received(self, exc):
        pass

    async def _loop(self):
        while True:
            now = time.time()
            for onion, p in list(self.peers.items()):
                alive = self.alive(onion)
                if p.get("was_alive") and not alive:
                    p["was_alive"] = False
                    try:
                        self.node.log(f"[net] udp {onion[:8]}: канал закрылся (нет пакетов {int(now - p['last'])} с)")
                    except Exception:                          # noqa: BLE001
                        pass
                    p["punch_until"] = now + PUNCH_FOR         # сразу пробивать заново по всем адресам
                    p["addr"] = None
                elif alive:
                    p["was_alive"] = True
                if p["punch_until"] > now and not alive:
                    self._send(onion, T_PUNCH)                 # пробиваем, пока не ответит
                elif alive and now - p.get("ka", 0) > KA_EVERY:
                    p["ka"] = now
                    self._send(onion, T_KA)
                    if now - p["last"] > 12 and p["cands"]:    # он молчит — постучать и по другим адресам
                        for a in p["cands"]:
                            if a != p["addr"]:
                                self._send(onion, T_KA, addr=a)
            await asyncio.sleep(0.25 + random.random() * 0.1)
