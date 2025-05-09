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


class TestDistributor(unittest.TestCase):
    """Tests for the Distributor class."""

    def setUp(self):
        self.distributor = Distributor()

    def test_read_dist_file(self):
        # Mock the dist file content
        dist_file_content = {
            "author": "John Doe",
            "targets": {
                "target1": {
                    "sourcePath": "path/to/source1",
                    "destPath": "path/to/dest1",
                },
                "target2": {
                    "sourcePath": "path/to/source2",
                    "destPath": "path/to/dest2",
                },
            },
        }

        # Mock the open() function to return the dist file content
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                json.dumps(dist_file_content)
            )

            # Call the method to read the dist file
            result = self.distributor.read_dist_file()

            # Assert that the method returns True
            self.assertTrue(result)

            # Assert that the distributor's root attribute is set correctly
            self.assertEqual(self.distributor.root, dist_file_content)

    def test_get_targets(self):
        # Set the distributor's root attribute
        self.distributor.root = {
            "targets": {
                "target1": {
                    "sourcePath": "path/to/source1",
                    "destPath": "path/to/dest1",
                },
                "target2": {
                    "sourcePath": "path/to/source2",
                    "destPath": "path/to/dest2",
                },
            }
        }

        # Call the method to get the targets
        targets = self.distributor.get_targets()

        # Assert that the targets are returned correctly
        self.assertEqual(targets, self.distributor.root["targets"])

    def test_get_files(self):
        # Set the distributor's directory attribute
        self.distributor.directory = "/path/to/directory"

        # Mock the util.walk() function to return a list of files
        with patch("distman.util.walk") as mock_walk:
            mock_walk.return_value = [
                "/path/to/directory/file1.txt",
                "/path/to/directory/file2.txt",
                "/path/to/directory/subdirectory/file3.txt",
            ]

            # Call the method to get the files
            files = self.distributor.get_files("/path/to/directory")

            # Assert that the files are returned correctly
            self.assertEqual(
                files,
                [
                    "/path/to/directory/file1.txt",
                    "/path/to/directory/file2.txt",
                    "/path/to/directory/subdirectory/file3.txt",
                ],
            )


if __name__ == "__main__":
    unittest.main()
