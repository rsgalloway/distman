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

"""Remove centrally recorded package targets from a deployment."""

import argparse
import copy
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from distman import util
from distman.deployment import TargetPublication, publish_targets
from distman.install import InstallError
from distman.install.command import _active_targets
from distman.install.models import InstalledArtifact
from distman.install.ownership import check_script_pairs, target_requirements
from distman.install.targets import Target
from distman.logger import log
from distman.metadata import Store, sync_directory
from distman.operations import operation
from distman.policy import Policy


def requests(values: Iterable[str]) -> List[Requirement]:
    """Parse unambiguous distribution removal selectors.

    :param values: Distribution names or exact version pins.
    :return: Validated package selectors.
    :raises InstallError: If a selector includes ranges, extras, markers, or URLs.
    """
    result = []
    for value in values:
        requirement = Requirement(value)
        specs = list(requirement.specifier)
        if (
            requirement.url
            or requirement.marker
            or requirement.extras
            or len(specs) > 1
            or any(spec.operator != "==" or "*" in spec.version for spec in specs)
        ):
            raise InstallError("Uninstall accepts package names or exact NAME==VERSION pins")
        result.append(requirement)
    return result


def runtime_labels(args: argparse.Namespace, records: Iterable[InstalledArtifact]) -> Set[str]:
    """Select installed runtime labels without defaulting to the host runtime.

    :param args: Optional platform, architecture, and Python filters.
    :param records: Installed records supplying the runtime inventory.
    :return: Installed labels matching every supplied filter.
    :raises InstallError: If a runtime filter is invalid.
    """
    platforms = None
    if args.platform and args.platform != "all":
        platforms = {
            {"osx": "darwin", "win64": "windows"}.get(value, value)
            for value in args.platform.split(",")
        }
        if not platforms <= {"linux", "windows", "darwin"}:
            raise InstallError(f"Unsupported platform: {args.platform}")
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(args.arch, args.arch)
    if arch and arch not in ("x86_64", "arm64"):
        raise InstallError(f"Unsupported architecture: {arch}")
    versions = set(args.python.split(",")) if args.python else None
    if versions and any(not re.fullmatch(r"3\.(?:[8-9]|[1-9][0-9])", value) for value in versions):
        raise InstallError("Expected CPython major.minor >= 3.8")
    selected = set()
    for record in records:
        for label in record["targets"]:
            system, architecture, python = label.split("-")
            version = python[2] + "." + python[3:]
            if (
                (platforms is None or system in platforms)
                and (not arch or architecture == arch)
                and (versions is None or version in versions)
            ):
                selected.add(label)
    return selected


def validate_removal(
    before: List[InstalledArtifact], after: List[InstalledArtifact], force: bool = False
) -> None:
    """Reject newly broken dependencies of retained package/runtime installations.

    :param before: Original package records.
    :param after: Proposed retained package records.
    :param force: Allow newly broken dependencies after reporting every blocker.
    :return: None.
    :raises InstallError: If unforced removal breaks retained dependencies.
    """
    check_script_pairs(before)
    check_script_pairs(after)
    withdrawn = {
        (record["name"], record["version"], label)
        for record in before
        for label in record["targets"]
        if record.get("component") != "scripts"
    } - {
        (record["name"], record["version"], label)
        for record in after
        for label in record["targets"]
        if record.get("component") != "scripts"
    }
    blockers = set()
    labels = {label for record in after for label in record["targets"]}
    for label in sorted(labels):
        system, arch, python = label.split("-")
        pending = [
            record
            for record in after
            if record.get("component") != "scripts" and label in record["targets"]
        ]
        visited = set()
        while pending:
            record = pending.pop()
            identity = id(record)
            if identity in visited:
                continue
            visited.add(identity)
            target = Target(
                system,
                arch,
                python[2] + "." + python[3:],
                python_full=record["compatibility"][label],
            )
            for requirement in target_requirements(record, target):
                name = canonicalize_name(requirement.name)
                old = [
                    candidate
                    for candidate in before
                    if candidate["name"] == name
                    and candidate.get("component") != "scripts"
                    and label in candidate["targets"]
                    and requirement.specifier.contains(candidate["version"], prereleases=True)
                ]
                retained = [
                    candidate
                    for candidate in after
                    if candidate["name"] == name
                    and candidate.get("component") != "scripts"
                    and label in candidate["targets"]
                    and (candidate["name"], candidate["version"], label) not in withdrawn
                    and requirement.specifier.contains(candidate["version"], prereleases=True)
                ]
                if old and not retained:
                    removed = ", ".join(
                        sorted(
                            {f"{candidate['name']}=={candidate['version']}" for candidate in old}
                        )
                    )
                    blockers.add(
                        f"  {record['name']}=={record['version']} requires {requirement} "
                        f"for {label}; removal would withdraw {removed}"
                    )
                pending.extend(retained)
    if blockers and force:
        log.warning(
            "WARNING: --force allows removal despite retained dependency requirements:\n%s"
            "\nDependent packages will remain installed and may stop working.",
            "\n".join(sorted(blockers)),
        )
    elif blockers:
        raise InstallError(
            "Removal would break packages that remain installed:\n"
            + "\n".join(sorted(blockers))
            + "\nNo packages were removed; deployment targets are unchanged."
            + "\nUse --force to allow removal despite these dependency requirements."
        )


def _selection_updates(
    artifacts: Dict[str, InstalledArtifact],
    selectors: List[Requirement],
    labels: Set[str],
) -> Dict[str, Optional[InstalledArtifact]]:
    """Build ownership updates for matching package/runtime selections.

    :param artifacts: Active artifacts indexed by centralized owner identifier.
    :param selectors: Package names and optional exact versions.
    :param labels: Runtime labels selected for removal.
    :return: Selected owners mapped to retained records or None for complete removal.
    """
    updates = {}
    for owner, record in artifacts.items():
        if not any(
            canonicalize_name(selector.name) == record["name"]
            and selector.specifier.contains(record["version"], prereleases=True)
            for selector in selectors
        ):
            continue
        removed = set(record["targets"]) & labels
        if not removed:
            continue
        remaining = set(record["targets"]) - removed
        if remaining:
            retained = copy.deepcopy(record)
            retained["targets"] = sorted(remaining)
            retained["compatibility"] = {
                label: version
                for label, version in record["compatibility"].items()
                if label in remaining
            }
            if record.get("component") == "scripts":
                platforms = {label.split("-")[0] for label in remaining}
                retained["managed_targets"] = [
                    target
                    for target in record.get("managed_targets", [])
                    if not (
                        (target.endswith(".exe") and "windows" not in platforms)
                        or (not target.endswith(".exe") and not platforms & {"linux", "darwin"})
                    )
                ]
            updates[owner] = retained
        else:
            updates[owner] = None
        log.info(
            "Planned removal: %s==%s [%s]",
            record["name"],
            record["version"],
            ", ".join(sorted(removed)),
        )
    return updates


def _plan_targets(
    targets: dict,
    updates: Dict[str, Optional[InstalledArtifact]],
    temporary: Path,
) -> Tuple[List[TargetPublication], List[Path]]:
    """Translate ownership updates into target unlinks or private-boundary revisions.

    :param targets: Active centralized target records.
    :param updates: Selected owner records after removal.
    :param temporary: Workspace for revised multi-owner package boundaries.
    :return: Publications and targets that will be fully unlinked.
    :raises InstallError: If a non-directory target unexpectedly has multiple owners.
    """
    publications = []
    unlinked = []
    for number, (destination, target) in enumerate(targets.items()):
        state = target.get("state", {}).get("artifacts", {})
        affected = set(state) & set(updates)
        if not affected:
            continue
        retained = dict(state)
        removed = []
        for owner in affected:
            replacement = updates[owner]
            if replacement is None or str(destination) not in replacement.get(
                "managed_targets", []
            ):
                removed.append(state[owner])
                retained.pop(owner)
            else:
                retained[owner] = replacement
        if not retained:
            publications.append(TargetPublication(destination, delete=True))
            unlinked.append(destination)
            continue
        if not removed:
            publications.append(TargetPublication(destination, state={"artifacts": retained}))
            continue
        if not destination.is_dir():
            raise InstallError(f"Cannot remove one owner from file target: {destination}")
        staged = temporary / f"target-{number}"
        shutil.copytree(destination, staged, ignore=shutil.ignore_patterns("__pycache__"))
        for record in removed:
            for name in record["files"]:
                path = staged / name
                if path.is_file():
                    path.unlink()
        for path in sorted(staged.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        publications.append(TargetPublication(destination, staged, {"artifacts": retained}))
    return publications, unlinked


def _selected_payloads(
    store: Store,
    selectors: List[Requirement],
    args: argparse.Namespace,
    releasing: Iterable[Path] = (),
) -> Set[Path]:
    """Find inactive retained payloads wholly owned by selected packages and runtimes.

    :param store: Deployment metadata store containing target history.
    :param selectors: Package names and optional exact versions to delete.
    :param args: Optional platform, architecture, and Python filters.
    :param releasing: Active destinations that this operation will unlink.
    :return: Retained payload paths safe to delete after publication.
    :raises InstallError: If a runtime filter is invalid.
    :raises RecoveryRequired: If deployment history is invalid.
    """
    released = {Path(path) for path in releasing}
    active_payloads = set()
    for destination, record in store.target_records().items():
        if destination in released:
            continue
        payload = Path(record["payload"])
        active_payloads.add(payload if payload.is_absolute() else store.root / payload)
    selected = set()
    for record in store.target_history_records().values():
        state = record.get("state", {})
        artifacts = state.get("artifacts", {}) if isinstance(state, dict) else {}
        if not isinstance(artifacts, dict) or not artifacts:
            continue
        matches = []
        for artifact in artifacts.values():
            if not isinstance(artifact, dict):
                matches.append(False)
                continue
            package_match = any(
                canonicalize_name(selector.name) == artifact.get("name")
                and selector.specifier.contains(artifact.get("version", ""), prereleases=True)
                for selector in selectors
            )
            labels = runtime_labels(args, [artifact]) if package_match else set()
            matches.append(package_match and labels == set(artifact.get("targets", [])))
        if not matches or not all(matches):
            continue
        payload = Path(record["payload"])
        if not payload.is_absolute():
            payload = store.root / payload
        if payload not in active_payloads and os.path.lexists(payload):
            selected.add(payload)
    return selected


def _delete_history(payloads: Iterable[Path]) -> int:
    """Delete selected inactive payload revisions.

    :param payloads: Exact retained revision files or directories to remove.
    :return: Number of payload revisions removed.
    :raises OSError: If retained payloads cannot be removed.
    """
    removed = 0
    parents = set()
    for payload in sorted(set(payloads)):
        if payload.is_dir() and not payload.is_symlink():
            shutil.rmtree(payload)
        elif payload.exists() or payload.is_symlink():
            payload.unlink()
        else:
            continue
        parents.add(payload.parent)
        removed += 1
    for parent in parents:
        sync_directory(parent)
    return removed


def uninstall(args: argparse.Namespace) -> int:
    """Remove selected package targets through centralized deployment metadata.

    :param args: Package selectors, runtime filters, dry-run and confirmation options.
    :return: Zero on success or already absent, one if confirmation is declined.
    :raises InstallError: If selection, ownership, or retained dependencies are unsafe.
    :raises OSError: If publication fails.
    """
    selectors = requests(args.packages)
    policy = Policy()
    log.info("Effective deployment root: %s", policy.deploy_root)
    with operation(policy.deploy_root, read_only=args.dryrun):
        store = Store(policy.deploy_root)
        targets, artifacts = _active_targets(store)
        records = list(artifacts.values())
        labels = runtime_labels(args, records)
        updates = _selection_updates(artifacts, selectors, labels)
        proposed = dict(artifacts)
        for owner, replacement in updates.items():
            if replacement is None:
                proposed.pop(owner, None)
            else:
                proposed[owner] = replacement
        validate_removal(records, list(proposed.values()), force=args.force)
        with tempfile.TemporaryDirectory(prefix="distman-uninstall-") as work:
            publications, unlinked = _plan_targets(targets, updates, Path(work))
            payloads = (
                _selected_payloads(store, selectors, args, unlinked) if args.delete else set()
            )
            if args.dryrun:
                log.info(
                    "Dry run: %d target(s), %d retained payload(s); "
                    "deployment targets unchanged",
                    len(publications),
                    len(payloads),
                )
                return 0
            if not publications and not payloads:
                log.info("Unchanged: selected packages/runtimes are not installed")
                return 0
            if not args.yes and not util.yesNo(f"Remove packages from {policy.deploy_root}?"):
                return 1
            if publications:
                publish_targets(publications, policy.deploy_root)
            deleted = _delete_history(payloads) if args.delete else 0
            message = f"Uninstalled: {len(publications)} target(s)"
            if args.delete:
                message += f"; deleted {deleted} retained payload(s)"
            log.info(message)
    return 0
