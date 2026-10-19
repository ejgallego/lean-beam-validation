<!-- Copyright (c) 2026 Lean FRO, LLC. Released under Apache 2.0. -->
# lean-beam artifact validation

Independent validation of Lean SDK archives containing Beam. All workflows, logs, and results live in `ejgallego/lean-beam-validation`.

## Upstream activity

GitHub workflows are manual only. There are no push, pull request, schedule, or upstream completion triggers. The artifact reader makes GET requests only. It neither dispatches upstream CI nor posts comments, check runs, commit statuses, issues, labels, or releases. Workflows have read permissions. Any future write to an official `leanprover` repository requires explicit user approval.

The initial source choices are `leanprover/lean4` and `ejgallego/lean4`. Reading an existing upstream artifact does not alter its producer run or the upstream PR. Evidence uploads belong to this validation repository.

## Current coverage

The [acceptance plan](docs/acceptance-plan.json) describes all 17 planned cases and five native platforms. Two Linux suites are implemented. Both report `release_qualified: false`.

The **pilot** checks:

* Check schema 3, expected Lean/Beam revisions, component roles, exactly five hashed payload members plus provenance, optional externally supplied provenance digest/build ID, and absence of external SDK links.
* Extract a complete SDK into a path containing spaces and Unicode. Run in a separate PID/network namespace with a clean environment and read-only SDK. Assert producer source/build trees are hidden or absent.
* Check frozen executable identity, ELF dependencies for CLI/MCP/daemon/plugin, and Lean/Beam library paths observed in a live plugin-backed session.
* Run direct CLI serve/update/probe/stop and MCP self-check. Verify unchanged payload hashes and absence of standalone runtime caches.

The **functional** suite adds:

* P04/P05: LF and CRLF source edits, a returned document version, stale-version rejection, typed probe errors, source diagnostics, and recovery to a valid proof.
* P06/P07: configured Lake elaboration with a dependency, save/replay, an ordinary import and native executable observing the saved value; deferred compilation rejection preserves existing artifacts when supported.
* P08/P09: an independent JSON-lines MCP client covering legacy initialization and modern discovery, tool listing, goals/proof probes, edits, stale versions, two interleaved workspaces, handle isolation, failed readmission, close/drop, and EOF.
* P10/P11: genuine local elan links, canonical aliases, an independent SDK with the same compiler SHA, conflicting inherited overrides, unavailable/floating/empty/malformed/dangling selectors. Elan is replaced by an invocation trap before behavior checks.
* P12/P13: missing and corrupted inventory members; missing daemon/plugin/shared libraries; live generation replacement; cleanup with malformed provenance. All mutations use a separate SDK copy without shared file inodes.
* P14/P15: five graceful cycles, forced owner loss, refusal of wrong-generation recovery, exact-generation recovery retaining evidence, safe restart, helper cancellation/EOF/client loss/deadline/abrupt exit, and descendant termination. Helper fixtures block on explicit release markers under external watchdogs.

Full release qualification remains blocked on independent producer attestation in P01, the native platform/format matrix in P16, and final publication digest reconciliation in P17. The current Linux implementation does not establish Windows job ownership, macOS loader compatibility, format parity, or the minimum supported OS baseline. Those remain separate qualification work.

## Run locally

Linux requires Python 3.12+, `zstd`, working unprivileged `bubblewrap`, and `elan` for functional selector setup. GitHub artifact download also requires `gh` and credentials with permission to read the selected artifact. No Lean/Beam source build is needed.

```sh
python3 -m compileall -q fetch.py run_linux.py validate.py functional.py mcp_client.py tests
python3 -m unittest discover -s tests -v

python3 fetch.py --repository leanprover/lean4 \
  --run RUN_ID --artifact ARTIFACT_ID --lean-sha LEAN_SHA \
  --output downloads/candidate

python3 run_linux.py --archive downloads/candidate/ARCHIVE.tar.zst \
  --lean-sha LEAN_SHA --beam-sha BEAM_SHA \
  --output evidence/candidate
```

Add `--tier functional` for the expanded suite. Allow at least 8 GiB of free scratch space for two complete SDK copies and project compilation. If necessary, pass `--scratch-parent /a/roomy/owned/directory` outside the producer checkout being hidden. The functional suite compiles only its small consumer fixtures.

`fetch.py` verifies the selected artifact's run and Lean SHA, checks the GitHub wrapper digest, and records the inner archive SHA separately. Choose an artifact ID explicitly; there is no “latest” selection. The API exposes the run's current attempt, which is recorded as an observation rather than claimed as a separately attested artifact-producing attempt. Only `tar.zst` consumption is implemented.

Pass `--hide-path /path/to/producer/checkout` for a local archive. Scratch must be outside that tree; use `--scratch-parent` if `/tmp` lacks space. Existing developer build directories are never renamed, modified, or removed. Temporary consumer extraction and fixtures are removed after evidence is copied out. Budget space for the downloaded wrapper, inner archive, and complete SDK extraction.

For a candidate with independent producer evidence, also pass `--producer-manifest-sha256 DIGEST` and `--build-id ID`. Deriving an “expected” digest from the extracted archive itself does not establish producer-to-consumer identity. Older archives without a producer-side record cannot satisfy that part of P01.

## Run in this repository

The **Validate artifact** workflow offers `self-test`, `linux-pilot`, and `linux-functional`. It must be dispatched explicitly on this repository. Supply producer repository, run ID, artifact ID, and exact Lean/Beam SHAs. Optional inputs accept the producer provenance digest and build ID.

The default workflow token successfully reads the current public upstream artifact. If a future source requires another credential, `ARTIFACT_READ_TOKEN` can supply artifact read access. Credentials are used only in the download step. Runtime checks use a cleared environment and receive no token. If access is unavailable or the artifact has expired, download fails; the workflow never triggers a replacement upstream build.

The workflow checks its own repository identity, uses read permissions, and uploads evidence with `always()` so failures retain logs. Missing isolation support fails instead of silently switching to a weaker execution environment. No hosted runs are triggered by pushing this repository.

## Evidence and next work

`downloads/candidate/identity.json` records the immutable artifact and archive identity. `evidence/` holds input identity, inventory, isolation settings, command transcripts, live module paths, owner cleanup, and a pilot summary. The workflow copies download identity into the uploaded evidence.

The initial hosted Linux pilot passed in [run 39271577415](https://github.com/ejgallego/lean-beam-validation/actions/runs/39271577415). The exact candidate is recorded in [candidates/c632-linux-x86_64.json](candidates/c632-linux-x86_64.json). That pilot is separate from the expanded functional suite and full release qualification.

Next: establish hosted functional results, implement native macOS/Windows/ARM consumers, and reconcile producer/publication evidence. Keep each result attached to its archive digest and harness revision; qualify a new archive after any binary change. Upstream changes and upstream CI remain under the user's separate approval policy.
