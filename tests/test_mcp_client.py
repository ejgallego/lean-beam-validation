import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp_client import McpClient, ProtocolError


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def client(self, code, **kwargs):
        return McpClient([sys.executable, "-u", "-c", code], self.root,
                         self.root / "transcript", timeout=0.5, **kwargs)

    def test_notifications_and_utf8_are_preserved(self):
        code = '''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 print(json.dumps({'jsonrpc':'2.0','method':'notifications/progress','params':{'text':'λ'}}),flush=True)
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':m['params']}),flush=True)
'''
        with self.client(code) as client:
            response = client.request("echo", {"text": "hello λ"})
            self.assertEqual(response["result"]["text"], "hello λ")
            self.assertEqual(client.notifications[0]["method"], "notifications/progress")
        self.assertIn("hello λ", (self.root / "transcript.jsonl").read_text())

    def test_non_protocol_stdout_and_wrong_response_id_fail(self):
        for output in ["noise", json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}),
                       json.dumps({"jsonrpc": "2.0", "id": True, "result": {}})]:
            code = f'import sys\nsys.stdin.readline()\nprint({output!r},flush=True)\n'
            with self.subTest(output=output), self.client(code) as client:
                with self.assertRaises(ProtocolError):
                    client.request("echo")

    def test_request_deadline_is_bounded_and_logs_retained(self):
        code = "import sys,time\nsys.stdin.readline()\nprint('entered',file=sys.stderr,flush=True)\ntime.sleep(30)\n"
        client = self.client(code, shutdown_timeout=0.2)
        with self.assertRaises(TimeoutError):
            client.request("hang")
        with self.assertRaises(ProtocolError):
            client.close()
        self.assertIsNotNone(client.proc.poll())
        self.assertIn("entered", (self.root / "transcript.stderr").read_text())

    def test_modern_requests_carry_protocol_metadata(self):
        code = '''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' in m: print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':m['params']}),flush=True)
'''
        with self.client(code, modern=True) as client:
            result = client.request("echo", {"value": 7})["result"]
            self.assertEqual(result["_meta"]["io.modelcontextprotocol/protocolVersion"], "2026-07-28")
            self.assertEqual(result["_meta"]["io.modelcontextprotocol/clientCapabilities"], {})
            self.assertEqual(result["_meta"]["io.modelcontextprotocol/clientInfo"]["name"], "beam-artifact-validation")

    def test_noise_after_final_response_is_rejected_at_shutdown(self):
        code = '''import sys,json
m=json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{}}),flush=True)
for line in sys.stdin: pass
print('non-protocol shutdown output',flush=True)
'''
        client = self.client(code)
        client.request("echo")
        with self.assertRaises(ProtocolError):
            client.close()


if __name__ == "__main__":
    unittest.main()
