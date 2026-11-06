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

"""Read legacy ownership and validate package compatibility and dependencies."""

import json
import os
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, Iterator

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename

from distman.install import InstallError
from distman.install.models import InstalledArtifact, InstallState
from distman.install.targets import Target
from distman.inventory import inventory
from distman.metadata import Store

MANIFEST = ".distman-install.json"


def legacy_python(label: str) -> str:
    """Recover a conservative full Python version from an old runtime label.

    :param label: Runtime label ending in a compact CPython tag such as ``cp311``.
    :return: Full Python compatibility version using patch level zero.
    :raises ValueError: If the label does not contain a valid CPython tag.
    """
    _, marker, compact = label.rpartition("-cp")
    if not marker or len(compact) < 2 or not compact.isdigit():
        raise ValueError(f"invalid runtime label: {label}")
    return f"{compact[0]}.{compact[1:]}.0"


def read_state(destination: Path) -> InstallState:
    """Read an owned revision and reject unsafe paths, corruption, or foreign files.

    :param destination: Active managed symlink whose receipt and payload should be verified.
    :return: Verified receipt, or an empty initial state when the destination does not exist.
    :raises InstallError: If ownership metadata is invalid or installed contents differ from the
        receipt.
    """
    deployment = next(
        (
            candidate
            for candidate in (destination, *destination.parents)
            if (candidate / ".distman/current.json").is_file()
        ),
        None,
    )
    if deployment is not None:
        artifacts = {}
        for target, record in Store(deployment).target_records().items():
            if target != destination and destination not in target.parents:
                continue
            for owner, artifact in record.get("state", {}).get("artifacts", {}).items():
                _, _, remainder = owner.partition(":")
                key, _, _ = remainder.partition(":")
                key = key or artifact["name"]
                previous = artifacts.get(key)
                if previous is not None and previous != artifact:
                    raise InstallError(f"Inconsistent centralized ownership: {key}")
                artifacts[key] = artifact
        return {"schema": 1, "artifacts": artifacts}
    if not os.path.lexists(destination):
        return {"schema": 1, "artifacts": {}}
    if not destination.is_symlink() or not (destination / MANIFEST).is_file():
        raise InstallError(f"Refusing unmanaged destination: {destination}")
    try:
        state = json.loads((destination / MANIFEST).read_text())
        if state["schema"] != 1 or not isinstance(state["artifacts"], dict):
            raise ValueError("unsupported schema")
        expected = {}
        for record in state["artifacts"].values():
            compatibility = record.setdefault(
                "compatibility", {label: legacy_python(label) for label in record["targets"]}
            )
            if set(compatibility) != set(record["targets"]):
                raise ValueError("runtime compatibility metadata does not match targets")
            for name, checksum in record["files"].items():
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                    raise ValueError("invalid ownership path")
                if name in expected:
                    raise ValueError("duplicate file ownership")
                expected[name] = checksum
        if expected != inventory(destination, exclude=(MANIFEST,)):
            raise InstallError(f"Installed content changed or is incomplete: {destination}")
        return state
    except (ValueError, KeyError, TypeError) as exc:
        raise InstallError(f"Invalid ownership metadata in {destination}: {exc}") from exc


def marker_environment(target: Target) -> Dict[str, str]:
    """Construct the target marker values known without executing its interpreter.

    :param target: Runtime supplying the known OS, architecture, and Python marker values.
    :return: Packaging marker environment with known runtime fields overridden.
    """
    environment = default_environment()
    environment.update(
        python_version=target.python,
        python_full_version=target.compatibility_python,
        implementation_name="cpython",
        implementation_version=target.compatibility_python,
        platform_python_implementation="CPython",
        platform_machine="AMD64" if target.platform == "windows" else target.arch,
        sys_platform={"linux": "linux", "windows": "win32", "darwin": "darwin"}[target.platform],
        platform_system={"linux": "Linux", "windows": "Windows", "darwin": "Darwin"}[
            target.platform
        ],
        os_name="nt" if target.platform == "windows" else "posix",
        extra="",
    )
    return environment


def target_records(
    records: Iterable[InstalledArtifact], target: Target
) -> Dict[str, InstalledArtifact]:
    """Select recorded target installations and their reachable shared dependencies.

    :param records: Receipts for proposed and retained libraries and scripts.
    :param target: Runtime whose dependency closure is required.
    :return: Distributions relevant to this runtime, indexed by canonical name.
    :raises InstallError: If a dependency marker requires unknown OS release data.
    """
    available = {
        record["name"]: record
        for record in records
        if record.get("component") != "scripts" and target.label in record["targets"]
    }
    selected = {
        name: record for name, record in available.items() if target.label in record["targets"]
    }
    pending = list(selected.values())
    while pending:
        for requirement in target_requirements(pending.pop(), target):
            name = canonicalize_name(requirement.name)
            if name in available and name not in selected:
                selected[name] = available[name]
                pending.append(available[name])
    return selected


def target_requirements(record: InstalledArtifact, target: Target) -> Iterator[Requirement]:
    """Yield base requirements applicable to a selected runtime.

    :param record: Distribution receipt containing declared dependencies.
    :param target: Runtime supplying known marker values.
    :yields: Requirements applicable to the runtime.
    :raises InstallError: If an OS release marker cannot be evaluated.
    """
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
        yield requirement


def check_script_pairs(records: Iterable[InstalledArtifact]) -> None:
    """Require paired library/script receipts to describe the same wheel revision.

    :param records: Proposed and retained artifacts from all affected roots.
    :return: None.
    :raises InstallError: If independently rolled-back roots disagree or a component is missing.
    """
    pairs = {}
    for record in records:
        if not record.get("separate_scripts"):
            continue
        identity = (record["name"], record["library_path"], record["scripts_path"])
        pairs.setdefault(identity, {})[record["component"]] = record
    for identity, components in pairs.items():
        library = components.get("library")
        scripts = components.get("scripts")
        if (
            not library
            or not scripts
            or library["sha256"] != scripts["sha256"]
            or set(library["targets"]) != set(scripts["targets"])
            or library["compatibility"] != scripts["compatibility"]
        ):
            raise InstallError(
                f"Library and scripts revisions disagree for {identity[0]}; "
                "restore matching revisions or reinstall the affected package"
            )


def check_dependencies(records: Iterable[InstalledArtifact], target: Target) -> None:
    """Reject broken base dependencies, including retained shared distributions.

    Pip still owns resolution. This guard detects host-marker omissions during
    cross downloads rather than claiming pip resolves a foreign environment.

    :param records: Proposed and retained artifact receipts participating in dependency
        validation.
    :param target: Runtime for evaluating compatibility and base dependency markers.
    :return: None.
    :raises InstallError: If a dependency is missing/incompatible or a required OS marker is
        unknown.
    """
    records = list(records)
    available = [
        record
        for record in records
        if record.get("component") != "scripts" and target.label in record["targets"]
    ]
    for record in available:
        tags = parse_wheel_filename(record["wheel"])[3]
        if not tags & target.tags or not SpecifierSet(record["requires_python"]).contains(
            target.compatibility_python, prereleases=True
        ):
            raise InstallError(f"{record['name']} is incompatible with {target.label}")
        for requirement in target_requirements(record, target):
            candidates = [
                dependency
                for dependency in available
                if dependency["name"] == canonicalize_name(requirement.name)
                and requirement.specifier.contains(dependency["version"], prereleases=True)
            ]
            if not candidates:
                raise InstallError(f"{record['name']} requires {requirement} for {target.label}")
