# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Native Mach-O identity, dependency, and live plugin observations."""

import json
from pathlib import Path
import platform
import re
import subprocess

from functional import FunctionalSuite
from processes import process_tree
from validate import Pilot, require, write_json


def dylib_paths(text):
    paths = set()
    for line in text.splitlines():
        match = re.search(r"(?:^|\s)(/.*?\.dylib)(?:\s*$)", line)
        if match:
            paths.add(match.group(1))
    return paths


def system_library(path):
    return str(path).startswith(("/usr/lib/", "/System/Library/", "/System/Volumes/Preboot/Cryptexes/OS/usr/lib/",
                                 "/System/Volumes/Preboot/Cryptexes/OS/System/Library/"))


def verify_live_images(paths, prefix):
    for path in paths:
        require(system_library(path) or Path(path).resolve().is_relative_to(prefix), f"external live dylib: {path}")


def verify_macho_dependencies(text, prefix):
    for line in text.splitlines():
        if not line[:1].isspace():
            continue
        dependency = line.strip().split(" (compatibility version", 1)[0]
        if not dependency:
            continue
        if dependency.startswith(("@rpath/", "@loader_path/", "@executable_path/")):
            continue
        path = Path(dependency)
        require(path.is_absolute(), f"relative Mach-O dependency: {dependency}")
        require(system_library(path) or path.resolve().is_relative_to(prefix), f"external Mach-O dependency: {dependency}")


class MacPilot(Pilot):
    suite_name = "initial-macos-pilot"
    loader_case = "Mach-O dependencies"

    def loaders(self):
        architecture = self.args.expected_architecture
        require(platform.machine() == architecture, f"runner is {platform.machine()}, expected native {architecture}")
        declarations = {}
        for relative in [self.record["components"]["frontends"]["cli"], self.target["daemon"],
                         self.record["components"]["frontends"]["mcp"], self.target["plugin"], self.target["lean"]]:
            path = self.prefix / relative
            actual = self.run(["/usr/bin/lipo", "-archs", path]).strip()
            require(architecture in actual.split(), f"wrong native architecture: {relative}: {actual}")
            linked = self.run(["/usr/bin/otool", "-L", path])
            verify_macho_dependencies(linked, self.prefix)
            declarations[relative] = {"architecture": actual, "dependencies": linked,
                                      "load_commands": self.run(["/usr/bin/otool", "-l", path])}
        write_json(self.output / "loader-declarations.json", declarations)

    def loaded_modules(self, owner_pid):
        observations = {}
        plugin = str((self.prefix / self.target["plugin"]).resolve())
        found_plugin = False
        for pid in sorted(process_tree(owner_pid)):
            self.counter += 1
            stem = self.output / f"vmmap-{pid}"
            result = subprocess.run(["/usr/bin/vmmap", "-w", str(pid)], text=True, capture_output=True, timeout=30)
            stem.with_suffix(".stdout").write_text(result.stdout)
            stem.with_suffix(".stderr").write_text(result.stderr)
            paths = dylib_paths(result.stdout)
            verify_live_images(paths, self.prefix)
            observations[str(pid)] = {"returncode": result.returncode, "paths": sorted(paths)}
            for path in paths:
                if str(Path(path).resolve()) == plugin:
                    found_plugin = True
        write_json(self.output / "loaded-modules.json", observations)
        require(found_plugin, "plugin not observed in native vmmap output; inspect retained vmmap logs")


class MacFunctional(FunctionalSuite, MacPilot):
    suite_name = "macos-functional"
