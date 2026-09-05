import unittest
from unittest.mock import patch
from nyx.opener import open_session


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
