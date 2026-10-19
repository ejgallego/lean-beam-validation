# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Initial artifact-only smoke checks. This is not the full qualification suite."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import time
import traceback


class ValidationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def owned_path(prefix, relative):
    path = PurePosixPath(relative)
    require(not path.is_absolute() and ".." not in path.parts and ":" not in relative
            and "\\" not in relative and str(path) == relative,
            f"unsafe inventory destination: {relative}")
    target = prefix / relative
    require(target.resolve().is_relative_to(prefix), f"path escapes SDK: {target}")
    return target


def verify_inventory(prefix, lean_sha, beam_sha, manifest_sha256=None, build_id=None):
    prefix = Path(prefix).resolve()
    manifest = owned_path(prefix, "lib/lean-beam/provenance.json")
    record = json.loads(manifest.read_text())
    require(record["schemaVersion"] == 3, "expected schema 3")
    require(record["sourceCommit"] == beam_sha, "unexpected Beam source revision")
    require(re.fullmatch(r"[0-9a-f]{32}", record["buildId"]), "invalid buildId")
    require(re.fullmatch(r"[0-9a-f]{64}", record["payloadHash"]), "invalid payloadHash")
    require(not build_id or record["buildId"] == build_id, "unexpected buildId")
    digest = sha256(manifest)
    require(not manifest_sha256 or digest == manifest_sha256, "producer manifest digest mismatch")
    components = record["components"]
    frontends = components["frontends"]
    require(len(components["targets"]) == 1, "expected exactly one target")
    target = components["targets"][0]
    require(frontends["compilerRevision"] == target["compilerRevision"] == lean_sha,
            "unexpected compiler revision")
    roles = {frontends["cli"], frontends["mcp"], target["daemon"], target["plugin"], components["license"]}
    require(len(roles) == 5, "expected five distinct payload roles")
    files = {}
    for item in record["artifacts"]:
        relative = item["destination"]
        require(relative not in files, f"duplicate inventory entry: {relative}")
        path = owned_path(prefix, relative)
        require(path.is_file(), f"missing payload: {relative}")
        require(sha256(path) == item["hash"], f"payload hash mismatch: {relative}")
        files[relative] = {"sha256": item["hash"], "size": path.stat().st_size}
    require(set(files) == roles, "inventory does not match component roles")
    private = {str(path.relative_to(prefix)) for path in (prefix / "lib/lean-beam").rglob("*") if path.is_file()}
    expected_private = {name for name in roles if name.startswith("lib/lean-beam/")} | {"lib/lean-beam/provenance.json"}
    require(private == expected_private, f"unexpected private payload: {private ^ expected_private}")
    files["lib/lean-beam/provenance.json"] = {"sha256": digest, "size": manifest.stat().st_size}
    return {"manifest_sha256": digest, "record": record, "files": files,
            "producer_manifest_digest_supplied": manifest_sha256 is not None}


def verify_executable_dependencies(text, prefix):
    require("not found" not in text, "missing executable dependency")
    for line in text.splitlines():
        name = line.strip().split(" ", 1)[0]
        if name.startswith(("libLean", "libLake", "liblean", "libInit")):
            require("=>" in line, f"unresolved dependency declaration: {line}")
            path = line.split("=>", 1)[1].strip().split(" (", 1)[0]
            require(Path(path).resolve().is_relative_to(prefix), f"external Lean library: {line}")


class Pilot:
    def __init__(self, args):
        self.args = args
        self.prefix = args.prefix.resolve()
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.results = []
        self.counter = 0

    def run(self, argv, cwd=None, timeout=120):
        self.counter += 1
        stem = self.output / f"command-{self.counter:03}"
        write_json(stem.with_suffix(".json"), {"argv": list(map(str, argv)), "cwd": str(cwd) if cwd else None})
        with stem.with_suffix(".stdout").open("w") as stdout, stem.with_suffix(".stderr").open("w") as stderr:
            result = subprocess.run(list(map(str, argv)), cwd=cwd, stdout=stdout, stderr=stderr,
                                    text=True, timeout=timeout)
        require(result.returncode == 0, f"command failed ({result.returncode}); see {stem.name}")
        return stem.with_suffix(".stdout").read_text()

    def case(self, name, action):
        start = time.monotonic()
        result = {"case": name, "status": "PASS", "started_at": datetime.now(timezone.utc).isoformat(),
                  "identity_file": "input.json"}
        try:
            action()
        except Exception as error:
            result.update(status="FAIL", error=str(error), traceback=traceback.format_exc())
            raise
        finally:
            result["duration_seconds"] = time.monotonic() - start
            self.results.append(result)
            write_json(self.output / "results.json", self.results)

    def inventory(self):
        for path in self.args.forbidden_path:
            require(not (path / "src").exists() and not (path / "build").exists(), f"producer tree is visible: {path}")
        self.before = verify_inventory(self.prefix, self.args.lean_sha, self.args.beam_sha,
                                       self.args.producer_manifest_sha256, self.args.build_id)
        write_json(self.output / "inventory.json", self.before)
        for path in self.prefix.rglob("*"):
            if path.is_symlink():
                require(path.resolve().is_relative_to(self.prefix), f"external SDK link: {path}")
        self.record = self.before["record"]
        self.beam = self.prefix / self.record["components"]["frontends"]["cli"]
        self.mcp = self.prefix / self.record["components"]["frontends"]["mcp"]
        self.target = self.record["components"]["targets"][0]

    def identity(self):
        require(Path(self.run([self.prefix / self.target["lean"], "--print-prefix"]).strip()).resolve() == self.prefix,
                "Lean selected an external prefix")
        info = json.loads(self.run([self.beam, "toolchain-build-info"]))
        require(info["sourceRevision"] == self.args.beam_sha, "compiled Beam revision mismatch")
        require(info["compiledLeanRevision"] == info["loadedLeanRevision"] == self.args.lean_sha,
                "compiled/loaded Lean revision mismatch")
        require(info["buildId"] == self.record["buildId"], "compiled generation mismatch")
        for executable in [self.beam, self.mcp]:
            require(self.record["payloadHash"] in self.run([executable, "--version"]), "version payload mismatch")
        write_json(self.output / "build-info.json", info)

    def loaders(self):
        declarations = {}
        for relative in [self.record["components"]["frontends"]["cli"], self.target["daemon"],
                         self.record["components"]["frontends"]["mcp"]]:
            text = self.run(["/usr/bin/ldd", self.prefix / relative])
            verify_executable_dependencies(text, self.prefix)
            declarations[relative] = text
        # Plugins can reuse libraries already loaded by their Lean host.
        declarations[self.target["plugin"]] = self.run(["/usr/bin/readelf", "-d", self.prefix / self.target["plugin"]])
        write_json(self.output / "loader-declarations.json", declarations)

    def loaded_modules(self, owner_pid):
        parents = {}
        for proc in Path("/proc").iterdir():
            if proc.name.isdigit():
                try:
                    stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                    parents[int(proc.name)] = int(stat[1])
                except (FileNotFoundError, ProcessLookupError):
                    pass
        owned = {owner_pid}
        while True:
            expanded = owned | {pid for pid, parent in parents.items() if parent in owned}
            if expanded == owned:
                break
            owned = expanded
        modules = {}
        for pid in owned:
            try:
                paths = sorted({line.split(maxsplit=5)[5].strip() for line in Path(f"/proc/{pid}/maps").read_text().splitlines()
                                if len(line.split(maxsplit=5)) == 6 and line.split(maxsplit=5)[5].startswith("/")})
            except (FileNotFoundError, ProcessLookupError):
                continue
            modules[str(pid)] = paths
            for path in paths:
                if Path(path).name.startswith(("libLean", "libLake", "liblean", "libInit", "libbeam_")):
                    require(Path(path).resolve().is_relative_to(self.prefix), f"external live Lean/Beam library: {path}")
        write_json(self.output / "loaded-modules.json", modules)
        require(any(str(self.prefix / self.target["plugin"]) in paths for paths in modules.values()),
                "plugin was not observed in live process maps")

    def cli(self):
        self.project = Path.cwd() / "project with spaces λ"
        self.project.mkdir()
        (self.project / "lakefile.toml").write_text('name = "beam_artifact_pilot"\n')
        (self.project / "Main.lean").write_text('module\n\n/-! Artifact consumer fixture. -/\n\ndef answer : Nat := 42\n')
        doctor = self.run([self.beam, "--root", self.project, "doctor"])
        require("target membership: present" in doctor, "containing SDK membership missing")
        for key in ["bundle daemon", "plugin"]:
            value = next(line[len(key) + 2:] for line in doctor.splitlines() if line.startswith(key + ": "))
            require(Path(value).resolve().is_relative_to(self.prefix), f"doctor reports external {key}")
        (self.output / "doctor.txt").write_text(doctor)
        owner_log = self.output / "owner.log"
        with owner_log.open("w") as log:
            owner = subprocess.Popen([str(self.beam), "--root", str(self.project), "serve"],
                                     stdout=log, stderr=log, start_new_session=True)
            cleanup = {"owner_pid": owner.pid, "forced": False}
            try:
                deadline = time.monotonic() + 120
                while "serving Beam session" not in owner_log.read_text():
                    require(owner.poll() is None, "serve exited before readiness")
                    require(time.monotonic() < deadline, "serve readiness timed out")
                    time.sleep(0.1)
                updated = json.loads(self.run([self.beam, "--root", self.project, "update", "Main.lean"]))
                probe = json.loads(self.run([self.beam, "--root", self.project, "run-at", "Main.lean",
                                            str(updated["result"]["version"]), "5", "0", "#check answer"]))
                require(probe["result"]["success"], f"probe failed: {probe}")
                self.loaded_modules(owner.pid)
            finally:
                try:
                    if owner.poll() is None:
                        self.run([self.beam, "--root", self.project, "stop"], timeout=30)
                    owner.wait(timeout=30)
                finally:
                    if owner.poll() is None:
                        cleanup["forced"] = True
                        os.killpg(owner.pid, signal.SIGKILL)
                        owner.wait(timeout=10)
                    cleanup["returncode"] = owner.returncode
                    write_json(self.output / "cleanup.json", cleanup)
            require(not cleanup["forced"] and owner.returncode == 0, "owner did not stop cleanly")

    def mcp_self_check(self):
        text = self.run([self.mcp, "--self-check", "Main.lean"], cwd=self.project)
        require("Lean Beam MCP self-check passed" in text, "MCP self-check failed")

    def unchanged(self):
        after = verify_inventory(self.prefix, self.args.lean_sha, self.args.beam_sha)
        require(after == self.before or after["files"] == self.before["files"], "payload changed during pilot")
        for path in [Path(os.environ["BEAM_HOME"]), Path(os.environ["BEAM_BUNDLE_DIR"]), self.project / ".beam/bundles"]:
            require(not path.exists(), f"unexpected standalone runtime cache: {path}")

    def execute(self):
        passed = False
        try:
            for name, action in [("inventory", self.inventory), ("identity", self.identity),
                                 ("ELF dependencies", self.loaders), ("CLI and live plugin", self.cli),
                                 ("MCP self-check", self.mcp_self_check), ("unchanged payload", self.unchanged)]:
                self.case(name, action)
            passed = True
        finally:
            write_json(self.output / "summary.json", {
                "suite": "initial-linux-pilot", "status": "PASS" if passed else "FAIL",
                "release_qualified": False,
                "qualification_note": "P01-P17 remain pending full implementation; these are pilot checks only.",
                "lean_sha": self.args.lean_sha, "beam_sha": self.args.beam_sha,
                "results": self.results,
            })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--lean-sha", required=True)
    parser.add_argument("--beam-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer-manifest-sha256")
    parser.add_argument("--build-id")
    parser.add_argument("--forbidden-path", action="append", type=Path, default=[])
    parser.add_argument("--tier", choices=["pilot", "functional"], default="pilot")
    parser.add_argument("--foreign-prefix", type=Path)
    args = parser.parse_args()
    if args.tier == "functional":
        from functional import FunctionalSuite
        FunctionalSuite(args).execute()
    else:
        Pilot(args).execute()


if __name__ == "__main__":
    main()
