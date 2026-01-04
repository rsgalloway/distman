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

from distman import cache, dist
from distman.logger import setup_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""

    from distman import __version__

    parser = argparse.ArgumentParser(
        prog="distman",
        description="distman: file distribution manager",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"distman {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    p_dist = sub.add_parser(
        "dist",
        help="File distribution management",
        parents=[dist.build_parser(prog="distman dist")],
        add_help=False,
    )
    p_dist.set_defaults(_handler="dist")

    p_cache = sub.add_parser(
        "cache",
        help="Cache management",
        parents=[cache.build_parser(prog="distman cache")],
        add_help=False,
    )
    p_cache.set_defaults(_handler="cache")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """distman suite entrypoint."""
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(dryrun=getattr(args, "dryrun", False))

    if args.command == "dist":
        return dist.run(args)

    if args.command == "cache":
        return cache.run(args)

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
