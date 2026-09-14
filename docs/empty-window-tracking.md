# Empty windows: deliberately separate from sessions

Status: **not implemented**. NEW opens the Codex composer; the OLED still lists
the real session only once Codex publishes it. No placeholder is inserted into
the session controller, and the existing close observer is unchanged.

## What we verified

The installed app's `codex://threads/new?mode=codex` handler navigates to its
new-task composer. It does not return a task ID or a window ID to `open`.
An exit code of zero from macOS `open` confirms delivery, not that the composer
was created. The desktop follower adapter follows known conversation IDs;
its existing state messages do not identify an empty composer.

The app also contains temporary `client-new-thread:` IDs internally. That is
not, by itself, an externally accessible draft-create/draft-close API.

## Boundary for a future implementation

Use a separate launch tracker, not synthetic SessionStart/SessionEnd hooks:

1. Send a launch request with a correlation ID.
2. Receive the exact draft/window ID from an app integration; only then list it
   as `EMPTY`, with approval controls disabled.
3. On an explicit draft-to-session event, replace that entry with the real
   session ID, once. Do not match by time, name, model, or "next session seen".
4. On that exact draft's close event, remove only that draft.
5. After real-session handoff, leave lifecycle ownership with the existing
   session observer and native adapters.

A missing launch acknowledgement may show a short launch-error indicator, but
must not invent an open session. Telemetry loss is not proof a window closed.
Reconnection needs a fresh draft inventory before stale entries can be resolved.

Before enabling this, verify two simultaneous drafts, close-before-first-chat,
failed launches, unrelated new sessions, duplicate/out-of-order events,
bridge restarts, and draft-to-real-session handoff. Existing terminal-close,
selection and permission tests must remain green.

Do not replace this requirement with window-title scraping, foreground-window
guesses, process liveness (one app process can own many windows), or a fake chat
message. A versioned app-side draft identity/lifecycle integration is still needed.
