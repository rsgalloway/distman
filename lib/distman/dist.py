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

import fnmatch
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple, List

from distman import config, util
from distman.logger import log
from distman.source import GitRepo


@dataclass
class Target:
    """Represents a distribution target with its name, source path, destination
    path and dist options."""

    name: str
    source: str
    dest: str
    options: Optional[dict] = None


def get_source_and_dest(target_dict: dict) -> Optional[Tuple[str, str]]:
    """Resolve the source and destination paths from the target dictionary.

    :param target_dict: Dictionary containing target information.
    :return: Tuple of source and destination paths, or None if not found.
    """
    source = target_dict.get(config.TAG_SOURCEPATH)
    dest = target_dict.get(config.TAG_DESTPATH)
    if source is None or dest is None:
        return None
    try:
        source = util.normalize_path(source)
        dest = util.sanitize_path(util.replace_vars(dest))
    except Exception as e:
        log.error(f"Error resolving paths: {e}")
        return None
    return source, dest


def confirm(prompt: str, yes: bool, dryrun: bool) -> bool:
    """Prompt the user for confirmation.

    :param prompt: The confirmation prompt message.
    :param yes: If True, automatically confirm without prompting.
    :param dryrun: If True, simulate the action without making changes.
    :return: True if confirmed, False otherwise.
    """
    return dryrun or yes or util.yesNo(prompt)


def update_symlink(dest: str, target: str, dryrun: bool) -> bool:
    """Update the symbolic link at `dest` to point to `target`.

    :param dest: The destination path where the symlink should be created.
    :param target: The target path that the symlink should point to.
    :param dryrun: If True, simulate the action without making changes.
    :return: True if the symlink was updated or would be updated in dry run mode.
    """
    if os.path.lexists(dest) and not dryrun:
        util.remove_object(dest)
    if dryrun:
        log.info("Would link: %s => %s", dest, target)
        return True
    return util.link_object(target, dest, target)


def get_version_dest(dest: str, version_num: int, short_head: Optional[str]) -> str:
    """Generate the destination path for a versioned file.

    :param dest: The original destination path.
    :param version_num: The version number to append.
    :param short_head: Optional short hash of the latest commit.
    :return: The versioned destination path.
    """
    versions_dir = os.path.join(os.path.dirname(dest), config.DIR_VERSIONS)
    os.makedirs(versions_dir, exist_ok=True)
    version_dest = os.path.join(
        versions_dir, os.path.basename(dest) + f".{version_num}"
    )
    if short_head:
        version_dest += f".{short_head}"
    return version_dest


def should_skip_target(target_name: str, pattern: Optional[str]) -> bool:
    """Check if a target should be skipped based on the provided pattern.

    :param target_name: The name of the target to check.
    :param pattern: The pattern to match against the target name.
    :return: True if the target should be skipped, False otherwise.
    """
    return pattern is not None and not fnmatch.fnmatch(target_name, pattern)


class Distributor(GitRepo):
    """Handles distribution of files based on a configuration file."""

    def __init__(self):
        """Initializes the Distributor class."""
        super().__init__()
        if not callable(getattr(os, "symlink", None)):
            util.add_symlink_support()

    def dist(
        self,
        target: Optional[str] = None,
        show: bool = False,
        force: bool = False,
        all: bool = False,
        yes: bool = False,
        dryrun: bool = False,
        versiononly: bool = False,
        verbose: bool = False,
    ) -> bool:
        """Distributes files based on targets defined in the dist file.

            {
                "author": "<email>",
                "targets": {
                    "<target>": {
                        "source": "<source-path>",
                        "destination": "<target-path>"
                    },
                }
            }

        :param target: Optional target pattern to filter targets.
        :param show: If True, shows distribution information without making changes.
        :param force: If True, forces the distribution even if there are uncommitted changes.
        :param all: If True, processes all files, ignoring changes.
        :param yes: If True, automatically confirms prompts.
        :param dryrun: If True, simulates the distribution without making changes.
        :param versiononly: If True, only updates the version without changing the symlink.
        :param verbose: If True, provides detailed output.
        :return: True if distribution was successful, False otherwise.
        """
        if not self.root:
            log.error(f"{config.DIST_FILE} not found or invalid")
            return False

        if not self.read_git_info():
            return False

        targets_node = self.get_targets()
        if not targets_node:
            return False

        changed_files = self.git_changed_files()
        changed_dirs = util.get_common_root_dirs(changed_files)
        global_options = self.root.get("options", {})

        if config.DIST_FILE in changed_files:
            log.warning(f"Uncommitted changes in {config.DIST_FILE}")

        target_list: List[Target] = []
        for name, entry in targets_node.items():
            if should_skip_target(name, target):
                continue

            source = entry.get(config.TAG_SOURCEPATH)
            dest = entry.get(config.TAG_DESTPATH)
            if source is None or dest is None:
                log.info(f"Target {name}: Missing source or dest path")
                continue

            # get target options
            target_options = util.get_effective_options(
                global_options, entry.get("options", {})
            )

            # check for wildcard in source
            if "*" in source:
                for src_path, dst_path in util.expand_wildcard_entry(source, dest):
                    try:
                        dst_resolved = util.sanitize_path(util.replace_vars(dst_path))
                        target_list.append(
                            Target(name, src_path, dst_resolved, target_options)
                        )
                    except Exception as e:
                        log.error(f"{e} resolving wildcard target {name}")
                        return False

            else:
                try:
                    dest_resolved = util.sanitize_path(util.replace_vars(dest))
                except Exception as e:
                    log.error(f"{e} in <dest> for {name}")
                    return False

                src_path = (
                    self.directory
                    if source == "."
                    else util.normalize_path(os.path.join(self.directory, source))
                )

                if not os.path.exists(src_path):
                    log.info(f"Target {name}: Source '{source}' does not exist")
                    return False

                if (
                    not show
                    and not force
                    and (src_path in changed_files or src_path in changed_dirs)
                ):
                    log.info(
                        f"Target {name}: Source '{source}' has uncommitted changes. Commit or use --force."
                    )
                    return False

                if (
                    not show
                    and not dryrun
                    and not os.path.exists(os.path.dirname(dest_resolved))
                ):
                    question = f"Target {name}: Destination dir '{os.path.dirname(dest_resolved)}' doesn't exist. Create?"
                    if not confirm(question, yes, dryrun):
                        return False
                    os.makedirs(os.path.dirname(dest_resolved), exist_ok=True)

                target_list.append(
                    Target(name, src_path, dest_resolved, target_options)
                )

        if not target_list:
            log.info(f"No matching targets in {config.DIST_FILE}")
            return False

        if not show and not force and self.is_git_behind():
            return False

        if dryrun:
            log.info(config.DRYRUN_MESSAGE)

        for t in target_list:
            util.create_dest_folder(t.dest, dryrun, yes)

            if not dryrun and not show:
                util.write_dist_info(
                    t.dest,
                    {
                        "name": self.name,
                        "origin": self.path,
                        "branch": self.branch_name,
                        "source": t.source,
                        "author": self.author,
                    },
                )

            version_list = util.get_file_versions(t.dest)
            if show:
                self.show_distribution_info(t.source, t.dest, version_list, verbose)
                continue

            source_path = t.source
            version_num = version_list[-1][1] + 1 if version_list else 0

            # look for matches within the last 10 versions
            matches = util.find_matching_versions(
                source_path, t.dest, version_list[-10:]
            )
            if matches and not force:
                match_file, match_num, _ = matches[-1]
                if os.path.islink(t.dest) and os.readlink(t.dest).endswith(
                    os.path.basename(match_file)
                ):
                    log.info(f"Unchanged: {t.source} => {match_file}")
                    continue
                if confirm(
                    f"Target: {t.source}: Found match {match_num}: {match_file}. Update link?",
                    yes,
                    dryrun,
                ):
                    update_symlink(t.dest, match_file, dryrun)
                    log.info(f"Updated: {t.source} => {match_file}")
                    continue

            version_dest = get_version_dest(t.dest, version_num, self.short_head)

            if not dryrun:
                util.copy_object(
                    source_path,
                    version_dest,
                    all_files=all,
                    substitute_tokens=t.options.get("substitute_tokens", False),
                )
                if not versiononly:
                    update_symlink(t.dest, version_dest, dryrun)
                    log.info(f"Updated: {t.source} => {version_dest}")
            elif not versiononly:
                log.info(f"Would update: {t.source} => {version_dest}")

        if self.repo:
            try:
                self.repo.close()
            except Exception:
                pass

        return True

    def reset_file_version(self, target: str, dryrun: bool = False) -> bool:
        """Reset the file version for the specified target.

        :param target: The target pattern to filter targets.
        :param dryrun: If True, simulates the reset without making changes.
        :return: True if any targets were reset, False otherwise.
        """
        targets_node = self.get_targets()
        if not targets_node:
            return False
        if dryrun:
            log.info(config.DRYRUN_MESSAGE)
        any_found = False
        for target_name, target_dict in targets_node.items():
            if should_skip_target(target_name, target):
                continue
            pair = get_source_and_dest(target_dict)
            if not pair:
                continue
            source, dest = pair
            any_found = True
            version_list = util.get_file_versions(dest)
            if not version_list:
                log.info(
                    f"Target {target_name}: No versioned files found for '{source}'"
                )
                continue
            latest_ver = version_list[-1][0]
            target_type = util.get_path_type(latest_ver)[0]
            if dryrun:
                log.info(f"{source} ={target_type}> {latest_ver}")
            else:
                update_symlink(dest, latest_ver, dryrun)
                log.info(f"{source} ={target_type}> {latest_ver}")
        if not any_found:
            log.info("No targets found to reset")
        return any_found

    def show_distribution_info(
        self,
        source: str,
        dest: str,
        version_list: List[Tuple[str, int, str]],
        verbose: bool,
    ) -> None:
        """Display distribution information for a target.

        :param source: The source path of the target.
        :param dest: The destination path of the target.
        :param version_list: List of versioned files with their metadata.
        :param verbose: If True, shows detailed commit information.
        :return: None
        """
        if callable(getattr(os, "readlink", None)):
            if not os.path.lexists(dest):
                log.info(f"Missing: {dest}")
            else:
                log.info(f"{source} => {os.readlink(dest)}:")
        else:
            log.info(f"{source}:")
        for version_file, version_num, version_commit in version_list:
            log.info(
                f"{version_num}: {version_file} - {time.ctime(os.path.getmtime(version_file))}"
            )
            if self.repo and verbose:
                try:
                    commit = self.repo.commit(version_commit)
                    log.info(f"    {commit.message.strip()}")
                    log.info(
                        f"    {time.ctime(commit.committed_date)} - {commit.author}"
                    )
                except Exception:
                    pass

    def change_file_version(
        self,
        target: str,
        target_commit: Optional[str] = None,
        target_version: Optional[int] = None,
        dryrun: bool = False,
    ) -> bool:
        """Change the version of a file for the specified target.

        :param target: The target pattern to filter targets.
        :param target_commit: Optional commit hash to reset to.
        :param target_version: Optional version number to reset to.
        :param dryrun: If True, simulates the change without making changes.
        :return: True if any targets were changed, False otherwise.
        """
        targets_node = self.get_targets()
        if not targets_node:
            return False
        if dryrun:
            log.info(config.DRYRUN_MESSAGE)

        any_found = False
        for target_name, target_dict in targets_node.items():
            if should_skip_target(target_name, target):
                continue

            pair = get_source_and_dest(target_dict)
            if not pair:
                continue
            source, dest = pair

            version_list = util.get_file_versions(dest)
            if not version_list:
                log.info(
                    f"Target {target_name}: No versioned files found for '{source}'"
                )
                continue

            if isinstance(target_version, int) and target_version < 0:
                if abs(target_version) > len(version_list) - 1:
                    log.warning(
                        f"Requested to roll back {abs(target_version)} versions but only {len(version_list) - 1} exist for {source}"
                    )
                    continue
                target_version = version_list[target_version - 1][1]

            matched = False
            for verfile, vernum, vercommit in version_list:
                if (target_commit and util.hashes_equal(target_commit, vercommit)) or (
                    target_version is not None and vernum == target_version
                ):
                    any_found = True
                    matched = True
                    target_type = util.get_path_type(verfile)[0]
                    if dryrun:
                        log.info(f"{source} ={target_type}> {verfile}")
                    else:
                        update_symlink(dest, verfile, dryrun)
                        log.info(f"{source} ={target_type}> {verfile}")
                    break

            if not matched:
                if target_commit:
                    log.info(
                        f"Target commit {target_commit} not found for target {target_name}"
                    )
                else:
                    log.info(
                        f"Target version {target_version} not found for target {target_name}"
                    )

        if not any_found:
            log.info("No targets found to change version")

        return any_found

    def delete_target(
        self,
        target: str,
        target_version: Optional[int] = None,
        target_commit: Optional[str] = None,
        yes: bool = False,
        dryrun: bool = False,
    ) -> bool:
        """Delete the specified target and its versions.

        :param target: The target pattern to filter targets.
        :param target_version: Optional version number to delete.
        :param target_commit: Optional commit hash to delete.
        :param yes: If True, automatically confirms prompts.
        :param dryrun: If True, simulates the deletion without making changes.
        :return: True if any targets were deleted, False otherwise.
        """
        targets_node = self.get_targets()
        if not targets_node:
            return False
        if dryrun:
            log.info(config.DRYRUN_MESSAGE)

        any_found = False
        for target_name, target_dict in targets_node.items():
            if should_skip_target(target_name, target):
                continue

            pair = get_source_and_dest(target_dict)
            if not pair:
                continue
            source, dest = pair

            version_list = util.get_file_versions(dest)
            if version_list:
                if target_version is not None:
                    version_list = [v for v in version_list if v[1] == target_version]
                elif target_commit:
                    version_list = [
                        v
                        for v in version_list
                        if util.hashes_equal(target_commit, v[2])
                    ]

            question = f"Delete target '{target_name}' ({source} => {dest}) and {len(version_list)} versions?"
            if not confirm(question, yes, dryrun):
                continue

            any_found = True
            distinfo = util.get_dist_info(dest=dest)
            link_path = util.get_link_full_path(dest)

            if (target_commit or target_version) and link_path in [
                v[0] for v in version_list
            ]:
                log.warning(
                    f"Cannot delete target '{target_name}' because it is linked to the version being deleted"
                )
                continue

            if target_commit is None and target_version is None:
                if os.path.lexists(dest):
                    log.info(f"Deleting: {dest}")
                    if not dryrun:
                        util.remove_object(dest)
                else:
                    log.info(f"Missing: {dest}")
                if os.path.lexists(distinfo):
                    log.info(f"Deleting: {distinfo}")
                    if not dryrun:
                        os.remove(distinfo)
                else:
                    log.info(f"Missing: {distinfo}")

            for verfile, _, _ in version_list:
                log.info(f"Deleting: {verfile}")
                if not dryrun:
                    util.remove_object(verfile, recurse=True)

        if not any_found:
            log.info("No targets found to delete")

        return any_found
