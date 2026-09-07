# USB protocol v1

115200 baud, 8N1. One ASCII-escaped JSON object per newline. No HTTP or network
port. Maximum line length 4096 bytes, excluding the newline. Unknown versions,
malformed JSON, arrays, and oversized lines are ignored. An oversized line is
discarded through its newline so the next valid frame can recover.

## Device → Mac

Boot/reconnect handshake (repeat once per second until state arrives):

```json
{"v":1,"type":"hello"}
```

After connection, send this every second:

```json
{"v":1,"type":"heartbeat"}
```

Button/encoder action, using exactly the view ID currently displayed:

```json
{"v":1,"type":"action","action":"approve","view_id":"opaque-id"}
```

Actions: approve, reject, open, next, previous. Approve/reject only work on a
pending permission view, not an ordinary session. Firmware requires a 700 ms
hold for approve; no auto-repeat. Firmware must release/re-arm after view changes.

## Mac → device

The complete selected view is sent every 250 ms, so reconnect needs no event replay:

```json
{"v":1,"type":"state","view_id":"opaque-id","session":"1234abcd","name":"Neon Raven","surface":"APP","project":"nyx","model":"gpt-6-astra","effort":"high","status":"PERMISSION_REQUIRED","detail":"echo hello","remaining":19,"actionable":true,"index":1,"count":2,"manual":true}
```

Status is IDLE, RUNNING, or PERMISSION_REQUIRED. With no sessions, count/index
are zero and view_id is empty. Detail is a preview of at most 240 characters,
not a complete security review. The device derives OFFLINE locally after three
seconds without state. The Mac also drops pending decisions after three seconds
without a device heartbeat.

`name` is a stable two-word alias derived locally from the session ID. `surface`
is `APP` or `TERM`; firmware renders it as a pixel icon because the SH1106 font
does not contain Unicode emoji. Model and effort are informational and may be
empty when an older or unknown Codex transcript format cannot provide them.
`actionable` distinguishes a hardware decision window from a passively observed
Codex prompt. When false, the OLED offers Open only; approve/reject remain inert.

`native: true` identifies a request shared with Codex's existing approval UI.
For these requests `remaining` is zero because there is no Nyx deadline. The
firmware displays DECIDE / OPEN without a countdown. Native request tokens change
after reconnect; on-screen resolution removes them. `status: queued` acknowledges
that Nyx queued a native decision, not that Codex has executed the operation.

```json
{"v":1,"type":"result","ok":true,"status":"submitted"}
```

Errors include stale_view and no_pending_request. Submitted confirms only local
acceptance; open_requested confirms only the focus attempt was scheduled.
The next full state is authoritative; firmware never infers execution success.

No secrets/auth tokens are sent. Project names and command previews are sent
to the connected device, so keep it physically trusted.
