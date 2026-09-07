import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nyx.session_info import (
    SessionObserver,
    funky_name,
    inspect_rollout,
    inspect_updates,
    is_internal_session,
    process_alive,
    rollout_path,
    terminal_surface,
)


class SessionInfoTests(unittest.TestCase):
    SESSION = "01234567-89ab-4cde-8123-0123456789ab"

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / f"rollout-test-{self.SESSION}.jsonl"
        self.payload = {"session_id": self.SESSION, "transcript_path": str(self.path)}

    def write(self, *records):
        self.path.write_text("".join(json.dumps(record) + "\n" for record in records))

    def header(self, originator="codex_work_desktop"):
        return {
            "type": "session_meta",
            "payload": {"id": self.SESSION, "originator": originator},
        }

    def test_reads_desktop_lifecycle_model_and_effort(self):
        self.write(
            self.header(),
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {
                "type": "turn_context",
                "payload": {
                    "model": "gpt-6-astra",
                    "effort": "high",
                    "approvals_reviewer": "auto_review",
                },
            },
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        )
        info = inspect_rollout(self.payload, sessions_dir=self.root)
        self.assertEqual((info.surface, info.status), ("APP", "IDLE"))
        self.assertEqual((info.model, info.effort), ("gpt-6-astra", "high"))
        self.assertEqual(info.approvals_reviewer, "auto_review")

    def test_latest_lifecycle_wins(self):
        self.write(
            self.header(),
            {"type": "event_msg", "payload": {"type": "task_complete"}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
        )
        self.assertEqual(inspect_rollout(self.payload, sessions_dir=self.root).status, "RUNNING")

    def test_cli_surface_is_terminal_but_status_remains_observer_optional(self):
        self.write(self.header("codex-tui"))
        self.assertEqual(inspect_rollout(self.payload, sessions_dir=self.root).surface, "TERM")

    def test_guardian_subagent_is_explicitly_internal(self):
        header = self.header()
        header["payload"]["source"] = {"subagent": {"other": "guardian"}}
        self.write(header)
        self.assertTrue(is_internal_session(self.payload, sessions_dir=self.root))

        header["payload"]["source"] = "vscode"
        self.write(header)
        self.assertFalse(is_internal_session(self.payload, sessions_dir=self.root))

    def test_outside_path_and_mismatched_header_are_rejected(self):
        outside = self.root.parent / f"outside-{self.SESSION}.jsonl"
        outside.write_text(json.dumps(self.header()) + "\n")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        self.payload["transcript_path"] = str(outside)
        self.assertIsNone(rollout_path(self.payload, sessions_dir=self.root))
        self.write({"type": "session_meta", "payload": {"id": "wrong"}})
        self.payload["transcript_path"] = str(self.path)
        self.assertEqual(inspect_rollout(self.payload, sessions_dir=self.root).surface, "")

    def test_funky_names_are_stable_and_readable(self):
        name = funky_name(self.SESSION)
        self.assertEqual(name, funky_name(self.SESSION))
        self.assertEqual(len(name.split()), 2)
        self.assertLessEqual(len(name), 18)

    def test_terminal_hint_uses_recorded_origin(self):
        self.assertEqual(terminal_surface({"_nyx": {"tty": "/dev/ttys001"}}), "TERM")
        self.assertEqual(terminal_surface({"_nyx": {}}), "")

    def test_incremental_reader_observes_completion(self):
        self.write(self.header(), {"type": "event_msg", "payload": {"type": "task_started"}})
        offset = self.path.stat().st_size
        with self.path.open("a") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "turn_context",
                        "payload": {
                            "model": "gpt-5.6-sol",
                            "effort": "medium",
                            "approvals_reviewer": "auto_review",
                        },
                    }
                )
                + "\n"
            )
            handle.write(
                json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n"
            )
        info, next_offset = inspect_updates(self.path, offset)
        self.assertEqual((info.status, info.model, info.effort), ("IDLE", "gpt-5.6-sol", "medium"))
        self.assertEqual(info.approvals_reviewer, "auto_review")
        self.assertEqual(next_offset, self.path.stat().st_size)

    def test_dead_origin_is_removed_only_after_grace(self):
        from nyx.controller import Controller

        now = [100.0]
        core = Controller()
        core.event(
            {
                "hook_event_name": "SessionStart",
                "session_id": self.SESSION,
                "cwd": "/tmp/project",
                "_nyx": {"origin_pid": 4321},
            },
            False,
        )
        observer = SessionObserver(
            core,
            pid_is_alive=lambda _pid: False,
            clock=lambda: now[0],
            liveness_grace=3,
        )
        observer.poll_once()
        self.assertEqual(core.snapshot()["count"], 1)
        now[0] += 2.9
        observer.poll_once()
        self.assertEqual(core.snapshot()["count"], 1)
        now[0] += 0.1
        observer.poll_once()
        self.assertEqual(core.snapshot()["count"], 0)

    def test_live_origin_and_unknown_pid_are_never_removed(self):
        from nyx.controller import Controller

        core = Controller()
        for session, metadata in [("live", {"origin_pid": 22}), ("old", {})]:
            core.event(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": session,
                    "cwd": "/tmp/project",
                    "_nyx": metadata,
                },
                False,
            )
        observer = SessionObserver(core, pid_is_alive=lambda _pid: True, liveness_grace=0)
        observer.poll_once()
        self.assertEqual(core.snapshot()["count"], 2)

    @patch("nyx.session_info.os.kill")
    def test_process_liveness_check_fails_open_safely(self, kill):
        self.assertTrue(process_alive(22))
        kill.side_effect = ProcessLookupError
        self.assertFalse(process_alive(22))
        kill.side_effect = PermissionError
        self.assertTrue(process_alive(22))
        self.assertTrue(process_alive(None))
