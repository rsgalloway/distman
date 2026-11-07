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

"""Withdraw active file distributions without deleting retained revisions."""

import argparse
import glob
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from distman import config, util
from distman.deployment import TargetPublication, publish_targets
from distman.dist.distribution import (
    Distributor,
    apply_destination_template,
    match_source_pattern,
    should_skip_target,
)
from distman.logger import log
from distman.operations import operation


class UndistError(ValueError):
    """A requested target cannot safely be withdrawn."""


def read_receipt(destination: Path) -> Optional[Dict[str, str]]:
    """Read a legacy dist ownership receipt without treating it as YAML.

    :param destination: Active deployment destination associated with the receipt.
    :return: Receipt fields, or None when the receipt does not exist.
    :raises UndistError: If an existing receipt is malformed.
    """
    path = Path(util.get_dist_file(str(destination)))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    fields: Dict[str, str] = {}
    for line in lines:
        if ": " not in line:
            raise UndistError(f"Invalid dist receipt: {path}")
        key, value = line.split(": ", 1)
        fields[key] = value
    if not fields.get("source"):
        raise UndistError(f"Dist receipt has no source: {path}")
    return fields


def _receipt_destinations(template: str) -> Iterable[Path]:
    """Discover destinations represented by wildcard receipt filenames.

    :param template: Resolved destination template containing ``%N`` placeholders.
    :return: Managed destination candidates inferred from matching receipt paths.
    """
    pattern = template
    for index in range(1, 10):
        pattern = pattern.replace(f"%{index}", "*")
    receipt_pattern = util.get_dist_file(pattern)
    suffix = config.DIST_INFO_EXT
    for receipt in glob.glob(receipt_pattern):
        path = Path(receipt)
        name = path.name
        if name.startswith(".") and name.endswith(suffix):
            yield path.with_name(name[1 : -len(suffix)])


def _add_target(resolved: Dict[Path, str], destination: Path, source: str) -> None:
    """Add a resolved target while rejecting conflicting source ownership.

    :param resolved: Destinations accumulated for the request.
    :param destination: Effective active deployment path.
    :param source: Expected source identity from the manifest or receipt.
    :return: None; ``resolved`` is updated in place.
    :raises UndistError: If another source resolves to the same destination.
    """
    previous = resolved.get(destination)
    if previous is not None and previous != source:
        raise UndistError(
            f"Ambiguous destination {destination}: selected by {previous} and {source}"
        )
    resolved[destination] = source


def _manifest_targets(
    distributor: Distributor,
    patterns: Optional[List[str]],
    include_all: bool = False,
) -> List[Tuple[Path, str]]:
    """Resolve selected manifest targets, including missing wildcard sources.

    :param distributor: Distributor with a loaded dist.json document.
    :param patterns: Optional target-name patterns.
    :param include_all: Include matching targets recorded by earlier dists.
    :return: Unique destination and expected-source pairs.
    :raises UndistError: If no configured targets match.
    """
    targets = distributor.get_targets() or {}
    resolved: Dict[Path, str] = {}
    for name, entry in targets.items():
        if should_skip_target(name, patterns):
            continue
        source = entry.get(config.TAG_SOURCEPATH)
        destination = entry.get(config.TAG_DESTPATH)
        if source is None or destination is None:
            continue
        source_pattern = util.resolve_relative_path(distributor.directory, source)
        destination_template = util.resolve_vars_path(destination)
        if "*" not in source_pattern:
            _add_target(resolved, Path(destination_template), source_pattern)
            continue
        for source_path, destination_path in util.expand_wildcard_entry(
            source_pattern, destination_template
        ):
            _add_target(resolved, Path(destination_path), source_path)
        if include_all:
            for candidate in _receipt_destinations(destination_template):
                receipt = read_receipt(candidate)
                if receipt is None or receipt.get("origin") != distributor.path:
                    continue
                recorded_source = util.resolve_relative_path(
                    distributor.directory, receipt["source"]
                )
                groups = match_source_pattern(recorded_source, source_pattern)
                if groups is not None:
                    expected = Path(apply_destination_template(destination_template, groups))
                    if expected == candidate:
                        _add_target(resolved, candidate, recorded_source)
    if not resolved:
        selection = ", ".join(patterns or ["*"])
        raise UndistError(f"No matching targets in {config.DIST_FILE}: {selection}")
    return list(resolved.items())


def _validate_target(
    destination: Path,
    expected_source: Optional[str],
    expected_origin: Optional[str] = None,
    source_root: Optional[Path] = None,
    allow_absent: bool = False,
) -> bool:
    """Validate ownership and the active retained-revision link.

    :param destination: Target to withdraw.
    :param expected_source: Optional source identity required by the request.
    :param expected_origin: Optional project identity required by a manifest request.
    :param source_root: Base directory for a legacy relative receipt source.
    :param allow_absent: Treat an absent, unrecorded manifest destination as unchanged.
    :return: True when an active link should be removed; False when already withdrawn.
    :raises UndistError: If ownership, source identity, or link integrity fails.
    """
    receipt = read_receipt(destination)
    if receipt is None:
        if allow_absent and not os.path.lexists(destination):
            return False
        raise UndistError(f"Destination is not managed by distman: {destination}")
    if expected_origin is not None and receipt.get("origin") != expected_origin:
        raise UndistError(
            f"Destination is owned by another project: {destination} "
            f"({receipt.get('origin', 'unknown origin')})"
        )
    if expected_source is not None:
        base = str(source_root or Path.cwd())
        expected = util.resolve_relative_path(base, expected_source)
        recorded = util.resolve_relative_path(base, receipt["source"])
        if expected != recorded:
            raise UndistError(f"Recorded source does not match {destination}: {receipt['source']}")
    if destination.is_symlink():
        linked = Path(util.get_link_full_path(str(destination)))
        versions = {Path(path) for path, _, _ in util.get_file_versions(str(destination))}
        if linked not in versions or not linked.exists():
            raise UndistError(f"Active target is not a retained dist revision: {destination}")
        return True
    if os.path.lexists(destination):
        raise UndistError(f"Managed destination is not a symbolic link: {destination}")
    return False


def _run(args: argparse.Namespace) -> int:
    """Resolve, validate, confirm, and publish an undist operation.

    :param args: Parsed undist arguments.
    :return: Zero for success or a declined confirmation.
    :raises UndistError: If request syntax or target validation fails.
    """
    if not os.path.isdir(args.location):
        raise UndistError(f"{args.location} is not a directory")
    if args.source and not args.dest:
        raise UndistError("--source requires --dest")
    if args.dest and args.target:
        raise UndistError("--dest cannot be combined with --target")
    if args.dest and args.all:
        raise UndistError("--all cannot be combined with --dest")

    distributor = Distributor()
    location = Path(args.location).resolve()
    try:
        if args.dest:
            destination = Path(util.resolve_vars_path(args.dest)).absolute()
            source = util.resolve_relative_path(str(location), args.source) if args.source else None
            targets = [(destination, source)]
            direct = True
            expected_origin = None
        else:
            if not distributor.read_dist_file(str(location)):
                raise UndistError(f"{config.DIST_FILE} not found or invalid")
            distributor.directory = str(location)
            distributor.read_git_info()
            if not distributor.repo:
                distributor.path = str(location).replace("\\", "/")
                distributor.name = location.name
            targets = _manifest_targets(distributor, args.target, args.all)
            direct = False
            expected_origin = distributor.path

        if args.dryrun:
            log.info(config.DRYRUN_MESSAGE)
        active = []
        for destination, source in targets:
            if _validate_target(
                destination,
                source if direct else None,
                expected_origin=expected_origin,
                source_root=location,
                allow_absent=not direct,
            ):
                log.info("Unlink: %s", destination)
                active.append(destination)
            else:
                log.info("Unchanged: %s is already unlinked", destination)
        if not active:
            log.info("Undisted: 0 target(s)")
            return 0
        if args.dryrun:
            log.info("Dry run: %d target(s); deployment unchanged", len(active))
            return 0
        if not args.yes and not util.yesNo(
            f"Withdraw {len(active)} target(s) from {config.DEPLOY_ROOT}?", default=True
        ):
            return 0
        publish_targets(
            (TargetPublication(destination=destination, delete=True) for destination in active),
            Path(config.DEPLOY_ROOT),
        )
        log.info("Undisted: %d target(s)", len(active))
        return 0
    finally:
        distributor.close()


def run(args: argparse.Namespace) -> int:
    """Coordinate undist locking and translate expected failures to exit codes.

    :param args: Parsed undist arguments.
    :return: Process exit status.
    """
    try:
        with operation(Path(config.DEPLOY_ROOT), read_only=args.dryrun):
            return _run(args)
    except KeyboardInterrupt:
        log.error("Undist canceled")
        return 130
    except (OSError, UndistError, ValueError) as exc:
        log.error("Undist failed: %s", exc)
        return 1
