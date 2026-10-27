# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Consume explicit candidate records without inferring a latest build."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from fetch import check_artifact
from validate import sha256


PLATFORMS = {
    "linux-x86_64": ("run_linux.py", "x86_64"),
    "linux-aarch64": ("run_linux.py", "aarch64"),
    "macos-x86_64": ("run_macos.py", "x86_64"),
    "macos-aarch64": ("run_macos.py", "arm64"),
}


def load_candidate(path, platform):
    data = json.loads(Path(path).read_text())
    if data["producer_repository"] not in {"leanprover/lean4", "ejgallego/lean4"}:
        raise ValueError("unsupported source repository")
    if data["platform"] != platform or platform not in PLATFORMS:
        raise ValueError("candidate does not match selected platform")
    for field in ["lean_sha", "beam_sha"]:
        if not re.fullmatch(r"[0-9a-f]{40}", data[field]):
            raise ValueError(f"invalid candidate {field}")
    for field in ["producer_run", "artifact_id"]:
        if type(data[field]) is not int or data[field] <= 0:
            raise ValueError(f"invalid candidate {field}")
    for field in ["inner_archive_sha256", "wrapper_sha256", "producer_manifest_sha256"]:
        if data.get(field) is not None and not re.fullmatch(r"[0-9a-f]{64}", data[field]):
            raise ValueError(f"invalid candidate {field}")
    return data


def verify_download(record, identity):
    check_artifact(identity["artifact_metadata"], record["artifact_id"], record["producer_run"], record["lean_sha"])
    for field in ["inner_archive_filename", "inner_archive_sha256", "wrapper_sha256"]:
        if record.get(field) and record[field] != identity[field]:
            raise ValueError(f"candidate download mismatch: {field}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["fetch", "validate"])
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--download", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tier", choices=["pilot", "functional"], default="pilot")
    args = parser.parse_args()
    record = load_candidate(args.candidate, args.platform)
    scripts = Path(__file__).resolve().parent
    if args.phase == "fetch":
        command = [sys.executable, scripts / "fetch.py", "--repository", record["producer_repository"],
            "--run", str(record["producer_run"]), "--artifact", str(record["artifact_id"]),
            "--lean-sha", record["lean_sha"], "--output", args.download]
        if record.get("inner_archive_filename"):
            command += ["--archive-name", record["inner_archive_filename"]]
        subprocess.run(command, check=True)
    identity = json.loads((args.download / "identity.json").read_text())
    verify_download(record, identity)
    if args.phase == "fetch":
        return
    if args.output is None:
        parser.error("validate requires --output")
    name = identity["inner_archive_filename"]
    if Path(name).name != name:
        raise ValueError("unsafe archive filename")
    archive = args.download / name
    if sha256(archive) != identity["inner_archive_sha256"]:
        raise ValueError("archive changed after download")
    script, architecture = PLATFORMS[args.platform]
    command = [sys.executable, scripts / script, "--archive", archive,
        "--lean-sha", record["lean_sha"], "--beam-sha", record["beam_sha"],
        "--output", args.output, "--expected-architecture", architecture, "--tier", args.tier]
    for option, field in [("--producer-manifest-sha256", "producer_manifest_sha256"), ("--build-id", "build_id")]:
        if record.get(field):
            command += [option, record[field]]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
