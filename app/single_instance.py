# single_instance.py
"""
Один ECHOES на пользователя.

Плеер теперь прячется в трей, и повторный запуск ярлыка открывал бы второй плеер (два звука,
два узла Echoes.NET на одном ключе). Первый запуск слушает локальный канал; второй находит его,
просит «покажись» и выходит.
"""
from __future__ import annotations

import hashlib
import os

from PyQt6.QtNetwork import QLocalServer, QLocalSocket


def _name() -> str:
    home = os.path.expanduser("~")
    return "ECHOES-player-" + hashlib.sha1(home.lower().encode("utf-8", "replace")).hexdigest()[:16]


def claim():
    """Сервер, если мы первые, или None — плеер уже запущен (его окно попросили показаться)."""
    name = _name()
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(400):
        sock.write(b"show\n")
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(name)                     # канал от упавшего плеера
    server = QLocalServer()
    server.listen(name)
    return server


def serve(server, on_show):
    """Повторный запуск → on_show() в уже открытом плеере."""
    if server is None:
        return

    def _new():
        while server.hasPendingConnections():
            c = server.nextPendingConnection()
            c.readyRead.connect(lambda c=c: (c.readAll(), on_show()))
            c.disconnected.connect(c.deleteLater)
    server.newConnection.connect(_new)
