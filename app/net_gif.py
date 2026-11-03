# net_gif.py
"""
Echoes.NET: GIF в чатах и постах — поиск (Tenor / Giphy), анимированное превью, окно выбора.

Поиск работает на ВАШЕМ бесплатном ключе (у сервисов нет поиска без ключа):
  • Tenor  — console.cloud.google.com → «Tenor API» → Credentials → API key;
  • Giphy  — developers.giphy.com → Create an App → API key.
Ключ вводится в «Сеть» → «GIF». Без ключа можно вставить ссылку на GIF (Tenor/Giphy) или отправить
GIF-файл как обычный файл.

В сообщение уходит только ссылка на GIF с CDN Tenor/Giphy (другие адреса не принимаются),
получатель скачивает её сам и кэширует в ~/Echoes/net/gif_cache.
"""
from __future__ import annotations

import data_home
import hashlib
import re
import threading
from pathlib import Path
from urllib.parse import quote, urlparse

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QByteArray, QBuffer, QIODevice, QSize, QTimer
from PyQt6.QtGui import QMovie, QColor, QPainter, QFont
from PyQt6.QtWidgets import (QLabel, QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QScrollArea,
                             QWidget, QComboBox)

CACHE = data_home.root() / "net" / "gif_cache"
SAVED = data_home.root() / "net" / "gif_saved.json"         # «Мои GIF»
SAVED_MAX = 500
MAX_GIF = 8 * 2 ** 20
HOSTS = re.compile(r"^(?:media\d?\.tenor\.com|c\.tenor\.com|media\d?\.giphy\.com|i\.giphy\.com)$")


def safe_url(url) -> str:
    """Только https и только CDN Tenor/Giphy — чужие адреса не открываем (IP, трекеры, что угодно)."""
    u = str(url or "").strip()
    try:
        p = urlparse(u)
    except ValueError:
        return ""
    return u[:500] if p.scheme == "https" and HOSTS.match(p.hostname or "") else ""


def clean_gif(g) -> dict | None:
    if not isinstance(g, dict):
        return None
    url = safe_url(g.get("url"))
    if not url:
        return None
    out = {"url": url, "preview": safe_url(g.get("preview")) or url}
    for k in ("w", "h"):
        try:
            out[k] = max(1, min(2000, int(g.get(k) or 0))) if g.get(k) else 0
        except (TypeError, ValueError):
            out[k] = 0
    out["title"] = str(g.get("title") or "")[:100]
    return out


def from_link(text: str) -> dict | None:
    """Ссылка от пользователя: прямой .gif с CDN или страница tenor/giphy (тогда — прямой адрес по id)."""
    t = (text or "").strip()
    if safe_url(t):
        return {"url": t, "preview": t, "w": 0, "h": 0, "title": ""}
    m = re.search(r"giphy\.com/gifs/(?:[\w-]*-)?([A-Za-z0-9]{8,})", t)
    if m:
        u = f"https://i.giphy.com/media/{m.group(1)}/giphy.gif"
        return {"url": u, "preview": u, "w": 0, "h": 0, "title": ""}
    return None


# ── «Мои GIF» ── #

def saved() -> list:
    import json
    try:
        d = json.loads(SAVED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [g for g in (clean_gif(x) for x in d if isinstance(x, dict)) if g]


def _write_saved(items):
    import json
    SAVED.parent.mkdir(parents=True, exist_ok=True)
    tmp = SAVED.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[:SAVED_MAX], ensure_ascii=False), encoding="utf-8")
    tmp.replace(SAVED)


def is_saved(g) -> bool:
    u = (g or {}).get("url")
    return any(x["url"] == u for x in saved())


def save(g) -> bool:
    g = clean_gif(g)
    if not g:
        return False
    items = [x for x in saved() if x["url"] != g["url"]]
    _write_saved([g] + items)                                  # новые — первыми
    return True


def unsave(g):
    u = (g or {}).get("url")
    _write_saved([x for x in saved() if x["url"] != u])


def cache_file(url) -> Path:
    return CACHE / (hashlib.sha1(str(url).encode()).hexdigest() + ".gif")


def export_file(g, parent=None, dest=None):
    """Сохранить GIF файлом на диск (из кэша; нет в кэше — сначала скачать)."""
    url = safe_url((g or {}).get("url"))
    if not url:
        return
    if dest is None:
        from PyQt6.QtWidgets import QFileDialog
        name = re.sub(r"[^\w\- ]+", "", (g.get("title") or "").strip())[:60].strip() or "gif"
        dest, _ = QFileDialog.getSaveFileName(parent, "Сохранить GIF", str(Path.home() / "Downloads" / f"{name}.gif"),
                                              "GIF (*.gif)")
        if not dest:
            return
    f = cache_file(url)
    if f.exists():
        Path(dest).write_bytes(f.read_bytes())
        return

    def done(u, data):
        if u != url:
            return
        try:
            loader().done.disconnect(done)
        except (TypeError, RuntimeError):
            pass
        if not data.isEmpty():
            Path(dest).write_bytes(bytes(data))
    loader().done.connect(done)
    loader().get(url)


def gif_menu(widget, g, pos, on_change=None):
    """Правый клик по GIF: в «Мои GIF» / убрать, сохранить файлом, копировать ссылку."""
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QMenu
    m = QMenu(widget)
    if is_saved(g):
        m.addAction("Убрать из моих GIF", lambda: (unsave(g), on_change and on_change()))
    else:
        m.addAction("★  В мои GIF", lambda: (save(g), on_change and on_change()))
    m.addAction("Сохранить файлом…", lambda: export_file(g, widget.window()))
    m.addAction("Копировать ссылку", lambda: QGuiApplication.clipboard().setText(g.get("url", "")))
    m.exec(pos)


# ── ключи: определить сервис по виду ключа и проверить ── #

KEY_TENOR = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")          # ключи Google Cloud
KEY_GIPHY = re.compile(r"^[A-Za-z0-9]{32}$")
CONNECT_URL = {"giphy": "https://developers.giphy.com/dashboard/?create=true",
               "tenor": "https://console.cloud.google.com/apis/library/tenor.googleapis.com"}


def key_provider(text) -> str:
    t = str(text or "").strip()
    return "tenor" if KEY_TENOR.match(t) else "giphy" if KEY_GIPHY.match(t) else ""


def check_key(provider, key) -> str:
    """'' — ключ работает, иначе текст ошибки. Блокирующий (из потока)."""
    try:
        search(provider, key, "", limit=1)
        return ""
    except Exception as e:                                     # noqa: BLE001
        return str(e)[:160]


# ── поиск ── #

def search(provider: str, key: str, q: str = "", limit: int = 30) -> list[dict]:
    """Блокирующий запрос (звать из потока). Пустой q — популярное."""
    import requests
    out = []
    if provider == "giphy":
        ep = "search" if q else "trending"
        r = requests.get(f"https://api.giphy.com/v1/gifs/{ep}", timeout=12,
                         params={"api_key": key, "q": q, "limit": limit, "rating": "pg-13", "lang": "ru"})
        r.raise_for_status()
        for d in r.json().get("data", []):
            im = d.get("images") or {}
            full, prev = im.get("downsized") or im.get("original") or {}, im.get("fixed_width_small") or {}
            g = clean_gif({"url": full.get("url"), "preview": prev.get("url"), "w": full.get("width"),
                           "h": full.get("height"), "title": d.get("title")})
            if g:
                out.append(g)
    else:
        ep = "search" if q else "featured"
        r = requests.get(f"https://tenor.googleapis.com/v2/{ep}", timeout=12,
                         params={"key": key, "q": q, "limit": limit, "client_key": "echoes",
                                 "media_filter": "gif,tinygif", "contentfilter": "medium", "locale": "ru_RU"})
        r.raise_for_status()
        for d in r.json().get("results", []):
            mf = d.get("media_formats") or {}
            full, prev = mf.get("gif") or {}, mf.get("tinygif") or {}
            dims = full.get("dims") or [0, 0]
            g = clean_gif({"url": full.get("url"), "preview": prev.get("url"), "w": dims[0], "h": dims[1],
                           "title": d.get("content_description")})
            if g:
                out.append(g)
    return out


# ── загрузка с кэшем ── #

class _Loader(QObject):
    done = pyqtSignal(str, QByteArray)

    def __init__(self):
        super().__init__()
        self._busy: set[str] = set()

    def get(self, url):
        url = safe_url(url)
        if not url:
            return
        f = cache_file(url)
        if f.exists():
            self.done.emit(url, QByteArray(f.read_bytes()))
            return
        if url in self._busy:
            return
        self._busy.add(url)

        def work():
            data = b""
            try:
                import requests
                with requests.get(url, timeout=20, stream=True) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(64 * 1024):
                        data += chunk
                        if len(data) > MAX_GIF:
                            data = b""
                            break
                if data[:3] == b"GIF":
                    CACHE.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(data)
                else:
                    data = b""
            except Exception as e:                             # noqa: BLE001
                print("[gif]", e)
            self._busy.discard(url)
            self.done.emit(url, QByteArray(data))
        threading.Thread(target=work, daemon=True).start()


_LOADER = None


def loader() -> _Loader:
    global _LOADER
    if _LOADER is None:
        _LOADER = _Loader()
    return _LOADER


class GifView(QLabel):
    """Анимированная GIF (как и должна быть): грузится в фоне, крутится, пока видна."""

    def __init__(self, g: dict, max_w=300, max_h=260, preview=False, parent=None):
        super().__init__(parent)
        self.g = g
        self.url = g.get("preview") if preview else g.get("url")
        w, h = g.get("w") or 4, g.get("h") or 3
        k = min(max_w / w, max_h / h)
        self.setFixedSize(max(60, int(w * k)), max(45, int(h * k)))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background:rgba(255,255,255,0.05);border-radius:12px;color:#8a8a8a;font-size:11px;")
        self.setText("GIF…")
        self._movie = None
        self._buf = None
        self._opening_full = False
        self.on_click = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        loader().done.connect(self._loaded)
        loader().get(self.url)

    def _loaded(self, url, data):
        if url != self.url:
            return
        try:
            loader().done.disconnect(self._loaded)
        except (TypeError, RuntimeError):
            pass
        if data.isEmpty():
            self.setText("GIF не загрузилась")
            return
        self._buf = QBuffer(self)
        self._buf.setData(data)
        self._buf.open(QIODevice.OpenModeFlag.ReadOnly)
        self._movie = QMovie(self._buf, QByteArray(), self)
        self._movie.setScaledSize(self.size())
        self.setMovie(self._movie)
        from performance import configure_movie
        configure_movie(self._movie,self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.on_click:
                self.on_click(self.g)
            else:
                self._open_full()

    def _open_full(self):
        """GIF attachments use the same resizable gallery as attached files."""
        full_url = safe_url(self.g.get("url"))
        if not full_url or self._opening_full:
            return
        file = cache_file(full_url)
        if file.exists():
            from net_files_ui import MediaViewer
            meta = {"name": self.g.get("title") or "GIF", "kind": "image", "size": file.stat().st_size,
                    "pv": {"gif": True}}
            MediaViewer(self, str(file), meta).exec()
            return
        self._opening_full = True
        self.setToolTip("Загружается полная GIF…")
        loader().done.connect(self._full_loaded)
        loader().get(full_url)

    def _full_loaded(self, url, data):
        if url != self.g.get("url"):
            return
        try:
            loader().done.disconnect(self._full_loaded)
        except (TypeError, RuntimeError):
            pass
        self._opening_full = False
        self.setToolTip("Нажмите — растягиваемое окно; правый клик — сохранить GIF")
        if not data.isEmpty() and cache_file(url).exists():
            self._open_full()

    def contextMenuEvent(self, e):
        gif_menu(self, self.g, e.globalPos(), self.on_menu_change)

    on_menu_change = None

    def hideEvent(self, e):
        if self._movie is not None:
            self._movie.setPaused(True)

    def showEvent(self, e):
        if self._movie is not None:
            from performance import configure_movie
            configure_movie(self._movie,self)


class GifPicker(QDialog):
    """Окно выбора GIF: поиск, популярное, сетка анимированных превью; ссылка — если ключа нет."""
    results = pyqtSignal(list, str)                            # из потока поиска → в окно (очередью Qt)

    def __init__(self, parent, settings, on_pick, qss="", save_cb=None):
        super().__init__(parent)
        self.setWindowTitle("GIF")
        self.settings = settings
        self.save_cb = save_cb
        self.on_pick = on_pick
        self.setMinimumSize(640, 560)
        self.setStyleSheet(qss + "QDialog{background:#111214;} QLabel{background:transparent;color:#fff;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        top = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Найти GIF…")
        self.q.returnPressed.connect(self._search)
        top.addWidget(self.q, 1)
        self.prov = QComboBox()
        for k, t in (("saved", "★ Мои GIF"), ("tenor", "Tenor"), ("giphy", "Giphy")):
            self.prov.addItem(t, k)
        has = {k: bool(str(settings.get(f"gif_key_{k}") or "").strip()) for k in ("tenor", "giphy")}
        want = settings.get("gif_provider") or ("tenor" if has["tenor"] else "giphy" if has["giphy"] else "saved")
        if want != "saved" and not has.get(want):
            want = "tenor" if has["tenor"] else "giphy" if has["giphy"] else "saved"
        self.prov.setCurrentIndex(self.prov.findData(want))
        top.addWidget(self.prov)
        b = QPushButton("Найти")
        b.setObjectName("AccentBtn")
        b.clicked.connect(self._search)
        top.addWidget(b)
        v.addLayout(top)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#9a9a9a;font-size:12px;")
        v.addWidget(self.info)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.inner = QWidget()
        self.inner.setStyleSheet("background:transparent;")
        from yamusic_theme import FlowLayout
        self.flow = FlowLayout(self.inner)
        self.area.setWidget(self.inner)
        v.addWidget(self.area, 1)
        row = QHBoxLayout()
        self.link = QLineEdit()
        self.link.setPlaceholderText("…или вставьте ссылку на GIF (Tenor / Giphy)")
        row.addWidget(self.link, 1)
        lb = QPushButton("Отправить ссылку")
        lb.clicked.connect(self._from_link)
        row.addWidget(lb)
        self.conn = QPushButton("Подключить Tenor / Giphy")
        self.conn.setObjectName("AccentBtn")
        self.conn.clicked.connect(self._connect)
        row.addWidget(self.conn)
        v.addLayout(row)
        self.prov.currentIndexChanged.connect(lambda _: self._search())
        self.results.connect(self._show)
        QTimer.singleShot(0, self._search)

    def _key(self):
        prov = self.prov.currentData()
        return prov, str(self.settings.get(f"gif_key_{prov}") or "").strip()

    def _connect(self):
        if ConnectDialog(self, self.settings, self.save_cb).exec():
            prov = next((k for k in ("tenor", "giphy") if self.settings.get(f"gif_key_{k}")), "saved")
            self.prov.setCurrentIndex(self.prov.findData(prov))
            self._search()

    def _search(self):
        prov, key = self._key()
        self.conn.setVisible(not any(str(self.settings.get(f"gif_key_{k}") or "").strip() for k in ("tenor", "giphy")))
        if prov == "saved":
            q = self.q.text().strip().casefold()
            res = [g for g in saved() if not q or q in (g.get("title") or "").casefold()]
            self._show(res, "", saved_mode=True)
            return
        if not key:
            from yamusic_theme import _clear_layout
            _clear_layout(self.flow)
            self.info.setText(f"{self.prov.currentText()} ещё не подключён — нажмите «Подключить Tenor / Giphy» "
                              "(минута, бесплатно). Пока можно взять GIF из «Мои GIF» или вставить ссылку ниже.")
            return
        self.info.setText("Ищу…")
        q = self.q.text().strip()

        def work():
            try:
                res = search(prov, key, q)
                err = ""
            except Exception as e:                             # noqa: BLE001
                res, err = [], str(e)[:120]
            try:
                self.results.emit(res, err)
            except RuntimeError:                               # окно уже закрыли
                pass
        threading.Thread(target=work, daemon=True).start()

    def _show(self, res, err, saved_mode=False):
        from yamusic_theme import _clear_layout
        _clear_layout(self.flow)
        if saved_mode:
            self.info.setText(f"Мои GIF: {len(res)} · правый клик — убрать или сохранить файлом" if res else
                              "Здесь будут ваши GIF: правый клик по любой GIF в чате или посте → «★ В мои GIF».")
        else:
            self.info.setText(f"Ошибка поиска: {err}" if err else ("Ничего не нашлось" if not res else
                              f"{'Популярное' if not self.q.text() else 'Найдено'}: {len(res)} · powered by "
                              f"{self.prov.currentText()} · правый клик — в «Мои GIF»"))
        for g in res:
            gv = GifView(g, 190, 150, preview=True)
            gv.setToolTip(g.get("title") or "")
            gv.on_click = self._pick
            if saved_mode:
                gv.on_menu_change = self._search
            self.flow.addWidget(gv)

    def _pick(self, g):
        self.settings["gif_provider"] = self.prov.currentData()
        if callable(self.save_cb):
            self.save_cb()
        self.on_pick(g)
        self.accept()

    def _from_link(self):
        g = from_link(self.link.text())
        if not g:
            self.info.setText("Это не ссылка на GIF с Tenor/Giphy (нужен адрес media.tenor.com, i.giphy.com "
                              "или страница giphy.com/gifs/…)")
            return
        self._pick(g)


class ConnectDialog(QDialog):
    """Подключить Tenor/Giphy почти в один клик: кнопка открывает страницу ключа, а скопированный ключ
    подхватывается из буфера обмена сам — сервис определяется по виду ключа, ключ проверяется и сохраняется.
    (Общего встроенного ключа быть не может: у Tenor и Giphy ключ личный, чужие вписывать нельзя.)"""
    checked = pyqtSignal(str, str, str)                        # сервис, ключ, ошибка ('' — работает)

    def __init__(self, parent, settings, save_cb=None):
        super().__init__(parent)
        self.settings, self.save_cb = settings, save_cb
        self.setWindowTitle("Подключить GIF")
        self.setMinimumWidth(540)
        self.setStyleSheet("QDialog{background:#141416;} QLabel{color:#f2f2f2;background:transparent;}"
                           "QLineEdit{background:#232326;color:#f2f2f2;border:1px solid #333;border-radius:12px;"
                           "padding:8px 12px;font-size:13px;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        h = QLabel("Подключить Tenor или Giphy")
        h.setStyleSheet("font-size:19px;font-weight:900;")
        v.addWidget(h)
        t = QLabel("1. Нажмите кнопку — откроется страница бесплатного ключа (войдите в аккаунт).\n"
                   "2. Создайте ключ и скопируйте его.\n"
                   "3. Всё — ECHOES сам заметит ключ в буфере обмена, проверит и сохранит.\n\n"
                   "Giphy: «Create an App» → API → ключ.  Tenor: «Включить» → Credentials → Create API key.")
        t.setWordWrap(True)
        t.setStyleSheet("color:#c8c8cc;font-size:13px;")
        v.addWidget(t)
        row = QHBoxLayout()
        for prov, text in (("giphy", "Открыть Giphy"), ("tenor", "Открыть Tenor")):
            b = QPushButton(text)
            b.setObjectName("AccentBtn" if prov == "giphy" else "GhostBtn")
            b.clicked.connect(lambda _=False, p=prov: self._open(p))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.key = QLineEdit()
        self.key.setPlaceholderText("…или вставьте ключ сюда — Tenor или Giphy определится сам")
        self.key.textChanged.connect(self._typed)
        v.addWidget(self.key)
        self.status = QLabel("Жду ключ…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9a9aa2;font-size:12px;")
        v.addWidget(self.status)
        close = QPushButton("Закрыть")
        close.setObjectName("GhostBtn")
        close.clicked.connect(self.reject)
        r2 = QHBoxLayout()
        r2.addStretch(1)
        r2.addWidget(close)
        v.addLayout(r2)
        self.checked.connect(self._result)
        from PyQt6.QtGui import QGuiApplication
        self._cb = QGuiApplication.clipboard()
        self._cb.dataChanged.connect(self._clip)
        self._busy = ""

    def _open(self, prov):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(CONNECT_URL[prov]))
        self.status.setText(f"Страница {prov.title()} открыта в браузере. Скопируйте ключ — я подхвачу его сам.")

    def _clip(self):
        try:
            t = self._cb.text().strip()
        except RuntimeError:
            return
        if key_provider(t) and t != self.key.text().strip():
            self.key.setText(t)                                # → _typed → проверка

    def _typed(self, text):
        t = text.strip()
        prov = key_provider(t)
        if not prov:
            if t:
                self.status.setText("Не похоже на ключ: у Giphy — 32 буквы и цифры, у Tenor (Google) — начинается с AIza")
            return
        if self._busy == t:
            return
        self._busy = t
        self.status.setText(f"Проверяю ключ {prov.title()}…")

        def work():
            err = check_key(prov, t)
            try:
                self.checked.emit(prov, t, err)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _result(self, prov, key, err):
        self._busy = ""
        if err:
            self.status.setText(f"Ключ {prov.title()} не подошёл: {err}")
            return
        self.settings[f"gif_key_{prov}"] = key
        self.settings["gif_provider"] = prov
        if callable(self.save_cb):
            self.save_cb()
        self.status.setText(f"{prov.title()} подключён ✓")
        QTimer.singleShot(700, self.accept)

    def done(self, r):
        try:
            self._cb.dataChanged.disconnect(self._clip)
        except (TypeError, RuntimeError):
            pass
        super().done(r)
