"""Follow terminal sessions on a shared Codex app-server.

Uses WebSocket messages over a private Unix socket. Never starts a daemon, thread, or
turn. Users connect their normal terminal UI with `codex --remote unix://`.
"""

import json
import logging
import os
import queue
import stat
import time

from websockets.sync.client import unix_connect
from websockets.exceptions import WebSocketException

from .native import METHODS, Source, response_for
from .protocol import runtime_dir

LOG = logging.getLogger(__name__)
MAX_LINE = 8 * 1024 * 1024


class AppServer(Source):
    name = "terminal"

    def __init__(self, hub, path=None):
        super().__init__(hub)
        self.path = path or runtime_dir().parent / "app-server-control" / "app-server-control.sock"
        self.sequence = 0
        self.pending = {}
        self.requests = {}
        self.joined = set()

    def send(self, message):
        self.connection.send(json.dumps(message, separators=(",", ":")))

    def call(self, method, params, context=None):
        self.sequence += 1
        self.pending[self.sequence] = (method, context, time.monotonic())
        self.send({"id": self.sequence, "method": method, "params": params})

    def run(self):
        while not self.stopped.is_set():
            try:
                info = self.path.stat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise ValueError("app-server socket unavailable or wrong owner")
                with unix_connect(
                    str(self.path),
                    open_timeout=3,
                    close_timeout=1,
                    max_size=MAX_LINE,
                    proxy=None,
                    compression=None,
                ) as self.connection:
                    self.call("initialize", {"clientInfo": {"name": "nyx", "version": "0.1.0"}})
                    self.loop()
            except (OSError, ValueError, KeyError, TypeError, WebSocketException) as exc:
                LOG.debug("Terminal server unavailable: %s", exc)
            finally:
                self.pending.clear()
                self.requests.clear()
                self.joined.clear()
                self.reset()
            self.stopped.wait(2)

    def loop(self):
        checked = 0
        while not self.stopped.is_set():
            try:
                self.message(json.loads(self.connection.recv(timeout=0.1)))
            except TimeoutError:
                pass
            now = time.monotonic()
            if any(now - started > 10 for _, _, started in self.pending.values()):
                raise ValueError("app-server response timeout")
            if self.connected and now - checked > 1:
                self.call("thread/loaded/list", {"limit": 100})
                checked = now
            if self.connected:
                self.decisions()

    def message(self, message):
        method = message.get("method")
        if method in METHODS and "id" in message:
            sid = message["params"]["threadId"]
            if sid in self.joined:
                self.requests.setdefault(sid, {})[message["id"]] = message
                self.hub.update(self, sid, list(self.requests[sid].values()))
            return
        if method == "serverRequest/resolved":
            params = message["params"]
            sid = params["threadId"]
            self.requests.get(sid, {}).pop(params["requestId"], None)
            self.hub.update(self, sid, list(self.requests.get(sid, {}).values()))
            return
        if method == "thread/status/changed":
            params = message["params"]
            self.hub.controller.native_state(
                params["threadId"], self.name, {"threadRuntimeStatus": params["status"]}
            )
            if params["status"].get("type") != "active":
                self.requests.pop(params["threadId"], None)
                self.hub.update(self, params["threadId"], [])
            return
        if method == "thread/closed":
            sid = message["params"]["threadId"]
            self.joined.discard(sid)
            self.requests.pop(sid, None)
            self.hub.update(self, sid, [])
            return
        pending = self.pending.pop(message.get("id"), None)
        if pending is None:
            return
        method, context, _ = pending
        if "error" in message:
            if method == "initialize":
                raise ValueError("app-server initialize rejected")
            if method == "thread/resume":
                self.joined.discard(context)
            LOG.debug("App-server %s rejected", method)
            return
        result = message["result"]
        if method == "initialize":
            self.send({"method": "initialized", "params": {}})
            self.connected = True
        elif method == "thread/loaded/list":
            # Only rejoin sessions known through hooks AND already in this
            # server. Never load a different copy of a standalone CLI session.
            known = self.hub.controller.session_payloads()
            for sid in result["data"]:
                if sid in known and sid not in self.joined:
                    self.joined.add(sid)
                    self.call("thread/resume", {"threadId": sid}, sid)
            if result.get("nextCursor"):
                self.call("thread/loaded/list", {"limit": 100, "cursor": result["nextCursor"]})
        elif method == "thread/resume":
            thread = result["thread"]
            self.hub.controller.native_state(
                context,
                self.name,
                {
                    "threadRuntimeStatus": thread.get("status"),
                    "latestModel": result.get("model"),
                    "latestReasoningEffort": result.get("reasoningEffort"),
                },
            )

    def decisions(self):
        while True:
            try:
                offer, action = self.actions.get_nowait()
            except queue.Empty:
                return
            requests = self.requests.get(offer.session_id, {})
            request = requests.get(offer.request_id)
            if request is None or request.get("method") != offer.method:
                continue
            response = response_for(request, action)
            if response is None:
                continue
            self.send({"id": offer.request_id, "result": response})
            requests.pop(offer.request_id, None)
            self.hub.update(self, offer.session_id, list(requests.values()))
