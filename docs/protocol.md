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
{"v":1,"type":"state","view_id":"opaque-id","session":"1234abcd","project":"nyx","status":"PERMISSION_REQUIRED","detail":"echo hello","remaining":19,"index":1,"count":2,"manual":true}
```

Status is IDLE, RUNNING, or PERMISSION_REQUIRED. With no sessions, count/index
are zero and view_id is empty. Detail is a preview of at most 240 characters,
not a complete security review. The device derives OFFLINE locally after three
seconds without state. The Mac also drops pending decisions after three seconds
without a device heartbeat.

```json
{"v":1,"type":"result","ok":true,"status":"submitted"}
```

Errors include stale_view and no_pending_request. Submitted confirms only local
acceptance; open_requested confirms only the focus attempt was scheduled.
The next full state is authoritative; firmware never infers execution success.

No secrets/auth tokens are sent. Project names and command previews are sent
to the connected device, so keep it physically trusted.
