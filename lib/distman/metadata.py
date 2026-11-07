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

"""Durable JSON records for incremental publication history and recovery."""

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Optional

from distman import config


class RecoveryRequired(OSError):
    """Publication cannot safely continue until recorded state is recovered."""


def sync_directory(path: Path) -> None:
    """Flush directory entries where the platform supports directory fsync.

    :param path: Existing directory whose entries were changed.
    :return: None.
    :raises OSError: If a supported filesystem rejects the durability request.
    """
    if os.name != "nt":
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def durable_directory(path: Path) -> None:
    """Create missing directories and flush their parent entries.

    :param path: Directory to create.
    :return: None.
    :raises OSError: If directory creation or flushing fails.
    """
    if path.is_dir():
        return
    durable_directory(path.parent)
    path.mkdir(exist_ok=True)
    sync_directory(path.parent)


def atomic_write(path: Path, content: str) -> None:
    """Replace a text record after flushing its temporary contents.

    :param path: Destination record on the deployment filesystem.
    :param content: UTF-8 text to persist.
    :return: None.
    :raises OSError: If writing, replacing, or flushing fails.
    """
    durable_directory(path.parent)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = stat.S_IMODE(path.parent.stat().st_mode) & 0o666
    descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=str(path.parent))
    try:
        os.chmod(temporary, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    """Access schema-versioned publication records beneath a deployment root.

    :param root: Deployment boundary owning these records.
    """

    def __init__(self, root: Path) -> None:
        """Initialize paths without creating deployment state.

        :param root: Deployment boundary owning these records.
        """
        self.root = root.resolve()
        self.directory = self.root / config.DISTMAN_META_DIR

    def read(self, relative: str) -> Optional[dict]:
        """Read a supported record, distinguishing absent from invalid metadata.

        :param relative: Internal record path relative to the metadata directory.
        :return: Record dictionary, or None if absent.
        :raises RecoveryRequired: If an existing record is corrupt or unsupported.
        """
        path = self.directory / relative
        try:
            value = json.loads(path.read_text())
            if not isinstance(value, dict) or value.get("schema") != 1:
                raise ValueError("unsupported record schema")
            return value
        except FileNotFoundError:
            return None
        except (ValueError, TypeError) as exc:
            raise RecoveryRequired(f"Invalid deployment metadata: {path}: {exc}") from exc

    def write(self, relative: str, value: dict) -> None:
        """Persist one versioned metadata record.

        :param relative: Internal record path relative to the metadata directory.
        :param value: Schema-versioned record to serialize.
        :return: None.
        :raises OSError: If the record cannot be durably replaced.
        """
        atomic_write(self.directory / relative, json.dumps(value, indent=2, sort_keys=True) + "\n")

    def remove(self, relative: str) -> None:
        """Remove an optional record and flush the directory entry.

        :param relative: Internal record path relative to the metadata directory.
        :return: None.
        :raises OSError: If removal or directory flushing fails.
        """
        path = self.directory / relative
        if path.exists():
            path.unlink()
            sync_directory(path.parent)

    def current(self) -> Optional[str]:
        """Read the selected snapshot identifier without traversing history.

        :return: Current identifier, or None before history is initialized.
        :raises RecoveryRequired: If the pointer or its snapshot is invalid.
        """
        record = self.read("current.json")
        if record is None:
            return None
        identifier = record.get("snapshot")
        self.validate_id(identifier)
        snapshot = self.read(f"snapshots/{identifier}.json")
        if snapshot is None or snapshot.get("id") != identifier:
            raise RecoveryRequired(f"Missing or mismatched snapshot: {identifier}")
        return identifier

    @staticmethod
    def validate_id(identifier: str) -> None:
        """Reject malformed identifiers before constructing record paths.

        :param identifier: Expected lowercase UUID hex identifier.
        :return: None.
        :raises RecoveryRequired: If the identifier is invalid.
        """
        if not isinstance(identifier, str) or re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
            raise RecoveryRequired(f"Invalid deployment record identifier: {identifier!r}")

    def selections(self) -> dict:
        """Resolve the latest recorded selection for every deployment target.

        Snapshot entries are incremental. Newer entries win while parent snapshots
        supply targets that were unchanged by later operations. A ``None`` value
        records an intentionally unlinked target.

        :return: Root-relative or absolute target paths mapped to revision-record paths or None.
        :raises RecoveryRequired: If the snapshot chain is corrupt or cyclic.
        """
        identifier = self.current()
        selections = {}
        visited = set()
        while identifier is not None:
            if identifier in visited:
                raise RecoveryRequired("Cyclic deployment snapshot history")
            visited.add(identifier)
            snapshot = self.read(f"snapshots/{identifier}.json")
            if snapshot is None or snapshot.get("id") != identifier:
                raise RecoveryRequired(f"Missing or mismatched snapshot: {identifier}")
            changes = snapshot.get("changes")
            if not isinstance(changes, dict):
                raise RecoveryRequired(f"Invalid deployment snapshot: {identifier}")
            for target, revision in changes.items():
                valid_revision = revision is None or (
                    isinstance(revision, str)
                    and re.fullmatch(
                        r"targets/[0-9a-f]{64}/revisions/[0-9a-f]{32}\.json",
                        revision,
                    )
                    is not None
                )
                target_path = Path(target) if isinstance(target, str) else None
                if (
                    target_path is None
                    or (not target_path.is_absolute() and ".." in target_path.parts)
                    or not valid_revision
                ):
                    raise RecoveryRequired(f"Invalid target selection: {identifier}")
                selections.setdefault(target, revision)
            identifier = snapshot.get("parent")
            if identifier is not None:
                self.validate_id(identifier)
        return selections

    def target_history_records(self) -> dict:
        """Read every target revision referenced by the current snapshot history.

        :return: Revision-record paths mapped to validated revision dictionaries.
        :raises RecoveryRequired: If the snapshot chain or a revision record is invalid.
        """
        identifier = self.current()
        references = set()
        visited = set()
        while identifier is not None:
            if identifier in visited:
                raise RecoveryRequired("Cyclic deployment snapshot history")
            visited.add(identifier)
            snapshot = self.read(f"snapshots/{identifier}.json")
            if snapshot is None or snapshot.get("id") != identifier:
                raise RecoveryRequired(f"Missing or mismatched snapshot: {identifier}")
            changes = snapshot.get("changes")
            if not isinstance(changes, dict):
                raise RecoveryRequired(f"Invalid deployment snapshot: {identifier}")
            references.update(reference for reference in changes.values() if reference is not None)
            identifier = snapshot.get("parent")
            if identifier is not None:
                self.validate_id(identifier)
        records = {}
        for reference in references:
            if (
                not isinstance(reference, str)
                or re.fullmatch(r"targets/[0-9a-f]{64}/revisions/[0-9a-f]{32}\.json", reference)
                is None
            ):
                raise RecoveryRequired(f"Invalid target revision reference: {reference!r}")
            record = self.read(reference)
            if record is None:
                raise RecoveryRequired(f"Missing target revision: {reference}")
            destination = record.get("destination")
            payload = record.get("payload")
            if (
                not isinstance(destination, str)
                or not isinstance(payload, str)
                or (not Path(destination).is_absolute() and ".." in Path(destination).parts)
                or (not Path(payload).is_absolute() and ".." in Path(payload).parts)
            ):
                raise RecoveryRequired(f"Invalid target revision: {reference}")
            records[reference] = record
        return records

    def target_records(self) -> dict:
        """Read active centralized target revision records.

        :return: Absolute destination paths mapped to validated revision dictionaries.
        :raises RecoveryRequired: If a selected revision record is missing or inconsistent.
        """
        records = {}
        for target, reference in self.selections().items():
            if reference is None:
                continue
            record = self.read(reference)
            payload = Path(record.get("payload", "")) if record is not None else None
            if (
                record is None
                or record.get("destination") != target
                or payload is None
                or (not payload.is_absolute() and ".." in payload.parts)
            ):
                raise RecoveryRequired(f"Missing or mismatched target revision: {target}")
            destination = Path(target)
            if not destination.is_absolute():
                destination = self.root / destination
            records[destination] = record
        return records

    def pending(self) -> Optional[dict]:
        """Read and validate the operation blocking further publication.

        :return: Pending operation or None.
        :raises RecoveryRequired: If the pointer or operation is invalid.
        """
        pointer = self.read("pending.json")
        if pointer is None:
            return None
        identifier = pointer.get("operation")
        self.validate_id(identifier)
        record = self.read(f"operations/{identifier}.json")
        if (
            record is None
            or record.get("id") != identifier
            or record.get("root") != str(self.root)
            or record.get("phase") not in ("prepared", "committed", "restored")
            or not isinstance(record.get("changes"), list)
            or not record.get("changes")
            or "previous_snapshot" not in record
        ):
            raise RecoveryRequired(f"Invalid publication journal: {identifier}")
        self.validate_id(record.get("snapshot"))
        if record.get("previous_snapshot") is not None:
            self.validate_id(record["previous_snapshot"])
        destinations = set()
        for change in record["changes"]:
            if not isinstance(change, dict) or "before" not in change:
                raise RecoveryRequired("Invalid publication change")
            destination = change.get("destination")
            before, after = change.get("before"), change.get("after")
            if (
                not isinstance(destination, str)
                or not Path(destination).is_absolute()
                or ".." in Path(destination).parts
                or destination in destinations
                or (before is not None and (not isinstance(before, str) or not before))
                or not isinstance(change.get("allow_absent", False), bool)
                or (after is not None and (not isinstance(after, str) or not after))
                or (before is None and after is None)
            ):
                raise RecoveryRequired("Invalid publication destination or link")
            path = Path(destination)
            if (
                path == self.root
                or path in self.root.parents
                or path == self.directory
                or self.directory in path.parents
                or any(
                    path in Path(other).parents or Path(other) in path.parents
                    for other in destinations
                )
            ):
                raise RecoveryRequired("Overlapping or reserved publication destination")
            destinations.add(destination)
        return record

    def ensure_clean(self) -> None:
        """Refuse new mutations while an earlier publication needs recovery.

        :return: None.
        :raises RecoveryRequired: If an operation is pending or metadata is corrupt.
        """
        self.current()
        pending = self.pending()
        if pending is not None:
            raise RecoveryRequired(
                f"Publication {pending['id']} needs recovery at {self.root}; "
                "run distman recover --root with this deployment path."
            )
