import argparse
from pathlib import Path
import tempfile
import unittest

from functional import fixture, helper_pids, position, response_file, writable_sdk_copy
from validate import ValidationError


class FixtureTests(unittest.TestCase):
    def test_current_cli_file_fields_require_real_owned_outputs(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            output = root / "produced file"
            output.write_text("receipt")
            for field, obsolete in [("ir_sig", "irSig"), ("quarantined_path", "quarantinedPath")]:
                response = {"ok": True, "result": {field: str(output)}}
                self.assertEqual(response_file(response, field, root), output)
                for invalid in [{"ok": True, "result": {obsolete: str(output)}},
                                {"ok": True, "result": {field: str(root / 'missing')}},
                                {"ok": False, "result": {field: str(output)}}]:
                    with self.subTest(field=field, invalid=invalid), self.assertRaises(ValidationError):
                        response_file(invalid, field, root)
                with self.assertRaises(ValidationError):
                    response_file(response, field, root / 'different project')

    def test_helper_readiness_rejects_partial_pid_publication(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            self.assertIsNone(helper_pids(root))
            (root / 'helper.pid').write_text('123')
            for text in ['', 'not a PID', '0', '-4']:
                (root / 'descendant.pid').write_text(text)
                self.assertIsNone(helper_pids(root))
            (root / 'descendant.pid').write_text('456')
            self.assertEqual(helper_pids(root), [123, 456])

    def test_probe_positions_work_with_lf_and_crlf(self):
        with tempfile.TemporaryDirectory() as scratch:
            for crlf in [False, True]:
                root = fixture(Path(scratch) / str(crlf), crlf=crlf)
                text = (root / "Demo.lean").read_bytes().decode()
                line, character = position(text, "  trivial")
                self.assertEqual(text.splitlines()[line][character:], "trivial")
                self.assertEqual("\r\n" in text, crlf)
                self.assertIn("dependencyValue + 1", text)

    def test_mutation_copy_does_not_share_file_inodes(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "original"
            source.mkdir()
            (source / "binary").write_bytes(b"original")
            copy = writable_sdk_copy(source, Path(scratch) / "copy")
            (copy / "binary").write_bytes(b"changed")
            self.assertEqual((source / "binary").read_bytes(), b"original")
            self.assertNotEqual((source / "binary").stat().st_ino, (copy / "binary").stat().st_ino)


if __name__ == "__main__":
    unittest.main()
