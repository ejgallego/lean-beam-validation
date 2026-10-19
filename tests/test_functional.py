import argparse
from pathlib import Path
import tempfile
import unittest

from functional import fixture, position, writable_sdk_copy


class FixtureTests(unittest.TestCase):
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
