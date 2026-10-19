from pathlib import Path
import tempfile
import unittest

from macos import dylib_paths, verify_macho_dependencies, verify_live_images
from run_macos import sandbox_profile
from processes import descendants, parse_parents
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


if __name__ == "__main__":
    unittest.main()
