# nyx

A local, headless bridge between **existing Codex sessions** and an ESP32 desk controller.
Python on your Mac; a small Arduino/C++ program on the board. No notification app,
web dashboard, network port, Wi-Fi, or new Codex session.

## What this version does

- Shows hook-observed IDLE/RUNNING state on a 1.3-inch SH1106 OLED.
- Lets the encoder select between sessions and pending requests.
- Opens a known origin when the encoder is pressed (window-routing limits below).
- Reconnects after USB loss; clears old permission decisions on disconnect/reboot.
- Optionally relays physical approve/reject decisions through real Codex hooks.

**Important: physical approvals are opt-in, not enabled by default.** Codex's
PermissionRequest hook runs *before* the normal approval flow, not after automatic
review. The documented input cannot reliably distinguish requests that "Approve
for me" will handle from requests eventually requiring you. We do not claim that
problem is solved. Use normal/passive mode with automatic review. Manual mode gives
you a bounded 20-second hardware decision window, then falls back to Codex.
See [the exact limitation and safety rules](docs/architecture.md).

## Find your way around

```text
nyx/                    Python code running on the Mac
  controller.py         states, session selection, one-shot decisions
  listener.py           local hook connections
  hardware.py           USB connection and reconnect
  hooks.py              short-lived Codex hook client
  hook.py               file entry point Codex runs from any directory
  opener.py             best-effort focus of the originating app
  installer.py          reversible hook configuration
  protocol.py           JSON format constants and framing
  __main__.py           the command-line controls
firmware/
  include/pins.h        all wiring choices in one place
  src/main.cpp          display, encoder, buttons, USB
  platformio.ini        board and pinned library versions
tests/                  automated tests; no physical board needed
docs/                   wiring, architecture, and USB protocol
```

Start reading with `nyx/controller.py` and `firmware/include/pins.h`.
You do not need to understand networking or JavaScript.

## 1. Set up the Mac

Requires macOS, Python 3.10+, and a Codex client with trusted command hooks.
The interface was researched against Codex CLI 0.153.0. Other client versions
need their own live verification; merely having Codex installed is not enough.

From the repository folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -v
```

All Python runtime dependencies are listed in `pyproject.toml`; only pyserial is needed.
Run the activation command again in each new terminal.

## 2. Build the board code

The target matches the existing hardware test project: **ESP32-S3-DevKitC-1**
and **SH1106 128×64 I2C OLED**, not an ESP32-C3. Confirm your board before flashing.
Read [wiring and first power-on](docs/wiring.md) first.

With the Python environment active:

```sh
python -m pip install platformio==6.1.19
pio run -d firmware
nyx ports
```

A successful build creates firmware locally; it does **not** change the board.
When you are ready, replace the example port with your actual board's port:

```sh
pio run -d firmware -t upload --upload-port /dev/cu.usbmodemYOUR_BOARD
```

Flashing replaces the previous program on the board. Keep your separate hardware
Testing project so you can reflash it if needed. Close PlatformIO Serial Monitor
before running Nyx: only one program should own the serial connection.

## 3. Install hooks and run

```sh
nyx install-hooks
nyx run --port /dev/cu.usbmodemYOUR_BOARD
```

The installer preserves other tools' hooks and makes a dated backup of hooks.json
before each actual change. Restart existing Codex sessions and review/trust Nyx's
changed hooks using the client's hook controls (`/hooks` in the CLI).
Start a new turn; Nyx cannot reconstruct events that happened before it started.

Expected device sequence: **BRIDGE OFFLINE → WAITING FOR CODEX → RUNNING → IDLE**.
There are no test notifications. The old notification implementation is removed.

When the foreground test works, stop it with Ctrl+C and use:

```sh
nyx start --port /dev/cu.usbmodemYOUR_BOARD
nyx status
nyx stop
```

`start` detaches a Python process and checks startup; it does not use nohup.
It runs until stopped, logout/reboot, or failure. **Autostart at login is not installed.**
After reboot, run `nyx start ...` again. Logs live in `~/.codex/nyx/bridge.log`.
The USB worker reconnects every second when disconnected. No claims of measured battery use.

## Optional: physical approval test

Use a Codex session configured for **manual/user review**, not "Approve for me".
First stop the passive bridge, then:

```sh
nyx run --port /dev/cu.usbmodemYOUR_BOARD --manual-approvals
```

When a real PermissionRequest hook fires with the device connected:

1. The OLED shows the project/session, request preview, and remaining time.
2. Hold the approve button for 700 ms to allow that request once; or press reject.
3. Encoder press requests Open. Inspect the full operation in Codex when the tiny
   preview is insufficient. Never approve an operation you do not understand.
4. If nobody answers within 20 seconds, Nyx makes **no decision** and Codex proceeds
   with its normal approval flow. The hardware button is then invalid.

The normal Codex prompt can be delayed during this optional hook window.
This is **not** simultaneous control of an already-visible approval dialog.
No missing device, expired request, or error ever means "approve".

## Known limits

- No reliable post-auto-review human-only approval feed yet; passive mode is the default.
- Hooks observe events only after installation/trust. Crashed clients can leave a last-known
  RUNNING state; the bridge does not scrape logs or infer live process state.
- Terminal/iTerm exact tab targeting requires valid origin metadata. VS Code is app-level
  focus only. Unknown origins do not open a random Terminal. Exact desktop-thread routing
  is not implemented. See [architecture](docs/architecture.md).
- No effort/model/approval-policy knob changes, 13-key layout, joystick, LEDs, or speaker yet.
  The encoder currently selects sessions, not reasoning effort.
- A C3 needs a separate pin/USB configuration; do not flash this S3 firmware to it.

## Test and remove

```sh
python -m unittest discover -v
pio run -d firmware
nyx stop
nyx uninstall-hooks
```

Tests use isolated local sockets, hook subprocesses, and a pseudo-terminal for the
real pyserial transport. They do not change global Codex config, approve live work,
or flash hardware. A compiler success is not a physical wiring test.

For consistent Python formatting when contributing:

```sh
python -m pip install -e '.[dev]'
ruff check nyx tests
ruff format nyx tests
```

Uninstall removes only this checkout's Nyx hook commands. Review/restart Codex
again after configuration changes. Source code, wiring notes, and dependency
manifests belong in Git; environments, binaries, logs, local instructions, and
secrets do not.
