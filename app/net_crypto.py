# net_crypto.py
"""
Echoes.NET: шифрование «конвертов» для доставки через общего друга.

Ключ личности — ed25519 (он же onion-адрес). Из него без новых ключей получается X25519:
  • открытый: точка Эдвардса y → координата Монтгомери u = (1 + y) / (1 − y) mod p;
  • закрытый: первые 32 байта SHA-512(seed) с «зажатыми» битами — тот же скаляр, что и в ed25519.
Общий секрет X25519(мой скаляр, его u) знают только двое — тот, кто несёт конверт,
прочитать его не может; подделать тоже: AES-GCM проверяет целостность, а ключ выводится
из пары отправитель↔получатель, так что «от кого» подтверждается самим расшифрованием.
Чистый Python (лестница Монтгомери) — без новых библиотек; AES — из pycryptodomex.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

P = 2 ** 255 - 19
A24 = 121665


def _x25519(k: bytes, u: int) -> bytes:
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    s = int.from_bytes(k, "little")
    x1, x2, z2, x3, z3, swap = u, 1, 0, u, 1, 0
    for t in reversed(range(255)):
        b = (s >> t) & 1
        swap ^= b
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = b
        a, aa = (x2 + z2) % P, None
        aa = a * a % P
        bb_ = (x2 - z2) % P
        bb = bb_ * bb_ % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * bb_ % P
        x3 = (da + cb) ** 2 % P
        z3 = x1 * (da - cb) ** 2 % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


def ed_pub_to_u(pub: bytes) -> int:
    y = int.from_bytes(pub, "little") & ((1 << 255) - 1)
    return (1 + y) * pow((1 - y) % P, P - 2, P) % P


def x_scalar(seed: bytes) -> bytes:
    return hashlib.sha512(seed).digest()[:32]


def pair_key(my_seed: bytes, my_onion: str, their_pub: bytes, their_onion: str) -> bytes:
    shared = _x25519(x_scalar(my_seed), ed_pub_to_u(their_pub))
    a, b = sorted((my_onion, their_onion))
    return hashlib.sha256(b"echoes-relay-v1|" + shared + a.encode() + b.encode()).digest()


def seal(key: bytes, obj: dict) -> str:
    from Cryptodome.Cipher import AES
    nonce = os.urandom(12)
    c = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ct, tag = c.encrypt_and_digest(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    return base64.b64encode(nonce + tag + ct).decode()


def open_(key: bytes, blob: str) -> dict | None:
    from Cryptodome.Cipher import AES
    try:
        raw = base64.b64decode(blob)
        c = AES.new(key, AES.MODE_GCM, nonce=raw[:12])
        obj = json.loads(c.decrypt_and_verify(raw[28:], raw[12:28]).decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:                                          # noqa: BLE001
        return None
