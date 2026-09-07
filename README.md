# Nyx

**An open-source, DIY alternative to Codex Micro.**

Nyx turns a small ESP32 into a physical desk controller for Codex. It watches the
Codex sessions already running on your Mac and lets you see their state, move between
them, open the correct window, and respond to real approval requests from hardware.

Nyx does not start agents, send prompts, or replace the normal Codex interface. It is
a local add-on: Codex continues to work normally when Nyx is stopped or unplugged.

## Phase 1: working

- Shows multiple Codex desktop and terminal sessions on a 1.3-inch OLED.
- Displays a friendly session name, App/Terminal source, state, model, and effort.
- Shows `IDLE`, `RUNNING`, and genuine `PERMISSION REQUIRED` states.
- Rotates through sessions with an encoder.
- Opens the exact Codex desktop task or matched Apple Terminal tab on encoder press.
- Approves or rejects a live request with physical buttons.
- Keeps the normal Codex approval controls working at the same time.
- Ignores work that Codex approves automatically instead of creating false requests.
- Removes closed terminal sessions and reconnects after USB interruptions.

The tested Phase 1 build is preserved in Git as `v0.2.0-phase1`. Its behavior and
regression checklist are documented in [the Phase 1 checkpoint](docs/phase-1-checkpoint.md).

## Parts used so far

| Quantity | Part |
| ---: | --- |
| 1 | ESP32-S3-N16R8 development board (DevKit style) |
| 1 | 1.3-inch SH1106 128×64 blue I2C OLED, 4 pin |
| 1 | KY-040-style rotary encoder module with push switch |
| 2 | Tactile push buttons: approve and reject |
| 1 | Full-size solderless breadboard |
| — | Male-to-male jumper wires |
| 1 | USB data cable for the board's USB/UART connector |
| 1 | Mac running Codex |

No external button resistors are needed because the firmware uses the ESP32's internal
pull-ups. LEDs, a speaker, joystick, mechanical switches, microphone, and enclosure are
not part of this build yet.

## Wiring

Disconnect USB before changing wires. Use 3.3 V and a shared ground.

| Part | ESP32-S3 pin |
| --- | --- |
| OLED VCC | 3V3 |
| OLED GND | GND |
| OLED SDA | GPIO 8 |
| OLED SCK/SCL | GPIO 9 |
| Approve button | GPIO 5 ↔ button ↔ GND |
| Reject button | GPIO 12 ↔ button ↔ GND |
| Encoder CLK | GPIO 6 |
| Encoder DT | GPIO 7 |
| Encoder SW | GPIO 10 |
| Encoder GND | GND |
| Encoder + | 3V3 |

The complete beginner-friendly guide is in [docs/wiring.md](docs/wiring.md).

## How it works

```text
Existing Codex sessions
        │
        ├── trusted hooks: state and session identity
        ├── desktop adapter: live desktop requests
        └── terminal adapter: live terminal requests
                         │
                         ▼
                  Python bridge on Mac
                         │
                    USB JSON messages
                         │
                         ▼
              ESP32 firmware + OLED + controls
```

The Mac bridge and ESP32 firmware are deliberately separate. The board only understands
small state and action messages; it contains no Codex credentials or session logic. This
makes it straightforward to add new controls later without rebuilding the integration.

### Engineering behind Phase 1

- **Real requests, not simulation:** Nyx follows the live request IDs used by Codex.
- **Safe one-shot actions:** every button action belongs to the exact request and session
  shown on screen. Resolved, duplicated, stale, or disconnected actions are rejected.
- **Fail-closed behavior:** a timeout, unknown protocol, missing device, or ambiguous
  session never becomes an approval.
- **Non-blocking integration:** hooks return immediately in normal mode, so Nyx cannot
  hold up automatic approval or the normal Codex interface.
- **Exact window routing:** desktop tasks use their task ID; terminal tasks are matched to
  a live TTY. Nyx opens nothing when it cannot identify the origin safely.
- **Lifecycle tracking:** the long-running Codex daemon is kept separate from the terminal
  window attached to it, so closing a tab removes the correct device entry.
- **Local transport:** Mac components communicate through local Unix sockets, and the
  hardware uses USB serial. Nyx opens no TCP port and does not need Wi-Fi.

For the deeper design and safety boundaries, read [docs/architecture.md](docs/architecture.md)
and [docs/protocol.md](docs/protocol.md).

## Build it

Nyx currently targets macOS, Python 3.10+, an ESP32-S3, and a SH1106 OLED.

```sh
git clone https://github.com/sidgupt12/nyx.git
cd nyx

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pip install platformio==6.1.19
```

Build and upload the firmware:

```sh
pio run -d firmware
nyx ports
pio run -d firmware -t upload --upload-port /dev/cu.usbmodemYOUR_BOARD
```

Use the exact board port printed by `nyx ports`. Close PlatformIO's Serial Monitor first;
only one process can use the USB serial connection at a time.

Install the Codex hooks and start the background bridge:

```sh
nyx install-hooks
nyx start --port /dev/cu.usbmodemYOUR_BOARD
nyx status
```

Restart or send a new message in each Codex session after installing the hooks. New state
cannot be reconstructed until a trusted hook from that session reaches Nyx.

For managed terminal sessions with native approval controls, use Codex's shared local
server:

```sh
codex app-server daemon start
codex --remote unix://
```

Start Nyx again after a Mac reboot. Stop it at any time with `nyx stop`.

## Controls

| Control | Action |
| --- | --- |
| Rotate encoder | Select a session or pending request |
| Press encoder | Open that session's window |
| Approve button | Allow the displayed live request once |
| Reject button | Decline or cancel the displayed live request |

## Project layout

```text
nyx/       Python bridge, Codex adapters, state, safety, and window routing
firmware/  ESP32 Arduino/PlatformIO firmware
docs/      wiring, protocol, architecture, and stable checkpoint notes
tests/     bridge and safety tests that run without physical hardware
```

Start with `nyx/controller.py`, `firmware/src/main.cpp`, and `firmware/include/pins.h`.

## Verify changes

```sh
python -m unittest discover -s tests -q
ruff check nyx tests
pio run -d firmware
```

Phase 1 has 95 passing Python tests and a successful ESP32-S3 firmware build. Hardware
changes still require the short live checklist in [docs/wiring.md](docs/wiring.md).

## Current limits

- The desktop integration uses a private local Codex interface that may change after an
  app update.
- Native terminal approvals require Codex's managed shared app server; an already-running
  standalone CLI cannot be attached retroactively.
- Apple Terminal tabs and Codex desktop tasks can open exactly. VS Code routing is currently
  app-level rather than exact integrated-terminal selection.
- Model and effort are display-only. The encoder does not change them yet.
- The current pin map and firmware target the ESP32-S3, not the ESP32-C3 Super Mini.
- Nyx does not install login autostart yet.

Nyx is an independent community project and is not affiliated with or endorsed by OpenAI.

## License

[MIT](LICENSE)
