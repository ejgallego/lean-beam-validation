# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
"""Assert the actual macOS sandbox admits loopback and denies external networking/SDK writes."""

import argparse
import errno
import json
import os
from pathlib import Path
import socket


def observe_permission(action, label):
    try:
        action()
    except OSError as error:
        if error.errno not in {errno.EPERM, errno.EACCES, errno.EROFS}:
            raise AssertionError(f"{label}: unexpected failure {error}") from error
        return {"denied": True, "errno": error.errno}
    return {"denied": False}


def denied(action, label):
    observation = observe_permission(action, label)
    if not observation["denied"]:
        raise AssertionError(f"{label} was permitted")
    return observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    observations = {"status": "FAIL"}
    try:
        with socket.socket() as server, socket.socket() as client:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            client.settimeout(3)
            client.connect(server.getsockname())
            with server.accept()[0] as accepted:
                client.sendall(b"probe")
                assert accepted.recv(5) == b"probe"
            observations["loopback"] = "PASS"
        with socket.socket() as remote:
            remote.settimeout(3)
            observations["external_tcp"] = denied(lambda: remote.connect(("198.51.100.1", 9)), "external TCP")
        with socket.socket() as listener:
            # Seatbelt's localhost filter describes host-local addresses, not just loopback.
            observations["wildcard_bind"] = observe_permission(lambda: listener.bind(("0.0.0.0", 0)), "wildcard bind")
        def open_manifest_for_write():
            descriptor = os.open(args.prefix / "lib/lean-beam/provenance.json", os.O_WRONLY)
            os.close(descriptor)
        observations["sdk_write"] = denied(open_manifest_for_write, "SDK write access")
        writable = args.output.parent / "writable-proof"
        writable.write_text("fixture writes are admitted\n")
        writable.unlink()
        observations["fixture_write"] = "PASS"
        observations["status"] = "PASS"
    finally:
        args.output.write_text(json.dumps(observations, indent=2) + "\n")


if __name__ == "__main__":
    main()
