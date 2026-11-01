# tag_all.py
"""
«Найти теги для всей музыки» (Настройки → Медиатека).

Проходит по всем скачанным трекам коллекции и плейлистов: у кого в файле нет исполнителя, альбома, жанра
или обложки — ищет трек на Deezer / iTunes / SoundCloud (auto_tags.lookup, совпадение ≥ 75 %) и
дописывает В ФАЙЛ только пустые поля. То, что вы вписали сами, не трогается.

Работа идёт в фоне (3 трека одновременно): окно можно закрыть — поиск продолжится, в конце будет
уведомление; повторное нажатие кнопки покажет прогресс. «Остановить» — прервать.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QProgressBar, QPushButton, QVBoxLayout

from cleanup import QSS, guess_query

WORKERS = 3
_JOB = None                                                # идущий поиск (один на всё приложение)


class TagJob:
    def __init__(self, win, tracks):
        self.win = win
        self.tracks = tracks
        self.total = len(tracks)
        self.done = self.updated = self.complete = self.notfound = 0
        self.log = []                                      # последние записанные: «Название — что дописано»
        self.current = ""
        self.cancel = False
        self.finished = False
        self._lock = threading.Lock()
        self._pending = 0                                  # результатов без сохранения медиатеки

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="tag-all").start()

    def _run(self):
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(self._one, self.tracks))
        self.finished = True
        self._ui(self._finish)

    def _one(self, t):
        if self.cancel:
            return
        path = str(t.get("path") or "")
        self.current = str(t.get("title") or Path(path).stem)
        res, status = None, "complete"
        try:
            import auto_tags
            miss = auto_tags.needs_tags(path, t, full=True)
            if miss:
                from lyrics_tags import read_metadata
                meta = read_metadata(path)
                q = dict(t)
                if (meta.get("artist") or "").strip().lower() not in auto_tags.UNKNOWN_ARTISTS:
                    q["artist"] = meta["artist"]
                if (meta.get("title") or "").strip():
                    q["title"] = meta["title"]
                title, artist = guess_query(q)
                try:
                    dur = float(t.get("duration") or 0)
                except (TypeError, ValueError):
                    dur = 0.0
                plan = auto_tags.plan_missing(path, artist, title, dur, miss)
                if plan and self._in_use(path):         # играет — запишется, когда трек сменится
                    self._ui(lambda: self.win._autotag_got(path, plan))
                    res = None
                    status = "updated"
                else:
                    res = auto_tags.apply_plan(path, plan)
                    status = "updated" if res else "notfound"
        except Exception as e:                             # noqa: BLE001
            print("[tag_all]", path, e)
            status = "notfound"
        with self._lock:
            self.done += 1
            if status == "updated":
                self.updated += 1
            elif status == "notfound":
                self.notfound += 1
            else:
                self.complete += 1
        if res:
            self._ui(lambda: self._apply(path, res))

    def _in_use(self, path):
        try:
            return bool(self.win._file_in_use(path))
        except Exception:                                  # noqa: BLE001
            return True

    def _ui(self, fn):
        try:
            self.win._ui_bridge.call.emit(fn)
        except RuntimeError:
            pass

    def _apply(self, path, res):
        """GUI-поток: дописанное — в записи коллекции и плейлистов (без перерисовки на каждый трек)."""
        meta = {"path": path, "cover_changed": bool(res.get("cover_changed"))}
        for k in ("title", "artist", "album", "genre"):
            if res.get(k):
                meta[k] = res[k]
        for t in self.win._all_track_dicts(path):
            t["tags_auto"] = True
        try:
            self.win._on_track_metadata_changed(meta, refresh=False)
        except Exception as e:                             # noqa: BLE001
            print("[tag_all] apply:", e)
        got = [n for k, n in (("artist", "исполнитель"), ("album", "альбом"), ("genre", "жанр")) if res.get(k)]
        if res.get("cover_changed"):
            got.insert(0, "обложка")
        name = res.get("title") or next((str(t.get("title") or "") for t in self.win._all_track_dicts(path)), "") \
            or Path(path).stem
        self.log.insert(0, f"{name}  —  {', '.join(got)}")
        del self.log[200:]
        self._pending += 1
        if self._pending >= 25:                            # не терять найденное, если плеер закроют посреди поиска
            self._save()

    def _save(self):
        self._pending = 0
        try:
            self.win._save_library()
            self.win._save_playlists()
        except Exception as e:                             # noqa: BLE001
            print("[tag_all] save:", e)

    def _finish(self):
        w = self.win
        self._save()
        if self.updated:
            w._library_changed_refresh()
        text = (f"Теги: дополнено {self.updated}, уже были полные {self.complete}, не нашлось {self.notfound}"
                if not self.cancel else f"Поиск тегов остановлен · дополнено {self.updated}")
        try:
            w._toast(text)
        except Exception:                                  # noqa: BLE001
            pass


def local_tracks(win) -> list[dict]:
    """Все скачанные файлы коллекции и плейлистов, по одному на путь."""
    seen, out = set(), []
    for t in list(win.library) + [x for pl in win.playlists.values() for x in pl.get("tracks", [])]:
        p = str(t.get("path") or "")
        if not p or p.startswith("stream:") or p in seen:
            continue
        seen.add(p)
        if Path(p).is_file():
            out.append(t)
    return out


class TagAllDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Теги для всей музыки")
        self.setStyleSheet(QSS + "QListWidget{background:#191a21;border:none;border-radius:12px;padding:6px;color:#d8d8e2;}"
                                 "QProgressBar{background:#1f2029;border:none;border-radius:6px;height:12px;color:transparent;}"
                                 "QProgressBar::chunk{background:#ff3d81;border-radius:6px;}")
        self.resize(620, 520)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(10)
        h = QLabel("Найти теги для всей музыки")
        h.setStyleSheet("font-size:20px;font-weight:800;")
        v.addWidget(h)
        d = QLabel("Для каждого скачанного трека, у которого в файле нет исполнителя, альбома, жанра или обложки, "
                   "ищется совпадение на Deezer, iTunes и SoundCloud. Найденное записывается в файл навсегда — "
                   "только в пустые поля, ваши правки не трогаются. Окно можно закрыть: поиск продолжится в фоне.")
        d.setObjectName("Muted")
        d.setWordWrap(True)
        v.addWidget(d)
        self.count = QLabel("")
        self.count.setObjectName("H")
        v.addWidget(self.count)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        v.addWidget(self.bar)
        self.now = QLabel("")
        self.now.setObjectName("Muted")
        v.addWidget(self.now)
        self.list = QListWidget()
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.go = QPushButton("Начать поиск")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._start)
        self.stop = QPushButton("Остановить")
        self.stop.setObjectName("Danger")
        self.stop.clicked.connect(self._stop)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(self.go)
        row.addWidget(self.stop)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        self._shown_log = None
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()
        self._tracks = local_tracks(win)
        self._refresh()

    def _start(self):
        global _JOB
        if _JOB is not None and not _JOB.finished:
            return
        if not self._tracks:
            self.count.setText("В коллекции нет скачанных треков")
            return
        _JOB = TagJob(self.win, self._tracks)
        _JOB.start()
        self._refresh()

    def _stop(self):
        if _JOB is not None and not _JOB.finished:
            _JOB.cancel = True
            self.now.setText("Останавливаю — дожидаюсь треков, которые уже ищутся…")

    def _refresh(self):
        j = _JOB
        running = j is not None and not j.finished
        self.go.setEnabled(not running)
        self.go.setText("Начать поиск" if j is None else "Искать ещё раз")
        self.stop.setEnabled(running and not j.cancel)
        if j is None:
            self.count.setText(f"Скачанных треков: {len(self._tracks)}")
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.now.setText("")
            return
        self.bar.setRange(0, max(1, j.total))
        self.bar.setValue(j.done)
        self.count.setText(f"Проверено {j.done} из {j.total}  ·  дополнено {j.updated}  ·  "
                           f"уже полные {j.complete}  ·  не нашлось {j.notfound}")
        if running and not j.cancel:
            self.now.setText("Ищу: " + j.current)
        elif j.finished:
            self.now.setText("Готово" if not j.cancel else "Остановлено")
        key = (len(j.log), j.log[0] if j.log else "")
        if key != self._shown_log:
            self._shown_log = key
            self.list.clear()
            self.list.addItems(j.log[:200] or ["Пока ничего не дописано"])


def open_dialog(win):
    dlg = TagAllDialog(win)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.exec()
