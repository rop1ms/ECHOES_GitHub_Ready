# downloader.py
"""
Downloader Engine — скачивание треков/альбомов/плейлистов по ссылке.

  * YouTube / YouTube Music / SoundCloud / VK — через yt-dlp (Python API),
    с прогресс-хуками (скорость/процент/имя текущего трека).
  * Spotify — spotdl: читает метаданные Spotify, скачивает с YouTube Music.

Всё выполняется в фоновом потоке (threading.Thread), прогресс и результаты
возвращаются в GUI-поток через pyqtSignal.

ВАЖНО (легальность): скачивание используйте только для контента, на который
у вас есть права. Соблюдайте условия платформ и законодательство об АП.
"""

import re
import sys
import shutil
import threading
import subprocess
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}


class _YdlLogger:
    """
    Пересылает предупреждения и ошибки yt-dlp в лог интерфейса.
    errors_list (опционально) — сюда же копятся все error()-сообщения,
    чтобы потом честно посчитать, сколько треков реально провалилось
    (с ignoreerrors=True код возврата download() почти всегда 0).
    """

    def __init__(self, log_signal, errors_list=None):
        self._log = log_signal
        self._errors = errors_list if errors_list is not None else []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self._log.emit(f"[yt-dlp] {msg}")

    def error(self, msg):
        self._log.emit(f"[yt-dlp] ОШИБКА: {msg}")
        self._errors.append(str(msg))


def detect_platform(url: str) -> str:
    u = (url or "").strip().lower()
    if u.startswith("scsearch"):
        return "soundcloud"
    if u.startswith("ytsearch"):
        return "youtube"
    if "spotify.com" in u or u.startswith("spotify:"):
        return "spotify"
    if "vk.com" in u or "vk.ru" in u:
        return "vk"
    if "soundcloud.com" in u:
        return "soundcloud"
    if "music.youtube.com" in u or "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "generic"


PLATFORM_LABELS = {
    "spotify": "Spotify",
    "vk": "VK Music",
    "soundcloud": "SoundCloud",
    "youtube": "YouTube / YouTube Music",
    "generic": "Другое (пробуем через yt-dlp)",
}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _embed_cover_ffmpeg(audio_path: Path, cover_path: Path) -> bool:
    """Вшивает обложку в аудиофайл через прямой вызов ffmpeg subprocess."""
    if not ffmpeg_available() or not cover_path.exists() or not audio_path.exists():
        return False

    tmp = audio_path.with_suffix(".tmp" + audio_path.suffix)
    ext = audio_path.suffix.lower()

    try:
        if ext == ".mp3":
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(audio_path),
                "-i", str(cover_path),
                "-map", "0:a", "-map", "1:v",
                "-c:a", "copy", "-c:v", "mjpeg",
                "-id3v2_version", "3",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)",
                str(tmp),
            ]
        elif ext == ".flac":
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(audio_path),
                "-i", str(cover_path),
                "-map", "0", "-map", "1:v",
                "-c", "copy", "-c:v", "mjpeg",
                str(tmp),
            ]
        elif ext in (".m4a", ".mp4"):
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(audio_path),
                "-i", str(cover_path),
                "-map", "0", "-map", "1:v",
                "-c", "copy", "-c:v", "mjpeg",
                "-disposition:v:0", "attached_pic",
                str(tmp),
            ]
        else:
            return False

        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0 and tmp.exists():
            audio_path.unlink()
            tmp.rename(audio_path)
            return True
        else:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            return False
    except Exception as e:
        print(f"[embed_cover] {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False


def _find_cover(stem: str, directory: Path) -> Path | None:
    """Ищет thumbnail-файл по стему имени трека."""
    for ext in (".jpg", ".jpeg", ".webp", ".png"):
        p = directory / (stem + ext)
        if p.exists():
            return p
    return None


def _convert_webp_to_jpg(src: Path) -> Path | None:
    """Конвертирует webp → jpg через ffmpeg."""
    if not ffmpeg_available():
        return None
    dst = src.with_suffix(".jpg")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), str(dst)],
            capture_output=True,
        )
        if r.returncode == 0 and dst.exists():
            return dst
    except Exception as e:
        print(f"[webp→jpg] {e}")
    return None


class DownloadManager(QObject):
    """Один экземпляр на приложение. Параллельные закачки не поддерживаются."""

    progress = pyqtSignal(int, int, str, float, float)
    item_finished = pyqtSignal(str)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._cancel_flag = threading.Event()
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def start(self, url: str, quality: str, out_dir: str, vk_cookies: str = "",
              spotify_client_id: str = "", spotify_client_secret: str = ""):
        if self._busy:
            return False
        self._cancel_flag.clear()
        self._busy = True
        self._thread = threading.Thread(
            target=self._run,
            args=(url, quality, out_dir, vk_cookies, spotify_client_id, spotify_client_secret),
            daemon=True,
        )
        self._thread.start()
        return True

    def cancel(self):
        self._cancel_flag.set()

    # ------------------------------------------------------------------ #

    @staticmethod
    def _snapshot(out_dir: Path):
        return {p.resolve() for p in out_dir.glob("*") if p.suffix.lower() in AUDIO_EXTS}

    def _run(self, url, quality, out_dir, vk_cookies, spotify_client_id="", spotify_client_secret=""):
        out_dir = Path(out_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            platform = detect_platform(url)
            if platform == "soundcloud" and not self._looks_like_collection(url) and "?" in url:
                url = url.split("?", 1)[0]         # ?in=…/sets/…, utm_… — хвосты страницы, а не часть трека
            before = self._snapshot(out_dir)

            if platform == "spotify":
                self._run_spotdl(url, quality, out_dir, before,
                                  spotify_client_id, spotify_client_secret)
            else:
                self._run_ytdlp(url, quality, out_dir, platform, vk_cookies)
                after = self._snapshot(out_dir)
                new_files = sorted(after - before, key=lambda p: p.stat().st_mtime)
                if not new_files:
                    new_files = list(getattr(self, "_already", []) or [])   # уже скачанный ранее
                for f in new_files:
                    if self._cancel_flag.is_set():
                        break
                    self.item_finished.emit(str(f))

            if self._cancel_flag.is_set():
                result = (False, "Отменено пользователем")
            else:
                result = (True, "Готово")
        except Exception as e:
            result = (False, str(e))
        finally:
            # сначала освобождаемся, потом сообщаем: слот finished (в GUI-потоке)
            # часто сразу запускает следующую закачку — раньше она иногда
            # видела busy=True и очередь вставала.
            self._busy = False
        self.finished.emit(*result)

    # ------------------------------------------------------------------ #
    #  yt-dlp                                                              #
    # ------------------------------------------------------------------ #
    #
    #  ИСПРАВЛЕНИЕ БАГА «SoundCloud качает только со 2-го клика»
    #  ----------------------------------------------------------
    #  Что было не так (в порядке важности):
    #
    #  1. NameError. В конце старого _run_ytdlp использовалась переменная
    #     `new_files`, которая существует ТОЛЬКО в _run(). Стоило yt-dlp
    #     записать в лог хотя бы одну ошибку (а SoundCloud на первом запросе
    #     это делает — см. п.2), как метод падал с
    #     «name 'new_files' is not defined», _run_ytdlp не возвращал
    #     управление, и item_finished для уже скачанного файла не
    #     эмитился: трек лежал на диске, но в библиотеку не попадал.
    #
    #  2. Холодный client_id. SoundCloud не даёт публичного API-ключа:
    #     yt-dlp сам вытаскивает client_id из JS-бандлов soundcloud.com и
    #     кладёт в свой кэш. При ПЕРВОМ запуске кэш пуст, берётся
    #     зашитый в yt-dlp (обычно уже протухший) id → 401/403 → yt-dlp
    #     обновляет id и только затем идёт дальше. Из-за ignoreerrors=True
    #     этот сбой превращался в «провал загрузки», а кэш к этому моменту
    #     уже был исправлен — поэтому 2-й клик проходил.
    #
    #  3. Не было повторной попытки. С ignoreerrors=True ydl.download()
    #     возвращает 0 даже при провале, так что о неудаче можно судить
    #     только по списку ошибок логгера и по отсутствию новых файлов.
    #
    #  Что сделано: (а) исправлен NameError; (б) для SoundCloud перед
    #  скачиванием «прогревается» client_id; (в) при неудаче (есть ошибки и
    #  нет новых файлов) автоматически делается до 3 попыток с принудительным
    #  обновлением client_id — всё в рамках ОДНОГО клика.

    _SC_ATTEMPTS = 3

    @staticmethod
    def _looks_like_collection(url: str) -> bool:
        """Грубая эвристика: ссылка на плейлист/альбом/профиль, а не на один трек."""
        u = (url or "").lower().split("?")[0].rstrip("/")
        if any(k in u for k in ("/sets/", "playlist", "/albums", "/likes", "/reposts")):
            return True
        if "on.soundcloud.com/" in u:
            return False                        # короткая ссылка «Поделиться» — обычно один трек
        if "soundcloud.com/" in u:
            # soundcloud.com/<user> — профиль (один сегмент пути), а
            # soundcloud.com/<user>/<track> — одиночный трек
            path = u.split("soundcloud.com/", 1)[1]
            return path.count("/") == 0
        return False

    @staticmethod
    def _collect_ids(info) -> set:
        ids = set()
        if not info:
            return ids
        if info.get("id"):
            ids.add(str(info["id"]))
        for e in info.get("entries") or []:
            if e and e.get("id"):
                ids.add(str(e["id"]))
        return ids

    def _sleep_cancellable(self, seconds: float):
        """Пауза, которую можно прервать кнопкой «Отмена»."""
        self._cancel_flag.wait(timeout=max(0.0, seconds))

    def _prepare_soundcloud(self, ydl, force_refresh: bool):
        """
        «Прогрев» client_id SoundCloud ДО основного запроса.
        Если в кэше yt-dlp его нет (первый запуск) или предыдущая попытка
        провалилась — заставляем экстрактор заново вытащить свежий id.
        Всё в try/except: это оптимизация, а не критичный шаг — приватные
        методы yt-dlp могут поменяться между версиями.
        """
        try:
            cached = ydl.cache.load("soundcloud", "client_id")
            if cached and not force_refresh:
                return
            if force_refresh:
                # Сбрасываем протухший id, чтобы экстрактор не взял его снова
                ydl.cache.store("soundcloud", "client_id", None)
            ie = ydl.get_info_extractor("Soundcloud")
            ie.initialize()
            ie._update_client_id()          # приватный, но стабильный метод
            self.log.emit("SoundCloud: client_id обновлён.")
        except Exception as e:              # noqa: BLE001
            self.log.emit(f"SoundCloud: не удалось заранее обновить client_id ({e}); "
                          f"yt-dlp попробует сам.")

    def _build_ytdlp_opts(self, yt_dlp, url, quality, out_dir, platform,
                          vk_cookies, ydl_errors, progress_state, info_by_stem):
        """Собирает словарь настроек yt-dlp (вынесено, чтобы пересоздавать на каждую попытку)."""
        codec = "flac" if quality == "flac" else "mp3"
        # %(id)s гарантирует уникальность имени файла (см. прежний комментарий).
        outtmpl = str(out_dir / "%(uploader)s - %(title)s [%(id)s].%(ext)s")

        def hook(d):
            if self._cancel_flag.is_set():
                raise yt_dlp.utils.DownloadError("Отменено пользователем")
            st = d.get("status")
            info = d.get("info_dict") or {}
            if st == "downloading":
                total = info.get("n_entries") or info.get("playlist_count") or 1
                idx = info.get("playlist_index") or 1
                progress_state["total"] = max(progress_state["total"], int(total))
                pct_str = re.sub(r"\x1b\[[0-9;]*m", "", d.get("_percent_str", "0%")).strip().rstrip("%")
                try:
                    pct = float(pct_str)
                except ValueError:
                    pct = 0.0
                speed = d.get("speed") or 0.0
                name = info.get("title") or Path(d.get("filename", "")).stem
                self.progress.emit(int(idx), int(total), name, pct, speed / 1024.0)
            elif st == "finished":
                name = info.get("title") or Path(d.get("filename", "")).stem
                self.log.emit(f"Скачано: {name} — конвертация…")
                fn = d.get("filename") or info.get("filename")
                if fn:
                    info_by_stem[Path(fn).stem] = info
            elif st == "error":
                name = info.get("title") or Path(d.get("filename", "")).stem or "трек"
                progress_state["failed"].append(name)
                self.log.emit(f"Не удалось скачать: {name}")

        opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "restrictfilenames": True,
            # ссылка на ОДИН трек (…watch?v=…&list=…, soundcloud.com/…?in=…/sets/…) — качаем только его,
            # а не весь плейлист/«микс», в котором он открыт (оттуда брались «случайные» треки)
            "noplaylist": not self._looks_like_collection(url),
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            "logger": _YdlLogger(self.log, ydl_errors),
            "writethumbnail": True,
            "overwrites": False,
            "progress_hooks": [hook],
            "postprocessors": [
                # обложка: превью → jpg (квадрат по центру — у YouTube картинки 16:9),
                # затем вшивается в файл силами самого yt-dlp; если это не
                # получится, ниже сработает запасной _post_embed_covers.
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "FFmpegExtractAudio", "preferredcodec": codec,
                 **({"preferredquality": "320"} if codec == "mp3" else {})},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ],
            "postprocessor_args": {
                "thumbnailsconvertor+ffmpeg_o": [
                    "-c:v", "mjpeg", "-q:v", "2",
                    "-vf", "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'"],
            },
            # yt-dlp сам повторяет сетевые обрывы фрагментов/запросов
            "retries": 5,
            "fragment_retries": 5,
            "extractor_retries": 3,
            "socket_timeout": 25,          # зависший запрос не блокирует очередь навсегда
        }
        # Пауза между треками нужна только для альбомов/плейлистов (анти-
        # троттлинг YouTube). Раньше она стояла всегда и добавляла 2-5 с
        # ожидания даже к одиночному треку.
        if self._looks_like_collection(url):
            opts["sleep_interval"] = 2
            opts["max_sleep_interval"] = 5
        if platform == "vk" and vk_cookies and Path(vk_cookies).exists():
            opts["cookiefile"] = str(vk_cookies)
        return opts

    def _run_ytdlp(self, url, quality, out_dir, platform, vk_cookies):
        try:
            import yt_dlp
        except ImportError:
            raise RuntimeError("yt-dlp не установлен. Выполните: pip install yt-dlp")

        out_dir = Path(out_dir)
        # YouTube тоже иногда отвечает разовой ошибкой (429, «bot check»,
        # оборванный фрагмент) — вторая попытка через паузу обычно проходит.
        max_attempts = self._SC_ATTEMPTS if platform == "soundcloud" else 2

        before = self._snapshot(out_dir)   # что уже лежало в папке до запуска
        info_by_stem: dict = {}
        new_files: list = []
        ydl_errors: list = []
        progress_state = {"total": 0, "failed": []}
        ret = 0
        sc_ids: set = set()
        self._already = []

        for attempt in range(1, max_attempts + 1):
            if self._cancel_flag.is_set():
                break
            if attempt > 1:
                self.log.emit(f"Повторная попытка {attempt}/{max_attempts}…")
                self._sleep_cancellable(1.5 * (attempt - 1))
                if self._cancel_flag.is_set():
                    break

            ydl_errors = []                                   # ошибки ТЕКУЩЕЙ попытки
            progress_state = {"total": 0, "failed": []}
            opts = self._build_ytdlp_opts(yt_dlp, url, quality, out_dir, platform,
                                          vk_cookies, ydl_errors, progress_state,
                                          info_by_stem)

            with yt_dlp.YoutubeDL(opts) as ydl:
                if platform == "soundcloud":
                    self._prepare_soundcloud(ydl, force_refresh=(attempt > 1))
                    # Двухфазно: сначала только метаданные (здесь и всплывает
                    # протухший client_id — сразу обновляем его и повторяем),
                    # потом скачивание по уже готовому info. Раньше первая
                    # попытка молча возвращала «ничего» и успевала лишь прогреть
                    # кэш — поэтому срабатывал только второй клик.
                    info = None
                    for k in range(3):
                        try:
                            info = ydl.extract_info(url, download=False)
                        except Exception as e:            # noqa: BLE001
                            self.log.emit(f"SoundCloud: {e}")
                            info = None
                        if info:
                            break
                        self.log.emit("SoundCloud: обновляю ключ доступа и пробую снова…")
                        self._prepare_soundcloud(ydl, force_refresh=True)
                        self._sleep_cancellable(0.8)
                        if self._cancel_flag.is_set():
                            break
                    if info:
                        sc_ids.update(self._collect_ids(info))
                        ydl.process_ie_result(info, download=True)
                        ret = 0
                    else:
                        ydl_errors.append("SoundCloud не отдал данные трека")
                        ret = 1
                else:
                    ret = ydl.download([url])

            new_files = sorted(self._snapshot(out_dir) - before)

            # Трек уже был скачан раньше — yt-dlp его пропускает, новых файлов
            # нет. Находим его по [id] в имени и считаем успехом.
            if not new_files and sc_ids:
                self._already = [p for p in out_dir.glob("*") if p.suffix.lower() in AUDIO_EXTS
                                 and any(f"[{i}]" in p.name for i in sc_ids)]
                if self._already:
                    break

            # Успех: появились новые файлы. Для SoundCloud пустой результат —
            # тоже повод повторить (ошибка могла не попасть в лог).
            if new_files or (not ydl_errors and platform != "soundcloud"):
                break

        self._post_embed_covers(out_dir)
        self._post_write_metadata(out_dir, info_by_stem)

        if self._cancel_flag.is_set():
            return

        if not new_files and self._already:
            return                                          # уже лежал в папке — не провал
        # Полный провал: ничего не скачано и yt-dlp жаловался.
        if not new_files and (ydl_errors or platform == "soundcloud"):
            if not ydl_errors:
                ydl_errors.append("SoundCloud не отдал файл трека")
            hint = ""
            if platform == "vk":
                hint = (" VK Music часто блокирует доступ без авторизации — "
                        "попробуйте указать файл cookies в настройках загрузчика.")
            elif platform == "soundcloud":
                hint = (" Если ошибка повторяется — обновите yt-dlp: "
                        "pip install -U yt-dlp (SoundCloud периодически меняет защиту).")
            last = ydl_errors[-1].strip().splitlines()[0][:300]
            raise RuntimeError(f"Не удалось скачать: {last}.{hint}")

        if ret != 0:
            raise RuntimeError(f"yt-dlp вернул код {ret}.")

        # Частичный успех в альбоме/плейлисте (ignoreerrors=True скрывает его в ret).
        expected = progress_state["total"]
        if ydl_errors and expected and len(new_files) < expected:
            names = "; ".join(progress_state["failed"][:5])
            hint = ""
            if platform == "youtube":
                hint = (" Похоже, YouTube временно ограничил доступ (троттлинг). "
                        "Подождите несколько минут перед повторной попыткой.")
            raise RuntimeError(
                f"Скачано {len(new_files)} из {expected} треков.{hint}"
                + (f" Не удалось: {names}" if names else "")
            )

    def _post_embed_covers(self, out_dir: Path):
        for audio in list(out_dir.glob("*")):
            if audio.suffix.lower() not in AUDIO_EXTS:
                continue
            cover = _find_cover(audio.stem, out_dir)
            if cover is None:
                continue
            if cover.suffix.lower() == ".webp":
                jpg = _convert_webp_to_jpg(cover)
                if jpg:
                    cover.unlink(missing_ok=True)
                    cover = jpg
                else:
                    cover.unlink(missing_ok=True)
                    continue
            ok = _embed_cover_ffmpeg(audio, cover)
            if ok:
                self.log.emit(f"Обложка вшита: {audio.name}")
            cover.unlink(missing_ok=True)

        for pat in ("*.webp", "*.jpg", "*.jpeg", "*.png"):
            for thumb in out_dir.glob(pat):
                try:
                    thumb.unlink(missing_ok=True)
                except Exception:
                    pass

    def _post_write_metadata(self, out_dir: Path, info_by_stem: dict):
        if not info_by_stem:
            return
        try:
            import mutagen
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError
            from mutagen.flac import FLAC
            from mutagen.mp4 import MP4
            from mutagen.oggvorbis import OggVorbis
        except ImportError:
            self.log.emit("mutagen не установлен — метаданные не записаны.")
            return

        for audio in list(out_dir.glob("*")):
            if audio.suffix.lower() not in AUDIO_EXTS:
                continue
            info = info_by_stem.get(audio.stem)
            if not info:
                continue
            title = str(info.get("title") or "").strip()
            artist = str(info.get("artist") or info.get("uploader") or "").strip()
            album = str(info.get("album") or "").strip()
            if not (title or artist or album):
                continue
            ext = audio.suffix.lower()
            try:
                if ext == ".mp3":
                    try:
                        tags = EasyID3(audio)
                    except ID3NoHeaderError:
                        tags = mutagen.File(audio, easy=True)
                        tags.add_tags()
                    if title:
                        tags["title"] = title
                    if artist:
                        tags["artist"] = artist
                    if album:
                        tags["album"] = album
                    tags.save()
                elif ext == ".flac":
                    f = FLAC(audio)
                    if title:
                        f["title"] = title
                    if artist:
                        f["artist"] = artist
                    if album:
                        f["album"] = album
                    f.save()
                elif ext in (".ogg", ".opus"):
                    o = OggVorbis(audio)
                    if title:
                        o["title"] = title
                    if artist:
                        o["artist"] = artist
                    if album:
                        o["album"] = album
                    o.save()
                elif ext in (".m4a", ".mp4"):
                    m = MP4(audio)
                    if title:
                        m["\xa9nam"] = [title]
                    if artist:
                        m["\xa9ART"] = [artist]
                    if album:
                        m["\xa9alb"] = [album]
                    m.save()
            except Exception as e:
                self.log.emit(f"Не удалось записать метаданные для {audio.name}: {e}")

    # ------------------------------------------------------------------ #
    #  spotdl: Spotify                                                     #
    # ------------------------------------------------------------------ #

    def _run_spotdl(self, url, quality, out_dir, before,
                     spotify_client_id="", spotify_client_secret=""):
        try:
            import spotdl  # noqa: F401
        except ImportError:
            raise RuntimeError("spotdl не установлен. Выполните: pip install spotdl")

        # Ключи Spotify API
        DEFAULT_CLIENT_ID = "e18d17324eba4d3bbbfe0b837617d208"
        DEFAULT_CLIENT_SECRET = "f45cc54724f34fbcb72f68ca15d0c9f3"

        client_id = spotify_client_id.strip() or DEFAULT_CLIENT_ID
        client_secret = spotify_client_secret.strip() or DEFAULT_CLIENT_SECRET

        fmt = "flac" if quality == "flac" else "mp3"
        out_template = str(out_dir / "{artist} - {title}.{output-ext}")

        # Формируем команду с вызовом 'download', ключами API и флагом официального API
        cmd = [
            sys.executable, "-m", "spotdl", "download", url,
            "--output", out_template,
            "--format", fmt,
            "--client-id", client_id,
            "--client-secret", client_secret,
            "--use-official-api"
        ]

        if fmt == "mp3":
            cmd += ["--bitrate", "320k"]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        total_guess = 1
        done_guess = 0
        seen = set(before)
        auth_error_seen = False

        try:
            for line in proc.stdout:
                if self._cancel_flag.is_set():
                    proc.terminate()
                    break
                line = line.strip()
                if not line:
                    continue
                self.log.emit(line)

                if "Could not get session auth tokens" in line or "BaseClientError" in line:
                    auth_error_seen = True

                m = re.search(r"[Ff]ound (\d+) songs?", line)
                if m:
                    total_guess = max(1, int(m.group(1)))

                if re.match(r'^(Downloaded|Download Completed)', line, re.IGNORECASE):
                    done_guess += 1
                    pct = min(100.0, done_guess / total_guess * 100.0)
                    self.progress.emit(done_guess, total_guess, line, pct, 0.0)

                now = self._snapshot(out_dir)
                for f in now - seen:
                    self.item_finished.emit(str(f))
                seen = now
        finally:
            proc.wait()

        if proc.returncode not in (0, None) and not self._cancel_flag.is_set():
            if auth_error_seen:
                raise RuntimeError(
                    "Ошибка авторизации Spotify API. "
                    "Убедитесь, что указаны верные Client ID и Client Secret."
                )
            raise RuntimeError(
                f"spotdl завершился с ошибкой (код {proc.returncode}). "
                f"Проверьте правильность ссылки и доступность трека на YouTube Music."
            )
