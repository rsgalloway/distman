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
Contains source file distribution classes and functions.
"""

import json
import os

import git

from distman import config, util
from distman.logger import log


class Source(object):
    """File source base class."""

    def __init__(self):
        super(Source, self).__init__()
        self.author = os.getenv("USERNAME", os.getenv("USER", "unknown"))
        self.changed_files = []
        self.directory = "."
        self.name = ""
        self.path = ""
        self.root = None

    def get_targets(self):
        """Returns the list of targets in the dist file."""
        if self.root:
            return self.root.get(config.TAG_TARGETS)
        return None

    def read_dist_file(self, directory="."):
        """Opens and parses the dist file.

        :param directory: Path to directory containing the dist file.
        :return: True if successful.
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
            with open(dist_file, "r") as jsonFile:
                json_data = json.load(jsonFile)
        except Exception as e:
            log.error("Failed to parse dist file: %s", str(e))
            return False

        self.root = json_data
        self.author = self.root.get(config.TAG_AUTHOR, util.get_user())

        log.info("Author: %s" % self.author)
        if config.TAG_VERSION in self.root:
            self.dist_file_version = self.root[config.TAG_VERSION]
            if int(self.dist_file_version) < config.DIST_FILE_VERSION:
                log.warning(
                    "WARNING: Old dist file version: %s (current %d)"
                    % (self.dist_file_version, config.DIST_FILE_VERSION)
                )
            elif int(self.dist_file_version) > config.DIST_FILE_VERSION:
                log.error(
                    "ERROR: This dist file is newer than this script version: %s "
                    "(currrent %d)" % (self.dist_file_version, config.DIST_FILE_VERSION)
                )
                self.root = None
                return False

        return True


class GitRepo(Source):
    """Git repo file source base class."""

    def __init__(self):
        super(GitRepo, self).__init__()
        self.branch_name = ""
        self.head = ""
        self.repo = None
        self.short_head = ""

    def get_repo_files(self, start=None):
        """Generator that yields relative file paths tracked by this git repo.

        :param start: Starting directory.
        :return: List of relative file paths.
        """
        from pathlib import Path

        if start and os.path.exists(start):
            start = os.path.relpath(start)

        paths = []
        tree = self.repo.tree()

        def list_paths(root_tree, path=Path(".")):
            for blob in root_tree.blobs:
                yield path / blob.name
            for tree in root_tree.trees:
                yield from list_paths(tree, path / tree.name)

        for path in list_paths(tree):
            if start and not str(path).startswith(start):
                continue
            if path not in paths:
                paths.append(str(path))

        return paths

    def get_path(self):
        """Get the git repo path."""
        if self.repo and self.repo.remotes:
            if "origin" in [remote.name for remote in self.repo.remotes]:
                return self.repo.remotes.origin.url
            else:
                return self.repo.remotes[0].url
        return self.path

    def read_git_info(self):
        """Read git repo information."""
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

            # remove user name and protocol from path
            if "@" in self.path:
                self.path = self.path[self.path.find("@") + 1 :]

            # change ssh style path to look like https style
            # self.path = self.path.replace(":", "/")
            self.name = self.path[self.path.rfind("/") + 1 :]
            if self.name.endswith(".git"):
                self.name = self.name[:-4]

            # this will generate TypeError if detached HEAD
            branch = self.repo.active_branch
            self.branch_name = branch.name
            if branch.name not in config.MAIN_BRANCHES:
                log.warning("WARNING: Not on master branch")

        except git.InvalidGitRepositoryError:
            log.warning("Not in a git repository directory")

        except (AttributeError, TypeError) as e:
            log.warning("WARNING: %s", str(e))

        except Exception as e:
            log.warning("ERROR: error reading git repo\n%s", str(e))

        log.info("Name: %s" % self.name)
        log.info("Path: %s" % self.path)
        if self.branch_name:
            log.info("Branch: %s" % self.branch_name)
            log.info("Head: %s (%s)" % (self.head, self.short_head))

        return True

    def is_git_behind(self):
        """Checks for upstream commits on the repo."""
        if not self.branch_name:
            return False

        if not self.repo.remotes:
            return False

        try:
            upstream_commits = list(
                self.repo.iter_commits(f"origin/{self.branch_name}..{self.branch_name}")
            )
            if upstream_commits:
                log.error(
                    "Directory is %d commits behind remote repository. "
                    "Run: git pull from origin first or use --force."
                    % len(upstream_commits)
                )
                return True

        except Exception as err:
            log.error("Error checking remote branch: %s", str(err))
            return True

        return False

    def git_changed_files(self):
        """Returns list of changed files (in staging or untracked).

        :return: List of changed files.
        """
        if not self.repo:
            return []

        # get list of changed files
        changed_files = [
            os.path.abspath(self.directory + os.path.sep + item.a_path)
            for item in self.repo.index.diff(None)
        ]

        # get list of staged files
        changed_files += [
            os.path.abspath(self.directory + os.path.sep + item.a_path)
            for item in self.repo.index.diff("HEAD")
        ]

        # get list of untracked files
        changed_files += [
            os.path.abspath(self.directory + os.path.sep + item)
            for item in self.repo.untracked_files
        ]

        return changed_files
