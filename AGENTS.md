<!-- Copyright (c) 2026 Lean FRO, LLC. Released under Apache 2.0. -->
# Scope

This repository exists to keep experimental validation activity in `ejgallego` and out of official `leanprover` repositories.

* Never write to `leanprover` repositories, dispatch or rerun their workflows, or publish upstream comments/status/checks without explicit user approval for that action.
* Keep workflows manual (`workflow_dispatch`) and guarded to `ejgallego/lean-beam-validation`. Do not add push, pull request, schedule, or upstream-completion triggers without approval.
* Keep workflow permissions read-only. Source artifact access must use GET requests only. Upload results solely to this validation repository.
* Never rebuild Beam to test an archive. Invoke the extracted SDK directly, with no producer-path or standalone-runtime fallback.
* Do not rename, modify, or remove existing developer build directories. Disposable consumer fixtures may be cleaned up by their owner.
* Add meaningful regression tests before implementing harness features. Run `python3 -m compileall -q ./*.py tests`, `python3 -m unittest discover -s tests -v`, and actionlint after relevant edits.
* Report current implemented coverage separately from the plan. A pilot pass is not release qualification. Missing required evidence is blocked, never a pass.
* Tokens must not reach runtime tests or evidence. Record artifact IDs, inner digests, revisions, and harness revision for every hosted pilot.
