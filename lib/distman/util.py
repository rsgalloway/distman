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
Contains utility functions and classes.
"""

import ctypes
import filecmp
import fnmatch
import glob
import os
import re
import shutil
import tempfile
from typing import List, Tuple, Generator, Optional

from distman import config
from distman.logger import log

# Precompiled regex for ignorable paths
IGNORABLE_PATHS = re.compile(
    "(" + ")|(".join([fnmatch.translate(i) for i in config.IGNORABLE]) + ")"
)


def add_symlink_support() -> None:
    """Adds symlink support on Windows by monkeypatching os.symlink."""

    def symlink_ms(source, link_name, target_is_directory=False):
        csl = ctypes.windll.kernel32.CreateSymbolicLinkW
        csl.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        csl.restype = ctypes.c_ubyte
        flags = 1 if target_is_directory else 0
        if csl(link_name, source.replace("/", "\\"), flags) == 0:
            raise ctypes.WinError()

    os.symlink = symlink_ms


def check_symlinks() -> bool:
    """Checks if the system can create symbolic links.

    :return: True if symbolic links can be created, False otherwise.
    """
    temp_file = tempfile.mktemp()
    link_file = tempfile.mktemp()

    with open(temp_file, "w"):
        pass

    try:
        os.symlink(temp_file, link_file)
    except OSError:
        log.warning(
            "Unable to create symbolic links. Admin privileges may be required."
        )
        os.remove(temp_file)
        return False
    finally:
        if os.path.exists(link_file):
            os.remove(link_file)
        if os.path.exists(temp_file):
            os.remove(temp_file)

    return True


def copy_file(
    source: str,
    dest: str,
    substitute_tokens: bool = False,
    token_env=None,
    token_defaults=None,
) -> None:
    """Copies a file or link. Converts line endings to linux LF, preserving
    original source file mode.

    Substitutes tokens in the file if substitute_tokens is True. Tokens are in
    the form of {TOKEN} and can be replaced with environment variables or default
    values.

    :param source: Path to source file or link.
    :param dest: Path to destination.
    :param substitute_tokens: If True, replaces tokens in the file
        with environment variables or defaults.
    :param token_env: Optional dictionary of environment variables to use for
        token substitution.
    :param token_defaults: Optional dictionary of default values for token
        substitution.
    :return: None
    """
    if token_env is None:
        token_env = os.environ
    if token_defaults is None:
        token_defaults = config.DEFAULT_ENV

    def is_binary(file_path):
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\0" in chunk

    try:
        destdir = os.path.dirname(dest)
        if not os.path.isdir(destdir):
            os.makedirs(destdir)
        if os.path.islink(source):
            linkto = os.readlink(source)
            try:
                os.symlink(linkto, dest, target_is_directory=os.path.isdir(linkto))
            except OSError as e:
                log.error("Failed to create symbolic link: %s" % str(e))
        # copy file, converting line endings to LF and replacing tokens
        else:
            with open(source, "r") as infile, open(dest, "wb") as outfile:
                for line in infile:
                    line = line.replace("\r\n", "\n").replace("\r", "\n")
                    if substitute_tokens and not is_binary(source):
                        line = replace_vars(
                            line, env=token_env, defaults=token_defaults, strict=False
                        )
                    outfile.write((line).encode("utf-8"))
    # if file is binary, or has invalid characters, copy it as is
    except (UnicodeDecodeError, TypeError, ValueError):
        shutil.copy2(source, dest)
    except Exception as e:
        log.error("File copy error: %s %s", source, str(e))
    finally:
        # preserve original file mode if not a link
        if not os.path.islink(source):
            os.chmod(dest, os.stat(source).st_mode)


def copy_directory(
    source: str,
    dest: str,
    all_files: bool = False,
    substitute_tokens: bool = False,
    token_env=None,
    token_defaults=None,
) -> None:
    """Recursively copies a directory (ignores hidden files).

    Substitutes tokens in the file if substitute_tokens is True. Tokens are in
    the form of {TOKEN} and can be replaced with environment variables or default
    values.

    :param source: Path to source directory.
    :param dest: Path to destination directory.
    :param all_files: Copy all files, including hidden and ignorable files.
    :param substitute_tokens: If True, replaces tokens in the file
        with environment variables or defaults.
    :param token_env: Optional dictionary of environment variables to use for
        token substitution.
    :param token_defaults: Optional dictionary of default values for token
        substitution.
    :return: None
    """
    source = os.path.relpath(source)

    for filepath in get_files(source, all_files=all_files):
        relative = filepath[len(source) + 1 :] if source != "." else filepath
        target = os.path.join(dest, relative)
        copy_file(filepath, target, substitute_tokens, token_env, token_defaults)


def copy_object(
    source: str,
    dest: str,
    all_files: bool = False,
    substitute_tokens: bool = False,
    token_env=None,
    token_defaults=None,
) -> None:
    """Copies, or links, a file or directory recursively (ignores hidden
    files).

    Substitutes tokens in the file if substitute_tokens is True. Tokens are in
    the form of {TOKEN} and can be replaced with environment variables or default
    values.

    :param source: Path to source file, link or directory.
    :param dest: Path to destination file or directory.
    :param all_files: Copy all files in a directory, including hidden and
        ignorable files.
    :param substitute_tokens: If True, replaces tokens in the file
        with environment variables or defaults.
    :param token_env: Optional dictionary of environment variables to use for
        token substitution.
    :param token_defaults: Optional dictionary of default values for token
        substitution.
    :return: None
    """
    if os.path.islink(source):
        link_target = os.readlink(source)
        link_object(link_target, dest, link_target)
    elif os.path.isfile(source):
        copy_file(source, dest, substitute_tokens, token_env, token_defaults)
    elif os.path.isdir(source):
        copy_directory(
            source, dest, all_files, substitute_tokens, token_env, token_defaults
        )
    else:
        raise Exception("Source '%s' not found" % source)


def compare_files(source: str, target: str) -> bool:
    """Compares two files, ignoring end of lines in text files. Checks for
    file mode changes, file content changes and link changes.

    :param source: Path to source file.
    :param target: Path to target file.
    :return: True if files or links are the same.
    """
    try:
        # compare links
        if os.path.islink(source):
            if os.path.islink(target):
                return os.readlink(source) == os.readlink(target)
            else:
                return False
        # compare files
        else:
            # file mode must match
            if os.stat(source).st_mode != os.stat(target).st_mode:
                return False
            # file contents must match
            with open(source, "r") as file1, open(target, "r") as file2:
                while True:
                    line1 = next(file1, None)
                    line2 = next(file2, None)
                    # if either file is finished return true
                    if line1 is None or line2 is None:
                        return line1 is None and line2 is None
                    # compare lines regardless of EOL
                    if line1.rstrip("\r\n") != line2.rstrip("\r\n"):
                        return False
    # do binary comparison if there are invalid characters
    except UnicodeDecodeError:
        return filecmp.cmp(source, target, shallow=False)
    except IsADirectoryError as err:
        log.error("Cannot compare source: %s" % err)
        return False
    except FileNotFoundError:
        return False


def compare_objects(path1: str, path2: str) -> bool:
    """Compares two files or two directories.

    :param path1: Path to first file or directory.
    :param path2: Path to second file or directory.
    :return: True if objects are equal.
    """
    if os.path.isfile(path1) and os.path.isfile(path2):
        return compare_files(path1, path2)

    path1 = os.path.relpath(path1)
    all_files = get_files(path1)

    for filepath in all_files:
        destPath = os.path.join(path2, filepath[len(path1) + 1 :])
        if not compare_files(filepath, destPath):
            return False

    return True


# TODO: optimize find_matching_versions to avoid reading all versions
def find_matching_versions(
    source_path: str,
    dest: str,
    version_list: Optional[List[Tuple[str, int, str]]] = None,
) -> List[Tuple[str, int, str]]:
    """Finds all matching versions of a file in the destination directory,
    sorted from oldest to newest.

    [("/path/to/target.1.abc123", 1, "abc123"),]

    :param source_path: Path to source file.
    :param dest: Path to destination directory.
    :param version_list: List of tuples with version file, number and commit.
    :return: List of tuples with version file, number and commit.
    """
    version_list = version_list or get_file_versions(dest)
    return [v for v in version_list if compare_objects(source_path, v[0])]


def get_effective_options(global_options: dict, target_options: dict) -> dict:
    """Merge global and target-specific options, with target taking precedence.

    :param global_options: Global options dictionary.
    :param target_options: Target-specific options dictionary.
    :return: Merged dictionary with effective options.
    """
    effective = dict(global_options or {})
    effective.update(target_options or {})
    return effective


def get_user() -> str:
    """Returns the current user name.

    :return: username from environment variables.
    """
    return os.getenv("USER", os.getenv("USERNAME", "unknown"))


def has_hidden_attr(filepath: str) -> bool:
    """Checks if file has hidden file attribute (windows only).

    :param filepath: file system path.
    :return: True if file is hidden.
    """
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(filepath))
        return attrs != -1 and bool(attrs & 2)
    except Exception:
        return False


def is_file_hidden(filepath: str) -> bool:
    """Cross platform check if file is hidden. Checks if file name begins
    with period or has hidden attribute.

    :param filepath: file system path.
    :return: True if file is hidden.
    """
    name = os.path.basename(os.path.abspath(filepath))
    return name.startswith(".") or has_hidden_attr(filepath)


def is_ignorable(filepath: str) -> bool:
    """Returns True if path is ignorable. Checks path against patterns
    in the ignorables list, as well as dot files.

    :param path: a file system path.
    :return: True if filepath is ignorable.
    """
    return is_file_hidden(filepath) or bool(IGNORABLE_PATHS.search(filepath))


def get_root_dir(path: str) -> str:
    """Returns the root directory of a path."""
    return os.path.dirname(path).split(os.path.sep)[0]


def get_common_root_dirs(filepaths: List[str]) -> List[str]:
    """Returns a list of common root directories for a list of file paths.

    :param filepaths: list of file paths.
    :return: list of common parent directories.
    """
    return list({os.path.dirname(path) for path in filepaths if os.path.dirname(path)})


def get_path_type(path: str) -> str:
    """Returns the short name of the path type: 'file', 'directory', 'link',
    or 'null' if path does not exist.

    :param path: file system path.
    :return: name of path type as a string.
    """
    if os.path.islink(path):
        return "link"
    elif os.path.isdir(path):
        return "directory"
    elif os.path.isfile(path):
        return "file"
    return "null"


def normalize_path(path: str) -> str:
    """Normalizes relative paths by removing leading "./" and calling
    os.path.normpath.

    :param path: file system path.
    :return: normalized path.
    """
    path = sanitize_path(path)
    return os.path.normpath(path.lstrip("./")) if not os.path.isabs(path) else path


def sanitize_path(path: str) -> str:
    """Sanitizes a path by changing separators to forward slashes and removing
    trailing slashes.

    :param path: file system path.
    :returns: sanitized path.
    """
    return path.replace("\\", "/").rstrip("/") if path else path


def get_link_full_path(link: str) -> str:
    """Returns the full path of a symbolic link.

    :param link: symbolic link path.
    :return: full path of link target.
    """
    if not os.path.islink(link):
        return ""
    target = os.readlink(link)
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(link), target)
    return os.path.normpath(target)


def get_dist_info(dest: str, ext: str = config.DIST_INFO_EXT) -> str:
    """Returns the dist info file path, e.g.

        /path/to/desploy/prod/.foobar.py.dist

    The dist info files are hidden dot files used to store distribution
    information and tell distman if a file has been previously disted.

    :param dest: destination directory.
    :param ext: file extension.
    :return: file path for dist info.
    """
    folder, original_name = os.path.split(dest)
    return os.path.join(folder, f".{original_name}{ext}")


def write_dist_info(dest: str, dist_info: dict) -> None:
    """Writes distribution information to a file.

    :param dest: Path to destination directory.
    :param dist_info: Dictionary of distribution information.
    :return: None
    """
    distinfo = get_dist_info(dest=dest)
    log.debug("Writing dist info to %s" % distinfo)
    with open(distinfo, "w") as outFile:
        for key, value in dist_info.items():
            outFile.write(f"{key}: {value}\n")


def create_dest_folder(dest: str, dryrun: bool = False, yes: bool = False) -> bool:
    """Creates destination folder if it does not exist. Prompts user to
    confirm if the folder does not exist yet.

    :param dest: destination file path.
    :param dryrun: dry run flag.
    :param yes: yes flag (skips user confirmation).
    :return: True if destination folder was created.
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


def expand_wildcard_entry(
    source_pattern: str, destination_template: str
) -> List[Tuple[str, str]]:
    """Expands a wildcard entry in the form of a glob pattern to a list of
    tuples of source and destination paths. Supports only `*`, not `**` or ?.

        build/* -> {DEPLOY_ROOT}/lib/python/%1

    :param source_pattern: glob pattern for source files.
    :param destination_template: template for destination paths.
    :return: list of tuples of source and destination paths.
    """

    # convert source glob to regex for extracting capture groups
    regex_pattern = re.escape(source_pattern)
    regex_pattern = regex_pattern.replace(r"\*", r"([^/]+)")
    regex_pattern = "^" + regex_pattern + "$"

    # expand the glob
    matched_paths = glob.glob(source_pattern)
    results = []

    for path in matched_paths:
        m = re.match(regex_pattern, path)
        if not m:
            continue

        # replace %1, %2, etc., with matched groups
        dest = destination_template
        found = False
        for i, group in enumerate(m.groups(), start=1):
            if f"%{i}" not in dest:
                log.warning(
                    f"Destination template '{destination_template}' "
                    f"does not contain a placeholder for group {i}."
                )
                continue
            else:
                found = True
                dest = dest.replace(f"%{i}", group)

        # if no groups were found, skip this entry
        if found:
            results.append((path, dest))

    return sorted(results)


def get_file_versions(target: str) -> List[Tuple[str, int, str]]:
    """Find the highest numeric version number for a file.

    :param target: Path to file to check.
    :return: List of tuples with version number and file.
    """
    filedir = os.path.join(os.path.dirname(target), config.DIR_VERSIONS)
    if not os.path.exists(filedir):
        return []

    filename = os.path.basename(target)
    version_list = []

    for f in os.listdir(filedir):
        file_name_length = len(filename)
        # get files that match <target>.<version>.<commit>
        if (
            f.startswith(filename)
            and len(f) > file_name_length + 1
            and f[file_name_length] == "."
            and str(f[file_name_length + 1]).isnumeric()
        ):
            # parse the number from the rest of the file name
            info = f[file_name_length + 1 :]
            dot_pos = info.find(".")
            if -1 != dot_pos:
                ver = int(info[:dot_pos])
            else:
                ver = int(info)
            commit = ""
            if -1 != dot_pos:
                # trim potential remaining dotted portions
                dot_pos2 = info.find(".", dot_pos + 1)
                if -1 == dot_pos2:
                    commit = info[dot_pos + 1 :]
                else:
                    commit = info[dot_pos + 1 : dot_pos2]
                # trim '-forced' if present
                dash_pos = commit.find("-")
                if -1 != dash_pos:
                    commit = commit[:dash_pos]
            version_list.append((filedir + "/" + f, ver, commit))

    return sorted(version_list, key=lambda tup: tup[1])


def hashes_equal(hash_str_a: str, hash_str_b: str) -> bool:
    """Compares two hash strings regardless of length or case

    :param hash_str_a: First hash string.
    :param hash_str_b: Second hash string.
    :return: True if hashes are equal.
    """
    if len(hash_str_a) > len(hash_str_b):
        return hash_str_a.upper().startswith(hash_str_b.upper())
    else:
        return hash_str_b.upper().startswith(hash_str_a.upper())


def link_object(target: str, link: str, actual_target: str) -> bool:
    """Creates symbolic link to a file or directory.

    :param target: Path to target file or directory.
    :param link: Path to symbolic link.
    :param actual_target: Path to actual target file or directory.
    :return: True if linking was successful.
    """
    if not os.path.exists(actual_target):
        log.warning("Target '%s' not found", actual_target)
        return False

    try:
        os.symlink(target, link, target_is_directory=os.path.isdir(actual_target))
        return True
    except OSError as e:
        log.error("Failed to create symbolic link '%s => %s': %s", link, target, e)
        return False


def remove_object(path: str, recurse: bool = False) -> None:
    """Deletes a file or directory tree.

    :param path: file system path.
    :param recurse: recursively delete directory tree.
    :return: None
    """
    try:
        if os.path.islink(path) or os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            if recurse:
                shutil.rmtree(path)
            else:
                os.rmdir(path)
    except Exception as e:
        log.error("Error removing '%s': %s", path, e)


def replace_vars(
    s: str,
    env=None,
    defaults=None,
    open_token=config.PATH_TOKEN_OPEN,
    close_token=config.PATH_TOKEN_CLOSE,
    strict=True,
) -> str:
    """Replaces {VARS} in the input string with values from the environment or defaults.

    :param s: The input string.
    :param env: Optional override dict for environment variables.
    :param defaults: Optional dict for fallback values.
    :param open_token: Start delimiter for token, default is '{'.
    :param close_token: End delimiter for token, default is '}'.
    :param strict: If True, raises an error if a token cannot be resolved.
    :return: The string with substitutions applied.
    """
    if env is None:
        env = os.environ
    if defaults is None:
        defaults = config.DEFAULT_ENV

    i = 0
    result = []
    while i < len(s):
        start = s.find(open_token, i)
        if start == -1:
            result.append(s[i:])
            break

        end = s.find(close_token, start + len(open_token))
        if end == -1:
            raise ValueError(
                f"Unclosed token starting at position {start}: {s[start:]}"
            )

        result.append(s[i:start])
        var_name = s[start + len(open_token) : end]
        value = env.get(var_name, defaults.get(var_name))
        if value is None:
            if strict:
                raise ValueError(f"Cannot resolve token: {var_name}")
            value = f"{open_token}{var_name}{close_token}"
        result.append(value)
        i = end + len(close_token)

    return "".join(result)


def yesNo(question: str) -> bool:
    """Displays question text to user and reads yes/no input.

    :param question: question text.
    :return: True if user answers yes.
    """
    while True:
        answer = input(f"{question} (y/n): ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        elif answer in {"n", "no"}:
            return False
        else:
            print("Please answer 'y' or 'n'.")


def get_files(start: str, all_files: bool = False) -> List[str]:
    """Returns a list of all files found in a given starting directory.

    :param start: Starting directory.
    :param all_files: Get all files in a directory, including hidden and
        ignorable files.
    :return: List of relative file paths.
    """
    return [f for f in walk(start, exclude_ignorables=(all_files == False))]


def walk(
    path: str, exclude_ignorables: bool = True, followlinks: bool = False
) -> Generator[str, None, None]:
    """Generator that yields relative file paths that are not ignorable.
    Will include nested directories and symbolic links to directories:

        target/
            |- subdir
            |   `- file.txt
            |- file-link -> /link/to/file.txt
            |- folder-link -> /link/to/folder/
            `- file.txt

    :param path: file system path.
    :param exclude_ignorables: exclude ignorable files.
    :param followlinks: follow symbolic links.
    :return: generator of file paths.
    """
    if not is_ignorable(path) and os.path.isfile(path):
        yield path
    for dirname, dirs, files in os.walk(path, topdown=True, followlinks=followlinks):
        if exclude_ignorables and is_ignorable(dirname):
            continue
        for d in dirs:
            if exclude_ignorables and is_ignorable(d):
                dirs.remove(d)
            # include symlinks to directories
            elif os.path.islink(os.path.join(dirname, d)):
                yield os.path.join(dirname, d)
        for name in files:
            if not is_ignorable(name):
                yield os.path.join(dirname, name)
