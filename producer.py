# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Wait for one explicitly selected producer, then freeze its native artifacts."""

import argparse
import json
from pathlib import Path
import re
import time

from fetch import check_artifact, get_json
from validate import write_json


NATIVE = [
    ("build-Linux release", "linux-x86_64", "ubuntu-24.04"),
    ("build-Linux aarch64", "linux-aarch64", "ubuntu-24.04-arm"),
    ("build-macOS", "macos-x86_64", "macos-15-intel"),
    ("build-macOS aarch64", "macos-aarch64", "macos-15"),
]


def select_candidates(run, artifacts, repository, run_id, lean_sha, beam_sha):
    if run["id"] != run_id or run["head_sha"] != lean_sha:
        raise ValueError("producer does not match selected run and Lean revision")
    if run["status"] != "completed":
        return None
    if run["conclusion"] != "success":
        raise ValueError(f"producer did not succeed: {run['conclusion']}")
    rows = []
    for name, platform, runner in NATIVE:
        matching = [artifact for artifact in artifacts if artifact["name"] == name]
        if len(matching) != 1:
            raise ValueError(f"expected one artifact named {name}")
        artifact, = matching
        check_artifact(artifact, artifact["id"], run_id, lean_sha)
        digest = artifact.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"missing or invalid wrapper digest: {name}")
        rows.append({"platform": platform, "runner": runner, "candidate": {
            "producer_repository": repository, "producer_run": run_id,
            "artifact_id": artifact["id"], "platform": platform,
            "lean_sha": lean_sha, "beam_sha": beam_sha,
            "wrapper_sha256": digest.removeprefix("sha256:"),
        }})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", choices=["leanprover/lean4", "ejgallego/lean4"], required=True)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--lean-sha", required=True)
    parser.add_argument("--beam-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.run <= 0 or any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in [args.lean_sha, args.beam_sha]):
        parser.error("select a positive run ID and full source revisions")
    deadline = time.monotonic() + 10800
    base = f"repos/{args.repository}/actions/runs/{args.run}"
    while time.monotonic() < deadline:
        run = get_json(base)
        if run["status"] != "completed":
            select_candidates(run, [], args.repository, args.run, args.lean_sha, args.beam_sha)
        if run["status"] == "completed":
            response = get_json(f"{base}/artifacts?per_page=100")
            if response["total_count"] != len(response["artifacts"]):
                raise ValueError("artifact list is truncated; select frozen candidates explicitly")
            rows = select_candidates(run, response["artifacts"], args.repository, args.run, args.lean_sha, args.beam_sha)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, {"include": rows})
            write_json(args.output.with_name("producer-run.json"), run)
            print(json.dumps({"include": rows}, separators=(",", ":")))
            return
        print(f"Waiting for explicit producer {args.run}: {run['status']}", flush=True)
        time.sleep(60)
    raise SystemExit("BLOCKED: selected producer did not finish within three hours")


if __name__ == "__main__":
    main()
