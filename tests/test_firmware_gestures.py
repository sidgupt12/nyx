"""Compile the actual firmware gesture header on the host; no hardware needed."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class GestureTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler not installed")
    def test_single_double_timeout_reset_and_clock_wrap(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "test.cpp"
            binary = Path(folder) / "test"
            source.write_text("""
#include <cassert>
#include "gestures.h"
int main() {
  ModeGesture g;
  assert(g.update(true, 100) == 0);
  assert(g.update(false, 420) == 0);
  assert(g.update(false, 421) == 1);
  assert(g.update(false, 900) == 0);
  assert(g.update(true, 1000) == 0);
  assert(g.update(true, 1200) == 2);
  assert(g.update(false, 1600) == 0);
  g.update(true, 2000); g.reset();
  assert(g.update(false, 3000) == 0);
  g.update(true, UINT32_MAX - 100);
  assert(g.update(false, 250) == 1);
}
""")
            include = Path(__file__).resolve().parents[1] / "firmware" / "include"
            subprocess.run(
                ["c++", "-std=c++11", "-I", str(include), str(source), "-o", str(binary)],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)
