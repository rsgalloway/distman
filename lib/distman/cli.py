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
    distman install [INSTALL OPTIONS...]

Convenience shims can map:
    dist      -> distman dist
    distcache -> distman cache
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from distman import cache, config, dist
from distman.logger import setup_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    :return: Suite parser containing dist, cache, and install subcommands.
    """

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
        parents=[build_command_parser("dist", prog="distman dist")],
        add_help=False,
    )
    p_dist.set_defaults(_handler="dist")

    p_cache = sub.add_parser(
        "cache",
        help="Cache management",
        parents=[build_command_parser("cache", prog="distman cache")],
        add_help=False,
    )
    p_cache.set_defaults(_handler="cache")

    sub.add_parser(
        "install",
        help="Install Python wheels into versioned destinations",
        parents=[build_command_parser("install", prog="distman install")],
        add_help=False,
    )

    return parser


def build_command_parser(command: str, prog: Optional[str] = None) -> argparse.ArgumentParser:
    """Build one command parser while retaining legacy standalone program names.

    :param command: Command name: dist, cache, or install.
    :param prog: Optional executable name displayed in usage and error messages.
    :return: Parser configured with the selected command's options.
    :raises ValueError: If the command name is unknown.
    """
    definitions = {
        "dist": (
            "dist: distribute files and directories to versioned destinations",
            _add_dist_options,
        ),
        "cache": ("Cross-platform caching with optional diff.", _add_cache_options),
        "install": ("Resolve wheels and publish versioned installations", _add_install_options),
    }
    if command not in definitions:
        raise ValueError(f"Unknown command: {command}")
    description, add_options = definitions[command]
    parser = argparse.ArgumentParser(
        prog=prog or command,
        description=description,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_options(parser)
    return parser


def _add_dist_options(parser: argparse.ArgumentParser) -> None:
    """Add dist options shared by suite and compatibility entry points.

    :param parser: Parser to receive the existing distribution command options.
    :return: None; the parser is updated in place.
    """
    from distman import __version__

    parser.add_argument(
        "location",
        metavar="LOCATION",
        nargs="?",
        default=".",
        help="source directory and optional dist file location (default is cwd)",
    )
    parser.add_argument(
        "-t",
        "--target",
        nargs="+",
        metavar="TARGET",
        help="target name in the dist file (supports wildcards)",
    )
    parser.add_argument(
        "--source",
        metavar="PATH",
        help="source file or directory; matches dist.json source patterns when present",
    )
    parser.add_argument(
        "--dest",
        metavar="PATH",
        help="deployment destination override; requires --source or --target",
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
        "--ignore-missing",
        action="store_true",
        default=config.IGNORE_MISSING,
        help="ignore missing files in dist file",
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
        action="count",
        default=0,
        help="output verbosity (-v for INFO, -vv for DEBUG)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"distman {__version__}",
    )


def _add_cache_options(parser: argparse.ArgumentParser) -> None:
    """Add cache options shared by suite and compatibility entry points.

    :param parser: Parser to receive the existing cache command options.
    :return: None; the parser is updated in place.
    """
    parser.add_argument(
        "--src",
        default=config.DEPLOY_ROOT,
        type=Path,
        help="Source directory",
    )
    parser.add_argument(
        "--dst",
        default=config.CACHE_ROOT,
        type=Path,
        help="Destination directory",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(32, (os.cpu_count() or 8) * 4),
        help="Copy threads (default: 4x CPU, capped at 32)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the cache",
    )
    parser.add_argument(
        "-p",
        "--prune",
        action="store_true",
        help="Prune the cache",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show differences only (no copy)",
    )
    parser.add_argument(
        "-t",
        "--ttl",
        type=float,
        default=config.CACHE_TTL,
        help="TTL (seconds) for remote epoch checks (0 = always check)",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force cache even if cache appears fresh (overrides TTL and epoch check)",
    )
    parser.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        help="Dry run (no changes made)",
    )


def _add_install_options(parser: argparse.ArgumentParser) -> None:
    """Add install options shared by suite and compatibility entry points.

    :param parser: Parser to receive package/project installation options.
    :return: None; the parser is updated in place.
    """
    parser.add_argument(
        "packages", nargs="*", help="PyPI requirements or an explicit local project path"
    )
    parser.add_argument(
        "-r", "--requirement", action="append", default=[], help="pip requirements file"
    )
    parser.add_argument("--project", help="explicit alias for a local project path")
    parser.add_argument("--env", help="reserved for future envstack package selection")
    parser.add_argument("--platform", help="comma-separated linux, windows, darwin (or all)")
    parser.add_argument("--arch", help="x86_64 or arm64 (macOS)")
    parser.add_argument("--python", help="comma-separated CPython major.minor versions")
    parser.add_argument("--find-links", action="append", default=[], help="pip wheel location")
    parser.add_argument("--no-index", action="store_true", help="use only --find-links")
    parser.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        help="resolve/stage in temporary storage and show changes without deployment writes",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="confirm activation")
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="publish a new revision even when unchanged; never override ownership",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="show pip output")


def _run_install(args: argparse.Namespace) -> int:
    """Validate input and translate expected installation failures into exit codes.

    :param args: Parsed install options from the centralized suite parser.
    :return: Installer exit status, or one for an expected input/dependency/installation
        failure.
    """
    from distman.install import InstallError

    try:
        if args.env:
            raise InstallError(
                "--env installation is deferred; use PyPI requirements, -r, or --project"
            )
        if not (args.packages or args.requirement or args.project):
            raise InstallError("Provide a PyPI requirement, -r, or --project")
        from distman.install.command import install

        return install(args)
    except ImportError as exc:
        print(f"install requires optional dependencies: pip install 'distman[install]' ({exc})")
        return 1
    except (InstallError, OSError, ValueError) as exc:
        print(f"Install failed: {exc}")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    """distman suite entrypoint.

    :param argv: Optional arguments excluding the executable; None reads sys.argv.
    :return: Exit status from the selected command.
    :raises SystemExit: If argument parsing requests help or rejects invalid input.
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(dryrun=getattr(args, "dryrun", False))

    if args.command == "dist":
        return dist.run(args)

    if args.command == "cache":
        return cache.run(args)

    if args.command == "install":
        return _run_install(args)

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
