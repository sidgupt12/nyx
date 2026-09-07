"""Session state and one-shot decisions, independent of USB and Codex I/O."""

from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import time
import uuid

from .protocol import APPROVAL_SECONDS, VERSION
from .session_info import funky_name, terminal_surface


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
        self.native_offers = OrderedDict()
        self.selected = ""

    def event(self, payload, device_ready):
        """Record a hook. Return a request only when explicitly enabled."""
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
            raise ValueError("missing or invalid session_id")
        name = payload.get("hook_event_name")
        with self.lock:
            if name == "SessionEnd":
                self.remove_session(session_id)
                return None
            session = self.sessions.setdefault(
                session_id, {"status": "IDLE", "name": funky_name(session_id)}
            )
            previous_metadata = session.get("payload", {}).get("_nyx", {})
            previous_tty = (
                previous_metadata.get("client_tty") if isinstance(previous_metadata, dict) else None
            )
            session["payload"] = payload
            if session.get("native_source") == "terminal":
                metadata = payload.setdefault("_nyx", {})
                metadata["shared_server"] = True
                if previous_tty:
                    metadata["client_tty"] = previous_tty
            surface = terminal_surface(payload)
            if surface and not session.get("native_source"):
                session["surface"] = surface
            model = payload.get("model")
            if isinstance(model, str) and model:
                session["model"] = model
            self.sessions.move_to_end(session_id)
            if name in {"UserPromptSubmit", "PostToolUse"}:
                session["status"] = "RUNNING"
                session["detail"] = ""
            elif name in {"Stop", "Interrupt"}:
                session["status"] = "IDLE"
                session["detail"] = ""
                self.cancel_session(session_id)
            while len(self.sessions) > 32:
                oldest = next(iter(self.sessions))
                self.cancel_session(oldest)
                self.sessions.pop(oldest)
            if name != "PermissionRequest":
                return None
            if session.get("native_source") or not self.manual_approvals:
                # The hook runs before automatic review. Only a live UI request
                # can tell us that a person really needs to answer.
                return None
            if automatic_mode(payload, session.get("approvals_reviewer", "")):
                # An automatic reviewer can resolve this without a human pause.
                session["status"] = "RUNNING"
                session["detail"] = ""
                return None
            inputs = payload.get("tool_input", {})
            detail = (
                inputs.get("command", inputs.get("description", ""))
                if isinstance(inputs, dict)
                else ""
            )
            detail = str(detail or payload.get("tool_name", "Check Codex"))[:240]
            # Observing a real manual prompt is independent from allowing the
            # hardware to answer it. Passive mode reports + opens it safely.
            session["status"] = "PERMISSION_REQUIRED"
            session["detail"] = detail
            if not self.manual_approvals or not device_ready or len(self.pending) >= 16:
                return None
            request = Request(
                session_id,
                detail,
                self.clock() + self.approval_seconds,
            )
            self.pending[request.id] = request
            self.selected = request.id
            return request

    def session_payloads(self):
        with self.lock:
            return {
                session_id: dict(session.get("payload", {}))
                for session_id, session in self.sessions.items()
            }

    def set_terminal_tty(self, session_id, tty):
        """Attach a verified live client TTY to its shared-server session."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or not isinstance(tty, str) or not tty:
                return
            metadata = session.setdefault("payload", {}).setdefault("_nyx", {})
            metadata["client_tty"] = tty

    def set_native_offers(self, offers):
        with self.lock:
            old = set(self.native_offers)
            self.native_offers = OrderedDict(
                (o["token"], o) for o in offers if o["session_id"] in self.sessions
            )
            new = [key for key in self.native_offers if key not in old]
            if new:
                self.selected = new[0]

    def native_state(self, session_id, source, state):
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return
            session["native_source"] = source
            session["surface"] = "APP" if source == "desktop" else "TERM"
            if source == "terminal":
                # A daemon's inherited TTY is not the terminal UI's TTY.
                metadata = session.setdefault("payload", {}).setdefault("_nyx", {})
                metadata["shared_server"] = True
            for target, key in [("model", "latestModel"), ("effort", "latestReasoningEffort")]:
                if isinstance(state.get(key), str):
                    session[target] = state[key]
            runtime = state.get("threadRuntimeStatus")
            kind = runtime.get("type") if isinstance(runtime, dict) else runtime
            if kind in {"idle", "notLoaded", "systemError"}:
                session["status"] = "IDLE"
            elif kind == "active":
                session["status"] = "RUNNING"
            session["detail"] = ""
            # Non-binary inputs remain visible with Open, but only approval
            # requests become actionable with the YES/NO hardware buttons.
            human_methods = {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
                "item/permissions/requestApproval",
                "item/tool/requestUserInput",
                "mcpServer/elicitation/request",
            }
            if any(r.get("method") in human_methods for r in state.get("requests", [])):
                session["status"] = "PERMISSION_REQUIRED"
                session["detail"] = "Review request in Codex"

    def clear_native_source(self, source):
        with self.lock:
            for session in self.sessions.values():
                if session.get("native_source") == source:
                    session.pop("native_source", None)

    def session_surface(self, session_id):
        with self.lock:
            return str(self.sessions.get(session_id, {}).get("surface", ""))

    def remove_session(self, session_id):
        """Forget one closed Codex process and invalidate its old buttons."""
        with self.lock:
            self.cancel_session(session_id)
            self.sessions.pop(session_id, None)
            self.native_offers = OrderedDict(
                (k, o) for k, o in self.native_offers.items() if o["session_id"] != session_id
            )
            if self.selected == session_id:
                self.selected = ""

    def reconcile(
        self,
        session_id,
        *,
        surface="",
        status="",
        model="",
        effort="",
        approvals_reviewer="",
    ):
        """Merge optional observer fields without creating phantom sessions."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return
            for key, value in {
                "surface": surface,
                "model": model,
                "effort": effort,
                "approvals_reviewer": approvals_reviewer,
            }.items():
                if isinstance(value, str) and value:
                    session[key] = value
            if (
                not session.get("native_source")
                and surface == "APP"
                and status in {"IDLE", "RUNNING"}
            ):
                session["status"] = status
                if status == "IDLE":
                    self.cancel_session(session_id)

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
        waiting.update(o["session_id"] for o in self.native_offers.values())
        return (
            list(self.native_offers)
            + list(self.pending)
            + [s for s in self.sessions if s not in waiting]
        )

    def snapshot(self):
        with self.lock:
            self._expire()
            views = self._views()
            if self.selected not in views:
                self.selected = views[0] if views else ""
            request = self.pending.get(self.selected)
            native = self.native_offers.get(self.selected)
            session_id = (
                native["session_id"] if native else request.session_id if request else self.selected
            )
            session = self.sessions.get(session_id, {})
            payload = session.get("payload", {})
            cwd = str(payload.get("cwd", ""))
            return {
                "v": VERSION,
                "type": "state",
                "view_id": self.selected,
                "session": session_id[:8],
                "name": str(session.get("name", "Codex Session"))[:24],
                "project": cwd.rstrip("/").split("/")[-1][:32],
                "surface": str(session.get("surface", ""))[:8],
                "model": str(session.get("model", ""))[:32],
                "effort": str(session.get("effort", ""))[:12],
                "status": "PERMISSION_REQUIRED"
                if request or native
                else session.get("status", "IDLE"),
                "detail": native["detail"]
                if native
                else request.detail
                if request
                else str(session.get("detail", ""))[:240],
                "remaining": max(0, int(request.deadline - self.clock())) if request else 0,
                "actionable": request is not None or native is not None,
                "native": native is not None,
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
            native = self.native_offers.get(view)
            if action == "open":
                session_id = (
                    native["session_id"] if native else request.session_id if request else view
                )
                return {"ok": True}, self.sessions[session_id]["payload"]
            if native and action in {"approve", "reject"}:
                return {"ok": True}, {"native_token": view, "native_action": action}
            if action in {"approve", "reject"} and request:
                self.finish(request, "allow" if action == "approve" else "deny")
                return {"ok": True, "status": "submitted"}, None
            return {"ok": False, "error": "no_pending_request"}, None


def automatic_mode(payload, known_reviewer=""):
    """Recognize explicit modes, NOT a reliable post-auto-review detector.

    The documented hook input has no final human-review routing flag.
    Manual mode is opt-in for that reason; see docs/architecture.md.
    """
    mode = str(payload.get("permission_mode", "")).lower()
    metadata = payload.get("_nyx", {})
    private_reviewer = metadata.get("approvals_reviewer", "") if isinstance(metadata, dict) else ""
    reviewer = str(payload.get("approvals_reviewer") or private_reviewer or known_reviewer).lower()
    return mode in {"dontask", "bypasspermissions"} or reviewer in {
        "auto_review",
        "auto",
        "guardian_subagent",
    }
