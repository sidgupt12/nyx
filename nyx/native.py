"""Live approvals shared with Codex's own UI; hooks never wait here.

Each offer names an actual server request. Losing the connection removes all
offers. Buttons are never replayed after reconnecting.
"""

from dataclasses import dataclass
import queue
import threading
import uuid


METHODS = {
    "item/commandExecution/requestApproval": "thread-follower-command-approval-decision",
    "item/fileChange/requestApproval": "thread-follower-file-approval-decision",
}


def decision_for(request, decision):
    """Respect the choices offered by this exact native prompt.

    Some CLI prompts offer Cancel ('tell Codex what to do differently') instead
    of Decline. Both refuse the operation, but Cancel also ends the turn.
    """
    choices = request.get("params", {}).get("availableDecisions")
    if choices is None or decision in choices:
        return decision
    if decision == "decline" and "cancel" in choices:
        return "cancel"
    return None


@dataclass
class Offer:
    token: str
    session_id: str
    request_id: str | int
    method: str
    detail: str
    source: object


class NativeApprovals:
    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.RLock()
        self.offers = {}
        self.submitted = set()
        self.sources = []

    def update(self, source, session_id, requests):
        """Replace a session's offers from an authoritative native snapshot."""
        with self.lock:
            valid = {
                (r["id"], r["method"]): r
                for r in requests
                if isinstance(r, dict)
                and r.get("method") in METHODS
                and type(r.get("id")) in (str, int)
            }
            old = [
                o for o in self.offers.values() if o.source is source and o.session_id == session_id
            ]
            self.submitted = {
                key for key in self.submitted if key[:2] != (source, session_id) or key[2:] in valid
            }
            for offer in old:
                if (offer.request_id, offer.method) not in valid:
                    self.offers.pop(offer.token, None)
            known = {(o.request_id, o.method) for o in old}
            for key, request in valid.items():
                if key in known or (source, session_id, *key) in self.submitted:
                    continue
                params = request.get("params", {})
                detail = str(params.get("command") or params.get("reason") or "Review in Codex")
                token = "native:" + uuid.uuid4().hex
                self.offers[token] = Offer(token, session_id, key[0], key[1], detail[:240], source)
            self._publish()

    def _publish(self):
        self.controller.set_native_offers(
            [
                {"token": o.token, "session_id": o.session_id, "detail": o.detail}
                for o in self.offers.values()
            ]
        )

    def disconnected(self, source):
        with self.lock:
            self.offers = {k: o for k, o in self.offers.items() if o.source is not source}
            self.submitted = {key for key in self.submitted if key[0] is not source}
            self._publish()
        self.controller.clear_native_source(source.name)

    def submit(self, token, action):
        with self.lock:
            offer = self.offers.get(token)
            if offer is None or action not in {"approve", "reject"}:
                return False
            try:
                offer.source.actions.put_nowait(
                    (offer, "accept" if action == "approve" else "decline")
                )
            except queue.Full:
                return False
            self.offers.pop(token)
            self.submitted.add((offer.source, offer.session_id, offer.request_id, offer.method))
            self._publish()
            # The source rechecks the request immediately before sending.
            return True

    def start(self):
        for source in self.sources:
            source.start()

    def close(self):
        for source in self.sources:
            source.close()


class Source:
    name = ""

    def __init__(self, hub):
        self.hub = hub
        self.actions = queue.Queue(maxsize=32)
        self.stopped = threading.Event()
        self.thread = None
        self.connected = False

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="nyx-" + self.name)
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)

    def reset(self):
        self.connected = False
        while not self.actions.empty():
            try:
                self.actions.get_nowait()
            except queue.Empty:
                break
        self.hub.disconnected(self)
