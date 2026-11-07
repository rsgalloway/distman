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

"""Acquire wheels, plan independent targets, validate, and publish them."""

import argparse
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from packaging.utils import parse_wheel_filename

from distman import util
from distman.deployment import TargetPublication, publish_targets
from distman.install import InstallError
from distman.install.models import Groups, InstalledArtifact, Selection
from distman.install.ownership import check_dependencies, check_script_pairs
from distman.install.projects import (
    Project,
    distribute_project,
    distribution_paths,
    prepare_arguments,
)
from distman.install.targets import Target, expand_targets
from distman.install.wheels import acquire, build_project, stage, staged_path
from distman.inventory import file_mode, inventory, sha256
from distman.logger import log
from distman.metadata import Store
from distman.operations import operation
from distman.policy import Policy


def collect(
    args: argparse.Namespace,
    policy: Policy,
    targets: Sequence[Target],
    temporary: Path,
    local_project: Optional[Project] = None,
) -> Tuple[Groups, List[InstalledArtifact]]:
    """Acquire wheels and group staged payloads by configured deployment boundary.

    :param args: Effective install options after local-project discovery.
    :param policy: Resolved installation policy used to compute import and target paths.
    :param targets: Runtime selections for which pip should acquire compatible wheels.
    :param temporary: Existing workspace for downloads, project builds, and staged wheel
        payloads.
    :param local_project: Optional project descriptor identifying custom dist.json ownership.
    :return: Grouped publication additions and validation-only receipts for dist-owned project
        wheels.
    :raises InstallError: If acquisition fails or configured paths/artifacts cannot coexist
        safely.
    """
    project = build_project(args, temporary / "project") if args.project else None
    excluded_name = (
        parse_wheel_filename(project.name)[0]
        if project and local_project and local_project.distribution
        else None
    )
    groups: Groups = defaultdict(dict)
    project_records: List[InstalledArtifact] = []
    _, installed_artifacts = _active_targets(Store(policy.deploy_root))
    payloads = {}
    for number, target in enumerate(targets):
        for artifact in acquire(args, target, temporary / f"download-{number}", project):
            provenance = Selection(
                **artifact.provenance,
                pure=artifact.pure,
                targets=[target.label],
                compatibility={target.label: target.compatibility_python},
                acquisition="pip resolution report",
                library_boundary="",
                library_path="",
                scripts_path="",
                component="library",
                separate_scripts=False,
            )
            if artifact.name == excluded_name:
                log.info("Project %s: publication is owned by dist.json", artifact.name)
                project_records.append(InstalledArtifact(**provenance, files={}))
                continue
            # pip may combine config files, environment and requirement-file indexes.
            # Do not invent an index of origin or persist credentials from these.
            import_root = policy.path(
                "DISTMAN_SHARED_LIB_PATH" if artifact.pure else "DISTMAN_RUNTIME_LIB_PATH",
                artifact,
                target,
            )
            destination = (
                import_root
                if artifact.pure
                else policy.path("DISTMAN_TARGET_PATH", artifact, target)
            )
            try:
                prefix = import_root.relative_to(destination)
            except ValueError as exc:
                raise InstallError(
                    f"Import path {import_root} must be within target {destination}"
                ) from exc
            if ".." in prefix.parts or ".." in destination.parts:
                raise InstallError("Installation paths must not contain '..'")
            scripts_root = policy.path(
                "DISTMAN_SHARED_BIN_PATH" if artifact.pure else "DISTMAN_RUNTIME_BIN_PATH",
                artifact,
                target,
            )
            if ".." in scripts_root.parts or scripts_root in destination.parents:
                raise InstallError(f"Invalid or overlapping scripts root: {scripts_root}")
            external_scripts = (
                scripts_root != destination and destination not in scripts_root.parents
            )
            provenance.update(
                library_boundary=str(destination),
                library_path=str(import_root),
                scripts_path=str(scripts_root),
                separate_scripts=external_scripts,
            )
            key = artifact.name if artifact.pure else f"{artifact.name}@{prefix.as_posix()}"
            launcher_platforms = {target.platform}
            if artifact.pure:
                previous = installed_artifacts.get(_owner(key, "library", destination))
                if previous:
                    provenance["targets"] = sorted(set(provenance["targets"] + previous["targets"]))
                    provenance["compatibility"] = {
                        **previous["compatibility"],
                        **provenance["compatibility"],
                    }
                    launcher_platforms.update(label.split("-")[0] for label in previous["targets"])
                for requested in targets:
                    if (
                        policy.path("DISTMAN_SHARED_LIB_PATH", artifact, requested) == import_root
                        and policy.path("DISTMAN_SHARED_BIN_PATH", artifact, requested)
                        == scripts_root
                    ):
                        launcher_platforms.add(requested.platform)
            identity = (
                provenance["sha256"],
                target.platform,
                import_root,
                scripts_root,
                destination,
            )
            if identity not in payloads:
                payload = temporary / f"wheel-{len(payloads)}"
                stage(
                    artifact,
                    payload,
                    target,
                    import_root,
                    scripts_root,
                    destination,
                    sorted(launcher_platforms),
                )
                payloads[identity] = payload
            payload = payloads[identity]
            pieces = [(destination, staged_path(payload, destination), key, "library")]
            if external_scripts:
                pieces.append((scripts_root, staged_path(payload, scripts_root), key, "scripts"))
            for boundary, contents, owner, component in pieces:
                selection = provenance.copy()
                selection["component"] = component
                previous = groups[boundary].get(owner)
                if previous:
                    if (
                        previous[2]["sha256"] != selection["sha256"]
                        or previous[2]["component"] != component
                        or previous[2]["library_path"] != selection["library_path"]
                        or inventory(previous[1]) != inventory(contents)
                    ):
                        raise InstallError(
                            f"Incompatible artifacts share {boundary}: {artifact.name}. "
                            "Use narrower runtime selection or separate configured paths."
                        )
                    previous[2]["targets"] = sorted(set(previous[2]["targets"] + [target.label]))
                    previous[2]["compatibility"].update(selection["compatibility"])
                    continue
                groups[boundary][owner] = (owner, contents, selection, Path("."))
    return groups, project_records


def _target_inventory(path: Path) -> dict:
    """Inventory one active or staged target.

    :param path: File or directory payload.
    :return: Relative file digests and modes.
    """
    if path.is_dir():
        return inventory(path)
    return {".": {"sha256": sha256(path), "mode": file_mode(path)}}


def _active_targets(store: Store) -> Tuple[dict, dict]:
    """Read and verify centralized target ownership.

    :param store: Deployment metadata store selected by the active policy.
    :return: Target records and deduplicated artifact ownership records.
    :raises InstallError: If active links, payloads, or ownership records disagree.
    """
    targets = store.target_records()
    artifacts = {}
    for destination, target in targets.items():
        if not destination.is_symlink() or not destination.exists():
            raise InstallError(f"Recorded target is missing: {destination}")
        payload = Path(target.get("payload", ""))
        if not payload.is_absolute():
            payload = store.root / payload
        expected_link = os.path.relpath(payload, destination.parent)
        if os.readlink(destination) != expected_link:
            raise InstallError(f"Recorded target selection changed: {destination}")
        if target.get("inventory") != _target_inventory(destination):
            raise InstallError(f"Installed content changed or is incomplete: {destination}")
        state = target.get("state", {})
        owned = state.get("artifacts", {})
        if not isinstance(owned, dict):
            raise InstallError(f"Invalid centralized ownership for {destination}")
        for owner, record in owned.items():
            previous = artifacts.get(owner)
            if previous is not None and previous != record:
                raise InstallError(f"Inconsistent centralized ownership record: {owner}")
            artifacts[owner] = record
    return targets, artifacts


def _owner(key: str, component: str, boundary: Path) -> str:
    """Build a stable ownership key for one package component and layout.

    :param key: Package or package/runtime identity.
    :param component: Library or scripts component.
    :param boundary: Configured logical publication root.
    :return: Stable centralized ownership identifier.
    """
    return f"{component}:{key}:{boundary}"


def _target_sources(source: Path, destination: Path):
    """Yield independently versioned targets from a staged shared component.

    Namespace-package directories remain ordinary containers. Their children are
    versioned independently so unrelated distributions can share the namespace.

    :param source: Staged component root.
    :param destination: Configured shared container root.
    :yields: Staged source and active target path pairs.
    """
    for child in sorted(source.iterdir()):
        target = destination / child.name
        if child.is_dir() and not child.name.endswith((".dist-info", ".data")):
            if not (child / "__init__.py").is_file():
                yield from _target_sources(child, target)
                continue
        yield child, target


def _copy_component(source: Path, destination: Path) -> None:
    """Merge one staged component into a private package boundary.

    :param source: Staged component tree.
    :param destination: Temporary complete package boundary.
    :return: None.
    :raises InstallError: If a file or directory collision is found.
    """
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            if target.exists() and not target.is_dir():
                raise InstallError(f"File/directory ownership conflict: {target}")
            target.mkdir(parents=True, exist_ok=True)
        elif target.exists():
            raise InstallError(f"File ownership conflict: {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def plan(
    groups: Groups, temporary: Path, force: bool, deploy_root: Path
) -> Tuple[List[TargetPublication], List[InstalledArtifact]]:
    """Convert staged wheels into package-level target publications.

    Shared roots remain ordinary directories. Pure-Python packages, metadata
    directories, and commands become independent targets. A binary package/version
    boundary remains one private target containing its selected runtime layouts.

    :param groups: Staged wheel components grouped by configured logical root.
    :param temporary: Workspace for private binary package revisions.
    :param force: Publish new payload revisions even when content is unchanged.
    :param deploy_root: Deployment root owning centralized target records.
    :return: Target publications and complete proposed artifact records.
    :raises InstallError: If ownership, integrity, or target collisions are unsafe.
    """
    store = Store(deploy_root)
    current_targets, current_artifacts = _active_targets(store)
    proposed_artifacts = dict(current_artifacts)
    publications: Dict[Path, TargetPublication] = {}
    planned_owners: Dict[Path, str] = {}
    binary_groups = []
    script_pairs = {
        (
            addition[2]["name"],
            addition[2]["library_path"],
            addition[2]["scripts_path"],
        )
        for additions in groups.values()
        for addition in additions.values()
        if addition[2]["component"] == "scripts" and any(addition[1].iterdir())
    }

    for boundary, additions in groups.items():
        for key, contents, original_selection, _ in additions.values():
            selection = original_selection.copy()
            component = selection["component"]
            pair = (selection["name"], selection["library_path"], selection["scripts_path"])
            selection["separate_scripts"] = pair in script_pairs
            if component == "scripts" and pair not in script_pairs:
                continue
            owner = _owner(key, component, boundary)
            old = current_artifacts.get(owner)
            if component == "library" and pair not in script_pairs:
                for old_owner, old_record in list(current_artifacts.items()):
                    if (
                        old_record.get("component") == "scripts"
                        and old_record["name"] == selection["name"]
                        and old_record.get("library_path") == selection["library_path"]
                        and old_record.get("scripts_path") == selection["scripts_path"]
                    ):
                        for obsolete in old_record.get("managed_targets", []):
                            target = Path(obsolete)
                            publications[target] = TargetPublication(target, delete=True)
                        proposed_artifacts.pop(old_owner, None)
            record = InstalledArtifact(**selection, files=inventory(contents))
            if old:
                record["targets"] = sorted(set(old["targets"] + record["targets"]))
                record["compatibility"] = {**old["compatibility"], **record["compatibility"]}
            if not selection["pure"] and component == "library":
                binary_groups.append((boundary, owner, contents, record))
                continue
            targets = list(_target_sources(contents, boundary))
            managed = [str(destination) for _, destination in targets]
            record["managed_targets"] = managed
            old_targets = set(old.get("managed_targets", [])) if old else set()
            for obsolete in sorted(old_targets - set(managed)):
                destination = Path(obsolete)
                publications[destination] = TargetPublication(destination, delete=True)
            for source, destination in targets:
                managed_ancestor = next(
                    (target for target in current_targets if target in destination.parents), None
                )
                if managed_ancestor is not None:
                    raise InstallError(
                        f"Target {destination} is beneath managed target {managed_ancestor}"
                    )
                existing = current_targets.get(destination)
                existing_owners = (
                    set(existing.get("state", {}).get("artifacts", {})) if existing else set()
                )
                if existing_owners and existing_owners != {owner}:
                    raise InstallError(f"Target is owned by another package: {destination}")
                if os.path.lexists(destination) and not destination.is_symlink():
                    raise InstallError(f"Refusing unmanaged destination: {destination}")
                other = planned_owners.get(destination)
                if other and other != owner:
                    raise InstallError(f"Package target collision at {destination}")
                planned_owners[destination] = owner
                state = {"artifacts": {owner: record}}
                if (
                    not force
                    and existing
                    and existing.get("inventory") == _target_inventory(source)
                    and existing.get("state") == state
                ):
                    continue
                publications[destination] = TargetPublication(
                    destination, source, state, reuse=not force
                )
            proposed_artifacts[owner] = record
            before = old["version"] if old else "absent"
            log.info("%s: %s -> %s at %s", key, before, record["version"], boundary)

    by_boundary = defaultdict(list)
    for item in binary_groups:
        by_boundary[item[0]].append(item)
    for number, (boundary, additions) in enumerate(by_boundary.items()):
        existing = current_targets.get(boundary)
        if os.path.lexists(boundary) and not boundary.is_symlink():
            raise InstallError(f"Refusing unmanaged destination: {boundary}")
        staged = temporary / f"binary-revision-{number}"
        if existing:
            shutil.copytree(boundary, staged, ignore=shutil.ignore_patterns("__pycache__"))
            state = dict(existing.get("state", {}).get("artifacts", {}))
        else:
            staged.mkdir(parents=True)
            state = {}
        for _, owner, contents, record in additions:
            previous = state.pop(owner, None)
            if previous:
                for name in previous["files"]:
                    path = staged / name
                    if path.is_file():
                        path.unlink()
            for path in sorted(staged.rglob("*"), key=lambda value: len(value.parts), reverse=True):
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            _copy_component(contents, staged)
            record["managed_targets"] = [str(boundary)]
            record["files"] = inventory(contents)
            state[owner] = record
            proposed_artifacts[owner] = record
            before = previous["version"] if previous else "absent"
            log.info(
                "%s: %s -> %s at %s", owner.split(":", 2)[1], before, record["version"], boundary
            )
        target_state = {"artifacts": state}
        if (
            not force
            and existing
            and existing.get("inventory") == inventory(staged)
            and existing.get("state") == target_state
        ):
            continue
        publications[boundary] = TargetPublication(boundary, staged, target_state, reuse=not force)

    return list(publications.values()), list(proposed_artifacts.values())


def install(args: argparse.Namespace) -> int:
    """Resolve policy once, validate the complete plan, then confirm and publish.

    :param args: Parsed install options containing inputs, runtime selection, and publication
        flags.
    :return: Zero for success or dry run, one for declined confirmation, or the legacy dist exit
        status.
    :raises InstallError: If project discovery, acquisition, ownership, or preflight validation
        fails.
    :raises OSError: If staging or publication cannot complete.
    """
    args, project = prepare_arguments(args)
    targets = expand_targets(args.platform, args.arch, args.python)
    policy = Policy()
    log.info("Effective deployment root: %s", policy.deploy_root)
    if project:
        log.info("Local project: %s", project.path)
    with operation(policy.deploy_root, read_only=args.dryrun):
        with tempfile.TemporaryDirectory(prefix="distman-install-") as work:
            temporary = Path(work)
            groups, project_records = (
                collect(args, policy, targets, temporary, project)
                if args.packages or args.requirement or args.project
                else ({}, [])
            )
            if project and project.distribution:
                for custom in distribution_paths(project, policy.values):
                    for package_root in groups:
                        if (
                            custom == package_root
                            or custom in package_root.parents
                            or package_root in custom.parents
                        ):
                            raise InstallError(
                                f"dist.json target overlaps package publication: {custom}"
                            )
                if distribute_project(project, args, policy.values, dryrun=True):
                    raise InstallError(
                        "Local dist.json preflight failed; no packages were published"
                    )
            changes, records = plan(groups, temporary, args.force, policy.deploy_root)
            records.extend(project_records)
            check_script_pairs(records)
            check_targets = {target.label: target for target in targets}
            for record in records:
                for label in record["targets"]:
                    system, arch, python = label.split("-")
                    check_targets.setdefault(
                        label,
                        Target(
                            system,
                            arch,
                            python[2] + "." + python[3:],
                            python_full=record["compatibility"][label],
                        ),
                    )
            for target in check_targets.values():
                check_dependencies(records, target)
            if args.dryrun:
                log.info("Dry run: %d target(s); deployment directories unchanged", len(changes))
                return 0
            if (changes or project) and not args.yes:
                if not util.yesNo(f"Publish installation to {policy.deploy_root}?", default=True):
                    return 1
            publish_targets(changes, policy.deploy_root)
            log.info("Installed: %d target(s)", len(changes))
            if project and project.distribution:
                return distribute_project(project, args, policy.values, dryrun=False)
        return 0
