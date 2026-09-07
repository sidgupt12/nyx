"""Adapter for the installed desktop app's private local follower protocol.

Version-dependent: incompatible messages disable controls, never fall back to
keystrokes. This independently written client does not modify the desktop app.
"""

import copy
import json
import logging
import os
import queue
import socket
import stat
import struct
import time
import uuid

from .native import METHODS, Source, decision_for
from .protocol import runtime_dir

LOG = logging.getLogger(__name__)
MAX_FRAME = 32 * 1024 * 1024
FIELDS = {"requests", "latestModel", "latestReasoningEffort", "threadRuntimeStatus", "cwd"}


def patch_fields(state, patches):
    """Apply Immer patches only to the small fields Nyx needs (no chat text)."""
    result = copy.deepcopy(state)
    for patch in patches:
        path = patch["path"]
        if not isinstance(path, list) or not path:
            raise ValueError("unsupported patch path")
        if path[0] not in FIELDS:
            continue
        node = result
        for part in path[:-1]:
            node = node[part]
        key, op = path[-1], patch["op"]
        if op == "remove":
            if isinstance(node, list):
                node.pop(key)
            else:
                del node[key]
        elif op in {"add", "replace"}:
            value = copy.deepcopy(patch["value"])
            if isinstance(node, list) and op == "add":
                node.insert(key, value)
            else:
                node[key] = value
        else:
            raise ValueError("unsupported patch operation")
    return result


class Desktop(Source):
    name = "desktop"

    def __init__(self, hub, path=None):
        super().__init__(hub)
        self.path = path or runtime_dir().parent / "ipc" / "ipc.sock"
        self.states = {}
        self.revisions = {}
        self.owners = {}

    def send(self, message):
        data = json.dumps(message, separators=(",", ":")).encode()
        self.sock.sendall(struct.pack("<I", len(data)) + data)

    def follow(self, session_id, following=True):
        self.send(
            {
                "type": "broadcast",
                "sourceClientId": self.client_id,
                "version": 1,
                "method": "thread-stream-following-changed",
                "params": {"conversationId": session_id, "hostId": "local", "following": following},
            }
        )

    def run(self):
        while not self.stopped.is_set():
            try:
                info = self.path.stat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise ValueError("desktop socket ownership mismatch")
                with socket.socket(socket.AF_UNIX) as self.sock:
                    self.sock.settimeout(0.25)
                    self.sock.connect(str(self.path))
                    self.client_id = "initializing-client"
                    self.send(
                        {
                            "type": "request",
                            "requestId": str(uuid.uuid4()),
                            "sourceClientId": self.client_id,
                            "version": 0,
                            "method": "initialize",
                            "params": {"clientType": "nyx"},
                        }
                    )
                    self.loop()
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                LOG.debug("Desktop connection unavailable: %s", exc)
            finally:
                self.states.clear()
                self.revisions.clear()
                self.owners.clear()
                self.reset()
            self.stopped.wait(2)

    def loop(self):
        buffer = bytearray()
        followed = set()
        checked = 0
        started = time.monotonic()
        while not self.stopped.is_set():
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    return
                buffer.extend(chunk)
            except socket.timeout:
                pass
            while len(buffer) >= 4:
                size = struct.unpack("<I", buffer[:4])[0]
                if not 0 < size <= MAX_FRAME:
                    raise ValueError("invalid desktop frame size")
                if len(buffer) < size + 4:
                    break
                message = json.loads(buffer[4 : 4 + size])
                del buffer[: 4 + size]
                self.message(message)
            if not self.connected:
                if time.monotonic() - started > 3:
                    raise ValueError("desktop initialization timeout")
                continue
            if time.monotonic() - checked >= 1:
                sessions = self.hub.controller.session_payloads()
                for sid in sessions.keys() - followed:
                    self.follow(sid)
                for sid in followed - sessions.keys():
                    self.follow(sid, False)
                    self.states.pop(sid, None)
                    self.hub.update(self, sid, [])
                followed = set(sessions)
                checked = time.monotonic()
            self.decisions()

    def message(self, message):
        kind = message.get("type")
        if kind == "client-discovery-request":
            self.send(
                {
                    "type": "client-discovery-response",
                    "requestId": message["requestId"],
                    "response": {"canHandle": False},
                }
            )
        elif kind == "response" and message.get("method") == "initialize":
            self.client_id = message["result"]["clientId"]
            self.connected = True
        elif kind == "response" and message.get("resultType") == "error":
            LOG.warning("Desktop decision not confirmed: %s", message.get("error"))
        elif kind == "broadcast" and message.get("method") == "client-status-changed":
            params = message.get("params", {})
            if params.get("status") == "disconnected":
                for sid, owner in list(self.owners.items()):
                    if owner == params.get("clientId"):
                        self.hub.update(self, sid, [])
                        self.states.pop(sid, None)
                        self.revisions.pop(sid, None)
                        self.owners.pop(sid, None)
                        self.follow(sid)
        elif kind == "broadcast" and message.get("method") == "thread-stream-state-changed":
            if message.get("version") != 11:
                raise ValueError("desktop state protocol changed")
            params = message["params"]
            if params.get("hostId") != "local":
                return
            sid, change = params["conversationId"], params["change"]
            if sid not in self.hub.controller.session_payloads():
                return
            if change["type"] == "snapshot":
                state = {k: v for k, v in change["conversationState"].items() if k in FIELDS}
                self.owners[sid] = message["sourceClientId"]
            elif change["type"] == "patches":
                if (
                    sid not in self.states
                    or change["baseRevision"] != self.revisions[sid]
                    or self.owners.get(sid) != message["sourceClientId"]
                ):
                    # Drop stale buttons and ask the owner for a fresh snapshot.
                    self.hub.update(self, sid, [])
                    self.follow(sid, False)
                    self.follow(sid)
                    return
                state = patch_fields(self.states[sid], change["patches"])
            else:
                raise ValueError("unknown desktop state change")
            self.states[sid] = state
            self.revisions[sid] = change["revision"]
            self.hub.controller.native_state(sid, self.name, state)
            self.hub.update(self, sid, state.get("requests", []))

    def decisions(self):
        while True:
            try:
                offer, decision = self.actions.get_nowait()
            except queue.Empty:
                return
            requests = self.states.get(offer.session_id, {}).get("requests", [])
            request = next(
                (
                    r
                    for r in requests
                    if r.get("id") == offer.request_id and r.get("method") == offer.method
                ),
                None,
            )
            if request is None:
                continue
            decision = decision_for(request, decision)
            if decision is None:
                continue
            self.send(
                {
                    "type": "request",
                    "requestId": str(uuid.uuid4()),
                    "sourceClientId": self.client_id,
                    "targetClientId": self.owners[offer.session_id],
                    "version": 1,
                    "timeoutMs": 2000,
                    "method": METHODS[offer.method],
                    "params": {
                        "conversationId": offer.session_id,
                        "requestId": offer.request_id,
                        "decision": decision,
                    },
                }
            )
