"""Command line entry points for the passive Codex listener."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from .installer import install_global_hook
from .listener import BackgroundListener, listener_is_running


PID_PATH = Path.home() / ".codex" / "nyx.pid"
LOG_PATH = Path.home() / ".codex" / "nyx.log"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyx",
        description="Passive macOS notifications for Codex permission requests.",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("start", "start the listener in the background"),
        ("stop", "stop the background listener"),
        ("status", "check whether the listener is running"),
        ("listener", "run the listener in the foreground"),
        ("install-hooks", "add the global Codex PermissionRequest hook"),
    ):
        subparsers.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "listener"
    if command == "listener":
        listener = BackgroundListener()
        try:
            listener.serve_forever()
        except KeyboardInterrupt:
            pass
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        finally:
            listener.stop()
        return 0
    if command == "start":
        return _start_background()
    if command == "stop":
        return _stop_background()
    if command == "status":
        running = listener_is_running()
        print("running" if running else "not running")
        return 0 if running else 1
    if command == "install-hooks":
        path = install_global_hook()
        print(f"Installed global Codex hook in {path}")
        print("Restart Codex sessions, then approve this hook once in Codex's /hooks screen.")
        return 0
    raise AssertionError(f"unhandled command: {command}")


def _start_background() -> int:
    if listener_is_running():
        print("Nyx listener is already running")
        return 0
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parent.parent
    log_handle = LOG_PATH.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "nyx", "listener"],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log_handle.close()
    PID_PATH.write_text(str(process.pid), encoding="utf-8")
    print(f"Nyx listener started (pid {process.pid})")
    print(f"Log: {LOG_PATH}")
    return 0


def _stop_background() -> int:
    if not PID_PATH.exists():
        print("Nyx listener is not running")
        return 0
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        PID_PATH.unlink()
    except FileNotFoundError:
        pass
    print("Nyx listener stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
