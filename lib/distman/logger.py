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
Contains logging functions and classes.
"""

import os
import logging
from logging.handlers import RotatingFileHandler

from distman import config

log = logging.Logger(config.LOG_NAME)

# fix for ValueErrors raised by python's logging module
LOG_LEVEL_MAP = {
    0: "NOTSET",
    10: "DEBUG",
    20: "INFO",
    30: "WARNING",
    40: "ERROR",
    50: "CRITICAL",
}
VALID_LOG_LEVELS = LOG_LEVEL_MAP.values()

LOG_LEVEL = config.LOG_LEVEL
if isinstance(LOG_LEVEL, int):
    LOG_LEVEL = LOG_LEVEL_MAP.get(LOG_LEVEL, config.LOG_LEVEL_DEFAULT)
elif isinstance(LOG_LEVEL, str) and LOG_LEVEL.isdigit():
    LOG_LEVEL = LOG_LEVEL_MAP.get(int(LOG_LEVEL), config.LOG_LEVEL_DEFAULT)
elif LOG_LEVEL not in VALID_LOG_LEVELS:
    LOG_LEVEL = config.LOG_LEVEL_DEFAULT

log.setLevel(LOG_LEVEL)
log.addHandler(logging.NullHandler())


class DryRunFilter(logging.Filter):
    """Filter that removes log records when in dry run mode."""

    def __init__(self, dryrun):
        super().__init__()
        self.dryrun = dryrun

    def filter(self, record):
        return not self.dryrun


class UserFilter(logging.Filter):
    """Adds the username to the log record."""

    def filter(self, record):
        try:
            record.username = os.getlogin()
        except Exception:
            import getpass

            record.username = getpass.getuser()
        return True


class UserRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that adds the username to the log record."""

    def __init__(
        self, filename, mode="a", maxBytes=0, backupCount=0, encoding=None, delay=False
    ):
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay)
        self.addFilter(UserFilter())


def setup_stream_handler(level=LOG_LEVEL):
    """Adds a new stdout stream handler."""
    for h in log.handlers:
        if h.name == log.name and "StreamHandler" in str(h):
            del log.handlers[log.handlers.index(h)]

    handler = logging.StreamHandler()
    handler.set_name(log.name)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))

    log.addHandler(handler)
    return handler


def setup_file_handler(
    maxBytes=config.LOG_MAX_BYTES,
    backupCount=config.LOG_BACKUP_COUNT,
    level=LOG_LEVEL,
    logdir=config.LOG_DIR,
    dryrun=False,
):
    """Adds a new rotating file handler."""
    for h in log.handlers:
        if h.name == log.name and "RotatingFileHandler" in str(h):
            del log.handlers[log.handlers.index(h)]

    os.makedirs(logdir, exist_ok=True)
    log_file = os.path.join(logdir, "distman.log")

    handler = UserRotatingFileHandler(
        log_file, maxBytes=maxBytes, backupCount=backupCount
    )
    handler.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s - %(username)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    # add dry run filter
    handler.addFilter(DryRunFilter(dryrun))

    log.addHandler(handler)
    return handler


def setup_logging(dryrun=False):
    """Setup log handlers.

    :param dryrun: dry run flag
    """
    setup_stream_handler()

    if not dryrun:
        try:
            setup_file_handler()
        except Exception as err:
            print("Error: %s" % str(err))
