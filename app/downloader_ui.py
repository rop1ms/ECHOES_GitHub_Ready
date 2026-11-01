# downloader_ui.py
"""Немодальное окно загрузчика треков/альбомов/плейлистов."""

from pathlib import Path

import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QProgressBar, QPlainTextEdit, QFileDialog, QListWidget, QListWidgetItem
)

from downloader import DownloadManager, detect_platform, PLATFORM_LABELS, ffmpeg_available


class DownloaderDialog(QDialog):
    """Не блокирует основное окно (немодальный show()) — можно продолжать
    слушать музыку, пока идёт закачка. Один DownloadManager на диалог,
    переиспользуется между запусками."""

    _search_done = pyqtSignal(int, object)

    def __init__(self, parent, download_dir: str, vk_cookies: str = "", on_track_ready=None,
                 spotify_client_id: str = "", spotify_client_secret: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Загрузчик треков")
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._download_dir = download_dir
        self._vk_cookies = vk_cookies
        self._spotify_client_id = spotify_client_id
        self._spotify_client_secret = spotify_client_secret
        self._on_track_ready = on_track_ready   # callback(path) -> добавить в библиотеку

        self.manager = DownloadManager(self)
        self.manager.progress.connect(self._on_progress)
        self.manager.item_finished.connect(self._on_item_finished)
        self.manager.log.connect(self._on_log)
        self.manager.finished.connect(self._on_finished)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        if not ffmpeg_available():
            warn = QLabel(
                "Внимание: ffmpeg не найден в PATH. Он нужен для конвертации в MP3/FLAC и "
                "вшивания обложки — без него скачивание, скорее всего, не сработает. "
                "См. инструкцию в requirements.txt."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#f87171;")
            lay.addWidget(warn)

        # ── поиск без ссылки: YouTube Music + SoundCloud ──
        lay.addWidget(QLabel("Найти трек в интернете (YouTube Music и SoundCloud) — без ссылки:"))
        s_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("исполнитель и название, например: Alex G Mary")
        self.search_edit.returnPressed.connect(self._online_search)
        s_row.addWidget(self.search_edit, 1)
        self.search_btn = QPushButton("Найти")
        self.search_btn.clicked.connect(self._online_search)
        s_row.addWidget(self.search_btn)
        lay.addLayout(s_row)
        self.results = QListWidget()
        self.results.setMaximumHeight(220)
        self.results.hide()
        self.results.itemDoubleClicked.connect(self._result_chosen)
        lay.addWidget(self.results)
        self._search_req = 0
        self._search_done.connect(self._show_results)

        lay.addWidget(QLabel("Или ссылка (VK Music / Spotify / YouTube Music / SoundCloud):"))
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://...")
        self.url_edit.textChanged.connect(self._update_platform_label)
        url_row.addWidget(self.url_edit, 1)
        lay.addLayout(url_row)

        self.platform_lbl = QLabel("Платформа: —")
        self.platform_lbl.setStyleSheet("color: rgba(128,128,120,0.95); font-size: 11px;")
        lay.addWidget(self.platform_lbl)

        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("Качество:"))
        self.quality_cb = QComboBox()
        self.quality_cb.addItem("MP3 320 kbps", "mp3")
        self.quality_cb.addItem("FLAC (без потерь)", "flac")
        opts_row.addWidget(self.quality_cb)
        opts_row.addStretch()
        self.dir_btn = QPushButton("Папка загрузки…")
        self.dir_btn.clicked.connect(self._choose_dir)
        opts_row.addWidget(self.dir_btn)
        lay.addLayout(opts_row)

        self.dir_lbl = QLabel(self._download_dir)
        self.dir_lbl.setStyleSheet("color: rgba(128,128,120,0.9); font-size: 11px;")
        self.dir_lbl.setWordWrap(True)
        lay.addWidget(self.dir_lbl)

        vk_row = QHBoxLayout()
        vk_row.addWidget(QLabel("VK cookies (необязательно):"))
        self.vk_cookies_lbl = QLabel(Path(vk_cookies).name if vk_cookies else "не указан")
        self.vk_cookies_lbl.setStyleSheet("color: rgba(128,128,120,0.9); font-size: 11px;")
        vk_row.addWidget(self.vk_cookies_lbl, 1)
        self.vk_cookies_btn = QPushButton("Выбрать…")
        self.vk_cookies_btn.clicked.connect(self._choose_vk_cookies)
        vk_row.addWidget(self.vk_cookies_btn)
        lay.addLayout(vk_row)

        spotify_hint = QLabel(
            "Spotify API (необязательно, но настоятельно рекомендуется): без "
            "своих ключей spotdl использует гостевой доступ, который Spotify "
            "часто блокирует. Бесплатное приложение — на "
            "developer.spotify.com/dashboard (Redirect URI можно указать "
            "любой, например http://127.0.0.1:9900/callback)."
        )
        spotify_hint.setWordWrap(True)
        spotify_hint.setStyleSheet("color: rgba(128,128,120,0.95); font-size: 11px;")
        lay.addWidget(spotify_hint)

        spotify_id_row = QHBoxLayout()
        spotify_id_row.addWidget(QLabel("Spotify Client ID:"))
        self.spotify_client_id_edit = QLineEdit(self._spotify_client_id)
        self.spotify_client_id_edit.setPlaceholderText("необязательно")
        self.spotify_client_id_edit.editingFinished.connect(self._on_spotify_creds_changed)
        spotify_id_row.addWidget(self.spotify_client_id_edit, 1)
        lay.addLayout(spotify_id_row)

        spotify_secret_row = QHBoxLayout()
        spotify_secret_row.addWidget(QLabel("Spotify Client Secret:"))
        self.spotify_client_secret_edit = QLineEdit(self._spotify_client_secret)
        self.spotify_client_secret_edit.setPlaceholderText("необязательно")
        self.spotify_client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.spotify_client_secret_edit.editingFinished.connect(self._on_spotify_creds_changed)
        spotify_secret_row.addWidget(self.spotify_client_secret_edit, 1)
        lay.addLayout(spotify_secret_row)

        btn_row = QHBoxLayout()
        self.download_btn = QPushButton("Скачать")
        self.download_btn.setObjectName("AccentBtn")
        self.download_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self.download_btn)
        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self._cancel_download)
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        lay.addWidget(self.progress_bar)

        self.status_lbl = QLabel("Готов к загрузке.")
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(140)
        self.log_view.setPlaceholderText("Лог загрузки появится здесь…")
        lay.addWidget(self.log_view)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)

    # ------------------------------------------------------------------ #
    #  Поиск без ссылки                                                    #
    # ------------------------------------------------------------------ #

    def _online_search(self):
        q = self.search_edit.text().strip()
        if not q:
            return
        self._search_req += 1
        req = self._search_req
        self.results.clear()
        self.results.show()
        self.results.addItem("Ищем на YouTube Music и SoundCloud…")
        self.search_btn.setEnabled(False)

        def work():
            try:
                import online_search
                res = online_search.search(q, "all")
            except Exception as e:                     # noqa: BLE001
                res = {"tracks": [], "artists": [], "errors": [str(e)]}
            self._search_done.emit(req, res)
        threading.Thread(target=work, daemon=True).start()

    def _show_results(self, req, res):
        if req != self._search_req:
            return
        self.search_btn.setEnabled(True)
        self.results.clear()
        names = {"ytm": "YT Music", "yt": "YouTube", "sc": "SoundCloud"}
        for t in res.get("tracks", []):
            d = int(t.get("duration") or 0)
            dur = f"  ·  {d // 60}:{d % 60:02d}" if d else ""
            it = QListWidgetItem(f"{t.get('title', '')}  —  {t.get('artist', '')}   "
                                 f"[{names.get(t.get('source'), '')}]{dur}")
            it.setData(Qt.ItemDataRole.UserRole, t.get("url", ""))
            it.setToolTip("Двойной клик — скачать в библиотеку")
            self.results.addItem(it)
        if not res.get("tracks"):
            self.results.addItem("Ничего не нашлось" + (": " + ", ".join(res["errors"]) if res.get("errors") else ""))

    def _result_chosen(self, item):
        url = item.data(Qt.ItemDataRole.UserRole)
        if not url:
            return
        self.url_edit.setText(url)
        self._start_download()

    def set_download_dir(self, path: str):
        self._download_dir = path
        self.dir_lbl.setText(path)

    def set_vk_cookies(self, path: str):
        self._vk_cookies = path
        self.vk_cookies_lbl.setText(Path(path).name if path else "не указан")

    def set_spotify_credentials(self, client_id: str, client_secret: str):
        self._spotify_client_id = client_id
        self._spotify_client_secret = client_secret
        self.spotify_client_id_edit.setText(client_id)
        self.spotify_client_secret_edit.setText(client_secret)

    def _on_spotify_creds_changed(self):
        self._spotify_client_id = self.spotify_client_id_edit.text().strip()
        self._spotify_client_secret = self.spotify_client_secret_edit.text().strip()

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для загрузок", self._download_dir)
        if d:
            self.set_download_dir(d)

    def _choose_vk_cookies(self):
        f, _ = QFileDialog.getOpenFileName(self, "Файл cookies.txt", "", "Text (*.txt)")
        if f:
            self.set_vk_cookies(f)

    def _update_platform_label(self, text):
        platform = detect_platform(text)
        label = PLATFORM_LABELS.get(platform, platform) if text.strip() else "—"
        self.platform_lbl.setText(f"Платформа: {label}")

    # ------------------------------------------------------------------ #

    def _start_download(self):
        url = self.url_edit.text().strip()
        if not url:
            self.status_lbl.setText("Вставьте ссылку на трек/альбом/плейлист.")
            return
        if self.manager.busy:
            return
        quality = self.quality_cb.currentData()
        Path(self._download_dir).mkdir(parents=True, exist_ok=True)
        ok = self.manager.start(
            url, quality, self._download_dir, self._vk_cookies,
            spotify_client_id=self._spotify_client_id,
            spotify_client_secret=self._spotify_client_secret,
        )
        if not ok:
            self.status_lbl.setText("Загрузка уже идёт.")
            return
        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.status_lbl.setText("Запуск загрузки…")
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

    def _cancel_download(self):
        self.manager.cancel()
        self.status_lbl.setText("Отмена…")
        self.cancel_btn.setEnabled(False)

    def _on_progress(self, idx, total, name, pct, speed_kbps):
        self.progress_bar.setValue(max(0, min(100, int(pct))))
        prefix = f"[{idx}/{total}] " if total > 1 else ""
        speed = f" — {speed_kbps:.0f} КБ/с" if speed_kbps > 0 else ""
        self.status_lbl.setText(f"{prefix}{name}{speed}")

    def _on_log(self, line):
        self.log_view.appendPlainText(line)

    def _on_item_finished(self, path):
        self.log_view.appendPlainText(f"Добавлено в библиотеку: {Path(path).name}")
        if self._on_track_ready:
            self._on_track_ready(path)

    def _on_finished(self, success, message):
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_lbl.setText(("Готово: " if success else "Ошибка: ") + message)
        if success:
            self.progress_bar.setValue(100)

    def closeEvent(self, event):
        # Не убиваем активную загрузку при закрытии окна — просто прячем его,
        # пользователь может переоткрыть диалог и увидеть прогресс дальше.
        event.ignore()
        self.hide()
