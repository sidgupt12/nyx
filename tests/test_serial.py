"""Exercise pyserial against a real pseudo-terminal (not a physical ESP32)."""

import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from nyx.listener import Bridge, control
from nyx.protocol import encode
from tests.test_controller import event
from tests.test_listener import until


class SerialTests(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.folder = tempfile.TemporaryDirectory(dir="/tmp")
        self.path = Path(self.folder.name) / "nyx" / "bridge.sock"
        self.bridge = Bridge(os.ttyname(self.slave), path=self.path, manual_approvals=True)
        self.thread = threading.Thread(target=self.bridge.run)
        self.thread.start()
        until(lambda: control(path=self.path))
        # The serial driver flushes old bytes on connect, like a board booting.
        self.last_hello = 0
        until(self.hello)
        self.buffer = bytearray()

    def hello(self):
        if self.bridge.hardware.ready.is_set():
            return True
        if time.monotonic() - self.last_hello >= 0.5:
            os.write(self.master, encode({"v": 1, "type": "hello"}))
            self.last_hello = time.monotonic()
        return False

    def tearDown(self):
        control("stop", self.path)
        self.thread.join(4)
        os.close(self.master)
        os.close(self.slave)
        self.folder.cleanup()
        self.assertFalse(self.thread.is_alive())

    def read_state(self, predicate=lambda frame: True):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if select.select([self.master], [], [], 0.1)[0]:
                self.buffer.extend(os.read(self.master, 4096))
            while b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if frame.get("type") == "state" and predicate(frame):
                    return frame
        self.fail("No matching state frame received")

    def test_state_and_physical_action_use_same_request_id(self):
        request = self.bridge.controller.event(event(), True)
        frame = self.read_state(lambda f: f["status"] == "PERMISSION_REQUIRED")
        self.assertEqual(frame["view_id"], request.id)
        action = encode({"v": 1, "type": "action", "action": "reject", "view_id": frame["view_id"]})
        # Deliberately split a serial message across two reads.
        os.write(self.master, action[:12])
        time.sleep(0.02)
        os.write(self.master, action[12:])
        until(request.ready.is_set)
        self.assertEqual(request.decision, "deny")

    def test_full_hook_to_usb_to_hook_roundtrip(self):
        root = Path(__file__).resolve().parent.parent
        process = subprocess.Popen(
            [sys.executable, str(root / "nyx/hook.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "CODEX_HOME": self.folder.name},
        )
        try:
            process.stdin.write(json.dumps(event()))
            process.stdin.close()
            process.stdin = None
            frame = self.read_state(lambda f: f["status"] == "PERMISSION_REQUIRED")
            os.write(
                self.master,
                encode(
                    {"v": 1, "type": "action", "action": "approve", "view_id": frame["view_id"]}
                ),
            )
            stdout, stderr = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(
                json.loads(stdout)["hookSpecificOutput"]["decision"], {"behavior": "allow"}
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_garbage_and_oversized_frames_recover(self):
        os.write(self.master, b"x" * 4200 + b"\n[]\n")
        os.write(self.master, encode({"v": 1, "type": "heartbeat"}))
        self.assertEqual(self.read_state()["type"], "state")

    def test_wrong_protocol_cannot_approve(self):
        request = self.bridge.controller.event(event(), True)
        os.write(
            self.master,
            encode({"v": 999, "type": "action", "action": "approve", "view_id": request.id}),
        )
        time.sleep(0.15)
        self.assertFalse(request.ready.is_set())

    def test_missing_heartbeat_invalidates_request(self):
        request = self.bridge.controller.event(event(), True)

        def disconnected():
            if select.select([self.master], [], [], 0)[0]:
                os.read(self.master, 4096)
            return not self.bridge.hardware.ready.is_set()

        until(disconnected, timeout=4)
        self.assertTrue(request.ready.is_set())
        self.assertIsNone(request.decision)

    def test_reboot_cancels_old_approval_and_reconnects(self):
        request = self.bridge.controller.event(event(), True)
        os.write(self.master, encode({"v": 1, "type": "hello"}))
        until(request.ready.is_set)
        self.assertIsNone(request.decision)
        self.assertTrue(self.bridge.hardware.ready.is_set())
