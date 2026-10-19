# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Observe owned process trees on Linux and macOS without inspecting command arguments."""

from pathlib import Path
import subprocess
import sys


def parse_parents(text):
    parents = {}
    for line in text.splitlines():
        if line.strip():
            pid, ppid = line.split()
            parents[int(pid)] = int(ppid)
    return parents


def descendants(parent, parents):
    owned = {parent}
    while True:
        expanded = owned | {pid for pid, ppid in parents.items() if ppid in owned}
        if expanded == owned:
            return owned
        owned = expanded


def process_tree(parent):
    if sys.platform == "darwin":
        text = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid="], text=True, timeout=10)
        return descendants(parent, parse_parents(text))
    parents = {}
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                parents[int(proc.name)] = int((proc / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (FileNotFoundError, ProcessLookupError):
                pass
    return descendants(parent, parents)


def running(pid):
    if sys.platform == "darwin":
        result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], text=True, capture_output=True, timeout=10)
        if result.returncode not in {0, 1}:
            raise RuntimeError(f"process observation failed: {result.stderr}")
        return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
