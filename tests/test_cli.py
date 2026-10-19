"""Compatibility tests for centralized suite and standalone argument definitions."""

import argparse
from typing import List

import pytest

from distman import cache, cli, dist


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
