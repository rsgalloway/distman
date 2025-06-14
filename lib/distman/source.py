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
Contains source file distribution classes and functions.
"""

import json
import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from functools import wraps

import git
from git.exc import GitCommandError

from distman import config, util
from distman.logger import log


def requires_git(func: Callable) -> Callable:
    """Decorator to ensure git repo info is loaded before calling the method."""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.repo is None:
            self.read_git_info()
        return func(self, *args, **kwargs)

    return wrapper


class Source(object):
    """Base class for handling dist file metadata."""

    def __init__(self):
        """Initializes the Source object with default values."""
        self.author = os.getenv("USERNAME", os.getenv("USER", "unknown"))
        self.changed_files: List[str] = []
        self.directory = "."
        self.name = ""
        self.path = ""
        self.root = None

    def get_targets(self) -> Optional[dict]:
        """Returns the targets defined in the dist file."""
        return self.root.get(config.TAG_TARGETS) if self.root else None

    def read_dist_file(self, directory: str = ".") -> bool:
        """Reads the dist file from the specified directory.

        :param directory: Directory containing the dist file.
        :return: True if the dist file was successfully read, False otherwise.
        """
        if not os.path.isdir(directory):
            log.info("%s is not a directory", directory)
            return False

        dist_file = os.path.join(directory, config.DIST_FILE)
        if not os.path.exists(dist_file):
            log.info("%s does not exist", dist_file)
            return False

        self.directory = directory

        try:
            with open(dist_file, "r") as json_file:
                self.root = json.load(json_file)
        except Exception as e:
            log.error("Failed to parse dist file: %s", str(e))
            return False

        self.author = self.root.get(config.TAG_AUTHOR, util.get_user())

        version = int(self.root.get(config.TAG_VERSION, 0))
        if version < config.DIST_FILE_VERSION:
            log.warning(
                "WARNING: Old dist file version: %s (current %d)",
                version,
                config.DIST_FILE_VERSION,
            )
        elif version > config.DIST_FILE_VERSION:
            log.error(
                "ERROR: This dist file is newer than supported version: %s (current %d)",
                version,
                config.DIST_FILE_VERSION,
            )
            self.root = None
            return False

        log.info("Author: %s", self.author)
        return True


class GitRepo(Source):
    """Extends Source to support Git-based repositories."""

    def __init__(self):
        """Initializes the GitRepo object."""
        super(GitRepo, self).__init__()
        self.branch_name = ""
        self.head = ""
        self.repo = None
        self.short_head = ""

    @requires_git
    def get_repo_files(self, start: str = ".") -> List[str]:
        """Returns a list of files tracked by the git repository starting from
        the specified directory.

        :param start: Directory to start searching for tracked files.
        :return: List of tracked file paths relative to the repository root.
        """
        repo_root = Path(self.repo.working_tree_dir).resolve()
        start_path = (repo_root / start).resolve()
        if not start_path.is_dir():
            raise ValueError(
                f"Start directory '{start}' does not exist or is not a directory."
            )

        tracked_files = []
        for item in self.repo.tree().traverse():
            item_path = repo_root / item.path
            if item_path.is_file() and start_path in item_path.parents:
                tracked_files.append(str(item_path.relative_to(repo_root)))

        return tracked_files

    @requires_git
    def get_untracked_files(
        self, start: str = ".", include_ignored: bool = True
    ) -> Tuple[List[str], List[str]]:
        """Returns a list of untracked files and directories in the git repository.

        :param start: Directory to start searching for untracked files.
        :param include_ignored: If True, includes ignored files in the result.
        :return: A tuple containing a list of untracked file paths and a list of
            untracked directory paths.
        """
        all_files = [util.normalize_path(f) for f in util.walk(start)]

        if include_ignored:
            repo_files = [util.normalize_path(f) for f in self.get_repo_files(start)]
            untracked_files = [f for f in all_files if f not in repo_files]
        else:
            untracked_files = [f for f in self.repo.untracked_files]

        untracked_dirs = util.get_common_root_dirs(untracked_files)
        return untracked_files, untracked_dirs

    def get_path(self) -> str:
        """Returns the URL of the git repository or the current path if not a
        git repo."""
        if self.repo and self.repo.remotes:
            if "origin" in [r.name for r in self.repo.remotes]:
                return self.repo.remotes.origin.url
            return self.repo.remotes[0].url
        return self.path

    def read_git_info(self) -> bool:
        """Reads the git repository information and initializes the object."""
        self.branch_name = ""
        self.head = ""
        self.short_head = ""
        self.path = os.getcwd().replace("\\", "/")
        self.name = os.path.basename(self.path)
        self.changed_files = []

        try:
            self.repo = git.Repo(self.directory)
            self.head = self.repo.head.commit.hexsha
            self.short_head = self.head[: config.LEN_HASH]
            self.path = self.get_path()

            if "@" in self.path:
                self.path = self.path.split("@", 1)[-1]
            self.name = os.path.basename(self.path).replace(".git", "")

            try:
                branch = self.repo.active_branch
                self.branch_name = branch.name
                if self.branch_name not in config.MAIN_BRANCHES:
                    log.warning("Warning: Not on a main branch")
            except (TypeError, AttributeError):
                log.warning("Warning: Detached HEAD or no active branch")

        except git.InvalidGitRepositoryError:
            log.warning("Warning: Not in a git repository")
            self.repo = None
        except Exception as e:
            log.warning("Error reading git repo: %s", str(e))

        log.info("Name: %s", self.name)
        log.info("Path: %s", self.path)
        if self.repo and self.branch_name:
            log.info("Branch: %s", self.branch_name)
            log.info("Head: %s (%s)", self.head, self.short_head)

        return True

    @requires_git
    def is_git_behind(self) -> bool:
        """Checks if the current branch is behind the remote branch.

        :return: True if the branch is behind, False otherwise.
        """
        if not self.branch_name or not self.repo.remotes:
            return False

        try:
            upstream_commits = list(
                self.repo.iter_commits(f"origin/{self.branch_name}..{self.branch_name}")
            )
            if upstream_commits:
                log.error(
                    "Directory is %d commits behind remote. Run 'git pull' or use --force.",
                    len(upstream_commits),
                )
                return True

        except GitCommandError as err:
            log.error("Git error checking remote branch: %s", str(err))
            return True
        except Exception as err:
            log.error("Unexpected error checking remote branch: %s", str(err))
            return True

        return False

    @requires_git
    def git_changed_files(self, include_untracked: bool = True) -> List[str]:
        """Returns a list of changed files in the git repository.

        :param include_untracked: If True, includes untracked files in the result.
        :return: List of changed file paths relative to the repository root.
        """
        if not self.repo:
            return []

        try:
            changed = [
                os.path.join(self.directory, item.a_path)
                for item in self.repo.index.diff(None)
            ]
            staged = [
                os.path.join(self.directory, item.a_path)
                for item in self.repo.index.diff("HEAD")
            ]
            untracked = []
            if include_untracked:
                untracked = [
                    os.path.join(self.directory, item)
                    for item in self.repo.untracked_files
                ]

            return [util.normalize_path(p) for p in (changed + staged + untracked)]

        except Exception as e:
            log.error("Error getting changed files: %s", str(e))
            return []
