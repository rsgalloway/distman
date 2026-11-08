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

"""Tests for withdrawing active dist targets while retaining their history."""

import json
import os
from pathlib import Path

import pytest

from distman import cli, config, util
from distman.dist.distribution import Distributor
from distman.dist.undist import _add_target, _same_origin, run


def test_project_origins_accept_equivalent_path_separators() -> None:
    """Treat native and sanitized Windows project paths as one identity.

    :return: None.
    """
    assert _same_origin(r"C:\work\sample", "C:/work/sample")
    assert not _same_origin(r"C:\work\sample", "C:/work/other")
    assert not _same_origin("https://example.test/One", "https://example.test/one")

    resolved = {}
    destination = Path(r"C:\deploy\tool")
    _add_target(resolved, destination, r"C:\work\sample")
    _add_target(resolved, destination, "C:/work/sample")
    assert resolved == {destination: "C:/work/sample"}


def managed_target(root: Path, source: Path, name: str = "tool", origin: Path = None) -> tuple:
    """Create one active legacy dist target and its ownership receipt.

    :param root: Deployment root containing the target.
    :param source: Recorded source path.
    :param name: Destination basename.
    :param origin: Optional project identity recorded in the receipt.
    :return: Active destination, retained revision, and receipt paths.
    """
    destination = root / "bin" / name
    revision = destination.parent / "versions" / f"{name}.0.abc1234"
    revision.parent.mkdir(parents=True, exist_ok=True)
    revision.write_text("payload\n", encoding="utf-8")
    os.symlink(Path("versions") / revision.name, destination)
    util.write_dist_file(
        str(destination),
        {
            "name": "sample",
            "origin": str(origin or source.parent),
            "branch": "main",
            "commit": "abc1234",
            "source": str(source),
            "author": "tester",
        },
    )
    return destination, revision, Path(util.get_dist_file(str(destination)))


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure a temporary deployment root.

    :param tmp_path: Pytest temporary directory.
    :param monkeypatch: Environment and module override fixture.
    :return: Temporary deployment root.
    """
    root = tmp_path / "deploy"
    root.mkdir()
    monkeypatch.setattr(config, "DEPLOY_ROOT", str(root))
    return root


def test_direct_undist_retains_receipt_and_revision(deployment: Path, tmp_path: Path) -> None:
    """Withdraw an ad hoc target through the journal without deleting history.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :return: None.
    """
    source = tmp_path / "source" / "tool"
    source.parent.mkdir()
    source.write_text("source payload\n", encoding="utf-8")
    destination, revision, receipt = managed_target(deployment, source)
    args = cli.build_parser().parse_args(
        ["undist", "--source", str(source), "--dest", str(destination), "--yes"]
    )

    assert run(args) == 0
    assert not os.path.lexists(destination)
    assert revision.is_file()
    assert receipt.is_file()
    assert source.read_text(encoding="utf-8") == "source payload\n"
    assert (deployment / ".distman/current.json").is_file()
    assert run(args) == 0


def test_non_git_location_uses_stable_project_origin(
    deployment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Withdraw a non-Git project distributed from another working directory.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :param monkeypatch: Fixture changing the caller's working directory.
    :return: None.
    """
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    distributor = Distributor()
    distributor.directory = str(project)
    distributor.read_git_info()
    assert _same_origin(distributor.path, str(project))

    source = project / "tool"
    destination, _, _ = managed_target(deployment, source, origin=Path(distributor.path))
    (project / "dist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "tool": {
                        "source": "tool",
                        "destination": str(destination),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["undist", str(project), "--yes"])

    assert run(args) == 0
    assert not os.path.lexists(destination)


def test_manifest_undist_does_not_require_source(deployment: Path, tmp_path: Path) -> None:
    """Resolve an exact manifest destination after its source has disappeared.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :return: None.
    """
    project = tmp_path / "project"
    project.mkdir()
    source = project / "old-tool"
    destination, revision, _ = managed_target(deployment, source, origin=project)
    (project / "dist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "tool": {
                        "source": "missing-tool",
                        "destination": str(destination),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["undist", str(project), "--target", "tool", "--yes"])

    assert run(args) == 0
    assert not os.path.lexists(destination)
    assert revision.is_file()


def test_wildcard_undist_discovers_receipt_after_source_removal(
    deployment: Path, tmp_path: Path
) -> None:
    """Use receipts to find wildcard destinations whose sources no longer exist.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :return: None.
    """
    project = tmp_path / "project"
    project.mkdir()
    source = project / "plugins" / "example"
    source.parent.mkdir()
    source.write_text("current source\n", encoding="utf-8")
    destination, _, _ = managed_target(deployment, source, "example", origin=project)
    earlier, _, _ = managed_target(
        deployment, project / "plugins" / "earlier", "earlier", origin=project
    )
    foreign, _, _ = managed_target(
        deployment,
        project / "plugins" / "foreign",
        "foreign",
        origin=tmp_path / "foreign-project",
    )
    (project / "dist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "plugins": {
                        "source": "plugins/*",
                        "destination": str(deployment / "bin" / "%1"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["undist", str(project), "--yes"])

    assert run(args) == 0
    assert not os.path.lexists(destination)
    assert earlier.is_symlink()
    assert foreign.is_symlink()

    args = cli.build_parser().parse_args(["undist", str(project), "--all", "--yes"])
    assert run(args) == 0
    assert not os.path.lexists(earlier)
    assert foreign.is_symlink()


def test_undist_rejects_unmanaged_and_preserves_dryrun(deployment: Path, tmp_path: Path) -> None:
    """Reject unmanaged paths and keep managed links during previews.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :return: None.
    """
    unmanaged = deployment / "bin" / "unmanaged"
    unmanaged.parent.mkdir(parents=True)
    unmanaged.write_text("external\n", encoding="utf-8")
    args = cli.build_parser().parse_args(["undist", "--dest", str(unmanaged), "--yes"])
    assert run(args) == 1
    assert unmanaged.is_file()

    destination, _, _ = managed_target(deployment, tmp_path / "tool")
    args = cli.build_parser().parse_args(["undist", "--dest", str(destination), "--dryrun"])
    assert run(args) == 0
    assert destination.is_symlink()
    assert not (deployment / ".distman/current.json").exists()


def test_undist_rolls_back_multiple_links_after_failure(
    deployment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore earlier links when a later unlink fails during publication.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :param monkeypatch: Fixture used to inject one unlink failure.
    :return: None.
    """
    project = tmp_path / "project"
    project.mkdir()
    first_source = project / "first"
    second_source = project / "second"
    first, _, _ = managed_target(deployment, first_source, "first", origin=project)
    second, _, _ = managed_target(deployment, second_source, "second", origin=project)
    (project / "dist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "first": {"source": "first", "destination": str(first)},
                    "second": {"source": "second", "destination": str(second)},
                },
            }
        ),
        encoding="utf-8",
    )
    original_unlink = Path.unlink
    failed = False

    def fail_second_once(path: Path, *args: object, **kwargs: object) -> None:
        """Fail the second target once, then permit journal recovery.

        :param path: Path being removed.
        :param args: Positional arguments forwarded to ``Path.unlink``.
        :param kwargs: Keyword arguments forwarded to ``Path.unlink``.
        :return: None.
        :raises OSError: On the first attempt to unlink the second target.
        """
        nonlocal failed
        if path == second and not failed:
            failed = True
            raise OSError("injected unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_once)
    args = cli.build_parser().parse_args(["undist", str(project), "--yes"])

    assert run(args) == 1
    assert first.is_symlink()
    assert second.is_symlink()
    assert not (deployment / ".distman/pending.json").exists()


def test_manifest_selection_skips_absent_and_unselected_targets(
    deployment: Path, tmp_path: Path
) -> None:
    """Withdraw only selected deployed targets and ignore an absent configured target.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :return: None.
    """
    project = tmp_path / "project"
    project.mkdir()
    selected_source = project / "selected"
    other_source = project / "other"
    selected, _, _ = managed_target(deployment, selected_source, "selected", origin=project)
    other, _, _ = managed_target(deployment, other_source, "other", origin=project)
    absent = deployment / "bin" / "absent"
    (project / "dist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "plugin-selected": {
                        "source": "selected",
                        "destination": str(selected),
                    },
                    "other": {"source": "other", "destination": str(other)},
                    "plugin-absent": {
                        "source": "absent",
                        "destination": str(absent),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["undist", str(project), "--target", "plugin-*", "--yes"])

    assert run(args) == 0
    assert not os.path.lexists(selected)
    assert other.is_symlink()
    assert not os.path.lexists(absent)


def test_manifest_rejects_unresolved_wildcard_destination(deployment: Path, tmp_path: Path) -> None:
    """Reject a wildcard destination with placeholders left after expansion.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :return: None.
    """
    project = tmp_path / "project"
    source = project / "plugins" / "example"
    source.parent.mkdir(parents=True)
    source.write_text("payload", encoding="utf-8")
    destination = deployment / "bin" / "example" / "%2"
    managed, _, _ = managed_target(deployment, source, "unrelated", origin=project)
    (project / "dist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "plugins": {
                        "source": "plugins/*",
                        "destination": str(destination).replace("example", "%1"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["undist", str(project), "--yes"])

    assert run(args) == 1
    assert managed.is_symlink()


def test_manifest_rejects_ambiguous_destination(deployment: Path, tmp_path: Path) -> None:
    """Fail before mutation when selected sources resolve to one destination.

    :param deployment: Temporary deployment root.
    :param tmp_path: Pytest temporary directory.
    :return: None.
    """
    project = tmp_path / "project"
    project.mkdir()
    first_source = project / "first"
    destination, _, _ = managed_target(deployment, first_source, origin=project)
    (project / "dist.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "first": {"source": "first", "destination": str(destination)},
                    "second": {"source": "second", "destination": str(destination)},
                },
            }
        ),
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["undist", str(project), "--yes"])

    assert run(args) == 1
    assert destination.is_symlink()
