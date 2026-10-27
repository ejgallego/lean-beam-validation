import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from candidate import load_candidate, main, verify_download
from validate import sha256


class CandidateTests(unittest.TestCase):
    def test_validate_routes_frozen_candidates_to_the_native_consumer(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            archive = root / "sdk.tar.zst"
            archive.write_bytes(b"frozen package fixture")
            identity = {"artifact_metadata": {"id": 2, "expired": False,
                        "workflow_run": {"id": 1, "head_sha": "a" * 40}},
                        "inner_archive_filename": archive.name, "inner_archive_sha256": sha256(archive)}
            (root / "identity.json").write_text(json.dumps(identity))
            path = root / "candidate.json"
            for platform, consumer, architecture in [
                    ("linux-x86_64", "run_linux.py", "x86_64"),
                    ("linux-aarch64", "run_linux.py", "aarch64"),
                    ("macos-x86_64", "run_macos.py", "x86_64"),
                    ("macos-aarch64", "run_macos.py", "arm64")]:
                record = {"producer_repository": "leanprover/lean4", "producer_run": 1, "artifact_id": 2,
                          "lean_sha": "a" * 40, "beam_sha": "b" * 40, "platform": platform}
                path.write_text(json.dumps(record))
                argv = ["candidate.py", "validate", "--candidate", str(path), "--platform", platform,
                        "--download", str(root), "--output", str(root / "evidence"), "--tier", "functional"]
                with self.subTest(platform=platform), patch("sys.argv", argv), patch("candidate.subprocess.run") as run:
                    main()
                    command = run.call_args.args[0]
                    self.assertEqual(Path(command[1]).name, consumer)
                    self.assertEqual(command[command.index("--expected-architecture") + 1], architecture)
                    self.assertEqual(command[command.index("--archive") + 1], archive)
                    self.assertEqual(command[command.index("--tier") + 1], "functional")

    def test_native_candidates_cannot_cross_platforms(self):
        platforms = ["linux-x86_64", "linux-aarch64", "macos-x86_64", "macos-aarch64"]
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "candidate.json"
            for platform in platforms:
                data = {"producer_repository": "leanprover/lean4", "producer_run": 1, "artifact_id": 2,
                        "lean_sha": "a" * 40, "beam_sha": "b" * 40, "platform": platform}
                path.write_text(json.dumps(data))
                self.assertEqual(load_candidate(path, platform), data)
                for other in platforms:
                    if other != platform:
                        with self.subTest(platform=platform, other=other), self.assertRaises(ValueError):
                            load_candidate(path, other)

    def test_frozen_member_and_both_digests_are_enforced(self):
        record = {"producer_run": 1, "artifact_id": 2, "lean_sha": "a" * 40,
                  "inner_archive_filename": "sdk.tar.zst", "inner_archive_sha256": "b" * 64,
                  "wrapper_sha256": "c" * 64}
        identity = {"artifact_metadata": {"id": 2, "expired": False,
                    "workflow_run": {"id": 1, "head_sha": "a" * 40}},
                    **{key: record[key] for key in ["inner_archive_filename", "inner_archive_sha256", "wrapper_sha256"]}}
        verify_download(record, identity)
        for key, value in [("inner_archive_filename", "sdk.zip"),
                           ("inner_archive_sha256", "d" * 64), ("wrapper_sha256", "e" * 64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_download(record, {**identity, key: value})

    def test_candidate_cannot_select_another_repository_or_invalid_revision(self):
        data = {"producer_repository": "leanprover/lean4", "producer_run": 1, "artifact_id": 2,
                "lean_sha": "a" * 40, "beam_sha": "b" * 40, "platform": "macos-aarch64",
                "wrapper_sha256": "c" * 64}
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "candidate.json"
            path.write_text(json.dumps(data))
            self.assertEqual(load_candidate(path, "macos-aarch64"), data)
            for key, value in [("producer_repository", "somewhere/else"), ("lean_sha", "latest"), ("artifact_id", -1)]:
                changed = {**data, key: value}
                path.write_text(json.dumps(changed))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    load_candidate(path, "macos-aarch64")
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_candidate(path, "macos-x86_64")


if __name__ == "__main__":
    unittest.main()
