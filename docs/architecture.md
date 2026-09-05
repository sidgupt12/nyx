# How Nyx works

## Two programs, one small protocol

```text
Existing Codex session
  └─ trusted, short-lived hook (nyx/hooks.py)
       └─ private Unix socket
            └─ Python bridge
                 ├─ controller: last-known session state + pending requests
                 ├─ opener: best-effort origin focus
                 └─ pyserial ↔ USB ↔ ESP32 firmware
```

Nyx never starts a Codex agent, sends it prompts, changes its approval policy,
or injects terminal keystrokes. The socket is a local filesystem endpoint, not
a TCP/HTTP port. It is owner-only; same-user local programs remain trusted.
Do not connect an untrusted USB device: the USB protocol is not authenticated.

Read the files in this order: controller.py → hooks.py → listener.py →
hardware.py → firmware/src/main.cpp. Tests show examples of each boundary.
Python is used for the bridge. The small firmware uses C++ because the user's
existing tested hardware project uses Arduino/PlatformIO and U8g2.

## What the research actually establishes

The official [Codex hook reference](https://learn.chatgpt.com/docs/hooks) says
PermissionRequest runs before normal approval handling. Returning allow/deny
can decide it; returning no decision continues normal handling.
The documented common input has permission_mode but no reliable final
"automatic review has finished and a human must decide" field.

Consequently, a PermissionRequest event is **not proof that the user needs to act**.
Filtering every auto-review session would hide potential manual escalations;
forwarding every hook would resurrect false requests. Neither is a complete fix.

The official [app-server protocol](https://learn.chatgpt.com/docs/app-server)
has server-initiated approval requests and serverRequest/resolved events.
Those are a better future integration point, but the documented interface
does not by itself establish a passive, universal subscription to every
independent CLI, VS Code, and desktop session. Nyx does not start/resume threads
or take over their approval client to pretend this is solved.

We therefore ship:

| Mode | Behavior |
| --- | --- |
| Passive (default) | Forward lifecycle state; permission hooks return without a decision or hardware prompt |
| Manual (explicit opt-in) | Offer a physical decision for up to 20 seconds before normal approval handling |
| Approve for me | Use passive mode; reliable human-only fallback events are not implemented |

Known explicit dontAsk/bypassPermissions/auto-review metadata is skipped as
defense in depth, not a claim of reliable detection. Unknown routing metadata
in opt-in manual mode is not proof of human necessity.
Do not enable manual mode while any hooked session is using automatic review:
the current hook contract cannot enforce that distinction for you.

## Decision safety

- Every pending request receives a fresh random ID, even in the same turn/session.
- The device includes its displayed view ID; a changed selection rejects stale actions.
- Allow/deny consumes the request once. There is no "allow for session" action.
- No default approval. Timeout/disconnect/shutdown/canceled hook means no decision.
- Interrupt, Stop, and SessionEnd cancel that session's pending requests.
- USB reboot and missing heartbeat cancel all pending requests; no action replay.
- Hook input and serial lines are bounded. At most 32 sessions and 16 requests remain in memory.
- There is no persistent approval queue. Restarting the bridge loses state safely.
- The display has only a preview. Use Open and inspect the full operation when needed.

Only the hook's final JSON reaches Codex. A USB result of "submitted" means the
bridge accepted the button, not that Codex executed the operation; another hook
or policy may still deny it. Native approval UI does not appear until this
optional synchronous hook finishes. That bounded delay is an explicit tradeoff.

## State and origin limits

IDLE/RUNNING are last-known hook states, not process liveness probes. A crash,
missing hook, or untrusted hook can leave them stale. Sessions before bridge
startup are not reconstructed. Graceful SessionEnd removes the session.

Origin metadata is best effort; daemons may not inherit a terminal's environment.
Terminal and iTerm routing matches the recorded TTY/session ID and does not type.
VS Code focus is app-level, not exact terminal selection. Unidentified origins
do nothing; lack of a TTY is not treated as proof of a Codex desktop chat.
Exact Codex desktop-thread routing remains future work, not a fake Terminal fallback.

## Reproduce the build

Researched against Codex CLI 0.153.0. Firmware builds with PlatformIO Core 6.1.19,
Espressif32 7.0.1, U8g2 2.36.12, ArduinoJson 6.21.5.
The board choice follows the [PlatformIO DevKitC-1 reference](https://docs.platformio.org/en/latest/boards/espressif32/esp32-s3-devkitc-1.html).
The display API follows [U8g2's official reference](https://github.com/olikraus/u8g2/wiki/u8g2reference).
Build dependencies are fetched by PlatformIO; no copied vendor source belongs in Git.

Before claiming hardware-ready, do the live checklist in wiring.md. Unit tests,
pseudo-serial integration, and compilation do not prove buttons, screen, or
every host's Codex integration work on the physical board.
