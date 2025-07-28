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
import filecmp
import tempfile
import shutil
import pytest
from distman import util


@pytest.fixture
def temp_dir():
    """Fixture to create a temporary directory for testing."""
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path)


def test_normalize_path():
    """Test the normalize_path function to ensure it correctly normalizes
    paths."""
    assert util.normalize_path("./foo/bar/") == os.path.normpath("foo/bar")
    assert util.normalize_path("") == "."


def test_sanitize_path():
    """Test the sanitize_path function to ensure it correctly sanitizes
    paths."""
    assert util.sanitize_path("foo\\bar\\") == "foo/bar"
    assert util.sanitize_path("foo/bar/") == "foo/bar"


def test_get_path_type(temp_dir):
    """Test the get_path_type function to ensure it correctly identifies file
    types."""
    file_path = os.path.join(temp_dir, "test.txt")
    dir_path = os.path.join(temp_dir, "subdir")
    link_path = os.path.join(temp_dir, "link")

    with open(file_path, "w") as f:
        f.write("hello world")

    os.mkdir(dir_path)
    os.symlink(file_path, link_path)

    assert util.get_path_type(file_path) == "file"
    assert util.get_path_type(dir_path) == "directory"
    assert util.get_path_type(link_path) == "link"
    assert util.get_path_type(os.path.join(temp_dir, "nonexistent")) == "null"


def test_copy_file_and_compare(temp_dir):
    """Test the copy_file function to ensure it correctly copies files and
    compares them."""
    src = os.path.join(temp_dir, "src.txt")
    dst = os.path.join(temp_dir, "dst.txt")

    with open(src, "w") as f:
        f.write("line1\r\nline2\nline3\r")

    util.copy_file(src, dst)

    with open(dst, "r") as f:
        lines = f.readlines()
    assert lines == ["line1\n", "line2\n", "line3\n"]
    assert util.compare_files(src, dst)


def test_copy_file_binary_file(temp_dir):
    """Test the copy_file function to ensure it correctly copies binary files."""
    src = os.path.join(temp_dir, "binary_file.bin")
    dst = os.path.join(temp_dir, "copied_binary_file.bin")

    # write 1KB of random binary data
    with open(src, "wb") as f:
        f.write(os.urandom(1024))

    util.copy_file(src, dst)

    # verify the copied file is the same as the original
    assert util.compare_files(src, dst)
    assert filecmp.cmp(src, dst)


def test_remove_object(temp_dir):
    """Test the remove_object function to ensure it correctly removes files and
    directories."""
    file_path = os.path.join(temp_dir, "file.txt")
    dir_path = os.path.join(temp_dir, "dir")
    os.mkdir(dir_path)
    with open(file_path, "w") as f:
        f.write("data")
    assert os.path.exists(file_path)
    util.remove_object(file_path)
    assert not os.path.exists(file_path)

    assert os.path.exists(dir_path)
    util.remove_object(dir_path)
    assert not os.path.exists(dir_path)


def test_replace_vars(monkeypatch):
    """Test the replace_vars function to ensure it correctly replaces environment
    variables."""
    monkeypatch.setenv("FOO", "bar")
    result = util.replace_vars("path/to/{FOO}/dir")
    assert result == "path/to/bar/dir"


def test_replace_vars_missing_strict():
    """Test the replace_vars function to ensure it raises a ValueError."""
    with pytest.raises(ValueError):
        util.replace_vars("path/to/{MISSING}/dir")


def test_replace_vars_missing_not_strict(monkeypatch):
    """Test the replace_vars function to ensure it does not replace vars that
    are missing."""
    result = util.replace_vars("path/to/{MISSING}/dir", strict=False)
    assert result == "path/to/{MISSING}/dir"


def test_replace_vars_binary(temp_dir):
    """Test the replace_vars function with a binary file."""
    src = os.path.join(temp_dir, "binary_file_3.bin")

    # write 1KB of random binary data
    with open(src, "wb") as f:
        f.write(os.urandom(1024))

    with pytest.raises(TypeError):
        util.replace_vars(open(src, "rb"))


def test_hashes_equal():
    """Test the hashes_equal function to ensure it correctly compares hashes."""
    assert util.hashes_equal("abc123", "ABC123")
    assert util.hashes_equal("ABC", "abcdef")
    assert util.hashes_equal("abcdef", "ABC")


def test_get_user(monkeypatch):
    """Test the get_user function to ensure it correctly retrieves the current
    user."""
    monkeypatch.setenv("USER", "alice")
    assert util.get_user() == "alice"
    monkeypatch.delenv("USER")
    monkeypatch.setenv("USERNAME", "bob")
    assert util.get_user() == "bob"


def test_expand_wildcard_entry(temp_dir):
    """Test the expand_wildcard_entry function to ensure it correctly expands
    wildcard patterns."""

    # setup some test files
    os.makedirs(os.path.join(temp_dir, "build"), exist_ok=True)
    with open(os.path.join(temp_dir, "build", "file1.txt"), "w") as f:
        f.write("Test file 1")
    with open(os.path.join(temp_dir, "build", "file2.txt"), "w") as f:
        f.write("Test file 2")

    source_pattern = os.path.join(temp_dir, "build", "*")
    destination_template = "{DEPLOY_ROOT}/lib/python/%1"

    expected_results = [
        (
            os.path.join(temp_dir, "build", "file1.txt"),
            "{DEPLOY_ROOT}/lib/python/file1.txt",
        ),
        (
            os.path.join(temp_dir, "build", "file2.txt"),
            "{DEPLOY_ROOT}/lib/python/file2.txt",
        ),
    ]

    results = util.expand_wildcard_entry(source_pattern, destination_template)

    assert len(results) == len(expected_results)
    for result, expected in zip(results, expected_results):
        assert result[0] == expected[0]
        assert result[1] == expected[1]


def test_get_file_versions(temp_dir):
    """Test the get_file_versions function to ensure it correctly retrieves file
    versions."""
    versioned_dir = os.path.join(temp_dir, "versions")
    os.makedirs(versioned_dir, exist_ok=True)

    # create versioned files
    base_filename = "testfile.txt"
    versions = [
        "testfile.txt.1.commitA",
        "testfile.txt.2.commitB",
        "testfile.txt.1.commitC",
        "testfile.txt.3.commitD",
        "testfile.txt.2.commitA",
    ]

    for version in versions:
        with open(os.path.join(versioned_dir, version), "w") as f:
            f.write("Versioned content")

    expected_results = [
        (os.path.join(versioned_dir, "testfile.txt.1.commitC"), 1, "commitC"),
        (os.path.join(versioned_dir, "testfile.txt.1.commitA"), 1, "commitA"),
        (os.path.join(versioned_dir, "testfile.txt.2.commitB"), 2, "commitB"),
        (os.path.join(versioned_dir, "testfile.txt.2.commitA"), 2, "commitA"),
        (os.path.join(versioned_dir, "testfile.txt.3.commitD"), 3, "commitD"),
    ]

    target = os.path.join(os.path.dirname(versioned_dir), base_filename)
    result = util.get_file_versions(target)

    assert len(result) == len(expected_results)
    for res, exp in zip(result, expected_results):
        assert res[0] == exp[0]
        assert res[1] == exp[1]
        assert res[2] == exp[2]


def test_link_object(temp_dir):
    """Test the link_object function to ensure it correctly creates symbolic
    links."""
    target_file = os.path.join(temp_dir, "target.txt")
    link_file = os.path.join(temp_dir, "link_to_target.txt")

    with open(target_file, "w") as f:
        f.write("This is a target file.")

    # test creating a symbolic link
    assert util.link_object(target_file, link_file, target_file) is True
    assert os.path.islink(link_file)
    assert os.readlink(link_file) == target_file

    # test linking to a non-existent target
    link_file_2 = os.path.join(temp_dir, "link_to_non_existent.txt")
    assert (
        util.link_object("non_existent.txt", link_file_2, "non_existent.txt") is False
    )
    assert not os.path.exists(link_file_2)

    os.remove(link_file)
    os.remove(target_file)


def test_find_matching_versions(temp_dir):
    """Test the find_matching_versions function to ensure it correctly finds
    matching versions of a file."""
    source_dir = os.path.join(temp_dir, "src", "source.txt")
    source_file = os.path.join(source_dir, "source.txt")
    dest_dir = os.path.join(temp_dir, "dest")
    target_file = os.path.join(dest_dir, "source.txt")
    versions_dir = os.path.join(dest_dir, "versions")
    os.makedirs(source_dir, exist_ok=True)
    os.makedirs(dest_dir, exist_ok=True)
    os.makedirs(versions_dir, exist_ok=True)

    # create a source file
    with open(source_file, "w") as f:
        f.write("this is the source file")

    # create versioned files in the destination directory
    versioned_files = [
        "source.txt.1.commitA",
        "source.txt.2.commitB",
        "source.txt.1.commitC",
        "source.txt.3.commitD",
        "source.txt.2.commitA",
    ]

    for version in versioned_files:
        # commitA files match source file content
        if version.endswith(".commitA"):
            content = "this is the source file"
        # commitB files have different content
        else:
            content = f"this is the versioned file for {version}"
        with open(os.path.join(versions_dir, version), "w") as f:
            f.write(content)

    # test without commit_hash, should match commitA
    results = util.find_matching_versions(source_file, target_file)
    assert len(results) == 2

    # test with a specific commit_hash, should match commitB
    results = util.find_matching_versions(
        source_file, target_file, commit_hash="commitB"
    )
    assert len(results) == 1

    # test with force option, should match commitA
    results = util.find_matching_versions(source_file, target_file, force=True)
    assert len(results) == 2


def test_safe_copytree(temp_dir):
    """Test the safe_copytree function to ensure it correctly copies trees."""
    src_dir = os.path.join(temp_dir, "src")
    dst_dir = os.path.join(temp_dir, "dst")
    os.makedirs(src_dir, exist_ok=True)

    # create some test files and directories
    with open(os.path.join(src_dir, "file1.txt"), "w") as f:
        f.write("This is file 1.")
    os.makedirs(os.path.join(src_dir, "subdir"), exist_ok=True)
    with open(os.path.join(src_dir, "subdir", "file2.txt"), "w") as f:
        f.write("This is file 2.")

    assert os.path.exists(os.path.join(src_dir, "file1.txt"))
    assert os.path.exists(os.path.join(src_dir, "subdir", "file2.txt"))

    # double-check the source directory structure
    files = [f for f in util.walk(src_dir)]
    assert len(files) == 2

    # perform the copy
    util.safe_copytree(src_dir, dst_dir)

    # check if the files were copied correctly
    assert os.path.exists(os.path.join(dst_dir, "file1.txt"))
    assert os.path.exists(os.path.join(dst_dir, "subdir", "file2.txt"))

    # check the contents of the copied files
    with open(os.path.join(dst_dir, "file1.txt"), "r") as f:
        assert f.read() == "This is file 1."
    with open(os.path.join(dst_dir, "subdir", "file2.txt"), "r") as f:
        assert f.read() == "This is file 2."


def test_safe_copytree_into_subdir(temp_dir):
    """Test the safe_copytree function to ensure it does not copy recursively."""
    src_dir = os.path.join(temp_dir, "src")
    dst_dir = os.path.join(src_dir, "subdir")  # dst is a subdir of src

    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "file1.txt"), "w") as f:
        f.write("This is file 1.")

    # perform the copy
    util.safe_copytree(src_dir, dst_dir)

    assert os.path.exists(os.path.join(dst_dir, "file1.txt"))

    # check the contents of the copied files
    with open(os.path.join(dst_dir, "file1.txt"), "r") as f:
        assert f.read() == "This is file 1."


def test_safe_copytree_valueerror(temp_dir):
    """Test the safe_copytree function to ensure it raises ValueError when
    trying to copy to the same directory."""
    src_dir = os.path.join(temp_dir, "src")

    with pytest.raises(ValueError):
        util.safe_copytree(src_dir, src_dir)
