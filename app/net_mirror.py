"""Зеркала Blossom: файлы обновлений (и модов) лежат на публичных серверах, пока автор не в сети.

Blossom — хранилище файлов для Nostr (BUD-01/02/06): файл адресуется своим SHA-256 (GET <сервер>/<sha256>),
загружает его автор, подписав разрешение своим ключом Nostr (kind 24242). Ключей API нет. Подменить файл
нельзя: скачанное сверяется с SHA-256 из подписанного манифеста. Одинаковые файлы не грузятся второй раз —
при новой версии уходят только изменившиеся.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

SERVERS = ["https://blossom.primal.net", "https://nostr.download", "https://blossom.yakihonne.com"]
UA = {"User-Agent": "EchoesPlayer/1.0"}
_TLS = threading.local()


def _session():
    s = getattr(_TLS, "s", None)
    if s is None:
        import requests
        s = _TLS.s = requests.Session()
        s.headers.update(UA)
    return s


def _auth(sk: int, verb: str, sha: str) -> str:
    from net_nostr import make_event
    ev = make_event(sk, 24242, f"ECHOES {verb}", [["t", verb], ["x", sha],
                                                  ["expiration", str(int(time.time()) + 3600)]], 0)
    return "Nostr " + base64.b64encode(json.dumps(ev, separators=(",", ":")).encode()).decode()


def has(server: str, sha: str) -> bool:
    try:
        r = _session().head(f"{server}/{sha}", timeout=12, allow_redirects=True)
        return r.status_code == 200
    except Exception:                                          # noqa: BLE001
        return False


def upload(sk: int, server: str, path, sha: str, size: int) -> bool:
    """Один файл на один сервер: уже есть → True; спросить разрешения (BUD-06), потом PUT /upload."""
    if has(server, sha):
        return True
    s = _session()
    hdr = {"Authorization": _auth(sk, "upload", sha), "X-SHA-256": sha, "X-Content-Length": str(size),
           "X-Content-Type": "application/octet-stream"}
    try:
        r = s.head(f"{server}/upload", headers=hdr, timeout=15)
        if r.status_code not in (200, 204):
            return False
        with open(path, "rb") as f:
            r = s.put(f"{server}/upload", data=f, timeout=600,
                      headers={"Authorization": hdr["Authorization"], "X-SHA-256": sha,
                               "Content-Type": "application/octet-stream", "Content-Length": str(size)})
        if r.status_code not in (200, 201):
            return False
        try:
            got = str((r.json() or {}).get("sha256") or sha)
        except ValueError:
            got = sha
        return got == sha
    except Exception:                                          # noqa: BLE001
        return False


def mirror_files(sk: int, files, progress=None, stop=None) -> dict:
    """files: [(path, sha, size)] → {sha: [серверы, где файл есть]}. Каждый файл — на все серверы, что примут."""
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    done = [0]
    total = len(files)

    def one(item):
        path, sha, size = item
        ok = [srv for srv in SERVERS if not (stop and stop()) and upload(sk, srv, path, sha, size)]
        out[sha] = ok
        done[0] += 1
        if progress:
            progress(done[0], total)

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, files))
    return out


def fetch(sha: str, size: int, dest, servers=None, on_bytes=None, stop=None) -> bool:
    """Скачать файл по SHA-256 с первого сервера, где он есть; True — скачан и совпал с хэшем."""
    dest = Path(dest)
    for srv in list(servers or SERVERS):
        if stop and stop():
            return False
        got = 0
        try:
            r = _session().get(f"{srv}/{sha}", stream=True, timeout=30)
            if r.status_code != 200:
                continue
            h = hashlib.sha256()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for b in r.iter_content(1 << 16):
                    if stop and stop():
                        return False
                    f.write(b)
                    h.update(b)
                    got += len(b)
                    if on_bytes:
                        on_bytes(len(b))
                    if got > size:
                        break
            if got == size and h.hexdigest() == sha:
                return True
            if on_bytes:
                on_bytes(-got)                                 # не тот файл — прогресс назад, пробуем дальше
        except Exception:                                      # noqa: BLE001
            if on_bytes and got:
                on_bytes(-got)
            continue
    return False
