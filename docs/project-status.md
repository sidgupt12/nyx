# Nyx project status

**Updated:** September 14, 2026

This is the handoff note for the next Nyx session. The stable physical baseline
remains tagged as `v0.2.0-phase1`; this document records the follow-up work after
that tag. Do not move or rewrite the tag.

## Working now

- Python bridge observes existing Codex desktop and managed-terminal sessions.
- ESP32-S3 firmware drives the SH1106 128x64 I2C OLED and the physical controls.
- Encoder selection and exact-origin opening work for known desktop tasks and
  matched terminal tabs.
- Native desktop and terminal approval requests remain controlled by Codex's own
  UI while Nyx offers one-shot hardware approve/reject actions for the same live
  request. Automatically resolved requests do not create false hardware prompts.
- NEW opens a fresh Codex-mode composer. It is deliberately a global action and
  does not invent a session before Codex publishes a real session ID.
- Session presentation has character-style nicknames, APP/TERM labeling, model,
  effort, state, and a small original animated pixel familiar.
- MODE is GPIO4 to GND. One press opens effort selection; two quick presses open
  model selection. The encoder browses and confirms, NO cancels, and five seconds
  of inactivity expires browsing. Codex's reported catalog is used; only model
  and effort are sent, for the next turn.

## Verification completed

- `python -m unittest discover -s tests -q`: 119 tests passing.
- `ruff check nyx tests`: passing.
- `pio run -d firmware`: successful ESP32-S3 build.
- Read-only model catalog check against the installed app-server succeeded.
- New settings tests cover stale menus, disconnects, permission preemption,
  concurrent metadata changes, native acknowledgements, and unsupported choices.

## Not flashed or physically validated yet

The current working tree contains the MODE menu and refreshed OLED artwork, but
the firmware has not been uploaded in this checkpoint. After wiring GPIO4, stop
Nyx and any serial monitor, upload using the actual port from `nyx ports`, restart
Nyx, and run the live checklist in `docs/wiring.md`. A successful compile is not
proof that the physical button or native settings route works on the board.

## Deliberately deferred: empty-window tracking

When NEW opens a blank Codex composer, the current launcher receives no stable
draft/window ID and no close event. Therefore the OLED shows that task only after
Codex creates the real session. This is documented in
[`empty-window-tracking.md`](empty-window-tracking.md).

The future implementation must be isolated from `Controller` session lifecycle:

1. Receive a real draft/window identity correlated to the NEW request.
2. Display it as a non-actionable temporary entry.
3. Promote it to the published session ID exactly once when the first real event
   arrives.
4. Remove it only on the matching draft-close event.

Do not use title scraping, process liveness, timers, foreground-window guesses,
or “the next session seen” matching. Those approaches can create ghost sessions
or remove an unrelated active session.

## Invariants for future changes

- Nyx is an add-on: stopping, unplugging, or updating Nyx must not block Codex.
- Never approve on timeout, disconnect, stale view, unknown protocol, or missing
  request identity.
- Keep `controller.py` lifecycle and approval behavior separate from settings
  menus and any future draft tracker.
- Keep the Mac bridge and firmware protocol versioned and independent.
- Do not commit `.venv`, `.pio`, caches, logs, machine state, or `AGENTS.md`.

## Next safe sequence

1. Review this status note and `docs/empty-window-tracking.md` before coding.
2. Wire and physically validate MODE; upload only after stopping the bridge.
3. Test model/effort acknowledgement on one harmless idle native session.
4. Commit/push only after tests, lint, firmware build, and the live checklist.
5. Design the draft/window API separately before adding empty entries to the OLED.
