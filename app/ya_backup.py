# ya_backup.py
"""
Резервная копия всего ECHOES одним файлом (.echoesbackup — это zip): настройки, плейлисты, «Мне нравится»,
история прослушиваний, профиль, оформление, Echoes.NET (друзья, чаты, ключи — тот же аккаунт), темы.
Кэши (обложки, волна, клипы, net_cache…) не входят — они соберутся заново.

Восстановление: файл кладётся рядом с настройками как restore_pending.zip и применяется при следующем
запуске ECHOES — до того, как что-либо прочитано (main.py → apply_pending). Старые файлы не удаляются:
они переезжают в ~/.neon_player_before_restore_<дата>.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import zipfile
from pathlib import Path

DATA = Path.home() / ".neon_player"
PENDING = "restore_pending.zip"
MANIFEST = "echoes_backup.json"
SKIP_DIRS = {"covers", "net_cache", "wave_cache", "noise_cache", "ui_cache", "dj_cache", "clips", "import",
             "avatars_cache", "__pycache__", "emoji"}
SKIP_FILES = {PENDING, "playlists.before-auto-cleanup.json"}


def _walk(root: Path, themes=True, chat_files=False):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        top = rel.parts[0]
        if top in SKIP_DIRS or rel.name in SKIP_FILES or rel.suffix in (".log", ".tmp", ".part", ".lock"):
            continue
        if top == "themes" and not themes:
            continue
        if top == "net" and len(rel.parts) > 1 and rel.parts[1] == "files" and not chat_files:
            continue
        if rel.name.endswith(("-wal", "-shm")):
            continue
        yield p, rel


def estimate(root: Path = DATA, themes=True, chat_files=False) -> int:
    return sum(p.stat().st_size for p, _r in _walk(root, themes, chat_files))


def make(dst: Path, root: Path = DATA, themes=True, chat_files=False, progress=None) -> dict:
    """Собрать копию. progress(доля 0..1). Возвращает манифест."""
    import sqlite3
    files = list(_walk(root, themes, chat_files))
    total = sum(p.stat().st_size for p, _r in files) or 1
    done = 0
    man = {"app": "ECHOES", "v": 1, "ts": time.time(), "files": len(files), "bytes": total}
    tmp = dst.with_suffix(dst.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
        z.writestr(MANIFEST, json.dumps(man, ensure_ascii=False, indent=1))
        for p, rel in files:
            arc = rel.as_posix()
            if p.suffix in (".db", ".sqlite"):                 # база открыта — снять согласованную копию
                try:
                    src = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                    snap = dst.parent / (dst.stem + "_snap.db")
                    out = sqlite3.connect(str(snap))
                    src.backup(out)
                    out.close()
                    src.close()
                    z.write(snap, arc)
                    snap.unlink(missing_ok=True)
                except Exception:                              # noqa: BLE001
                    z.write(p, arc)
            else:
                z.write(p, arc,
                        compress_type=zipfile.ZIP_STORED if p.suffix.lower() in (".jpg", ".png", ".mp4", ".webm",
                                                                                ".gif", ".zip", ".ogg") else None)
            done += p.stat().st_size
            if progress:
                progress(done / total)
    tmp.replace(dst)
    return man


def check(path: Path) -> dict | None:
    """Это копия ECHOES? Манифест или None."""
    try:
        with zipfile.ZipFile(path) as z:
            man = json.loads(z.read(MANIFEST).decode("utf-8"))
            if man.get("app") != "ECHOES":
                return None
            for n in z.namelist():                             # без выхода за папку (../, абсолютные пути)
                if n.startswith(("/", "\\")) or ".." in Path(n).parts or ":" in n:
                    return None
            return man
    except Exception:                                          # noqa: BLE001
        return None


def schedule_restore(path: Path, root: Path = DATA) -> bool:
    if check(path) is None:
        return False
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, root / PENDING)
    return True


def apply_pending(root: Path = DATA) -> str:
    """Вызывается при запуске ДО чтения настроек. Возвращает папку со старыми файлами или ""."""
    z = root / PENDING
    if not z.exists():
        return ""
    if check(z) is None:
        z.rename(root / (PENDING + ".bad"))
        return ""
    old = root.parent / time.strftime(".neon_player_before_restore_%Y%m%d_%H%M%S")
    old.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z) as zf:
        tops = {Path(n).parts[0] for n in zf.namelist() if n != MANIFEST}
        for t in tops:                                         # то, что заменится, — в сторону
            cur = root / t
            if cur.exists():
                shutil.move(str(cur), str(old / t))
        for n in zf.namelist():
            if n == MANIFEST or n.endswith("/"):
                continue
            dst = root / n
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(n) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
    z.unlink()
    (root / "restored_from.txt").write_text(str(old), encoding="utf-8")
    return str(old)


# ══════════════════════════ интерфейс ══════════════════════════ #

def settings_card(shell):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                                 QPushButton, QVBoxLayout)
    win = shell.win
    card = QFrame()
    card.setObjectName("YaWaveCard")
    card.setStyleSheet("QFrame#YaWaveCard{background:#171717;border-radius:16px;}")
    card.setMaximumWidth(760)
    v = QVBoxLayout(card)
    v.setContentsMargins(18, 14, 18, 14)
    t = QLabel("Резервная копия одним файлом")
    t.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
    v.addWidget(t)
    d = QLabel("Настройки, плейлисты, история, профиль и оформление, Echoes.NET (друзья, чаты — тот же аккаунт), "
               "темы. Перенос на новый ПК: поставьте ECHOES, «Восстановить из копии…», перезапустите. Сама музыка "
               "в копию не входит — добавьте папку с ней заново. Не запускайте один аккаунт на двух ПК сразу — "
               "для этого есть «Мои устройства» в Echoes.NET.")
    d.setObjectName("YaSub")
    d.setWordWrap(True)
    v.addWidget(d)
    opts = QHBoxLayout()
    c_th = QCheckBox("Темы")
    c_th.setChecked(True)
    c_fl = QCheckBox("Файлы из чатов")
    opts.addWidget(c_th)
    opts.addWidget(c_fl)
    opts.addStretch(1)
    v.addLayout(opts)
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    bar.hide()
    st = QLabel("")
    st.setObjectName("YaSub")
    h = QHBoxLayout()
    mk = QPushButton("Сделать копию…")
    mk.setObjectName("AccentBtn")
    rs = QPushButton("Восстановить из копии…")
    h.addWidget(mk)
    h.addWidget(rs)
    h.addStretch(1)
    v.addLayout(h)
    v.addWidget(bar)
    v.addWidget(st)
    prog = {"f": 0.0, "done": None}

    def do_make():
        size = estimate(DATA, c_th.isChecked(), c_fl.isChecked())
        fn, _ = QFileDialog.getSaveFileName(win, "Резервная копия ECHOES",
                                            str(Path.home() / "Desktop" / time.strftime("ECHOES %Y-%m-%d.echoesbackup")),
                                            "Копия ECHOES (*.echoesbackup)")
        if not fn:
            return
        mk.setEnabled(False)
        bar.show()
        st.setText(f"Собираю копию (~{size / 1048576:.0f} МБ до сжатия)…")
        try:
            win.save_settings()
        except Exception:                                      # noqa: BLE001
            pass

        def run():
            try:
                prog["done"] = ("ok", make(Path(fn), DATA, c_th.isChecked(), c_fl.isChecked(),
                                           lambda f: prog.__setitem__("f", f)))
            except Exception as e:                             # noqa: BLE001
                prog["done"] = ("err", str(e))
        threading.Thread(target=run, daemon=True).start()
        tm = QTimer(card)

        def poll():
            try:
                bar.setValue(int(prog["f"] * 1000))
                if prog["done"]:
                    tm.stop()
                    kind, res = prog["done"]
                    prog["done"] = None
                    mk.setEnabled(True)
                    bar.hide()
                    st.setText(f"Готово: {Path(fn).name} · {Path(fn).stat().st_size / 1048576:.1f} МБ, файлов: "
                               f"{res['files']}" if kind == "ok" else f"Не получилось: {res}")
            except RuntimeError:
                tm.stop()
        tm.timeout.connect(poll)
        tm.start(150)

    def do_restore():
        fn, _ = QFileDialog.getOpenFileName(win, "Восстановить ECHOES из копии", str(Path.home() / "Desktop"),
                                            "Копия ECHOES (*.echoesbackup *.zip)")
        if not fn:
            return
        man = check(Path(fn))
        if man is None:
            QMessageBox.warning(win, "Восстановление", "Это не копия ECHOES.")
            return
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(man.get("ts", 0)))
        if QMessageBox.question(win, "Восстановление",
                                f"Копия от {when} ({man.get('files')} файлов).\nОна заменит текущие настройки, плейлисты "
                                "и Echoes.NET при следующем запуске ECHOES (нынешние файлы сохранятся в отдельную "
                                "папку). Продолжить?") != QMessageBox.StandardButton.Yes:
            return
        schedule_restore(Path(fn))
        st.setText("Копия применится при следующем запуске — закройте ECHOES (через трей → «Выход») и откройте снова.")
    mk.clicked.connect(do_make)
    rs.clicked.connect(do_restore)
    return card
