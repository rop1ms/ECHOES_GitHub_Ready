"""Offline full-window smoke audit; all application data lives in a temporary home.

Run with the installed ECHOES Python. No real library, account, or network is used.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qa" / "results"
OUT.mkdir(parents=True, exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT / "app"))
installed = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "ECHOES"
for directory in (installed / "vlc", installed / "ffmpeg"):
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ["PATH"]
os.environ["PYTHON_VLC_LIB_PATH"] = str(installed / "vlc" / "libvlc.dll")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["ECHOES_SPOKEN_OFFLINE"] = "1"
os.environ["ECHOES_AUDIO_BACKEND"] = "vlc"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
temporary = tempfile.TemporaryDirectory(prefix="echoes-ui-audit-")
audit_home = Path(temporary.name)
Path.home = classmethod(lambda cls: audit_home)
os.environ["USERPROFILE"] = str(audit_home)
os.environ["HOME"] = str(audit_home)
os.environ["APPDATA"] = str(audit_home / "roaming")
os.environ["LOCALAPPDATA"] = str(audit_home / "local")


def no_network(*args, **kwargs):
    raise OSError("Network disabled in offline UI audit")


socket.socket.connect = no_network
socket.create_connection = no_network
data = audit_home / ".neon_player"
data.mkdir()
settings = {
    "theme": "Echoes Music", "ya_page": "collection", "discord_rpc": False,
    "media_session": False, "close_to_tray": False, "own_audio_output": False,
    "intro_enabled": False, "custom_titlebar": False, "fx_breathe": False,
    "fx_dust": False, "auto_theme": False,
    "language": os.environ.get("ECHOES_QA_LANGUAGE", "ru"),
}
(data / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QTabWidget, QLabel, QAbstractButton

import i18n
i18n.install(settings["language"])
app = QApplication([])
app.setStyle("Fusion")
for font in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf", "arial.ttf", "arialbd.ttf"):
    QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / font))
app.setFont(QFont("Segoe UI", 10))
i18n.start(app)
errors, qt_messages, checks = [], [], []
translation_gaps = set()


def exception_hook(kind, value, tb):
    message = "".join(traceback.format_exception(kind, value, tb))
    errors.append(message)
    print(message, flush=True)


sys.excepthook = exception_hook
from PyQt6.QtCore import qInstallMessageHandler
def qt_handler(kind, context, message):
    if "Recursive repaint" in message and not any("repaint_stack" in c for c in checks):
        checks.append({"repaint_stack": "".join(traceback.format_stack())})
    qt_messages.append(message)


qInstallMessageHandler(qt_handler)


def settle(duration=.12):
    until = time.monotonic() + duration
    while time.monotonic() < until:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(.005)


def check(label, fn):
    start = time.monotonic()
    try:
        fn()
        if settings['language'] == 'en':
            for widget in app.allWidgets():
                if widget.isVisible() and isinstance(widget, (QLabel, QAbstractButton)):
                    displayed = i18n.display_text(widget)
                    if i18n._CYR.search(displayed):
                        translation_gaps.add(displayed)
        checks.append({"name": label, "ok": True, "seconds": round(time.monotonic()-start, 3)})
    except Exception:
        errors.append(traceback.format_exc())
        checks.append({"name": label, "ok": False, "error": errors[-1]})
    print(json.dumps(checks[-1], ensure_ascii=False), flush=True)


win = None
try:
    import main
    # Preserve the tested real constructor; avoid optional OS integration in this harness.
    main.WindowsMediaKeyFilter.register = lambda self: None
    win = main.MainWindow()
    win.resize(1280, 900)
    win.show()
    settle()
    checks.append({"name": "MainWindow constructor", "ok": True})
    shell = win._ya
    for key in ("collection", "search", "wave", "foryou", "albums", "follows",
                "tierlist", "duel", "net", "profile", "queue", "lyrics", "settings", "listening"):
        def visit(key=key):
            shell.show_page(key)
            settle()
            if key in ("collection", "tierlist", "duel", "profile", "listening"):
                settle(.5)  # Include completion of both navigation and settings morph.
                win.grab().save(str(OUT / (key + ".png")))
        check("page:" + key, visit)
    for key in ("feed", "chats", "room", "friends", "net"):
        check("net:" + key, lambda key=key: (shell.show_page("net"), shell.pages["net"].show_tab(key), settle()))
    def populated_net():
        net = shell.pages["net"]
        store = net.node.store
        store.upsert_contact("offline-audit-peer", name="Audit friend", status="friend")
        for index in range(65):
            store.put_post({"id": f"audit-post-{index}", "author": net.node.me.onion, "name": "Audit",
                            "ts": time.time() - index * 60, "text": f"Audit post {index}", "kind": "post"})
            store.add_message(f"audit-message-{index}", "offline-audit-peer", "in", time.time() - index,
                              f"Message {index}: emoji content remains 🔥 🎧")
        net._data_ver = net.__dict__.get("_data_ver", 0) + 1
        shell.show_page("net")
        net.show_tab("feed")
        # виртуальная лента: место есть у каждого поста, карточки — только рядом с экраном
        net._fill_feed()                             # (раньше это делало «Показать ещё»)
        settle()
        assert len(net._feed_slots) >= 65
        assert 0 < len(net._feed_cards) < 30
        settle()
        win.grab().save(str(OUT / f"net_feed_{settings['language']}.png"))
        net.open_chat("offline-audit-peer")
        assert len(net._chat_rows) == 65
        first = net._chat_rows["audit-message-0"][1]
        store.add_message("audit-new", "offline-audit-peer", "in", time.time(), "New message")
        net._on_event("message", "offline-audit-peer")
        assert net._chat_rows["audit-message-0"][1] is first
        settle()
        win.grab().save(str(OUT / f"net_chat_{settings['language']}.png"))
    check("net:populated feed, paging and incremental chat", populated_net)
    shell.show_page("listening")
    settle(.5)
    panel = shell._listener_panel
    tabs = panel.findChild(QTabWidget)
    assert not panel.isWindow(), "Listening tools escaped to a separate window"
    for index in range(tabs.count()):
        check("listening:" + tabs.tabText(index), lambda index=index: (tabs.setCurrentIndex(index), settle()))
    for category in list(shell.settings_hub.nav_btns):
        check("settings:" + category, lambda category=category: (shell.settings_hub.select(category), settle(.3)))
    def volume_roundtrip():
        win.vol.setValue(63)
        shell._muted_from = None
        shell._toggle_mute()
        assert win.vol.value() == 0
        shell._toggle_mute()
        assert win.vol.value() == 63
    check("player:mute roundtrip", volume_roundtrip)
    # Load populated data after empty-state coverage; no files are opened for playback.
    win.library = [{"path": str(audit_home / (str(i)+".wav")), "title": "Track "+str(i),
                    "artist": "Audit artist", "duration": 120} for i in range(30)]
    for key in ("collection", "albums", "tierlist", "duel", "profile", "queue"):
        check("populated:" + key, lambda key=key: (shell._dirty.add(key), shell.show_page(key), settle()))
    for width, height in ((860, 600), (1120, 720), (1920, 1080)):
        check(f"resize:{width}x{height}", lambda w=width, h=height: (win.resize(w, h), settle(), win.grab()))
    for theme in ("Vinyl glass", "Fluid", "Fluid Dark", "Winamp", "Echoes Music"):
        if theme in main.THEMES:
            check("theme:" + theme, lambda theme=theme: (win._apply_theme(theme), settle(.3), win.grab()))
except Exception:
    errors.append(traceback.format_exc())
    print(errors[-1], flush=True)
finally:
    if win is not None:
        win._really_quit = True
        check("window shutdown", lambda: (win.close(), settle()))
    result = {"checks": checks, "python_errors": errors, "qt_messages": qt_messages,
              "translation_gaps": sorted(translation_gaps),
              "network": "blocked", "user_data": "temporary home"}
    (OUT / ("ui_smoke_" + settings["language"] + ".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT", len(checks), "checks;", len(errors), "Python errors;", len(qt_messages), "Qt messages", flush=True)
    # Stop lingering third-party daemon workers; this harness has already tested window shutdown.
    sys.stdout.flush()
    os._exit(1 if errors or any(c.get("ok") is False for c in checks) else 0)
