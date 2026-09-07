import unittest
from nyx.controller import Controller


def event(name="PermissionRequest", session="session-a", **extra):
    return {
        "hook_event_name": name,
        "session_id": session,
        "cwd": "/tmp/project",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hello"},
        **extra,
    }


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.core = Controller(manual_approvals=True, clock=lambda: self.now)

    def request(self, session="session-a"):
        return self.core.event(event(session=session), True)

    def test_lifecycle(self):
        for name, status in [
            ("SessionStart", "IDLE"),
            ("UserPromptSubmit", "RUNNING"),
            ("Stop", "IDLE"),
            ("PostToolUse", "RUNNING"),
            ("Interrupt", "IDLE"),
        ]:
            self.core.event(event(name), True)
            self.assertEqual(self.core.snapshot()["status"], status)
        self.core.event(event("SessionEnd"), True)
        self.assertEqual(self.core.snapshot()["count"], 0)

    def test_remove_session_invalidates_pending_request(self):
        request = self.request()
        self.core.remove_session("session-a")
        self.assertTrue(request.ready.is_set())
        self.assertIsNone(request.decision)
        self.assertEqual(self.core.snapshot()["count"], 0)

    def test_hook_alone_does_not_claim_human_permission_is_needed(self):
        core = Controller()
        self.assertIsNone(core.event(event(), True))
        state = core.snapshot()
        self.assertEqual(state["status"], "IDLE")
        self.assertFalse(state["actionable"])
        self.assertEqual(state["detail"], "")

    def test_device_absent_never_waits(self):
        self.assertIsNone(self.core.event(event(), False))

    def test_explicit_automatic_modes_never_wait(self):
        for extra in [
            {"permission_mode": "dontAsk"},
            {"permission_mode": "bypassPermissions"},
            {"approvals_reviewer": "auto_review"},
            {"_nyx": {"approvals_reviewer": "auto_review"}},
        ]:
            self.assertIsNone(self.core.event(event(**extra), True))
            self.assertEqual(self.core.snapshot()["status"], "RUNNING")

    def test_allow_and_deny_are_one_shot(self):
        for action, decision in [("approve", "allow"), ("reject", "deny")]:
            request = self.request()
            result, _ = self.core.action({"action": action, "view_id": request.id})
            self.assertTrue(result["ok"])
            self.assertEqual(request.decision, decision)
            result, _ = self.core.action({"action": action, "view_id": request.id})
            self.assertFalse(result["ok"])

    def test_different_requests_in_same_turn_have_different_ids(self):
        one, two = self.request(), self.request()
        self.assertNotEqual(one.id, two.id)
        self.assertEqual(self.core.snapshot()["count"], 2)

    def test_wrong_or_old_view_cannot_approve(self):
        one = self.request()
        two = self.request("session-b")
        result, _ = self.core.action({"action": "approve", "view_id": one.id})
        self.assertFalse(result["ok"])
        self.assertFalse(one.ready.is_set())
        self.assertFalse(two.ready.is_set())

    def test_selection_and_open_do_not_decide(self):
        one, two = self.request(), self.request("session-b")
        self.core.action({"action": "previous", "view_id": two.id})
        self.assertEqual(self.core.snapshot()["view_id"], one.id)
        result, payload = self.core.action({"action": "open", "view_id": one.id})
        self.assertEqual(payload["session_id"], one.session_id)
        self.assertFalse(one.ready.is_set())

    def test_expired_button_never_approves(self):
        request = self.request()
        self.now += 21
        result, _ = self.core.action({"action": "approve", "view_id": request.id})
        self.assertFalse(result["ok"])
        self.assertTrue(request.ready.is_set())
        self.assertIsNone(request.decision)

    def test_interrupt_invalidates_request(self):
        request = self.request()
        self.core.event(event("Interrupt"), True)
        self.assertTrue(request.ready.is_set())
        self.assertIsNone(request.decision)

    def test_disconnect_cancels_without_allowing(self):
        requests = [self.request(), self.request("session-b")]
        self.core.cancel_all()
        for request in requests:
            self.assertTrue(request.ready.is_set())
            self.assertIsNone(request.decision)

    def test_queue_is_bounded(self):
        for _ in range(17):
            self.request()
        self.assertEqual(len(self.core.pending), 16)
        for index in range(50):
            self.core.event(event("SessionStart", str(index)), True)
        self.assertEqual(len(self.core.sessions), 32)

    def test_invalid_session_is_rejected(self):
        for invalid in [None, [], "", "x" * 129]:
            with self.assertRaises(ValueError):
                self.core.event(event(session=invalid), True)

    def test_snapshot_has_friendly_runtime_metadata(self):
        self.core.event(
            event(
                "UserPromptSubmit",
                model="gpt-6-astra",
                _nyx={"term_program": "Apple_Terminal"},
            ),
            True,
        )
        self.core.reconcile("session-a", model="gpt-6-astra", effort="xhigh")
        state = self.core.snapshot()
        self.assertEqual(state["surface"], "TERM")
        self.assertEqual((state["model"], state["effort"]), ("gpt-6-astra", "xhigh"))
        self.assertEqual(len(state["name"].split()), 2)

    def test_desktop_reconciliation_can_mark_idle(self):
        self.core.event(event("UserPromptSubmit"), True)
        self.core.reconcile("session-a", surface="APP", status="IDLE")
        self.assertEqual(self.core.snapshot()["status"], "IDLE")

    def test_terminal_reconciliation_does_not_override_hook_state(self):
        self.core.event(event("UserPromptSubmit"), True)
        self.core.reconcile("session-a", surface="TERM", status="IDLE")
        self.assertEqual(self.core.snapshot()["status"], "RUNNING")

    def test_cached_automatic_reviewer_suppresses_later_permission(self):
        self.core.event(event("UserPromptSubmit"), True)
        self.core.reconcile("session-a", approvals_reviewer="auto_review")
        self.assertIsNone(self.core.event(event("PermissionRequest"), True))
        self.assertEqual(self.core.snapshot()["status"], "RUNNING")
