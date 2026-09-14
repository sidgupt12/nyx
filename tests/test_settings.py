import queue
import unittest
from unittest.mock import Mock

from nyx.appserver import AppServer
from nyx.controller import Controller
from nyx.desktop import Desktop
from nyx.native import NativeApprovals
from nyx.settings import Settings, normalize_models


CATALOG = [
    {"model": "alpha", "efforts": ["low", "high"], "default": "low"},
    {"model": "beta", "efforts": ["medium"], "default": "medium"},
]


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.core = Controller()
        self.core.event({"hook_event_name": "SessionStart", "session_id": "a"}, False)
        self.hub = NativeApprovals(self.core)
        self.source = Desktop(self.hub)
        self.source.connected = True
        self.source.client_id = "nyx"
        self.source.owners["a"] = "owner-a"
        self.source.send = Mock()
        self.core.native_state(
            "a", "desktop", {"latestModel": "alpha", "latestReasoningEffort": "high"}
        )
        self.picker = Settings(
            self.core, [self.source], catalog=lambda: CATALOG, clock=lambda: self.now[0]
        )
        self.hub.settings = self.picker

    def action(self, action, **extra):
        return self.picker.action(
            {
                "action": action,
                "view_id": "a",
                "menu_id": (self.picker.menu or {}).get("id"),
                **extra,
            }
        )

    def test_browse_only_does_not_change_runtime(self):
        self.assertTrue(self.action("menu_open", kind="effort")["ok"])
        self.assertEqual(self.picker.snapshot()["menu"]["value"], "high")
        self.action("menu_next")
        self.assertEqual(self.picker.snapshot()["menu"]["value"], "low")
        self.assertEqual(self.core.snapshot()["effort"], "high")
        self.assertTrue(self.source.settings_actions.empty())

    def test_five_seconds_idle_cancels_without_writing(self):
        self.action("menu_open", kind="model")
        self.now[0] += 4.9
        self.assertIn("menu", self.picker.snapshot())
        self.now[0] += 0.1
        self.assertNotIn("menu", self.picker.snapshot())
        self.assertFalse(self.action("menu_confirm")["ok"])
        self.assertTrue(self.source.settings_actions.empty())

    def test_rotation_extends_timeout(self):
        self.action("menu_open", kind="model")
        self.now[0] += 4
        self.action("menu_next")
        self.now[0] += 4
        self.assertIn("menu", self.picker.snapshot())

    def test_model_change_selects_valid_effort_not_unsupported_old_effort(self):
        self.action("menu_open", kind="model")
        self.action("menu_next")
        self.action("menu_confirm")
        job = self.source.settings_actions.get_nowait()
        self.assertEqual(job.patch, {"model": "beta", "effort": "medium"})
        self.assertEqual(
            (job.session_id, job.expected_model, job.expected_effort), ("a", "alpha", "high")
        )
        self.assertEqual(self.picker.snapshot()["menu"]["phase"], "saving")
        self.assertFalse(self.action("menu_confirm")["ok"])
        self.picker.complete(job, False)
        self.assertEqual(self.picker.snapshot()["menu"]["phase"], "error")
        self.assertEqual(self.core.snapshot()["model"], "alpha")

    def test_permission_preempts_menu_and_invalidates_queued_setting(self):
        self.action("menu_open", kind="effort")
        self.action("menu_confirm")
        job = self.source.settings_actions.get_nowait()
        self.core.set_native_offers([{"token": "approval", "session_id": "a", "detail": "review"}])
        self.assertNotIn("menu", self.picker.snapshot())
        self.assertFalse(self.picker.can_send(job))
        self.assertTrue(self.core.snapshot()["actionable"])

    def test_closed_session_never_recreated_by_late_ack(self):
        self.action("menu_open", kind="effort")
        self.action("menu_confirm")
        job = self.source.settings_actions.get_nowait()
        self.core.remove_session("a")
        self.picker.complete(job, True)
        self.assertNotIn("menu", self.picker.snapshot())
        self.assertEqual(self.core.snapshot()["count"], 0)

    def test_external_setting_change_invalidates_queued_write(self):
        self.action("menu_open", kind="effort")
        self.action("menu_confirm")
        job = self.source.settings_actions.get_nowait()
        self.core.native_state("a", "desktop", {"latestReasoningEffort": "low"})
        self.assertFalse(self.picker.can_send(job))

    def test_disconnect_drops_queue_and_does_not_replay(self):
        self.action("menu_open", kind="effort")
        self.action("menu_confirm")
        self.source.reset()
        self.assertTrue(self.source.settings_actions.empty())
        self.assertIsNone(self.picker.menu)

    def test_old_menu_token_cannot_confirm_new_picker(self):
        self.action("menu_open", kind="effort")
        old = self.picker.menu["id"]
        self.action("menu_open", kind="model")
        self.assertFalse(self.action("menu_confirm", menu_id=old)["ok"])
        self.assertTrue(self.source.settings_actions.empty())

    def test_only_native_sessions_can_open_menu(self):
        self.source.connected = False
        self.assertFalse(self.action("menu_open", kind="model")["ok"])

    def test_full_queue_does_not_claim_saving(self):
        self.action("menu_open", kind="model")
        self.source.settings_actions = queue.Queue(maxsize=1)
        self.source.settings_actions.put(None)
        self.assertFalse(self.action("menu_confirm")["ok"])
        self.assertEqual(self.picker.snapshot()["menu"]["phase"], "browse")

    def test_desktop_request_targets_owner_and_uses_conditional_minimal_patch(self):
        # Use real monotonic time here, matching the native adapter's deadline.
        import time

        self.picker.clock = time.monotonic
        self.action("menu_open", kind="effort")
        self.action("menu_next")
        self.action("menu_confirm")
        self.source.settings_decisions()
        sent = self.source.send.call_args.args[0]
        self.assertEqual(sent["method"], "thread-follower-update-thread-settings")
        self.assertEqual(sent["version"], 2)
        self.assertEqual(sent["targetClientId"], "owner-a")
        self.assertEqual(
            sent["params"],
            {
                "conversationId": "a",
                "threadSettings": {"model": "alpha", "effort": "low"},
                "condition": {"ifModelEquals": "alpha", "ifEffortEquals": "high"},
            },
        )
        self.source.message(
            {
                "type": "response",
                "requestId": sent["requestId"],
                "resultType": "success",
                "result": {"applied": True},
            }
        )
        self.assertEqual(self.picker.snapshot()["menu"]["phase"], "done")

    def test_terminal_update_and_settings_notification(self):
        terminal = AppServer(self.hub)
        terminal.connected = True
        terminal.joined.add("a")
        terminal.send = Mock()
        self.picker.sources = [terminal]
        self.core.native_state("a", "terminal", {})
        self.action("menu_open", kind="effort")
        self.action("menu_next")
        self.action("menu_confirm")
        terminal.settings_decisions()
        sent = terminal.send.call_args.args[0]
        self.assertEqual(sent["method"], "thread/settings/update")
        self.assertEqual(sent["params"], {"threadId": "a", "model": "alpha", "effort": "low"})
        terminal.message({"id": sent["id"], "result": {}})
        self.assertEqual(self.picker.snapshot()["menu"]["phase"], "done")
        terminal.message(
            {
                "method": "thread/settings/updated",
                "params": {"threadId": "a", "threadSettings": {"model": "alpha", "effort": "low"}},
            }
        )
        self.assertEqual(self.core.snapshot()["effort"], "low")

    def test_catalog_has_no_invented_models_or_efforts(self):
        rows = [
            {
                "model": "alpha",
                "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
                "defaultReasoningEffort": "high",
            },
            {"model": "hidden", "hidden": True},
        ]
        self.assertEqual(
            normalize_models(rows), [{"model": "alpha", "efforts": ["low"], "default": "low"}]
        )
        self.assertEqual(normalize_models(None), [])
        self.assertEqual(normalize_models([{"model": "a", "supportedReasoningEfforts": None}]), [])

    def test_desktop_missing_owner_sends_nothing(self):
        self.action("menu_open", kind="model")
        self.action("menu_confirm")
        self.source.owners.clear()
        self.source.settings_decisions()
        self.source.send.assert_not_called()
        self.assertEqual(self.picker.snapshot()["menu"]["phase"], "error")

    def test_unrelated_disconnect_does_not_close_menu(self):
        self.action("menu_open", kind="effort")
        self.picker.disconnected(object())
        self.assertIn("menu", self.picker.snapshot())

    def test_cancel_after_confirmation_invalidates_unsent_job(self):
        self.action("menu_open", kind="effort")
        self.action("menu_confirm")
        job = self.source.settings_actions.get_nowait()
        self.picker.cancel()
        self.assertFalse(self.picker.can_send(job))
        self.picker.complete(job, True)
        self.assertIsNone(self.picker.menu)
