# net_nostr.py
"""
Echoes.NET поверх Nostr — бесплатные публичные релеи вместо своего сервера.

Зачем: (1) общая лента и профили всех пользователей ECHOES, публичные комнаты и знакомства
по вкусу — то, что без сервера иначе не найти; (2) запасная «почта» между друзьями, когда
Tor и прямой путь не работают (конверты зашифрованы ключами пары — релей их не прочтёт).

Ключ Nostr (secp256k1) выводится из того же seed, что и личность, — отдельно хранить нечего.
Связь с onion-адресом подтверждается подписью ed25519 в профиле (подделать нельзя).
Подписи BIP340 (Schnorr) — на чистом Python (эталонный алгоритм; ~10 мс на подпись).
От спама — PoW (NIP-13): у публичного поста id начинается с нескольких нулевых бит.

Релеи держат энтузиасты; подключаемся сразу к нескольким, отпавшие переподключаются.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time

# ══════════════════════════════════════════════════════════════════════════ #
#  secp256k1 / BIP340
# ══════════════════════════════════════════════════════════════════════════ #

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _jadd(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    z1z1, z2z2 = z1 * z1 % P, z2 * z2 % P
    u1, u2 = x1 * z2z2 % P, x2 * z1z1 % P
    s1, s2 = y1 * z2 * z2z2 % P, y2 * z1 * z1z1 % P
    if u1 == u2:
        return _jdbl(p1) if s1 == s2 else None
    h = (u2 - u1) % P
    r = (s2 - s1) % P
    h2 = h * h % P
    h3 = h * h2 % P
    u1h2 = u1 * h2 % P
    x3 = (r * r - h3 - 2 * u1h2) % P
    y3 = (r * (u1h2 - x3) - s1 * h3) % P
    return x3, y3, h * z1 * z2 % P


def _jdbl(p1):
    if p1 is None:
        return None
    x, y, z = p1
    if y == 0:
        return None
    ysq = y * y % P
    s = 4 * x * ysq % P
    m = 3 * x * x % P
    x3 = (m * m - 2 * s) % P
    return x3, (m * (s - x3) - 8 * ysq * ysq) % P, 2 * y * z % P


def _mul(k, pt):
    r = None
    q = (pt[0], pt[1], 1)
    while k:
        if k & 1:
            r = _jadd(r, q)
        q = _jdbl(q)
        k >>= 1
    if r is None:
        return None
    zi = pow(r[2], P - 2, P)
    zi2 = zi * zi % P
    return r[0] * zi2 % P, r[1] * zi2 * zi % P


def _add_affine(a, b):
    if a is None:
        return b
    if b is None:
        return a
    r = _jadd((a[0], a[1], 1), (b[0], b[1], 1))
    if r is None:
        return None
    zi = pow(r[2], P - 2, P)
    zi2 = zi * zi % P
    return r[0] * zi2 % P, r[1] * zi2 * zi % P


def _tag(tag, data):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + data).digest()


def _lift_x(x):
    if x >= P:
        return None
    ysq = (pow(x, 3, P) + 7) % P
    y = pow(ysq, (P + 1) // 4, P)
    if y * y % P != ysq:
        return None
    return x, y if y % 2 == 0 else P - y


def seckey_from_seed(seed: bytes) -> int:
    return int.from_bytes(hashlib.sha256(b"echoes-nostr-v1|" + seed).digest(), "big") % (N - 1) + 1


def pubkey(sk: int) -> str:
    return _mul(sk, G)[0].to_bytes(32, "big").hex()


def schnorr_sign(sk: int, msg: bytes) -> str:
    pt = _mul(sk, G)
    d = sk if pt[1] % 2 == 0 else N - sk
    aux = secrets.token_bytes(32)
    t = (d ^ int.from_bytes(_tag("BIP0340/aux", aux), "big")).to_bytes(32, "big")
    px = pt[0].to_bytes(32, "big")
    k0 = int.from_bytes(_tag("BIP0340/nonce", t + px + msg), "big") % N
    rpt = _mul(k0, G)
    k = k0 if rpt[1] % 2 == 0 else N - k0
    rx = rpt[0].to_bytes(32, "big")
    e = int.from_bytes(_tag("BIP0340/challenge", rx + px + msg), "big") % N
    return (rx + ((k + e * d) % N).to_bytes(32, "big")).hex()


def schnorr_verify(pub_hex: str, msg: bytes, sig_hex: str) -> bool:
    try:
        pub, sig = bytes.fromhex(pub_hex), bytes.fromhex(sig_hex)
    except ValueError:
        return False
    if len(pub) != 32 or len(sig) != 64:
        return False
    pt = _lift_x(int.from_bytes(pub, "big"))
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if pt is None or r >= P or s >= N:
        return False
    e = int.from_bytes(_tag("BIP0340/challenge", sig[:32] + pub + msg), "big") % N
    rr = _add_affine(_mul(s, G), _mul(N - e, pt))
    return rr is not None and rr[1] % 2 == 0 and rr[0] == r


# ══════════════════════════════════════════════════════════════════════════ #
#  События
# ══════════════════════════════════════════════════════════════════════════ #

def event_id(ev) -> str:
    ser = json.dumps([0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
                     separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(ser.encode("utf-8")).hexdigest()


def pow_bits(id_hex: str) -> int:
    n = 0
    for ch in id_hex:
        v = int(ch, 16)
        if v == 0:
            n += 4
            continue
        return n + (4 - v.bit_length())
    return n


def make_event(sk: int, kind: int, content: str, tags=None, pow_target=0) -> dict:
    ev = {"pubkey": pubkey(sk), "created_at": int(time.time()), "kind": kind, "tags": list(tags or []),
          "content": content}
    if pow_target:                                             # NIP-13: перебор nonce до нужных нулевых бит
        ev["tags"].append(["nonce", "0", str(pow_target)])
        i = 0
        while True:
            ev["tags"][-1][1] = str(i)
            eid = event_id(ev)
            if pow_bits(eid) >= pow_target:
                break
            i += 1
            if i % 20000 == 0:
                ev["created_at"] = int(time.time())
    ev["id"] = event_id(ev)
    ev["sig"] = schnorr_sign(sk, bytes.fromhex(ev["id"]))
    return ev


def verify_event(ev) -> bool:
    try:
        if not isinstance(ev, dict) or event_id(ev) != ev.get("id"):
            return False
        return schnorr_verify(ev["pubkey"], bytes.fromhex(ev["id"]), ev["sig"])
    except (KeyError, TypeError, ValueError):
        return False


def tag(ev, name):
    for t in ev.get("tags") or []:
        if isinstance(t, list) and len(t) >= 2 and t[0] == name:
            return t[1]
    return None


# ══════════════════════════════════════════════════════════════════════════ #
#  Релеи
# ══════════════════════════════════════════════════════════════════════════ #

DEFAULT_RELAYS = ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.primal.net", "wss://nostr.mom",
                  "wss://relay.snort.social", "wss://nostr.oxtr.dev", "wss://relay.nostr.net",
                  "wss://nostr-pub.wellorder.net"]
RATE_BURST = 8                     # событий подряд на релей
RATE_EVERY = 1.6                   # дальше — не чаще одного раза в столько секунд (лимиты релеев)
BAN_PAUSE = 600                    # релей пожаловался на частоту / забанил — 10 минут ему не пишем


class Relay:
    def __init__(self, url, pool):
        self.url, self.pool = url, pool
        self.ws = None
        self.up = False
        self._task = None
        self.direct = False                                    # Tor к этому релею не пускает — напрямую
        self._tor_fails = 0
        self.tokens = float(RATE_BURST)
        self.tok_t = 0.0
        self.cool_until = 0.0
        self.queue: list = []
        self._drain = None

    def start(self):
        self._task = asyncio.ensure_future(self._run())

    async def _connect(self):
        from websockets.asyncio.client import connect
        sock = None
        if self.pool.socks and not self.direct:                # через Tor: релей не видит наш IP
            sock = await asyncio.get_running_loop().run_in_executor(None, _socks_sock, self.pool.socks, self.url)
        return await connect(self.url, open_timeout=20, max_size=2 ** 22, ping_interval=40, sock=sock,
                             user_agent_header="ECHOES")

    async def _run(self):
        pause = 5
        while not self.pool.closed:
            try:
                self.ws = await self._connect()
                self.up = True
                pause = 5
                self.pool._changed()
                for sid, (filters, _cb) in list(self.pool.subs.items()):
                    await self.ws.send(json.dumps(["REQ", sid] + filters))
                async for raw in self.ws:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(msg, list) and msg and msg[0] == "EVENT" and len(msg) >= 3:
                        self.pool._event(msg[1], msg[2], self.url)
                    elif isinstance(msg, list) and msg and msg[0] == "EOSE" and len(msg) >= 2:
                        self.pool._eose(msg[1], self.url)
                    elif isinstance(msg, list) and msg and msg[0] == "OK" and len(msg) >= 3:
                        self.pool._ok(msg[1], bool(msg[2]), msg[3] if len(msg) > 3 else "", self.url)
            except asyncio.CancelledError:
                break
            except Exception as e:                             # noqa: BLE001
                self.pool.log(f"[nostr] {self.url}: {type(e).__name__} {str(e)[:80]}")
                # многие релеи закрыты для выходов Tor (damus/primal пускают, nos.lol, nostr.mom — нет):
                # после двух неудач через Tor — к этому релею напрямую, иначе почта, заявки и профили не доходят
                if self.pool.socks and not self.direct and not self.up and self.pool.direct_fallback:
                    self._tor_fails += 1
                    if self._tor_fails >= 2:
                        self.direct = True
                        pause = 1
                        self.pool.log(f"[nostr] {self.url}: через Tor не пускает — подключаюсь напрямую")
            self.up = False
            self.ws = None
            self.pool._changed()
            await asyncio.sleep(pause)
            pause = min(300, pause * 2)

    def _take_token(self) -> bool:
        import time as _t
        now = _t.time()
        self.tokens = min(float(RATE_BURST), self.tokens + (now - self.tok_t) / RATE_EVERY) if self.tok_t else self.tokens
        self.tok_t = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    async def publish(self, ev) -> bool:
        """Событие — с учётом лимита релея: сверх него — в очередь (не больше 300), при бане — пропуск."""
        import time as _t
        if _t.time() < self.cool_until or not self.up:
            return False
        if not self.queue and self._take_token():
            return await self.send(["EVENT", ev])
        if len(self.queue) >= 300:
            self.queue.pop(0)
        self.queue.append(ev)
        if self._drain is None or self._drain.done():
            self._drain = asyncio.ensure_future(self._drain_queue())
        return True

    async def _drain_queue(self):
        import time as _t
        while self.queue and not self.pool.closed:
            if _t.time() < self.cool_until or not self.up:
                self.queue.clear()
                return
            if self._take_token():
                await self.send(["EVENT", self.queue.pop(0)])
            else:
                await asyncio.sleep(RATE_EVERY / 2)

    async def send(self, obj):
        if self.ws is not None and self.up:
            try:
                await self.ws.send(json.dumps(obj, ensure_ascii=False))
                return True
            except Exception:                                  # noqa: BLE001
                return False
        return False

    async def close(self):
        if self._task:
            self._task.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:                                  # noqa: BLE001
                pass


def _socks_sock(socks_port, url):
    """Блокирующий SOCKS5-сокет через Tor к хосту релея (для websockets.connect(sock=...))."""
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "wss" else 80)
    s = socket.create_connection(("127.0.0.1", socks_port), timeout=60)
    s.sendall(b"\x05\x01\x00")
    if s.recv(2) != b"\x05\x00":
        raise OSError("SOCKS отказ")
    hb = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + port.to_bytes(2, "big"))
    head = s.recv(4)
    if len(head) < 4 or head[1] != 0:
        raise OSError("SOCKS: релей недоступен")
    n = {1: 4, 4: 16}.get(head[3]) or s.recv(1)[0]
    s.recv(n + 2)
    s.settimeout(None)
    return s


class Pool:
    def __init__(self, urls, on_event, log=print, socks=0, on_change=None, direct_fallback=True):
        self.relays = [Relay(u, self) for u in urls]
        self.direct_fallback = direct_fallback
        self.subs: dict[str, tuple[list, object]] = {}
        self.on_event = on_event
        self.on_change = on_change or (lambda n: None)
        self.log = log
        self.socks = socks
        self.closed = False
        self.seen: dict[str, float] = {}
        self._waiters: dict[str, asyncio.Future] = {}

    def start(self):
        for r in self.relays:
            r.start()

    @property
    def up_count(self) -> int:
        return sum(1 for r in self.relays if r.up)

    def _changed(self):
        self.on_change(self.up_count)

    def _event(self, sid, ev, url):
        if not isinstance(ev, dict) or ev.get("id") in self.seen:
            return
        self.seen[ev.get("id")] = time.time()
        if len(self.seen) > 20000:
            for k in sorted(self.seen, key=self.seen.get)[:5000]:
                self.seen.pop(k, None)
        cb = (self.subs.get(sid) or (None, None))[1]
        if cb:
            cb(ev)
        else:
            self.on_event(sid, ev)

    def _eose(self, sid, url):
        pass

    def _ok(self, eid, ok, why, url):
        if not ok:
            self.log(f"[nostr] {url} отклонил {eid[:8]}: {why}")
            w = str(why or "").lower()
            if "rate" in w or "banned" in w or "slow down" in w or "too many" in w:
                import time as _t
                for r in self.relays:                          # жалуется на частоту — замолчать для него на 10 мин
                    if r.url == url:
                        r.cool_until = _t.time() + BAN_PAUSE
                        r.queue.clear()

    async def publish(self, ev, urgent=False) -> int:
        """urgent — мимо очереди лимита (одиночные важные события: «я вышел из сети»)."""
        n = 0
        for r in self.relays:
            if await (r.send(["EVENT", ev]) if urgent else r.publish(ev)):
                n += 1
        return n

    async def subscribe(self, sid, filters, cb=None):
        self.subs[sid] = (filters, cb)
        for r in self.relays:
            await r.send(["REQ", sid] + filters)

    async def unsubscribe(self, sid):
        self.subs.pop(sid, None)
        for r in self.relays:
            await r.send(["CLOSE", sid])

    async def close(self):
        self.closed = True
        for r in self.relays:
            await r.close()
