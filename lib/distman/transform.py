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
Contains basic transform classes and functions.
"""

import os
import shutil
import py_compile

from distman import util
from distman.logger import log


class TransformError(Exception):
    """Raised when a transform step fails during execution."""

    pass


def replace_tokens(input: str, output: str, tokens: dict) -> str:
    """Replace tokens in a file or directory.

    :param input: Path to the input file or directory.
    :param output: Path to the output file or directory.
    :param tokens: Dictionary of tokens to replace in the format {'token': 'replacement'}.
    :raises TransformError: If the input file does not exist or is not a file.
    :return: The path to the output file or directory.
    """
    if os.path.isdir(input):
        shutil.copytree(input, output, dirs_exist_ok=True)
        _replace_tokens_in_dir(output, tokens)
    else:
        _replace_tokens_in_file(input, output, tokens)
    return output


def _replace_tokens_in_dir(directory: str, tokens: dict) -> str:
    """Replace tokens in all files within a directory.

    :param directory: Path to the directory containing files to process.
    :param tokens: Dictionary of tokens to replace in the format {'token': 'replacement'}.
    :raises TransformError: If a file is not found or is not a regular file.
    :return: The path to the directory after processing.
    """
    for filepath in util.walk(directory):
        if util.is_binary(filepath):
            continue
        _replace_tokens_in_file(filepath, filepath, tokens)
    return directory


def _replace_tokens_in_file(src: str, dst: str, tokens: dict) -> str:
    """Replace tokens in a single file.

    :param src: Path to the source file.
    :param dst: Path to the destination file.
    :param tokens: Dictionary of tokens to replace in the format {'token': 'replacement'}.
    :raises TransformError: If the source file does not exist, is not a file, or is empty.
    :return: The path to the destination file after processing.
    """
    if not os.path.exists(src):
        raise TransformError(f"Source file does not exist: {src}")
    if not os.path.isfile(src):
        raise TransformError(f"Source is not a file: {src}")
    if os.path.getsize(src) == 0:
        log.warning("Source file is empty: %s", src)
        return

    with open(src, "r", encoding="utf-8") as f:
        content = f.read()
    for key, val in tokens.items():
        content = content.replace(key, val)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)

    return dst


def chmod(input: str, output: str, mode: str) -> str:
    """Change the file mode of a file or directory.

    :param input: Path to the input file or directory.
    :param output: Path to the output file or directory.
    :param mode: File mode as a string (e.g., '755').
    :raises TransformError: If the input file does not exist or is not a file.
    :return: The path to the output file or directory with the new mode.
    """
    if input != output:
        shutil.copy2(input, output)
    else:
        output = input
    os.chmod(output, int(mode, 8))
    return output


def byte_compile(input: str, output: str) -> str:
    """Byte-compile a Python file or directory.

    :param input: Path to the input file or directory.
    :param output: Path to the output file or directory.
    :raises TransformError: If the input is not a .py file or directory.
    :return: The path to the output file or directory after byte-compilation.
    """
    if os.path.isdir(input):
        shutil.copytree(input, output, dirs_exist_ok=True)
        _byte_compile_dir(output)
    elif input.endswith(".py"):
        os.makedirs(os.path.dirname(output), exist_ok=True)
        py_compile.compile(input, cfile=output + "c")
    else:
        raise TransformError("Can only byte-compile .py files or directories")
    return output


def _byte_compile_dir(directory: str) -> str:
    """Byte-compile all Python files in a directory.

    :param directory: Path to the directory containing Python files.
    :raises TransformError: If a file is not a .py file.
    :return: The path to the directory after byte-compilation.
    """
    for filepath in util.walk(directory):
        if filepath.endswith(".py"):
            py_compile.compile(filepath, cfile=filepath + "c")
            os.remove(filepath)
    return directory
