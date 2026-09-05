import io
import json
import unittest
from unittest.mock import patch
from nyx.hooks import run_hook
from tests.test_controller import event


class HookTests(unittest.TestCase):
    def test_missing_bridge_falls_through(self):
        output = io.StringIO()
        self.assertEqual(
            run_hook(
                io.StringIO(json.dumps(event())), output, "/tmp/nyx-intentionally-missing.sock"
            ),
            0,
        )
        self.assertEqual(output.getvalue(), "")

    def test_malformed_hook_input_is_harmless(self):
        for text in ["[]", "invalid", '{"hook_event_name":"Other"}']:
            output = io.StringIO()
            self.assertEqual(run_hook(io.StringIO(text), output), 0)
            self.assertEqual(output.getvalue(), "")

    @patch("nyx.hooks.socket.socket")
    @patch("nyx.hooks.receive")
    def test_only_permission_can_emit_decision(self, receive, socket):
        receive.return_value = {"decision": "allow"}
        for name, expected in [("PermissionRequest", True), ("Stop", False)]:
            output = io.StringIO()
            run_hook(io.StringIO(json.dumps(event(name))), output)
            self.assertEqual(bool(output.getvalue()), expected)

    @patch("nyx.hooks.socket.socket")
    @patch("nyx.hooks.receive")
    def test_unknown_decision_is_ignored(self, receive, socket):
        receive.return_value = {"decision": "acceptForSession"}
        output = io.StringIO()
        run_hook(io.StringIO(json.dumps(event())), output)
        self.assertEqual(output.getvalue(), "")
