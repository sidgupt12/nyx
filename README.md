# Nyx

This is a small, background listener for real Codex sessions. It does not
start Codex or open a dashboard. Codex calls one hook when a session reaches a
permission request; the listener shows a native macOS notification and never
blocks Codex's own approval flow.

## First-time setup

Run these from this project folder:

```bash
python3 -m nyx install-hooks
python3 -m nyx start
```

The **Open Codex** notification action requires the signed `alerter` helper,
which can be installed on macOS with:

```bash
brew install vjeantet/tap/alerter
```

The first command adds one `PermissionRequest` entry to your global
`~/.codex/hooks.json` and keeps any hooks you already have. It creates a
`.bak` backup when that file already exists. Codex may ask you to approve the
new hook in its `/hooks` screen; that is Codex's safety check. Approve it once.

The second command starts the listener in the background. It writes its PID
to `~/.codex/nyx.pid` and logs startup/errors to
`~/.codex/nyx.log`.

Now restart any Codex sessions. From then on, a permission request in a Codex
terminal or desktop session produces a macOS notification while the normal
Codex prompt remains in the session that owns it.

Useful commands:

```bash
python3 -m nyx status
python3 -m nyx stop
```

## Important behavior

- **Open Codex** focuses the terminal that owns the request.
- Allow/reject remain in Codex's own prompt. The listener intentionally does
  not intercept those decisions, so a missing notification can never freeze
  Codex.
- If `alerter` is not installed, the listener falls back to a basic
  notification and leaves approval in Codex.
- Hooks are loaded when a Codex session starts. Restart sessions that were
  already open when the hook was installed.
- Codex invokes the hook before its final approval routing. If a session uses
  automatic review, a notification can be produced even when no human action
  is ultimately needed.
- The listener uses a private Unix socket at
  `~/.codex/nyx.sock`; it never exposes a network port.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Project shape

```text
Codex session -> PermissionRequest hook -> local Unix socket
                                      -> BackgroundListener -> macOS notification
```

The files stay deliberately small:

- `listener.py` — socket server and notification message formatting.
- `hooks.py` / `hook.py` — the Codex hook bridge.
- `installer.py` — safe, idempotent global `hooks.json` merge.
- `notifications.py` — macOS Notification Center and the Open Codex alert.
- `__main__.py` — start/stop/status/install commands.
