# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Read an existing artifact; all GitHub API requests use GET."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

from validate import sha256, write_json


def check_artifact(artifact, artifact_id, run_id, lean_sha):
    if (artifact["id"] != artifact_id or artifact["expired"] or
            artifact["workflow_run"]["id"] != run_id or
            artifact["workflow_run"]["head_sha"] != lean_sha):
        raise ValueError("artifact does not match selected run/candidate or has expired")


def extract_tar(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as stream:
        stream.extractall(destination, filter="data")


def unpack(archive, destination):
    if archive.name.endswith(".tar.zst"):
        destination.mkdir(parents=True, exist_ok=True)
        with subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE) as process:
            with tarfile.open(fileobj=process.stdout, mode="r|") as stream:
                stream.extractall(destination, filter="data")
            if process.wait() != 0:
                raise ValueError("zstd decompression failed")
    else:
        extract_tar(archive, destination)
    roots = list(destination.iterdir())
    if len(roots) != 1 or not (roots[0] / "lib/lean-beam/provenance.json").is_file():
        raise ValueError("expected one complete SDK root in archive")
    return roots[0]


def get_json(route):
    return json.loads(subprocess.check_output(["gh", "api", "--method", "GET", route], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="leanprover/lean4", choices=["leanprover/lean4", "ejgallego/lean4"])
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--artifact", type=int, required=True)
    parser.add_argument("--lean-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = f"repos/{args.repository}/actions"
    record = get_json(f"{base}/artifacts/{args.artifact}")
    check_artifact(record, args.artifact, args.run, args.lean_sha)
    run = get_json(f"{base}/runs/{args.run}")
    wrapper = args.output / "github-artifact.zip"
    with wrapper.open("wb") as stream:
        subprocess.run(["gh", "api", "--method", "GET", f"{base}/artifacts/{args.artifact}/zip"], stdout=stream, check=True)
    wrapper_digest = sha256(wrapper)
    expected_digest = record.get("digest")
    if not expected_digest or expected_digest != f"sha256:{wrapper_digest}":
        raise ValueError("GitHub artifact wrapper digest mismatch or absent")
    with zipfile.ZipFile(wrapper) as stream:
        members = [member for member in stream.infolist() if member.filename.endswith(".tar.zst")]
        if len(members) != 1:
            raise ValueError("expected exactly one tar.zst inside GitHub artifact")
        member = members[0]
        archive = args.output / Path(member.filename).name
        with stream.open(member) as source, archive.open("wb") as destination:
            shutil.copyfileobj(source, destination)
    write_json(args.output / "identity.json", {
        "repository": args.repository, "run_id": args.run, "artifact_id": args.artifact,
        "run_attempt_observed": run["run_attempt"],
        "attempt_note": "API reports current run attempt, not a separately attested artifact-producing attempt.",
        "artifact_metadata": record, "wrapper_sha256": wrapper_digest,
        "inner_archive_filename": archive.name, "inner_archive_sha256": sha256(archive),
        "inner_archive_bytes": archive.stat().st_size,
    })
    with (args.output / "archive-path.txt").open("w") as stream:
        stream.write(str(archive.resolve()) + "\n")
    print(archive)


if __name__ == "__main__":
    main()
