import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from nyx.listener import control
from tests.test_listener import until


class CliTests(unittest.TestCase):
    def test_background_start_status_stop_without_hardware(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            path = Path(folder) / "nyx" / "bridge.sock"
            env = {**os.environ, "CODEX_HOME": folder}
            command = [sys.executable, "-m", "nyx"]
            try:
                started = subprocess.run(
                    command + ["start", "--port", "/dev/nyx-missing"],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=6,
                )
                self.assertEqual(started.returncode, 0, started.stderr + started.stdout)
                status = control(path=path)
                self.assertTrue(status["ok"])
                self.assertFalse(status["device"])
                self.assertFalse(status["manual_approvals"])
                stopped = subprocess.run(
                    command + ["stop"], env=env, text=True, capture_output=True, timeout=2
                )
                self.assertEqual(stopped.returncode, 0)
                until(lambda: control(path=path) is None)
            finally:
                control("stop", path=path)
