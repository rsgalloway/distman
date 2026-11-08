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

"""Compatibility tests for centralized suite and standalone argument definitions."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

from distman import cache, cli
from distman.dist import command as dist


@pytest.mark.parametrize(
    "command,arguments",
    [
        ("dist", ["--source", "src", "--dest", "out", "--dryrun", "--yes"]),
        ("dist", ["--target", "python", "--force", "--version-only"]),
        ("cache", ["--src", "src", "--dst", "cache", "--dryrun", "--ttl", "0"]),
        ("cache", ["--prune", "--force"]),
    ],
)
def test_standalone_and_suite_options_match(command: str, arguments: List[str]) -> None:
    """Existing standalone parsers and suite subcommands produce the same options."""
    builder = dist.build_parser if command == "dist" else cache.build_parser
    expected = vars(builder().parse_args(arguments))
    actual = vars(cli.build_parser().parse_args([command, *arguments]))
    actual.pop("command")
    actual.pop("_handler")
    assert actual == expected


def test_suite_dispatches_existing_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI centralization preserves the existing dist/cache operation boundaries."""
    calls = []

    def handler(args: argparse.Namespace) -> int:
        """Record the selected operation and return its exit status unchanged."""
        calls.append(args.command)
        return 7

    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(dist, "run", handler)
    monkeypatch.setattr(cache, "run", handler)
    assert cli.main(["dist", "--dryrun"]) == 7
    assert cli.main(["cache", "--dryrun"]) == 7
    assert calls == ["dist", "cache"]


def test_install_keeps_target_reserved_for_distribution() -> None:
    """Platform selection cannot accidentally reuse the existing dist target option."""
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["install", "sample", "--target", "linux"])
    assert raised.value.code == 2


def test_shared_deployment_policy_defaults_and_override(tmp_path: Path) -> None:
    """Resolve config and command policy from one packaged or user-authored environment.

    :param tmp_path: Temporary home and ENVPATH directory.
    :return: None.
    """
    environment = os.environ.copy()
    for key in ("ROOT", "DEPLOY_ROOT", "TOOLS_ROOT", "ENVPATH"):
        environment.pop(key, None)
    environment.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), ENV="dev", PYTHONPATH="lib")
    script = (
        "from pathlib import Path; from distman import config, util; "
        "from distman.policy import Policy; "
        "policy = Policy(); print(config.DEPLOY_ROOT); print(policy.deploy_root); "
        "print(Path(util.resolve_vars_path('{DEPLOY_ROOT}/bin')))"
    )
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    expected = str(tmp_path / ".local/distman/dev")
    assert process.stdout.splitlines() == [expected, expected, str(Path(expected) / "bin")]

    policy_directory = tmp_path / "policy"
    policy_directory.mkdir()
    custom = tmp_path / "custom/deployment"
    (policy_directory / "distman.env").write_text(
        f"all:\n  DEPLOY_ROOT: {custom.as_posix()}\n",
        encoding="utf-8",
    )
    environment["ENVPATH"] = str(policy_directory)
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.splitlines() == [str(custom), str(custom), str(custom / "bin")]

    environment.pop("ENVPATH")
    for raw, expected_root in (
        ("~/home-relative", tmp_path / "home-relative"),
        ("working-relative", Path(__file__).parents[1] / "working-relative"),
    ):
        environment["DEPLOY_ROOT"] = raw
        process = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
        )
        assert process.returncode == 0, process.stderr
        expected = str(expected_root)
        assert process.stdout.splitlines() == [expected, expected, str(expected_root / "bin")]
