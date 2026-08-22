"""The passive background listener for real Codex permission events."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

from .notifications import ActionableMacOSNotifier, MacOSNotifier, NotificationSink


SOCKET_PATH = Path.home() / ".codex" / "nyx.sock"
MAX_EVENT_BYTES = 256 * 1024


def _short(value: Any, limit: int = 180) -> str:
    text = str(value).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def permission_message(payload: Mapping[str, Any]) -> str:
    """Turn Codex's hook payload into a compact notification body."""

    tool_name = str(payload.get("tool_name") or "permission request")
    tool_input = payload.get("tool_input")
    detail: Optional[str] = None
    if isinstance(tool_input, Mapping):
        for key in ("command", "cmd", "path", "file_path", "description"):
            value = tool_input.get(key)
            if value:
                detail = _short(value)
                break
    elif tool_input:
        detail = _short(tool_input)

    if detail:
        return f"{tool_name}: {detail}"
    return f"Codex requested permission for {tool_name}"


class BackgroundListener:
    """Receive hook events over a private local Unix socket.

    The listener never launches Codex or blocks a Codex hook. Open Codex is
    handled asynchronously; the terminal's native prompt remains authoritative.
    """

    def __init__(
        self,
        *,
        socket_path: Path = SOCKET_PATH,
        notifier: Optional[NotificationSink] = None,
    ) -> None:
        self.socket_path = Path(socket_path).expanduser()
        self.notifier = notifier if notifier is not None else ActionableMacOSNotifier()
        self._server: Optional[socket.socket] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._server is not None:
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if listener_is_running(self.socket_path):
            raise RuntimeError(f"a listener is already running at {self.socket_path}")
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            server.listen(16)
            server.settimeout(0.5)
        except BaseException:
            server.close()
            raise
        self._server = server
        self._stop.clear()

    def serve_forever(self) -> None:
        self.start()
        assert self._server is not None
        print(f"Nyx listener is running at {self.socket_path}", flush=True)
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                raise
            threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                daemon=True,
            ).start()

    def _serve_connection(self, connection: socket.socket) -> None:
        with connection:
            self._handle_connection(connection)

    def stop(self) -> None:
        self._stop.set()
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _handle_connection(self, connection: socket.socket) -> None:
        connection.settimeout(1.0)
        data = bytearray()
        while len(data) < MAX_EVENT_BYTES:
            try:
                chunk = connection.recv(min(4096, MAX_EVENT_BYTES - len(data)))
            except (socket.timeout, OSError):
                return
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
        line = bytes(data).split(b"\n", 1)[0]
        try:
            payload = json.loads(line.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("event must be a JSON object")
            if payload.get("hook_event_name") != "PermissionRequest":
                raise ValueError("only PermissionRequest events are supported")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            self._reply(connection, {"ok": False, "error": "invalid event"})
            return

        title = "Codex needs permission"
        message = permission_message(payload)
        try:
            async_notify = getattr(self.notifier, "notify_async", None)
            if callable(async_notify):
                async_notify(
                    title,
                    message,
                    on_open=lambda: open_codex_session(payload),
                )
                self._reply(connection, {"ok": True})
                return
            # A notification is a side effect, not part of Codex's approval
            # path. The simple test notifier returns immediately.
            self.notifier.notify(title, message)
            self._reply(connection, {"ok": True})
        except Exception:
            # A broken notification provider must never stop the listener.
            self._reply(connection, {"ok": True})

    @staticmethod
    def _reply(connection: socket.socket, payload: Mapping[str, Any]) -> None:
        try:
            connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        except OSError:
            return


def open_codex_session(payload: Mapping[str, Any]) -> None:
    """Bring the terminal that owns this Codex turn to the foreground."""

    metadata = payload.get("_nyx")
    if not isinstance(metadata, Mapping):
        metadata = {}
    terminal = str(metadata.get("term_program") or "").lower()
    tty = str(metadata.get("tty") or "")
    iterm_session_id = str(metadata.get("iterm_session_id") or "")
    if "apple_terminal" in terminal and tty:
        escaped_tty = tty.replace("\\", "\\\\").replace('"', '\\"')
        script = f'''tell application "Terminal"
repeat with aWindow in windows
repeat with aTab in tabs of aWindow
if (tty of aTab as text) is "{escaped_tty}" then
set selected tab of aWindow to aTab
set index of aWindow to 1
activate
return
end if
end repeat
end repeat
activate
end tell'''
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
        return
    if "iterm" in terminal and iterm_session_id:
        target = iterm_session_id.rsplit(":", 1)[-1]
        escaped_target = target.replace("\\", "\\\\").replace('"', '\\"')
        script = f'''tell application "iTerm2"
repeat with aWindow in windows
repeat with aTab in tabs of aWindow
repeat with aSession in sessions of aTab
if (unique id of aSession as text) is "{escaped_target}" then
select aSession
select aTab
set index of aWindow to 1
activate
return
end if
end repeat
end repeat
end repeat
activate
end tell'''
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
        return
    app_name = "Terminal"
    if "iterm" in terminal:
        app_name = "iTerm"
    elif "vscode" in terminal:
        app_name = "Visual Studio Code"
    elif "ghostty" in terminal:
        app_name = "Ghostty"
    elif "wezterm" in terminal:
        app_name = "WezTerm"
    subprocess.run(["open", "-a", app_name], check=False, capture_output=True)


def listener_is_running(socket_path: Path = SOCKET_PATH) -> bool:
    """Return whether the listener socket accepts a health-check message."""

    path = Path(socket_path).expanduser()
    if not path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.25)
            connection.connect(str(path))
            connection.sendall(b'{"hook_event_name":"health-check"}\n')
            return bool(connection.recv(128))
    except OSError:
        return False
