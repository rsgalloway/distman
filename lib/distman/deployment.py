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

"""Git-independent, byte-preserving publication of prepared targets.

Uses the same sibling ``versions`` layout as Distributor. Callers serialize
writers and validate ownership before publishing. Each link replacement is
atomic on POSIX; Windows directory links have a recoverable unlink/rename gap.
Multiple targets have exception rollback, not global atomic visibility.
"""

import hashlib
import os
import platform
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from distman import config, util
from distman.inventory import file_mode, inventory, sha256
from distman.locking import locks
from distman.metadata import (
    RecoveryRequired,
    Store,
    atomic_write,
    durable_directory,
    sync_directory,
)


@dataclass(frozen=True)
class TargetPublication:
    """One independently versioned deployment target change.

    :param destination: Active target link to create, replace, or remove.
    :param source: Prepared file or directory to publish; None keeps the current payload.
    :param state: Optional centralized ownership/provenance data for this target revision.
    :param delete: If True, unlink the active target without deleting retained revisions.
    :param reuse: If True, select an identical retained payload instead of copying it again.
    """

    destination: Path
    source: Optional[Path] = None
    state: Optional[Dict[str, Any]] = None
    delete: bool = False
    reuse: bool = True


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
    """Replace an active symlink using the platform's supported rename semantics.

    Windows cannot rename over directory links. Its unlink/rename gap requires
    callers to journal the previous link before calling this function.

    :param destination: Active symlink path to replace; its parent must already exist.
    :param target: Link target, relative to the destination parent or absolute.
    :return: None.
    :raises OSError: If link creation or atomic replacement fails.
    """
    destination = Path(destination)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.link")
    try:
        os.symlink(target, temporary, target_is_directory=(destination.parent / target).is_dir())
        if platform.system() == "Windows" and (temporary.is_dir() or destination.is_dir()):
            if destination.is_symlink():
                destination.unlink()
            # rename refuses an existing destination on Windows, including an
            # unmanaged object that appeared during the gap.
            os.rename(temporary, destination)
        else:
            os.replace(temporary, destination)
        sync_directory(destination.parent)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def prepare_revision(
    source: Path,
    destination: Path,
    reuse: bool = True,
    reserved: Iterable[Path] = (),
) -> Path:
    """Copy one file or directory before exposing a complete numbered revision.

    :param source: Complete staged file or directory to copy without normalization.
    :param destination: Active destination used to select the next sibling revision.
    :param reuse: Return the newest identical retained revision when available.
    :param reserved: Historical revision identities that must not be allocated again.
    :return: Path of the completed, inactive revision.
    :raises OSError: If the staged content cannot be copied or renamed.
    """
    destination = Path(destination)
    versions = util.get_file_versions(str(destination))
    if reuse:
        source_inventory = _payload_inventory(source)
        for candidate, _, _ in reversed(versions):
            path = Path(candidate)
            if path.is_dir() != source.is_dir():
                continue
            if _payload_inventory(path) == source_inventory:
                return path
    numbers = [version for _, version, _ in versions]
    version_directory = destination.parent / config.DIR_VERSIONS
    for path in reserved:
        path = Path(path)
        if path.parent != version_directory:
            continue
        parsed = util.parse_versioned_filename(path.name, destination.name)
        if parsed is not None:
            numbers.append(parsed[1])
    number = max(numbers, default=-1) + 1
    revision = Path(get_version_dest(str(destination), number, None))
    durable_directory(revision.parent)
    temporary = revision.with_name(f".{revision.name}.{uuid.uuid4().hex}.staging")
    try:
        if source.is_dir():
            shutil.copytree(source, temporary, symlinks=True)
            flush_payload(temporary)
        else:
            shutil.copy2(source, temporary)
            flush_file(temporary)
        os.rename(temporary, revision)
        sync_directory(revision.parent)
    finally:
        if temporary.is_dir():
            shutil.rmtree(temporary)
        elif temporary.exists():
            temporary.unlink()
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
                flush_file(path)
        sync_directory(Path(root))


def flush_file(path: Path) -> None:
    """Flush one private payload copy using a Windows-compatible descriptor.

    Windows requires a writable descriptor for ``fsync``. Permissions may be
    relaxed only on the private copy and are restored before it becomes active.

    :param path: Prepared regular file that has not yet been activated.
    :return: None.
    :raises OSError: If permission changes, opening, or flushing fails.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    try:
        if not mode & stat.S_IWUSR:
            path.chmod(mode | stat.S_IWUSR)
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())
    finally:
        if not mode & stat.S_IWUSR:
            path.chmod(mode)


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

    The command orchestrator must hold the deployment writer lock. Recovery also
    acquires the pending operation's target locks because configured destinations
    may be shared by otherwise independent deployment roots.

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
    destinations = [Path(change["destination"]) for change in record["changes"]]
    with locks(destinations, dryrun=dryrun):
        return _recover_publication(deploy_root, dryrun)


def _recover_publication(deploy_root: Path, dryrun: bool = False) -> List[str]:
    """Recover while the caller owns deployment and destination coordination.

    Publication failure handling calls this helper while its original target locks
    are still held. Explicit recovery enters through :func:`recover_publication`,
    which acquires those locks before reaching this implementation.

    :param deploy_root: Deployment boundary owning the pending operation.
    :param dryrun: Report recovery actions without changing links or metadata.
    :return: Human-readable actions, or an empty list if no operation is pending.
    :raises RecoveryRequired: If state conflicts or required revisions are missing.
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
        missing_during_swap = (
            record["phase"] == "prepared" and change.get("allow_absent", False) and actual is None
        )
        if (actual not in (before, after) and not missing_during_swap) or (
            committed and actual != after
        ):
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
            missing_during_swap = (
                record["phase"] == "prepared"
                and change.get("allow_absent", False)
                and actual is None
            )
            if actual != change["after"] and not missing_during_swap:
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
        atomic_write(store.directory / config.DISTMAN_EPOCH_FILE, f"{time.time_ns()}\n")
        record["phase"] = "restored"
        store.write(f"operations/{record['id']}.json", record)
    store.remove("pending.json")
    return actions


def _record_path(path: Path, root: Path) -> str:
    """Return a stable root-relative path where possible.

    :param path: Absolute deployment path to encode.
    :param root: Deployment root used for relative records.
    :return: POSIX relative path or the absolute path when outside the root.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _payload_inventory(path: Path) -> Dict[str, Any]:
    """Inventory one immutable target payload.

    :param path: Revision file or directory.
    :return: File digests and modes keyed relative to the target.
    """
    if path.is_dir():
        return inventory(path)
    return {".": {"sha256": sha256(path), "mode": file_mode(path)}}


def publish_targets(publications: Iterable[TargetPublication], deploy_root: Path) -> None:
    """Coordinate target-level writers before publishing one journaled operation.

    The deployment-root lock coordinates commands using the same metadata store.
    Target locks also serialize policies that intentionally place targets outside
    that root, where another deployment policy could otherwise select the same path.

    :param publications: Prepared target changes and centralized ownership state.
    :param deploy_root: Deployment boundary owning publication records.
    :return: None.
    :raises OSError: If target coordination or publication fails.
    :raises ValueError: If a publication target is invalid.
    """
    values = _validate_publications(publications, deploy_root)
    with locks((value.destination for value in values)):
        _publish_targets(values, deploy_root)


def _validate_publications(
    publications: Iterable[TargetPublication], deploy_root: Path
) -> List[TargetPublication]:
    """Normalize and reject invalid targets before creating coordination state.

    :param publications: Requested target changes.
    :param deploy_root: Deployment boundary owning publication metadata.
    :return: Normalized publication values.
    :raises ValueError: If targets are reserved, overlap, or have inconsistent options.
    """
    store = Store(deploy_root)
    values = [
        TargetPublication(
            Path(value.destination).absolute(),
            Path(value.source) if value.source is not None else None,
            value.state,
            value.delete,
            value.reuse,
        )
        for value in publications
    ]
    destinations = [value.destination for value in values]
    for i, value in enumerate(values):
        destination = value.destination
        if value.delete and value.source is not None:
            raise ValueError(f"Deleted target cannot have a source: {destination}")
        if (
            ".." in destination.parts
            or destination.name == config.DIR_VERSIONS
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
    return values


def _publish_targets(publications: Iterable[TargetPublication], deploy_root: Path) -> None:
    """Publish or unlink independent targets with one durable journal.

    Shared directories are containers only. Each publication versions the named file
    or directory beside that container, matching the long-standing ``dist`` model.

    :param publications: Prepared target changes and centralized ownership state.
    :param deploy_root: Deployment boundary owning publication records.
    :return: None.
    :raises RecoveryRequired: If earlier work is pending or active state changed.
    :raises OSError: If preparation, activation, or durable metadata writes fail.
    :raises ValueError: If target paths overlap or a publication is inconsistent.
    """
    store = Store(deploy_root)
    store.ensure_clean()
    active_targets = store.target_records()
    values = list(publications)
    if not values:
        return
    identifier = uuid.uuid4().hex
    previous_snapshot = store.current()
    prepared = []
    selections: Dict[str, Optional[str]] = {}
    for value in values:
        destination = value.destination
        target_path = _record_path(destination, store.root)
        previous = observed_link(destination)
        if previous is not None and not value.delete and destination not in active_targets:
            raise RecoveryRequired(
                f"Destination is managed by another deployment or is unrecorded: {destination}"
            )
        if previous is not None and not (destination.parent / previous).exists():
            raise RecoveryRequired(f"Previous revision is missing: {destination}")
        if value.delete:
            if previous is None:
                continue
            revision = None
            link = None
        elif value.source is None:
            if previous is None:
                raise RecoveryRequired(f"Cannot update absent target metadata: {destination}")
            revision = destination.parent / previous
            link = previous
        else:
            reserved = []
            for record in store.target_revision_records(target_path).values():
                payload = Path(record["payload"])
                reserved.append(payload if payload.is_absolute() else store.root / payload)
            revision = prepare_revision(value.source, destination, value.reuse, reserved)
            link = os.path.relpath(revision, destination.parent)
        prepared.append(
            {
                "destination": str(destination),
                "before": previous,
                "after": link,
                "allow_absent": platform.system() == "Windows",
            }
        )
        if revision is None:
            selections[target_path] = None
            continue
        payload_path = _record_path(revision, store.root)
        target_id = hashlib.sha256(target_path.encode()).hexdigest()
        revision_record = f"targets/{target_id}/revisions/{identifier}.json"
        store.write(
            revision_record,
            {
                "schema": 1,
                "destination": target_path,
                "payload": payload_path,
                "kind": "directory" if revision.is_dir() else "file",
                "id": identifier,
                "inventory": _payload_inventory(revision),
                "state": value.state or {},
            },
        )
        selections[target_path] = revision_record
    if not prepared:
        return
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
            if change["after"] is None:
                destination.unlink()
                sync_directory(destination.parent)
            elif change["after"] != change["before"]:
                replace_link(destination, change["after"])
        store.write("current.json", {"schema": 1, "snapshot": identifier})
        atomic_write(store.directory / config.DISTMAN_EPOCH_FILE, f"{time.time_ns()}\n")
        journal["phase"] = "committed"
        store.write(f"operations/{identifier}.json", journal)
    except BaseException:
        try:
            _recover_publication(deploy_root)
        except BaseException as recovery_error:
            raise RecoveryRequired(
                f"Publication interrupted; recovery is required at {deploy_root}: "
                f"{recovery_error}"
            ) from recovery_error
        raise
    store.remove("pending.json")


def publish_directories(changes: Iterable[Tuple[Path, Path]], deploy_root: Path) -> None:
    """Publish directory pairs through the generic per-target journal.

    :param changes: Staged-source/destination directory pairs.
    :param deploy_root: Deployment boundary owning publication records.
    :return: None.
    """
    publish_targets(
        (
            TargetPublication(destination=destination, source=source)
            for source, destination in changes
        ),
        deploy_root,
    )
