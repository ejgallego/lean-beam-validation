<!-- Copyright (c) 2026 Lean FRO, LLC. Released under Apache 2.0. -->
# lean-beam artifact validation

Independent validation of Lean SDK archives containing Beam. All workflows, logs, and results live in `ejgallego/lean-beam-validation`.

## Upstream activity

GitHub workflows are manual only. There are no push, pull request, schedule, or upstream completion triggers. The artifact reader makes GET requests only. It neither dispatches upstream CI nor posts comments, check runs, commit statuses, issues, labels, or releases. Workflows have read permissions. Any future write to an official `leanprover` repository requires explicit user approval.

The initial source choices are `leanprover/lean4` and `ejgallego/lean4`. Reading an existing upstream artifact does not alter its producer run or the upstream PR. Evidence uploads belong to this validation repository.

## Current coverage

The [acceptance plan](docs/acceptance-plan.json) describes all 17 planned cases and five native platforms. Pilot and functional suites are implemented for Linux x86_64 and both macOS architectures. All report `release_qualified: false`.

The **pilot** checks:

* Check schema 3, expected Lean/Beam revisions, component roles, exactly five hashed payload members plus provenance, optional externally supplied provenance digest/build ID, and absence of external SDK links.
* Extract a complete SDK into a path containing spaces and Unicode. Use a clean environment and read-only SDK, with producer source/build trees hidden or absent. Linux uses separate PID/network namespaces; the native macOS sandbox is described below.
* Check frozen executable identity, ELF or Mach-O dependencies for CLI/MCP/daemon/plugin, and Lean/Beam library paths observed in a live plugin-backed session.
* Run direct CLI serve/update/probe/stop and independent MCP discovery, initialization, tool listing, sync, and EOF checks for both supported protocol revisions. Verify unchanged payload hashes and absence of standalone runtime caches.

The **functional** suite adds:

* P04/P05: LF and CRLF source edits, a returned document version, stale-version rejection, typed probe errors, source diagnostics, and recovery to a valid proof.
* P06/P07: configured Lake elaboration with a dependency, save/replay, an ordinary import and native executable observing the saved value; deferred compilation rejection preserves existing artifacts when supported.
* P08/P09: an independent JSON-lines MCP client covering legacy initialization and modern discovery, tool listing, goals/proof probes, edits, stale versions, two interleaved workspaces, handle isolation, failed readmission, close/drop, and EOF.
* P10/P11: genuine local elan links, canonical aliases, an independent SDK with the same compiler SHA, conflicting inherited overrides, unavailable/floating/empty/malformed/dangling selectors. Elan is replaced by an invocation trap before behavior checks.
* P12/P13: missing and corrupted inventory members; missing daemon/plugin/shared libraries; live generation replacement; cleanup with malformed provenance. All mutations use a separate SDK copy without shared file inodes.
* P14/P15: five graceful cycles, forced owner loss, refusal of wrong-generation recovery, exact-generation recovery retaining evidence, safe restart, helper cancellation/EOF/client loss/slow success/abrupt exit, and descendant termination. Helper fixtures block on explicit release markers under external watchdogs. Slow setup must remain pending beyond the former 30-second startup deadline, then complete after release and terminate its descendants.

Full release qualification remains blocked on independent producer attestation in P01, the native platform/format matrix in P16, and final publication digest reconciliation in P17. A pass on one native platform does not establish another platform's process ownership, loader compatibility, format parity, or the minimum supported OS baseline.

## macOS consumers

The separate **Validate macOS artifacts** workflow consumes explicit candidate records on native `macos-15` ARM and `macos-15-intel` runners. It supports `pilot` and `functional` tiers and remains manual only. Its initial candidate set is `c632`, with separate artifact IDs and wrapper digests per architecture.

`run_macos.py` extracts a complete SDK with spaces and Unicode in its path, prepares an isolated profile, and executes the consumer under Seatbelt. A preflight probe must demonstrate loopback TCP, denial of external TCP, denial of SDK writes, and allowed fixture writes. External denial must be a permission error, not a timeout or an unavailable remote host. Wildcard bind permission is recorded separately: Seatbelt's `localhost` filter permits host-local addresses and does not guarantee loopback-only binds. The harness checks Beam's actual TCP listeners with `lsof` and rejects wildcard or non-loopback endpoints. There is no unsandboxed fallback; macOS does not provide the Linux suite's separate network namespace.

`macos.py` checks native architecture with `lipo`, declared dependencies/load commands with `otool`, live plugin/library paths with `vmmap`, and owned TCP listeners with `lsof`. The functional tier reuses the Linux scenarios with macOS process observations and `.dylib` dependencies. Canonical paths account for `/var` versus `/private/var`; functional selectors also exercise a second alias of the SDK. The initial Intel and ARM runs failed in the harness preflight because it incorrectly required wildcard binds to be denied; Beam had not yet run. The [failure report](reports/c632-macos-sandbox-preflight-failure.json) retains the archive identities and diagnosis. Both native functional suites now pass in [run 39287560390](https://github.com/ejgallego/lean-beam-validation/actions/runs/39287560390); full release qualification remains blocked.

The dylib's own install name (`otool -D`, `LC_ID_DYLIB`) is retained separately from its loaded dependencies. An absolute producer install name alone does not establish a failed dependency lookup; only the matching first entry is excluded from `otool -L` dependency validation. External loaded dependencies still fail. Process ownership observations use Apple's `libproc` API for bounded child-PID lists and process state, avoiding Seatbelt's refusal to execute `/bin/ps`. Permission failures and incomplete observations fail the case. The ABI and zombie-state handling follow Apple's [process structures](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h) and [kernel implementation](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/proc_info.c).

The self-tests include a native Seatbelt preflight against a tiny SDK fixture, before any archive download. It checks actual networking and file-write decisions, plus observation and reaping of an owned child, with the same profile and probe used by the artifact consumer. This test is skipped on Linux; static profile checks do not establish macOS sandbox behavior.

The network profile uses the host-local address syntax in [Codex's Seatbelt implementation](https://github.com/openai/codex/blob/main/codex-rs/sandboxing/src/seatbelt.rs); [SwiftPM's sandbox implementation](https://github.com/swiftlang/swift-package-manager/blob/main/Sources/Basics/Sandbox.swift) also documents independent network and read-only directory rules. [MXC's Seatbelt backend documentation](https://github.com/microsoft/mxc/blob/main/docs/seatbelt/seatbelt-backend.md) describes the host-local filtering limitation. Actual probe results, including partial results on failure, are retained with each consumer run.

## Run locally

Linux requires Python 3.12+, `zstd`, working unprivileged `bubblewrap`, and `elan` for functional selector setup. GitHub artifact download also requires `gh` and credentials with permission to read the selected artifact. No Lean/Beam source build is needed.

```sh
python3 -m compileall -q ./*.py tests
python3 -m unittest discover -s tests -v

python3 fetch.py --repository leanprover/lean4 \
  --run RUN_ID --artifact ARTIFACT_ID --lean-sha LEAN_SHA \
  --output downloads/candidate

python3 run_linux.py --archive downloads/candidate/ARCHIVE.tar.zst \
  --lean-sha LEAN_SHA --beam-sha BEAM_SHA \
  --output evidence/candidate
```

Add `--tier functional` for the expanded suite. Allow at least 8 GiB of free scratch space for two complete SDK copies and project compilation. If necessary, pass `--scratch-parent /a/roomy/owned/directory` outside the producer checkout being hidden. The functional suite compiles only its small consumer fixtures.

`fetch.py` verifies the selected artifact's run and Lean SHA, checks the GitHub wrapper digest, and records the inner archive SHA separately. Choose an artifact ID explicitly; there is no “latest” selection. The API exposes the run's current attempt, which is recorded as an observation rather than claimed as a separately attested artifact-producing attempt. The consumer accepts publisher `tar.zst` and ZIP packages. If a GitHub artifact contains both, pass `--archive-name` to select the exact inner member; each format has its own digest and result. ZIP extraction preserves executable modes and rejects link or special-file entries. ZIP support is covered by harness fixtures; publisher ZIP packages still require native qualification.

Pass `--hide-path /path/to/producer/checkout` for a local archive. Scratch must be outside that tree; use `--scratch-parent` if `/tmp` lacks space. Existing developer build directories are never renamed, modified, or removed. Temporary consumer extraction and fixtures are removed after evidence is copied out. Budget space for the downloaded wrapper, inner archive, and complete SDK extraction.

For a candidate with independent producer evidence, also pass `--producer-manifest-sha256 DIGEST` and `--build-id ID`. Deriving an “expected” digest from the extracted archive itself does not establish producer-to-consumer identity. Older archives without a producer-side record cannot satisfy that part of P01.

## Run in this repository

The **Validate existing producer run** workflow can be started while an explicitly selected Lean CI run is still building. It waits up to three hours for that run to succeed, freezes the four named Linux/macOS artifact IDs and wrapper digests, and starts native functional consumers. It checks the exact producer run and source revision throughout and never searches for a newer run or triggers an upstream build. Frozen selections and consumer results are retained as artifacts in this repository. Each producer artifact must contain exactly one package; for coexisting formats or independently attested identities, use explicit records with the frozen native workflow instead. Results from a PR producer remain PR evidence; published packages need their own identity and byte reconciliation.

The **Validate frozen native artifact** workflow consumes one committed candidate record on an explicitly selected Linux x86_64, Linux aarch64, macOS Intel, or macOS ARM runner. It defaults to the functional suite and checks the candidate's platform before selecting its consumer. Linux also checks the runner architecture and the ELF architecture of Lean, all three Beam executables, and the plugin before execution. This workflow is manual only and does not create upstream builds.

Freeze a record under `candidates/` with `producer_repository`, `producer_run`, `artifact_id`, full `lean_sha` and `beam_sha`, and `platform`. Include the exact `inner_archive_filename`, `inner_archive_sha256`, and `wrapper_sha256` when the archive has been selected; include independently obtained `producer_manifest_sha256` and `build_id` when available. Download observations alone do not establish independent producer evidence. Each archive format needs its own record and result. PR, merge-queue, and published packages remain separate candidates unless their actual bytes and identities match.

The same entry point works locally:

```sh
python3 candidate.py fetch --candidate candidates/FROZEN.json --platform linux-x86_64 --download downloads/candidate
python3 candidate.py validate --candidate candidates/FROZEN.json --platform linux-x86_64 --download downloads/candidate --output evidence/candidate --tier functional
```

The **Validate artifact** workflow offers `self-test`, `linux-pilot`, and `linux-functional`. It must be dispatched explicitly on this repository. Supply producer repository, run ID, artifact ID, and exact Lean/Beam SHAs. Optional inputs accept the producer provenance digest and build ID.

The default workflow token successfully reads the current public upstream artifact. If a future source requires another credential, `ARTIFACT_READ_TOKEN` can supply artifact read access. Credentials are used only in the download step. Runtime checks use a cleared environment and receive no token. If access is unavailable or the artifact has expired, download fails; the workflow never triggers a replacement upstream build.

The workflow checks its own repository identity, uses read permissions, and uploads evidence with `always()` so failures retain logs. Missing isolation support fails instead of silently switching to a weaker execution environment. No hosted runs are triggered by pushing this repository.

## Evidence and next work

`downloads/candidate/identity.json` records the immutable artifact and archive identity. `evidence/` holds input identity, inventory, isolation settings, command transcripts, live module paths, owner cleanup, and a pilot summary. The workflow copies download identity into the uploaded evidence.

The initial hosted Linux pilot passed in [run 39271577415](https://github.com/ejgallego/lean-beam-validation/actions/runs/39271577415). The exact candidate is recorded in [candidates/c632-linux-x86_64.json](candidates/c632-linux-x86_64.json). That pilot is separate from the expanded functional suite and full release qualification.

The hosted functional suite also passed in [run 39274451011](https://github.com/ejgallego/lean-beam-validation/actions/runs/39274451011): 18 harness tests, 16 functional check groups, and ten helper lifecycle scenarios. The [candidate report](reports/c632-linux-x86_64.json) preserves the tested harness revision, archive digest, observed generation, per-file sizes, and individual outcomes. The consumer-observed provenance digest remains separate from the missing independent producer attestation.

The shared macOS adapter changes also passed a complete local Linux regression at harness revision `3f9706952e5f46e9fa6917c23f320c585dc98c68`: all 16 groups and ten helper lifecycle scenarios passed, with unchanged payload hashes. The [local regression report](reports/c632-linux-x86_64-macos-adapter-regression.json) identifies the exact source files and archive. This is local evidence; it does not establish macOS behavior. Native ARM and Intel functional validation was dispatched in [run 39281809704](https://github.com/ejgallego/lean-beam-validation/actions/runs/39281809704).

Native macOS ARM and Intel both passed in [run 39287560390](https://github.com/ejgallego/lean-beam-validation/actions/runs/39287560390): 30 harness tests, all 16 functional groups, five graceful shutdown cycles plus forced owner loss/recovery, and ten helper lifecycle scenarios on each architecture. Live plugin and Lean/Lake libraries resolved inside the SDK; the observed Beam listener bound to `127.0.0.1`. External TCP and SDK writes were denied. The [ARM report](reports/c632-macos-aarch64.json) and [Intel report](reports/c632-macos-x86_64.json) preserve archive identities, consumer provenance digests, per-file sizes and individual outcomes. Producer attestation remains absent, and these macOS 15 runs do not establish the minimum supported OS baseline.

Linux ARM now has a native consumer alongside Linux x86_64 and both macOS architectures. Current qualification still needs successful consumers for the selected archives, OS baselines/formats, and producer/publication reconciliation. Windows Beam bundling remains deferred and is outside the Linux/macOS release gate. Keep each result attached to its archive digest and harness revision; qualify a new archive after any binary change. Upstream changes and upstream CI remain under the user's separate approval policy.
