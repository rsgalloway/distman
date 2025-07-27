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
import re
import shutil
import py_compile
from typing import Optional

from distman import util
from distman.logger import log


class TransformError(Exception):
    """Raised when a transform step fails during execution."""

    pass


def dummy_transform(input, output, **kwargs):
    """A dummy transform function that copies the input file to the output file.

    :param input: Path to the input file.
    :param output: Path to the output file.
    :param kwargs: Additional keyword arguments (not used).
    :raises TransformError: If the input file does not exist or is not a file.
    :return: The path to the output file.
    """
    with open(input, "r") as f_in, open(output, "w") as f_out:
        f_out.write(f_in.read().upper())


def replace_tokens(input: str, output: str, tokens: dict) -> str:
    """Replace tokens in a file or directory.

    :param input: Path to the input file or directory.
    :param output: Path to the output file or directory.
    :param tokens: Dictionary of tokens to replace in the format {'token': 'replacement'}.
    :raises TransformError: If the input file does not exist or is not a file.
    :return: The path to the output file or directory.
    """
    if os.path.isdir(input):
        util.safe_copytree(input, output)
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
        content = content.replace(key, util.replace_vars(val))
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
        util.safe_copytree(input, output)
        return _byte_compile_dir(output)
    elif os.path.isfile(input):
        os.makedirs(os.path.dirname(output), exist_ok=True)
        return _byte_compile_file(input, output)
    else:
        raise TransformError(
            f"Cannot byte-compile: '{input}' is not a file or directory"
        )


def _byte_compile_file(
    input: str, output: str = None, display_path: Optional[str] = None
) -> str:
    """Byte-compile a single Python file.

    :param input: Path to the input Python file.
    :param output: Path to the output compiled file.
    :param display_path: Optional path for display purposes (e.g., for error messages).
    :return: The path to the output compiled file.
    """
    if not output:
        output = input + "c"

    # if no display path is given, use the input file name
    if not display_path:
        display_path = os.path.basename(input)

    py_compile.compile(input, cfile=output, dfile=display_path)

    # preserve the original file's mode for the compiled file
    mode = os.stat(input).st_mode
    os.chmod(output, mode)

    return output


def _byte_compile_dir(directory: str) -> str:
    """Byte-compile all Python files in a directory.

    :param directory: Path to the directory containing Python files.
    :raises TransformError: If a file is not a .py file.
    :return: The path to the directory after byte-compilation.
    """
    for filepath in util.walk(directory):
        if util.is_binary(filepath):
            continue
        display_path = os.path.relpath(filepath, directory)
        _byte_compile_file(filepath, filepath + "c", display_path)
        os.remove(filepath)
    return directory


def minify(input: str, output: str, strict: bool = False) -> str:
    """Minify a given src file and output to a dst file.

    :param input: Path to the input file or directory.
    :param output: Path to the output file or directory.
    :param strict: If True, raises an error for unsupported file types.
    :raises TransformError: If the input file does not exist or is not a file.
    :return: The path to the output file or directory after minification.
    """

    if os.path.isdir(input):
        util.safe_copytree(input, output)
        _minify_dir(output, strict=strict)
    else:
        os.makedirs(os.path.dirname(output), exist_ok=True)
        _minify_file(input, output, strict=strict)

    return output


def _minify_css(input: str) -> str:
    """Returns minified css source.

    :param input: Path to the input CSS file.
    :return: Minified CSS source as a string.
    """

    minified = ""

    with open(input, "r") as infile:
        minified = infile.read()
        minified = re.sub(r"/\*.*?\*/", "", minified, flags=re.DOTALL)
        minified = re.sub(r"\s+", " ", minified)
        minified = re.sub(r"\s*([{}:;,])\s*", r"\1", minified)
        minified = re.sub(r"}\s*", "}", minified)
        minified = re.sub(r"{\s*", "{", minified)

    return minified


def _minify_js(input: str) -> str:
    """Returns minified js source.

    :param input: Path to the input JavaScript file.
    :return: Minified JavaScript source as a string.
    """

    minified = ""

    from jsmin import jsmin

    with open(input) as js_file:
        minified = jsmin(js_file.read(), quote_chars="'\"`")

    return minified


def _minify_html(input: str) -> str:
    """Returns minified html source.

    :param input: Path to the input HTML file.
    :return: Minified HTML source as a string.
    """

    import htmlmin

    with open(input, "r") as infile:
        html_content = infile.read()

        minified_html = htmlmin.minify(
            html_content, remove_comments=True, remove_empty_space=True
        )

        return minified_html


def _minify_text(input: str) -> str:
    """Returns minified text source. Removes extra whitespace and newlines.

    :param input: Path to the input text file.
    :return: Minified text source as a string.
    """

    with open(input, "r") as infile:
        text_content = infile.read()
        minified_text = re.sub(r"\s+", " ", text_content.strip())
        return minified_text


def _minify_dir(directory: str, strict: bool = False) -> str:
    """Minify all files in a directory.

    :param directory: Path to the directory containing files to minify.
    :param strict: If True, raises an error for unsupported file types.
    :raises TransformError: If a file is not found or is not a regular file.
    :return: The path to the directory after minification.
    """
    for filepath in util.walk(directory):
        if util.is_binary(filepath):
            continue
        _minify_file(filepath, filepath, strict=strict)
    return directory


def _minify_file(input: str, output: str, strict: bool = False) -> str:
    """Returns minified file contents.

    :param input: Path to the input file.
    :param output: Path to the output file.
    :param strict: If True, raises an error for unsupported file types.
    :raises TransformError: If the input file does not exist or is not a file.
    :return: The path to the output file after minification.
    """

    minified = ""

    if not os.path.exists(input):
        raise TransformError(f"File not found: {input}")

    _, ext = os.path.splitext(str(input).lower())

    try:
        if ext in (".js",):
            minified = _minify_js(input)
        elif ext in (".css", ".css3"):
            minified = _minify_css(input)
        elif ext in (".html", ".htm"):
            minified = _minify_html(input)
        elif ext in (".txt"):
            minified = _minify_text(input)
        else:
            if strict:
                raise TransformError(f"Unsupported file type for minification: {ext}")
            else:
                log.warning("Unsupported file type for minification: %s", input)
                if input != output:
                    os.makedirs(os.path.dirname(output), exist_ok=True)
                    shutil.copy2(input, output)
                    return output
                else:
                    return input

    except Exception as e:
        raise TransformError(e)

    with open(output, "w") as fp:
        fp.write(minified)

    return output
