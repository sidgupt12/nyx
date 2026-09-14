"""A temporary model/effort picker, separate from session and approval state.

Browsing never writes settings. A confirmed selection is bound to one native
source, session, menu token and expected settings. Nothing is replayed on USB
or app reconnect. Sources acknowledge the change before we display success.
"""

from dataclasses import dataclass
import json
import queue
import threading
import time
import uuid

from .protocol import runtime_dir


def cached_models():
    """Use Codex's own visible model catalog, never a hard-coded effort list."""
    path = runtime_dir().parent / "models_cache.json"
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return []
        return normalize_models(json.loads(path.read_text())["models"], cached=True)
    except (OSError, ValueError, KeyError, TypeError):
        return []


def normalize_models(rows, cached=False):
    models = []
    if not isinstance(rows, list):
        return models
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        if row.get("hidden") or (cached and row.get("visibility") != "list"):
            continue
        name = row.get("slug" if cached else "model")
        levels = row.get(
            "supported_reasoning_levels" if cached else "supportedReasoningEfforts", []
        )
        if not isinstance(levels, list):
            continue
        levels = [
            x.get("effort" if cached else "reasoningEffort") for x in levels if isinstance(x, dict)
        ]
        levels = [x for x in levels if isinstance(x, str) and x.isascii() and len(x) <= 12]
        default = row.get("default_reasoning_level" if cached else "defaultReasoningEffort")
        if isinstance(name, str) and name.isascii() and len(name) <= 64 and levels:
            models.append(
                {
                    "model": name,
                    "efforts": list(dict.fromkeys(levels)),
                    "default": default if default in levels else levels[0],
                }
            )
    return models


@dataclass(frozen=True)
class SettingsJob:
    token: str
    session_id: str
    source: object
    model: str
    effort: str
    expected_model: str
    expected_effort: str
    deadline: float

    @property
    def patch(self):
        # In particular, never send approval, sandbox or permission settings.
        return {"model": self.model, "effort": self.effort}


class Settings:
    def __init__(self, controller, sources, *, catalog=cached_models, clock=time.monotonic):
        self.controller, self.sources = controller, sources
        self.catalog, self.clock = catalog, clock
        self.lock = threading.RLock()
        self.menu = None

    def cancel(self):
        with self.lock:
            self.menu = None

    def disconnected(self, source):
        with self.lock:
            if self.menu and self.menu["source"] is source:
                self.menu = None

    def _valid(self, state):
        menu = self.menu
        return (
            menu is not None
            and self.clock() < menu["until"]
            and state["view_id"] == menu["session_id"]
            and state["status"] != "PERMISSION_REQUIRED"
            and menu["source"].connected
        )

    def snapshot(self):
        with self.lock:
            state = self.controller.snapshot()
            if not self._valid(state):
                self.menu = None
                return state
            m = self.menu
            state["menu"] = {
                "id": m["id"],
                "kind": m["kind"],
                "value": m["choices"][m["index"]],
                "index": m["index"] + 1,
                "count": len(m["choices"]),
                "phase": m["phase"],
                "note": m.get("note", ""),
            }
            return state

    def action(self, message):
        with self.lock:
            state = self.controller.snapshot()
            action = message.get("action")
            if message.get("view_id") != state["view_id"] or not state["view_id"]:
                self.menu = None
                return {"ok": False, "error": "stale_view"}
            if action == "menu_open":
                return self._open(state, message.get("kind"))
            if not self._valid(state) or message.get("menu_id") != self.menu["id"]:
                self.menu = None
                return {"ok": False, "error": "stale_menu"}
            m = self.menu
            if action == "menu_cancel":
                self.menu = None
                return {"ok": True}
            if m["phase"] != "browse":
                return {"ok": False, "error": "menu_busy"}
            if action in {"menu_next", "menu_previous"}:
                m["index"] = (m["index"] + (1 if action == "menu_next" else -1)) % len(m["choices"])
                m["until"] = self.clock() + 5
                return {"ok": True}
            if action != "menu_confirm":
                return {"ok": False, "error": "invalid_menu_action"}
            value = m["choices"][m["index"]]
            model = value if m["kind"] == "model" else m["model"]
            entry = next((x for x in m["catalog"] if x["model"] == model), None)
            if entry is None:
                return {"ok": False, "error": "model_unavailable"}
            effort = (
                value
                if m["kind"] == "effort"
                else (m["effort"] if m["effort"] in entry["efforts"] else entry["default"])
            )
            job = SettingsJob(
                m["id"],
                m["session_id"],
                m["source"],
                model,
                effort,
                m["model"],
                m["effort"],
                self.clock() + 4,
            )
            try:
                m["source"].settings_actions.put_nowait(job)
            except queue.Full:
                return {"ok": False, "error": "settings_busy"}
            m.update(phase="saving", until=self.clock() + 5, note="WAITING FOR CODEX")
            return {"ok": True, "status": "queued"}

    def _open(self, state, kind):
        if kind not in {"effort", "model"} or state["status"] == "PERMISSION_REQUIRED":
            return {"ok": False, "error": "menu_unavailable"}
        with self.controller.lock:
            session = self.controller.sessions.get(state["view_id"], {})
            source = next(
                (s for s in self.sources if s.name == session.get("native_source") and s.connected),
                None,
            )
        if source is None:
            return {"ok": False, "error": "native_settings_unavailable"}
        catalog = getattr(source, "models", None) or self.catalog()
        current = next((x for x in catalog if x["model"] == state["model"]), None)
        choices = (
            [x["model"] for x in catalog] if kind == "model" else (current or {}).get("efforts", [])
        )
        if not choices or not state["model"] or not state["effort"]:
            return {"ok": False, "error": "settings_not_ready"}
        value = state[kind]
        self.menu = {
            "id": uuid.uuid4().hex,
            "session_id": state["view_id"],
            "source": source,
            "kind": kind,
            "catalog": catalog,
            "choices": choices,
            "index": choices.index(value) if value in choices else 0,
            "model": state["model"],
            "effort": state["effort"],
            "phase": "browse",
            "until": self.clock() + 5,
        }
        return {"ok": True}

    def can_send(self, job):
        with self.lock:
            state = self.controller.snapshot()
            return (
                self._valid(state)
                and self.menu["id"] == job.token
                and self.menu["phase"] == "saving"
                and self.clock() < job.deadline
                and state["model"] == job.expected_model
                and state["effort"] == job.expected_effort
            )

    def complete(self, job, ok):
        with self.lock:
            # Even a late acknowledgement must never recreate a closed view.
            if not self.menu or self.menu["id"] != job.token:
                return
            self.menu.update(
                phase="done" if ok else "error",
                until=self.clock() + 2,
                note="SET FOR NEXT TURN" if ok else "NOT CONFIRMED",
            )
