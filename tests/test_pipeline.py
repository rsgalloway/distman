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
Contains tests for the pipeline module.
"""

import os
import pytest

from distman.dist import Target
from distman.pipeline import (
    run_pipeline,
    validate_pipeline_spec,
    get_pipeline_for_target,
    ValidationError,
)


@pytest.fixture
def sample_file(tmp_path):
    file = tmp_path / "input.txt"
    file.write_text("hello world")
    return str(file)


def test_run_pipeline_executes_python_func(tmp_path, sample_file, monkeypatch):
    """Test that a Python function can be executed as a pipeline step."""
    target = Target("test", "input.txt", "test/input.txt", "f")
    pipeline = {"uppercase": {"func": "distman.transform.dummy_transform"}}

    output = run_pipeline(target, pipeline, sample_file, str(tmp_path / "build"))
    assert os.path.exists(output)
    with open(os.path.join(output)) as f:
        assert f.read() == "HELLO WORLD"


def test_validate_pipeline_spec_raises_on_invalid():
    """Test that an invalid pipeline specification raises ValidationError."""
    with pytest.raises(ValidationError):
        validate_pipeline_spec({"bad": {"foo": "bar"}}, "test")


def test_get_pipeline_for_target_merges():
    """Test that the global pipeline and target-specific pipeline are merged correctly."""
    global_pipeline = {"step1": {"script": "echo 1"}}
    target_pipeline = {"step2": {"script": "echo 2"}}
    merged = get_pipeline_for_target(global_pipeline, target_pipeline)
    assert "step1" in merged and "step2" in merged


def test_black_check_fails(tmp_path):
    """Test that the black_check step fails on unformatted code."""
    from distman.pipeline import run_pipeline, TransformError

    f = tmp_path / "unformatted.py"
    f.write_text("x=1+2")

    target = Target("test", "input.txt", "test/input.txt", "f")
    pipeline = {"black_check": {"script": ["black --check {input}"]}}

    with pytest.raises(TransformError):
        run_pipeline(target, pipeline, str(f), tmp_path / "build")
