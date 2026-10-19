# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Observe owned process trees on Linux and macOS without inspecting command arguments."""

from pathlib import Path
import ctypes
import errno
from functools import lru_cache
import sys


class ShortBsdInfo(ctypes.Structure):
    # proc_bsdshortinfo in Apple's sys/proc_info.h (64 bytes on both native targets).
    _fields_ = [(name, ctypes.c_uint32) for name in ["pid", "ppid", "pgid", "status"]] + [
        ("comm", ctypes.c_char * 16),
    ] + [(name, ctypes.c_uint32) for name in ["flags", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved"]]


class DarwinProcesses:
    def __init__(self, library=None):
        self.library = library if library is not None else ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.library.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        self.library.proc_listpids.restype = ctypes.c_int
        self.library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        self.library.proc_pidinfo.restype = ctypes.c_int

    def children(self, parent):
        capacity = 64
        while capacity <= 65536:
            buffer = (ctypes.c_int * capacity)()
            ctypes.set_errno(0)
            count = self.library.proc_listpids(6, parent, buffer, ctypes.sizeof(buffer))  # PROC_PPID_ONLY
            error = ctypes.get_errno()
            if count <= 0 and error:
                if error == errno.ESRCH:
                    return set()
                raise OSError(error, "proc_listpids failed")
            if count < 0 or count % ctypes.sizeof(ctypes.c_int):
                raise RuntimeError("invalid child process list size")
            if count < ctypes.sizeof(buffer):
                return {pid for pid in buffer[:count // ctypes.sizeof(ctypes.c_int)] if pid > 0}
            capacity *= 2
        raise RuntimeError("child process list exceeded observation bound")

    def info(self, pid):
        info = ShortBsdInfo()
        ctypes.set_errno(0)
        # PROC_PIDT_SHORTBSDINFO; arg=1 includes zombies so they can be distinguished.
        count = self.library.proc_pidinfo(pid, 13, 1, ctypes.byref(info), ctypes.sizeof(info))
        error = ctypes.get_errno()
        if count == 0 and error == errno.ESRCH:
            return None
        if count == 0 and error:
            raise OSError(error, "proc_pidinfo failed")
        if count != ctypes.sizeof(info):
            raise RuntimeError(f"short process info: {count} bytes")
        if info.pid != pid:
            raise RuntimeError("process info identity mismatch")
        return info

    def tree(self, parent):
        owned, pending = {parent}, [parent]
        while pending:
            children = self.children(pending.pop()) - owned
            owned.update(children)
            pending.extend(children)
            if len(owned) > 65536:
                raise RuntimeError("process tree exceeded observation bound")
        return owned

    def running(self, pid):
        info = self.info(pid)
        return info is not None and info.status != 5  # SZOMB


@lru_cache(maxsize=1)
def darwin_processes():
    return DarwinProcesses()


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
        return darwin_processes().tree(parent)
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
        return darwin_processes().running(pid)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
