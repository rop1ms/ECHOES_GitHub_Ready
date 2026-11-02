# playlist_share.py
"""
Обмен плейлистами и темами файлами.

Плейлист → маленький текстовый файл «.echoesplaylist» (JSON): только список треков (исполнитель,
название, альбом, длительность) — без музыки, несколько килобайт. Друг открывает его в своём
ECHOES: треки, которые у него уже есть, просто попадают в плейлист, остальные сами находятся
на YouTube Music / YouTube / SoundCloud и скачиваются. Spotify не нужен (из России он отдаёт
списки плохо или не отдаёт вовсе).

Понимает и чужие списки: .m3u / .m3u8 (#EXTINF) и обычный .txt («Исполнитель - Название»
в каждой строке).

Как открыть файл (тему .echoestheme или плейлист):
  * перетащить файл на окно плеера;
  * кнопка «＋ Новый плейлист» → «Из файла (от друга)…» (тема — конструктор → «Сохранить» → «Импорт…»);
  * положить файл в папку «Импорт» (~/.neon_player/import) — плеер заберёт его сам при запуске
    и когда окно снова становится активным.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QDialog

PLAYLIST_EXT = ".echoesplaylist"
THEME_EXT = ".echoestheme"
LIST_EXTS = (PLAYLIST_EXT, ".m3u", ".m3u8", ".txt")
INBOX = Path.home() / ".neon_player" / "import"
FORMAT = "echoes-playlist"
FORMAT_ALL = "echoes-playlists"     # все плейлисты одним файлом — для ECHOES на телефоне


# ------------------------------------------------------------------ #
#  Формат                                                             #
# ------------------------------------------------------------------ #

def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", s or "").strip()[:80] or "playlist"


_REF = re.compile(r"\[([A-Za-z0-9_-]{11}|\d{4,})\]\.\w+$")


def _track_ref(t: dict) -> str:
    """Откуда трек: «yt:<id видео>» или «sc:<id трека SoundCloud>» — из имени скачанного файла
    («Исполнитель - Название [id].mp3»). Телефон по нему стримит именно эту запись. Пусто — неизвестно."""
    m = _REF.search(str(t.get("path") or ""))
    if not m:
        return ""
    v = m.group(1)
    return f"sc:{v}" if v.isdigit() else f"yt:{v}"


def playlist_payload(win, name: str) -> dict:
    pl = win.playlists.get(name) or {}
    tracks = []
    for t in pl.get("tracks", []):
        title = (t.get("title") or Path(str(t.get("path", ""))).stem or "").strip()
        if not title:
            continue
        item = {"artist": (t.get("artist") or "").strip(), "title": title,
                "album": (t.get("album") or "").strip(), "duration": round(float(t.get("duration") or 0), 1)}
        ref = _track_ref(t)
        if ref:
            item["id"] = ref
        tracks.append(item)
    who = (getattr(win, "profile", {}) or {}).get("nickname") or ""
    return {"format": FORMAT, "version": 1, "name": name, "description": pl.get("desc") or "",
            "by": who, "created": time.strftime("%Y-%m-%d"), "count": len(tracks), "tracks": tracks}


def _parse_line(s: str) -> dict | None:
    s = s.strip().lstrip("﻿")
    if not s or s.startswith("#"):
        return None
    dur = 0.0
    m = re.search(r"[\(\[]\s*(\d{1,2}):(\d{2})\s*[\)\]]\s*$", s)
    if m:
        dur = int(m.group(1)) * 60 + int(m.group(2))
        s = s[:m.start()].strip()
    s = re.sub(r"^\d+[.)]\s+", "", s)                    # «12. Исполнитель - Название»
    for sep in (" — ", " – ", " - "):
        if sep in s:
            a, t = s.split(sep, 1)
            return {"artist": a.strip(), "title": t.strip(), "album": "", "duration": dur}
    return {"artist": "", "title": s, "album": "", "duration": dur}


def read_playlist_file(path: str) -> tuple[str, str, list[dict]]:
    """→ (название, описание, треки). Бросает ValueError, если это не список треков."""
    p = Path(path)
    raw = p.read_text("utf-8", errors="replace")
    ext = p.suffix.lower()
    if ext == PLAYLIST_EXT or raw.lstrip().startswith("{"):
        d = json.loads(raw)
        if d.get("format") == FORMAT_ALL:
            raise ValueError("это файл для ECHOES на телефоне (все плейлисты сразу) — откройте его в приложении на iPhone")
        if d.get("format") != FORMAT or not isinstance(d.get("tracks"), list):
            raise ValueError("это не плейлист ECHOES")
        out = []
        for t in d["tracks"][:5000]:
            if isinstance(t, dict) and str(t.get("title") or "").strip():
                out.append({"artist": str(t.get("artist") or "")[:200], "title": str(t["title"])[:300],
                            "album": str(t.get("album") or "")[:200],
                            "duration": float(t.get("duration") or 0)})
        return str(d.get("name") or p.stem)[:80], str(d.get("description") or "")[:500], out
    out = []
    if ext in (".m3u", ".m3u8"):
        pending = None
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("#EXTINF"):
                body = line.split(":", 1)[1] if ":" in line else ""
                dur, _, rest = body.partition(",")
                pending = _parse_line(rest) or None
                if pending is not None:
                    try:
                        pending["duration"] = max(0.0, float(dur))
                    except ValueError:
                        pass
            elif line and not line.startswith("#"):
                t = pending or _parse_line(Path(line.replace("\\", "/")).stem)
                if t:
                    out.append(t)
                pending = None
    else:
        out = [t for t in (_parse_line(x) for x in raw.splitlines()[:5000]) if t]
    if not out:
        raise ValueError("в файле не нашлось ни одного трека")
    return p.stem[:80], "", out


# ------------------------------------------------------------------ #
#  Экспорт / импорт                                                   #
# ------------------------------------------------------------------ #

def _toast(win, text):
    fn = getattr(win, "_toast", None)
    if fn is not None:
        try:
            fn(text)
            return
        except Exception:                                  # noqa: BLE001
            pass
    print("[share]", text)


def export_playlist(win, name: str, parent=None):
    if name not in getattr(win, "playlists", {}):
        return
    start = str(Path.home() / "Desktop" / (_safe_name(name) + PLAYLIST_EXT))
    path, _ = QFileDialog.getSaveFileName(parent or win, "Поделиться плейлистом", start,
                                          f"Плейлист ECHOES (*{PLAYLIST_EXT})")
    if not path:
        return
    if not path.lower().endswith(PLAYLIST_EXT):
        path += PLAYLIST_EXT
    data = playlist_payload(win, name)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    kb = max(1, Path(path).stat().st_size // 1024)
    _toast(win, f"Готово: {Path(path).name} ({kb} КБ, {data['count']} треков). Отправьте файл другу — "
                f"пусть перетащит его на окно ECHOES")


def export_all_for_phone(win, parent=None):
    """Все плейлисты одним файлом — открыть в ECHOES на iPhone («Моя музыка» → «Импорт с ПК»)."""
    names = [n for n in getattr(win, "playlists", {}) if (win.playlists.get(n) or {}).get("tracks")]
    if not names:
        _toast(win, "Плейлистов с треками нет")
        return
    start = str(Path.home() / "Desktop" / ("ECHOES-плейлисты" + PLAYLIST_EXT))
    path, _ = QFileDialog.getSaveFileName(parent or win, "Перенести плейлисты на телефон", start,
                                          f"Плейлисты ECHOES (*{PLAYLIST_EXT})")
    if not path:
        return
    if not path.lower().endswith(PLAYLIST_EXT):
        path += PLAYLIST_EXT
    lists = [playlist_payload(win, n) for n in names]
    data = {"format": FORMAT_ALL, "version": 1, "created": time.strftime("%Y-%m-%d"), "playlists": lists}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    kb = max(1, Path(path).stat().st_size // 1024)
    total = sum(p["count"] for p in lists)
    QMessageBox.information(
        parent or win, "Перенос на телефон",
        f"Сохранено: {Path(path).name} — {len(lists)} плейлистов, {total} треков ({kb} КБ).\n\n"
        "1. Отправьте файл себе (Telegram «Избранное», iCloud Drive или почта).\n"
        "2. На iPhone: откройте файл → «Поделиться» → «Сохранить в Файлы».\n"
        "3. В ECHOES на телефоне: «Моя музыка» → «Импорт с ПК» → выберите файл.\n\n"
        "Музыка не копируется — телефон сам находит треки онлайн.")


def import_playlist_file(win, path: str | None = None, parent=None):
    if not path:
        path, _ = QFileDialog.getOpenFileName(parent or win, "Открыть файл плейлиста", str(Path.home()),
                                              f"Плейлисты (*{PLAYLIST_EXT} *.m3u *.m3u8 *.txt);;Все файлы (*)")
        if not path:
            return
    try:
        name, desc, tracks = read_playlist_file(path)
    except Exception as e:                                 # noqa: BLE001
        QMessageBox.warning(parent or win, "Плейлист", f"Не получилось прочитать файл:\n{e}")
        return
    dlg = PlaylistFileDialog(win, name, desc, tracks, Path(path).name)
    try:
        import inapp
        inapp.present(win, dlg, "Плейлист из файла")
    except Exception:                                      # noqa: BLE001
        dlg.show()


def import_theme_file(win, path: str):
    try:
        t = win.theme_store.import_package(path)
    except Exception as e:                                 # noqa: BLE001
        QMessageBox.warning(win, "Тема", f"Это не тема ECHOES или файл повреждён:\n{e}")
        return
    try:
        win.custom_themes_changed()
        from themes import THEMES
        key = next((k for k, d in THEMES.items() if d.get("custom") and (d.get("ct") or {}).get("id") == t.get("id")),
                   None)
        if key:
            QTimer.singleShot(0, lambda: win._apply_theme(key))
        _toast(win, f"Тема «{t.get('name', '')}» добавлена в «Мои темы»")
    except Exception as e:                                 # noqa: BLE001
        print("[share] theme:", e)


def open_any(win, path: str) -> bool:
    ext = Path(path).suffix.lower()
    if ext == THEME_EXT:
        import_theme_file(win, path)
        return True
    if ext in LIST_EXTS:
        import_playlist_file(win, path)
        return True
    return False


try:
    from spotify_import import SpotifyImportDialog as _Base
except Exception:                                          # noqa: BLE001
    _Base = QDialog


class PlaylistFileDialog(_Base):
    """Как «Импорт из Spotify», только список треков — из файла (сеть Spotify не нужна).
    Поиск строже: берутся только уверенные совпадения, сомнительные не качаются наугад."""

    MIN_SCORE = 0.72

    def __init__(self, win, name, desc, tracks, fname):
        super().__init__(win)
        self.setWindowTitle("Плейлист из файла")
        for w in (getattr(self, "url", None), getattr(self, "btn_fetch", None)):
            if w is not None:
                w.hide()
        for lb in self.findChildren(QLabel):
            if lb.objectName() == "Sub":
                lb.setText(f"Файл «{fname}»: {len(tracks)} треков. Треки, которые уже есть у вас, сразу попадут в "
                           "плейлист, остальные будут найдены на YouTube Music / YouTube / SoundCloud и скачаны. "
                           "Берутся только точные совпадения (исполнитель, название и длительность).")
                break
        self._desc = desc
        self._on_fetched(name, tracks)

    def _on_matched(self, i, res, state):
        if res is not None and state != "have" and res.get("score") is not None \
                and float(res.get("score", 1)) < self.MIN_SCORE:
            # сомнительное совпадение — не качаем чужой трек. Если длительность известна, пусть поиск
            # попробует сам: скачанный файл не той длины загрузчик отбросит
            t = self.tracks[i]
            if float(t.get("duration") or 0) > 0:
                q = f"{t.get('artist', '')} - {t['title']}".strip(" -").replace(":", " ")
                res, state = {"url": f"ytsearch1:{q} audio", "source": "yt", "alts": []}, "yt"
            else:
                res, state = None, "none"
        super()._on_matched(i, res, state)
        pl = self.win.playlists.get(self.pl_name.text().strip())
        if pl is not None and pl.get("desc") in ("", None, "Импорт из Spotify"):
            pl["desc"] = self._desc or "Из файла плейлиста"


# ------------------------------------------------------------------ #
#  Справка «куда класть файлы»                                         #
# ------------------------------------------------------------------ #

class FilesHelpDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Файлы тем и плейлистов")
        self.setMinimumWidth(560)
        self.setStyleSheet("QDialog { background: #15151b; } QLabel { color: #ececf2; background: transparent; "
                           "font-size: 13px; } QPushButton { background: #24242c; color: #f0f0f5; border: 1px solid "
                           "#3a3a46; border-radius: 12px; padding: 6px 14px; } QPushButton:hover { border-color: "
                           "#9aa0ff; } QPushButton#AccentBtn { background: #ffffff; color: #111; font-weight: 700; }")
        v = QVBoxLayout(self)
        v.setSpacing(10)
        try:
            themes_dir = str(win.theme_store.root)
        except Exception:                                  # noqa: BLE001
            themes_dir = str(Path.home() / ".neon_player" / "themes")
        INBOX.mkdir(parents=True, exist_ok=True)
        txt = (
            "<h3>Как открыть чужую тему или плейлист</h3>"
            "<p><b>1. Перетащите файл на окно ECHOES</b> — проще всего. Тема (<b>.echoestheme</b>) сразу "
            "включится, плейлист (<b>.echoesplaylist</b>, <b>.m3u</b>) откроется окном переноса.</p>"
            "<p><b>2. Или положите файл в папку «Импорт»</b> (кнопка ниже) — плеер заберёт его сам при запуске и "
            "когда вы вернётесь в окно. Обработанные файлы переезжают в «Импорт/готово».</p>"
            "<p><b>3. Или кнопками:</b> плейлист — «＋ Новый плейлист» → «Из файла (от друга)…» (подойдёт и "
            "<b>.txt</b> со строками «Исполнитель - Название»); тема — конструктор тем → страница «Сохранить» → "
            "«Импорт…».</p>"
            "<h3>Как поделиться</h3>"
            "<p><b>Плейлист:</b> правый клик по плейлисту → «Поделиться файлом…». Получится маленький файл "
            "(несколько КБ) — в нём только названия треков, без музыки. У друга недостающие треки скачаются "
            "сами, без Spotify.</p>"
            "<p><b>Тема:</b> конструктор тем → страница «Сохранить» → «Экспорт в файл…» — один файл "
            "<b>.echoestheme</b> со всеми картинками, шрифтами и скриптами.</p>"
            f"<p style='color:#888'>Папка «Импорт»: {INBOX}<br>Ваши темы хранятся тут: {themes_dir}</p>")
        lb = QLabel(txt)
        lb.setWordWrap(True)
        lb.setTextFormat(Qt.TextFormat.RichText)
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(lb)
        row = QHBoxLayout()
        for text, fn in (("Открыть папку «Импорт»", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(INBOX)))),
                         ("Открыть файл…", self._open_file),
                         ("Папка с темами", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(themes_dir)))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        ok = QPushButton("Понятно")
        ok.setObjectName("AccentBtn")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        v.addLayout(row)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Тема или плейлист", str(Path.home()),
                                              f"Темы и плейлисты (*{THEME_EXT} *{PLAYLIST_EXT} *.m3u *.m3u8 *.txt)")
        if path:
            self.accept()
            open_any(self.win, path)


def show_help(win):
    dlg = FilesHelpDialog(win)
    try:
        import inapp
        inapp.present(win, dlg, "Файлы тем и плейлистов")
    except Exception:                                      # noqa: BLE001
        dlg.show()


# ------------------------------------------------------------------ #
#  Перетаскивание на окно и папка «Импорт»                             #
# ------------------------------------------------------------------ #

class _Drops(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._scan_t = 0.0

    @staticmethod
    def _files(e):
        md = e.mimeData()
        if md is None or not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()
                and Path(u.toLocalFile()).suffix.lower() in (THEME_EXT, PLAYLIST_EXT, ".m3u", ".m3u8")]

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self._files(e):
                e.acceptProposedAction()
                return True
        elif t == QEvent.Type.Drop:
            files = self._files(e)
            if files:
                e.acceptProposedAction()
                for f in files:
                    QTimer.singleShot(0, lambda f=f: open_any(self.win, f))
                return True
        elif t == QEvent.Type.WindowActivate and obj is self.win:
            if time.monotonic() - self._scan_t > 4:
                self._scan_t = time.monotonic()
                QTimer.singleShot(300, lambda: scan_inbox(self.win))
        return False


def scan_inbox(win):
    """Файлы из папки «Импорт»: открыть и переложить в «Импорт/готово»."""
    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        files = [p for p in sorted(INBOX.iterdir()) if p.is_file()
                 and p.suffix.lower() in (THEME_EXT, PLAYLIST_EXT, ".m3u", ".m3u8")]
    except OSError:
        return
    if not files:
        return
    done = INBOX / "готово"
    done.mkdir(exist_ok=True)
    for p in files:
        dst = done / p.name
        if dst.exists():
            dst = done / f"{p.stem}_{int(time.time())}{p.suffix}"
        try:
            shutil.move(str(p), str(dst))
        except OSError:
            continue
        open_any(win, str(dst))


def install(win):
    if win.__dict__.get("_share") is not None:
        return
    INBOX.mkdir(parents=True, exist_ok=True)
    f = _Drops(win)
    win.setAcceptDrops(True)
    win.installEventFilter(f)
    win._share = f
    win.share_playlist = lambda name, parent=None: export_playlist(win, name, parent)
    win.export_playlists_for_phone = lambda parent=None: export_all_for_phone(win, parent)
    win.import_playlist_file = lambda path=None, parent=None: import_playlist_file(win, path, parent)
    win.files_help = lambda: show_help(win)
    QTimer.singleShot(2500, lambda: scan_inbox(win))
