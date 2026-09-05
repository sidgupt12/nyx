"""Shared constants and bounded JSON lines. No serial or Codex logic here."""

import json
import os
import socket
from pathlib import Path

VERSION = 1
MAX_LINE = 4096
MAX_HOOK = 256 * 1024
APPROVAL_SECONDS = 20
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "Interrupt",
    "SessionEnd",
)


def runtime_dir():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "nyx"


def socket_path():
    return runtime_dir() / "bridge.sock"


def encode(message):
    # ASCII escaping keeps the firmware's input byte limit predictable.
    return (json.dumps(message, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def receive(connection, limit=MAX_HOOK):
    """Read exactly one object without consuming the next JSON line."""
    data = bytearray()
    while len(data) <= limit:
        chunk = connection.recv(min(4096, limit + 1 - len(data)), socket.MSG_PEEK)
        if not chunk:
            raise ValueError("connection closed before newline")
        # Peek first so a wait acknowledgement and fast decision can arrive
        # together without losing the second message.
        count = chunk.index(b"\n") + 1 if b"\n" in chunk else len(chunk)
        chunk = connection.recv(count)
        data.extend(chunk)
        if b"\n" in data:
            line = data.split(b"\n", 1)[0]
            if len(line) > limit:
                break
            result = json.loads(line)
            if not isinstance(result, dict):
                raise ValueError("expected object")
            return result
    raise ValueError("message too large")
