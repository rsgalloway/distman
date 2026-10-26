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

"""Acquire, compose, validate, and publish complete installation revisions."""

import argparse
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

from distman import util
from distman.deployment import publish_directories
from distman.install import InstallError
from distman.install.models import Changes, Groups, InstalledArtifact, InstallState, Selection
from distman.install.ownership import (
    check_dependencies,
    check_script_pairs,
    compose,
    inventory,
    locks,
    marker_environment,
    read_state,
    same_state,
)
from distman.install.projects import (
    Project,
    distribute_project,
    distribution_paths,
    prepare_arguments,
)
from distman.install.targets import Target, expand_targets
from distman.install.wheels import acquire, build_project, stage, staged_path
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
    payloads = {}
    for number, target in enumerate(targets):
        for artifact in acquire(args, target, temporary / f"download-{number}", project):
            provenance = Selection(
                **artifact.provenance,
                pure=artifact.pure,
                targets=[target.label],
                acquisition="pip resolution report",
                library_boundary="",
                library_path="",
                scripts_path="",
                component="library",
                separate_scripts=False,
            )
            if artifact.name == excluded_name:
                print(f"Project {artifact.name}: publication is owned by dist.json")
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
            identity = (
                provenance["sha256"],
                target.platform,
                import_root,
                scripts_root,
                destination,
            )
            if identity not in payloads:
                payload = temporary / f"wheel-{len(payloads)}"
                stage(artifact, payload, target, import_root, scripts_root, destination)
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
                    continue
                groups[boundary][owner] = (owner, contents, selection, Path("."))
    include_dependencies(groups, policy, targets)
    destinations = sorted(groups)
    for i, destination in enumerate(destinations):
        for other in destinations[i + 1 :]:
            if destination in other.parents or other in destination.parents:
                raise InstallError(f"Overlapping deployment targets: {destination} and {other}")
    return groups, project_records


def include_companions(groups: Groups, states: Optional[Dict[Path, InstallState]] = None) -> None:
    """Include existing paired roots in locking and validation without adding artifacts.

    A custom bin can serve multiple independent library roots. Their receipts
    must all be checked when updating that bin, even if they are not being upgraded.

    :param groups: Publication groups to extend with read-only companion boundaries.
    :param states: Optional cache of integrity-checked receipts during discovery.
    :return: None; empty addition groups are inserted for existing companions.
    :raises InstallError: If recorded companions are invalid or their payloads are corrupt.
    """
    states = {} if states is None else states
    pending = list(groups)
    visited = set()
    while pending:
        destination = pending.pop()
        if destination in visited:
            continue
        visited.add(destination)
        if destination not in states:
            states[destination] = read_state(destination)
        for record in states[destination]["artifacts"].values():
            if not record.get("separate_scripts"):
                continue
            for field in ("library_boundary", "scripts_path"):
                boundary = Path(record[field])
                if not boundary.is_absolute() or ".." in boundary.parts:
                    raise InstallError(f"Invalid paired deployment boundary: {boundary}")
                if boundary not in groups:
                    groups[boundary] = {}
                    pending.append(boundary)


def include_dependencies(groups: Groups, policy: Policy, targets: Sequence[Target]) -> None:
    """Include retained dependency roots for validation without republishing them.

    Search only configured package boundaries, following requirements transitively.
    Planned versions remain authoritative; discovery cannot override a conflict.
    All discovered roots are read again under the publication locks.

    :param groups: Planned additions, extended with read-only dependency boundaries.
    :param policy: Resolved path templates used to locate installed distributions.
    :param targets: Requested runtimes, supplemented by retained receipt runtimes.
    :return: None; groups is updated in place.
    :raises InstallError: If receipts are corrupt or installed candidates are ambiguous.
    """
    states: Dict[Path, InstallState] = {}
    runtimes = {target.label: target for target in targets}
    while True:
        include_companions(groups, states)
        records = []
        for root, additions in groups.items():
            effective = dict(states[root]["artifacts"])
            effective.update({key: addition[2] for key, addition in additions.items()})
            records.extend(effective.values())
        for record in records:
            for label in record["targets"]:
                system, arch, python = label.split("-")
                runtimes.setdefault(label, Target(system, arch, python[2] + "." + python[3:]))
        discovered = set()
        for target in runtimes.values():
            selected = {
                record["name"]: record
                for record in records
                if record.get("component") != "scripts"
                and (record["pure"] or target.label in record["targets"])
            }
            requirements = defaultdict(list)
            for record in selected.values():
                for text in record["requires_dist"]:
                    requirement = Requirement(text)
                    if requirement.marker:
                        if any(
                            field in str(requirement.marker)
                            for field in ("platform_release", "platform_version")
                        ):
                            raise InstallError(f"Cannot evaluate target OS release marker: {text}")
                        if not requirement.marker.evaluate(marker_environment(target)):
                            continue
                    name = canonicalize_name(requirement.name)
                    if name not in selected:
                        requirements[name].append(requirement)
            for name, constraints in requirements.items():
                candidates = set()
                for root in policy.installed_roots(name, target):
                    if root in groups:
                        continue
                    if root not in states:
                        states[root] = read_state(root)
                    for record in states[root]["artifacts"].values():
                        if (
                            record["name"] == name
                            and record.get("component") != "scripts"
                            and (record["pure"] or target.label in record["targets"])
                            and all(
                                req.specifier.contains(record["version"], prereleases=True)
                                for req in constraints
                            )
                        ):
                            candidates.add(root)
                if len(candidates) > 1:
                    raise InstallError(
                        f"Multiple installed versions satisfy {name} for {target.label}; "
                        "include an explicit package version in the install request"
                    )
                discovered.update(candidates)
        if not discovered:
            return
        for root in sorted(discovered):
            groups[root] = {}


def plan(groups: Groups, temporary: Path, force: bool) -> Tuple[Changes, List[InstalledArtifact]]:
    """Compose proposed revisions in temporary storage and detect unchanged targets.

    :param groups: Staged wheel additions grouped by active deployment destination.
    :param temporary: Workspace in which new composed revision directories will be created.
    :param force: If True, include unchanged targets in the publication list.
    :return: Staged-source/destination pairs to publish and receipts for all composed artifacts.
    :raises InstallError: If active integrity or proposed file ownership validation fails.
    """
    changes, records = [], []
    for number, (destination, additions) in enumerate(groups.items()):
        current = read_state(destination)
        if not additions:
            records.extend(current["artifacts"].values())
            continue
        for key, _, provenance, _ in additions.values():
            old = current["artifacts"].get(key)
            if old:
                if any(
                    old.get(field, provenance[field]) != provenance[field]
                    for field in ("library_path", "scripts_path")
                ):
                    raise InstallError(
                        "Changing an existing library/scripts layout requires "
                        f"a separate deployment root: {destination}"
                    )
                provenance["targets"] = sorted(set(old["targets"] + provenance["targets"]))
            before = old["version"] if old else "absent"
            print(f"{key}: {before} -> {provenance['version']} at {destination}")
        staged = temporary / f"revision-{number}"
        state = compose(destination, staged, list(additions.values()))
        records.extend(state["artifacts"].values())
        if (force and additions) or not same_state(destination, state):
            changes.append((staged, destination))
        else:
            print(f"Unchanged: {destination}")
    return changes, records


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
    if project:
        print(f"Local project: {project.path}")
        print(f"Effective deployment root: {policy.deploy_root}")
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
                raise InstallError("Local dist.json preflight failed; no packages were published")
        with locks(groups, args.dryrun):
            changes, records = plan(groups, temporary, args.force)
            records.extend(project_records)
            check_script_pairs(records)
            check_targets = {target.label: target for target in targets}
            for record in records:
                for label in record["targets"]:
                    system, arch, python = label.split("-")
                    check_targets.setdefault(
                        label, Target(system, arch, python[2] + "." + python[3:])
                    )
            for target in check_targets.values():
                check_dependencies(records, target)
            if args.dryrun:
                print(f"Dry run: {len(changes)} revision(s); deployment directories unchanged")
                return 0
            if (changes or project) and not args.yes:
                if not util.yesNo(f"Publish installation to {policy.deploy_root}?"):
                    return 1
            publish_directories(changes, policy.deploy_root)
            print(f"Installed: {len(changes)} revision(s)")
            if project and project.distribution:
                return distribute_project(project, args, policy.values, dryrun=False)
    return 0
