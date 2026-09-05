"""Short-lived Codex hook client. Missing bridge means normal Codex behavior."""

import json
import os
import socket
import subprocess
import sys

from .protocol import APPROVAL_SECONDS, EVENTS, MAX_HOOK, encode, receive, socket_path


def origin():
    metadata = {
        name: os.environ.get(env, "")
        for name, env in {
            "term_program": "TERM_PROGRAM",
            "iterm_session_id": "ITERM_SESSION_ID",
            "vscode_pid": "VSCODE_PID",
            "term_session_id": "TERM_SESSION_ID",
            "vscode_ipc_hook_cli": "VSCODE_IPC_HOOK_CLI",
        }.items()
    }
    try:
        tty = subprocess.check_output(
            ["ps", "-o", "tty=", "-p", str(os.getppid())],
            text=True,
            timeout=0.2,
            stderr=subprocess.DEVNULL,
        ).strip()
        metadata["tty"] = "/dev/" + tty if tty and tty != "??" else ""
    except (OSError, subprocess.SubprocessError):
        metadata["tty"] = ""
    return metadata


def run_hook(stdin=None, stdout=None, path=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        raw = stdin.read(MAX_HOOK + 1)
        if len(raw.encode()) > MAX_HOOK:
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS:
            return 0
        payload["_nyx"] = origin()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.3)
            connection.connect(str(path or socket_path()))
            connection.sendall(encode(payload))
            response = receive(connection)
            if response.get("wait") is True:
                connection.settimeout(APPROVAL_SECONDS + 1)
                response = receive(connection)
            decision = response.get("decision")
            if payload["hook_event_name"] == "PermissionRequest" and decision in {"allow", "deny"}:
                json.dump(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PermissionRequest",
                            "decision": {"behavior": decision},
                        }
                    },
                    stdout,
                )
                stdout.write("\n")
    except (OSError, ValueError, TypeError):
        pass  # Empty stdout: Codex keeps its own approval policy and prompt.
    return 0


def main():
    raise SystemExit(run_hook())


if __name__ == "__main__":
    main()
