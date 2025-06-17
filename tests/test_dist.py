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
Contains tests for the dist module.
"""

import os
import tempfile
import shutil
import pytest
from unittest.mock import patch

from distman import config, Distributor
from distman.dist import (
    get_source_and_dest,
    confirm,
    update_symlink,
    get_version_dest,
    should_skip_target,
)


@pytest.fixture
def temp_dir():
    """Fixture to create a temporary directory for testing."""
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)


@pytest.fixture
def mock_dist_dict():
    """Helper function to create a mock distribution dictionary."""
    return {
        "targets": {
            "test_target": {
                "source": "source_path",
                "destination": "{DEPLOY_ROOT}/lib/python/source_path",
            }
        }
    }


@pytest.fixture
def mock_distributor(mocker, temp_dir, mock_dist_dict):
    """Fixture to mock the Distributor class and its methods."""
    mocker.patch("distman.dist.Distributor.read_git_info", return_value=True)
    mocker.patch("distman.dist.Distributor.is_git_behind", return_value=False)
    mocker.patch("distman.dist.Distributor.git_changed_files", return_value=[])
    mocker.patch(
        "distman.dist.Distributor.get_targets", return_value=mock_dist_dict["targets"]
    )
    mocker.patch("distman.util.get_file_versions", return_value=[])
    mocker.patch("distman.util.link_object", return_value=True)
    mocker.patch("distman.util.remove_object", return_value=True)
    mocker.patch("distman.util.replace_vars", side_effect=lambda s: s)
    mocker.patch("distman.util.yesNo", return_value=True)
    mocker.patch("os.makedirs", return_value=None)
    os.environ["DEPLOY_ROOT"] = temp_dir


def test_get_source_and_dest_valid():
    """Test the get_source_and_dest function with valid source and destination."""
    target_dict = {
        "source": "path/to/source",
        "destination": "path/to/dest",
    }
    result = get_source_and_dest(target_dict)
    assert result == ("path/to/source", "path/to/dest")


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


def test_update_symlink_existing_link():
    """ "Test the update_symlink function when the destination exists."""
    dest = "path/to/existing/link"
    target = "path/to/target"
    dryrun = False

    # mock the necessary functions
    with patch("os.path.lexists", return_value=True), patch(
        "distman.util.remove_object"
    ) as mock_remove, patch("distman.util.link_object", return_value=True) as mock_link:
        result = update_symlink(dest, target, dryrun)

        mock_remove.assert_called_once_with(dest)
        mock_link.assert_called_once_with(target, dest, target)
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


def test_update_symlink_no_existing_link():
    """Test the update_symlink function when the destination does not exist."""
    dest = "path/to/nonexistent/link"
    target = "path/to/target"
    dryrun = False

    with patch("os.path.lexists", return_value=False), patch(
        "distman.util.link_object", return_value=True
    ) as mock_link:
        result = update_symlink(dest, target, dryrun)

        mock_link.assert_called_once_with(target, dest, target)
        assert result is True


def test_get_version_dest_creates_versioned_path(temp_dir):
    """Test the get_version_dest function creates a versioned path."""
    dest = os.path.join(temp_dir, "file.txt")
    version_num = 1
    short_head = "abc123"

    with open(dest, "w") as f:
        f.write("hello world")

    result = get_version_dest(dest, version_num, short_head)
    expected = os.path.join(temp_dir, config.DIR_VERSIONS, "file.txt.1.abc123")

    assert result == expected
    assert os.path.exists(os.path.dirname(result))


def test_get_version_dest_without_short_head(temp_dir):
    """Test the get_version_dest function without a short head."""
    dest = os.path.join(temp_dir, "file.txt")
    version_num = 2
    short_head = None

    with open(dest, "w") as f:
        f.write("hello world")

    result = get_version_dest(dest, version_num, short_head)
    expected = os.path.join(temp_dir, config.DIR_VERSIONS, "file.txt.2")

    assert result == expected
    assert os.path.exists(os.path.dirname(result))


def test_get_version_dest_creates_directory(temp_dir):
    """Test the get_version_dest function creates the versions directory."""
    dest = os.path.join(temp_dir, "test.txt")
    version_num = 3
    short_head = "def456"

    with open(dest, "w") as f:
        f.write("hello world")

    result = get_version_dest(dest, version_num, short_head)
    expected_dir = os.path.join(os.path.dirname(dest), config.DIR_VERSIONS)

    assert result == os.path.join(expected_dir, "test.txt.3.def456")
    assert os.path.exists(expected_dir)
    assert os.path.isdir(expected_dir)


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


def test_distributor_initialization():
    """Test the initialization of the Distributor class."""
    distributor = Distributor()
    assert distributor is not None


def test_dist_with_valid_target(mock_distributor, mocker, mock_dist_dict):
    """Test the dist method with a valid target."""
    mocker.patch("os.path.exists", return_value=True)

    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.dist(target="test_target", dryrun=True)
    assert result is True


def test_dist_with_missing_source(mock_distributor, mocker, mock_dist_dict):
    """Test the dist method when the source is missing."""
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.dist(target="test_target", dryrun=False)
    assert result is False


def test_reset_file_version_with_valid_target(mock_distributor, mocker, mock_dist_dict):
    """Test the reset_file_version method with a valid target."""
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.reset_file_version("test_target", dryrun=True)
    assert result is True


def test_change_file_version_with_valid_target(
    mock_distributor, mocker, mock_dist_dict
):
    """Test the change_file_version method with a valid target."""
    mocker.patch("distman.util.get_file_versions", return_value=[
        ("/path/to/test_target.1.abc123", 1, "abc123")
    ])
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.change_file_version("test_target", target_version=1, dryrun=True)
    assert result is True


def test_delete_target_with_existing_target(mock_distributor, mocker, mock_dist_dict):
    """Test the delete_target method with an existing target."""
    dist = Distributor()
    dist.root = mock_dist_dict
    result = dist.delete_target("test_target", dryrun=True)
    assert result is True
