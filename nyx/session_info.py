"""Small, bounded reader for optional session display metadata.

Hooks are the primary state source. Desktop builds do not always deliver Stop,
so the bridge may also read lifecycle metadata from the transcript path Codex
provides. Unknown formats simply produce no update.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from .protocol import runtime_dir

TAIL_BYTES = 8 * 1024 * 1024
LINE_BYTES = 128 * 1024
LIVENESS_GRACE_SECONDS = 3.0

ADJECTIVES = (
    "Astro",
    "Chrome",
    "Cosmic",
    "Dream",
    "Electric",
    "Glitch",
    "Lunar",
    "Midnight",
    "Neon",
    "Nova",
    "Pixel",
    "Quantum",
    "Solar",
    "Turbo",
    "Velvet",
    "Wild",
)
CREATURES = (
    "Cobra",
    "Comet",
    "Dragon",
    "Echo",
    "Fox",
    "Gecko",
    "Lynx",
    "Moth",
    "Orbit",
    "Owl",
    "Panther",
    "Phantom",
    "Raven",
    "Spark",
    "Viper",
    "Wolf",
)


@dataclass(frozen=True)
class SessionInfo:
    surface: str = ""
    status: str = ""
    model: str = ""
    effort: str = ""
    approvals_reviewer: str = ""


def funky_name(session_id):
    """Return a short, stable two-word alias for a session."""
    digest = hashlib.blake2s(str(session_id).encode(), digest_size=2).digest()
    return f"{ADJECTIVES[digest[0] % len(ADJECTIVES)]} {CREATURES[digest[1] % len(CREATURES)]}"


def terminal_surface(payload):
    metadata = payload.get("_nyx", {})
    if not isinstance(metadata, dict):
        return ""
    markers = (
        metadata.get("term_program"),
        metadata.get("tty"),
        metadata.get("iterm_session_id"),
        metadata.get("vscode_pid"),
    )
    return "TERM" if any(markers) else ""


def process_alive(pid):
    """Check process existence without sending a real signal or launching ps."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Fail open: an unexpected OS error must not hide a live session.
        return True
    return True


def rollout_path(payload, *, sessions_dir=None):
    """Resolve only this session's file inside the local sessions directory."""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        return None
    root = Path(sessions_dir or runtime_dir().parent / "sessions").resolve()
    candidates = []
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        candidates.append(Path(transcript))
    try:
        candidates.extend(root.glob(f"**/rollout-*-{session_id}.jsonl"))
    except OSError:
        return None
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def inspect_rollout(payload, *, sessions_dir=None):
    """Read a bounded tail and return only lifecycle/display fields."""
    path = rollout_path(payload, sessions_dir=sessions_dir)
    if path is None:
        return SessionInfo()
    session_id = payload.get("session_id")
    surface = status = model = effort = approvals_reviewer = ""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            # Origin lives in the first record and may be far outside the tail.
            header = handle.readline(LINE_BYTES + 1)
            if len(header) <= LINE_BYTES:
                try:
                    record = json.loads(header)
                    data = record.get("payload", {}) if isinstance(record, dict) else {}
                    if (
                        isinstance(record, dict)
                        and record.get("type") == "session_meta"
                        and isinstance(data, dict)
                        and data.get("id") == session_id
                    ):
                        originator = data.get("originator")
                        if originator in {"codex_work_desktop", "Codex Desktop"}:
                            surface = "APP"
                        elif originator == "codex-tui":
                            surface = "TERM"
                except (UnicodeDecodeError, ValueError):
                    pass
            start = max(0, size - TAIL_BYTES)
            handle.seek(start)
            if start:
                handle.readline(LINE_BYTES + 1)
            while handle.tell() <= size:
                line = handle.readline(LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > LINE_BYTES:
                    continue
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                kind = record.get("type")
                data = record.get("payload")
                if not isinstance(data, dict):
                    continue
                if kind == "turn_context":
                    if isinstance(data.get("model"), str):
                        model = data["model"]
                    if isinstance(data.get("effort"), str):
                        effort = data["effort"]
                    if isinstance(data.get("approvals_reviewer"), str):
                        approvals_reviewer = data["approvals_reviewer"]
                elif kind == "event_msg":
                    event = data.get("type")
                    if event == "task_started":
                        status = "RUNNING"
                    elif event in {"task_complete", "turn_aborted"}:
                        status = "IDLE"
    except OSError:
        return SessionInfo()
    return SessionInfo(surface, status, model, effort, approvals_reviewer)


def inspect_updates(path, offset):
    """Read complete records appended after offset and return the next offset."""
    status = model = effort = approvals_reviewer = ""
    next_offset = offset
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            while True:
                start = handle.tell()
                line = handle.readline(LINE_BYTES + 1)
                if not line:
                    break
                if not line.endswith(b"\n") and len(line) > LINE_BYTES:
                    # Discard the rest of one oversized record, in bounded reads.
                    while not line.endswith(b"\n"):
                        line = handle.readline(LINE_BYTES + 1)
                        if not line:
                            next_offset = start
                            break
                        next_offset = handle.tell()
                    continue
                if not line.endswith(b"\n"):
                    next_offset = start
                    break
                next_offset = handle.tell()
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, ValueError):
                    continue
                if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
                    continue
                data = record["payload"]
                if record.get("type") == "turn_context":
                    if isinstance(data.get("model"), str):
                        model = data["model"]
                    if isinstance(data.get("effort"), str):
                        effort = data["effort"]
                    if isinstance(data.get("approvals_reviewer"), str):
                        approvals_reviewer = data["approvals_reviewer"]
                elif record.get("type") == "event_msg":
                    if data.get("type") == "task_started":
                        status = "RUNNING"
                    elif data.get("type") in {"task_complete", "turn_aborted"}:
                        status = "IDLE"
    except OSError:
        pass
    return SessionInfo(
        status=status,
        model=model,
        effort=effort,
        approvals_reviewer=approvals_reviewer,
    ), next_offset


class SessionObserver:
    """Refresh optional metadata without ever controlling a Codex session."""

    def __init__(
        self,
        controller,
        interval=0.5,
        *,
        pid_is_alive=process_alive,
        clock=time.monotonic,
        liveness_grace=LIVENESS_GRACE_SECONDS,
    ):
        self.controller = controller
        self.interval = interval
        self.pid_is_alive = pid_is_alive
        self.clock = clock
        self.liveness_grace = liveness_grace
        self.stopped = threading.Event()
        self.thread = None
        self.modified = {}
        self.positions = {}
        self.dead_since = {}

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="nyx-sessions")
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)

    def run(self):
        while not self.stopped.is_set():
            self.poll_once()
            self.stopped.wait(self.interval)

    def poll_once(self):
        """Perform one bounded metadata and process-liveness refresh."""
        payloads = self.controller.session_payloads()
        now = self.clock()
        active = set(payloads)
        for session_id, payload in payloads.items():
            if self._closed(session_id, payload, now):
                self.controller.remove_session(session_id)
                active.discard(session_id)
                continue
            path = rollout_path(payload)
            if path is None:
                continue
            try:
                stat = path.stat()
                modified = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
            previous = self.modified.get(session_id)
            if previous == modified:
                continue
            self.modified[session_id] = modified
            if session_id not in self.positions or modified[1] < self.positions[session_id]:
                info = inspect_rollout(payload)
                self.positions[session_id] = modified[1]
            else:
                info, position = inspect_updates(path, self.positions[session_id])
                self.positions[session_id] = position
            # Transcript lifecycle is a fallback only for desktop, where
            # Stop is not delivered consistently by current app builds.
            known_surface = info.surface
            if not known_surface and previous is not None:
                known_surface = self.controller.session_surface(session_id)
            self.controller.reconcile(
                session_id,
                surface=info.surface,
                status=info.status if known_surface == "APP" else "",
                model=info.model,
                effort=info.effort,
                approvals_reviewer=info.approvals_reviewer,
            )
        self.modified = {key: value for key, value in self.modified.items() if key in active}
        self.positions = {key: value for key, value in self.positions.items() if key in active}
        self.dead_since = {key: value for key, value in self.dead_since.items() if key in active}

    def _closed(self, session_id, payload, now):
        metadata = payload.get("_nyx", {})
        pid = metadata.get("origin_pid") if isinstance(metadata, dict) else None
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
            self.dead_since.pop(session_id, None)
            return False
        try:
            alive = self.pid_is_alive(pid)
        except Exception:
            alive = True
        if alive:
            self.dead_since.pop(session_id, None)
            return False
        first_seen = self.dead_since.setdefault(session_id, now)
        return now - first_seen >= self.liveness_grace
