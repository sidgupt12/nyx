"""Best-effort focus of the known origin. Never type into any terminal."""

import logging
import subprocess
import shlex
from pathlib import Path
from uuid import UUID

from .session_info import inspect_rollout

LOG = logging.getLogger(__name__)


def open_session(payload):
    metadata = payload.get("_nyx", {})
    term = str(metadata.get("term_program", "")).lower()
    tty = str(metadata.get("tty", ""))
    if metadata.get('shared_server'):
        tty = remote_terminal_tty(payload.get('session_id', ''))
        if not tty:
            LOG.info('Shared-server terminal is not uniquely identifiable; no window opened')
            return
        # Terminal's scripting interface still checks that this exact TTY exists.
        # It cannot accidentally activate the daemon's original terminal.
        term = 'apple_terminal'
    try:
        if term == "apple_terminal" and tty:
            script = """on run argv
tell application "Terminal"
repeat with w in windows
repeat with t in tabs of w
if (tty of t as text) is (item 1 of argv) then
set selected tab of w to t
set index of w to 1
activate
return
end if
end repeat
end repeat
end tell
end run"""
            _run(["osascript", "-e", script, tty])
        elif "iterm" in term and metadata.get("iterm_session_id"):
            script = """on run argv
tell application "iTerm2"
repeat with w in windows
repeat with t in tabs of w
repeat with s in sessions of t
if (unique id of s as text) is (item 1 of argv) then
select s
select t
set index of w to 1
activate
return
end if
end repeat
end repeat
end repeat
end tell
end run"""
            _run(["osascript", "-e", script, metadata["iterm_session_id"].rsplit(":", 1)[-1]])
        elif "vscode" in term or metadata.get("vscode_pid"):
            # Do not replace the user's current workspace or open an arbitrary
            # integrated terminal. Exact VS Code terminal routing needs an extension.
            _run(["open", "-a", "Visual Studio Code"])
        elif term in {"ghostty", "wezterm"}:
            _run(["open", "-a", "Ghostty" if term == "ghostty" else "WezTerm"])
        else:
            link = desktop_link(payload)
            if link:
                # The installed ChatGPT/Codex desktop app registers this bundle
                # and URL scheme. Include the ID to select the actual task.
                _run(["open", "-b", "com.openai.codex", link])
            else:
                LOG.info("Cannot identify this session's window; no app opened")
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Could not focus session: %s", exc)


def _run(command):
    subprocess.run(command, capture_output=True, check=True, timeout=2)


def remote_terminal_tty(session_id):
    """Match an explicit `codex resume --remote unix:// SESSION_ID` process.

    New tasks without an ID in their command line, multiple UIs for one task,
    and unsupported terminal apps deliberately have no guessed Open target.
    """
    try:
        if str(UUID(session_id)) != session_id:
            return ''
        output = subprocess.run(
            ['ps', '-axo', 'tty=,comm=,args='], capture_output=True,
            text=True, check=True, timeout=1,
        ).stdout
        matches = set()
        for line in output.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) != 3 or Path(parts[1]).name != 'codex':
                continue
            tty, _, command = parts
            args = shlex.split(command)
            if 'resume' not in args or '--remote' not in args:
                continue
            remote_index = args.index('--remote') + 1
            if remote_index >= len(args) or args[remote_index] != 'unix://':
                continue
            if session_id in args[args.index('resume') + 1:] and tty.startswith('ttys'):
                matches.add('/dev/' + tty)
        return matches.pop() if len(matches) == 1 else ''
    except (ValueError, OSError, subprocess.SubprocessError):
        return ''


def desktop_link(payload, *, sessions_dir=None):
    """Identify desktop tasks from their local session header, not a missing TTY.

    Only invoked on Open, not polled. Read no conversation messages: just the
    bounded first session_meta record. This is a version-dependent fallback
    because hooks do not provide a documented desktop-origin field.
    """
    session_id = payload.get("session_id")
    try:
        if not isinstance(session_id, str) or str(UUID(session_id)) != session_id:
            return None
    except ValueError:
        return None
    if inspect_rollout(payload, sessions_dir=sessions_dir).surface == "APP":
        return f"codex://threads/{session_id}"
    return None
