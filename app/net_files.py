# net_files.py
"""
Echoes.NET: файлы в чатах и постах — тип, превью, хэш.

Превью делает ОТПРАВИТЕЛЬ и кладёт прямо в сообщение (как Telegram): друг сразу видит,
что внутри, ещё до скачивания самого файла через Tor.

  image — миниатюра (до 360 px), размеры, GIF ли;
  video — кадр из середины первых секунд (PyAV, иначе ffmpeg из комплекта), длительность, размеры;
  audio — обложка из тегов, исполнитель/название, длительность, «волна» (48 столбиков);
  pdf   — первая страница (QtPdf) и число страниц;
  text  — первые строки (код, логи, заметки), число строк;
  archive — список файлов внутри (zip), сколько всего;
  прочее — значок с расширением.
Модуль без виджетов: QImage работает и в фоновом потоке, preview() зовут из потока.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import subprocess
import zipfile
from pathlib import Path

MAX_FILE = 200 * 2 ** 20                       # больше через Tor — слишком долго
THUMB_MAX = 40 * 1024                          # миниатюра в сообщении (base64)

IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
AUDIO = {".mp3", ".flac", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".wma"}
TEXT = {".txt", ".md", ".log", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".xml", ".ini", ".cfg",
        ".yaml", ".yml", ".lua", ".c", ".cpp", ".h", ".cs", ".java", ".go", ".rs", ".sh", ".lrc", ".srt", ".echo"}
ARCHIVE = {".zip", ".rar", ".7z", ".tar", ".gz"}
DANGER = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".msi", ".scr", ".com", ".lnk", ".reg",
          ".hta", ".jar", ".dll", ".cpl", ".pif"}

KIND_COLOR = {"image": "#3ddc84", "video": "#ff4fa3", "audio": "#ffdb1a", "pdf": "#ff5c5c", "text": "#5ab0ff",
              "archive": "#c08cff", "file": "#9a9a9a"}


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE:
        return "image"
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT:
        return "text"
    if ext in ARCHIVE:
        return "archive"
    return "file"


def human(n) -> str:
    n = float(n or 0)
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or u == "ГБ":
            return f"{n:.0f} {u}" if u == "Б" else f"{n:.1f} {u}".replace(".0 ", " ")
        n /= 1024
    return f"{n:.1f} ГБ"


def fmt_dur(s) -> str:
    s = int(max(0, float(s or 0)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def safe_name(name: str) -> str:
    base = os.path.basename(str(name or "file")).replace("\x00", "")
    base = "".join(ch for ch in base if ch not in '<>:"/\\|?*' and ord(ch) >= 32).strip(" .")
    return (base or "file")[:120]


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _thumb_b64(img, side=360) -> tuple[str, int, int]:
    """QImage → миниатюра JPEG base64 (умещается в THUMB_MAX), исходные размеры."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    if img is None or img.isNull():
        return "", 0, 0
    w, h = img.width(), img.height()
    sc = img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    for q, s in ((78, side), (65, side), (60, 240), (55, 160)):
        if s != side:
            sc = img.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        sc.convertToFormat(sc.Format.Format_RGB32).save(buf, "JPG", q)
        b64 = base64.b64encode(bytes(ba)).decode()
        if len(b64) <= THUMB_MAX:
            return b64, w, h
    return "", w, h


def _ffmpeg_dir():
    here = Path(__file__).resolve().parent
    for d in (here.parent / "ffmpeg", here / "ffmpeg"):
        if (d / "ffmpeg.exe").exists():
            return d
    return None


def _video(path) -> dict:
    from PyQt6.QtGui import QImage
    out = {}
    try:
        import av
        with av.open(str(path)) as c:
            vs = next((s for s in c.streams if s.type == "video"), None)
            if c.duration:
                out["dur"] = c.duration / 1_000_000
            if vs is not None:
                target = min(2.0, (out.get("dur") or 4) * 0.25)
                try:
                    c.seek(int(target / float(vs.time_base)), stream=vs)
                except Exception:                              # noqa: BLE001
                    pass
                for fr in c.decode(vs):
                    rgb = fr.to_ndarray(format="rgb24")
                    h, w = rgb.shape[:2]
                    img = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
                    out["thumb"], out["w"], out["h"] = _thumb_b64(img)
                    break
        return out
    except Exception:                                          # noqa: BLE001
        pass
    d = _ffmpeg_dir()                                          # запасной путь — ffmpeg из комплекта ECHOES
    if d is None:
        return out
    try:
        tmp = Path(path).with_suffix(".echoes_thumb.jpg")
        subprocess.run([str(d / "ffmpeg.exe"), "-y", "-ss", "1", "-i", str(path), "-frames:v", "1", "-vf",
                        "scale=480:-1", str(tmp)], capture_output=True, timeout=30, creationflags=0x08000000)
        if tmp.exists():
            img = QImage(str(tmp))
            out["thumb"], out["w"], out["h"] = _thumb_b64(img)
            tmp.unlink(missing_ok=True)
        pr = subprocess.run([str(d / "ffprobe.exe"), "-v", "error", "-show_entries", "format=duration", "-of",
                             "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=20,
                            creationflags=0x08000000)
        out["dur"] = float(pr.stdout.strip() or 0)
    except Exception:                                          # noqa: BLE001
        pass
    return out


def _audio(path) -> dict:
    from PyQt6.QtGui import QImage
    out = {}
    try:
        import mutagen
        mf = mutagen.File(str(path))
        if mf is not None:
            if getattr(mf, "info", None) and getattr(mf.info, "length", 0):
                out["dur"] = float(mf.info.length)
            tags = mf.tags or {}

            def tag(*keys):
                for k in keys:
                    try:
                        v = tags.get(k)
                    except Exception:                          # noqa: BLE001
                        v = None
                    if v:
                        v = v[0] if isinstance(v, list) else v
                        return str(getattr(v, "text", [v])[0] if hasattr(v, "text") else v)[:120]
                return ""
            out["title"] = tag("TIT2", "title", "\xa9nam", "TITLE")
            out["artist"] = tag("TPE1", "artist", "\xa9ART", "ARTIST")
            pic = None
            for k in list(getattr(tags, "keys", lambda: [])()):
                if str(k).startswith("APIC"):
                    pic = tags[k].data
                    break
            if pic is None and hasattr(mf, "pictures") and mf.pictures:
                pic = mf.pictures[0].data
            if pic is None and "covr" in tags:
                pic = bytes(tags["covr"][0])
            if pic:
                img = QImage()
                img.loadFromData(pic)
                out["thumb"], _w, _h = _thumb_b64(img, 240)
    except Exception:                                          # noqa: BLE001
        pass
    try:                                                       # «волна»: 48 столбиков по громкости
        import av
        import numpy as np
        peaks = []
        with av.open(str(path)) as c:
            st = next(s for s in c.streams if s.type == "audio")
            res = av.AudioResampler(format="s16", layout="mono", rate=4000)
            for fr in c.decode(st):
                for rf in res.resample(fr):
                    peaks.append(np.abs(rf.to_ndarray().astype(np.int32)).ravel())
                if sum(len(p) for p in peaks) > 4000 * 900:    # не больше 15 минут
                    break
        if peaks:
            a = np.concatenate(peaks)
            n = 48
            seg = max(1, len(a) // n)
            vals = [float(a[i * seg:(i + 1) * seg].max(initial=0)) for i in range(n)]
            top = max(vals) or 1.0
            out["wave"] = [round(v / top, 2) for v in vals]
            out.setdefault("dur", len(a) / 4000)
    except Exception:                                          # noqa: BLE001
        pass
    return out


def _pdf(path) -> dict:
    out = {}
    try:
        from PyQt6.QtPdf import QPdfDocument
        from PyQt6.QtCore import QSize
        doc = QPdfDocument(None)
        doc.load(str(path))
        out["pages"] = doc.pageCount()
        if doc.pageCount():
            ps = doc.pagePointSize(0)
            k = 480 / max(1.0, ps.width())
            img = doc.render(0, QSize(int(ps.width() * k), int(ps.height() * k)))
            from PyQt6.QtGui import QImage, QPainter, QColor
            page = QImage(img.size(), QImage.Format.Format_RGB32)   # страница прозрачная — подложить белый лист
            page.fill(QColor("#ffffff"))
            pt = QPainter(page)
            pt.drawImage(0, 0, img)
            pt.end()
            out["thumb"], out["w"], out["h"] = _thumb_b64(page)
        doc.close()
    except Exception:                                          # noqa: BLE001
        pass
    return out


def _text(path) -> dict:
    try:
        raw = Path(path).read_bytes()[:256 * 1024]
        txt = raw.decode("utf-8", errors="replace")
        lines = txt.splitlines()
        return {"text": "\n".join(lines[:14])[:1200], "lines": txt.count("\n") + 1}
    except OSError:
        return {}


def _archive(path) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
        return {"items": [safe_name(n.split("/")[-1]) for n in names[:12]], "count": len(names)}
    except Exception:                                          # noqa: BLE001
        return {}


def preview(path) -> dict:
    """Описание файла для сообщения: имя, размер, тип, хэш и превью (звать из фонового потока)."""
    p = Path(path)
    size = p.stat().st_size
    if size > MAX_FILE:
        raise ValueError(f"файл больше {human(MAX_FILE)} — через Tor это слишком долго")
    kind = kind_of(p.name)
    meta = {"name": safe_name(p.name), "size": size, "kind": kind, "sha": sha256_file(p),
            "mime": (mimetypes.guess_type(p.name)[0] or "application/octet-stream")[:60]}
    pv = {}
    if kind == "image":
        from PyQt6.QtGui import QImage, QImageReader
        rd = QImageReader(str(p))
        pv["gif"] = p.suffix.lower() == ".gif"
        img = rd.read()
        pv["thumb"], pv["w"], pv["h"] = _thumb_b64(img)
    elif kind == "video":
        pv = _video(p)
    elif kind == "audio":
        pv = _audio(p)
    elif kind == "pdf":
        pv = _pdf(p)
    elif kind == "text":
        pv = _text(p)
    elif kind == "archive":
        pv = _archive(p)
    meta["pv"] = {k: v for k, v in pv.items() if v not in (None, "", [], {})}
    return meta


def clean_meta(m) -> dict | None:
    """Описание файла из сети: только известные поля и разумные размеры."""
    if not isinstance(m, dict):
        return None
    fid = str(m.get("fid") or "")
    sha = str(m.get("sha") or "")
    if not (8 <= len(fid) <= 40 and fid.isalnum()) or len(sha) != 64:
        return None
    try:
        size = int(m.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 <= size <= MAX_FILE:
        return None
    kind = m.get("kind") if m.get("kind") in KIND_COLOR else kind_of(str(m.get("name", "")))
    out = {"fid": fid, "name": safe_name(m.get("name")), "size": size, "kind": kind, "sha": sha.lower(),
           "mime": str(m.get("mime") or "")[:60]}
    pv = m.get("pv") if isinstance(m.get("pv"), dict) else {}
    cp = {}
    th = pv.get("thumb")
    if isinstance(th, str) and len(th) <= THUMB_MAX + 64:
        try:
            if base64.b64decode(th)[:3] == b"\xff\xd8\xff":
                cp["thumb"] = th
        except ValueError:
            pass
    for k in ("w", "h", "pages", "lines", "count"):
        try:
            if pv.get(k) is not None:
                cp[k] = max(0, min(10 ** 7, int(pv[k])))
        except (TypeError, ValueError):
            pass
    try:
        if pv.get("dur") is not None:
            cp["dur"] = max(0.0, min(360000.0, float(pv["dur"])))
    except (TypeError, ValueError):
        pass
    for k, n in (("title", 120), ("artist", 120), ("text", 1200)):
        if isinstance(pv.get(k), str):
            cp[k] = pv[k][:n]
    if pv.get("gif"):
        cp["gif"] = True
    if isinstance(pv.get("wave"), list):
        try:
            cp["wave"] = [max(0.0, min(1.0, float(x))) for x in pv["wave"][:64]]
        except (TypeError, ValueError):
            pass
    if isinstance(pv.get("items"), list):
        cp["items"] = [safe_name(x) for x in pv["items"][:12] if isinstance(x, str)]
    out["pv"] = cp
    return out


def shrink_meta(meta: dict, max_thumb: int = 9000) -> dict:
    """Копия описания файла с маленькой миниатюрой — для публичных постов (у релеев Nostr лимит на размер)."""
    m = dict(meta)
    pv = dict(m.get("pv") or {})
    th = pv.get("thumb")
    if th and len(th) > max_thumb:
        try:
            from PyQt6.QtGui import QImage
            img = QImage()
            img.loadFromData(base64.b64decode(th))
            pv["thumb"] = ""
            for side in (200, 150, 110, 80):
                b64, _w, _h = _thumb_b64(img, side)
                if b64 and len(b64) <= max_thumb:
                    pv["thumb"] = b64
                    break
        except Exception:                                      # noqa: BLE001
            pv.pop("thumb", None)
    pv.pop("wave", None) if len(str(pv.get("wave", ""))) > 400 else None
    pv.pop("text", None) if len(str(pv.get("text", ""))) > 600 else None
    m["pv"] = {k: v for k, v in pv.items() if v not in ("", None)}
    return m
