"""Small CLI: install hooks, run/start/stop the bridge, and list USB ports."""

import argparse
import logging
from pathlib import Path
import signal
import subprocess
import sys
import time

from .installer import install_hooks
from .listener import Bridge, control
from .protocol import runtime_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="Nyx: Codex to ESP32, no GUI or notifications.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "start"):
        command = commands.add_parser(name, help=f"{name} the USB bridge")
        command.add_argument("--port", required=True, help="USB serial path; see 'nyx ports'")
        command.add_argument(
            "--manual-approvals",
            action="store_true",
            help="opt in to a 20-second pre-prompt approval window; NOT for Approve for me",
        )
    for name in ("stop", "status", "ports", "install-hooks", "uninstall-hooks"):
        commands.add_parser(name)
    args = parser.parse_args(argv)
    try:
        if args.command == "ports":
            from serial.tools.list_ports import comports

            for port in comports():
                print(f"{port.device}  {port.description}")
            return 0
        if args.command in {"install-hooks", "uninstall-hooks"}:
            path = install_hooks(uninstall=args.command == "uninstall-hooks")
            print(f"Updated {path}")
            print("Restart Codex sessions and review/trust the changed hooks with /hooks.")
            return 0
        if args.command == "status":
            status = control()
            if not status:
                print("Nyx is not running.")
                return 1
            print(
                f"Bridge running (pid {status['pid']}); device: "
                f"{'connected' if status['device'] else 'disconnected'}; "
                f"manual approvals: {status['manual_approvals']}"
            )
            return 0
        if args.command == "stop":
            if control("stop"):
                print("Stop requested. Codex will keep running.")
            else:
                print("Nyx is not running.")
            return 0
        if args.manual_approvals:
            print(
                "Manual mode: requests may wait up to 20 seconds before Codex's prompt.",
                file=sys.stderr,
            )
            print(
                "Do not use this mode with Approve for me; see docs/architecture.md.",
                file=sys.stderr,
            )
        if args.command == "start":
            if control():
                print("Already running. Stop it first to change port or approval mode.")
                return 1
            runtime_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
            log = runtime_dir() / "bridge.log"
            command = [sys.executable, "-m", "nyx", "run", "--port", args.port]
            if args.manual_approvals:
                command.append("--manual-approvals")
            with log.open("a") as handle:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=handle,
                    start_new_session=True,
                    cwd=str(Path(__file__).resolve().parent.parent),
                )
            for _ in range(30):
                if process.poll() is not None:
                    break
                status = control()
                if status and status.get("pid") == process.pid:
                    print(f"Bridge started; it will reconnect when USB is available. Log: {log}")
                    return 0
                time.sleep(0.1)
            print(f"Could not confirm startup. Check {log}", file=sys.stderr)
            return 1
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        bridge = Bridge(args.port, manual_approvals=args.manual_approvals)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: bridge.stopped.set())
        bridge.run()
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Nyx: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
