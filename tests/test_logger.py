#!/usr/bin/env python3
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

"""Tests for suite-wide console and rotating-file logging."""

import logging
from pathlib import Path

import pytest

from distman.logger import log, setup_logging


def test_command_messages_reach_console_and_file(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Record normal command messages while keeping later dry runs out of the file.

    :param tmp_path: Isolated log directory parent.
    :param capsys: Captured console output.
    :return: None.
    """
    logdir = tmp_path / "logs"
    try:
        setup_logging(logdir=str(logdir))
        log.info("Installed: 2 target(s)")
        for handler in log.handlers:
            handler.flush()
        assert "Installed: 2 target(s)" in capsys.readouterr().out
        logfile = logdir / "distman.log"
        assert "Installed: 2 target(s)" in logfile.read_text()

        setup_logging(dryrun=True, logdir=str(logdir))
        log.info("Dry run: deployment unchanged")
        for handler in log.handlers:
            handler.flush()
        assert "Dry run: deployment unchanged" in capsys.readouterr().out
        assert "Dry run: deployment unchanged" not in logfile.read_text()
    finally:
        for handler in list(log.handlers):
            if not isinstance(handler, logging.NullHandler):
                log.removeHandler(handler)
                handler.close()
