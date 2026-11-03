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

"""Remove receipt-owned packages through reversible deployment revisions."""

import argparse
import configparser
import copy
import csv
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Set

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from distman import util
from distman.deployment import publish_directories
from distman.install import InstallError
from distman.install.command import include_companions
from distman.install.models import Changes, InstalledArtifact, InstallState
from distman.install.ownership import MANIFEST, check_script_pairs, read_state, target_requirements
from distman.install.targets import Target
from distman.inventory import sha256
from distman.locking import locks
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
    :param records: Installed receipts supplying the runtime inventory.
    :return: Installed labels matching every supplied filter.
    :raises InstallError: If a runtime filter is invalid.
    """
    platforms = None
    if args.platform and args.platform != "all":
        platforms = {
            {"osx": "darwin", "win64": "windows"}.get(p, p) for p in args.platform.split(",")
        }
        if not platforms <= {"linux", "windows", "darwin"}:
            raise InstallError(f"Unsupported platform: {args.platform}")
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(args.arch, args.arch)
    if arch and arch not in ("x86_64", "arm64"):
        raise InstallError(f"Unsupported architecture: {arch}")
    versions = set(args.python.split(",")) if args.python else None
    if versions and any(not re.fullmatch(r"3\.(?:[8-9]|[1-9][0-9])", v) for v in versions):
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


def generated_removals(root: Path, record: InstalledArtifact, remaining: Set[str]) -> Set[Path]:
    """Locate generated launchers no longer needed by a partially retained package.

    :param root: Managed library boundary containing entrypoint metadata.
    :param record: Original shared-library receipt.
    :param remaining: Runtime labels retained after removal.
    :return: Absolute generated launcher paths no retained platform requires.
    :raises InstallError: If a legacy receipt cannot identify its scripts boundary safely.
    """
    metadata = [name for name in record["files"] if name.endswith(".dist-info/entry_points.txt")]
    if not metadata:
        return set()
    if not record.get("scripts_path"):
        raise InstallError(f"Reinstall {record['name']} before partial runtime removal")
    platforms = {label.split("-")[0] for label in remaining}
    paths = set()
    for name in metadata:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(root / name)
        for section in ("console_scripts", "gui_scripts"):
            if not parser.has_section(section):
                continue
            for command in parser[section]:
                if Path(command).name != command or "\\" in command:
                    raise InstallError(f"Invalid installed entrypoint name: {command}")
                if "windows" not in platforms:
                    paths.add(Path(record["scripts_path"]) / (command + ".exe"))
                if not platforms & {"linux", "darwin"}:
                    paths.add(Path(record["scripts_path"]) / command)
    return paths


def removal_states(
    states: Dict[Path, InstallState], selectors: List[Requirement], labels: Set[str]
) -> Dict[Path, InstallState]:
    """Plan package and runtime removal without editing active deployment files.

    :param states: Verified current ownership receipts.
    :param selectors: Package names and optional exact versions.
    :param labels: Runtime labels selected for removal.
    :return: Proposed receipts, preserving unrelated distributions and file ownership.
    :raises InstallError: If partial removal cannot identify legacy launcher ownership.
    """
    proposed = copy.deepcopy(states)
    launchers = set()
    for root, state in states.items():
        for key, record in state["artifacts"].items():
            if not any(
                canonicalize_name(req.name) == record["name"]
                and req.specifier.contains(record["version"], prereleases=True)
                for req in selectors
            ):
                continue
            removed = set(record["targets"]) & labels
            if not removed:
                continue
            remaining = set(record["targets"]) - removed
            if remaining:
                proposed[root]["artifacts"][key]["targets"] = sorted(remaining)
                if record["pure"] and record.get("component") != "scripts":
                    launchers.update(generated_removals(root, record, remaining))
            else:
                del proposed[root]["artifacts"][key]
            print(
                f"Planned removal: {record['name']}=={record['version']} "
                f"[{', '.join(sorted(removed))}] at {root}"
            )
    for root, state in proposed.items():
        for record in state["artifacts"].values():
            for name in list(record["files"]):
                if root / name in launchers:
                    del record["files"][name]
    return proposed


def validate_removal(
    before: List[InstalledArtifact], after: List[InstalledArtifact], force: bool = False
) -> None:
    """Reject newly broken dependencies of retained package/runtime installations.

    :param before: Original library and scripts receipts from configured roots.
    :param after: Proposed retained receipts.
    :param force: Allow newly broken dependencies after reporting every blocker.
    :return: None.
    :raises InstallError: If paired receipts disagree or unforced removal breaks dependencies.
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
        target = Target(system, arch, python[2] + "." + python[3:])
        pending = [r for r in after if r.get("component") != "scripts" and label in r["targets"]]
        visited = set()
        while pending:
            record = pending.pop()
            if id(record) in visited:
                continue
            visited.add(id(record))
            for requirement in target_requirements(record, target):
                name = canonicalize_name(requirement.name)
                old = [
                    r
                    for r in before
                    if r["name"] == name
                    and r.get("component") != "scripts"
                    and (r["pure"] or label in r["targets"])
                    and requirement.specifier.contains(r["version"], prereleases=True)
                ]
                retained = [
                    r
                    for r in after
                    if r["name"] == name
                    and r.get("component") != "scripts"
                    and (r["pure"] or label in r["targets"])
                    and (r["name"], r["version"], label) not in withdrawn
                    and requirement.specifier.contains(r["version"], prereleases=True)
                ]
                if old and not retained:
                    affected = ", ".join(sorted({f"{r['name']}=={r['version']}" for r in old}))
                    blockers.add(
                        f"  {record['name']}=={record['version']} requires {requirement} "
                        f"for {label}; removal would withdraw {affected}"
                    )
                # Several binary versions can coexist. Follow every retained
                # candidate so none of their transitive requirements is hidden.
                pending.extend(retained)
    if blockers and force:
        print(
            "WARNING: --force allows removal despite retained dependency requirements:\n"
            + "\n".join(sorted(blockers))
            + "\nDependent packages will remain installed and may stop working."
        )
    elif blockers:
        raise InstallError(
            "Removal would break packages that remain installed:\n"
            + "\n".join(sorted(blockers))
            + "\nNo packages were removed; deployment directories are unchanged."
            + "\nKeep the required packages, or explicitly include their dependent packages "
            "in the uninstall request if you intend to remove those too. "
            "Use --force to allow removal despite these dependency requirements."
        )


def stage_removal(
    before: Dict[Path, InstallState], after: Dict[Path, InstallState], temporary: Path
) -> Changes:
    """Compose removal snapshots and update RECORD entries for retained packages.

    :param before: Verified original states.
    :param after: Proposed ownership states, updated with rewritten RECORD digests.
    :param temporary: Workspace for complete staged revisions.
    :return: Changed staged-directory/destination pairs, including empty managed roots.
    :raises OSError: If snapshot creation or metadata updates fail.
    """
    deleted = set()
    for root, state in before.items():
        owned = {name for record in after[root]["artifacts"].values() for name in record["files"]}
        deleted.update(
            root / name
            for record in state["artifacts"].values()
            for name in record["files"]
            if name not in owned
        )
    changes = []
    for number, (root, state) in enumerate(after.items()):
        if state == before[root]:
            continue
        staged = temporary / f"revision-{number}"
        shutil.copytree(root, staged, ignore=shutil.ignore_patterns("__pycache__"))
        for path in deleted:
            if root in path.parents and (staged / path.relative_to(root)).is_file():
                (staged / path.relative_to(root)).unlink()
        for record in state["artifacts"].values():
            for name in record["files"]:
                if not name.endswith(".dist-info/RECORD"):
                    continue
                path = staged / name
                with path.open(newline="") as stream:
                    rows = list(csv.reader(stream))
                base = (root / name).parent.parent
                retained = [
                    row for row in rows if Path(os.path.normpath(base / row[0])) not in deleted
                ]
                if retained != rows:
                    with path.open("w", newline="") as stream:
                        csv.writer(stream, lineterminator="\n").writerows(retained)
                    record["files"][name] = {
                        "sha256": sha256(path),
                        "mode": path.stat().st_mode & 0o777,
                    }
        for path in sorted(staged.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        (staged / MANIFEST).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        changes.append((staged, root))
    return changes


def uninstall(args: argparse.Namespace) -> int:
    """Remove selected managed packages with preflight checks and reversible publication.

    :param args: Package selectors, runtime filters, dry-run and confirmation options.
    :return: Zero on success or already absent, one if confirmation is declined.
    :raises InstallError: If selection, ownership, or retained dependencies are unsafe.
    :raises OSError: If staging or publication fails.
    """
    selectors = requests(args.packages)
    policy = Policy()
    print(f"Effective deployment root: {policy.deploy_root}")
    with operation(policy.deploy_root, read_only=args.dryrun):
        groups = {root: {} for root in policy.managed_roots()}
        include_companions(groups)
        roots = sorted(groups)
        for i, root in enumerate(roots):
            if any(root in other.parents for other in roots[i + 1 :]):
                raise InstallError(f"Overlapping managed boundaries: {root}")
        with locks(roots, args.dryrun):
            states = {root: read_state(root) for root in roots}
            records = [
                record for state in states.values() for record in state["artifacts"].values()
            ]
            labels = runtime_labels(args, records)
            proposed = removal_states(states, selectors, labels)
            retained = [
                record for state in proposed.values() for record in state["artifacts"].values()
            ]
            validate_removal(records, retained, force=args.force)
            with tempfile.TemporaryDirectory(prefix="distman-uninstall-") as work:
                changes = stage_removal(states, proposed, Path(work))
                if args.dryrun:
                    print(f"Dry run: {len(changes)} revision(s); deployment directories unchanged")
                    return 0
                if not changes:
                    print("Unchanged: selected packages/runtimes are not installed")
                    return 0
                if not args.yes and not util.yesNo(f"Remove packages from {policy.deploy_root}?"):
                    return 1
                publish_directories(changes, policy.deploy_root)
                print(f"Uninstalled: {len(changes)} revision(s)")
        return 0
