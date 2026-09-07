"""Short-lived Codex hook client. Missing bridge means normal Codex behavior."""

import json
import os
import socket
import subprocess
import sys

from .protocol import APPROVAL_SECONDS, EVENTS, MAX_HOOK, encode, receive, socket_path
from .session_info import inspect_rollout, is_internal_session


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
    pid, tty = codex_process()
    if pid is not None:
        # This is only a liveness identity. Nyx never controls the process.
        metadata["origin_pid"] = pid
    metadata["tty"] = tty
    return metadata


def codex_process():
    """Find the verified Codex ancestor and the hook launcher's TTY."""
    pid = os.getppid()
    tty = ""
    for depth in range(8):
        if pid <= 1:
            break
        try:
            output = subprocess.check_output(
                ["ps", "-o", "ppid=,tty=,comm=", "-p", str(pid)],
                text=True,
                timeout=0.2,
                stderr=subprocess.DEVNULL,
            ).strip()
            parent_text, current_tty, command = output.split(maxsplit=2)
            parent = int(parent_text)
        except (OSError, ValueError, subprocess.SubprocessError):
            break
        if depth == 0 and current_tty != "??":
            tty = "/dev/" + current_tty
        if os.path.basename(command).lower() == "codex":
            return pid, tty
        pid = parent
    return None, tty


def run_hook(stdin=None, stdout=None, path=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        raw = stdin.read(MAX_HOOK + 1)
        if len(raw.encode()) > MAX_HOOK:
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("hook_event_name") not in EVENTS:
            return 0
        # Guardian/reviewer subagents are implementation details, not chats the
        # user can select or open. Their rollout explicitly identifies them.
        if is_internal_session(payload):
            return 0
        payload["_nyx"] = origin()
        # Permission hooks do not document who will review the request. Current
        # session metadata does, so copy only that setting for Nyx's filter.
        if payload["hook_event_name"] == "PermissionRequest" and isinstance(
            payload.get("transcript_path"), str
        ):
            reviewer = inspect_rollout(payload).approvals_reviewer
            if reviewer:
                payload["_nyx"]["approvals_reviewer"] = reviewer
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
