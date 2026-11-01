# spotify_import.py
"""
Утилита «Импорт из Spotify»: ссылка на плейлист/альбом Spotify → список
треков → каждый ищется на YouTube Music (если там нет — на SoundCloud),
проверяется по исполнителю/названию/длительности и скачивается в плейлист
с тем же названием. Треки, которые уже есть в коллекции, просто
добавляются в плейлист без скачивания.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QListWidget, QListWidgetItem, QProgressBar,QFileDialog)

SEC_PER_TRACK = 10      # грубая оценка до первых результатов (поиск + скачивание)


def fmt_eta(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return "меньше минуты"
    h, m = sec // 3600, (sec % 3600 + 59) // 60
    if m == 60:
        h, m = h + 1, 0
    return f"{h} ч {m} мин" if h else f"{m} мин"


ST_TEXT = {
    "": "ожидает", "search": "ищу…", "ytm": "найдено на YouTube Music", "yt": "найдено на YouTube",
    "sc": "найдено на SoundCloud", "none": "не найдено", "have": "уже в коллекции",
    "queued": "в очереди", "progress": "скачивается", "done": "готово", "error": "ошибка",
    "retry": "пробую другой источник…",
}
ST_COLOR = {"none": "#ff6b6b", "error": "#ff6b6b", "done": "#3ddc84", "have": "#3ddc84",
            "progress": "#ffdb1a", "queued": "#ffdb1a", "search": "#9a9a9a",
            "retry": "#ffb347"}


class SpotifyImportDialog(QDialog):
    _fetched = pyqtSignal(object, object)            # (name, tracks) | (None, error)
    _matched = pyqtSignal(int, object, str, int)          # row, track|None, state

    def __init__(self, win, parent=None, provider='spotify'):
        super().__init__(parent or win)
        self.win = win
        from duel_settings import tr
        self.t=lambda ru,en:tr(win,ru,en)
        self.provider=provider
        self.brand={'spotify':'Spotify','yandex':self.t('Яндекс Музыка','Yandex Music'),'vk':'VK Music'}[provider]
        self.setWindowTitle(self.t('Импорт плейлиста: ','Import playlist: ')+self.brand)
        self.setMinimumSize(620, 560)
        self.tracks = []
        self.states = []
        self.pct = []
        self._running = False
        self._generation=0
        self._cancel = threading.Event()
        self._t0 = 0.0
        self._todo0 = 0

        from echo_glass import GlassFrame,glass_controls_qss
        self.setStyleSheet('QDialog{background:#090b10;}QLabel{color:#eef2fa;background:transparent;}QListWidget{background:#11151c;color:#eef2fa;border:1px solid #303946;border-radius:12px;}'+glass_controls_qss())
        outer=QVBoxLayout(self);glass=GlassFrame(radius=22);outer.addWidget(glass);lay=QVBoxLayout(glass);lay.setContentsMargins(22,20,22,20)
        lay.setSpacing(10)
        hint = QLabel(self.t('Вставьте ссылку на публичный плейлист или альбом. Треки из вашей коллекции добавятся сразу; остальные будут найдены на YouTube Music / SoundCloud.',
                             'Paste a public playlist or album link. Tracks in your library are reused; other tracks are matched on YouTube Music / SoundCloud.'))
        hint.setWordWrap(True)
        hint.setObjectName("Sub")
        lay.addWidget(hint)
        row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText({'spotify':'https://open.spotify.com/playlist/…','yandex':'https://music.yandex.ru/playlists/…','vk':'https://vk.com/music/playlist/…'}[provider])
        self.url.returnPressed.connect(self._fetch)
        row.addWidget(self.url, 1)
        self.btn_fetch = QPushButton(self.t('Получить треки','Get tracks'))
        self.btn_fetch.clicked.connect(self._fetch)
        row.addWidget(self.btn_fetch)
        lay.addLayout(row)
        if provider!='spotify':
            note=QLabel(self.t('Без токенов и входа в аккаунт. Если сервис скрывает список, загрузите TXT/M3U со строками «Исполнитель — Название». Неполный список будет отмечен.',
                'No tokens or account login. If the service hides the list, load a TXT/M3U file with Artist — Title entries. Partial lists are marked.'))
            note.setWordWrap(True);lay.addWidget(note)
            file_button=QPushButton(self.t('Загрузить список из файла…','Load track list from file…'));file_button.clicked.connect(self._load_list_file);lay.addWidget(file_button)
        row = QHBoxLayout()
        row.addWidget(QLabel(self.t('Плейлист в плеере:','Playlist in ECHOES:')))
        self.pl_name = QLineEdit()
        row.addWidget(self.pl_name, 1)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        self.btn_go = QPushButton(self.t('Скачать всё','Download all'))
        self.btn_go.setObjectName("AccentBtn")
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self._start)
        self.btn_stop = QPushButton(self.t('Остановить поиск','Stop matching'))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        close = QPushButton(self.t('Закрыть','Close'))
        close.clicked.connect(self.hide)
        row.addWidget(self.btn_go)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._fetched.connect(self._on_fetched)
        self._matched.connect(self._on_matched)

    # ── список треков ── #

    def _fetch(self):
        if self._running or getattr(self,'_fetching',False):return
        url = self.url.text().strip()
        if not url:
            return
        self.btn_fetch.setEnabled(False)
        self._fetching=True
        self.status.setText(self.t('Получаю список треков: ','Fetching track list: ')+self.brand+'…')
        s = self.win.settings
        cid, sec = s.get("spotify_client_id", ""), s.get("spotify_client_secret", "")

        def work():
            try:
                import online_search
                if self.provider=='spotify':name, tracks = online_search.spotify_tracks(url, cid, sec)
                else:
                    from service_playlist_import import fetch
                    name,tracks=fetch(self.provider,url,self.win.settings.get('language','en'))
                self._fetched.emit(name, tracks)
            except Exception as e:                   # noqa: BLE001
                self._fetched.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_fetched(self, name, tracks):
        self._fetching=False
        self.btn_fetch.setEnabled(True)
        if name is None:
            self.status.setText(self.t('Не получилось: ','Could not import: ')+str(tracks))
            return
        warning=''
        if isinstance(tracks,dict):warning=tracks.get('warning','');tracks=tracks.get('tracks',[])
        self.tracks = list(tracks)
        self.states = [""] * len(self.tracks)
        self.pct = [0.0] * len(self.tracks)
        self.pl_name.setText(name)
        self.list.clear()
        for i, t in enumerate(self.tracks):
            it = QListWidgetItem()
            self.list.addItem(it)
            self._paint_row(i)
        more = "" if len(self.tracks) != 100 or self.provider!='spotify' else \
            "  (Spotify отдал ровно 100 — возможно, это не весь плейлист; помогут ключи Spotify API в загрузчике)"
        self.status.setText(self.t(f'«{name}»: {len(self.tracks)} треков. Примерно ~{fmt_eta(len(self.tracks)*SEC_PER_TRACK)}. {more}',
                                  f'{name}: {len(self.tracks)} tracks. About {(len(self.tracks)*SEC_PER_TRACK+59)//60} min.')+' '+warning)
        self.btn_go.setText(self.t('Скачать всё','Download all'))
        self.btn_go.setEnabled(bool(self.tracks))
        self.bar.setValue(0)

    def _load_list_file(self):
        if self._running or getattr(self,'_fetching',False):return
        filename,_=QFileDialog.getOpenFileName(self,self.t('Список треков','Track list'),'','Track lists (*.txt *.m3u *.m3u8 *.echoesplaylist)')
        if not filename:return
        try:
            from pathlib import Path
            if Path(filename).stat().st_size>10*2**20:raise ValueError(self.t('Файл должен быть не больше 10 МиБ.','The file must be no larger than 10 MiB.'))
            from playlist_share import read_playlist_file
            name,_,tracks=read_playlist_file(filename);self._on_fetched(name,tracks)
        except (ValueError,OSError) as error:self.status.setText(str(error))

    def _paint_row(self, i):
        it = self.list.item(i)
        if it is None:
            return
        t = self.tracks[i]
        st = self.states[i]
        txt = self.t(ST_TEXT.get(st, st), {'':'waiting','search':'matching…','ytm':'found on YouTube Music','yt':'found on YouTube','sc':'found on SoundCloud',
            'none':'not found','have':'already in library','queued':'queued','progress':'downloading','done':'done','error':'error','retry':'trying another source…'}.get(st,st))
        if st == "progress":
            txt += f" {int(self.pct[i])}%"
        d = int(t.get("duration") or 0)
        dur = f"  {d // 60}:{d % 60:02d}" if d else ""
        it.setText(f"{i + 1:>3}. {t.get('artist', '')} — {t.get('title', '')}{dur}      [{txt}]")
        if st in ST_COLOR:
            it.setForeground(QColor(ST_COLOR[st]))

    def _update_bar(self):
        if not self.tracks:
            return
        fin = sum(1 for s in self.states if s in ("done", "have", "none", "error"))
        part = sum(self.pct[i] / 100.0 for i, s in enumerate(self.states) if s == "progress")
        self.bar.setValue(int((fin + part) / len(self.tracks) * 100))
        if self._running and fin < len(self.tracks) and not self._cancel.is_set():
            done_now = max(0, fin - (len(self.tracks) - self._todo0))
            left = len(self.tracks) - fin
            per = (time.monotonic() - self._t0) / done_now if done_now >= 3 else SEC_PER_TRACK
            self.status.setText(self.t(f"Перенос: {fin}/{len(self.tracks)}. Осталось примерно {fmt_eta(left * per)}",f"Transfer: {fin}/{len(self.tracks)}. About {int(left*per/60)+1} min left."))
        if fin == len(self.tracks) and self._running:
            self._running = False
            self.btn_fetch.setEnabled(True);self.pl_name.setEnabled(True)
            ok = sum(1 for s in self.states if s in ("done", "have"))
            miss = sum(1 for s in self.states if s in ("none", "error"))
            self.status.setText(self.t(f"Готово: {ok} треков в плейлисте «{self.pl_name.text().strip()}», не удалось: {miss}",f"Done: {ok} tracks in {self.pl_name.text().strip()}; unavailable: {miss}"))
            self.btn_stop.setEnabled(False)
            if miss:
                self.btn_go.setText(self.t("Повторить неудачные","Retry failed tracks"))
                self.btn_go.setEnabled(True)

    # ── поиск и скачивание ── #

    def _start(self):
        name = self.pl_name.text().strip() or self.brand
        self.pl_name.setText(name)
        self._generation+=1
        generation=self._generation
        self._running = True
        self.btn_fetch.setEnabled(False);self.pl_name.setEnabled(False)
        self._t0 = time.monotonic()
        self._todo0 = sum(1 for s in self.states if s not in ("done", "have"))
        self._cancel.clear()
        self.btn_go.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status.setText(self.t("Ищу треки на YouTube Music и SoundCloud…","Matching tracks on YouTube Music and SoundCloud…"))
        lib = list(self.win.library)

        def have(t):
            try:
                from track_identity import similarity
            except Exception:                        # noqa: BLE001
                return None
            for x in lib:
                if float(x.get("duration") or 0) and float(x.get("duration") or 0) < 45 \
                        and float(t.get("duration") or 0) > 60:
                    continue                         # в коллекции лишь 30-сек. превью — качаем заново
                if similarity(t["title"], x.get("title", "")) >= 0.9 and \
                        (not t["artist"] or similarity(t["artist"].split(",")[0], x.get("artist", "")) >= 0.75):
                    return x
            return None

        def job(i):
            if self._cancel.is_set():
                return
            if self.states[i] in ("done", "have", "queued", "progress", "retry"):
                return
            t = self.tracks[i]
            local = have(t)
            if local is not None:
                self._matched.emit(i, local, "have",generation)
                return
            self._matched.emit(i, None, "search",generation)
            artist = (t.get("artist") or "").split(",")[0].strip()
            cands = []
            try:
                import online_search
                cands = online_search.find_candidates(t["title"], artist, t.get("duration", 0))
            except Exception as e:                   # noqa: BLE001
                print("[spotify import]", e)
            if cands:
                res = dict(cands[0])
                res["alts"] = [c["url"] for c in cands[1:]]
            else:
                # поиск ничего уверенного не дал (или сервис не ответил) — не сдаёмся:
                # пусть yt-dlp сам найдёт «исполнитель — название» на YouTube/SoundCloud
                q = f"{artist} - {t['title']}" if artist else t["title"]
                res = {"url": f"ytsearch1:{q.replace(':', ' ')} audio", "source": "yt", "alts": []}
            self._matched.emit(i, res, res.get("source") or "yt",generation)

        def run():
            with ThreadPoolExecutor(max_workers=3) as ex:
                list(ex.map(job, range(len(self.tracks))))
        threading.Thread(target=run, daemon=True).start()

    def _stop(self):
        self._cancel.set()
        self._generation+=1;self._running=False
        self.btn_fetch.setEnabled(True);self.pl_name.setEnabled(True);self.btn_go.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.status.setText(self.t("Поиск остановлен — уже найденное докачается. Можно загрузить новый список.","Matching stopped; queued downloads will finish. You can load a new list."))

    def _on_matched(self, i, res, state,generation):
        if generation!=self._generation:return
        name = self.pl_name.text().strip() or self.brand
        self.states[i] = state
        if state == "have" and res is not None:
            pl = self.win.playlists.setdefault(name, {"tracks": [], "desc": self.t("Импорт из ","Imported from ")+self.brand, "cover": ""})
            if not any(x.get("path") == res.get("path") for x in pl["tracks"]):
                pl["tracks"].append(dict(res))
                self.win._ya_save_playlists()
        elif res is not None and res.get("url"):
            t = self.tracks[i]

            def cb(st, v, i=i):
                if generation!=self._generation:return
                if st == "progress":
                    self.states[i], self.pct[i] = "progress", float(v)
                elif st in ("queued", "done", "error", "retry"):
                    self.states[i] = st
                    if st == "done":
                        self._drop_previews(name, self.tracks[i], v)
                    if st == "error":
                        self.list.item(i).setToolTip(self.t("Не удалось: ","Failed: ")+str(v)) if self.list.item(i) else None
                self._paint_row(i)
                self._update_bar()
            self.win._quick_download(res["url"], playlist=name, cb=cb,
                                     meta={"title": t["title"], "artist": t["artist"],
                                           "duration": t.get("duration", 0), "alts": res.get("alts") or [],
                                           "album": t.get("album") or res.get("album") or "",
                                           "thumb_url": t.get("cover") or res.get("thumb_url") or ""})
        self._paint_row(i)
        self._update_bar()

    def _drop_previews(self, name, t, new_path):
        """Убрать из плейлиста старые 30-секундные превью этого трека."""
        try:
            from track_identity import similarity
            pl = self.win.playlists.get(name)
            if not pl:
                return
            keep = []
            for x in pl["tracks"]:
                d = float(x.get("duration") or 0)
                if x.get("path") != new_path and 0 < d < 45 and \
                        similarity(t.get("title", ""), x.get("title", "")) >= 0.85:
                    continue
                keep.append(x)
            if len(keep) != len(pl["tracks"]):
                pl["tracks"] = keep
                self.win._ya_save_playlists()
        except Exception as e:                       # noqa: BLE001
            print("[spotify import] previews:", e)
