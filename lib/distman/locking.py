#!/usr/bin/env python3
#
# Copyright (c) 2024-2025, Ryan Galloway (ryan@rsgalloway.com)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#  - Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
#  - Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
#  - Neither the name of the software nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#

"""Coordinate cooperating writers without package or acquisition dependencies."""

import json
import math
import os
import socket
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

from distman import config


class LockError(OSError):
    """A deployment writer lock could not be safely acquired or released."""


_local = threading.local()


def held_locks() -> Dict[str, dict]:
    """Return locks owned by this process and thread.

    :return: Root paths mapped to owner records, reset after a process fork.
    """
    if getattr(_local, "pid", None) != os.getpid():
        _local.pid = os.getpid()
        _local.held = {}
    return _local.held


def lock_environment(root: Path) -> Dict[str, str]:
    """Authorize only the direct distribution child of a current writer.

    :param root: Deployment root whose lock the child may borrow.
    :return: Explicit subprocess environment additions, or an empty mapping.
    """
    owner = held_locks().get(str(root.resolve()))
    return {"DISTMAN_PARENT_LOCK": json.dumps(owner)} if owner else {}


@contextmanager
def deployment_lock(
    root: Path, dryrun: bool = False, timeout: Optional[float] = None
) -> Iterator[None]:
    """Hold a deployment-wide writer lock, waiting for a bounded interval.

    :param root: Shared deployment boundary; all writers must select the same root.
    :param dryrun: Skip writes and locking for previews and read-only operations.
    :param timeout: Wait seconds, defaulting to DISTMAN_LOCK_TIMEOUT or 30.
    :yields: None while the operation owns or explicitly borrows the lock.
    :raises LockError: On timeout, invalid timeout, or changed lock ownership.
    :raises OSError: If lock metadata cannot be created or removed.
    """
    if dryrun:
        yield
        return
    try:
        seconds = (
            float(os.environ.get("DISTMAN_LOCK_TIMEOUT", "30"))
            if timeout is None
            else float(timeout)
        )
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("timeout must be finite and nonnegative")
    except ValueError as exc:
        raise LockError(f"Invalid deployment lock timeout: {exc}") from exc
    root = root.resolve()
    key = str(root)
    lock = root / config.DISTMAN_META_DIR / "locks" / "write"
    owner_path = lock / "owner.json"
    if key in held_locks():
        yield
        return
    borrowed = False
    try:
        delegated = json.loads(os.environ.get("DISTMAN_PARENT_LOCK", "null"))
        borrowed = (
            isinstance(delegated, dict)
            and delegated.get("root") == key
            and delegated.get("pid") == os.getppid()
            and delegated.get("host") == socket.gethostname()
            and json.loads(owner_path.read_text()) == delegated
        )
    except (OSError, ValueError):
        pass
    if borrowed:
        yield
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + seconds
    announced = False
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if not announced:
                print(f"Waiting for deployment lock: {lock}", file=sys.stderr, flush=True)
                announced = True
            if time.monotonic() >= deadline:
                try:
                    detail = owner_path.read_text()
                except OSError:
                    detail = "owner information unavailable"
                raise LockError(
                    f"Deployment lock timeout: {lock} ({detail}). "
                    "Inspect the owning operation before removing a stale lock."
                )
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    owner = {
        "root": key,
        "token": uuid.uuid4().hex,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "user": os.environ.get("USER", os.environ.get("USERNAME", "unknown")),
        "operation": " ".join(sys.argv[:2]),
    }
    try:
        owner_path.write_text(json.dumps(owner))
    except BaseException:
        owner_path.unlink(missing_ok=True)
        lock.rmdir()
        raise
    held_locks()[key] = owner
    try:
        yield
    finally:
        held_locks().pop(key, None)
        if json.loads(owner_path.read_text()) != owner:
            raise LockError(f"Deployment lock ownership changed: {lock}")
        owner_path.unlink()
        lock.rmdir()


@contextmanager
def locks(destinations: Iterable[Path], dryrun: bool = False) -> Iterator[None]:
    """Serialize writers in deterministic order; dry runs create no lock directories.

    :param destinations: Active target paths whose sibling lock directories serialize
        installation.
    :param dryrun: If True, yield without creating parent or lock directories.
    :yields: None while all requested writer locks are held.
    :raises LockError: If a target already has an active or stale lock directory.
    """
    acquired = []
    try:
        if not dryrun:
            for destination in sorted(destinations):
                destination.parent.mkdir(parents=True, exist_ok=True)
                lock = destination.with_name(f".{destination.name}.install-lock")
                try:
                    lock.mkdir()
                except FileExistsError as exc:
                    raise LockError(
                        f"Deployment lock exists; inspect before removing: {lock}"
                    ) from exc
                acquired.append(lock)
        yield
    finally:
        for lock in reversed(acquired):
            lock.rmdir()
