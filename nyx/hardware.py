"""USB JSON-lines transport. Reconnects without replaying button actions."""

import json
import logging
import threading
import time
import serial

from .protocol import MAX_LINE, VERSION, encode

LOG = logging.getLogger(__name__)


class SerialHardware:
    def __init__(self, port, snapshot, action, disconnected):
        self.port = port
        self.snapshot = snapshot
        self.action = action
        self.disconnected = disconnected
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="nyx-usb")
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.ready.clear()
        self.disconnected()
        if self.thread:
            self.thread.join(timeout=2)

    def run(self):
        while not self.stopped.is_set():
            try:
                with serial.Serial(
                    self.port, 115200, timeout=0.1, write_timeout=0.5, exclusive=True
                ) as device:
                    device.reset_input_buffer()
                    self._connected(device)
            except (OSError, serial.SerialException) as exc:
                LOG.warning("USB unavailable (%s); retrying", exc)
            finally:
                self.ready.clear()
                self.disconnected()
            self.stopped.wait(1)

    def _connected(self, device):
        buffer = bytearray()
        dropping = False
        heartbeat = time.monotonic()
        sent = 0.0
        while not self.stopped.is_set():
            for byte in device.read(device.in_waiting or 1):
                if byte != 10:
                    if not dropping:
                        buffer.append(byte)
                        if len(buffer) > MAX_LINE:
                            buffer.clear()
                            dropping = True
                    continue
                if dropping:
                    dropping = False
                    continue
                try:
                    message = json.loads(buffer)
                except (ValueError, UnicodeDecodeError):
                    message = {}
                buffer.clear()
                if not isinstance(message, dict) or message.get("v") != VERSION:
                    continue
                if message.get("type") == "hello":
                    self.disconnected()  # Reboot invalidates previous requests.
                    heartbeat = time.monotonic()
                    self.ready.set()
                elif message.get("type") == "heartbeat" and self.ready.is_set():
                    heartbeat = time.monotonic()
                elif message.get("type") == "action" and self.ready.is_set():
                    result = self.action(message)
                    device.write(encode({"v": VERSION, "type": "result", **result}))
            now = time.monotonic()
            if now - heartbeat > 3:
                raise serial.SerialException("device heartbeat timed out")
            if self.ready.is_set() and now - sent >= 0.25:
                device.write(encode(self.snapshot()))
                sent = now
