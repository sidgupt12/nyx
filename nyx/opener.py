"""Best-effort focus of the known origin. Never type into any terminal."""

import logging
import subprocess

LOG = logging.getLogger(__name__)


def open_session(payload):
    metadata = payload.get("_nyx", {})
    term = str(metadata.get("term_program", "")).lower()
    tty = str(metadata.get("tty", ""))
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
        elif metadata.get("host") == "codex":
            _run(["open", "-b", "com.openai.codex"])
        elif term in {"ghostty", "wezterm"}:
            _run(["open", "-a", "Ghostty" if term == "ghostty" else "WezTerm"])
        else:
            LOG.info("Cannot identify this session's window; no app opened")
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("Could not focus session: %s", exc)


def _run(command):
    subprocess.run(command, capture_output=True, check=True, timeout=2)
