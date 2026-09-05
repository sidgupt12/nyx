"""Private local socket server connecting short-lived hooks to the controller."""

import fcntl
import logging
import os
import select
import socket
import threading
import time

from .controller import Controller
from .hardware import SerialHardware
from .opener import open_session
from .protocol import EVENTS, encode, receive, socket_path

LOG = logging.getLogger(__name__)


class Bridge:
    def __init__(self, port, *, path=None, manual_approvals=False, controller=None):
        self.path = path or socket_path()
        self.controller = controller or Controller(manual_approvals=manual_approvals)
        self.stopped = threading.Event()
        self.workers = threading.BoundedSemaphore(24)
        self.hardware = SerialHardware(
            port, self.controller.snapshot, self.action, self.controller.cancel_all
        )
        self.server = None
        self.lock_file = None

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = open(self.path.with_suffix(".lock"), "a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("Nyx is already running")
        # Only the lock owner may replace a stale socket.
        self.path.unlink(missing_ok=True)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.server.listen(24)
        self.server.settimeout(0.25)
        self.hardware.start()

    def run(self):
        try:
            self.start()
            LOG.info("Nyx bridge ready; manual approvals: %s", self.controller.manual_approvals)
            while not self.stopped.is_set():
                try:
                    connection, _ = self.server.accept()
                except socket.timeout:
                    continue
                if not self.workers.acquire(blocking=False):
                    connection.close()
                    continue
                threading.Thread(target=self.handle, args=(connection,), daemon=True).start()
        finally:
            self.close()

    def close(self):
        self.stopped.set()
        self.controller.cancel_all()
        self.hardware.close()
        if self.server:
            self.server.close()
            self.server = None
        if self.lock_file:
            self.path.unlink(missing_ok=True)
            self.lock_file.close()
            self.lock_file = None

    def handle(self, connection):
        request = None
        try:
            with connection:
                connection.settimeout(1)
                payload = receive(connection)
                if payload.get("type") == "status":
                    connection.sendall(
                        encode(
                            {
                                "ok": True,
                                "device": self.hardware.ready.is_set(),
                                "manual_approvals": self.controller.manual_approvals,
                                "pid": os.getpid(),
                            }
                        )
                    )
                    return
                if payload.get("type") == "stop":
                    connection.sendall(encode({"ok": True}))
                    self.stopped.set()
                    return
                if payload.get("hook_event_name") not in EVENTS:
                    return
                request = self.controller.event(payload, self.hardware.ready.is_set())
                connection.sendall(encode({"wait": request is not None}))
                if request is None:
                    return
                while not request.ready.wait(0.05):
                    if self.stopped.is_set() or not self.hardware.ready.is_set():
                        break
                    if time.monotonic() >= request.deadline:
                        break
                    # A canceled/killed hook must invalidate its physical button.
                    readable, _, _ = select.select([connection], [], [], 0)
                    if readable:
                        break
                connection.sendall(encode({"decision": request.decision}))
        except (OSError, ValueError, TypeError):
            LOG.debug("Hook disconnected or sent an invalid event")
        finally:
            if request:
                self.controller.finish(request)
            self.workers.release()

    def action(self, message):
        result, payload = self.controller.action(message)
        if payload is not None:
            # Window automation must never block the USB heartbeat.
            threading.Thread(target=open_session, args=(payload,), daemon=True).start()
            result["status"] = "open_requested"
        return result


def control(command="status", path=None):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.5)
            connection.connect(str(path or socket_path()))
            connection.sendall(encode({"type": command}))
            return receive(connection)
    except (OSError, ValueError):
        return None
