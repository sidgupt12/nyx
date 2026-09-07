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
import shlex
import subprocess
import threading
import time
from uuid import UUID

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
    internal: bool = False


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


def remote_terminal_clients():
    """Return live remote Codex TUIs by TTY and any explicit resumed session ID.

    The shared app-server outlives its terminal clients. A TTY is therefore the
    useful liveness identity; the daemon PID is not. Unknown process formats
    fail open by returning no inferred identity rather than guessing a window.
    """
    try:
        output = subprocess.run(
            ["ps", "-axo", "tty=,comm=,args="],
            capture_output=True,
            text=True,
            check=True,
            timeout=1,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    clients = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or not parts[0].startswith("ttys"):
            continue
        if Path(parts[1]).name.lower() != "codex":
            continue
        try:
            args = shlex.split(parts[2])
            remote = args.index("--remote") + 1
        except (ValueError, IndexError):
            continue
        if remote >= len(args) or not args[remote].startswith("unix://"):
            continue
        session_ids = set()
        if "resume" in args:
            for value in args[args.index("resume") + 1 :]:
                try:
                    if str(UUID(value)) == value:
                        session_ids.add(value)
                except (ValueError, AttributeError):
                    continue
        clients.setdefault("/dev/" + parts[0], set()).update(session_ids)
    return clients


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


def _origin_info(path, session_id):
    """Read only the bounded session header needed for origin classification."""
    try:
        with path.open("rb") as handle:
            header = handle.readline(LINE_BYTES + 1)
        if len(header) > LINE_BYTES:
            return SessionInfo()
        record = json.loads(header)
        data = record.get("payload", {}) if isinstance(record, dict) else {}
        if (
            not isinstance(record, dict)
            or record.get("type") != "session_meta"
            or not isinstance(data, dict)
            or data.get("id") != session_id
        ):
            return SessionInfo()
        originator = data.get("originator")
        surface = ""
        if originator in {"codex_work_desktop", "Codex Desktop"}:
            surface = "APP"
        elif originator == "codex-tui":
            surface = "TERM"
        source = data.get("source")
        internal = isinstance(source, dict) and source.get("subagent") is not None
        return SessionInfo(surface=surface, internal=internal)
    except (OSError, UnicodeDecodeError, ValueError):
        return SessionInfo()


def inspect_origin(payload, *, sessions_dir=None):
    """Return origin metadata without scanning any conversation records."""
    path = rollout_path(payload, sessions_dir=sessions_dir)
    if path is None:
        return SessionInfo()
    return _origin_info(path, payload.get("session_id"))


def inspect_rollout(payload, *, sessions_dir=None):
    """Read a bounded tail and return only lifecycle/display fields."""
    path = rollout_path(payload, sessions_dir=sessions_dir)
    if path is None:
        return SessionInfo()
    session_id = payload.get("session_id")
    origin = _origin_info(path, session_id)
    surface, internal = origin.surface, origin.internal
    status = model = effort = approvals_reviewer = ""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
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
    return SessionInfo(surface, status, model, effort, approvals_reviewer, internal)


def is_internal_session(payload, *, sessions_dir=None):
    """Return true only for an explicitly marked Codex subagent rollout."""
    return inspect_rollout(payload, sessions_dir=sessions_dir).internal


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
        terminal_clients=remote_terminal_clients,
        clock=time.monotonic,
        liveness_grace=LIVENESS_GRACE_SECONDS,
    ):
        self.controller = controller
        self.interval = interval
        self.pid_is_alive = pid_is_alive
        self.terminal_clients = terminal_clients
        self.clock = clock
        self.liveness_grace = liveness_grace
        self.stopped = threading.Event()
        self.thread = None
        self.modified = {}
        self.positions = {}
        self.dead_since = {}
        self.terminal_ttys = {}

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
        shared = {
            sid
            for sid, payload in payloads.items()
            if isinstance(payload.get("_nyx"), dict)
            and payload["_nyx"].get("shared_server") is True
        }
        try:
            clients = self.terminal_clients() if shared else None
        except Exception:
            clients = None
        for session_id, payload in payloads.items():
            if self._closed(session_id, payload, now, clients):
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
            if info.internal:
                self.controller.remove_session(session_id)
                active.discard(session_id)
                continue
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
        self.terminal_ttys = {
            key: value for key, value in self.terminal_ttys.items() if key in active
        }

    def _closed(self, session_id, payload, now, clients=None):
        metadata = payload.get("_nyx", {})
        if isinstance(metadata, dict) and metadata.get("shared_server") is True:
            if clients is None:
                # Losing visibility into processes must never hide a session.
                self.dead_since.pop(session_id, None)
                return False
            # An explicit `resume SESSION_ID` mapping wins. For a new remote
            # TUI, assign only the one unclaimed TTY; ambiguity fails open.
            exact = [tty for tty, ids in clients.items() if session_id in ids]
            if len(exact) == 1:
                self.terminal_ttys[session_id] = exact[0]
            tty = self.terminal_ttys.get(session_id)
            if tty in clients:
                self.dead_since.pop(session_id, None)
                return False
            if tty is None:
                claimed = {value for key, value in self.terminal_ttys.items() if key != session_id}
                available = set(clients) - claimed
                if len(available) == 1:
                    self.terminal_ttys[session_id] = available.pop()
                    self.dead_since.pop(session_id, None)
                    return False
                if clients:
                    self.dead_since.pop(session_id, None)
                    return False
            first_seen = self.dead_since.setdefault(session_id, now)
            return now - first_seen >= self.liveness_grace
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
