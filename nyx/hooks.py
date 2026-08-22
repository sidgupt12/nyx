"""Codex PermissionRequest hook entry point and local socket client."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, TextIO

from .listener import SOCKET_PATH
from .notifications import MacOSNotifier, NotificationSink


def send_event(
    payload: Mapping[str, Any],
    *,
    socket_path: Path = SOCKET_PATH,
    timeout: float = 0.75,
) -> Optional[dict]:
    """Send one event and return immediately without waiting for the listener."""

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(Path(socket_path).expanduser()))
            connection.sendall(json.dumps(dict(payload)).encode("utf-8") + b"\n")
            return {"ok": True}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None


def run_hook(
    *,
    stdin: TextIO = sys.stdin,
    socket_path: Path = SOCKET_PATH,
    notifier: Optional[NotificationSink] = None,
) -> int:
    """Forward Codex's JSON input without returning a permission decision.

    If the background process is not running, notifying directly keeps the
    integration useful while the user is setting it up.
    """

    try:
        payload = json.load(stdin)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "PermissionRequest":
        return 0
    payload = dict(payload)
    payload["_nyx"] = {
        "term_program": os.environ.get("TERM_PROGRAM"),
        "term_session_id": os.environ.get("TERM_SESSION_ID"),
        "iterm_session_id": os.environ.get("ITERM_SESSION_ID"),
        "tty": _current_tty(),
    }
    sent = send_event(payload, socket_path=socket_path, timeout=0.75)
    if sent is not None:
        return 0
    (notifier if notifier is not None else MacOSNotifier()).notify(
        "Codex needs permission",
        _fallback_message(payload),
    )
    return 0


def _current_tty() -> Optional[str]:
    try:
        output = subprocess.check_output(
            ["ps", "-o", "tty=", "-p", str(os.getppid())],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return None
    tty = output.strip()
    return tty if tty and tty != "??" else None


def _fallback_message(payload: Mapping[str, Any]) -> str:
    from .listener import permission_message

    return permission_message(payload)


def main() -> None:
    raise SystemExit(run_hook())


if __name__ == "__main__":
    main()
