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
Contains tests for the util module.
"""

import os
import unittest
from unittest.mock import patch

from distman.util import (check_symlinks, full_path, get_user, is_file_hidden,
                          is_ignorable, normalize_path, remove_object, walk,
                          yesNo)


class TestUtils(unittest.TestCase):
    def test_check_symlinks(self):
        # Mock the tempfile.mktemp() function to return temporary file paths
        with patch("tempfile.mktemp") as mock_mktemp:
            mock_mktemp.side_effect = ["/tmp/temp_file", "/tmp/link_file"]

            # Mock the os.symlink() function to raise an OSError
            with patch("os.symlink", side_effect=OSError):
                result = check_symlinks()

                # Assert that the method returns False
                self.assertFalse(result)

            # Assert that the temporary files are removed
            self.assertFalse(os.path.exists("/tmp/temp_file"))
            self.assertFalse(os.path.exists("/tmp/link_file"))

    def test_get_user(self):
        # Mock the os.getenv() function to return the user name
        with patch("os.getenv") as mock_getenv:
            mock_getenv.side_effect = ["john", None, None]

            # Call the method to get the user name
            result = get_user()

            # Assert that the correct user name is returned
            self.assertEqual(result, "john")

            # Call the method when both USER and USERNAME environment variables are not set
            result = get_user()

            # Assert that the default user name is returned
            self.assertEqual(result, "unknown")

    def test_is_file_hidden(self):
        # Mock the os.path.basename() and has_hidden_attr() functions
        with patch("os.path.basename") as mock_basename, patch(
            "distman.util.has_hidden_attr"
        ) as mock_has_hidden_attr:
            # Set the return values of the mocked functions
            mock_basename.return_value = ".hidden_file"
            mock_has_hidden_attr.return_value = True

            # Call the method to check if the file is hidden
            result = is_file_hidden("/path/to/file")

            # Assert that the method returns True
            self.assertTrue(result)

    def test_is_ignorable(self):
        # Mock the is_file_hidden() function
        with patch("distman.util.is_file_hidden") as mock_is_file_hidden:
            # Set the return value of the mocked function
            mock_is_file_hidden.return_value = False

            # Call the method to check if the file is ignorable
            result = is_ignorable("/path/to/file")

            # Assert that the method returns False
            self.assertFalse(result)

    def test_normalize_path(self):
        # Call the method to normalize a path
        result = normalize_path("path\\to\\file/")

        # Assert that the path is normalized correctly
        self.assertEqual(result, "path/to/file")

    def test_full_path(self):
        # Call the method to get the full path from a relative path
        result = full_path("/start/directory", "../relative/path")

        # Assert that the full path is returned correctly
        self.assertEqual(result, "/start/relative/path")

    def test_remove_object(self):
        # Mock the os.path.isdir() and os.remove() functions
        with patch("os.path.isdir") as mock_isdir, patch("os.remove") as mock_remove:
            # Set the return value of the mocked isdir() function
            mock_isdir.return_value = True

            # Call the method to remove a directory
            remove_object("/path/to/directory", recurse=True)

            # Assert that the rmtree() function is called
            mock_remove.assert_called_with("/path/to/directory")

            # Call the method to remove a file
            remove_object("/path/to/file")

            # Assert that the remove() function is called
            mock_remove.assert_called_with("/path/to/file")

    def test_yesNo(self):
        # Mock the input() function to return user input
        with patch("builtins.input") as mock_input:
            # Set the return value of the mocked input() function
            mock_input.return_value = "y"

            # Call the method to ask a yes/no question
            result = yesNo("Do you want to continue?")

            # Assert that the method returns True
            self.assertTrue(result)

            # Set the return value of the mocked input() function
            mock_input.return_value = "n"

            # Call the method to ask a yes/no question
            result = yesNo("Do you want to continue?")

            # Assert that the method returns False
            self.assertFalse(result)

    def test_walk(self):
        # Mock the is_ignorable() function
        with patch("distman.util.is_ignorable") as mock_is_ignorable:
            # Set the return value of the mocked is_ignorable() function
            mock_is_ignorable.return_value = False

            # Call the method to walk through the files
            result = list(walk("/path/to/directory"))

            # Assert that the correct file paths are returned
            self.assertEqual(
                result,
                [
                    "/path/to/directory/file1.txt",
                    "/path/to/directory/file2.txt",
                    "/path/to/directory/subdirectory/file3.txt",
                ],
            )


if __name__ == "__main__":
    unittest.main()
