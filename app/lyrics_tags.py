# lyrics_tags.py
"""
Чтение/запись текста песни туда, где его может найти сам плеер:
локальный .lrc-файл рядом с треком, либо прямо в тег аудиофайла
(USLT для MP3/WAV, Vorbis-comment для FLAC/OGG, ©lyr для MP4/M4A).

Используется:
  * lyrics_editor.py — сохраняет то, что написал/синхронизировал пользователь
  * main.py:_load_lyrics — проверяет локальный источник ПЕРЕД сетевым поиском
    (LRCLIB/lyrics.ovh/Genius), чтобы то, что уже записано вручную, не
    перезатиралось и не требовало интернета при каждом воспроизведении
"""

from pathlib import Path

try:
    from mutagen.id3 import ID3, USLT, ID3NoHeaderError
except ImportError:
    ID3 = USLT = ID3NoHeaderError = None

try:
    from mutagen.flac import FLAC
except ImportError:
    FLAC = None

try:
    from mutagen.oggvorbis import OggVorbis
    from mutagen.oggopus import OggOpus
except ImportError:
    OggVorbis = OggOpus = None

try:
    from mutagen.mp4 import MP4
except ImportError:
    MP4 = None

try:
    from mutagen.wave import WAVE
except ImportError:
    WAVE = None

VORBIS_KEY = "LYRICS"

# ── Обложка ──────────────────────────────────────────────────────────── #

def read_cover(audio_path: str) -> bytes | None:
    """Читает встроенную обложку из тега. Возвращает сырые байты или None."""
    ext = Path(audio_path).suffix.lower()
    try:
        if ext in (".mp3", ".wav") and ID3:
            try:
                tags = ID3(audio_path)
            except (ID3NoHeaderError, Exception):
                return None
            pics = tags.getall("APIC")
            return pics[0].data if pics else None
        if ext == ".flac" and FLAC:
            f = FLAC(audio_path)
            return f.pictures[0].data if f.pictures else None
        if ext in (".ogg",) and OggVorbis:
            # Vorbis METADATA_BLOCK_PICTURE — base64 в теге
            import base64, struct
            o = OggVorbis(audio_path)
            raw = o.get("METADATA_BLOCK_PICTURE")
            if raw:
                data = base64.b64decode(raw[0])
                mime_len = struct.unpack(">I", data[4:8])[0]
                desc_offset = 8 + mime_len
                desc_len = struct.unpack(">I", data[desc_offset:desc_offset+4])[0]
                img_offset = desc_offset + 4 + desc_len + 16 + 4
                img_len = struct.unpack(">I", data[img_offset:img_offset+4])[0]
                return data[img_offset+4:img_offset+4+img_len]
        if ext in (".m4a", ".mp4") and MP4:
            m = MP4(audio_path)
            covr = m.tags.get("covr") if m.tags else None
            return bytes(covr[0]) if covr else None
    except Exception as e:
        print("[lyrics_tags] cover read error:", e)
    return None


def write_cover(audio_path: str, image_bytes: bytes, mime: str = "image/jpeg") -> bool:
    """Записывает обложку в тег аудиофайла. mime — 'image/jpeg' или 'image/png'."""
    ext = Path(audio_path).suffix.lower()
    try:
        if ext == ".mp3" and ID3:
            from mutagen.id3 import APIC
            try:
                tags = ID3(audio_path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
            tags.save(audio_path)
            return True
        if ext == ".wav" and WAVE:
            from mutagen.id3 import APIC
            w = WAVE(audio_path)
            if w.tags is None:
                w.add_tags()
            w.tags.delall("APIC")
            w.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
            w.save()
            return True
        if ext == ".flac" and FLAC:
            from mutagen.flac import Picture
            f = FLAC(audio_path)
            f.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.desc = "Cover"
            pic.data = image_bytes
            f.add_picture(pic)
            f.save()
            return True
        if ext == ".ogg" and OggVorbis:
            import base64, struct
            from mutagen.flac import Picture
            o = OggVorbis(audio_path)
            pic = Picture()
            pic.type = 3; pic.mime = mime; pic.desc = "Cover"; pic.data = image_bytes
            o["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
            o.save()
            return True
        if ext in (".m4a", ".mp4") and MP4:
            from mutagen.mp4 import MP4Cover
            fmt = MP4Cover.FORMAT_PNG if "png" in mime else MP4Cover.FORMAT_JPEG
            m = MP4(audio_path)
            m.tags["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
            m.save()
            return True
    except Exception as e:
        print("[lyrics_tags] cover write error:", e)
    return False


# ── Метаданные (title / artist / album / genre) ───────────────────────── #

def read_metadata(audio_path: str) -> dict:
    """Возвращает {'title','artist','album','genre'} из тегов файла."""
    ext = Path(audio_path).suffix.lower()
    result = {"title": "", "artist": "", "album": "", "genre": ""}
    try:
        if ext in (".mp3", ".wav") and ID3:
            try:
                tags = ID3(audio_path)
            except (ID3NoHeaderError, Exception):
                return result
            def _id3(key):
                v = tags.get(key)
                return str(v.text[0]) if v and v.text else ""
            result["title"] = _id3("TIT2")
            result["artist"] = _id3("TPE1")
            result["album"] = _id3("TALB")
            result["genre"] = _id3("TCON")
        elif ext == ".flac" and FLAC:
            f = FLAC(audio_path)
            def _vc(key):
                v = f.get(key.upper()) or f.get(key.lower())
                return v[0] if v else ""
            result.update(title=_vc("title"), artist=_vc("artist"),
                          album=_vc("album"), genre=_vc("genre"))
        elif ext == ".ogg" and OggVorbis:
            o = OggVorbis(audio_path)
            def _vc(key):
                v = o.get(key.lower())
                return v[0] if v else ""
            result.update(title=_vc("title"), artist=_vc("artist"),
                          album=_vc("album"), genre=_vc("genre"))
        elif ext == ".opus" and OggOpus:
            o = OggOpus(audio_path)
            def _vc(key):
                v = o.get(key.lower())
                return v[0] if v else ""
            result.update(title=_vc("title"), artist=_vc("artist"),
                          album=_vc("album"), genre=_vc("genre"))
        elif ext in (".m4a", ".mp4") and MP4:
            m = MP4(audio_path)
            def _m4(key):
                v = m.tags.get(key) if m.tags else None
                return str(v[0]) if v else ""
            result.update(title=_m4("\xa9nam"), artist=_m4("\xa9ART"),
                          album=_m4("\xa9alb"), genre=_m4("\xa9gen"))
    except Exception as e:
        print("[lyrics_tags] metadata read error:", e)
    return result


def write_metadata(audio_path: str, title: str = "", artist: str = "",
                   album: str = "", genre: str = "") -> bool:
    """Записывает title/artist/album/genre в тег файла. Пустая строка = не трогать."""
    ext = Path(audio_path).suffix.lower()
    try:
        if ext == ".mp3" and ID3:
            from mutagen.id3 import TIT2, TPE1, TALB, TCON
            try:
                tags = ID3(audio_path)
            except ID3NoHeaderError:
                tags = ID3()
            if title:  tags["TIT2"] = TIT2(encoding=3, text=title)
            if artist: tags["TPE1"] = TPE1(encoding=3, text=artist)
            if album:  tags["TALB"] = TALB(encoding=3, text=album)
            if genre:  tags["TCON"] = TCON(encoding=3, text=genre)
            tags.save(audio_path)
            return True
        if ext == ".wav" and WAVE:
            from mutagen.id3 import TIT2, TPE1, TALB, TCON
            w = WAVE(audio_path)
            if w.tags is None:
                w.add_tags()
            if title:  w.tags["TIT2"] = TIT2(encoding=3, text=title)
            if artist: w.tags["TPE1"] = TPE1(encoding=3, text=artist)
            if album:  w.tags["TALB"] = TALB(encoding=3, text=album)
            if genre:  w.tags["TCON"] = TCON(encoding=3, text=genre)
            w.save()
            return True
        if ext == ".flac" and FLAC:
            f = FLAC(audio_path)
            if title:  f["title"] = title
            if artist: f["artist"] = artist
            if album:  f["album"] = album
            if genre:  f["genre"] = genre
            f.save()
            return True
        if ext in (".ogg",) and OggVorbis:
            o = OggVorbis(audio_path)
            if title:  o["title"] = title
            if artist: o["artist"] = artist
            if album:  o["album"] = album
            if genre:  o["genre"] = genre
            o.save()
            return True
        if ext == ".opus" and OggOpus:
            o = OggOpus(audio_path)
            if title:  o["title"] = title
            if artist: o["artist"] = artist
            if album:  o["album"] = album
            if genre:  o["genre"] = genre
            o.save()
            return True
        if ext in (".m4a", ".mp4") and MP4:
            m = MP4(audio_path)
            if m.tags is None:
                m.add_tags()
            if title:  m.tags["\xa9nam"] = [title]
            if artist: m.tags["\xa9ART"] = [artist]
            if album:  m.tags["\xa9alb"] = [album]
            if genre:  m.tags["\xa9gen"] = [genre]
            m.save()
            return True
    except Exception as e:
        print("[lyrics_tags] metadata write error:", e)
    return False


def lrc_path_for(audio_path: str) -> Path:
    """Путь к .lrc-файлу, который лежит рядом с треком (то же имя, .lrc)."""
    p = Path(audio_path)
    return p.with_suffix(".lrc")


def read_local_lyrics(audio_path: str) -> str | None:
    """
    Ищет текст в порядке приоритета:
      1. <track>.lrc рядом с файлом (что бы пользователь ни сохранил — LRC
         с таймкодами или обычный текст — приоритет за явным .lrc-файлом)
      2. Тег в самом аудиофайле (USLT / Vorbis LYRICS / ©lyr)
    Возвращает None, если ничего не найдено — тогда main.py идёт в сеть.
    """
    if not audio_path:
        return None
    lrc = lrc_path_for(audio_path)
    if lrc.exists():
        try:
            text = lrc.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception as e:
            print("[lyrics_tags] LRC read error:", e)

    return _read_tag(audio_path)


def _read_tag(audio_path: str) -> str | None:
    ext = Path(audio_path).suffix.lower()
    try:
        if ext == ".mp3" and ID3:
            tags = ID3(audio_path)
            frames = tags.getall("USLT")
            if frames:
                return frames[0].text or None
        elif ext == ".wav" and WAVE:
            w = WAVE(audio_path)
            if w.tags:
                frames = w.tags.getall("USLT")
                if frames:
                    return frames[0].text or None
        elif ext == ".flac" and FLAC:
            f = FLAC(audio_path)
            if VORBIS_KEY in f:
                return "\n".join(f[VORBIS_KEY]) or None
        elif ext == ".ogg" and OggVorbis:
            o = OggVorbis(audio_path)
            if VORBIS_KEY in o:
                return "\n".join(o[VORBIS_KEY]) or None
        elif ext == ".opus" and OggOpus:
            o = OggOpus(audio_path)
            if VORBIS_KEY in o:
                return "\n".join(o[VORBIS_KEY]) or None
        elif ext in (".m4a", ".mp4") and MP4:
            m = MP4(audio_path)
            if "\xa9lyr" in m:
                vals = m["\xa9lyr"]
                return "\n".join(vals) if vals else None
    except Exception as e:
        print("[lyrics_tags] tag read error:", e)
    return None


def write_lyrics_lrc(audio_path: str, text: str) -> Path:
    """Сохраняет текст (с таймкодами или без) в <track>.lrc рядом с файлом."""
    path = lrc_path_for(audio_path)
    path.write_text(text, encoding="utf-8")
    return path


def write_lyrics_tag(audio_path: str, text: str) -> bool:
    """
    Записывает текст прямо в тег аудиофайла:
      MP3/WAV → ID3 USLT-фрейм
      FLAC/OGG/Opus → Vorbis comment "LYRICS"
      M4A/MP4 → атом ©lyr
    Возвращает True при успехе.
    """
    ext = Path(audio_path).suffix.lower()
    try:
        if ext == ".mp3" and ID3:
            try:
                tags = ID3(audio_path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="rus", desc="", text=text))
            tags.save(audio_path)
            return True
        if ext == ".wav" and WAVE:
            w = WAVE(audio_path)
            if w.tags is None:
                w.add_tags()
            w.tags.delall("USLT")
            w.tags.add(USLT(encoding=3, lang="rus", desc="", text=text))
            w.save()
            return True
        if ext == ".flac" and FLAC:
            f = FLAC(audio_path)
            f[VORBIS_KEY] = text
            f.save()
            return True
        if ext == ".ogg" and OggVorbis:
            o = OggVorbis(audio_path)
            o[VORBIS_KEY] = text
            o.save()
            return True
        if ext == ".opus" and OggOpus:
            o = OggOpus(audio_path)
            o[VORBIS_KEY] = text
            o.save()
            return True
        if ext in (".m4a", ".mp4") and MP4:
            m = MP4(audio_path)
            m["\xa9lyr"] = [text]
            m.save()
            return True
    except Exception as e:
        print("[lyrics_tags] tag write error:", e)
        return False
    return False
