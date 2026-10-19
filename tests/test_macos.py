from pathlib import Path
import errno
import json
import subprocess
import sys
import tempfile
import unittest

from macos import dylib_paths, verify_macho_dependencies, verify_live_images, verify_loopback_listeners
from run_macos import sandbox_profile
from processes import descendants, parse_parents
from sandbox_probe import denied, observe_permission
from validate import ValidationError


class MacTests(unittest.TestCase):
    def test_library_paths_with_spaces_and_unicode(self):
        text = '''__TEXT  100000-200000 /private/tmp/SDK with spaces λ/lib/lean/libLake_shared.dylib
dyld[123]: <ABC-DEF> /private/tmp/SDK with spaces λ/lib/lean-beam/libbeam_Beam_LSP.dylib
__TEXT  200000-300000 /usr/lib/libSystem.B.dylib
'''
        self.assertEqual(dylib_paths(text), {
            "/private/tmp/SDK with spaces λ/lib/lean/libLake_shared.dylib",
            "/private/tmp/SDK with spaces λ/lib/lean-beam/libbeam_Beam_LSP.dylib",
            "/usr/lib/libSystem.B.dylib",
        })

    def test_macho_dependencies_reject_external_runtime_and_relative_build_paths(self):
        with tempfile.TemporaryDirectory() as scratch:
            prefix = Path(scratch).resolve()
            verify_macho_dependencies("beam:\n\t@rpath/libLake_shared.dylib (compatibility version 0.0.0, current version 0.0.0)\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)", prefix)
            for path in ["/opt/homebrew/lib/libgmp.dylib", "/producer/lib/libLake_shared.dylib", "../../build/libLean.dylib"]:
                with self.subTest(path=path), self.assertRaises(ValidationError):
                    verify_macho_dependencies(f"beam:\n\t{path} (compatibility version 1.0.0, current version 1.0.0)", prefix)

    def test_profile_quotes_sdk_path_without_broad_outbound_permission(self):
        sdk = Path('/private/tmp/SDK "quoted" λ')
        profile = sandbox_profile(sdk)
        self.assertIn('(deny network*)', profile)
        self.assertIn('(remote ip "localhost:*")', profile)
        self.assertNotIn('(remote ip "*:*")', profile)
        self.assertIn('(deny file-write* (subpath "/private/tmp/SDK \\"quoted\\" λ"))', profile)

    def test_transitive_homebrew_library_cannot_mask_missing_sdk_payload(self):
        with tempfile.TemporaryDirectory() as scratch:
            prefix = Path(scratch).resolve()
            verify_live_images({"/usr/lib/libSystem.B.dylib", str(prefix / "lib/lean/libgmp.dylib")}, prefix)
            with self.assertRaises(ValidationError):
                verify_live_images({"/opt/homebrew/opt/gmp/lib/libgmp.10.dylib"}, prefix)

    def test_process_tree_uses_parent_edges_only(self):
        parents = parse_parents("  10 1\n  20 10\n  30 20\n  40 1\n")
        self.assertEqual(descendants(10, parents), {10, 20, 30})
        with self.assertRaises(ValueError):
            parse_parents("not a process listing")

    def test_live_tcp_listeners_must_be_loopback(self):
        self.assertEqual(verify_loopback_listeners("p10\nn127.0.0.1:4567\np20\nn[::1]:5678\n"),
                         {"10": ["127.0.0.1:4567"], "20": ["[::1]:5678"]})
        for endpoint in ["*:4567", "0.0.0.0:4567", "[::]:4567", "192.0.2.1:4567"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValidationError):
                verify_loopback_listeners(f"p10\nn{endpoint}\n")
        with self.assertRaises(ValidationError):
            verify_loopback_listeners("")

    def test_wildcard_bind_is_observed_but_required_denials_still_fail_closed(self):
        self.assertEqual(observe_permission(lambda: None, "wildcard bind"), {"denied": False})
        with self.assertRaisesRegex(AssertionError, "external TCP was permitted"):
            denied(lambda: None, "external TCP")
        def permission_error():
            raise PermissionError(errno.EPERM, "denied by sandbox")
        self.assertEqual(denied(permission_error, "SDK write access"), {"denied": True, "errno": errno.EPERM})
        def unavailable_network():
            raise OSError(errno.ENETUNREACH, "no route")
        with self.assertRaisesRegex(AssertionError, "unexpected failure"):
            denied(unavailable_network, "external TCP")

    @unittest.skipUnless(sys.platform == "darwin", "requires native macOS Seatbelt")
    def test_native_sandbox_preflight_before_archive_download(self):
        with tempfile.TemporaryDirectory(prefix="beam-preflight-") as directory:
            root = Path(directory).resolve()
            sdk = root / "SDK with spaces λ"
            manifest = sdk / "lib/lean-beam/provenance.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_bytes(b"{}\n")
            for name in ["home", "tmp"]:
                (root / name).mkdir()
            profile = root / "sandbox.sb"
            profile.write_text(sandbox_profile(sdk))
            output = root / "probe.json"
            probe = Path(__file__).resolve().parent.parent / "sandbox_probe.py"
            result = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(profile), sys.executable,
                                     str(probe), "--prefix", str(sdk), "--output", str(output)],
                                    env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root / "home"),
                                         "TMPDIR": str(root / "tmp"), "LANG": "en_US.UTF-8"},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr +
                             (output.read_text() if output.exists() else ""))
            observations = json.loads(output.read_text())
            self.assertEqual(observations["status"], "PASS")
            self.assertEqual(observations["loopback"], "PASS")
            self.assertTrue(observations["external_tcp"]["denied"])
            self.assertTrue(observations["sdk_write"]["denied"])
            self.assertEqual(observations["fixture_write"], "PASS")
            self.assertEqual(manifest.read_bytes(), b"{}\n")


if __name__ == "__main__":
    unittest.main()
