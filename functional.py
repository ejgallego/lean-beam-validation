# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Functional checks using only executables from the supplied SDK."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback

from mcp_client import McpClient
from processes import process_tree, running
from validate import Pilot, ValidationError, require, sha256, verify_inventory, write_json


CONFIG = '''name = "beam_artifact_acceptance"
[leanOptions]
maxRecDepth = 777
[[lean_lib]]
name = "Demo"
[[lean_exe]]
name = "nativeCheck"
root = "NativeCheck"
'''
SOURCE = '''module
public import Lean
public import Demo.Dep

/-! Exercise configured elaboration, dependency imports, and a proof checkpoint. -/
open Lean Elab Command
elab "#configured" : command => do
  unless maxRecDepth.get (← getOptions) == 777 do throwError "missing structured Lake option"
#configured

@[noinline] public def value : Nat := dependencyValue + 1

example : True := by
  trivial
'''


def fixture(root, *, crlf=False):
    root.mkdir()
    (root / "Demo").mkdir()
    (root / "lakefile.toml").write_text(CONFIG)
    (root / "Demo/Dep.lean").write_text("module\n/-! Dependency of the artifact fixture. -/\npublic abbrev dependencyValue : Nat := 40\n")
    (root / "Demo.lean").write_bytes(SOURCE.replace("\n", "\r\n" if crlf else "\n").encode())
    (root / "Check.lean").write_text("module\npublic meta import Demo\n/-! Check a saved module through an ordinary import. -/\n#eval value\n")
    (root / "NativeCheck.lean").write_text("module\nimport Demo\n/-! Check saved code through native compilation. -/\npublic def main : IO Unit := IO.println value\n")
    return root


def simple_fixture(root, value=42):
    root.mkdir()
    (root / "lakefile.toml").write_text('name = "beam_simple_consumer"\n')
    (root / "Main.lean").write_text(f"module\n/-! Independent MCP consumer fixture. -/\ndef answer : Nat := {value}\n\nexample : True := by\n  trivial\n")
    return root


def position(text, marker):
    lines = text.splitlines()
    matches = [(index, line.index(marker.lstrip())) for index, line in enumerate(lines) if line == marker]
    require(len(matches) == 1, f"expected one fixture marker: {marker}")
    return matches[0]


def writable_sdk_copy(prefix, destination):
    shutil.copytree(prefix, destination, symlinks=True)
    return destination


def wait_until(predicate, message, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        require(time.monotonic() < deadline, message)
        time.sleep(0.05)


def response_file(response, field, root):
    value = response.get("result", {}).get(field)
    require(response.get("ok") is True and isinstance(value, str), f"missing successful {field}: {response}")
    path = Path(value).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), f"missing or external {field}: {path}")
    return path


def helper_pids(root):
    result = []
    for name in ["helper.pid", "descendant.pid"]:
        try:
            text = (root / name).read_text()
        except FileNotFoundError:
            return None
        if not text.isdecimal() or int(text) <= 0:
            return None
        result.append(int(text))
    return result


def helper_fixture(root, *, abrupt=False):
    simple_fixture(root)
    (root / "lakefile.toml").unlink()
    descendant = root / "descendant.py"
    descendant.write_text("from pathlib import Path\nimport os,time\n" +
        f"pid_path = Path({str(root / 'descendant.pid')!r})\n" +
        "pending_pid = pid_path.with_suffix('.pid.tmp')\n" +
        "pending_pid.write_text(str(os.getpid()))\npending_pid.replace(pid_path)\n" +
        f"while not Path({str(root / 'release')!r}).exists(): time.sleep(0.01)\n")
    (root / "lakefile.lean").write_text("import Lake\n/-! Deterministic owned-helper lifecycle fixture. -/\nopen Lake DSL\nrun_cmd do\n" +
        f"  IO.FS.writeFile {json.dumps(str(root / 'helper.pid.tmp'), ensure_ascii=False)} (toString (← IO.Process.getPID))\n" +
        f"  IO.FS.rename {json.dumps(str(root / 'helper.pid.tmp'), ensure_ascii=False)} {json.dumps(str(root / 'helper.pid'), ensure_ascii=False)}\n" +
        "  discard <| IO.Process.spawn {\n" +
        f"    cmd := {json.dumps(sys.executable, ensure_ascii=False)}\n" +
        f"    args := #[{json.dumps(str(descendant), ensure_ascii=False)}]\n" +
        "    stdin := .null, stdout := .null, stderr := .null\n  }\n" +
        f"  while !(← (System.FilePath.mk {json.dumps(str(root / 'descendant.pid'), ensure_ascii=False)}).pathExists) do IO.sleep 10\n" +
        ("  (IO.Process.forceExit 23 : IO Unit)\n" if abrupt else
         f"  while !(← (System.FilePath.mk {json.dumps(str(root / 'release'), ensure_ascii=False)}).pathExists) do IO.sleep 10\n") +
        "package helper_lifetime_fixture\nlean_lib Main\n")
    return root


class NotApplicable(Exception):
    pass


class FunctionalSuite(Pilot):
    suite_name = "linux-functional"

    def capture(self, argv, *, cwd=None, env=None, timeout=120):
        self.counter += 1
        stem = self.output / f"command-{self.counter:03}"
        write_json(stem.with_suffix(".json"), {"argv": list(map(str, argv)), "cwd": str(cwd) if cwd else None})
        result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, timeout=timeout,
                                text=True, capture_output=True)
        stem.with_suffix(".stdout").write_text(result.stdout)
        stem.with_suffix(".stderr").write_text(result.stderr)
        write_json(stem.with_suffix(".exit.json"), {"returncode": result.returncode})
        return result

    def call(self, root, *args, ok=True, prefix=None, env=None):
        beam = (prefix / self.record["components"]["frontends"]["cli"]) if prefix else self.beam
        result = self.capture([beam, "--root", root, *args], cwd=root, env=env)
        require((result.returncode == 0) == ok,
                f"unexpected CLI exit {result.returncode}: {args}: {result.stdout} {result.stderr}")
        return result

    @contextmanager
    def owner(self, root, *, prefix=None, expected_exit=(0,)):
        beam = (prefix / self.record["components"]["frontends"]["cli"]) if prefix else self.beam
        self.counter += 1
        stem = self.output / f"owner-{self.counter:03}"
        log_path = stem.with_suffix(".log")
        with log_path.open("w") as log:
            process = subprocess.Popen([str(beam), "--root", str(root), "serve"], cwd=root,
                                       stdout=log, stderr=log, start_new_session=True)
            forced = False
            try:
                deadline = time.monotonic() + 120
                while "serving Beam session" not in log_path.read_text():
                    require(process.poll() is None, f"serve exited before ready: {log_path.read_text()}")
                    require(time.monotonic() < deadline, "serve startup timed out")
                    time.sleep(0.05)
                yield process
            finally:
                try:
                    if process.poll() is None:
                        self.capture([beam, "--root", root, "stop"], cwd=root, timeout=30)
                    process.wait(timeout=30)
                finally:
                    if process.poll() is None:
                        forced = True
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                    write_json(stem.with_suffix(".cleanup.json"), {"forced": forced, "returncode": process.returncode})
                require(not forced and process.returncode in expected_exit, "session owner did not stop cleanly")

    def fixture_at(self, name, *, crlf=False):
        root = fixture(Path.cwd() / name, crlf=crlf)
        self.run([self.prefix / "bin/lake", "--no-cache", "build", "Demo"], cwd=root, timeout=300)
        return root

    def cli_edit_and_recovery(self):
        for crlf in [False, True]:
            root = self.fixture_at(f"CLI-{'CRLF' if crlf else 'LF'}", crlf=crlf)
            source = root / "Demo.lean"
            line, character = position(source.read_text(), "  trivial")
            with self.owner(root):
                updated = json.loads(self.call(root, "update", "Demo.lean").stdout)["result"]
                version = updated["version"]
                valid = json.loads(self.call(root, "run-at", "Demo.lean", str(version), str(line), str(character), "trivial").stdout)
                require(valid["result"]["success"], f"proof probe failed: {valid}")
                self.call(root, "close", "Demo.lean")
                source.write_bytes(source.read_bytes().replace(b"dependencyValue + 1", b"dependencyValue + 2"))
                edited = json.loads(self.call(root, "update", "Demo.lean").stdout)["result"]
                stale = json.loads(self.call(root, "run-at", "Demo.lean", str(edited["version"] + 1),
                                             str(line), str(character), "trivial", ok=False).stdout)
                require(stale["error"]["code"] == "contentModified", f"wrong version failure: {stale}")
                command_line = str(position(source.read_text(), "example : True := by")[0])
                broken = json.loads(self.call(root, "run-at", "Demo.lean", str(edited["version"]), command_line, "0",
                                              'def broken : Nat := "bad"').stdout)
                require(broken["ok"] and broken["result"]["success"] is False, f"semantic failure confused with transport: {broken}")
                require(any(message.get("severity") == "error" and "Type mismatch" in message.get("text", "")
                            for message in broken["result"]["messages"]), "typed probe error was not reported")
                recovered = json.loads(self.call(root, "run-at", "Demo.lean", str(edited["version"]), command_line, "0", "example : value = 42 := rfl").stdout)
                require(recovered["result"]["success"],
                        f"edit or recovery not observed: {recovered}")
                good_source = source.read_bytes()
                self.call(root, "close", "Demo.lean")
                source.write_bytes(good_source.replace(b"dependencyValue + 2", b'"bad"'))
                diagnosed = json.loads(self.call(root, "sync", "Demo.lean").stdout)
                require(diagnosed["ok"] and diagnosed["result"]["diagnostics"]["counts"]["error"] > 0,
                        f"source error was confused with transport failure: {diagnosed}")
                self.call(root, "close", "Demo.lean")
                source.write_bytes(good_source)
                repaired = json.loads(self.call(root, "sync", "Demo.lean").stdout)
                require(repaired["result"]["diagnostics"]["counts"]["error"] == 0, "source recovery retained an error")

    def save_and_replay(self):
        root = self.fixture_at("save replay")
        source = root / "Demo.lean"
        with self.owner(root):
            self.call(root, "sync", "Demo.lean")
            self.call(root, "close", "Demo.lean")
            source.write_text(SOURCE.replace("dependencyValue + 1", "dependencyValue + 2"))
            self.call(root, "sync", "Demo.lean")
            saved = json.loads(self.call(root, "save", "Demo.lean").stdout)
            response_file(saved, "ir_sig", root)
        replay = self.capture([self.prefix / "bin/lake", "--no-cache", "-v", "build", "Demo"], cwd=root, timeout=300)
        require(replay.returncode == 0 and "Replayed Demo" in replay.stdout + replay.stderr
                and "Built Demo" not in replay.stdout + replay.stderr, "Lake did not replay saved output")
        imported = self.run([self.prefix / "bin/lake", "env", "lean", "Check.lean"], cwd=root)
        require(imported.strip() == "42", f"import did not observe save: {imported}")
        self.run([self.prefix / "bin/lake", "--no-cache", "build", "nativeCheck"], cwd=root, timeout=300)
        native = self.run([root / ".lake/build/bin/nativeCheck"], cwd=root)
        require(native.strip() == "42", f"native program did not observe save: {native}")

    def deferred_save(self):
        info = json.loads(self.run([self.beam, "toolchain-build-info"]))
        if not info["lakeSupportsDeferredCompilation"]:
            raise NotApplicable("selected Lake does not support deferred compilation")
        for mode in ["lake option", "source option"]:
            root = fixture(Path.cwd() / f"deferred {mode}")
            source = root / "Demo.lean"
            source.write_text("module\n/-! Deferred compilation checkpoint fixture. -/\npublic def value : Nat := 3\n")
            if mode == "lake option":
                (root / "lakefile.toml").write_text(CONFIG.replace("maxRecDepth = 777", "maxRecDepth = 777\ncompiler.postponeCompile = true"))
            self.run([self.prefix / "bin/lake", "--no-cache", "build", "Demo"], cwd=root, timeout=300)
            family = lambda: {str(p.relative_to(root)): sha256(p) for p in (root / ".lake/build").rglob("Demo.*") if p.is_file()}
            before = family()
            if mode == "source option":
                source.write_text("module\n/-! Deferred compilation checkpoint fixture. -/\nset_option compiler.postponeCompile true in\npublic def value : Nat := 4\n")
            with self.owner(root):
                self.call(root, "sync", "Demo.lean")
                saved = json.loads(self.call(root, "save", "Demo.lean", ok=False).stdout)
                error = saved["error"]
                require(error["code"] == "saveUnsupportedSetup" and error["data"]["sync"]["readiness"]["reason"] == "deferredCompilation",
                        f"wrong deferred save rejection: {saved}")
                require(family() == before, "unsupported save changed published artifacts")
            self.run([self.prefix / "bin/lake", "--no-cache", "build", "Demo"], cwd=root, timeout=300)

    def mcp_workflow(self):
        for modern in [False, True]:
            root = simple_fixture(Path.cwd() / f"MCP-{modern}")
            source = root / "Main.lean"
            line, character = position(source.read_text(), "  trivial")
            with McpClient([self.mcp], root, self.output / f"mcp-{modern}", modern=modern) as client:
                client.initialize()
                listed = client.request("tools/list")
                names = {tool["name"] for tool in listed["result"]["tools"]}
                require({"lean_sync", "lean_run_at", "lean_goals", "lean_drop_workspace"} <= names, "missing MCP tools")
                synced = client.tool("lean_sync", root, {"path": "Main.lean"})
                goals = client.tool("lean_goals", root, {"path": "Main.lean", "version": synced["version"],
                                                         "line": line, "character": character, "mode": "before"})
                require(goals["goals"] and goals["goals"][0]["target"] == "True", f"wrong goals: {goals}")
                probe = client.tool("lean_run_at", root, {"path": "Main.lean", "version": synced["version"],
                                                          "line": line, "character": character, "text": "trivial"})
                require(probe["success"], f"MCP proof failed: {probe}")
                old_version = synced["version"]
                source.write_text(source.read_text().replace(":= 42", ":= 43"))
                changed = client.tool("lean_sync", root, {"path": "Main.lean"})
                require(changed["version"] > old_version, "MCP edit not synchronized")
                stale = client.tool("lean_run_at", root, {"path": "Main.lean", "version": old_version,
                    "line": line, "character": character, "text": "trivial"}, allow_error=True)
                require("contentModified" in json.dumps(stale) and
                        ("error" in stale or stale.get("result", {}).get("isError") is True),
                        f"stale MCP version was admitted: {stale}")
                observed = client.tool("lean_run_at", root, {"path": "Main.lean", "version": changed["version"],
                                                             "line": position(source.read_text(), "example : True := by")[0], "character": 0,
                                                             "text": "example : answer = 43 := rfl"})
                require(observed["success"], f"MCP did not observe edited value: {observed}")
                client.tool("lean_close", root, {"path": "Main.lean"})
                dropped = client.tool("lean_drop_workspace", root)
                require(dropped["dropped"] and dropped["invalidated_handles"], f"MCP drop failed: {dropped}")

    def selectors(self):
        root = simple_fixture(Path.cwd() / "selector cases")
        selector = root / "lean-toolchain"
        foreign = self.args.foreign_prefix
        require(foreign and foreign.is_dir(), "missing independent foreign SDK fixture")
        require(self.run([foreign / "bin/lean", "--githash"]).strip() == self.args.lean_sha,
                "foreign fixture must have the same compiler revision")
        cases = [(None, None, True, "target membership: present"),
                 ("beam-matching", None, True, "target membership: present"),
                 ("beam-matching-second", None, True, "target membership: present"),
                 ("beam-alias", None, True, "target membership: present"),
                 ("beam-foreign", None, False, "not a member of this Beam installation"),
                 ("beam-matching", "beam-foreign", True, "target membership: present"),
                 ("beam-foreign", "beam-matching", False, "not a member of this Beam installation"),
                 ("beam-not-installed", None, False, "unavailable locally"),
                 ("leanprover/lean4:nightly", None, False, "requires remote resolution"),
                 ("beam-dangling", None, False, "toolchains/beam-dangling/bin/lean"),
                 ("", None, False, "unavailable locally"),
                 ("malformed:selector:extra", None, False, "unavailable locally")]
        outcomes = []
        for selected, inherited, ok, needle in cases:
            if selected is None:
                selector.unlink(missing_ok=True)
            else:
                selector.write_text(selected + "\n")
            env = dict(os.environ)
            if inherited:
                env["ELAN_TOOLCHAIN"] = inherited
            result = self.call(root, "doctor", ok=ok, env=env)
            combined = result.stdout + result.stderr
            require(not needle or needle in combined, f"wrong selector failure for {selected}: {combined}")
            if selected == "beam-dangling":
                error = json.loads(result.stdout)["error"]
                require(error["code"] == "setupError" and "no such file or directory" in error["message"],
                        f"dangling selector did not report the missing local compiler: {error}")
            outcomes.append({"selected": selected, "inherited": inherited, "returncode": result.returncode, "output": combined})
        write_json(self.output / "selector-outcomes.json", outcomes)
        require(not (Path(os.environ["HOME"]) / "elan-invocations").exists(), "runtime invoked elan")
        require(not (Path(os.environ["ELAN_HOME"]) / "toolchains/beam-not-installed").exists(), "missing toolchain was provisioned")

    def mcp_workspace_isolation(self):
        for modern in [False, True]:
            first = simple_fixture(Path.cwd() / f"workspace-A-{modern}", 41)
            second = simple_fixture(Path.cwd() / f"workspace-B-{modern}", 42)
            with McpClient([self.mcp], first, self.output / f"mcp-isolation-{modern}", modern=modern) as client:
                client.initialize()
                versions = {}
                for root, expected in [(first, "41"), (second, "42")]:
                    sync = client.tool("lean_sync", root, {"path": "Main.lean"})
                    versions[root] = sync["version"]
                    probe = client.tool("lean_run_at", root, {"path": "Main.lean", "version": sync["version"],
                        "line": position((root / "Main.lean").read_text(), "example : True := by")[0],
                        "character": 0, "text": f"example : answer = {expected} := rfl"})
                    require(probe["success"], f"workspaces were confused: {probe}")
                line = position((first / "Main.lean").read_text(), "example : True := by")[0]
                minted = client.tool("lean_run_at_handle", first, {"path": "Main.lean", "version": versions[first],
                    "line": line, "character": 0, "text": "def workspaceWitness : Nat := 7"})
                require(minted["success"] and isinstance(minted["next_handle"], dict), "missing continuation handle")
                handle = minted["next_handle"]
                foreign_handle = client.tool("lean_run_with", second, {"path": "Main.lean", "handle": handle,
                    "text": "#check workspaceWitness"}, allow_error=True)
                require("error" in foreign_handle or foreign_handle.get("result", {}).get("isError") is True,
                        f"cross-workspace handle admitted: {foreign_handle}")
                selector = first / "lean-toolchain"
                for selected, needle in [("beam-foreign", "not a member of this Beam installation"),
                                         ("beam-not-installed", "unavailable locally")]:
                    selector.write_text(selected + "\n")
                    denied = client.tool("lean_sync", first, {"path": "Main.lean"}, allow_error=True)
                    require(denied.get("error", {}).get("code") == -32600 and needle in denied["error"]["message"],
                            f"live selector was admitted: {denied}")
                    healthy = client.tool("lean_sync", second, {"path": "Main.lean"})
                    require(healthy["version"] == versions[second], "rejected A corrupted B")
                selector.unlink()
                resumed = client.tool("lean_sync", first, {"path": "Main.lean"})
                require(resumed["version"] == versions[first], "failed admission retargeted A")
                continued = client.tool("lean_run_with", first, {"path": "Main.lean", "handle": handle,
                    "text": "#check workspaceWitness"})
                require(continued["success"], "failed admission corrupted retained handle")
                for root in [first, second]:
                    client.tool("lean_drop_workspace", root)

    def damaged_payload(self):
        prefix = self.args.foreign_prefix
        outcomes = []
        for relative in self.before["files"]:
            path = prefix / relative
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"corrupt")
                try:
                    verify_inventory(prefix, self.args.lean_sha, self.args.beam_sha)
                except (ValidationError, json.JSONDecodeError):
                    outcomes.append({"file": relative, "mutation": "corrupt", "rejected": True})
                else:
                    raise ValidationError(f"corrupt payload admitted: {relative}")
            finally:
                path.write_bytes(original)
            parked = path.with_name(path.name + ".parked")
            path.rename(parked)
            try:
                try:
                    verify_inventory(prefix, self.args.lean_sha, self.args.beam_sha)
                except (ValidationError, FileNotFoundError):
                    outcomes.append({"file": relative, "mutation": "missing", "rejected": True})
                else:
                    raise ValidationError(f"missing payload admitted: {relative}")
            finally:
                parked.rename(path)
        root = simple_fixture(Path.cwd() / "missing runtime")
        for relative in [self.target["daemon"], self.target["plugin"]]:
            path = prefix / relative
            parked = path.with_name(path.name + ".parked")
            path.rename(parked)
            try:
                result = self.call(root, "serve", prefix=prefix, ok=False)
                require(path.name in result.stderr and "missing" in result.stderr, "wrong missing runtime failure")
            finally:
                parked.rename(path)
        extension = ".dylib" if sys.platform == "darwin" else ".so"
        for name in [f"libleanshared{extension}", f"libLake_shared{extension}"]:
            path = prefix / "lib/lean" / name
            parked = path.with_name(path.name + ".parked")
            path.rename(parked)
            try:
                for relative, args in [(self.record["components"]["frontends"]["cli"], ["--version"]),
                                       (self.record["components"]["frontends"]["mcp"], ["--version"]),
                                       (self.target["daemon"], ["build-info"])]:
                    result = self.capture([prefix / relative, *args], cwd=root)
                    require(result.returncode != 0 and name in result.stderr, f"missing SDK library fell back: {name}")
            finally:
                parked.rename(path)
        write_json(self.output / "payload-mutations.json", outcomes)
        verify_inventory(prefix, self.args.lean_sha, self.args.beam_sha)

    def generation_and_cleanup(self):
        prefix = self.args.foreign_prefix
        provenance = prefix / "lib/lean-beam/provenance.json"
        original = provenance.read_bytes()
        replacement = json.loads(original)
        replacement["payloadHash"] = "f" * 64 if replacement["payloadHash"] != "f" * 64 else "a" * 64
        root = simple_fixture(Path.cwd() / "CLI generation")
        try:
            with self.owner(root, prefix=prefix) as owner:
                self.call(root, "sync", "Main.lean", prefix=prefix)
                provenance.write_text(json.dumps(replacement))
                self.call(root, "stats", prefix=prefix, ok=False)
                provenance.write_text("{broken provenance")
                self.call(root, "stop", prefix=prefix)
                require(owner.wait(timeout=30) == 0, "CLI stop failed with malformed provenance")
        finally:
            provenance.write_bytes(original)
        for modern in [False, True]:
            root = simple_fixture(Path.cwd() / f"MCP generation {modern}")
            mcp = prefix / self.record["components"]["frontends"]["mcp"]
            try:
                with McpClient([mcp], root, self.output / f"mcp-generation-{modern}", modern=modern) as client:
                    client.initialize()
                    client.tool("lean_sync", root, {"path": "Main.lean"})
                    provenance.write_text(json.dumps(replacement))
                    denied = client.tool("lean_sync", root, {"path": "Main.lean"}, allow_error=True)
                    require(denied.get("error", {}).get("code") == -32600 and "generation changed" in denied["error"]["message"],
                            f"generation replacement admitted: {denied}")
                    provenance.write_text("{broken provenance")
                    require("result" in client.request("tools/list"), "tools/list required broken runtime admission")
                    dropped = client.tool("lean_drop_workspace", root)
                    require(dropped["dropped"], "MCP cleanup required valid provenance")
            finally:
                provenance.write_bytes(original)
        verify_inventory(prefix, self.args.lean_sha, self.args.beam_sha)

    def owned_sessions(self):
        control = simple_fixture(Path.cwd() / "unrelated control")
        observations = []
        with McpClient([self.mcp], control, self.output / "ownership-control") as client:
            client.initialize()
            client.tool("lean_sync", control, {"path": "Main.lean"})
            for index in range(5):
                root = simple_fixture(Path.cwd() / f"lifecycle cycle {index}")
                with self.owner(root) as owner:
                    self.call(root, "sync", "Main.lean")
                    children = process_tree(owner.pid)
                wait_until(lambda: not any(running(pid) for pid in children), "graceful stop leaked an owned process")
                observations.append({"scenario": f"graceful-cycle-{index}", "owned_pids": sorted(children), "stopped": True})
                client.tool("lean_sync", control, {"path": "Main.lean"})
            root = simple_fixture(Path.cwd() / "forced CLI owner")
            with self.owner(root, expected_exit=(-signal.SIGKILL,)) as owner:
                self.call(root, "sync", "Main.lean")
                children = process_tree(owner.pid)
                registry = root / ".beam/beam-daemon.json"
                generation = json.loads(registry.read_text())["daemonId"]
                owner.kill()
                owner.wait(timeout=10)
                wait_until(lambda: not any(running(pid) for pid in children), "forced owner loss leaked a worker")
            observations.append({"scenario": "forced-owner-loss", "owned_pids": sorted(children), "stopped": True})
            status = json.loads(self.call(root, "status").stdout)
            require(status["result"]["state"] == "recoveryRequired", "abnormal session was silently replaced")
            self.call(root, "recover", "--generation", "wrong-generation", ok=False)
            require(registry.exists(), "wrong-generation recovery removed the fence")
            recovered = json.loads(self.call(root, "recover", "--generation", generation).stdout)
            require(recovered["result"]["state"] == "absent" and recovered["result"]["changed"], "explicit recovery failed")
            response_file(recovered, "quarantined_path", root)
            with self.owner(root):
                self.call(root, "sync", "Main.lean")
            client.tool("lean_sync", control, {"path": "Main.lean"})
        write_json(self.output / "session-lifecycles.json", observations)

    def helper_lifetimes(self):
        outcomes = []
        for modern in [False, True]:
            for scenario in ["eof", "cancel-drop", "client-loss", "slow-success", "helper-exit"]:
                root = helper_fixture(Path.cwd() / f"helper {modern} {scenario}", abrupt=scenario == "helper-exit")
                client = McpClient([self.mcp], root, self.output / f"helper-{modern}-{scenario}",
                                   modern=modern, shutdown_timeout=30)
                pids = []
                try:
                    client.initialize()
                    request = client.send("tools/call", {"name": "lean_sync", "arguments": {
                        "workspace": {"root": str(root)}, "path": "Main.lean"}})
                    wait_until(lambda: helper_pids(root) is not None,
                               "helper fixture did not enter", timeout=60)
                    pids = helper_pids(root)
                    if scenario == "cancel-drop":
                        client.notify("notifications/cancelled", {"requestId": request})
                        dropped = client.tool("lean_drop_workspace", root)
                        require(dropped["dropped"], "cancelled workspace did not drop")
                    elif scenario == "slow-success":
                        # Cold Lake setup has no implicit startup deadline.
                        try:
                            response = client.receive(request, timeout=35)
                        except TimeoutError:
                            pass
                        else:
                            raise ValidationError(f"held helper completed before release: {response}")
                        require(client.proc.poll() is None and all(running(pid) for pid in pids),
                                "slow setup lost an owned helper before release")
                        (root / "release").touch()
                        response = client.receive(request)
                        result = response.get("result", {})
                        require("error" not in response and result.get("isError") is False
                                and result.get("structuredContent", {}).get("path") == "Main.lean",
                                f"released helper did not complete sync: {response}")
                    elif scenario == "helper-exit":
                        response = client.receive(request)
                        text = json.dumps(response)
                        expected = "exited before completing its response"
                        require(expected in text and ("error" in response or response.get("result", {}).get("isError") is True),
                                f"wrong helper failure: {response}")
                    elif scenario == "client-loss":
                        client.proc.kill()
                        client.proc.wait(timeout=10)
                    client.close(expected_exit=(-signal.SIGKILL,) if scenario == "client-loss" else (0,))
                    wait_until(lambda: not any(running(pid) for pid in pids), f"{scenario} leaked helper/descendant", timeout=10)
                    outcomes.append({"modern": modern, "scenario": scenario, "pids": pids, "stopped": True})
                finally:
                    (root / "release").touch()
                    client.close()
                    write_json(self.output / "helper-lifecycles.json", outcomes)

    def execute(self):
        checks = [("inventory", self.inventory), ("identity", self.identity), (self.loader_case, self.loaders),
                  ("CLI and live plugin", self.cli), ("external MCP probe", self.mcp_probe)]
        checks += [("P04/P05 CLI edits and recovery", self.cli_edit_and_recovery),
                   ("P06 save and replay", self.save_and_replay), ("P07 deferred save", self.deferred_save),
                   ("P08 independent MCP", self.mcp_workflow), ("P09 workspace isolation", self.mcp_workspace_isolation),
                   ("P10/P11 offline target selectors", self.selectors), ("P12 damaged payload", self.damaged_payload),
                   ("P13 generation and cleanup", self.generation_and_cleanup),
                   ("P14 owned sessions", self.owned_sessions), ("P15 helper lifetimes", self.helper_lifetimes),
                   ("unchanged payload", self.unchanged)]
        failed = False
        for name, action in checks:
            try:
                self.case(name, action)
            except NotApplicable as error:
                self.results[-1].update(status="NOT_APPLICABLE", error=str(error))
            except Exception:
                failed = True
                if name in {"inventory", "identity"}:
                    break
        write_json(self.output / "results.json", self.results)
        write_json(self.output / "summary.json", {
            "suite": self.suite_name, "status": "FAIL" if failed else "PASS", "release_qualified": False,
            "qualification_status": "BLOCKED", "lean_sha": self.args.lean_sha, "beam_sha": self.args.beam_sha,
            "pending": ["P01 independent producer attestation",
                        "P16 other platforms/formats", "P17 publication reconciliation"], "results": self.results,
        })
        if failed:
            raise SystemExit(1)
