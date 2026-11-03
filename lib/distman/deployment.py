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

"""Git-independent, byte-preserving publication of prepared directories.

Uses the same sibling ``versions`` layout as Distributor. Callers serialize
writers and validate ownership before publishing. Each link replacement is
atomic; multiple targets have exception rollback, not global atomic visibility.
"""

import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

from distman import config, util
from distman.metadata import (
    RecoveryRequired,
    Store,
    atomic_write,
    durable_directory,
    sync_directory,
)


def get_version_dest(dest: str, version_num: int, short_head: Optional[str]) -> str:
    """Generate a destination using distman's existing revision naming scheme.

    :param dest: Active destination whose sibling versions directory will be used.
    :param version_num: Distman revision number, independent of the package version.
    :param short_head: Optional source revision suffix; None omits the suffix.
    :return: Path of the numbered revision without creating it.
    """
    versions_dir = os.path.join(os.path.dirname(dest), config.DIR_VERSIONS)
    version_dest = os.path.join(versions_dir, os.path.basename(dest) + f".{version_num}")
    if short_head:
        version_dest += f".{short_head}"
    return util.sanitize_path(version_dest)


def replace_link(destination: Union[str, Path], target: Union[str, Path]) -> None:
    """Replace an active symlink without first unlinking the old revision.

    :param destination: Active symlink path to replace; its parent must already exist.
    :param target: Link target, relative to the destination parent or absolute.
    :return: None.
    :raises OSError: If link creation or atomic replacement fails.
    """
    destination = Path(destination)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.link")
    try:
        os.symlink(target, temporary, target_is_directory=(destination.parent / target).is_dir())
        os.replace(temporary, destination)
        sync_directory(destination.parent)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def prepare_revision(source: Path, destination: Path) -> Path:
    """Copy all bytes and modes before exposing a complete numbered revision.

    :param source: Complete staged directory to copy without normalizing its contents.
    :param destination: Active destination used to select the next sibling revision.
    :return: Path of the completed, inactive revision.
    :raises OSError: If the staged content cannot be copied or renamed.
    """
    destination = Path(destination)
    versions = util.get_file_versions(str(destination))
    number = versions[-1][1] + 1 if versions else 0
    revision = Path(get_version_dest(str(destination), number, None))
    durable_directory(revision.parent)
    temporary = revision.with_name(f".{revision.name}.{uuid.uuid4().hex}.staging")
    try:
        shutil.copytree(source, temporary, symlinks=True)
        flush_payload(temporary)
        os.rename(temporary, revision)
        sync_directory(revision.parent)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return revision


def flush_payload(directory: Path) -> None:
    """Flush newly copied payload files and directory entries before activation.

    :param directory: Prepared revision tree; symbolic links are not followed.
    :return: None.
    :raises OSError: If a file or supported directory flush fails.
    """
    for root, _, files in os.walk(directory, topdown=False, followlinks=False):
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        sync_directory(Path(root))


def observed_link(destination: Path) -> Optional[str]:
    """Inspect active link identity without confusing absence with an unmanaged object.

    :param destination: Active deployment destination.
    :return: Stored link text, or None for an absent path.
    :raises RecoveryRequired: If an unexpected non-link object occupies the destination.
    """
    if destination.is_symlink():
        return os.readlink(destination)
    if os.path.lexists(destination):
        raise RecoveryRequired(f"Unexpected non-link destination: {destination}")
    return None


def recover_publication(deploy_root: Path, dryrun: bool = False) -> List[str]:
    """Restore a pending publication, or finalize an already committed operation.

    The command orchestrator must hold the deployment writer lock. Validate all
    destinations before changing any; do not overwrite unexpected external changes.

    :param deploy_root: Deployment boundary owning the pending operation.
    :param dryrun: Report recovery actions without changing links or metadata.
    :return: Human-readable actions, or an empty list if no operation is pending.
    :raises RecoveryRequired: If state conflicts or required previous revisions are missing.
    :raises OSError: If restoration or durable metadata updates fail.
    """
    store = Store(deploy_root)
    record = store.pending()
    if record is None:
        return []
    current = store.current()
    previous, selected = record["previous_snapshot"], record["snapshot"]
    if current not in (previous, selected):
        raise RecoveryRequired("Current snapshot changed outside the pending publication")
    if previous is not None:
        baseline = store.read(f"snapshots/{previous}.json")
        if baseline is None or baseline.get("id") != previous:
            raise RecoveryRequired(f"Previous snapshot is missing: {previous}")
    if record["phase"] == "restored" and current != previous:
        raise RecoveryRequired("Restored publication has an inconsistent snapshot pointer")
    committed = record["phase"] == "committed"
    if committed and current != selected:
        raise RecoveryRequired("Committed publication has an inconsistent snapshot pointer")
    actions = []
    for change in record["changes"]:
        destination = Path(change["destination"])
        actual = observed_link(destination)
        before, after = change["before"], change["after"]
        expected = after if committed else before
        if actual not in (before, after) or (committed and actual != after):
            raise RecoveryRequired(f"Recovery conflict at {destination}: unexpected active link")
        if expected is not None and not (destination.parent / expected).exists():
            raise RecoveryRequired(f"Recovery revision is missing: {destination.parent / expected}")
        if not committed and actual != before:
            actions.append(f"Restore: {destination} -> {before or '(absent)'}")
    actions.append(f"Finalize {'committed' if committed else 'restored'} operation: {record['id']}")
    if dryrun:
        return actions
    if not committed:
        for change in reversed(record["changes"]):
            destination = Path(change["destination"])
            before = change["before"]
            actual = observed_link(destination)
            if actual == before:
                continue
            if actual != change["after"]:
                raise RecoveryRequired(f"Recovery conflict at {destination}")
            if before is None:
                destination.unlink()
                sync_directory(destination.parent)
            else:
                if not (destination.parent / before).exists():
                    raise RecoveryRequired(f"Recovery revision disappeared: {destination}")
                replace_link(destination, before)
        if previous is None:
            store.remove("current.json")
        else:
            store.write("current.json", {"schema": 1, "snapshot": previous})
        atomic_write(store.directory / config.DISTMAN_EPOCH_FILE, uuid.uuid4().hex + "\n")
        record["phase"] = "restored"
        store.write(f"operations/{record['id']}.json", record)
    store.remove("pending.json")
    return actions


def publish_directories(changes: Iterable[Tuple[Path, Path]], deploy_root: Path) -> None:
    """Publish prepared directories with a durable journal and incremental snapshot.

    Callers hold the deployment writer lock. Retained revisions provide rollback
    bytes; only selected link changes are journaled, without scanning the deployment.

    :param changes: Staged-source/destination pairs to publish.
    :param deploy_root: Deployment boundary owning publication records.
    :return: None.
    :raises RecoveryRequired: If earlier work is pending or rollback cannot complete safely.
    :raises OSError: If preparation, activation, or durable metadata writes fail.
    """
    store = Store(deploy_root)
    store.ensure_clean()
    changes = [(Path(source), Path(destination).absolute()) for source, destination in changes]
    destinations = [destination for _, destination in changes]
    for i, destination in enumerate(destinations):
        if (
            ".." in destination.parts
            or destination == store.root
            or destination in store.root.parents
            or destination == store.directory
            or store.directory in destination.parents
        ):
            raise ValueError(f"Reserved or invalid publication destination: {destination}")
        if any(
            destination == other or destination in other.parents or other in destination.parents
            for other in destinations[i + 1 :]
        ):
            raise ValueError(f"Overlapping publication destinations: {destination}")
    if not changes:
        return
    identifier = uuid.uuid4().hex
    previous_snapshot = store.current()
    prepared = []
    selections = {}
    for source, destination in changes:
        previous = observed_link(destination)
        if previous is not None and not (destination.parent / previous).exists():
            raise RecoveryRequired(f"Previous revision is missing: {destination}")
        revision = prepare_revision(source, destination)
        link = os.path.relpath(revision, destination.parent)
        prepared.append({"destination": str(destination), "before": previous, "after": link})
        try:
            target_path = destination.relative_to(store.root).as_posix()
            payload_path = revision.relative_to(store.root).as_posix()
        except ValueError:
            target_path, payload_path = str(destination), str(revision)
        target_id = hashlib.sha256(target_path.encode()).hexdigest()
        revision_record = f"targets/{target_id}/revisions/{identifier}.json"
        store.write(
            revision_record,
            {
                "schema": 1,
                "destination": target_path,
                "payload": payload_path,
                "kind": "directory",
                "id": identifier,
                "ownership": (
                    "embedded-receipt"
                    if (revision / ".distman-install.json").is_file()
                    else "unknown"
                ),
            },
        )
        selections[target_path] = revision_record
    snapshot = {
        "schema": 1,
        "id": identifier,
        "parent": previous_snapshot,
        "created": datetime.now(timezone.utc).isoformat(),
        "coverage": "recorded-targets",
        "changes": selections,
    }
    store.write(f"snapshots/{identifier}.json", snapshot)
    journal = {
        "schema": 1,
        "id": identifier,
        "root": str(store.root),
        "phase": "prepared",
        "snapshot": identifier,
        "previous_snapshot": previous_snapshot,
        "changes": prepared,
    }
    store.write(f"operations/{identifier}.json", journal)
    store.write("pending.json", {"schema": 1, "operation": identifier})
    try:
        for change in prepared:
            destination = Path(change["destination"])
            if observed_link(destination) != change["before"]:
                raise RecoveryRequired(f"Destination changed before activation: {destination}")
            replace_link(destination, change["after"])
        store.write("current.json", {"schema": 1, "snapshot": identifier})
        atomic_write(store.directory / config.DISTMAN_EPOCH_FILE, identifier + "\n")
        journal["phase"] = "committed"
        store.write(f"operations/{identifier}.json", journal)
    except BaseException:
        try:
            recover_publication(deploy_root)
        except BaseException as recovery_error:
            raise RecoveryRequired(
                f"Publication interrupted; recovery is required at {deploy_root}: {recovery_error}"
            ) from recovery_error
        raise
    store.remove("pending.json")
