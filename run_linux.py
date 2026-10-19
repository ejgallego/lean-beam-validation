# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Run a complete SDK extraction in a read-only, offline Linux namespace."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from fetch import unpack
from validate import sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--lean-sha", required=True)
    parser.add_argument("--beam-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer-manifest-sha256")
    parser.add_argument("--build-id")
    parser.add_argument("--hide-path", action="append", type=Path, default=[])
    parser.add_argument("--scratch-parent", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "input.json", {
        "archive": args.archive.name, "archive_sha256": sha256(args.archive),
        "lean_sha": args.lean_sha, "beam_sha": args.beam_sha,
        "producer_manifest_sha256": args.producer_manifest_sha256,
    })
    if sys.platform != "linux" or not shutil.which("bwrap"):
        raise SystemExit("BLOCKED: Linux and bubblewrap are required; isolation has no silent fallback")
    with tempfile.TemporaryDirectory(prefix="beam-artifact-", dir=args.scratch_parent) as temporary:
        scratch = Path(temporary).resolve()
        sdk = unpack(args.archive, scratch / "SDK extraction with spaces λ")
        evidence = scratch / "evidence"
        evidence.mkdir()
        for name in ["home", "tmp", "elan-home"]:
            (scratch / name).mkdir()
        shutil.copyfile(Path(__file__).with_name("validate.py"), scratch / "validate.py")
        command = ["bwrap", "--die-with-parent", "--unshare-pid", "--ro-bind", "/", "/", "--dev", "/dev",
                   "--proc", "/proc", "--unshare-net", "--bind", str(scratch), str(scratch),
                   "--ro-bind", str(sdk), str(sdk)]
        forbidden = list(args.hide_path) + [Path("/home/runner/work/lean4/lean4")]
        for path in forbidden:
            path = path.resolve()
            if scratch.is_relative_to(path):
                raise ValueError("scratch must be outside the producer paths being hidden")
            if path.is_dir():
                command += ["--tmpfs", str(path)]
        environment = {
            "PATH": f"{sdk}/bin:/usr/bin:/bin", "HOME": str(scratch / "home"),
            "TMPDIR": str(scratch / "tmp"), "ELAN_HOME": str(scratch / "elan-home"),
            "USERPROFILE": str(scratch / "home"), "XDG_CACHE_HOME": str(scratch / "home/cache"),
            "BEAM_HOME": str(scratch / "forbidden-source"),
            "BEAM_BUNDLE_DIR": str(scratch / "forbidden-runtime-cache"),
            "BEAM_INSTALL_BUNDLE_DIR": str(scratch / "forbidden-install-cache"),
            "LANG": "C.UTF-8",
        }
        command += ["--clearenv"]
        for name, value in environment.items():
            command += ["--setenv", name, value]
        command += ["--chdir", str(scratch), "/usr/bin/python3", str(scratch / "validate.py"),
                    "--prefix", str(sdk), "--lean-sha", args.lean_sha,
                    "--beam-sha", args.beam_sha, "--output", str(evidence)]
        for path in forbidden:
            command += ["--forbidden-path", str(path.resolve())]
        for option, value in [("--producer-manifest-sha256", args.producer_manifest_sha256), ("--build-id", args.build_id)]:
            if value:
                command += [option, value]
        write_json(evidence / "isolation.json", {
            "network": "unshared namespace", "sdk": "read-only bind", "environment": environment,
            "producer_paths_hidden_or_absent": list(map(str, forbidden)),
            "consumer_platform": dict(zip(["system", "node", "release", "version", "machine"], os.uname())),
        })
        try:
            result = subprocess.run(command, timeout=900)
        finally:
            shutil.copytree(evidence, args.output, dirs_exist_ok=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    print(f"Initial Linux pilot passed; qualification remains pending. Evidence: {args.output}")


if __name__ == "__main__":
    main()
