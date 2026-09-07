import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from nyx.opener import desktop_link, open_session


class OpenerTests(unittest.TestCase):
    @patch("nyx.opener._run")
    def test_unknown_origin_never_opens_random_terminal(self, run):
        open_session({"_nyx": {}})
        run.assert_not_called()

    @patch("nyx.opener._run")
    def test_terminal_tty_is_an_argument_not_script_source(self, run):
        tty = '/dev/ttys001"'
        open_session({"_nyx": {"term_program": "Apple_Terminal", "tty": tty}})
        command = run.call_args.args[0]
        self.assertEqual(command[-1], tty)
        self.assertNotIn(tty, command[2])

    @patch("nyx.opener._run")
    def test_vscode_does_not_replace_workspace_or_guess_terminal(self, run):
        open_session({"cwd": "/tmp/project", "_nyx": {"term_program": "vscode"}})
        run.assert_called_once_with(["open", "-a", "Visual Studio Code"])


class DesktopOpenerTests(unittest.TestCase):
    SESSION = "01234567-89ab-4cde-8123-0123456789ab"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.rollout = self.root / f"rollout-2026-09-06-{self.SESSION}.jsonl"
        self.payload = {"session_id": self.SESSION}
        self.write_header()

    def write_header(self, originator="codex_work_desktop", session=None):
        self.rollout.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {
                        "id": session or self.SESSION,
                        "source": "vscode",
                        "originator": originator,
                    },
                }
            )
            + "\nnot a message to read\n"
        )

    def link(self):
        return desktop_link(self.payload, sessions_dir=self.root)

    def test_desktop_header_resolves_exact_thread(self):
        self.assertEqual(self.link(), f"codex://threads/{self.SESSION}")

    def test_vscode_source_does_not_imply_desktop(self):
        self.write_header(originator="codex_vscode")
        self.assertIsNone(self.link())

    def test_mismatched_session_never_routes(self):
        self.write_header(session="another-session")
        self.assertIsNone(self.link())

    def test_invalid_ids_cannot_inject_url_actions(self):
        for value in [None, [], "new?prompt=hello", "../settings", ""]:
            self.payload["session_id"] = value
            self.assertIsNone(self.link())

    def test_malformed_or_oversized_headers_are_ignored(self):
        for text in ["invalid", "[]", '{"type":"session_meta","payload":[]}', " " * 131073]:
            self.rollout.write_text(text)
            self.assertIsNone(self.link())

    def test_does_not_follow_rollout_outside_sessions(self):
        other = self.root / "outside.jsonl"
        self.rollout.rename(other)
        inside = self.root / "sessions"
        inside.mkdir()
        (inside / self.rollout.name).symlink_to(other)
        self.payload["transcript_path"] = str(other)
        self.assertIsNone(desktop_link(self.payload, sessions_dir=inside))

    @patch("nyx.opener._run")
    @patch("nyx.opener.desktop_link")
    def test_open_uses_bundle_and_thread_link(self, link, run):
        link.return_value = f"codex://threads/{self.SESSION}"
        open_session(self.payload)
        run.assert_called_once_with(["open", "-b", "com.openai.codex", link.return_value])

    @patch("nyx.opener._run")
    @patch("nyx.opener.desktop_link")
    def test_working_terminal_route_keeps_priority(self, link, run):
        open_session(
            {**self.payload, "_nyx": {"term_program": "Apple_Terminal", "tty": "/dev/ttys001"}}
        )
        link.assert_not_called()
        self.assertEqual(run.call_args.args[0][0], "osascript")
