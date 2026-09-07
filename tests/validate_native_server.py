"""Isolated real Codex server + local fake model. No OpenAI model calls."""

import http.server
import argparse
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nyx.controller import Controller
from nyx.native import NativeApprovals
from nyx.appserver import AppServer
from websockets.sync.client import unix_connect
from websockets.exceptions import ConnectionClosed

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--codex", default="/Applications/ChatGPT.app/Contents/Resources/codex")
parser.add_argument("--action", choices=["approve", "reject", "native-first"], default="reject")
args = parser.parse_args()
CODEX = args.codex


class Model(http.server.BaseHTTPRequestHandler):
    count = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        names = [t.get("name") for t in payload.get("tools", [])]
        print("model tools include exec_command:", "exec_command" in names, flush=True)
        Model.count += 1
        if Model.count % 2:
            item = {
                "type": "function_call",
                "id": "fc_nyx_" + str(Model.count),
                "call_id": "call_nyx_" + str(Model.count),
                "name": "exec_command",
                "arguments": json.dumps(
                    {
                        "cmd": "printf nyx-native-validation",
                        "sandbox_permissions": "require_escalated",
                        "justification": "Local Nyx approval integration test.",
                    }
                ),
            }
        else:
            item = {
                "type": "message",
                "id": "msg_nyx",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Validation complete."}],
            }
        response = {
            "id": "resp_nyx_" + str(Model.count),
            "object": "response",
            "status": "completed",
            "output": [item],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        events = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            },
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        body = "".join(
            "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Client:
    def __init__(self, path):
        self.ws = unix_connect(path, open_timeout=3, close_timeout=1, proxy=None, compression=None)
        self.q = queue.Queue()
        self.seq = 0
        self.backlog = []

        def reader():
            try:
                for line in self.ws:
                    try:
                        self.q.put(json.loads(line))
                    except ValueError:
                        pass
            except ConnectionClosed:
                pass

        threading.Thread(target=reader, daemon=True).start()
        self.call("initialize", {"clientInfo": {"name": "nyx_validation", "version": "1"}})
        self.send({"method": "initialized", "params": {}})

    def send(self, m):
        self.ws.send(json.dumps(m))

    def wait(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, m in enumerate(self.backlog):
                if predicate(m):
                    return self.backlog.pop(i)
            m = self.q.get(timeout=max(0.1, deadline - time.monotonic()))
            if predicate(m):
                return m
            self.backlog.append(m)
        raise TimeoutError()

    def call(self, method, params):
        self.seq += 1
        n = self.seq
        self.send({"id": n, "method": method, "params": params})
        result = self.wait(lambda m: m.get("id") == n and "method" not in m)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    def close(self):
        self.ws.close()


with tempfile.TemporaryDirectory(prefix="nyx-native-validation-") as folder:
    http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    path = folder + "/server.sock"
    provider = {
        "name": "Nyx local validation",
        "base_url": "http://127.0.0.1:" + str(http.server_port) + "/v1",
        "wire_api": "responses",
        "requires_openai_auth": False,
    }
    # TOML inline table: all values here are controlled constants.
    config = (
        "{"
        + ", ".join(
            k + " = " + (str(v).lower() if isinstance(v, bool) else json.dumps(v))
            for k, v in provider.items()
        )
        + "}"
    )
    server = subprocess.Popen(
        [
            CODEX,
            "app-server",
            "--listen",
            "unix://" + path,
            "--disable",
            "hooks",
            "-c",
            'model_provider="nyx_validation"',
            "-c",
            'model="nyx_validation"',
            "-c",
            "mcp_servers={}",
            "-c",
            "model_providers.nyx_validation=" + config,
        ],
        cwd=folder,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    clients = []
    sid = None
    adapter = None
    try:
        deadline = time.monotonic() + 8
        while not Path(path).exists() and time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("test server exited")
            time.sleep(0.1)
        owner = Client(path)
        clients.append(owner)
        follower = Client(path)
        clients.append(follower)
        result = owner.call(
            "thread/start",
            {
                "cwd": folder,
                "ephemeral": False,
                "model": "nyx_validation",
                "modelProvider": "nyx_validation",
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "sandbox": "read-only",
            },
        )
        sid = result["thread"]["id"]
        owner.call(
            "turn/start",
            {
                "threadId": sid,
                "input": [{"type": "text", "text": "Run the local validation command."}],
            },
        )
        a = owner.wait(lambda m: m.get("method") == "item/commandExecution/requestApproval")
        joined = follower.call("thread/resume", {"threadId": sid})
        print("both clients joined same live thread:", joined["thread"]["id"] == sid, flush=True)
        b = follower.wait(lambda m: m.get("method") == "item/commandExecution/requestApproval")
        print("both clients received approval:", a["id"] == b["id"], flush=True)
        core = Controller()
        core.event({"session_id": sid, "hook_event_name": "SessionStart"}, True)
        hub = NativeApprovals(core)
        adapter = AppServer(hub, Path(path))
        adapter.start()
        deadline = time.monotonic() + 8
        while not core.snapshot()["actionable"] and time.monotonic() < deadline:
            time.sleep(0.05)
        state = core.snapshot()
        assert state["actionable"], "actual Nyx adapter did not receive the live request"
        offer = hub.offers[state["view_id"]]
        assert offer.request_id == a["id"]
        if args.action == "native-first":
            owner.send({"id": a["id"], "result": {"decision": "cancel"}})
        else:
            assert hub.submit(state["view_id"], args.action)
        for label, c in [("owner", owner), ("follower", follower)]:
            c.wait(lambda m: m.get("method") == "serverRequest/resolved")
            print(label, "saw request resolved", flush=True)
        if args.action == "native-first":
            deadline = time.monotonic() + 3
            while core.snapshot()["actionable"] and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not hub.submit(state["view_id"], "approve"), "resolved request still actionable"
        if args.action == "approve":
            completed = owner.wait(
                lambda m: m.get("method") == "item/completed"
                and m.get("params", {}).get("item", {}).get("type") == "commandExecution"
            )
            assert completed["params"]["item"]["status"] == "completed"
        print("PASS:", args.action, "uses the same native request without hooks.", flush=True)
    finally:
        if adapter:
            adapter.close()
        if sid:
            try:
                owner.call("thread/archive", {"threadId": sid})
            except Exception:
                pass
        for c in clients:
            c.close()
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        http.shutdown()
