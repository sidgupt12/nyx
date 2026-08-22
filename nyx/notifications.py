"""Notification side effects, kept separate from application state."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import threading
from typing import Any, Callable, Optional, Protocol


class NotificationSink(Protocol):
    def notify(self, title: str, message: str) -> None:
        """Deliver a user-facing notification."""


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class MacOSNotifier:
    """Use the built-in macOS notification center, with a safe no-op fallback."""

    def __init__(
        self,
        *,
        system_name: str | None = None,
        runner: Callable[..., object] | None = None,
    ) -> None:
        self.system_name = system_name or platform.system()
        self.runner = runner or subprocess.run

    def notify(self, title: str, message: str) -> None:
        if self.system_name != "Darwin":
            return
        script = (
            f'display notification "{_escape_applescript(message)}" '
            f'with title "{_escape_applescript(title)}"'
        )
        try:
            self.runner(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return


class ActionableMacOSNotifier:
    """Show actionable macOS alerts when ``alerter`` is present.

    The existing osascript notification remains the fallback so installation
    can be staged without making the listener unusable.
    """

    def __init__(
        self,
        *,
        system_name: str | None = None,
        alerter_path: str | None = None,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.system_name = system_name or platform.system()
        self.alerter_path = alerter_path or shutil.which("alerter")
        self.runner = runner or subprocess.run

    def notify_async(
        self,
        title: str,
        message: str,
        *,
        on_open: Callable[[], None],
    ) -> None:
        """Show a non-blocking alert with an optional Open Codex action.

        This is the safe hook path: Codex is never held waiting for a
        notification click. The native Codex prompt remains authoritative.
        """

        if self.system_name != "Darwin" or not self.alerter_path:
            MacOSNotifier(system_name=self.system_name).notify(title, message)
            return
        command = [
            self.alerter_path,
            "--title",
            title,
            "--message",
            message,
            "--actions",
            "Open Codex",
            "--close-label",
            "Close",
            "--json",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        except OSError:
            MacOSNotifier(system_name=self.system_name).notify(title, message)
            return

        def wait_for_action() -> None:
            stdout, _ = process.communicate()
            if _parse_alerter_action(stdout or "") == "open":
                try:
                    on_open()
                except OSError:
                    pass

        threading.Thread(target=wait_for_action, daemon=True).start()


def _parse_alerter_action(output: str) -> Optional[str]:
    """Normalize alerter's JSON/plain-text result across releases."""

    text = (output or "").strip()
    if not text:
        return None
    values = []
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            values.append(line)
            continue
        if isinstance(data, dict):
            values.extend(
                str(data.get(key, ""))
                for key in (
                    "action",
                    "actionIdentifier",
                    "event",
                    "result",
                    "activationValue",
                )
            )
        else:
            values.append(str(data))

    for value in values:
        normalized = value.strip().lower()
        if "open codex" in normalized or normalized == "open":
            return "open"
        if normalized in {"@timeout", "timeout", "@contentclicked"}:
            return None
    return None
