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

Desktop app's local follower socket ─┐
Shared terminal app-server socket ───┴─ native.py → controller → USB
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
Nyx now uses those messages for terminal sessions already loaded on the shared
local app-server. It calls `thread/resume` with only the existing thread ID to
join the same live thread, without sending a turn or overriding its settings.
It never loads a standalone session into a separate backend. Two-client testing
against Codex 0.153.4 verified that a late follower receives the same pending
request ID and its decision clears the request for both clients.

The desktop adapter follows the installed app's private local IPC protocol:
owner discovery/follower broadcasts, state snapshots (version 11), and patches.
Its decision names the conversation ID, request ID, and owning app client.
It keeps only request/runtime fields in memory, not chat history. Frame limits,
revision checks, reconnect invalidation, and supported-method checks fail closed.
This is independently written protocol integration, not copied vendor code or
a modification to the desktop installation. App updates may require adapter changes.

We therefore ship:

| Mode | Behavior |
| --- | --- |
| Native (default) | Hooks return immediately; live native requests enable hardware decisions alongside the normal UI |
| Legacy manual (explicit opt-in) | A 20-second pre-prompt hook window; not simultaneous native UI control |
| Approve for me | No hook-based prompt; a request actually routed to the native client can still be answered |

Legacy manual mode skips known automatic-review metadata as defense in depth.
Native mode does not suppress a real native request just because that session
uses automatic review. An upstream hook alone cannot create a native offer.
Do not enable manual mode while any hooked session is using automatic review:
the current hook contract cannot enforce that distinction for you.

## Decision safety

- Every pending request receives a fresh random ID, even in the same turn/session.
- The device includes its displayed view ID; a changed selection rejects stale actions.
- Allow/deny consumes the request once. There is no "allow for session" action.
- No default approval. Timeout/disconnect/shutdown/canceled hook means no decision.
- Interrupt, Stop, and SessionEnd cancel that session's pending requests.
- Source disconnect and native resolution invalidate native buttons. Source
  reconnect clears queued decisions and assigns fresh tokens.
- Hook input and serial lines are bounded. At most 32 hook sessions and 16 legacy
  hook requests remain in memory. Native state frames are bounded separately.
- There is no persistent approval queue. Restarting the bridge loses state safely.
- The display has only a preview. Use Open and inspect the full operation when needed.

Native decisions go directly to the existing request. A USB result of "queued"
means Nyx accepted the button; native resolution remains authoritative. Duplicate
button presses cannot re-arm an in-flight decision. For prompts offering Cancel
instead of Decline, the reject button cancels the turn. For structured permission
requests, approve returns exactly the requested permission subset with turn scope;
reject returns an empty subset. Broad session grants and question forms remain in
the native UI.

In legacy manual mode only, hook output supplies the decision and native UI
does not appear until that synchronous hook returns. Do not enable that mode
when you want simultaneous native UI and hardware control.

## State and origin limits

Without a native subscription, IDLE/RUNNING are last-known hook states. A crash,
missing hook, or untrusted hook can leave them stale. Sessions before bridge
startup are not reconstructed. Graceful SessionEnd removes the session.

Origin metadata is best effort; a shared daemon does not identify each terminal UI.
Nyx refuses to use the daemon's inherited TTY as an Open target. Its lifecycle
observer correlates a uniquely identifiable live Codex TUI—including the current
bare `codex` managed-client command—with a TTY and records that exact match for
Apple Terminal routing. An explicit `resume SESSION_ID` match takes priority;
ambiguous mappings open nothing.
Terminal and iTerm routing matches the recorded TTY/session ID and does not type.
VS Code focus is app-level, not exact terminal selection. Unidentified origins
do nothing; lack of a TTY is not treated as proof of a Codex desktop chat.
For desktop tasks, Open reads only the bounded first `session_meta` record of the
matching local rollout, checks its session ID and `codex_work_desktop` originator,
and opens `codex://threads/<session_id>` with bundle `com.openai.codex`. That bundle
is named ChatGPT on the tested Mac. The URL route was inspected in desktop build
26.901.31953; it is not a promised stable public API. Metadata format changes fail
to no action, never to a random Terminal. A `source` value of `vscode` alone does
not identify desktop origin. Terminal/iTerm/VS Code metadata keeps routing priority.

The lookup runs only when Open is pressed. It does not poll transcripts or read
conversation messages, and it refuses files outside the local sessions directory.
Restart the Mac bridge after updating the opener; no firmware or hook reinstall
is needed. Sessions reappear as their next trusted hooks arrive.

### Desktop state and OLED metadata

The documented hook payload supplies the session ID, transcript path, cwd, and
active model. Current desktop builds can record `task_complete` without reliably
delivering Nyx's `Stop` hook. For desktop sessions only, a background observer
therefore reads a bounded 8 MB tail once, then reads only newly appended complete
records. It extracts `task_started`, `task_complete`, `turn_aborted`, model, and
effort; it ignores prompts, responses, and tool contents. Files outside
`~/.codex/sessions` and malformed or oversized records are ignored. Shared-daemon
terminal sessions use the live remote TUI's TTY as their liveness identity; the
daemon PID and its 30-minute loaded-thread grace period are not treated as an open
terminal tab. Current shared-daemon TUI rollouts can carry a `nyx` origin after
the follower attaches, so that label is treated as terminal rather than internal.
Explicit Codex subagents remain hidden from the device. Ambiguous client-to-session
matches fail open. Terminal
status remains hook-owned, so transcript fallback cannot incorrectly mark a live
terminal task idle.

Every session ID maps deterministically to a short two-word alias such as
`Quantum Fox`. The alias is display-only and does not rename the Codex task.

The documented `PermissionRequest` hook does not include `approvals_reviewer`.
To avoid showing automatically reviewed internal requests as human pauses, the
short-lived hook reads the latest bounded `turn_context` and copies only its
reviewer value into private Nyx metadata for legacy mode. Default native mode
does not infer an actionable request from this field. Its live native subscription
takes precedence over transcript status.

## Reproduce the build

Researched against Codex CLI 0.153.0. Firmware builds with PlatformIO Core 6.1.19,
Espressif32 7.0.1, U8g2 2.36.12, ArduinoJson 6.21.5.
The board choice follows the [PlatformIO DevKitC-1 reference](https://docs.platformio.org/en/latest/boards/espressif32/esp32-s3-devkitc-1.html).
The display API follows [U8g2's official reference](https://github.com/olikraus/u8g2/wiki/u8g2reference).
Build dependencies are fetched by PlatformIO; no copied vendor source belongs in Git.

Before claiming hardware-ready, do the live checklist in wiring.md. Unit tests,
pseudo-serial integration, and compilation do not prove buttons, screen, or
every host's Codex integration work on the physical board.
