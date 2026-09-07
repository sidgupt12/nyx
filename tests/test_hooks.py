import io
import json
import unittest
from types import SimpleNamespace
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

    @patch("nyx.hooks.codex_process", return_value=(2468, "/dev/ttys003"))
    @patch("nyx.hooks.socket.socket")
    @patch("nyx.hooks.receive", return_value={"wait": False})
    def test_hook_records_origin_process(self, receive, socket, process):
        run_hook(io.StringIO(json.dumps(event("SessionStart"))), io.StringIO())
        sent = socket.return_value.__enter__.return_value.sendall.call_args.args[0]
        metadata = json.loads(sent)["_nyx"]
        self.assertEqual(metadata["origin_pid"], 2468)
        self.assertEqual(metadata["tty"], "/dev/ttys003")

    @patch("nyx.hooks.os.getppid", return_value=200)
    @patch("nyx.hooks.subprocess.check_output")
    def test_verified_codex_ancestor_skips_short_lived_shell(self, check, getppid):
        from nyx.hooks import codex_process

        check.side_effect = [
            "300 ttys003 /bin/sh\n",
            "1 ttys003 /opt/homebrew/lib/node_modules/@openai/codex\n",
        ]
        self.assertEqual(codex_process(), (300, "/dev/ttys003"))

    @patch("nyx.hooks.codex_process", return_value=(None, ""))
    @patch("nyx.hooks.socket.socket")
    @patch("nyx.hooks.receive", return_value={"wait": False})
    def test_unverified_process_is_not_used_for_liveness(self, receive, socket, process):
        run_hook(io.StringIO(json.dumps(event("SessionStart"))), io.StringIO())
        sent = socket.return_value.__enter__.return_value.sendall.call_args.args[0]
        self.assertNotIn("origin_pid", json.loads(sent)["_nyx"])

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

    @patch("nyx.hooks.socket.socket")
    @patch("nyx.hooks.receive", return_value={"wait": False})
    @patch("nyx.hooks.inspect_rollout")
    def test_permission_adds_live_automatic_reviewer(self, inspect, receive, socket):
        inspect.return_value = SimpleNamespace(approvals_reviewer="auto_review")
        payload = event(transcript_path="/tmp/transcript.jsonl")
        run_hook(io.StringIO(json.dumps(payload)), io.StringIO())
        sent = socket.return_value.__enter__.return_value.sendall.call_args.args[0]
        self.assertEqual(json.loads(sent)["_nyx"]["approvals_reviewer"], "auto_review")
