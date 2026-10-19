<!-- Copyright (c) 2026 Lean FRO, LLC. Released under Apache 2.0. -->
# lean-beam artifact validation

Independent validation of Lean SDK archives containing Beam. All workflows, logs, and results live in `ejgallego/lean-beam-validation`.

## Upstream activity

GitHub workflows are manual only. There are no push, pull request, schedule, or upstream completion triggers. The artifact reader makes GET requests only. It neither dispatches upstream CI nor posts comments, check runs, commit statuses, issues, labels, or releases. Workflows have read permissions. Any future write to an official `leanprover` repository requires explicit user approval.

The initial source choices are `leanprover/lean4` and `ejgallego/lean4`. Reading an existing upstream artifact does not alter its producer run or the upstream PR. Evidence uploads belong to this validation repository.

## Current coverage

The [acceptance plan](docs/acceptance-plan.json) describes all 17 planned cases and five native platforms. The working implementation is an **initial Linux pilot**, not release qualification:

* Check schema 3, expected Lean/Beam revisions, component roles, exactly five hashed payload members plus provenance, optional externally supplied provenance digest/build ID, and absence of external SDK links.
* Extract a complete SDK into a path containing spaces and Unicode. Run in a separate PID/network namespace with a clean environment and read-only SDK. Assert producer source/build trees are hidden or absent.
* Check frozen executable identity, ELF dependencies for CLI/MCP/daemon/plugin, and Lean/Beam library paths observed in a live plugin-backed session.
* Run direct CLI serve/update/probe/stop and MCP self-check. Verify unchanged payload hashes and absence of standalone runtime caches.

Full P01–P17 coverage remains pending. In particular, independent MCP protocol tests, save/replay, selector fixtures, mutation/generation checks, forced lifecycle scenarios, full platform coverage, producer-side provenance attestation, and publication digest reconciliation are not implemented. Every pilot summary sets `release_qualified: false`.

## Run locally

Linux requires Python 3.12+, `zstd`, and working unprivileged `bubblewrap`. GitHub artifact download also requires `gh` and credentials with permission to read the selected artifact. No Lean/Beam source build is needed.

```sh
python3 -m compileall -q fetch.py run_linux.py validate.py tests
python3 -m unittest discover -s tests -v

python3 fetch.py --repository leanprover/lean4 \
  --run RUN_ID --artifact ARTIFACT_ID --lean-sha LEAN_SHA \
  --output downloads/candidate

python3 run_linux.py --archive downloads/candidate/ARCHIVE.tar.zst \
  --lean-sha LEAN_SHA --beam-sha BEAM_SHA \
  --output evidence/candidate
```

`fetch.py` verifies the selected artifact's run and Lean SHA, checks the GitHub wrapper digest, and records the inner archive SHA separately. Choose an artifact ID explicitly; there is no “latest” selection. The API exposes the run's current attempt, which is recorded as an observation rather than claimed as a separately attested artifact-producing attempt. Only `tar.zst` consumption is implemented.

Pass `--hide-path /path/to/producer/checkout` for a local archive. Scratch must be outside that tree; use `--scratch-parent` if `/tmp` lacks space. Existing developer build directories are never renamed, modified, or removed. Temporary consumer extraction and fixtures are removed after evidence is copied out. Budget space for the downloaded wrapper, inner archive, and complete SDK extraction.

For a candidate with independent producer evidence, also pass `--producer-manifest-sha256 DIGEST` and `--build-id ID`. Deriving an “expected” digest from the extracted archive itself does not establish producer-to-consumer identity. Older archives without a producer-side record cannot satisfy that part of P01.

## Run in this repository

The **Validate artifact** workflow offers `self-test` and `linux-pilot`. It must be dispatched explicitly on this repository. For a pilot, supply producer repository, run ID, artifact ID, and exact Lean/Beam SHAs. Optional inputs accept the producer provenance digest and build ID.

Cross-repository Actions artifacts need an appropriate read credential: configure `ARTIFACT_READ_TOKEN` as a repository secret with read access to the chosen producer's Actions artifacts. The workflow uses it only in the download step. The pilot runs with a cleared environment and receives no token. If unavailable or expired, download fails; the workflow never triggers a replacement upstream build.

The workflow checks its own repository identity, uses read permissions, and uploads evidence with `always()` so failures retain logs. Missing isolation support fails instead of silently switching to a weaker execution environment. No hosted runs are triggered by pushing this repository.

## Evidence and next work

`downloads/candidate/identity.json` records the immutable artifact and archive identity. `evidence/` holds input identity, inventory, isolation settings, command transcripts, live module paths, owner cleanup, and a pilot summary. The workflow copies download identity into the uploaded evidence.

Next: run the exact hosted Linux artifact, adapt the remaining planned cases, then implement native macOS/Windows/ARM consumers. Keep each result attached to its archive digest and harness revision; qualify a new archive after any binary change. Upstream changes and upstream CI remain under the user's separate approval policy.
