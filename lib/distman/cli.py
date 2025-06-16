#!/usr/bin/env python
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

__doc__ = """
Command line interface for distman: simple software distribution system.

Usage:

    $ distman [LOCATION] [OPTIONS]
"""

import argparse
import os
import sys

from distman import Distributor, config, util
from distman.logger import setup_logging


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
        help="directory containing dist file (default is cwd)",
    )
    parser.add_argument(
        "-t",
        "--target",
        metavar="TARGET",
        help="source TARGET in the dist file (supports wildcards)",
    )
    parser.add_argument(
        "-s",
        "--show",
        action="store_true",
        help="show current distributed versions only",
    )
    parser.add_argument(
        "-c",
        "--commit",
        metavar="HASH",
        help="change TARGET version number to point to commit HASH",
    )
    parser.add_argument(
        "-n",
        "--number",
        metavar="NUMBER",
        help="change TARGET version number to point to NUMBER",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="reset version for TARGET to point to latest version",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="delete dist TARGET from deployment folder",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="answer yes to all questions, skipping user interaction",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="dist all files, including ignorable files",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="force action, e.g. disting uncommitted file changes",
    )
    parser.add_argument(
        "--version-only",
        action="store_true",
        help="distribute files only, do not create links",
    )
    parser.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        help="do a dry run, no actions will be performed",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show verbose information",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"distman {__version__}",
    )

    args = parser.parse_args()
    return args


def main():
    """Main thread."""

    args = parse_args()

    # set up logging handlers
    setup_logging(dryrun=args.dryrun)

    # validate arguments
    if not os.path.isdir(args.location):
        print("%s is not a directory" % args.location)
        return 1
    if sum([bool(args.commit), bool(args.number), bool(args.reset)]) > 1:
        print("--commit,--number and --reset are mutually exclusive")
        return 1

    # create distributor object
    distributor = Distributor()

    # process the requested location
    if not distributor.read_dist_file(args.location):
        return 1

    # remove target(s), symlink privs not needed
    if args.delete:
        if distributor.delete_target(
            target=args.target,
            target_version=args.number,
            target_commit=args.commit,
            yes=args.yes,
            dryrun=args.dryrun,
        ):
            return 0
        else:
            return 1

    # on windows make sure SeCreateSymbolicLinkPrivilege privilege is held
    # (must be done after Distributor object is created)
    if os.name == "nt":
        if not util.check_symlinks():
            return 1

    # change target version
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
            target_file = args.target
            target_version = args.number
            try:
                target_version = int(target_version)
            except Exception:
                print("Invalid version number: %s" % args.number[1])
                return 2
            target_commit = ""

        else:
            target_file = args.target
            target_commit = args.commit
            target_version = args.number
            if len(target_commit) < config.LEN_MINHASH:
                print("Hashes must be at least %d characters" % config.LEN_MINHASH)
                return 2

        # do target version change
        if distributor.change_file_version(
            target_file, target_commit, target_version, dryrun=args.dryrun
        ):
            return 0
        else:
            return 1

    # do file distribution
    try:
        if distributor.dist(
            target=args.target,
            show=args.show,
            force=args.force,
            all=args.all,
            yes=args.yes,
            dryrun=args.dryrun,
            versiononly=args.version_only,
            verbose=args.verbose,
        ):
            return 0

    except KeyboardInterrupt:
        print("Stopping dist...")
        return 2

    return 1


if __name__ == "__main__":
    sys.exit(main())
