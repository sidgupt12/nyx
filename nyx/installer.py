"""Install/uninstall only Nyx's hooks, preserving other tools' configuration."""

import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time

from .protocol import APPROVAL_SECONDS, EVENTS, runtime_dir

LABEL = "Nyx hardware bridge"


def hook_command():
    return shlex.join([sys.executable, str(Path(__file__).with_name("hook.py").resolve())])


def is_ours(handler):
    if not isinstance(handler, dict):
        return False
    try:
        parts = shlex.split(handler.get("command", ""))
    except ValueError:
        return False
    # Recognize the exact file previously installed by this checkout, even if
    # the Python interpreter changed. Never use a broad substring match.
    target = str(Path(__file__).with_name("hook.py").resolve())
    return len(parts) == 2 and parts[1] == target


def install_hooks(*, path=None, uninstall=False):
    path = Path(path) if path else runtime_dir().parent / "hooks.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain a hooks object")
    hooks = data.setdefault("hooks", {})
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"{event} must be a list")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"Invalid {event} hook group; refusing to overwrite it")
            group["hooks"] = [h for h in group["hooks"] if not is_ours(h)]
        # Remove only emptied groups; there are no executable hooks in them.
        hooks[event] = [g for g in groups if g["hooks"]]
        if not uninstall:
            hooks[event].append(
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_command(),
                            "timeout": APPROVAL_SECONDS + 4 if event == "PermissionRequest" else 2,
                            "statusMessage": LABEL,
                        }
                    ],
                }
            )
        elif not hooks[event]:
            hooks.pop(event)
    rendered = json.dumps(data, indent=2) + "\n"
    if path.exists() and path.read_text() == rendered:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.nyx-backup-{time.time_ns()}"))
    temporary = path.with_suffix(".nyx-tmp")
    # Owner-only from creation, not just after writing.
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(rendered)
    os.replace(temporary, path)
    return path
