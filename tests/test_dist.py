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
Contains tests for the dist module.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from distman import config, util
from distman.dist import (
    Distributor,
    apply_destination_template,
    confirm,
    get_source_and_dest,
    get_version_dest,
    match_source_pattern,
    parse_args,
    run,
    should_skip_target,
    update_symlink,
)


@pytest.fixture
def temp_dir():
    """Fixture to create a temporary directory for testing."""
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)


@pytest.fixture
def mock_dist_dict():
    """Helper function to create a mock distribution dict."""
    return {
        "targets": {
            "test_target": {
                "source": "source_path",
                "destination": "{DEPLOY_ROOT}/lib/python/source_path",
            }
        }
    }


@pytest.fixture
def mock_dist_dict_with_pipeline():
    """Helper function to create a mock distribution dict with pipeline steps."""
    return {
        "targets": {
            "test_target": {
                "source": "source_path",
                "destination": "{DEPLOY_ROOT}/lib/python/source_path",
                "options": {"match": "content"},
                "pipeline": {"formatting": {"script": ["black --check {input}"]}},
            }
        }
    }


@pytest.fixture
def mock_distributor(mocker, monkeypatch, temp_dir, mock_dist_dict):
    """Fixture to mock the Distributor class and its methods."""
    mocker.patch("distman.dist.Distributor.read_git_info", return_value=True)
    mocker.patch("distman.dist.Distributor.is_git_behind", return_value=False)
    mocker.patch("distman.dist.Distributor.git_changed_files", return_value=[])
    mocker.patch("distman.dist.Distributor.get_targets", return_value=mock_dist_dict["targets"])
    mocker.patch("distman.util.get_file_versions", return_value=[])
    mocker.patch("distman.util.link_object", return_value=True)
    mocker.patch("distman.util.remove_object", return_value=True)
    mocker.patch("distman.util.yesNo", return_value=True)
    monkeypatch.setenv("DEPLOY_ROOT", temp_dir)
    monkeypatch.setattr(config, "DEPLOY_ROOT", temp_dir)


def test_get_source_and_dest_valid():
    """Test the get_source_and_dest function with valid source and destination."""
    target_dict = {
        "source": "path/to/source",
        "destination": "path/to/dest",
    }
    result = get_source_and_dest(target_dict)
    r1, r2 = result
    assert (Path(r1), Path(r2)) == (Path("path/to/source"), Path("path/to/dest"))


def test_get_source_and_dest_missing_source():
    """Test the get_source_and_dest function with missing source."""
    target_dict = {
        "destination": "path/to/dest",
    }
    result = get_source_and_dest(target_dict)
    assert result is None


def test_get_source_and_dest_missing_dest():
    """Test the get_source_and_dest function with missing destination."""
    target_dict = {
        "source": "path/to/source",
    }
    result = get_source_and_dest(target_dict)
    assert result is None


def test_get_source_and_dest_invalid_paths():
    """Test the get_source_and_dest function with invalid paths."""
    target_dict = {
        "source": None,
        "destination": None,
    }
    result = get_source_and_dest(target_dict)
    assert result is None


def test_confirm_yes():
    """Test the confirm function with yes prompt returning True."""
    result = confirm("Proceed?", yes=True, dryrun=False)
    assert result is True


def test_confirm_dryrun():
    """Test the confirm function with dryrun returning True."""
    result = confirm("Proceed?", yes=False, dryrun=True)
    assert result is True


def test_confirm_no():
    """Test the confirm function with no prompt returning False."""
    with patch("distman.util.yesNo", return_value=False):
        result = confirm("Proceed?", yes=False, dryrun=False)
        assert result is False


def test_confirm_yesNo():
    """Test the confirm function with yesNo prompt returning True."""
    with patch("distman.util.yesNo", return_value=True):
        result = confirm("Proceed?", yes=False, dryrun=False)
        assert result is True


def test_update_symlink_dryrun():
    """Test the update_symlink function when dryrun is True and the destination exists."""
    dest = "path/to/existing/link"
    target = "path/to/target"
    dryrun = True

    with patch("os.path.lexists", return_value=True), patch(
        "distman.util.remove_object"
    ) as mock_remove:
        result = update_symlink(dest, target, dryrun)

        mock_remove.assert_not_called()
        assert result is True


def test_update_symlink_existing_link():
    """Test the update_symlink function when the destination exists."""
    dest = "path/to/existing/link"
    target = "path/to/target"
    dryrun = False

    with patch("os.path.lexists", return_value=True), patch(
        "distman.util.remove_object"
    ) as mock_remove, patch("distman.util.link_object", return_value=True) as mock_link:
        result = update_symlink(dest, target, dryrun)

        mock_remove.assert_called_once_with(dest)
        version_dest = util.get_rel_version_path(target)
        mock_link.assert_called_once_with(version_dest, dest, target)
        assert result is True


def test_update_symlink_no_existing_link():
    """Test the update_symlink function when the destination does not exist."""
    dest = "path/to/nonexistent/link"
    target = "path/to/target"
    dryrun = False

    with patch("os.path.lexists", return_value=False), patch(
        "distman.util.link_object", return_value=True
    ) as mock_link:
        result = update_symlink(dest, target, dryrun)

        version_dest = util.get_rel_version_path(target)
        mock_link.assert_called_once_with(version_dest, dest, target)
        assert result is True


def test_get_version_dest_with_short_head(temp_dir):
    """Test the get_version_dest function with a short head."""
    dest = os.path.join(temp_dir, "file.txt")
    version_num = 1
    short_head = "abc123"

    with open(dest, "w") as f:
        f.write("hello world")

    result = get_version_dest(dest, version_num, short_head)
    expected = os.path.join(temp_dir, config.DIR_VERSIONS, "file.txt.1.abc123")

    assert Path(result) == Path(expected)


def test_get_version_dest_without_short_head(temp_dir):
    """Test the get_version_dest function without a short head."""
    dest = os.path.join(temp_dir, "file.txt")
    version_num = 2
    short_head = None

    with open(dest, "w") as f:
        f.write("hello world")

    result = get_version_dest(dest, version_num, short_head)
    expected = os.path.join(temp_dir, config.DIR_VERSIONS, "file.txt.2")

    assert Path(result) == Path(expected)


def test_get_version_dest_version_num(temp_dir):
    """Test the get_version_dest function with a short head and version number."""
    dest = os.path.join(temp_dir, "test.txt")
    version_num = 3
    short_head = "def456"

    with open(dest, "w") as f:
        f.write("hello world")

    result = get_version_dest(dest, version_num, short_head)
    expected_dir = os.path.join(os.path.dirname(dest), config.DIR_VERSIONS)

    assert Path(result) == Path(os.path.join(expected_dir, "test.txt.3.def456"))


def test_should_skip_target_with_matching_pattern():
    """Test should_skip_target function with a matching pattern."""
    target_name = "example_target"
    pattern = "example_target"
    result = should_skip_target(target_name, pattern)
    assert result is False


def test_should_skip_target_with_matching_wildcard_pattern():
    """Test should_skip_target function with a matching wildcard pattern."""
    target_name = "example_target"
    pattern = "example*"
    result = should_skip_target(target_name, pattern)
    assert result is False


def test_should_skip_target_with_non_matching_pattern():
    """Test should_skip_target function with a non-matching pattern."""
    target_name = "example_target"
    pattern = "test*"
    result = should_skip_target(target_name, pattern)
    assert result is True


def test_should_skip_target_with_none_pattern():
    """Test should_skip_target function with None pattern."""
    target_name = "example_target"
    pattern = None
    result = should_skip_target(target_name, pattern)
    assert result is False


def test_should_skip_target_with_empty_pattern():
    """Test should_skip_target function with an empty pattern."""
    target_name = "example_target"
    pattern = ""
    result = should_skip_target(target_name, pattern)
    assert result is True


def test_match_source_pattern_exact():
    """Exact source matches should return an empty capture tuple."""
    assert match_source_pattern("build/pyparser", "build/pyparser") == ()


def test_match_source_pattern_wildcard():
    """Wildcard source matches should expose capture groups for destination templates."""
    assert match_source_pattern("build/pyparser", "build/*") == ("pyparser",)


def test_match_source_pattern_no_match():
    """Non-matching sources should return None."""
    assert match_source_pattern("build/pyparser", "lib/*") is None


def test_apply_destination_template():
    """Destination templates should substitute wildcard capture groups."""
    assert (
        apply_destination_template("{DEPLOY_ROOT}/lib/python/%1", ("pyparser",))
        == "{DEPLOY_ROOT}/lib/python/pyparser"
    )


def test_distributor_initialization():
    """Test the initialization of the Distributor class."""
    distributor = Distributor()
    assert distributor is not None


def test_dist_with_valid_target(mock_distributor, mocker, mock_dist_dict):
    """Test the dist method with a valid target."""

    # Create the temp file, then CLOSE it before dist() tries to read/copy it.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".source_path") as temp_file:
        source_path = temp_file.name

    try:
        mock_dist_dict["targets"]["test_target"]["source"] = source_path

        distributor = Distributor()
        distributor.root = mock_dist_dict
        result = distributor.dist(target="test_target", yes=True, dryrun=False)
        assert result is True
    finally:
        os.remove(source_path)


def test_dist_with_pipeline_steps(mock_distributor, mocker, mock_dist_dict_with_pipeline):
    """Test the dist method with pipeline steps."""
    mocker.patch("os.path.exists", return_value=True)

    dist = Distributor()
    dist.root = mock_dist_dict_with_pipeline
    result = dist.dist(target="test_target", dryrun=True)
    assert result is True


def test_dist_with_missing_source(mock_distributor, mocker, mock_dist_dict):
    """Test the dist method when the source is missing."""
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.dist(target="test_target", dryrun=False)
    assert result is False


def test_dist_with_source_override_matches_config(mocker, temp_dir):
    """A CLI source should match configured source patterns and reuse the target destination."""
    build_dir = Path(temp_dir) / "build"
    build_dir.mkdir()
    source_dir = build_dir / "pyparser"
    source_dir.mkdir()
    (source_dir / "module.py").write_text("print('hi')\n", encoding="utf-8")

    dist = Distributor()
    dist.directory = temp_dir
    dist.root = {
        "targets": {
            "build": {
                "source": "build/*",
                "destination": "{DEPLOY_ROOT}/lib/python/%1",
            }
        }
    }

    mocker.patch("distman.dist.Distributor.read_git_info", return_value=True)
    mocker.patch("distman.dist.Distributor.is_git_behind", return_value=False)
    mocker.patch("distman.dist.Distributor.git_changed_files", return_value=[])
    mocker.patch("distman.util.get_file_versions", return_value=[])
    mocker.patch("distman.util.yesNo", return_value=True)

    result = dist.dist(source="build/pyparser", yes=True, dryrun=True)
    assert result is True


def test_dist_with_dest_requires_source_or_target(mocker, temp_dir):
    """A destination override should not apply to every configured target implicitly."""
    dist = Distributor()
    dist.directory = temp_dir
    dist.root = {
        "targets": {
            "lib": {
                "source": "lib/distman",
                "destination": "{DEPLOY_ROOT}/lib/python/distman",
            },
            "bin": {
                "source": "bin/dist",
                "destination": "{DEPLOY_ROOT}/bin/dist",
            },
        }
    }

    mocker.patch("distman.dist.Distributor.read_git_info", return_value=True)
    mocker.patch("distman.dist.Distributor.git_changed_files", return_value=[])

    result = dist.dist(dest=os.path.join(temp_dir, "deploy", "distman"), dryrun=True)
    assert result is False


def test_dist_with_source_and_dest_without_dist_file(mocker, temp_dir):
    """CLI source and destination should support ad hoc deployment without dist.json."""
    source_dir = Path(temp_dir) / "build" / "foobar"
    source_dir.mkdir(parents=True)
    (source_dir / "module.py").write_text("print('hi')\n", encoding="utf-8")

    dist = Distributor()
    dist.directory = temp_dir
    dist.root = None

    mocker.patch("distman.dist.Distributor.read_git_info", return_value=True)
    mocker.patch("distman.dist.Distributor.is_git_behind", return_value=False)
    mocker.patch("distman.dist.Distributor.git_changed_files", return_value=[])
    mocker.patch("distman.util.get_file_versions", return_value=[])
    mocker.patch("distman.util.yesNo", return_value=True)

    destination = os.path.join(temp_dir, "deploy", "lib", "python", "foobar")
    result = dist.dist(source="build/foobar", dest=destination, yes=True, dryrun=True)
    assert result is True


def test_dist_with_invalid_direct_dest_returns_false(mocker, temp_dir):
    """Invalid ad hoc destination variables should fail cleanly."""
    source_file = Path(temp_dir) / "artifact.txt"
    source_file.write_text("artifact\n", encoding="utf-8")

    dist = Distributor()
    dist.directory = temp_dir
    dist.root = None

    mocker.patch("distman.dist.Distributor.read_git_info", return_value=True)
    mocker.patch("distman.dist.Distributor.is_git_behind", return_value=False)
    mocker.patch("distman.dist.Distributor.git_changed_files", return_value=[])

    result = dist.dist(source="artifact.txt", dest="{MISSING_ROOT}/artifact.txt")
    assert result is False


def test_run_ad_hoc_dist_without_dist_file(tmp_path, monkeypatch):
    """The CLI should dist a file directly from a directory without dist.json."""
    source_file = tmp_path / "artifact.txt"
    source_file.write_text("artifact\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    args = parse_args(
        [
            "--source",
            "artifact.txt",
            "--dest",
            "deploy/artifact.txt",
            "--dryrun",
            "--yes",
        ]
    )

    assert run(args) == 0


def test_reset_file_version_with_valid_target(mock_distributor, mocker, mock_dist_dict):
    """Test the reset_file_version method with a valid target."""
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.reset_file_version("test_target", dryrun=True)
    assert result is True


def test_change_file_version_with_valid_target(mock_distributor, mocker, mock_dist_dict):
    """Test the change_file_version method with a valid target."""
    mocker.patch(
        "distman.util.get_file_versions",
        return_value=[("/path/to/test_target.1.abc123", 1, "abc123")],
    )
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.change_file_version("test_target", target_version=1, dryrun=True)
    assert result is True


def test_delete_target_with_existing_target(mock_distributor, mocker, mock_dist_dict):
    """Test the delete_target method with an existing target."""
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch(
        "distman.util.get_file_versions",
        return_value=[("/path/to/test_target.1.abc123", 1, "abc123")],
    )
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.delete_target("test_target", dryrun=True)
    assert result is True


def test_delete_target_with_no_versions(mock_distributor, mocker, mock_dist_dict):
    """Test the delete_target method with no versions found."""
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("distman.util.get_file_versions", return_value=[])
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.delete_target("test_target", dryrun=True)
    assert result is False


@pytest.mark.skipif(os.name != "nt", reason="Windows-only handle regression test")
def test_dist_does_not_emit_popen_del_invalid_handle(tmp_path):
    """
    Run distman in a fresh interpreter and assert we don't get the
    'Exception ignored in: Popen.__del__ ... WinError 6' noise on stderr.

    This catches GitPython subprocess-handle leaks on early-return code paths.
    """
    import subprocess
    import sys

    # make a minimal dist.json that will fail early in a deterministic way.
    # we intentionally use an unresolved var in destination to trigger an early return.
    dist_json = tmp_path / "dist.json"
    dist_json.write_text(
        """{
  "version": 2,
  "targets": {
    "t": {
      "source": ".",
      "destination": "{THIS_VAR_DOES_NOT_EXIST}/x"
    }
  }
}""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    # be sure we don't accidentally have that var set in CI
    env.pop("THIS_VAR_DOES_NOT_EXIST", None)

    cmd = [sys.executable, "-c", "from distman.dist import main; raise SystemExit(main(['-d']))"]

    p = subprocess.run(
        cmd,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )

    # w expect non-zero exit due to invalid dest var; that's fine
    assert p.returncode != 0

    # the actual regression assertion:
    stderr = p.stderr or ""
    assert "Exception ignored in: <function Popen.__del__" not in stderr
    assert "WinError 6" not in stderr
