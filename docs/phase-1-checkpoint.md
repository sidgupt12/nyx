# Phase 1 checkpoint

**Saved:** September 7, 2026
**Git tag:** `v0.2.0-phase1`
**Milestone:** the first working Nyx physical control deck

This is the known-good foundation for the Nyx project. It is deliberately
small: Nyx observes existing Codex sessions, shows them on the physical controller,
and adds physical controls without replacing or blocking the normal Codex UI.

## Proven on the real hardware

- ESP32-S3-DevKitC-1 and the 1.3-inch SH1106 I2C OLED communicate with the Mac over USB.
- Nyx runs headlessly and reconnects after a temporary USB loss.
- Existing Codex desktop and managed terminal sessions appear on the OLED.
- Sessions show a friendly name, App/Terminal identity, state, model, and effort.
- Rotating the encoder selects sessions.
- Pressing the encoder opens the exact Codex desktop task or matched Apple Terminal tab.
- Closing a matched terminal client removes it after a short grace period.
- A genuine native approval can be accepted or rejected from the hardware.
- The normal Codex approval controls remain usable; either UI can resolve the request.
- Automatically approved work is not paused and does not become a false hardware request.
- Old, duplicated, disconnected, or already-resolved button actions cannot approve work.

The Python suite contained 95 passing tests when this checkpoint was created, followed
by a successful live test of session selection and encoder opening.

## Guarantees future changes must preserve

1. Nyx is an add-on. Codex must continue working normally when Nyx is stopped,
   disconnected, or unable to understand a new protocol version.
2. Never convert missing information, a timeout, or a disconnect into approval.
3. A hardware action must target the exact request and session currently displayed.
4. Automatically reviewed requests must not wait for Nyx.
5. Never open a guessed or unrelated window. Ambiguity must fail safely.
6. Do not treat the shared app-server daemon as a live terminal client. Track its TTY.
7. Do not show Nyx validation sessions, subagents, or unrelated loaded sessions.
8. Keep the Mac bridge and ESP32 firmware separate and the USB protocol versioned.

## Check before calling a later build stable

From the repository folder with the Python environment active:

```sh
python -m unittest discover -s tests -q
ruff check nyx tests
pio run -d firmware
```

Then perform this live smoke test:

1. Start Nyx and confirm that the device reports connected.
2. Send one message in a desktop task and one in a managed terminal session.
3. Confirm both appear and transition between RUNNING and IDLE.
4. Rotate between them and press the encoder; each must open its own exact window.
5. Trigger one harmless manual permission. Confirm the normal UI still appears and
   that YES or NO resolves the same live request exactly once.
6. Confirm an automatically approved operation creates no false waiting state.
7. Close the terminal client and confirm its OLED entry disappears after the grace period.

Unit tests and a successful firmware build are necessary, but they do not replace this
physical test.

## Return to this checkpoint safely

First commit or stash any work you want to keep. Then create a recovery branch from the
checkpoint rather than deleting current work:

```sh
git switch -c recover-phase-1 v0.2.0-phase1
```

The tag preserves the complete source, firmware, tests, and documentation used by this
working build. Runtime logs, virtual environments, and machine-specific state are not
part of the checkpoint.
