# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Run a complete SDK extraction in a read-only, offline Linux namespace."""

import argparse
import hashlib
import json
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
    parser.add_argument("--tier", choices=["pilot", "functional"], default="pilot")
    parser.add_argument("--elan", type=Path, help="real elan used only to prepare offline selector links")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    harness = Path(__file__).resolve().parent
    harness_snapshot = {p.name: p.read_bytes() for p in harness.glob("*.py")}
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=harness, text=True, capture_output=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=harness, text=True, capture_output=True, check=True).stdout.strip())
    write_json(args.output / "input.json", {
        "archive": args.archive.name, "archive_sha256": sha256(args.archive),
        "lean_sha": args.lean_sha, "beam_sha": args.beam_sha,
        "producer_manifest_sha256": args.producer_manifest_sha256,
        "harness_revision": revision, "harness_worktree_dirty": dirty,
        "harness_files": {name: hashlib.sha256(data).hexdigest() for name, data in harness_snapshot.items()},
    })
    source_identity = args.archive.parent / "identity.json"
    if source_identity.is_file():
        identity = json.loads(source_identity.read_text())
        if identity["inner_archive_sha256"] != sha256(args.archive):
            raise ValueError("download identity does not match the supplied archive")
        shutil.copyfile(source_identity, args.output / "artifact-identity.json")
    if sys.platform != "linux" or not shutil.which("bwrap"):
        raise SystemExit("BLOCKED: Linux and bubblewrap are required; isolation has no silent fallback")
    with tempfile.TemporaryDirectory(prefix="beam-artifact-", dir=args.scratch_parent) as temporary:
        scratch = Path(temporary).resolve()
        for name, data in harness_snapshot.items():
            (scratch / name).write_bytes(data)
        sdk = unpack(args.archive, scratch / "SDK extraction with spaces λ")
        evidence = scratch / "evidence"
        evidence.mkdir()
        for name in ["home", "tmp", "elan-home"]:
            (scratch / name).mkdir()
        if args.tier == "functional":
            real_elan = args.elan or shutil.which("elan")
            if not real_elan:
                raise SystemExit("BLOCKED: functional selector fixtures need a real elan executable")
            foreign = scratch / "independent foreign SDK"
            shutil.copytree(sdk, foreign, symlinks=True)
            tools = scratch / "tools"
            tools.mkdir()
            elan = tools / "elan"
            shutil.copyfile(real_elan, elan)
            elan.chmod(0o755)
            alias = scratch / "SDK canonical alias"
            alias.symlink_to(sdk, target_is_directory=True)
            setup_env = {"HOME": str(scratch / "home"), "ELAN_HOME": str(scratch / "elan-home"), "PATH": "/usr/bin:/bin"}
            for name, target in [("beam-matching", sdk), ("beam-matching-second", sdk),
                                 ("beam-alias", alias), ("beam-foreign", foreign)]:
                subprocess.run([elan, "toolchain", "link", name, target], env=setup_env, check=True,
                               capture_output=True, text=True, timeout=30)
            (scratch / "elan-home/toolchains/beam-dangling").symlink_to(scratch / "absent SDK")
            elan.write_text('#!/bin/sh\nprintf "unexpected elan invocation\\n" >> "$HOME/elan-invocations"\nexit 99\n')
            elan.chmod(0o755)
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
            "PATH": f"{scratch}/tools:{sdk}/bin:/usr/bin:/bin", "HOME": str(scratch / "home"),
            "TMPDIR": str(scratch / "tmp"), "ELAN_HOME": str(scratch / "elan-home"),
            "USERPROFILE": str(scratch / "home"), "XDG_CACHE_HOME": str(scratch / "home/cache"),
            "BEAM_HOME": str(scratch / "forbidden-source"),
            "BEAM_BUNDLE_DIR": str(scratch / "forbidden-runtime-cache"),
            "BEAM_INSTALL_BUNDLE_DIR": str(scratch / "forbidden-install-cache"),
            "LANG": "C.UTF-8", "LEAN_NUM_THREADS": "2",
        }
        command += ["--clearenv"]
        for name, value in environment.items():
            command += ["--setenv", name, value]
        command += ["--chdir", str(scratch), "/usr/bin/python3", str(scratch / "validate.py"),
                    "--prefix", str(sdk), "--lean-sha", args.lean_sha,
                    "--beam-sha", args.beam_sha, "--output", str(evidence), "--tier", args.tier]
        for path in forbidden:
            command += ["--forbidden-path", str(path.resolve())]
        if args.tier == "functional":
            command += ["--foreign-prefix", str(foreign)]
        for option, value in [("--producer-manifest-sha256", args.producer_manifest_sha256), ("--build-id", args.build_id)]:
            if value:
                command += [option, value]
        write_json(evidence / "isolation.json", {
            "network": "unshared namespace", "sdk": "read-only bind", "environment": environment,
            "producer_paths_hidden_or_absent": list(map(str, forbidden)),
            "consumer_platform": dict(zip(["system", "node", "release", "version", "machine"], os.uname())),
        })
        shutil.copyfile(args.output / "input.json", evidence / "input.json")
        if (args.output / "artifact-identity.json").exists():
            shutil.copyfile(args.output / "artifact-identity.json", evidence / "artifact-identity.json")
        try:
            result = subprocess.run(command, timeout=1800 if args.tier == "functional" else 900)
        finally:
            shutil.copytree(evidence, args.output, dirs_exist_ok=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    print(f"Linux {args.tier} checks passed; release qualification remains pending. Evidence: {args.output}")


if __name__ == "__main__":
    main()
