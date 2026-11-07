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

"""Distribution command orchestration and standalone entry point."""

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from distman import config, util
from distman.dist.distribution import Distributor
from distman.logger import log, setup_logging
from distman.operations import operation
from distman.pipeline import ValidationError
from distman.transform import TransformError


def build_parser(prog: str = "dist") -> argparse.ArgumentParser:
    """Build the legacy parser through the centralized command-line interface.

    :param prog: Executable name to display in the standalone distribution parser.
    :return: Distribution parser supplied by the centralized CLI.
    """
    from distman.cli import build_command_parser

    return build_command_parser("dist", prog=prog)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for the dist utility.

    :param argv: Optional argument sequence, excluding the executable name.
    :return: Parsed command-line arguments.
    """
    parser = build_parser()
    return parser.parse_args(list(argv) if argv is not None else None)


def _run(args: argparse.Namespace) -> int:
    """Run the dist utility with parsed command-line arguments.

    :param args: Parsed command-line arguments from ``parse_args``.
    :return: Process exit code.
    """

    # validate
    if not os.path.isdir(args.location):
        log.error("%s is not a directory", args.location)
        return 1

    if sum([bool(args.commit), bool(args.number), bool(args.reset)]) > 1:
        log.error("--commit, --number, and --reset are mutually exclusive")
        return 1

    distributor = Distributor()
    try:

        dist_file = os.path.join(args.location, config.DIST_FILE)
        has_direct_target = bool(args.source and args.dest)
        if has_direct_target and not os.path.exists(dist_file):
            # An explicit source and destination is a complete ad-hoc deployment.
            distributor.directory = args.location
        elif not distributor.read_dist_file(args.location):
            distributor.directory = args.location
            return 1
        else:
            distributor.directory = args.location

        # delete target(s)
        if args.delete:
            ok = distributor.delete_target(
                target=args.target,
                target_version=args.number,
                target_commit=args.commit,
                yes=args.yes,
                dryrun=args.dryrun,
            )
            return 0 if ok else 1

        # windows: ensure symlink privilege
        if os.name == "nt":
            if not util.check_symlinks():
                return 1

        # change/reset versions
        if args.number or args.commit or args.reset:
            if args.reset:
                ok = distributor.reset_file_version(args.target, dryrun=args.dryrun)
                return 0 if ok else 1

            if args.number:
                try:
                    target_version = int(args.number)
                except Exception:
                    log.error("Invalid version number: %s", args.number)
                    return 2
                target_commit = ""
            else:
                target_version = args.number
                target_commit = args.commit
                if len(target_commit) < config.LEN_MINHASH:
                    log.error("Hashes must be at least %d characters", config.LEN_MINHASH)
                    return 2

            ok = distributor.change_file_version(
                target=args.target,
                target_commit=target_commit,
                target_version=target_version,
                dryrun=args.dryrun,
            )
            return 0 if ok else 1

        # run dist
        ok = distributor.dist(
            target=args.target,
            source=args.source,
            dest=args.dest,
            show=args.show,
            force=args.force,
            all=args.all,
            yes=args.yes,
            ignore_missing=args.ignore_missing,
            dryrun=args.dryrun,
            versiononly=args.version_only,
            verbose=args.verbose,
        )
        return 0 if ok else 1

    finally:
        distributor.close()


def run(args: argparse.Namespace) -> int:
    """Coordinate a distribution request and translate cancellation or operation failures.

    :param args: Parsed distribution options and policy selection.
    :return: Zero on success, 130 on cancellation, or one on failure.
    """
    if args.show and (args.delete or args.number or args.commit or args.reset):
        log.error("--show cannot be combined with --delete, --number, --commit, or --reset")
        return 2
    try:
        with operation(Path(config.DEPLOY_ROOT), read_only=args.dryrun or args.show):
            return _run(args)
    except KeyboardInterrupt:
        log.error("Distribution canceled")
        return 130
    except (OSError, ValueError, ValidationError, TransformError) as exc:
        log.error(str(exc))
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point for the dist utility.

    :param argv: Optional argument sequence, excluding the executable name.
    :return: Process exit code.
    """

    args = parse_args(argv)

    setup_logging(dryrun=args.dryrun)

    return run(args)
