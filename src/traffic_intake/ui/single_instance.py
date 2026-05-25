"""QLocalServer/QLocalSocket-based single-instance enforcement.

Why we need this: the Traffic Intake app holds a Playwright persistent
Edge profile under %LOCALAPPDATA%\\traffic-intake\\edge-profile-* which
is single-writer, plus a keyring secret store and QSettings registry
entries that interleave badly across processes. A second double-click
without a lock will silently corrupt or stall — observed twice in
2026-05-14 / 2026-05-17. This module provides a cheap lock keyed on
the Windows username (so per-user installs on a shared machine don't
collide) and a hand-off path so a second launch can deliver its CLI
args (e.g., a dropped .eml) to the running instance instead of
spawning a duplicate.

Kept intentionally light: imports only `PySide6.QtCore`/`QtNetwork` so
the bootstrap can call it before the heavy app modules load.
"""
from __future__ import annotations

import os
from typing import Callable, Optional, Sequence

from PySide6.QtCore import QByteArray, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket


CONNECT_TIMEOUT_MS = 1000
WRITE_TIMEOUT_MS = 500
READ_TIMEOUT_MS = 1000


def instance_key(prefix: str = "traffic-intake-single-instance") -> str:
    """Stable per-user key for the local socket. Username is used (not SID)
    for simplicity — the failure mode of two users with the same username
    on one machine sharing the lock is acceptable and astronomically rare.
    """
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    return f"{prefix}-{user}"


def claim_single_instance(key: str) -> Optional[QLocalServer]:
    """Try to be the single running instance.

    Strategy: listen-first, then probe-on-conflict (more robust than
    probe-first, which can falsely succeed against a stale named pipe
    that Windows hasn't cleaned up yet after an abrupt process kill).

    1. Try to `listen(key)` directly. Success → we're the first instance.
    2. Listen failed: probe to distinguish real-running vs stale-socket.
       - Probe connects: a real instance is running → return None.
       - Probe fails: socket is stale → `removeServer(key)` + retry listen.
    """
    server = QLocalServer()
    if server.listen(key):
        return server

    # Listen failed. Could be a real running instance OR a stale socket.
    probe = QLocalSocket()
    probe.connectToServer(key)
    if probe.waitForConnected(CONNECT_TIMEOUT_MS):
        # Real instance is alive on this socket.
        probe.disconnectFromServer()
        return None

    # Probe couldn't connect → the registered socket is stale (e.g.,
    # previous instance crashed). Clear it and try listen again.
    QLocalServer.removeServer(key)
    if server.listen(key):
        return server
    return None


def send_to_running_instance(key: str, args: Sequence[str]) -> bool:
    """Open a client socket to the running instance and hand it `args`.

    Wire format: each arg on its own line, UTF-8, terminated with '\\n'.
    An empty payload (single '\\n') is sent when there are no args — that
    still signals "raise the window" to the running instance.
    """
    sock = QLocalSocket()
    sock.connectToServer(key)
    if not sock.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    payload = ("\n".join(args) + "\n").encode("utf-8") if args else b"\n"
    sock.write(QByteArray(payload))
    ok = sock.waitForBytesWritten(WRITE_TIMEOUT_MS)
    sock.disconnectFromServer()
    return bool(ok)


def install_secondary_launch_handler(
    server: QLocalServer,
    on_secondary_launch: Callable[[list[str]], None],
) -> QObject:
    """Wire the server's newConnection to a callback that receives the
    args sent by a secondary launch.

    Returns the connection-handling QObject so the caller can keep it
    alive (Qt connections via lambdas are fine, but keeping a reference
    to the handler around prevents GC of the closures).
    """

    class _Handler(QObject):
        def __init__(self, srv: QLocalServer):
            super().__init__()
            self._srv = srv
            srv.newConnection.connect(self._on_new_connection)

        def _on_new_connection(self) -> None:
            sock = self._srv.nextPendingConnection()
            if sock is None:
                return
            try:
                # Read all available bytes up to the timeout. The sender
                # is expected to send a single short payload and close.
                if not sock.waitForReadyRead(READ_TIMEOUT_MS):
                    return
                data = bytes(sock.readAll())
                text = data.decode("utf-8", errors="replace")
                args = [line for line in text.splitlines() if line.strip()]
                try:
                    on_secondary_launch(args)
                except Exception:
                    # Don't let a callback error kill the running instance.
                    pass
            finally:
                sock.disconnectFromServer()
                sock.deleteLater()

    return _Handler(server)
