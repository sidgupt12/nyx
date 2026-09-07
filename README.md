# nyx

A local, headless bridge between **existing Codex sessions** and an ESP32 desk controller.
Python on your Mac; a small Arduino/C++ program on the board. No notification app,
web dashboard, network port, Wi-Fi, or new Codex session.

## What this version does

- Shows hook-observed IDLE/RUNNING state on a 1.3-inch SH1106 OLED.
- Lets the encoder select between sessions and pending requests.
- Opens a known origin when the encoder is pressed (window-routing limits below).
- Reconnects after USB loss; clears old permission decisions on disconnect/reboot.
- Removes a stale session shortly after its originating Codex process exits.
- Answers native command/file approvals from the hardware or the normal Codex UI.

**Native approvals are now the default.** Hooks return immediately; the bridge
follows the actual pending requests exposed by the desktop app or a shared
terminal server. An automatic approval needs no button press. If a request reaches
the human UI, Nyx can answer that same request. Answering in Codex clears Nyx's button.
The desktop adapter uses a private, version-dependent local interface.
Standalone terminal sessions must be reconnected as described below.

## Find your way around

```text
nyx/                    Python code running on the Mac
  controller.py         states, session selection, one-shot decisions
  native.py             live request IDs and one-shot hardware actions
  desktop.py            desktop app's local follower connection
  appserver.py          shared terminal server connection
  session_info.py       friendly names + bounded desktop metadata reader
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
The native terminal interface was tested against Codex CLI 0.153.4. Other client versions
need their own live verification; merely having Codex installed is not enough.

From the repository folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -v
```

Runtime dependencies are pinned in `pyproject.toml`: pyserial for USB and
websockets for the terminal server's local Unix-socket connection.
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

Use the exact port printed by `nyx ports`. The tested dual-USB board uses its
USB/UART connector for both uploading and Nyx data; macOS describes it as
`USB Single Serial`.

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

## 4. Connect native approvals

Desktop: run Nyx normally, then send a message in the desktop task. Trusted hooks
identify the task; Nyx follows its live state. `nyx status` prints
`Native desktop: connected` after the app's socket handshake succeeds.

Terminal: Codex and Nyx must share the same running backend. Start Codex's official
local daemon once, then launch the usual terminal UI connected to it:

```sh
codex app-server daemon start
codex --remote unix://
```

The daemon command requires Codex's managed standalone installation. A Homebrew
CLI alone may report `managed standalone Codex install not found`. In that case,
complete the official Codex standalone setup before using the daemon; Nyx does
not install or replace Codex automatically. Desktop observation does not require
this terminal daemon.

Use that second command in each terminal (including VS Code's terminal). Nyx does
not launch the terminal UI. The socket is local to your account; no TCP port opens.
For an existing standalone session, finish/interrupt its current operation, exit
that CLI, and resume its exact session ID:

```sh
codex resume --remote unix:// YOUR_SESSION_ID
```

Do not run the same saved session simultaneously in its old standalone process
and the shared server. Already-running standalone CLIs cannot be attached by this
adapter. Shared-server sessions may continue when their terminal UI closes.
Hooks running in a shared daemon may lack the individual terminal's TTY; exact
Open routing for these sessions is not yet guaranteed.

When connected, the OLED displays the native request. Hold YES for 700 ms to
allow once, or press NO to decline. If Codex offers Cancel instead of Decline,
NO cancels the turn, matching its native refusal choice. You can also use the
normal on-screen buttons. There is no Nyx countdown for native requests.
Broader session grants and input forms must be answered in Codex.

## Legacy hook-only approval test

Use a Codex session configured for **manual/user review**, not "Approve for me".
First stop the passive bridge, then:

```sh
nyx run --port /dev/cu.usbmodemYOUR_BOARD --manual-approvals
```

When a real PermissionRequest hook fires with the device connected:

1. The OLED shows a stable funky session name, App/Terminal icon, live state,
   model, effort, request preview, and remaining time.
2. Hold the approve button for 700 ms to allow that request once; or press reject.
3. Encoder press requests Open. Inspect the full operation in Codex when the tiny
   preview is insufficient. Never approve an operation you do not understand.
4. If nobody answers within 20 seconds, Nyx makes **no decision** and Codex proceeds
   with its normal approval flow. The hardware button is then invalid.

The normal Codex prompt can be delayed during this optional hook window.
This is **not** simultaneous control of an already-visible approval dialog.
No missing device, expired request, or error ever means "approve".

## Known limits

- Desktop live state protocol version 11 was inspected and tested on this Mac.
  App updates may require updating this adapter; unknown protocol versions disable
  native buttons. It is not a documented third-party API.
- Shared terminal approvals were verified against Codex CLI 0.153.4 using a real
  local server and a local fake model. Standalone CLIs and the VS Code extension's
  separate backend are not covered by the terminal adapter.
- A connected transport is not proof that every session is subscribed: sessions
  still need their trusted hooks to run after Nyx starts.
- Hooks observe events only after installation/trust. Current desktop builds do not always
  deliver `Stop`, so Nyx performs one bounded transcript catch-up and then follows only new
  lifecycle metadata. Terminal status still comes from hooks. Each hook also records its
  originating Codex process ID; Nyx removes that session after the process has been gone for
  three seconds. This catches a closed terminal process or Cmd+Q without treating chat
  switching, inactivity, or Cmd+W as a finished session.
- Terminal/iTerm exact tab targeting requires valid origin metadata. VS Code is app-level
  focus only. Desktop tasks marked `codex_work_desktop` open their exact conversation
  through the installed app's deep link. Unknown origins do not open a random Terminal.
  See [architecture](docs/architecture.md) for the version-dependent metadata fallback.
- Model and effort are display-only. No effort/approval-policy knob changes, 13-key layout,
  joystick, LEDs, or speaker yet. The encoder currently selects sessions, not reasoning effort.
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

An opt-in test runs a real temporary Codex server with a local fake model. It
creates a validation session and archives it afterward. It never calls an OpenAI
model or sends anything to your board. The approve case executes only
`printf nyx-native-validation` in a temporary folder:

```sh
python tests/validate_native_server.py --action reject
python tests/validate_native_server.py --action approve
python tests/validate_native_server.py --action native-first
```

Use `--codex /path/to/codex` if the desktop-bundled binary is elsewhere.

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
