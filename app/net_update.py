"""Обновление ECHOES через Echoes.NET — без серверов и без пересылки архивов вручную.

Автор сборки нажимает «Разослать обновление»: ECHOES составляет список файлов программы с их SHA-256
(манифест) и подписывает его ключом Echoes.NET (ed25519 — тем же, из которого сделан код друга).
Друг получает манифест, проверяет подпись и что автор у него в «доверенных», сравнивает со своими
файлами и скачивает только изменившиеся. Каждый файл сверяется с подписанным манифестом —
подменить его по дороге нельзя. Установка — отдельным процессом после закрытия плеера
(файлы .dll заняты, пока он работает), с резервной копией заменённых файлов и откатом при ошибке.
Файлы только добавляются и заменяются — ничего не удаляется; ничего вне папки программы не пишется.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

APP_DIR = Path(__file__).resolve().parent
STATE_NAME = "update_state.json"                  # какая сборка установлена (лежит рядом с программой)
DEV_ONION = "b7i2y6inok7zcadrcffkj7bfcushhhznbxs2wn4t4525mutasvlkjoqd"   # автор ECHOES — доверен по умолчанию
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_EXT = {".pyc", ".pyo", ".bak", ".log", ".tmp"}
MAX_FILES = 5000
MAX_TOTAL = 1024 * 2 ** 20                        # вся программа по манифесту — не больше 1 ГБ
MAX_BLOB_FAST = 200 * 2 ** 20                     # одной передачей через Tor / напрямую
MAX_BLOB_NOSTR = 4 * 2 ** 20                      # через релеи Nostr — медленно и с лимитами, только мелкие правки
CHUNK_FAST = 512 * 1024
CHUNK_NOSTR = 14000                               # в base64 + шифровании влезает в одно событие Nostr


def home_dir(home=None) -> Path:
    """Папка обновлений (кэш хэшей, текущий манифест, скачанное). У узла сети — <net>/update."""
    d = Path(home) if home else Path.home() / ".neon_player" / "net" / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _canon(obj) -> bytes:
    return json.dumps({k: v for k, v in obj.items() if k != "sig"}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def man_id(man: dict) -> str:
    return hashlib.sha256(_canon(man)).hexdigest()[:16]


def safe_rel(rel) -> str:
    """Путь внутри папки программы (posix) или '' — никаких «..», дисков и абсолютных путей."""
    if not isinstance(rel, str) or not rel or len(rel) > 240 or "\\" in rel or ":" in rel or "\x00" in rel:
        return ""
    p = PurePosixPath(rel)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        return ""
    if skipped(rel) or p.name.lower() in ("desktop.ini",):
        return ""
    return str(p)


def skipped(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (any(x in SKIP_DIRS for x in parts) or Path(name).suffix.lower() in SKIP_EXT or ".before_" in name
            or name == STATE_NAME)


# ── хэши файлов программы (с кэшем по размеру и времени изменения) ── #

def scan(app_dir: Path = APP_DIR, home=None) -> dict:
    cache_p = home_dir(home) / "hash_cache.json"
    try:
        cache = json.loads(cache_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    out, new_cache = {}, {}
    root = Path(app_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            if skipped(rel):
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            key = f"{root}|{rel}"
            sig = [st.st_size, st.st_mtime_ns]
            c = cache.get(key)
            if c and c[0] == sig:
                h = c[1]
            else:
                h = _sha_file(full)
                if h is None:
                    continue
            new_cache[key] = [sig, h]
            out[rel] = [h, st.st_size]
    try:
        cache_p.write_text(json.dumps(new_cache), encoding="utf-8")
    except OSError:
        pass
    return out


def _sha_file(path: Path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def installed(app_dir: Path = APP_DIR) -> dict:
    try:
        st = json.loads((Path(app_dir) / STATE_NAME).read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def app_version(app_dir: Path = APP_DIR) -> str:
    try:
        return (Path(app_dir) / "version.txt").read_text(encoding="utf-8").strip()[:40]
    except OSError:
        return ""


# ── манифест ── #

def make_manifest(identity, note="", app_dir: Path = APP_DIR, home=None, public=False) -> dict:
    files = scan(app_dir, home)
    man = {"t": "echoes-update", "v": 1, "pub": identity.onion, "ver": app_version(app_dir), "ts": time.time(),
           "note": str(note or "")[:500], "files": files, "public": bool(public)}
    return identity.sign_obj(man)


def check(man, trusted) -> str:
    """'' — манифест годится; иначе причина."""
    from net_core import verify_obj
    if not isinstance(man, dict) or man.get("t") != "echoes-update" or not isinstance(man.get("files"), dict):
        return "не обновление ECHOES"
    if not verify_obj(man, "pub"):
        return "подпись не сходится"
    if man.get("pub") not in set(trusted or ()):
        return "автор не в доверенных"
    files = man["files"]
    if not files or len(files) > MAX_FILES or "main.py" not in files:
        return "странный список файлов"
    total = 0
    for rel, v in files.items():
        if not safe_rel(rel) or not isinstance(v, list) or len(v) != 2 or not isinstance(v[0], str) \
                or len(v[0]) != 64 or not isinstance(v[1], int) or v[1] < 0:
            return f"недопустимый файл: {str(rel)[:60]}"
        total += v[1]
    if total > MAX_TOTAL:
        return "слишком большое"
    return ""


def needed(man: dict, app_dir: Path = APP_DIR, home=None) -> list:
    """Какие файлы у нас отличаются от манифеста (или их нет)."""
    local = scan(app_dir, home)
    return sorted(rel for rel, (h, _s) in man["files"].items() if (local.get(rel) or [None])[0] != h)


def need_size(man: dict, rels) -> int:
    return sum(int(man["files"][r][1]) for r in rels if r in man["files"])


# ── упаковка у отправителя / распаковка у получателя ── #

def pack(man: dict, rels, app_dir: Path = APP_DIR) -> bytes:
    """Zip с запрошенными файлами — одинаковый байт в байт при повторе (докачка кусков)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in sorted(set(rels)):
            rel = safe_rel(rel)
            ent = man["files"].get(rel) if rel else None
            if not ent:
                continue
            path = Path(app_dir) / rel
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if hashlib.sha256(data).hexdigest() != ent[0]:
                continue                                       # файл уже другой — не отдаём то, что не подписано
            zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return buf.getvalue()


def stage(man: dict, blob: bytes, rels, home=None) -> Path:
    """Проверить каждый файл по манифесту и разложить в папку ожидания. Возвращает её путь."""
    sid = man_id(man)
    d = home_dir(home) / "staged" / sid
    files_d = d / "files"
    if d.exists():
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    files_d.mkdir(parents=True, exist_ok=True)
    want = set(rels)
    got = set()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for zi in z.infolist():
            rel = safe_rel(zi.filename)
            ent = man["files"].get(rel) if rel else None
            if not ent or rel not in want or zi.file_size > MAX_BLOB_FAST:
                raise ValueError(f"лишний файл в обновлении: {zi.filename[:60]}")
            data = z.read(zi)
            if hashlib.sha256(data).hexdigest() != ent[0]:
                raise ValueError(f"файл не совпадает с подписью: {rel}")
            out = files_d / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            got.add(rel)
    if got != want:
        raise ValueError(f"не хватает файлов: {len(want - got)}")
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (d / "files.json").write_text(json.dumps(sorted(got)), encoding="utf-8")
    return d


# ── установка: отдельный процесс ждёт, пока плеер закроется, и заменяет файлы ── #

APPLY_SCRIPT = r'''
import ctypes, json, os, shutil, subprocess, sys, time
from pathlib import Path
pid, stage, app, argv = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4])
log = open(stage / "apply.log", "a", encoding="utf-8")
def say(*a):
    log.write(time.strftime("%H:%M:%S ") + " ".join(map(str, a)) + "\n"); log.flush()
try:
    h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
    if h:
        ctypes.windll.kernel32.WaitForSingleObject(h, 60000)
        ctypes.windll.kernel32.CloseHandle(h)
except Exception as e:
    say("wait:", e)
time.sleep(1.0)
files = json.loads((stage / "files.json").read_text(encoding="utf-8"))
backup = stage / "backup"
done = []
ok = True
for rel in files:
    src, dst = stage / "files" / rel, app / rel
    try:
        if dst.exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup / rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        for i in range(40):
            try:
                shutil.copyfile(src, dst)
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            raise PermissionError(f"занят: {rel}")
        done.append(rel)
    except Exception as e:
        say("ОШИБКА", rel, e)
        ok = False
        break
if not ok:
    for rel in done:
        try:
            b = backup / rel
            if b.exists():
                shutil.copyfile(b, app / rel)
            else:
                (app / rel).unlink()
        except Exception as e:
            say("откат", rel, e)
    say("откат выполнен")
else:
    man = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    st = {"id": sys.argv[5], "ts": man.get("ts"), "ver": man.get("ver"), "pub": man.get("pub"),
          "note": man.get("note", ""), "at": time.time()}
    (app / "update_state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(stage / "manifest.json", Path(sys.argv[6]) / "current.json")
    say("установлено файлов:", len(done))
(stage / ("applied.ok" if ok else "applied.fail")).write_text("1")
try:
    subprocess.Popen(argv, cwd=str(app), creationflags=0x00000008 | 0x00000200, close_fds=True)
except Exception as e:
    say("перезапуск:", e)
'''


def launch_apply(stage_dir: Path, app_dir: Path = APP_DIR, argv=None, home=None) -> bool:
    """Запустить установщик (он дождётся закрытия этого процесса). После вызова плеер надо закрыть."""
    stage_dir, app_dir = Path(stage_dir).resolve(), Path(app_dir).resolve()
    home = Path(home_dir(home)).resolve()
    man = json.loads((stage_dir / "manifest.json").read_text(encoding="utf-8"))
    script = stage_dir / "apply_update.py"
    script.write_text(APPLY_SCRIPT, encoding="utf-8")
    argv = argv or [sys.executable] + sys.argv
    exe = sys.executable
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0          # DETACHED | NEW_PROCESS_GROUP
    subprocess.Popen([exe, "-I", str(script), str(os.getpid()), str(stage_dir), str(app_dir), json.dumps(argv),
                      man_id(man), str(home_dir(home))], cwd=str(stage_dir), creationflags=flags, close_fds=True)
    return True


def current_manifest(home=None) -> dict | None:
    """Манифест установленной сборки (свой опубликованный или полученный) — им можно делиться дальше."""
    try:
        m = json.loads((home_dir(home) / "current.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else None
    except (OSError, ValueError):
        return None


def save_current(man: dict, home=None):
    (home_dir(home) / "current.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
