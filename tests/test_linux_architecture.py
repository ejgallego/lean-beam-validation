import tempfile
from pathlib import Path
import unittest

from run_linux import verify_native_elf
from validate import ValidationError


class LinuxArchitectureTests(unittest.TestCase):
    def test_wrong_isa_and_non_native_headers_fail_before_execution(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "executable"
            for architecture, machine in [("x86_64", 62), ("aarch64", 183)]:
                header = b"\x7fELF\x02\x01\x01" + bytes(11) + machine.to_bytes(2, "little")
                path.write_bytes(header)
                verify_native_elf(path, architecture)
                other = "aarch64" if architecture == "x86_64" else "x86_64"
                with self.assertRaises(ValidationError):
                    verify_native_elf(path, other)
                for malformed in [b"", b"not an ELF", header[:4] + b"\x01" + header[5:],
                                  header[:5] + b"\x02" + header[6:]]:
                    path.write_bytes(malformed)
                    with self.assertRaises(ValidationError):
                        verify_native_elf(path, architecture)
