import json
import tempfile
import unittest
from pathlib import Path

from nyx.installer import install_global_hook


class InstallerTests(unittest.TestCase):
    def test_installer_preserves_existing_hooks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks.json"
            path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {"hooks": [{"type": "command", "command": "echo existing"}]}
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            command = "/usr/bin/python3 /tmp/nyx-hook.py"
            install_global_hook(hooks_path=path, hook_command=command)
            install_global_hook(hooks_path=path, hook_command=command)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["hooks"]["SessionStart"]), 1)
            permission_groups = data["hooks"]["PermissionRequest"]
            self.assertEqual(len(permission_groups), 1)
            self.assertEqual(len(permission_groups[0]["hooks"]), 1)
            self.assertTrue(path.with_suffix(".json.bak").exists())


if __name__ == "__main__":
    unittest.main()
