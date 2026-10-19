import json
from pathlib import Path
import tempfile
import unittest

from candidate import load_candidate


class CandidateTests(unittest.TestCase):
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
