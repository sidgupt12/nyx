"""Session state and one-shot decisions, independent of USB and Codex I/O."""

from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import time
import uuid

from .protocol import APPROVAL_SECONDS, VERSION


@dataclass
class Request:
    session_id: str
    detail: str
    deadline: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ready: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None


class Controller:
    def __init__(
        self, *, manual_approvals=False, clock=time.monotonic, approval_seconds=APPROVAL_SECONDS
    ):
        self.manual_approvals = manual_approvals
        self.clock = clock
        self.approval_seconds = approval_seconds
        self.lock = threading.RLock()
        self.sessions = OrderedDict()
        self.pending = OrderedDict()
        self.selected = ""

    def event(self, payload, device_ready):
        """Record a hook. Return a request only when explicitly enabled."""
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
            raise ValueError("missing or invalid session_id")
        name = payload.get("hook_event_name")
        with self.lock:
            if name == "SessionEnd":
                self.cancel_session(session_id)
                self.sessions.pop(session_id, None)
                return None
            session = self.sessions.setdefault(session_id, {"status": "IDLE"})
            session["payload"] = payload
            self.sessions.move_to_end(session_id)
            if name in {"UserPromptSubmit", "PostToolUse"}:
                session["status"] = "RUNNING"
            elif name in {"Stop", "Interrupt"}:
                session["status"] = "IDLE"
                self.cancel_session(session_id)
            while len(self.sessions) > 32:
                oldest = next(iter(self.sessions))
                self.cancel_session(oldest)
                self.sessions.pop(oldest)
            if name != "PermissionRequest":
                return None
            session["status"] = "RUNNING"
            if not self.manual_approvals or not device_ready or automatic_mode(payload):
                return None
            if len(self.pending) >= 16:
                return None
            inputs = payload.get("tool_input", {})
            detail = (
                inputs.get("command", inputs.get("description", ""))
                if isinstance(inputs, dict)
                else ""
            )
            request = Request(
                session_id,
                str(detail or payload.get("tool_name", "Check Codex"))[:240],
                self.clock() + self.approval_seconds,
            )
            self.pending[request.id] = request
            self.selected = request.id
            return request

    def finish(self, request, decision=None):
        with self.lock:
            if self.pending.pop(request.id, None) is not None:
                request.decision = decision
                request.ready.set()

    def cancel_session(self, session_id):
        with self.lock:
            for request in list(self.pending.values()):
                if request.session_id == session_id:
                    self.finish(request)

    def cancel_all(self):
        with self.lock:
            for request in list(self.pending.values()):
                self.finish(request)

    def _expire(self):
        for request in list(self.pending.values()):
            if self.clock() >= request.deadline:
                self.finish(request)

    def _views(self):
        # Each pending request gets its own view, even within the same session.
        waiting = {r.session_id for r in self.pending.values()}
        return list(self.pending) + [s for s in self.sessions if s not in waiting]

    def snapshot(self):
        with self.lock:
            self._expire()
            views = self._views()
            if self.selected not in views:
                self.selected = views[0] if views else ""
            request = self.pending.get(self.selected)
            session_id = request.session_id if request else self.selected
            session = self.sessions.get(session_id, {})
            payload = session.get("payload", {})
            cwd = str(payload.get("cwd", ""))
            return {
                "v": VERSION,
                "type": "state",
                "view_id": self.selected,
                "session": session_id[:8],
                "project": cwd.rstrip("/").split("/")[-1][:32],
                "status": "PERMISSION_REQUIRED" if request else session.get("status", "IDLE"),
                "detail": request.detail if request else "",
                "remaining": max(0, int(request.deadline - self.clock())) if request else 0,
                "index": views.index(self.selected) + 1 if views else 0,
                "count": len(views),
                "manual": self.manual_approvals,
            }

    def action(self, message):
        """Return (result, payload_to_open). Never inject keyboard shortcuts."""
        with self.lock:
            self._expire()
            action = message.get("action")
            view = message.get("view_id")
            if not isinstance(view, str) or view != self.snapshot()["view_id"] or not view:
                return {"ok": False, "error": "stale_view"}, None
            views = self._views()
            if action in {"next", "previous"}:
                step = 1 if action == "next" else -1
                self.selected = views[(views.index(view) + step) % len(views)]
                return {"ok": True}, None
            request = self.pending.get(view)
            if action == "open":
                session_id = request.session_id if request else view
                return {"ok": True}, self.sessions[session_id]["payload"]
            if action in {"approve", "reject"} and request:
                self.finish(request, "allow" if action == "approve" else "deny")
                return {"ok": True, "status": "submitted"}, None
            return {"ok": False, "error": "no_pending_request"}, None


def automatic_mode(payload):
    """Recognize explicit modes, NOT a reliable post-auto-review detector.

    The documented hook input has no final human-review routing flag.
    Manual mode is opt-in for that reason; see docs/architecture.md.
    """
    mode = str(payload.get("permission_mode", "")).lower()
    reviewer = str(payload.get("approvals_reviewer", "")).lower()
    return mode in {"dontask", "bypasspermissions"} or reviewer in {
        "auto_review",
        "auto",
        "guardian_subagent",
    }
