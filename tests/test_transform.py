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
Contains tests for the transform module.
"""

import os
import pytest

from distman.transform import replace_tokens, byte_compile, chmod, minify
from distman.transform import TransformError


def test_replace_tokens(tmp_path):
    """Test the replace_tokens function to ensure it correctly replaces tokens in a file."""
    src = tmp_path / "source.py"
    dst = tmp_path / "out.py"
    src.write_text("print('__VERSION__')")

    replace_tokens(str(src), str(dst), {"__VERSION__": "1.0.0"})
    assert dst.read_text() == "print('1.0.0')"


def test_chmod(tmp_path):
    """Test the chmod function to ensure it sets the executable bit on a file."""
    f = tmp_path / "script.sh"
    f.write_text("#!/bin/bash\necho hi")

    chmod(str(f), str(f), mode="755")
    assert os.access(f, os.X_OK)


def test_byte_compile_file(tmp_path):
    """Test the byte_compile function to ensure it compiles a Python file to bytecode."""
    f = tmp_path / "module.py"
    f.write_text("x = 42")

    out_dir = tmp_path / "compiled"
    out_dir.mkdir()

    out_file = out_dir / "module.pyc"
    byte_compile(str(f), str(out_file))

    assert out_file.exists()


def test_minify_css(tmp_path):
    """Test the minify function to ensure it correctly minifies a CSS file."""
    css_file = tmp_path / "style.css"
    css_file.write_text("body { margin: 0; } /* Comment */")
    minified_css_file = tmp_path / "minified_style.css"

    minify(str(css_file), str(minified_css_file))
    assert minified_css_file.read_text() == "body{margin:0;}"


def test_minify_js(tmp_path):
    """Test the minify function to ensure it correctly minifies a JS file."""
    js_file = tmp_path / "script.js"
    js_file.write_text("function test() { console.log('Hello'); }")
    minified_js_file = tmp_path / "minified_script.js"

    minify(str(js_file), str(minified_js_file))
    assert minified_js_file.read_text() == "function test(){console.log('Hello');}"


def test_minify_html(tmp_path):
    """Test the minify function to ensure it correctly minifies an HTML file."""
    html_file = tmp_path / "index.html"
    html_file.write_text(
        "<html>\n<head>\n<title> Test </title> </head>\n<body>\n</body>\n</html>"
    )
    minified_html_file = tmp_path / "minified_index.html"

    minify(str(html_file), str(minified_html_file))
    assert (
        minified_html_file.read_text()
        == "<html><head><title>Test</title></head><body></body></html>"
    )


def test_minify_binary_file(tmp_path):
    """Test the minify function to ensure it correctly copies a binary file as-is."""
    binary_file = tmp_path / "image.png"
    binary_data = bytes([137, 80, 78, 71, 13, 10, 26, 10])  # sample PNG header
    binary_file.write_bytes(binary_data)
    minified_binary_file = tmp_path / "minified" / "image.png"

    minify(str(binary_file), str(minified_binary_file))
    assert minified_binary_file.read_bytes() == binary_data


def test_minify_binary_file_strict(tmp_path):
    """Test the minify function to ensure it raises a TransformError when strict=True
    for a binary file."""
    binary_file = tmp_path / "image.png"
    binary_data = bytes([137, 80, 78, 71, 13, 10, 26, 10])  # sample PNG header
    binary_file.write_bytes(binary_data)
    minified_binary_file = tmp_path / "minified" / "image.png"

    with pytest.raises(TransformError):
        minify(str(binary_file), str(minified_binary_file), strict=True)