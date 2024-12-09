#!/usr/bin/env python
#
# Copyright (c) 2024, Ryan Galloway (ryan@rsgalloway.com)
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

__doc__ = """
Command line interface for distman: simple software distribution system.

Usage:

    $ distman [LOCATION] [OPTIONS]
"""

import argparse
import os
import sys

from distman import Distributor, config, util
from distman.logger import setup_stream_handler

setup_stream_handler()


def parse_args():
    """Parse command line arguments."""
    from distman import __version__

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "location",
        metavar="LOCATION",
        nargs="?",
        default=".",
        help="The directory containing dist file. Default is current directory",
    )
    parser.add_argument(
        "-t",
        "--target",
        metavar="TARGET",
        help="The source TARGET in the dist file (supports wildcards)",
    )
    parser.add_argument(
        "-s",
        "--show",
        action="store_true",
        help="Show current distributed versions only",
    )
    parser.add_argument(
        "-c",
        "--commit",
        metavar="HASH",
        help="Change TARGET version number to point to commit HASH",
    )
    parser.add_argument(
        "-n",
        "--number",
        metavar="NUMBER",
        help="Change TARGET version number to point to NUMBER",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset version for TARGET to point to latest version",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete dist TARGET from deployment folder.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Answer yes to all questions, skipping user interaction",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force action, e.g. disting uncommitted file changes",
    )
    parser.add_argument(
        "--version-only",
        action="store_true",
        help="Distribute files only, do not create links",
    )
    parser.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        help="Do a dry run, no actions will be performed",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show verbose information",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"distman {__version__}",
    )

    args = parser.parse_args()
    return args


def main():
    """Main thread."""

    args = parse_args()

    if not os.path.isdir(args.location):
        print("%s is not a directory" % args.location)
        return 1

    if sum([bool(args.commit), bool(args.number), bool(args.reset)]) > 1:
        print("--commit,--number and --reset are mutually exclusive")
        return 1

    distributor = Distributor()

    # process the requested location
    if not distributor.read_dist_file(args.location):
        return 1

    # remove target(s), symlink privs not needed
    if args.delete:
        if distributor.delete_target(args.target, yes=args.yes, dryrun=args.dryrun):
            return 0
        else:
            return 1

    # on windows make sure SeCreateSymbolicLinkPrivilege privilege is held
    # (must be done after Distributor object is created)
    if os.name == "nt":
        if not util.check_symlinks():
            return 1

    if args.number or args.commit or args.reset:
        if args.reset:
            if distributor.reset_file_version(
                args.target,
                dryrun=args.dryrun,
            ):
                return 0
            else:
                return 1

        elif args.number:
            if not args.target:
                print("No target specified to change version")
                return 2
            else:
                target_file = args.target
                target_version = args.number
            try:
                target_version = int(target_version)
            except Exception:
                print("Invalid version number: %s" % args.number[1])
                return 2
            target_commit = ""

        else:
            if not args.target:
                print("No target specified to change version")
                return 2
            else:
                target_file = args.target
                target_commit = args.commit
            if len(target_commit) < config.LEN_MINHASH:
                print("Hashes must be at least %d characters" % config.LEN_MINHASH)
                return 0
            target_version = 0

        # file version change
        if distributor.change_file_version(
            target_file, target_commit, target_version, dryrun=args.dryrun
        ):
            return 0
        else:
            return 1

    # do file distribution
    if distributor.dist(
        target=args.target,
        show=args.show,
        force=args.force,
        yes=args.yes,
        dryrun=args.dryrun,
        versiononly=args.version_only,
        verbose=args.verbose,
    ):
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
