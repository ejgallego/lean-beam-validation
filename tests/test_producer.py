import copy
import unittest

from producer import select_candidates


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.run = {"id": 10, "head_sha": "a" * 40, "status": "completed", "conclusion": "success"}
        self.artifacts = [
            {"id": index, "name": name, "expired": False, "digest": "sha256:" + "c" * 64,
             "workflow_run": {"id": 10, "head_sha": "a" * 40}}
            for index, name in enumerate(["build-Linux release", "build-Linux aarch64",
                                          "build-macOS", "build-macOS aarch64"], 1)]

    def select(self):
        return select_candidates(self.run, self.artifacts, "leanprover/lean4", 10, "a" * 40, "b" * 40)

    def test_waits_for_success_and_freezes_all_four_exact_artifacts(self):
        self.run["status"] = "in_progress"
        self.assertIsNone(self.select())
        self.run["status"] = "completed"
        rows = self.select()
        self.assertEqual({row["candidate"]["artifact_id"] for row in rows}, {1, 2, 3, 4})
        self.assertEqual(len({row["platform"] for row in rows}), 4)
        self.assertTrue(all(row["candidate"]["wrapper_sha256"] == "c" * 64 for row in rows))

    def test_wrong_run_or_revision_and_failed_producer_cannot_validate(self):
        for field, value in [("id", 11), ("head_sha", "d" * 40), ("conclusion", "failure")]:
            original = self.run[field]
            self.run[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.select()
            self.run[field] = original

    def test_missing_duplicate_expired_or_wrong_source_artifact_fails(self):
        original = copy.deepcopy(self.artifacts)
        variants = [original[:-1], original + [copy.deepcopy(original[0])]]
        for field, value in [("expired", True), ("digest", None),
                             ("workflow_run", {"id": 11, "head_sha": "a" * 40})]:
            changed = copy.deepcopy(original)
            changed[0][field] = value
            variants.append(changed)
        for artifacts in variants:
            self.artifacts = artifacts
            with self.subTest(artifacts=artifacts), self.assertRaises(ValueError):
                self.select()
