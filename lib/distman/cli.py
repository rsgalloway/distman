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

__doc__ = """
distman: suite entrypoint

Usage:
    distman dist  [DIST OPTIONS...]
    distman cache [CACHE OPTIONS...]

Convenience shims can map:
    dist      -> distman dist
    distcache -> distman cache
"""

import argparse
import sys
from typing import List, Optional

from distman.logger import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""

    from distman import __version__

    parser = argparse.ArgumentParser(
        prog="distman",
        description="distman: distribution + caching utilities",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="output verbosity (-v for INFO, -vv for DEBUG)",
    )
    parser.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        help="dry run (where supported)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"distman {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    # dist subcommand: we intentionally do NOT define all dist flags here.
    # We pass through unknown args to distman.dist.main for full backward compat.
    p_dist = sub.add_parser(
        "dist",
        help="deploy / manage versions (same behavior as the 'dist' command)",
        add_help=False,  # let the underlying dist parser handle -h/--help
    )
    p_dist.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="arguments passed to the dist command (use: distman dist -- -h)",
    )

    # cache subcommand: same pass-through behavior to distman.cache.main
    p_cache = sub.add_parser(
        "cache",
        help="cache/clone deployment root locally (same behavior as the 'distcache' command)",
        add_help=False,
    )
    p_cache.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="arguments passed to the cache command (use: distman cache -- -h)",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """distman suite entrypoint."""
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = _build_parser()
    args = parser.parse_args(argv)

    # setup logging once here
    setup_logging(dryrun=getattr(args, "dryrun", False))

    if args.command == "dist":
        from distman import dist

        # strip optional "--" that users may insert for clarity:
        passthru = args.argv[1:] if (args.argv and args.argv[0] == "--") else args.argv
        return dist.main(passthru)

    if args.command == "cache":
        from distman import cache

        passthru = args.argv[1:] if (args.argv and args.argv[0] == "--") else args.argv
        return cache.main(passthru)

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
