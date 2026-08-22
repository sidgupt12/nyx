"""Install the small global Codex hook without replacing other hooks."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict


DEFAULT_HOOKS_PATH = Path.home() / ".codex" / "hooks.json"
HOOK_TIMEOUT_SECONDS = 5


def install_global_hook(
    *,
    hooks_path: Path = DEFAULT_HOOKS_PATH,
    hook_command: str | None = None,
) -> Path:
    """Merge our PermissionRequest hook into Codex's global hooks.json.

    Existing JSON content is preserved.  A dated backup is made before an
    existing file is changed.
    """

    hooks_path = Path(hooks_path).expanduser()
    hook_command = hook_command or _default_hook_command()
    if hooks_path.exists():
        with hooks_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Codex hooks.json must contain a JSON object")
        backup_path = hooks_path.with_suffix(hooks_path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(hooks_path, backup_path)
    else:
        data = {}

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Codex hooks.json 'hooks' value must be an object")
    groups = hooks.setdefault("PermissionRequest", [])
    if not isinstance(groups, list):
        raise ValueError("Codex hooks.json PermissionRequest value must be an array")

    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks", [])
        if any(
            isinstance(handler, dict)
            and handler.get("type") == "command"
            and handler.get("command") == hook_command
            for handler in handlers
        ):
            for handler in handlers:
                if (
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and handler.get("command") == hook_command
                ):
                    handler["timeout"] = HOOK_TIMEOUT_SECONDS
            _write_json(hooks_path, data)
            return hooks_path

    groups.append(
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command,
                    "timeout": HOOK_TIMEOUT_SECONDS,
                    "statusMessage": "sending Codex permission notification",
                }
            ],
        }
    )
    _write_json(hooks_path, data)
    return hooks_path


def _default_hook_command() -> str:
    hook_path = Path(__file__).resolve().with_name("hook.py")
    return f"{_python_executable()} {hook_path}"


def _python_executable() -> str:
    # The hook is launched by Codex from arbitrary project directories.  The
    # current interpreter is the one that can import this local project.
    import sys

    return sys.executable


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
