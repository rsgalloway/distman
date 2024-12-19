#!/usr/bin/env python
#
# Copyright (c) 2024, Ryan Galloway (ryan@rsgalloway.com)
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
Contains utility functions and classes.
"""

import ctypes
import fnmatch
import os
import re
import shutil
from collections import defaultdict

from distman import config
from distman.logger import log

# cache regex pattern that matches any patterns in IGNORABLE
IGNORABLE_PATHS = re.compile(
    "(" + ")|(".join([fnmatch.translate(i) for i in config.IGNORABLE]) + ")"
)


def add_symlink_support():
    """Adds symlink support for Windows."""

    def symlink_ms(source, link_name, target_is_directory=False):
        csl = ctypes.windll.kernel32.CreateSymbolicLinkW
        csl.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        csl.restype = ctypes.c_ubyte
        flags = 1 if target_is_directory else 0
        if csl(link_name, source.replace("/", "\\"), flags) == 0:
            raise ctypes.WinError()

    os.symlink = symlink_ms


def check_symlinks():
    """Checks if it is possible to create symbolic links by creatiung a temp file
    and trying to create a link to it.

    :returns: True if symbolic links can be created.
    """
    import tempfile

    temp_file = tempfile.mktemp()
    link_file = tempfile.mktemp()

    with open(temp_file, "a"):
        pass

    try:
        os.symlink(temp_file, link_file)

    except OSError:
        log.warning("Privileges to create symbolic links are required.")
        log.warning(
            "Run as Administrator or change system settings for "
            "SeCreateSymbolicLinkPrivilege."
        )
        os.remove(temp_file)
        return False

    os.remove(link_file)
    os.remove(temp_file)

    return True


def get_user():
    """Returns the current user name."""
    return os.getenv("USER", os.getenv("USERNAME", "unknown"))


def has_hidden_attr(filepath):
    """Checks if file has hidden file attribute (windows only).

    :param filepath: file system path.
    :returns: True if file is hidden.
    """

    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(filepath))
        assert attrs != -1
        result = bool(attrs & 2)

    except (AttributeError, AssertionError):
        result = False

    return result


def is_file_hidden(filepath):
    """Cross platform check if file is hidden. Checks if file name begins
    with period or has hidden attribute.

    :param filepath: file system path.
    :returns: True if file is hidden.
    """
    name = os.path.basename(os.path.abspath(filepath))
    return name.startswith(".") or has_hidden_attr(filepath)


def is_ignorable(filepath):
    """Returns True if path is ignorable. Checks path against patterns
    in the ignorables list, as well as dot files.

    :param path: a file system path.
    :returns: True if filepath is ignorable.
    """

    if is_file_hidden(filepath):
        return True

    return re.search(IGNORABLE_PATHS, filepath) is not None


def get_root_dir(path):
    """Returns the root directory of a path."""
    return os.path.dirname(path).split(os.path.sep)[0]


def get_common_root_dirs(filepaths):
    """Returns a list of common root directories for a list of file paths.

    :param filepaths: list of file paths.
    :returns: list of common root directories.
    """

    path_components = [normalize_path(path).split(os.sep) for path in filepaths]

    # group files by their top-level directory
    directory_tree = defaultdict(list)
    for components in path_components:
        root = components[0]  # Top-level directory
        directory_tree[root].append(components)

    common_directories = set()

    # find common subdirectories within each top-level directory group
    for root, paths in directory_tree.items():
        if len(paths) > 1:
            common_prefix = os.path.join(*os.path.commonprefix(paths))
            common_directories.add(common_prefix)

    return list(common_directories)


def get_path_type(path):
    """Returns the short name of the path type: 'file', 'directory', 'link',
    or 'null' if path does not exist.

    :param path: file system path.
    :returns: name of path type as a string.
    """

    if os.path.islink(path):
        target_type = "link"
    elif os.path.isdir(path):
        target_type = "directory"
    elif os.path.isfile(path):
        target_type = "file"
    else:
        target_type = "null"

    return target_type


def normalize_path(path):
    """Normalizes a path by removing leading "./" and calling os.path.normpath.

    :param path: file system path.
    :returns: normalized path.
    """

    if not path:
        return path
    path = sanitize_path(path)
    return os.path.normpath(path.lstrip("./"))


def sanitize_path(path):
    """Sanitizes a path by changing separators to forward slashes and removing
    trailing slashes.

    :param path: file system path.
    :returns: sanitized path.
    """

    if not path:
        return path
    path = path.replace("\\", "/")
    if path[-1] == "/":
        path = path[:-1]
    return path


def get_dist_info(dest, ext=config.DIST_INFO_EXT):
    """Returns the dist info file path, e.g.

        /path/to/desploy/prod/.foobar.py.dist

    The dist info files are hidden dot files used to store distribution
    information and tell distman if a file has been previously disted.

    :param dest: destination directory.
    :param ext: file extension.
    """
    folder, original_name = os.path.split(dest)
    return os.path.join(folder, f".{original_name}{ext}")


def write_dist_info(dest, dist_info):
    """Writes distribution information to a file.

    :param dest: Path to destination directory.
    :param dist_info: Dictionary of distribution information.
    """
    distinfo = get_dist_info(dest=dest)
    log.debug("Writing dist info to %s" % distinfo)
    with open(distinfo, "w") as outFile:
        for key, value in dist_info.items():
            outFile.write(f"{key}: {value}\n")


def create_dest_folder(dest, dryrun=False, yes=False):
    """Creates destination folder if it does not exist.

    :param dest: destination file path.
    :param dryrun: dry run flag.
    :param yes: yes flag.
    :returns: True if destination folder was created.
    """
    dest_dir = os.path.dirname(dest)

    if not os.path.exists(dest_dir):
        log.info("Creating destination directory '%s'" % dest_dir)
        if not dryrun:
            try:
                os.makedirs(dest_dir)
            except Exception as e:
                log.info(
                    "ERROR: Failed to create directory '%s': %s" % (dest_dir, str(e))
                )
                return False
    elif not os.path.isdir(dest_dir):
        log.info("Directory not found: %s" % dest_dir)
        return False

    # if dist info file does not exist means this is a new target
    distinfo = get_dist_info(dest)
    if not os.path.exists(distinfo):
        if os.path.exists(dest):
            question = (
                "Target '%s' already exists as a %s and will "
                "be deleted, continue?"
                % (dest, "dir" if os.path.isdir(dest) else "file")
            )
            if not yes and not yesNo(question):
                return False
        log.info("Initializing: %s" % dest)


def full_path(start, relative_path):
    """Returns the full path from a relative path.

    :param start: starting directory.
    :param relative_path: relative path.
    :returns: full path.
    """
    if not relative_path:
        raise Exception("Empty path")

    if (
        relative_path[0] == "/"
        or relative_path[0] == "\\"
        or (
            len(relative_path) > 1
            and (relative_path[1] == ":" or relative_path.startswith(".."))
        )
    ):
        path = os.path.abspath(relative_path)
        temp_relative_path = os.path.relpath(path, start=start)
        if temp_relative_path.startswith(".."):
            raise Exception("Path below start directory")
        return path

    if not start:
        start = os.getcwd()

    return os.path.abspath(start + os.path.sep + relative_path)


def remove_object(path, recurse=False):
    """Deletes a file or directory tree.

    :param path: file system path.
    :param recurse: recursively delete directory tree.
    """
    try:
        if os.path.isdir(path):
            if recurse:
                shutil.rmtree(path)
            else:
                os.rmdir(path)
        else:
            os.remove(path)

    except OSError:
        try:
            # no way to tell if a directory is a symlink:
            # os.path.isdir() will return True
            # try to delete as file if fails
            os.remove(path)
        except OSError as e:
            log.error("Error removing '%s': %s" % (path, str(e)))


def yesNo(question):
    """Displays question text to user and reads yes/no input.

    :param question: question text.
    :returns: True if user answers yes.
    """
    while True:
        answer = input(question + " (y/n): ").lower().strip()
        if answer in ("y", "yes", "n", "no"):
            return answer in ("y", "yes")
        else:
            print("You must answer yes or no.")


def walk(path, exclude_ignorables=True):
    """Generator that yields relative file paths that are not ignorable.

    :param path: file system path.
    :param exclude_ignorables: exclude ignorable files.
    :returns: generator of file paths.
    """
    if not is_ignorable(path) and os.path.isfile(path):
        yield path
    for dirname, dirs, files in os.walk(path, topdown=True):
        if exclude_ignorables and is_ignorable(dirname):
            continue
        for d in dirs:
            if exclude_ignorables and is_ignorable(d):
                dirs.remove(d)
        for name in files:
            if not is_ignorable(name):
                yield os.path.join(dirname, name)
