# lyrics_editor.py
"""
Встроенный редактор текста песни с LRC-таймингом ("караоке"-редактор).

Немодальный — можно держать открытым и печатать/расставлять таймкоды
прямо во время прослушивания трека. Хоткей (по умолчанию F8, плюс кнопка
в тулбаре) ставит таймкод [mm:ss.xx] в начало текущей строки на основании
текущей позиции воспроизведения, полученной напрямую из AudioEngine, и
переводит курсор на следующую строку — так же, как работают привычные
LRC-тайм-тегеры: играешь трек, жмёшь хоткей на каждой строке в такт.

После того как пользователь сохранил свой текст (через _save_lrc или
_save_tag), флаг _user_locked=True защищает его от автоматической
перезаписи сетевым поиском. Сбросить защиту можно кнопкой «Найти снова».
"""

import re
import threading

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor, QPixmap, QImage
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QMessageBox, QLineEdit, QFormLayout, QGroupBox,
    QFileDialog, QSizePolicy, QFrame
)

from lyrics_tags import (
    write_lyrics_lrc, write_lyrics_tag, read_local_lyrics,
    write_cover, write_metadata, read_metadata, read_cover,
)

_LEADING_TS = re.compile(r"^\s*\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*")


def _fmt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"[{minutes:02d}:{secs:05.2f}]"


class LyricsEditorDialog(QDialog):
    lyrics_changed = pyqtSignal(str)       # новый текст после сохранения
    metadata_changed = pyqtSignal(dict)    # {'title','artist','album','genre'} после сохранения тегов
    _genius_result = pyqtSignal(object, str)
    _sync_status = pyqtSignal(str)
    _sync_done = pyqtSignal(object, str)    # (LRC | None, ошибка)

    def __init__(self, parent, engine, genius_token_getter, track_path: str,
                 title: str, artist: str, initial_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Текст и теги — {title}")
        self.setMinimumSize(560, 580)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._engine = engine
        self._genius_token_getter = genius_token_getter
        self._track_path = track_path
        self._title = title
        self._artist = artist
        # Если True — пользователь уже сохранил свой текст; автопоиск не
        # должен его перезаписывать до явного разрешения («Найти снова»).
        self._user_locked: bool = False
        self._pending_cover_bytes: bytes | None = None
        self._pending_cover_mime: str = "image/jpeg"

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        # ── Блок метаданных (теги файла) ────────────────────────────────
        meta_box = QGroupBox("Теги файла")
        meta_box.setStyleSheet("QGroupBox { font-weight: 600; border-radius: 8px; "
                               "border: 1px solid rgba(128,128,128,0.26); padding-top: 6px; }")
        meta_form = QFormLayout(meta_box)
        meta_form.setSpacing(6)
        meta_form.setContentsMargins(10, 12, 10, 10)

        self.fld_title  = QLineEdit(); self.fld_title.setPlaceholderText("Название")
        self.fld_artist = QLineEdit(); self.fld_artist.setPlaceholderText("Исполнитель")
        self.fld_album  = QLineEdit(); self.fld_album.setPlaceholderText("Альбом")
        self.fld_genre  = QLineEdit(); self.fld_genre.setPlaceholderText("Жанр")
        meta_form.addRow("Название:", self.fld_title)
        meta_form.addRow("Исполнитель:", self.fld_artist)
        meta_form.addRow("Альбом:", self.fld_album)
        meta_form.addRow("Жанр:", self.fld_genre)

        # Обложка
        cover_row = QHBoxLayout()
        self.cover_preview = QLabel()
        self.cover_preview.setFixedSize(64, 64)
        self.cover_preview.setStyleSheet(
            "border-radius:8px; background:rgba(128,128,128,0.20); border:1px solid rgba(128,128,128,0.26);"
        )
        self.cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_row.addWidget(self.cover_preview)
        cover_btns = QVBoxLayout()
        btn_pick_cover = QPushButton("Выбрать обложку…")
        btn_pick_cover.clicked.connect(self._pick_cover)
        cover_btns.addWidget(btn_pick_cover)
        self.cover_status = QLabel("")
        self.cover_status.setStyleSheet("color: rgba(128,128,128,0.90); font-size: 11px;")
        cover_btns.addWidget(self.cover_status)
        cover_row.addLayout(cover_btns)
        cover_row.addStretch()
        meta_form.addRow("Обложка:", cover_row)  # type: ignore[arg-type]

        btn_save_meta = QPushButton("Сохранить теги в файл")
        btn_save_meta.setObjectName("AccentBtn")
        btn_save_meta.clicked.connect(self._save_metadata)
        meta_form.addRow("", btn_save_meta)

        lay.addWidget(meta_box)

        # ── Текст песни ─────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(128,128,128,0.23);")
        lay.addWidget(sep)

        hint = QLabel(
            "Печатайте построчно. Нажмите F8 (или «Таймкод»), чтобы проставить "
            "[мм:сс.сс] в начало строки и перейти на следующую."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgba(128,128,128,0.82); font-size: 11px;")
        lay.addWidget(hint)

        pos_lock_row = QHBoxLayout()
        self.pos_lbl = QLabel("Позиция: 0:00")
        self.pos_lbl.setStyleSheet("color: rgba(128,128,128,0.90); font-size: 11px;")
        pos_lock_row.addWidget(self.pos_lbl)
        pos_lock_row.addStretch()
        self.lock_lbl = QLabel("")
        self.lock_lbl.setStyleSheet("color: #a78bfa; font-size: 11px; font-weight: 600;")
        pos_lock_row.addWidget(self.lock_lbl)
        lay.addLayout(pos_lock_row)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText(initial_text or read_local_lyrics(track_path) or "")
        self.text_edit.setPlaceholderText("Впишите текст песни построчно…")
        lay.addWidget(self.text_edit, 1)

        transport_row = QHBoxLayout()
        self.play_btn = QPushButton("Играть / пауза")
        self.play_btn.clicked.connect(self._toggle_play)
        transport_row.addWidget(self.play_btn)
        self.timestamp_btn = QPushButton("Таймкод (F8)")
        self.timestamp_btn.clicked.connect(self._insert_timestamp)
        transport_row.addWidget(self.timestamp_btn)
        self.ai_btn = QPushButton("ИИ-синхронизация")
        self.ai_btn.setObjectName("AccentBtn")
        self.ai_btn.setToolTip(
            "Нейросеть Whisper слушает трек (локально, на вашем компьютере) и сама\n"
            "расставляет таймкоды по строкам текста. Нужен текст в окне ниже —\n"
            "можно без таймкодов. Результат можно поправить вручную (F8) и сохранить.")
        self.ai_btn.clicked.connect(self._ai_sync)
        transport_row.addWidget(self.ai_btn)
        transport_row.addStretch()
        lay.addLayout(transport_row)

        QShortcut(QKeySequence("F8"), self, activated=self._insert_timestamp)

        save_row = QHBoxLayout()
        b = QPushButton("Сохранить в .lrc")
        b.setObjectName("AccentBtn")
        b.clicked.connect(self._save_lrc)
        save_row.addWidget(b)
        b = QPushButton("Сохранить в тег файла")
        b.clicked.connect(self._save_tag)
        save_row.addWidget(b)
        lay.addLayout(save_row)

        find_row = QHBoxLayout()
        self.genius_btn = QPushButton("Найти текст в сети")
        self.genius_btn.setObjectName("GhostBtn")
        self.genius_btn.clicked.connect(self._find_on_genius)
        find_row.addWidget(self.genius_btn)
        self.unlock_btn = QPushButton("Найти снова (сбросить защиту)")
        self.unlock_btn.setObjectName("GhostBtn")
        self.unlock_btn.setToolTip(
            "Разрешить автоматическому поиску перезаписать текст.\n"
            "Нажми, только если хочешь заменить свой текст сетевым."
        )
        self.unlock_btn.clicked.connect(self._unlock_and_search)
        self.unlock_btn.hide()
        find_row.addWidget(self.unlock_btn)
        find_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.hide)
        find_row.addWidget(close_btn)
        lay.addLayout(find_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: rgba(128,128,128,0.90); font-size: 11px;")
        lay.addWidget(self.status_lbl)

        if engine is not None:
            engine.position_changed.connect(self._on_position)

        self._genius_result.connect(self._on_genius_result)
        self._sync_status.connect(lambda s: self.status_lbl.setText(s))
        self._sync_done.connect(self._on_sync_done)
        self._sync_cancel = None
        self._load_meta_fields()
        self._load_cover_preview()

    # ── Метаданные ──────────────────────────────────────────────────────

    def _load_meta_fields(self):
        meta = read_metadata(self._track_path)
        self.fld_title.setText(meta.get("title", "") or self._title)
        self.fld_artist.setText(meta.get("artist", "") or self._artist)
        self.fld_album.setText(meta.get("album", ""))
        self.fld_genre.setText(meta.get("genre", ""))

    def _load_cover_preview(self):
        data = read_cover(self._track_path)
        if data:
            self._set_cover_preview_from_bytes(data)
        else:
            self.cover_preview.setText("нет обложки")

    def _set_cover_preview_from_bytes(self, data: bytes):
        pm = QPixmap()
        pm.loadFromData(data)
        if not pm.isNull():
            self.cover_preview.setPixmap(
                pm.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
            )

    def _pick_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать обложку", "",
            "Изображения (*.jpg *.jpeg *.png *.webp)"
        )
        if not path:
            return
        from pathlib import Path as _P
        data = _P(path).read_bytes()
        mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        self._pending_cover_bytes = data
        self._pending_cover_mime = mime
        self._set_cover_preview_from_bytes(data)
        self.cover_status.setText("Обложка выбрана — нажми «Сохранить теги»")

    def _save_metadata(self):
        title  = self.fld_title.text().strip()
        artist = self.fld_artist.text().strip()
        album  = self.fld_album.text().strip()
        genre  = self.fld_genre.text().strip()

        ok_meta = write_metadata(self._track_path, title=title, artist=artist,
                                 album=album, genre=genre)
        ok_cover = True
        cover_saved = False
        if self._pending_cover_bytes:
            ok_cover = write_cover(self._track_path, self._pending_cover_bytes,
                                   self._pending_cover_mime)
            cover_saved = bool(ok_cover)
            if ok_cover:
                self._pending_cover_bytes = None
                self.cover_status.setText("Обложка сохранена.")

        if ok_meta:
            self.status_lbl.setText("Теги сохранены в файл.")
            # Оповещаем плеер, чтобы он обновил library и заголовок
            self.metadata_changed.emit({
                "title": title, "artist": artist,
                "album": album, "genre": genre,
                "path": self._track_path, "cover_changed": cover_saved,
            })
            # Обновляем внутренние поля для Genius-поиска
            if title:  self._title  = title
            if artist: self._artist = artist
        else:
            QMessageBox.warning(self, "Ошибка",
                "Не удалось записать теги для этого формата.\n"
                "Попробуйте другой формат или проверьте права на файл.")
        if not ok_cover:
            QMessageBox.warning(self, "Ошибка обложки",
                "Не удалось записать обложку в тег файла.")

    # ── Позиция / транспорт ─────────────────────────────────────────────

    def _on_position(self, seconds: float):
        m, s = divmod(int(seconds), 60)
        self.pos_lbl.setText(f"Позиция: {m}:{s:02d}")

    def _toggle_play(self):
        if self._engine is None:
            return
        if self._engine.is_playing():
            self._engine.pause_with_effect() if hasattr(self._engine, "pause_with_effect") else self._engine.pause()
        else:
            self._engine.play_with_effect() if hasattr(self._engine, "play_with_effect") else self._engine.play()

    def _insert_timestamp(self):
        if self._engine is None or not self._engine.current_path:
            self.status_lbl.setText("Нет воспроизводимого трека.")
            return
        pos = self._engine.get_position()
        ts = _fmt_timestamp(pos)

        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfLine, QTextCursor.MoveMode.KeepAnchor)
        line = cursor.selectedText()
        clean = _LEADING_TS.sub("", line).lstrip()
        cursor.insertText(f"{ts} {clean}")

        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        moved = cursor.movePosition(QTextCursor.MoveOperation.Down)
        if not moved:
            cursor.movePosition(QTextCursor.MoveOperation.EndOfLine)
            cursor.insertText("\n")
        else:
            cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        self.text_edit.setTextCursor(cursor)
        self.status_lbl.setText(f"Таймкод {ts} поставлен.")

    # ── Сохранение текста / блокировка ──────────────────────────────────

    def _set_locked(self, locked: bool):
        self._user_locked = locked
        if locked:
            self.lock_lbl.setText("Текст защищён от автозамены")
            self.unlock_btn.show()
        else:
            self.lock_lbl.setText("")
            self.unlock_btn.hide()

    def _save_lrc(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            self.status_lbl.setText("Текст пуст — нечего сохранять.")
            return
        try:
            path = write_lyrics_lrc(self._track_path, text)
            self.status_lbl.setText(f"Сохранено: {path.name}")
            self._set_locked(True)
            self.lyrics_changed.emit(text)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить .lrc: {e}")

    def _save_tag(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            self.status_lbl.setText("Текст пуст — нечего сохранять.")
            return
        ok = write_lyrics_tag(self._track_path, text)
        if ok:
            self.status_lbl.setText("Сохранено в тег файла.")
            self._set_locked(True)
            self.lyrics_changed.emit(text)
        else:
            QMessageBox.warning(
                self, "Ошибка",
                "Не удалось записать тег. Попробуйте «Сохранить в .lrc»."
            )

    # ── ИИ-синхронизация ────────────────────────────────────────────────

    def _ai_sync(self):
        if self._sync_cancel is not None:                    # идёт синхронизация — кнопка работает как «Отмена»
            self._sync_cancel.set()
            self.status_lbl.setText("Отменяю…")
            return
        import lyrics_sync as LS
        text = self.text_edit.toPlainText()
        if not LS.plain_lines(text):
            self.status_lbl.setText("Сначала впишите текст песни или нажмите «Найти текст в сети».")
            return
        if not self._track_path:
            self.status_lbl.setText("Нет трека.")
            return
        if not LS.whisper_installed():
            if QMessageBox.question(
                self, "ИИ-синхронизация",
                "Для синхронизации нужна нейросеть распознавания речи (библиотека faster-whisper).\n\n"
                "Она ставится один раз; при первом запуске скачается модель (около 460 МБ). "
                "Аудио никуда не отправляется — всё работает на вашем компьютере.\n\nУстановить?"
            ) != QMessageBox.StandardButton.Yes:
                return
        self._sync_cancel = threading.Event()
        cancel = self._sync_cancel
        self.ai_btn.setText("Отмена")
        path = self._sync_path = self._track_path
        fast = bool(getattr(self.parent(), 'settings', {}).get('lyrics_sync_fast', False))

        def work():
            import lyrics_sync as LS
            try:
                if not LS.whisper_installed():
                    self._sync_status.emit("Устанавливаю faster-whisper (1–3 минуты)…")
                    if not LS.install_whisper(lambda s: self._sync_status.emit(s)):
                        self._sync_done.emit(None, "Не удалось установить faster-whisper (нет интернета?)")
                        return
                lrc = LS.sync_lyrics(path, text, progress=lambda s: self._sync_status.emit(s), cancel=cancel,
                                     model='base' if fast else 'small', use_vocals=not fast)
                self._sync_done.emit(lrc, "")
            except LS.SyncCancelled:
                self._sync_done.emit(None, "cancel")
            except Exception as e:                           # noqa: BLE001
                print("[lyrics sync]", e)
                self._sync_done.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_sync_done(self, lrc, err):
        self._sync_cancel = None
        self.ai_btn.setText("ИИ-синхронизация")
        if lrc and getattr(self, "_sync_path", None) != self._track_path:
            self.status_lbl.setText("Трек сменился — результат синхронизации отброшен.")
        elif lrc:
            self.text_edit.setPlainText(lrc)
            self.status_lbl.setText("Готово! Проверьте по треку, при необходимости поправьте (F8) и сохраните.")
        elif err == "cancel":
            self.status_lbl.setText("Синхронизация отменена.")
        else:
            self.status_lbl.setText(f"Не получилось: {err}")

    # ── Genius / unlock ─────────────────────────────────────────────────

    def _find_on_genius(self):
        if self._user_locked:
            self.status_lbl.setText(
                "Текст защищён. Нажми «Найти снова (сбросить защиту)» чтобы разрешить замену."
            )
            return
        self._do_genius_search()

    def _unlock_and_search(self):
        if QMessageBox.question(
            self, "Сбросить защиту",
            "Это заменит твой текст найденным в сети. Продолжить?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._set_locked(False)
        self._do_genius_search()

    def _do_genius_search(self):
        self.genius_btn.setEnabled(False)
        self.status_lbl.setText("Ищем текст (с таймингами, если есть)…")
        token = self._genius_token_getter() if self._genius_token_getter else ""

        def work():
            from lyrics import fetch_lyrics
            try:
                text, src = fetch_lyrics(self._title, self._artist, token,
                                         path=getattr(self, "_track_path", "") or "")
            except Exception:
                text, src = None, ""
            self._genius_result.emit(text, src or "")

        threading.Thread(target=work, daemon=True).start()

    def _on_genius_result(self, text, src):
        self.genius_btn.setEnabled(True)
        if text:
            self.text_edit.setPlainText(text)
            self.status_lbl.setText(f"Найдено ({src}). Проверьте и сохраните.")
        else:
            self.status_lbl.setText("Ничего не найдено.")

    # ── Служебное ───────────────────────────────────────────────────────

    def set_track(self, track_path: str, title: str, artist: str, initial_text: str = ""):
        """Переключает редактор на новый трек (вызывается из MainWindow)."""
        self._track_path = track_path
        self._title = title
        self._artist = artist
        self._pending_cover_bytes = None
        self._set_locked(False)
        self.setWindowTitle(f"Текст и теги — {title}")
        self.text_edit.setPlainText(initial_text or read_local_lyrics(track_path) or "")
        self._load_meta_fields()
        self._load_cover_preview()
        self.status_lbl.setText("")
        self.cover_status.setText("")

    def closeEvent(self, event):
        event.ignore()
        self.hide()


