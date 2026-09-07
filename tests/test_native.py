"""Approval races and independent native transports, without a real user task."""

import copy
import unittest
from unittest.mock import patch
import subprocess

from nyx.appserver import AppServer
from nyx.controller import Controller
from nyx.desktop import Desktop, patch_fields
from nyx.native import NativeApprovals, decision_for
from tests.test_controller import event
from nyx.opener import open_session, remote_terminal_tty


COMMAND = "item/commandExecution/requestApproval"
PERMISSIONS = "item/permissions/requestApproval"


def request(number=1, sid="session-a"):
    return {"id": number, "method": COMMAND, "params": {"threadId": sid, "command": "echo test"}}


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.core = Controller()
        self.core.event(event("UserPromptSubmit"), True)
        self.hub = NativeApprovals(self.core)
        self.desktop = Desktop(self.hub)
        self.sent = []
        self.desktop.send = self.sent.append
        self.desktop.client_id = "nyx-test"

    def snapshot(self, requests, revision=1):
        self.desktop.message(
            {
                "type": "broadcast",
                "method": "thread-stream-state-changed",
                "version": 11,
                "sourceClientId": "owner",
                "params": {
                    "conversationId": "session-a",
                    "hostId": "local",
                    "change": {
                        "type": "snapshot",
                        "revision": revision,
                        "conversationState": {
                            "requests": requests,
                            "threadRuntimeStatus": {"type": "active"},
                            "latestModel": "gpt-6-astra",
                            "latestReasoningEffort": "high",
                            "turns": ["private conversation text is not retained"],
                        },
                    },
                },
            }
        )

    def test_automatic_review_does_not_hide_real_manual_exception(self):
        self.core.event(event(approvals_reviewer="auto_review"), True)
        self.assertFalse(self.core.snapshot()["actionable"])
        self.snapshot([request()])
        self.assertTrue(self.core.snapshot()["actionable"])
        self.assertNotIn("turns", self.desktop.states["session-a"])
        self.assertFalse(self.core.pending)  # Hook was never held open.

    def test_native_ui_answer_invalidates_hardware_token(self):
        self.snapshot([request()])
        old = self.core.snapshot()["view_id"]
        self.snapshot([], 2)
        self.assertFalse(self.hub.submit(old, "approve"))
        self.desktop.decisions()
        self.assertEqual(self.sent, [])

    def test_button_uses_exact_native_request_and_owner(self):
        self.snapshot([request(123)])
        token = self.core.snapshot()["view_id"]
        result, action = self.core.action({"view_id": token, "action": "approve"})
        self.assertTrue(result["ok"])
        self.assertTrue(self.hub.submit(action["native_token"], action["native_action"]))
        self.desktop.decisions()
        sent = self.sent[-1]
        self.assertEqual(sent["targetClientId"], "owner")
        self.assertEqual(
            sent["params"], {"conversationId": "session-a", "requestId": 123, "decision": "accept"}
        )
        self.assertFalse(self.hub.submit(token, "approve"))
        self.snapshot([request(123)], 2)  # Delayed native update cannot re-arm it.
        self.assertFalse(self.core.snapshot()["actionable"])

    def test_resolution_after_button_before_send_sends_nothing(self):
        self.snapshot([request()])
        self.hub.submit(self.core.snapshot()["view_id"], "reject")
        self.snapshot([], 2)
        self.desktop.decisions()
        self.assertEqual(self.sent, [])

    def test_disconnect_invalidates_and_reconnect_has_new_token(self):
        self.snapshot([request()])
        old = self.core.snapshot()["view_id"]
        self.desktop.reset()
        self.assertFalse(self.core.snapshot()["actionable"])
        self.snapshot([request()])
        self.assertNotEqual(old, self.core.snapshot()["view_id"])

    def test_revision_gap_drops_controls_and_requests_snapshot(self):
        self.snapshot([request()])
        self.desktop.message(
            {
                "type": "broadcast",
                "method": "thread-stream-state-changed",
                "version": 11,
                "sourceClientId": "owner",
                "params": {
                    "conversationId": "session-a",
                    "hostId": "local",
                    "change": {
                        "type": "patches",
                        "baseRevision": 999,
                        "revision": 1000,
                        "patches": [],
                    },
                },
            }
        )
        self.assertFalse(self.core.snapshot()["actionable"])
        self.assertEqual([m["params"]["following"] for m in self.sent], [False, True])

    def test_selected_session_is_preserved_for_open(self):
        self.snapshot([request()])
        token = self.core.snapshot()["view_id"]
        result, payload = self.core.action({"view_id": token, "action": "open"})
        self.assertTrue(result["ok"])
        self.assertEqual(payload["session_id"], "session-a")

    def test_patches_remove_request_without_copying_chat(self):
        state = {"requests": [request()], "latestModel": "old"}
        before = copy.deepcopy(state)
        result = patch_fields(
            state,
            [
                {"op": "remove", "path": ["requests", 0]},
                {"op": "replace", "path": ["latestModel"], "value": "new"},
                {"op": "add", "path": ["turns", 0], "value": "private"},
            ],
        )
        self.assertEqual(state, before)
        self.assertEqual(result, {"requests": [], "latestModel": "new"})

    def test_terminal_native_response_and_manual_resolution(self):
        terminal = AppServer(self.hub)
        terminal.joined.add("session-a")
        terminal.send = self.sent.append
        terminal.message(request(45))
        token = self.core.snapshot()["view_id"]
        self.assertTrue(self.hub.submit(token, "reject"))
        terminal.decisions()
        self.assertEqual(self.sent, [{"id": 45, "result": {"decision": "decline"}}])
        terminal.message(request(46))
        token = self.core.snapshot()["view_id"]
        terminal.message(
            {
                "method": "serverRequest/resolved",
                "params": {"threadId": "session-a", "requestId": 46},
            }
        )
        self.assertFalse(self.hub.submit(token, "approve"))

    def test_desktop_permission_request_grants_only_requested_turn_permissions(self):
        permission = {
            "id": "permission-1",
            "method": PERMISSIONS,
            "params": {
                "threadId": "session-a",
                "reason": "Write generated output",
                "permissions": {
                    "fileSystem": {
                        "entries": [
                            {"access": "write", "path": {"type": "path", "path": "/tmp/out"}}
                        ]
                    }
                },
            },
        }
        self.snapshot([permission])
        token = self.core.snapshot()["view_id"]
        self.assertTrue(self.hub.submit(token, "approve"))
        self.desktop.decisions()
        sent = self.sent[-1]
        self.assertEqual(sent["method"], "thread-follower-permissions-request-approval-response")
        self.assertEqual(
            sent["params"]["response"],
            {"permissions": permission["params"]["permissions"], "scope": "turn"},
        )

    def test_terminal_permission_reject_grants_nothing(self):
        terminal = AppServer(self.hub)
        terminal.joined.add("session-a")
        terminal.send = self.sent.append
        permission = {
            "id": 91,
            "method": PERMISSIONS,
            "params": {
                "threadId": "session-a",
                "permissions": {"network": {"enabled": True}},
            },
        }
        terminal.message(permission)
        token = self.core.snapshot()["view_id"]
        self.assertTrue(self.hub.submit(token, "reject"))
        terminal.decisions()
        self.assertEqual(
            self.sent[-1],
            {"id": 91, "result": {"permissions": {}, "scope": "turn"}},
        )

    def test_terminal_does_not_load_standalone_sessions(self):
        terminal = AppServer(self.hub)
        terminal.pending[1] = ("thread/loaded/list", None, 0)
        terminal.send = self.sent.append
        terminal.message({"id": 1, "result": {"data": ["some-other-session"]}})
        self.assertEqual(self.sent, [])

    def test_shared_daemon_tty_is_never_used_as_open_target(self):
        self.core.native_state("session-a", "terminal", {})
        self.core.event(event("PostToolUse", _nyx={"tty": "/dev/ttys999"}), True)
        payload = self.core.session_payloads()["session-a"]
        self.assertTrue(payload["_nyx"]["shared_server"])
        with patch("nyx.opener._run") as run:
            open_session(payload)
            run.assert_not_called()  # Invalid/non-UUID session cannot target a terminal.

    def test_explicit_resumed_terminal_match_must_be_unique(self):
        sid = "01a02ab0-5de7-7c80-9169-4bfcfc03f890"
        row = f"ttys003 /opt/bin/codex codex resume --remote unix:// {sid}\n"
        with patch(
            "nyx.opener.subprocess.run", return_value=subprocess.CompletedProcess([], 0, row)
        ):
            self.assertEqual(remote_terminal_tty(sid), "/dev/ttys003")
        with patch(
            "nyx.opener.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, row + row.replace("ttys003", "ttys004")
            ),
        ):
            self.assertEqual(remote_terminal_tty(sid), "")

    def test_reject_uses_cancel_when_that_is_the_native_choice(self):
        r = request()
        r["params"]["availableDecisions"] = ["accept", "cancel"]
        self.assertEqual(decision_for(r, "decline"), "cancel")
        self.assertEqual(decision_for(r, "accept"), "accept")
        r["params"]["availableDecisions"] = ["cancel"]
        self.assertIsNone(decision_for(r, "accept"))


if __name__ == "__main__":
    unittest.main()
