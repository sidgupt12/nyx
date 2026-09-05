import json
import tempfile
import unittest
from pathlib import Path
from nyx.installer import install_hooks, hook_command
from nyx.protocol import EVENTS


class InstallerTests(unittest.TestCase):
    def test_preserves_other_hooks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hooks.json"
            other = {"type": "command", "command": "echo existing"}
            path.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [other]}]}}))
            install_hooks(path=path)
            first = path.read_text()
            install_hooks(path=path)
            self.assertEqual(first, path.read_text())
            data = json.loads(first)["hooks"]
            self.assertEqual(data["SessionStart"][0]["hooks"], [other])
            for name in EVENTS:
                self.assertEqual(data[name][-1]["hooks"][0]["command"], hook_command())
            self.assertEqual(len(list(path.parent.glob("*.nyx-backup-*"))), 1)
            install_hooks(path=path, uninstall=True)
            self.assertEqual(
                json.loads(path.read_text())["hooks"], {"SessionStart": [{"hooks": [other]}]}
            )

    def test_invalid_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hooks.json"
            for original in ["[]", '{"hooks":{"Stop":"wrong"}}', "invalid"]:
                path.write_text(original)
                with self.assertRaises(ValueError):
                    install_hooks(path=path)
                self.assertEqual(path.read_text(), original)

    def test_migrates_old_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hooks.json"
            path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PermissionRequest": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": hook_command(), "timeout": 5}
                                    ]
                                }
                            ]
                        }
                    }
                )
            )
            install_hooks(path=path)
            groups = json.loads(path.read_text())["hooks"]["PermissionRequest"]
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["hooks"][0]["timeout"], 24)
