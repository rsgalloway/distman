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

import json
import unittest
from unittest.mock import patch

from distman import Distributor
from distman.dist import get_source_and_dest, confirm, update_symlink


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
    """"Test the update_symlink function when the destination exists."""
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

    with patch("os.path.lexists", return_value=True), patch(
        "distman.util.link_object", return_value=True
    ) as mock_link:
        result = update_symlink(dest, target, dryrun)

        mock_link.assert_called_once_with(target, dest, target)
        assert result is True
