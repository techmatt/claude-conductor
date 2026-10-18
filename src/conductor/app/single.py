"""One app per user: a second launch hands its folders to the running one and exits.

The first app listens on a per-user `QLocalServer` (a named pipe on Windows, a socket
file elsewhere). A later `conductor app [<folder> …]` connects, sends one JSON line
`{"folders": [...]}` and exits; the running app adds those folders and comes forward.
Without this, each launch adds a tray icon, and each app rewrites the remembered list
from what it alone has open.
"""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from typing import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .. import procs

TIMEOUT_MS = 1000


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:                   # no user name in the environment
        user = "user"
    return "conductor-app-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)


def forward(folders: list[Path], name: str | None = None) -> bool:
    """Send `folders` to a running app. True when one took them (so this launch exits)."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(TIMEOUT_MS):
        return False
    procs.allow_foreground()            # let the running app raise its window
    sock.write((json.dumps({"folders": [str(f) for f in folders]}) + "\n").encode("utf-8"))
    sock.flush()
    ok = sock.waitForBytesWritten(TIMEOUT_MS) or sock.bytesToWrite() == 0
    sock.disconnectFromServer()
    return ok


def listen(on_folders: Callable[[list[Path]], None], name: str | None = None) -> QLocalServer | None:
    """Serve forwarded launches. None when the name can't be claimed (the app then runs
    alone, as before)."""
    name = name or server_name()
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        # A socket file left by an app that crashed (POSIX); forward() already found
        # nobody answering on it.
        QLocalServer.removeServer(name)
        if not server.listen(name):
            return None

    def accept() -> None:
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf) -> None:
                buf.extend(bytes(sock.readAll()))
                if b"\n" in buf:
                    line = bytes(buf).split(b"\n", 1)[0]
                    buf.clear()
                    try:
                        folders = [Path(f) for f in json.loads(line.decode("utf-8"))["folders"]]
                    except (ValueError, KeyError, TypeError):
                        folders = None
                    if folders is not None:
                        on_folders(folders)

            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                read()

    server.newConnection.connect(accept)
    return server
