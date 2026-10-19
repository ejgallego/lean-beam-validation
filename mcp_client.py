# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Independent JSON-lines MCP client with transcripts and bounded shutdown."""

import json
from pathlib import Path
import queue
import subprocess
import threading
import time


class ProtocolError(Exception):
    pass


class McpClient:
    def __init__(self, command, cwd, transcript, *, modern=False, timeout=120, shutdown_timeout=30, env=None):
        self.modern = modern
        self.timeout = timeout
        self.shutdown_timeout = shutdown_timeout
        self.next_id = 0
        self.pending = set()
        self.responses = {}
        self.notifications = []
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.transcript = Path(str(transcript) + ".jsonl").open("w", encoding="utf-8")
        self.stderr = Path(str(transcript) + ".stderr").open("w", encoding="utf-8")
        self.proc = subprocess.Popen(list(map(str, command)), cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.stderr, text=True,
                                     encoding="utf-8", bufsize=1)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        self.closed = False

    def record(self, direction, message):
        with self.lock:
            self.transcript.write(json.dumps({"at_monotonic": time.monotonic(), "direction": direction,
                                               "message": message}, ensure_ascii=False) + "\n")
            self.transcript.flush()

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.record("server", line.rstrip("\n"))
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ProtocolError(f"invalid JSON-RPC stdout: {line!r}")
                self.events.put(message)
        except Exception as error:
            self.events.put(ProtocolError(str(error)))
        finally:
            self.events.put(None)

    def send(self, method, params=None, *, notification=False):
        message = {"jsonrpc": "2.0", "method": method}
        if self.modern:
            params = dict(params or {})
            params["_meta"] = {**params.get("_meta", {}),
                               "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                               "io.modelcontextprotocol/clientCapabilities": {},
                               "io.modelcontextprotocol/clientInfo": {"name": "beam-artifact-validation", "version": "1"}}
        if params is not None:
            message["params"] = params
        if not notification:
            self.next_id += 1
            message["id"] = self.next_id
            self.pending.add(self.next_id)
        self.record("client", message)
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return message.get("id")

    def notify(self, method, params=None):
        return self.send(method, params, notification=True)

    def _accept(self, message):
        if message is None:
            raise ProtocolError("MCP stdout closed before response")
        if isinstance(message, Exception):
            raise message
        if "method" in message and "id" not in message:
            self.notifications.append(message)
            return
        identifier = message.get("id")
        if type(identifier) is not int or "method" in message or identifier not in self.pending or identifier in self.responses:
            raise ProtocolError(f"unexpected server response/request: {message}")
        self.responses[identifier] = message

    def receive(self, identifier, timeout=None):
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while identifier not in self.responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"MCP request {identifier} timed out; see transcript")
            try:
                self._accept(self.events.get(timeout=remaining))
            except queue.Empty:
                raise TimeoutError(f"MCP request {identifier} timed out; see transcript") from None
        self.pending.remove(identifier)
        return self.responses.pop(identifier)

    def request(self, method, params=None):
        return self.receive(self.send(method, params))

    def initialize(self):
        if self.modern:
            response = self.request("server/discover")
            if "2026-07-28" not in response.get("result", {}).get("supportedVersions", []):
                raise ProtocolError(f"unexpected modern discovery: {response}")
        else:
            response = self.request("initialize", {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "beam-artifact-validation", "version": "1"},
            })
            if response.get("result", {}).get("protocolVersion") != "2025-11-25":
                raise ProtocolError(f"unexpected initialize: {response}")
            self.notify("notifications/initialized")

    def tool(self, name, root=None, arguments=None, *, allow_error=False):
        arguments = dict(arguments or {})
        if root is not None:
            arguments["workspace"] = {"root": str(Path(root).resolve())}
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        if allow_error:
            return response
        result = response.get("result", {})
        if "error" in response or result.get("isError") is True or not isinstance(result.get("structuredContent"), dict):
            raise ProtocolError(f"{name} failed: {response}")
        return result["structuredContent"]

    def close(self, *, expected_exit=(0,)):
        if self.closed:
            return
        self.closed = True
        forced = False
        try:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                forced = True
                self.proc.kill()
                self.proc.wait(timeout=10)
            self.thread.join(timeout=5)
            self.proc.stdout.close()
        finally:
            self.stderr.close()
            self.transcript.close()
        if forced or self.proc.returncode not in expected_exit:
            raise ProtocolError(f"MCP shutdown failed: forced={forced}, exit={self.proc.returncode}")
        while not self.events.empty():
            event = self.events.get_nowait()
            if event is not None:
                self._accept(event)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            if exc is None:
                raise
