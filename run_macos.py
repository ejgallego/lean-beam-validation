# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Consume an SDK on a fresh native Mac with external TCP and SDK writes denied."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile

from fetch import unpack
from validate import sha256, write_json


def sandbox_profile(sdk):
    quoted = json.dumps(str(sdk.resolve()), ensure_ascii=False)
    return '\n'.join([
        '(version 1)', '(allow default)', '(deny network*)',
        '(allow network-bind (local ip "localhost:*"))',
        '(allow network-inbound (local ip "localhost:*"))',
        '(allow network-outbound (remote ip "localhost:*"))',
        f'(deny file-write* (subpath {quoted}))', '',
    ])


def prepare_selectors(sdk, scratch, real_elan):
    foreign = scratch / "independent foreign SDK"
    shutil.copytree(sdk, foreign, symlinks=True)
    tools = scratch / "tools"
    tools.mkdir()
    elan = tools / "elan"
    shutil.copyfile(real_elan, elan)
    elan.chmod(0o755)
    alias = scratch / "SDK canonical alias"
    alias.symlink_to(sdk, target_is_directory=True)
    env = {"HOME": str(scratch / "home"), "ELAN_HOME": str(scratch / "elan-home"), "PATH": "/usr/bin:/bin"}
    for name, target in [("beam-matching", sdk), ("beam-matching-second", sdk), ("beam-alias", alias), ("beam-foreign", foreign)]:
        subprocess.run([elan, "toolchain", "link", name, target], env=env, check=True,
                       capture_output=True, text=True, timeout=30)
    (scratch / "elan-home/toolchains/beam-dangling").symlink_to(scratch / "absent SDK")
    elan.write_text('#!/bin/sh\nprintf "unexpected elan invocation\\n" >> "$HOME/elan-invocations"\nexit 99\n')
    elan.chmod(0o755)
    return foreign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--lean-sha", required=True)
    parser.add_argument("--beam-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-architecture", required=True, choices=["arm64", "x86_64"])
    parser.add_argument("--tier", choices=["pilot", "functional"], default="pilot")
    parser.add_argument("--producer-manifest-sha256")
    parser.add_argument("--build-id")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if sys.platform != "darwin" or platform.machine() != args.expected_architecture:
        raise SystemExit("BLOCKED: the selected native macOS architecture is required")
    if not Path("/usr/bin/sandbox-exec").is_file():
        raise SystemExit("BLOCKED: sandbox-exec unavailable; no weaker isolation fallback")
    forbidden = [Path("/Users/runner/work/lean4/lean4"), Path("/Users/runner/_work/lean4/lean4")]
    if any(path.exists() for path in forbidden):
        raise SystemExit("BLOCKED: producer checkout exists; use a fresh consumer runner")
    harness = Path(__file__).resolve().parent
    snapshot = {path.name: path.read_bytes() for path in harness.glob("*.py")}
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=harness, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=harness, text=True).strip())
    with tempfile.TemporaryDirectory(prefix="beam-mac-") as directory:
        scratch = Path(directory).resolve()
        for name, data in snapshot.items():
            (scratch / name).write_bytes(data)
        evidence = scratch / "evidence"
        evidence.mkdir()
        write_json(evidence / "input.json", {
            "archive": args.archive.name, "archive_sha256": sha256(args.archive),
            "lean_sha": args.lean_sha, "beam_sha": args.beam_sha,
            "harness_revision": revision, "harness_worktree_dirty": dirty,
            "harness_files": {name: hashlib.sha256(data).hexdigest() for name, data in snapshot.items()},
            "expected_architecture": args.expected_architecture,
            "producer_manifest_sha256": args.producer_manifest_sha256,
        })
        identity_path = args.archive.parent / "identity.json"
        if identity_path.is_file():
            identity = json.loads(identity_path.read_text())
            if identity["inner_archive_sha256"] != sha256(args.archive):
                raise ValueError("download identity does not match supplied archive")
            shutil.copyfile(identity_path, evidence / "artifact-identity.json")
        try:
            sdk = unpack(args.archive, scratch / "SDK extraction with spaces λ")
            for name in ["home", "tmp", "elan-home"]:
                (scratch / name).mkdir()
            foreign = None
            if args.tier == "functional":
                real_elan = shutil.which("elan")
                if not real_elan:
                    raise SystemExit("BLOCKED: functional selectors require a real elan executable")
                foreign = prepare_selectors(sdk, scratch, real_elan)
            profile = evidence / "sandbox.sb"
            profile.write_text(sandbox_profile(sdk))
            environment = {
                "PATH": f"{scratch}/tools:{sdk}/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(scratch / "home"),
                "TMPDIR": str(scratch / "tmp"), "ELAN_HOME": str(scratch / "elan-home"),
                "USERPROFILE": str(scratch / "home"), "XDG_CACHE_HOME": str(scratch / "home/cache"),
                "BEAM_HOME": str(scratch / "forbidden-source"),
                "BEAM_BUNDLE_DIR": str(scratch / "forbidden-runtime-cache"),
                "BEAM_INSTALL_BUNDLE_DIR": str(scratch / "forbidden-install-cache"), "LANG": "en_US.UTF-8",
            }
            write_json(evidence / "isolation.json", {
                "network": "Seatbelt: host-local TCP allowed; external TCP denied; wildcard bind permission measured",
                "sdk": "Seatbelt: file writes denied", "environment": environment,
                "producer_paths_asserted_absent": list(map(str, forbidden)),
                "consumer_platform": dict(zip(["system", "node", "release", "version", "machine"], os.uname())),
            })
            base_command = ["/usr/bin/sandbox-exec", "-f", str(profile), sys.executable]
            with (evidence / "sandbox-probe.stdout").open("w") as stdout, (evidence / "sandbox-probe.stderr").open("w") as stderr:
                subprocess.run([*base_command, str(scratch / "sandbox_probe.py"), "--prefix", str(sdk),
                    "--output", str(evidence / "sandbox-probe.json")], env=environment, cwd=scratch,
                    stdout=stdout, stderr=stderr, check=True, timeout=30)
            command = [*base_command, str(scratch / "validate.py"), "--prefix", str(sdk), "--lean-sha", args.lean_sha,
                "--beam-sha", args.beam_sha, "--output", str(evidence), "--tier", args.tier,
                "--expected-architecture", args.expected_architecture]
            for path in forbidden:
                command += ["--forbidden-path", str(path)]
            for option, value in [("--foreign-prefix", foreign), ("--producer-manifest-sha256", args.producer_manifest_sha256), ("--build-id", args.build_id)]:
                if value:
                    command += [option, str(value)]
            with subprocess.Popen(command, env=environment, cwd=scratch, start_new_session=True) as process:
                try:
                    result = process.wait(timeout=1800 if args.tier == "functional" else 900)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                    raise
            if result:
                raise SystemExit(result)
        finally:
            shutil.copytree(evidence, args.output, dirs_exist_ok=True)
    print(f"macOS {args.tier} passed; release qualification remains pending. Evidence: {args.output}")


if __name__ == "__main__":
    main()
