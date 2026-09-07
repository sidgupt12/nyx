"""Integration tests use real local sockets and real hook subprocesses.

No Codex session, global config, physical USB board, or network is touched.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from nyx.controller import Controller
from nyx.listener import Bridge, control
from tests.test_controller import event

ROOT = Path(__file__).resolve().parent.parent


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class FakeHardware:
    def __init__(self):
        self.ready = threading.Event()
        self.ready.set()

    def start(self):
        pass

    def close(self):
        self.ready.clear()


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir="/tmp")
        self.path = Path(self.folder.name) / "nyx" / "bridge.sock"
        core = Controller(manual_approvals=True, approval_seconds=0.8)
        self.bridge = Bridge("unused", path=self.path, controller=core, native=False)
        self.bridge.hardware = FakeHardware()
        self.thread = threading.Thread(target=self.bridge.run)
        self.thread.start()
        until(lambda: control(path=self.path))

    def tearDown(self):
        control("stop", self.path)
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        self.folder.cleanup()

    def hook(self, payload):
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "nyx/hook.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "CODEX_HOME": self.folder.name},
        )
        process.stdin.write(json.dumps(payload))
        process.stdin.close()
        process.stdin = None
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def test_approve_and_reject_reach_real_hook_stdout(self):
        for action, behavior in [("approve", "allow"), ("reject", "deny")]:
            process = self.hook(event())
            request = until(lambda: next(iter(self.bridge.controller.pending.values()), None))
            self.bridge.action({"action": action, "view_id": request.id})
            stdout, stderr = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(
                json.loads(stdout)["hookSpecificOutput"]["decision"]["behavior"], behavior
            )

    def test_timeout_returns_empty_stdout_not_approval(self):
        process = self.hook(event())
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual((stdout, stderr, process.returncode), ("", "", 0))

    def test_disconnect_falls_back(self):
        process = self.hook(event())
        until(lambda: self.bridge.controller.pending)
        self.bridge.hardware.ready.clear()
        stdout, _ = process.communicate(timeout=2)
        self.assertEqual(stdout, "")
        until(lambda: not self.bridge.controller.pending)

    def test_canceled_hook_drops_pending_button(self):
        process = self.hook(event())
        request = until(lambda: next(iter(self.bridge.controller.pending.values()), None))
        process.terminate()
        process.communicate(timeout=2)
        until(lambda: request.ready.is_set())
        self.assertIsNone(request.decision)

    def test_passive_mode_has_no_wait(self):
        self.bridge.controller.manual_approvals = False
        process = self.hook(event())
        stdout, stderr = process.communicate(timeout=1)
        self.assertEqual((stdout, stderr), ("", ""))
        self.assertFalse(self.bridge.controller.pending)

    def test_socket_permissions_and_status(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(control(path=self.path)["device"])

    def test_second_bridge_cannot_replace_live_socket(self):
        duplicate = Bridge("unused", path=self.path)
        with self.assertRaises(RuntimeError):
            duplicate.start()
        duplicate.close()
        self.assertTrue(control(path=self.path)["ok"])

    def test_malformed_event_does_not_kill_bridge(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(self.path))
            connection.sendall(b"[]\n")
        self.assertTrue(control(path=self.path)["ok"])
