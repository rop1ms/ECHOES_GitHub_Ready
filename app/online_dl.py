# online_dl.py
"""
Очередь закачек из онлайн-поиска (без ссылок): трек находится в поиске,
клик «Скачать» — ставится в очередь, качается через тот же движок, что и
загрузчик (yt-dlp: MP3 320, обложка, теги), попадает в коллекцию и, если
выбрано, в плейлист. Можно «Скачать и слушать».
Состояния: queued → loading (pct) → done / error  (+ retry — пробую другой источник).

Надёжность (чтобы «ошибка» была только когда трека реально нет нигде):
  * у каждого задания — список источников: найденная ссылка, та же ссылка
    через www.youtube.com, запасные совпадения из поиска, затем поиск
    yt-dlp по «исполнитель — название» на YouTube и на SoundCloud;
  * каждое задание качается в СВОЮ пустую временную папку — новый файл
    определяется однозначно (раньше уже скачанный ранее файл или соседняя
    закачка в той же папке давали ложную «ошибку»), потом файл переносится
    в папку загрузок; совпавшие имена получают отдельный суффикс;
  * если не удалось ни с одного источника — задание откладывается и
    повторяется ещё раз в конце очереди (после паузы: YouTube часто
    временно режет частые запросы).
"""
from __future__ import annotations

import data_home
import hashlib
import io
import shutil
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from downloader import DownloadManager, AUDIO_EXTS

TMP_ROOT = data_home.root() / "dl_tmp"
ROUNDS = 3                 # сколько полных проходов по всем источникам
ROUND_PAUSE_MS = (0, 15000, 45000)


def _yt_id(url: str) -> str:
    u = url or ""
    for key in ("v=", "youtu.be/"):
        if key in u:
            return u.split(key, 1)[1].split("&")[0].split("?")[0].split("/")[0]
    return ""


def build_candidates(url: str, track: dict) -> list[str]:
    """Упорядоченный список того, что можно отдать yt-dlp для этого трека."""
    out: list[str] = []

    def add(u):
        u = (u or "").strip()
        if u and u not in out:
            out.append(u)

    add(url)
    vid = _yt_id(url)
    if vid and ("youtube" in (url or "") or "youtu.be" in (url or "")):
        add(f"https://www.youtube.com/watch?v={vid}")
        add(f"https://music.youtube.com/watch?v={vid}")
    alts = list(track.get("alts") or [])
    alts.sort(key=lambda u: "soundcloud" in u)        # SoundCloud — только после всех YouTube
    yt_alts = [u for u in alts if "soundcloud" not in u]
    sc_alts = [u for u in alts if "soundcloud" in u]
    for alt in yt_alts:
        add(alt)
        v2 = _yt_id(alt)
        if v2:
            add(f"https://www.youtube.com/watch?v={v2}")
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").split(",")[0].strip()
    if title:
        q = f"{artist} - {title}" if artist else title
        q = q.replace(":", " ")
        add(f"ytsearch1:{q} audio")
        add(f"ytsearch1:{q}")
    for alt in sc_alts:
        add(alt)
    if title:
        add(f"scsearch1:{artist} {title}".strip())
    return out


def _square_jpeg(data: bytes) -> bytes:
    """Обложка → квадрат по центру, JPEG ~600px (у YouTube превью 16:9)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
        if s > 800:
            im = im.resize((800, 800), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=92)
        return out.getvalue()
    except Exception:                                 # noqa: BLE001
        return data


def _has_cover(m) -> bool:
    tags = getattr(m, "tags", None)
    try:
        if tags is not None and hasattr(tags, "getall") and tags.getall("APIC"):
            return True
    except Exception:                                 # noqa: BLE001
        pass
    if getattr(m, "pictures", None):
        return True
    try:
        if tags is not None and tags.get("covr"):
            return True
    except Exception:                                 # noqa: BLE001
        pass
    return False


def finalize_file(path: str, track: dict):
    """Довести скачанный файл до ума: альбом/название/исполнитель из поиска,
    если теги пустые, и обложка из поиска, если yt-dlp её не вшил."""
    try:
        import mutagen
        m = mutagen.File(path)
    except Exception:                                 # noqa: BLE001
        return
    if m is None:
        return
    ext = Path(path).suffix.lower()
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").strip()
    album = (track.get("album") or "").strip()
    # ── текстовые теги ──
    try:
        e = mutagen.File(path, easy=True)
        if e is not None:
            if e.tags is None:
                e.add_tags()
            changed = False
            for key, val in (("album", album), ("title", title), ("artist", artist)):
                if val and not (e.tags.get(key) or [""])[0].strip():
                    e.tags[key] = val
                    changed = True
            if changed:
                e.save()
    except Exception as ex:                           # noqa: BLE001
        print("[online dl] теги:", ex)
    # ── обложка ──
    try:
        m = mutagen.File(path)
        if m is None or _has_cover(m):
            return
        url = track.get("thumb_url") or track.get("cover_url") or ""
        if not url.startswith("http"):
            return
        import requests
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or len(r.content) < 500:
            return
        data = _square_jpeg(r.content)
        if ext == ".mp3":
            from mutagen.id3 import ID3, APIC, ID3NoHeaderError
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data))
            tags.save(path, v2_version=3)
        elif ext == ".flac":
            from mutagen.flac import FLAC, Picture
            f = FLAC(path)
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", data
            f.add_picture(pic)
            f.save()
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            f = MP4(path)
            f["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
            f.save()
    except Exception as ex:                           # noqa: BLE001
        print("[online dl] обложка:", ex)


class OnlineDownloadQueue(QObject):
    state_changed = pyqtSignal(str, str, float)      # url, state, pct
    track_ready = pyqtSignal(str, str, object)       # path, playlist ('' — только коллекция), track info
    _finalized = pyqtSignal(str, str, object)        # из фонового потока → в GUI
    _resolved = pyqtSignal(object, object)           # (job, [ссылки YT Music]) из фонового потока

    def __init__(self, out_dir_fn, quality="mp3", parent=None):
        super().__init__(parent)
        self._out_dir_fn = out_dir_fn
        self.quality = quality
        self.mgr = DownloadManager(self)
        self.mgr.progress.connect(self._on_progress)
        self.mgr.item_finished.connect(self._on_item)
        self.mgr.finished.connect(self._on_finished)
        self.mgr.log.connect(self._on_log)
        self.queue: list[dict] = []
        self.deferred: list[dict] = []                # провалились во всех источниках — повторим позже
        self.current: dict | None = None
        self.states: dict[str, tuple] = {}            # url → (state, pct)
        self.errors: dict[str, str] = {}              # url → последняя причина
        self._got_file = False
        self._last_log = ""
        self._round_timer = QTimer(self)
        self._round_timer.setSingleShot(True)
        self._round_timer.timeout.connect(self._start_deferred)
        self._finalized.connect(self._emit_ready)
        self._resolved.connect(self._on_resolved)

    def state(self, url):
        return self.states.get(url, ("", 0.0))

    def enqueue(self, track: dict, playlist: str = "", play: bool = False):
        url = track.get("url", "")
        if not url:
            return
        st = self.states.get(url, ("",))[0]
        if st in ("queued", "loading", "retry"):
            if play and self.current and self.current.get("url") == url:
                self.current["play"] = True
            for j in self.queue + self.deferred:
                if j["url"] == url:
                    j["play"] = j["play"] or play
            return
        job = {"url": url, "track": track, "playlist": playlist, "play": play,
               "cands": build_candidates(url, track), "ci": 0, "round": 0, "why": []}
        if "soundcloud.com" in url:
            # Выбран конкретный трек SoundCloud — качаем ИМЕННО его. Раньше сначала искали «похожий» на
            # YouTube Music и брали первый результат: андеграундных треков там нет, и скачивался чужой трек.
            # Запасной вариант (YT Music) — только если SoundCloud не отдал файл, и только при точном
            # совпадении названия, исполнителя и длительности.
            job["cands"] = [url]
            job["sc_fallback"] = True
        self.queue.append(job)
        self._set(url, "queued", 0.0)
        self._next()

    def _set(self, url, st, pct):
        self.states[url] = (st, pct)
        self.state_changed.emit(url, st, float(pct))

    # ── выполнение ── #

    def _tmp_dir(self, job) -> Path:
        h = hashlib.md5(job["url"].encode("utf-8", "ignore")).hexdigest()[:12]
        d = TMP_ROOT / h
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _next(self):
        if self.current is not None or self.mgr.busy:
            return
        if not self.queue:
            if self.deferred and not self._round_timer.isActive():
                rnd = min(self.deferred[0]["round"], len(ROUND_PAUSE_MS) - 1)
                self._round_timer.start(ROUND_PAUSE_MS[rnd])
            return
        self.current = self.queue.pop(0)
        self._got_file = False
        self._last_log = ""
        job = self.current
        if not job.get("resolved"):
            job["resolved"] = True
            if self._needs_ytm(job):
                self._resolve_ytm(job)              # сначала ищем этот трек на YouTube Music
                return
        src = job["cands"][job["ci"]]
        try:
            job["tmp"] = self._tmp_dir(job)
        except OSError as e:
            job["tmp"] = Path(self._out_dir_fn())
            print("[online dl] tmp:", e)
        self._set(job["url"], "loading" if job["ci"] == 0 and job["round"] == 0 else "retry", 0.0)
        if not self.mgr.start(src, self.quality, str(job["tmp"])):
            self.queue.insert(0, job)
            self.current = None
            QTimer.singleShot(300, self._next)

    # ── приоритет YouTube Music ── #

    @staticmethod
    def _needs_ytm(job) -> bool:
        """Трек из SoundCloud (или поиск-заглушка) — сначала пробуем найти его
        на YouTube Music, а SoundCloud оставляем на крайний случай."""
        url = job.get("url", "")
        t = job.get("track") or {}
        if not (t.get("title") or "").strip():
            return False
        return url.startswith(("scsearch", "ytsearch"))

    def _resolve_ytm(self, job, strict=False):
        self._set(job["url"], "queued", 0.0)
        t = dict(job.get("track") or {})

        def work():
            found = []
            try:
                import online_search
                title = (t.get("title") or "").strip()
                artist = (t.get("artist") or "").split(",")[0].strip()
                dur = float(t.get("duration") or 0)
                # у SoundCloud «исполнитель» часто ник загрузившего, а настоящий
                # артист — в названии («Artist - Title»)
                if " - " in title and not job["url"].startswith(("ytsearch", "scsearch")):
                    a2, t2 = title.split(" - ", 1)
                    found = online_search.find_candidates(t2.strip(), a2.strip(), dur, allow_sc=False)
                if not found:
                    found = online_search.find_candidates(title, artist, dur, allow_sc=False)
                if strict:                            # запасной вариант для SoundCloud: только точное совпадение
                    found = [c for c in found if c.get("score", 0) >= 0.85
                             and (not dur or not c.get("duration") or abs(float(c["duration"]) - dur) <= 4)]
            except Exception as e:                    # noqa: BLE001
                print("[online dl] ytm resolve:", e)
            self._resolved.emit(job, [c["url"] for c in found if c.get("url")])
        threading.Thread(target=work, daemon=True).start()

    def _on_resolved(self, job, urls):
        if self.current is not job:
            return
        if job.get("strict_fb"):
            # SoundCloud не отдал файл — только точно совпавшие треки с YouTube Music, без «поиска наугад»
            job.pop("strict_fb", None)
            job["cands"], job["ci"] = list(urls), 0
            self.current = None
            if urls:
                job.setdefault("track", {})["source"] = "ytm"
                self.queue.insert(0, job)
            else:
                why = "; ".join(job.get("why") or []) or "SoundCloud не отдал файл"
                self.errors[job["url"]] = why + " — на YouTube Music точного совпадения нет"
                self._set(job["url"], "error", 0.0)
            self._next()
            return
        track = dict(job.get("track") or {})
        orig = job["url"]
        alts = list(urls[1:]) + list(track.get("alts") or [])
        if not orig.startswith(("ytsearch", "scsearch")):
            alts.append(orig)                         # исходная ссылка SoundCloud — в самом конце
        track["alts"] = alts
        primary = urls[0] if urls else (orig if orig.startswith("ytsearch") else "")
        cands = build_candidates(primary, track) if primary else build_candidates("", track)
        if not cands:
            cands = [orig]
        job["cands"], job["ci"] = cands, 0
        if urls:
            job.setdefault("track", {})["source"] = "ytm"
        self.current = None
        self.queue.insert(0, job)
        self._next()

    def _start_deferred(self):
        jobs, self.deferred = self.deferred, []
        for j in jobs:
            j["ci"] = 0
            self.queue.append(j)
        self._next()

    def _on_log(self, msg):
        if "ОШИБКА" in msg or "Не удалось" in msg:
            self._last_log = msg.replace("[yt-dlp] ОШИБКА:", "").strip()

    def _on_progress(self, idx, total, name, pct, speed):
        if self.current:
            st = "loading" if self.current["ci"] == 0 and self.current["round"] == 0 else "retry"
            self._set(self.current["url"], st, float(pct))

    def _move_up(self, path: str) -> str:
        """Сохранить загрузку, не заменяя файлы с совпавшим именем."""
        src = Path(path)
        out = Path(self._out_dir_fn())
        out.mkdir(parents=True, exist_ok=True)
        if src.parent.resolve() == out.resolve():
            return str(src)
        suffix = 1
        while True:
            name = src.name if suffix == 1 else f"{src.stem} ({suffix}){src.suffix}"
            dst = out / name
            try:
                target = dst.open("xb")
                break
            except FileExistsError:
                suffix += 1
        try:
            with target, src.open("rb") as source:
                shutil.copyfileobj(source, target)
        except Exception:
            # Only our newly created partial copy is removed; the source survives.
            dst.unlink(missing_ok=True)
            raise
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass
        try:
            src.unlink()
        except OSError:
            pass
        return str(dst)

    @staticmethod
    def _length(path) -> float:
        try:
            from mutagen import File as MF
            m = MF(path)
            return float(m.info.length) if m and getattr(m, "info", None) else 0.0
        except Exception:                             # noqa: BLE001
            return 0.0

    def _is_preview(self, path) -> bool:
        exp = float((self.current or {}).get("track", {}).get("duration") or 0)
        return self._is_preview_file(path, exp)

    def _wrong_track(self, path) -> bool:
        """Файл пришёл из поиска «наугад» (ytsearch/scsearch) или запасного источника, а длительность
        заметно не та, что у выбранного трека, — значит, скачался чужой трек."""
        job = self.current or {}
        cands = job.get("cands") or []
        src = cands[job.get("ci", 0)] if 0 <= job.get("ci", 0) < len(cands) else ""
        if src == job.get("url") and not src.startswith(("ytsearch", "scsearch")):
            return False                              # ровно та ссылка, которую выбрали, — верим ей
        exp = float((job.get("track") or {}).get("duration") or 0)
        ln = self._length(path)
        if exp <= 0 or ln <= 0:
            return False
        return abs(ln - exp) > max(8.0, exp * 0.07)

    @classmethod
    def _is_preview_file(cls, path, exp=0.0) -> bool:
        """30-секундное превью (SoundCloud Go+ и т.п.) или явный обрезок."""
        ln = cls._length(path)
        if ln <= 0:
            return False
        exp = float(exp or 0)
        if ln < 45 and (exp == 0 or exp > 60):
            return True
        if exp > 60 and ln < exp * 0.6:
            return True
        return False

    def _on_item(self, path):
        if not self.current or self._got_file:
            return
        if Path(path).suffix.lower() not in AUDIO_EXTS or not Path(path).exists():
            return
        if self._is_preview(path):
            self._last_log = "источник отдал только 30-секундное превью"
            try:
                Path(path).unlink()
            except OSError:
                pass
            return
        if self._wrong_track(path):
            self._last_log = "поиск нашёл другой трек (не совпала длительность)"
            try:
                Path(path).unlink()
            except OSError:
                pass
            return
        try:
            final = self._move_up(path)
        except (OSError, ValueError, TypeError) as e:
            self.current["move_error"] = f"Не удалось сохранить загрузку: {e}. Файл оставлен: {path}"
            self._last_log = self.current["move_error"]
            return
        self._got_file = True
        self.errors.pop(self.current["url"], None)
        job = self.current
        # теги/обложку дописываем ДО того, как трек попадёт в коллекцию и заиграет
        def work():
            try:
                finalize_file(final, job.get("track") or {})
            finally:
                self._finalized.emit(final, job.get("playlist", ""), job)
        threading.Thread(target=work, daemon=True).start()

    def _emit_ready(self, path, playlist, job):
        try:
            self.track_ready.emit(path, playlist, job)
        except Exception as e:                        # noqa: BLE001
            print("[online dl] track_ready:", e)

    def _on_finished(self, ok, msg):
        job, self.current = self.current, None
        if job is None:
            self._next()
            return
        tmp = job.pop("tmp", None)
        if job.get("move_error"):
            # Do not announce a temp path as a finished download and then delete it.
            self.errors[job["url"]] = job["move_error"]
            self._set(job["url"], "error", 0.0)
            self._next()
            return
        if tmp is not None and tmp.parent == TMP_ROOT:
            # на всякий случай: файл есть, а item_finished не пришёл
            if not self._got_file:
                files = [p for p in tmp.glob("*") if p.suffix.lower() in AUDIO_EXTS]
                if files:
                    self.current = job
                    self._on_item(str(max(files, key=lambda p: p.stat().st_size)))
                    self.current = None
            shutil.rmtree(tmp, ignore_errors=True)
        if self._got_file:
            self._set(job["url"], "done", 100.0)
        else:
            why = (msg if not ok else "") or self._last_log or "источник не отдал файл"
            job["why"].append(why)
            if msg == "Отменено пользователем":
                self.errors[job["url"]] = why
                self._set(job["url"], "error", 0.0)
            elif job["ci"] + 1 < len(job["cands"]):
                job["ci"] += 1                       # следующий источник — сразу
                self.queue.insert(0, job)
                self._set(job["url"], "retry", 0.0)
            elif job.get("sc_fallback"):
                job["sc_fallback"] = False           # SoundCloud не отдал — ищем ТОЧНО такой же трек на YT Music
                job["strict_fb"] = True
                self.current = job
                self._set(job["url"], "retry", 0.0)
                self._resolve_ytm(job, strict=True)
                return
            elif job["round"] + 1 < ROUNDS:
                job["round"] += 1                    # все источники мимо — повторим позже
                self.deferred.append(job)
                self._set(job["url"], "retry", 0.0)
            else:
                self.errors[job["url"]] = why
                self._set(job["url"], "error", 0.0)
        self._next()

