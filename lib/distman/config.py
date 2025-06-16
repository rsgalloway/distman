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
Contains default config and settings.
"""

import os
import platform

# default environment settings
ENV = os.getenv("ENV", "prod")
HOME = os.getenv("HOME", os.path.expanduser("~"))
PLATFORM = platform.system().lower()
ROOT = {
    "darwin": f"{HOME}/Library/Application Support/pipe",
    "linux": f"{HOME}/.local/pipe",
    "windows": "C:\\ProgramData\\pipe",
}.get(PLATFORM, f"./pipe/{ENV}")
DEPLOY_ROOT = os.getenv("DEPLOY_ROOT", f"{ROOT}/{ENV}")
DEFAULT_ENV = {
    "DEPLOY_ROOT": DEPLOY_ROOT,
    "ENV": ENV,
    "HOME": HOME,
    "PLATFORM": PLATFORM,
    "ROOT": ROOT,
}

# dist file settings
DIST_FILE = "dist.json"
DIST_FILE_VERSION = 1
DIST_INFO_EXT = ".dist"
DIR_VERSIONS = "versions"

# logging settings
LOG_NAME = "distman"
LOG_DIR = os.getenv("LOG_DIR", os.path.expanduser("~/log/distman"))
LOG_LEVEL_DEFAULT = "INFO"
LOG_LEVEL = os.getenv("LOG_LEVEL", LOG_LEVEL_DEFAULT)
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5
DRYRUN_MESSAGE = "NOTICE: Dry run (no changes will be made)"

# ignorable files and directories
IGNORABLE = [
    "*~",
    ".git*",
    ".env",
    ".venv",
    "*.bup",
    "*.bak",
    "*.orig",
    "*.lock",
    # "*.dist-info",  # required by importlib-metadata
    # "*.egg-info",  # required by setuptools
    "*.pyc",
    "*.pyd",
    "*.pyo",
    "*.swp",
    "*.temp*",
    "*.tmp",
    "venv*",
    "MANIFEST*",
    "__pycache__",
    "Thumbs.db",
    ".DS_Store",
    ".idea",
    ".vscode",
]

# git repo settings
LEN_HASH = 7
LEN_MINHASH = 4
MAIN_BRANCHES = ["master", "main"]

# embedded path tokens
PATH_TOKEN_OPEN = "{"
PATH_TOKEN_CLOSE = "}"

# dist file keys/tags
TAG_AUTHOR = "author"
TAG_DESTPATH = "destination"
TAG_SOURCEPATH = "source"
TAG_VERSION = "version"
TAG_TARGETS = "targets"
