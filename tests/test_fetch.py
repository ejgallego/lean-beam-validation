import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from fetch import check_artifact, extract_tar


class ArtifactTests(unittest.TestCase):
    def test_wrong_run_candidate_or_expired_artifact_rejected(self):
        artifact = {"id": 7, "expired": False, "workflow_run": {"id": 9, "head_sha": "a" * 40}}
        check_artifact(artifact, 7, 9, "a" * 40)
        for args in [(8, 9, "a" * 40), (7, 10, "a" * 40), (7, 9, "b" * 40)]:
            with self.assertRaises(ValueError):
                check_artifact(artifact, *args)
        artifact["expired"] = True
        with self.assertRaises(ValueError):
            check_artifact(artifact, 7, 9, "a" * 40)

    def test_extraction_preserves_sdk_executable(self):
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / "sdk.tar"
            with tarfile.open(archive, "w") as stream:
                info = tarfile.TarInfo("sdk/bin/lean")
                info.size = 4
                info.mode = 0o755
                stream.addfile(info, io.BytesIO(b"lean"))
            destination = Path(scratch) / "unpacked"
            extract_tar(archive, destination)
            self.assertEqual((destination / "sdk/bin/lean").read_bytes(), b"lean")
            self.assertTrue((destination / "sdk/bin/lean").stat().st_mode & 0o100)

    def test_extraction_rejects_path_and_link_escape(self):
        for name, link in [("../../outside", None), ("sdk/escape", "/tmp/foreign")]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as scratch:
                archive = Path(scratch) / "sdk.tar"
                with tarfile.open(archive, "w") as stream:
                    info = tarfile.TarInfo(name)
                    if link:
                        info.type = tarfile.SYMTYPE
                        info.linkname = link
                    stream.addfile(info)
                with self.assertRaises(tarfile.FilterError):
                    extract_tar(archive, Path(scratch) / "unpacked")


if __name__ == "__main__":
    unittest.main()
