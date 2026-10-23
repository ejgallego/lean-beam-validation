import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import stat
import zipfile

from fetch import check_artifact, extract_tar, unpack, select_package


class ArtifactTests(unittest.TestCase):
    def test_package_selection_requires_exact_name_when_formats_coexist(self):
        with tempfile.TemporaryDirectory() as scratch:
            wrapper = Path(scratch) / 'artifact.zip'
            with zipfile.ZipFile(wrapper, 'w') as stream:
                stream.writestr('lean.tar.zst', b'tar')
                stream.writestr('lean.zip', b'zip')
            with zipfile.ZipFile(wrapper) as stream:
                with self.assertRaises(ValueError):
                    select_package(stream)
                self.assertEqual(select_package(stream, 'lean.zip').filename, 'lean.zip')
                with self.assertRaises(ValueError):
                    select_package(stream, 'absent.zip')

    def test_zip_extraction_preserves_bytes_and_executable_mode(self):
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / 'lean.zip'
            with zipfile.ZipFile(archive, 'w') as stream:
                executable = zipfile.ZipInfo('sdk/bin/lean')
                executable.create_system = 3
                executable.external_attr = (stat.S_IFREG | 0o755) << 16
                stream.writestr(executable, b'lean')
                stream.writestr('sdk/lib/lean-beam/provenance.json', b'{}')
            root = unpack(archive, Path(scratch) / 'SDK with spaces λ')
            self.assertEqual((root / 'bin/lean').read_bytes(), b'lean')
            self.assertTrue((root / 'bin/lean').stat().st_mode & 0o100)

    def test_zip_rejects_unsafe_names_links_and_duplicate_members(self):
        for name in ['../outside', '/outside', 'C:/outside', 'sdk/../outside', 'sdk\\outside']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as scratch:
                archive = Path(scratch) / 'lean.zip'
                with zipfile.ZipFile(archive, 'w') as stream:
                    stream.writestr(name, b'escape')
                with self.assertRaises(ValueError):
                    unpack(archive, Path(scratch) / 'out')
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / 'lean.zip'
            with zipfile.ZipFile(archive, 'w') as stream:
                link = zipfile.ZipInfo('sdk/link')
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                stream.writestr(link, b'/outside')
            with self.assertRaises(ValueError):
                unpack(archive, Path(scratch) / 'out')
        with tempfile.TemporaryDirectory() as scratch:
            archive = Path(scratch) / 'lean.zip'
            with zipfile.ZipFile(archive, 'w') as stream:
                stream.writestr('sdk/file', b'first')
                stream.writestr('sdk/./file', b'second')
            with self.assertRaises(ValueError):
                unpack(archive, Path(scratch) / 'out')

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
