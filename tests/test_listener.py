import json
import io
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from nyx.hooks import run_hook
from nyx.listener import BackgroundListener, permission_message


class RecordingNotifier:
    def __init__(self):
        self.calls = []

    def notify(self, title, message):
        self.calls.append((title, message))


def sample_event():
    return {
        "hook_event_name": "PermissionRequest",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "cwd": "/tmp/project",
        "model": "gpt-5.6-luna",
        "permission_mode": "default",
        "tool_name": "shell",
        "tool_input": {"command": "npm install"},
        "transcript_path": None,
    }


class ListenerTests(unittest.TestCase):
    def test_permission_message_prefers_command(self):
        self.assertEqual(permission_message(sample_event()), "shell: npm install")

    def test_socket_event_becomes_notification(self):
        notifier = RecordingNotifier()
        listener = BackgroundListener(notifier=notifier)
        sender, receiver = socket.socketpair()
        try:
            thread = threading.Thread(target=listener._handle_connection, args=(receiver,))
            thread.start()
            sender.sendall(json.dumps(sample_event()).encode("utf-8") + b"\n")
            self.assertEqual(json.loads(sender.recv(128).decode("utf-8")), {"ok": True})
            thread.join(timeout=2)
            self.assertEqual(notifier.calls, [("Codex needs permission", "shell: npm install")])
        finally:
            sender.close()
            receiver.close()

    def test_hook_falls_back_to_notification_without_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            notifier = RecordingNotifier()
            result = run_hook(
                stdin=io.StringIO(json.dumps(sample_event())),
                socket_path=Path(directory) / "missing.sock",
                notifier=notifier,
            )
            self.assertEqual(result, 0)
            self.assertEqual(notifier.calls[0][0], "Codex needs permission")

    def test_hook_ignores_other_events(self):
        with tempfile.TemporaryDirectory() as directory:
            notifier = RecordingNotifier()
            event = sample_event()
            event["hook_event_name"] = "SessionStart"
            result = run_hook(
                stdin=io.StringIO(json.dumps(event)),
                socket_path=Path(directory) / "missing.sock",
                notifier=notifier,
            )
            self.assertEqual(result, 0)
            self.assertEqual(notifier.calls, [])

    def test_hook_returns_without_waiting_for_notification_action(self):
        output = io.StringIO()
        with patch("nyx.hooks.send_event", return_value={"ok": True}):
            with redirect_stdout(output):
                result = run_hook(stdin=io.StringIO(json.dumps(sample_event())))
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
