import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import argparse
import sys

from validate import Pilot, ValidationError, verify_executable_dependencies, verify_inventory


LEAN = "1" * 40
BEAM = "2" * 40


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prefix = Path(self.temp.name) / "SDK with spaces λ"
        self.destinations = ["bin/lean-beam", "bin/lean-beam-mcp", "bin/beam-daemon",
                             "lib/lean-beam/libbeam_Beam_LSP.so", "lib/lean-beam/LICENSE"]
        artifacts = []
        for destination in self.destinations:
            path = self.prefix / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(destination.encode())
            artifacts.append({"destination": destination, "hash": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.record = {
            "schemaVersion": 3, "sourceCommit": BEAM, "buildId": "3" * 32,
            "payloadHash": "4" * 64, "artifacts": artifacts,
            "components": {
                "frontends": {"cli": self.destinations[0], "mcp": self.destinations[1], "compilerRevision": LEAN},
                "targets": [{"lean": "bin/lean", "daemon": self.destinations[2],
                             "plugin": self.destinations[3], "compilerRevision": LEAN}],
                "license": self.destinations[4],
            },
        }
        self.manifest = self.prefix / "lib/lean-beam/provenance.json"
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.record))

    def verify(self, **kwargs):
        return verify_inventory(self.prefix, LEAN, BEAM, **kwargs)

    def test_complete_payload_and_external_provenance_digest(self):
        digest = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        result = self.verify(manifest_sha256=digest)
        self.assertEqual(len(result["files"]), 6)
        self.assertEqual(result["manifest_sha256"], digest)

    def test_corruption_and_missing_payload_fail_before_execution(self):
        for destination in self.destinations:
            with self.subTest(destination=destination):
                path = self.prefix / destination
                original = path.read_bytes()
                path.write_bytes(original + b"corrupt")
                with self.assertRaises(ValidationError):
                    self.verify()
                path.unlink()
                with self.assertRaises(ValidationError):
                    self.verify()
                path.write_bytes(original)

    def test_wrong_candidate_rejected(self):
        with self.assertRaises(ValidationError):
            verify_inventory(self.prefix, "9" * 40, BEAM)
        with self.assertRaises(ValidationError):
            verify_inventory(self.prefix, LEAN, "9" * 40)
        with self.assertRaises(ValidationError):
            self.verify(manifest_sha256="9" * 64)
        with self.assertRaises(ValidationError):
            self.verify(build_id="9" * 32)

    def test_unsafe_destinations_rejected(self):
        for destination in ["../outside", "/tmp/outside", "C:/outside", "bin\\escape", "bin/../outside"]:
            with self.subTest(destination=destination):
                old = self.record["artifacts"][0]["destination"]
                self.record["artifacts"][0]["destination"] = destination
                self.write_manifest()
                with self.assertRaises(ValidationError):
                    self.verify()
                self.record["artifacts"][0]["destination"] = old

    def test_duplicate_and_undeclared_payload_rejected(self):
        self.record["artifacts"].append(self.record["artifacts"][0])
        self.write_manifest()
        with self.assertRaises(ValidationError):
            self.verify()
        self.record["artifacts"].pop()
        self.write_manifest()
        (self.prefix / "lib/lean-beam/undeclared.so").write_bytes(b"extra runtime")
        with self.assertRaises(ValidationError):
            self.verify()

    def test_second_target_and_compiler_mismatch_rejected(self):
        self.record["components"]["targets"].append(self.record["components"]["targets"][0])
        self.write_manifest()
        with self.assertRaises(ValidationError):
            self.verify()
        self.record["components"]["targets"].pop()
        self.record["components"]["targets"][0]["compilerRevision"] = "9" * 40
        self.write_manifest()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_external_symlink_rejected_even_with_matching_bytes(self):
        original = self.prefix / self.destinations[0]
        foreign = self.prefix.parent / "foreign"
        foreign.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(foreign)
        with self.assertRaises(ValidationError):
            self.verify()


class PilotMcpTests(unittest.TestCase):
    def probe(self, root, version=1, tools=('lean_sync',)):
        server = root / 'lean-beam-mcp'
        server.write_text(f'''#!{sys.executable}
import sys, json
assert len(sys.argv) == 1, 'probe must use stdio without a private self-check flag'
for line in sys.stdin:
    m = json.loads(line)
    if 'id' not in m: continue
    result = {{}}
    if m['method'] == 'initialize': result = {{'protocolVersion': '2025-11-25'}}
    elif m['method'] == 'server/discover': result = {{'supportedVersions': ['2026-07-28']}}
    elif m['method'] == 'tools/list': result = {{'tools': [{{'name': name}} for name in {list(tools)!r}]}}
    elif m['method'] == 'tools/call': result = {{'structuredContent': {{'version': {version!r}}}}}
    else: raise RuntimeError(m['method'])
    print(json.dumps({{'jsonrpc': '2.0', 'id': m['id'], 'result': result}}), flush=True)
''')
        server.chmod(0o755)
        args = argparse.Namespace(prefix=root, output=root / 'evidence')
        pilot = Pilot(args)
        pilot.mcp = server
        pilot.project = root
        return pilot

    def test_probe_drives_both_protocols_through_external_stdio(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            pilot = self.probe(root)
            pilot.mcp_probe()
            for modern, admission in [(False, 'initialize'), (True, 'server/discover')]:
                events = [json.loads(line) for line in
                          (root / f'evidence/mcp-pilot-{modern}.jsonl').read_text().splitlines()]
                methods = [event['message']['method'] for event in events if event['direction'] == 'client']
                self.assertIn(admission, methods)
                self.assertIn('tools/list', methods)
                self.assertIn('tools/call', methods)

    def test_probe_rejects_missing_sync_and_invalid_version(self):
        for version, tools in [(1, ()), (True, ('lean_sync',)), (-1, ('lean_sync',))]:
            with self.subTest(version=version, tools=tools), tempfile.TemporaryDirectory() as scratch:
                pilot = self.probe(Path(scratch), version, tools)
                with self.assertRaises(ValidationError):
                    pilot.mcp_probe()


class LoaderTests(unittest.TestCase):
    def test_executable_missing_or_external_lean_library_rejected(self):
        prefix = Path("/tmp/sdk").resolve()
        verify_executable_dependencies("libLake_shared.so => /tmp/sdk/lib/lean/libLake_shared.so (0x123)", prefix)
        for text in ["libLake_shared.so => not found", "libc.so.6 => not found",
                     "libLake_shared.so => /tmp/foreign/libLake_shared.so (0x123)"]:
            with self.subTest(text=text), self.assertRaises(ValidationError):
                verify_executable_dependencies(text, prefix)


if __name__ == "__main__":
    unittest.main()
