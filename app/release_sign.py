"""Подписанные выпуски ECHOES: обновление ставится, только если его подписал автор.

Выпуск — файл release/<канал>.json в репозитории (каналы: stable — все, beta — друзья-тестеры):
    {"version", "commit" (точный коммит на GitHub), "channel", "notes", "ts",
     "files": {"app/main.py": "<sha256>", ...}  — каждый файл app/ и setup/ из этого коммита,
     "sig": "<base64 Ed25519 подпись всего остального>"}
Обновлятор (github_update) и лаунчер проверяют подпись встроенным открытым ключом, скачивают ровно этот
коммит и сверяют КАЖДЫЙ файл; лишний или изменённый файл — обновление отклоняется. Поэтому ни пуш в main,
ни захват аккаунта GitHub без закрытого ключа автора никому ничего не установят.

Закрытый ключ — только у автора (tools/release.py, вне репозитория).
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

PUBLIC_KEY_HEX = "10e1a953614752e09839737327ce9c60e7833437e2a0c08483857bca58149117"           # tools/release.py --init-key вписывает сюда ключ
CHANNELS = ("stable", "beta")
COVERED = ("app/", "setup/")                               # что сверяется (то, что копирует установщик)


def canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "sig"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def covered(path: str) -> bool:
    p = path.replace("\\", "/")
    return p.startswith(COVERED) and "/__pycache__/" not in p and not p.endswith((".pyc", ".pyo"))


def verify(manifest: dict, public_hex: str = "") -> bool:
    """Подпись автора верна и манифест целый."""
    key = public_hex or PUBLIC_KEY_HEX
    if not isinstance(manifest, dict) or not key or key.startswith("__"):
        return False
    try:
        from Cryptodome.Signature import eddsa
        pub = eddsa.import_public_key(bytes.fromhex(key))
        eddsa.new(pub, "rfc8032").verify(canonical(manifest), base64.b64decode(manifest.get("sig") or ""))
    except (ValueError, TypeError, ImportError):
        return False
    files = manifest.get("files")
    return (isinstance(files, dict) and bool(files) and all(isinstance(k, str) and isinstance(v, str)
                                                            for k, v in files.items())
            and manifest.get("channel") in CHANNELS and isinstance(manifest.get("commit"), str)
            and len(manifest["commit"]) == 40)


def check_tree(root: Path, manifest: dict) -> list:
    """Сверить распакованный выпуск с манифестом. Пусто — всё совпало; иначе — список проблем."""
    root = Path(root)
    want = manifest.get("files") or {}
    bad = []
    seen = set()
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if not covered(rel):
            continue
        seen.add(rel)
        h = want.get(rel)
        if h is None:
            bad.append(f"лишний файл: {rel}")
        elif sha256(f.read_bytes()) != h:
            bad.append(f"изменён: {rel}")
    for rel in want:
        if rel not in seen:
            bad.append(f"нет файла: {rel}")
    return bad


def public_hex(seed: bytes) -> str:
    from Cryptodome.Signature import eddsa
    return eddsa.import_private_key(seed).public_key().export_key(format="raw").hex()


def sign(manifest: dict, seed: bytes) -> dict:
    """seed — 32 байта закрытого ключа Ed25519 (файл автора, вне репозитория)."""
    from Cryptodome.Signature import eddsa
    key = eddsa.import_private_key(seed)
    out = dict(manifest)
    out["sig"] = base64.b64encode(eddsa.new(key, "rfc8032").sign(canonical(out))).decode()
    return out
