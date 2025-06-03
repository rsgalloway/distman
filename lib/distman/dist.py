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
Contains file distribution classes and functions.
"""

import fnmatch
import os
import time

from distman import config, util
from distman.logger import log
from distman.source import GitRepo


class Distributor(GitRepo):
    """File distribution class."""

    def __init__(self):
        super(Distributor, self).__init__()
        self.__add_symlink_support()

    @staticmethod
    def __add_symlink_support():
        """Adds symlink support for platforms that lack it."""
        os_symlink = getattr(os, "symlink", None)
        if not callable(os_symlink):
            util.add_symlink_support()

    def dist(
        self,
        target: str = None,
        show: bool = False,
        force: bool = False,
        all: bool = False,
        yes: bool = False,
        dryrun: bool = False,
        versiononly: bool = False,
        verbose: bool = False,
    ):
        """Performs the file distribution.

        :param target: optionally match specific targets.
        :param show: Show file versions.
        :param force: Force distribution.
        :param all: Distribute all files (including ignorables).
        :param yes: Assume yes to all questions.
        :param dryrun: Perform dry run.
        :param versiononly: Distribute files only, do not create links.
        :param verbose: Show more information.
        :return: True if successful.
        """
        if self.root is None:
            log.error("%s not found or invalid" % config.DIST_FILE)
            return False

        if not self.read_git_info():
            return False

        targets_node = self.get_targets()

        if targets_node is None:
            return False

        changed_files = self.git_changed_files()
        changed_dirs = util.get_common_root_dirs(changed_files)

        if changed_files and (config.DIST_FILE in changed_files):
            log.warning("Uncommitted changes in %s" % config.DIST_FILE)

        targets = []
        for target_name, target_dict in targets_node.items():
            source = target_dict.get(config.TAG_SOURCEPATH)
            dest = target_dict.get(config.TAG_DESTPATH)
            if source is None or dest is None:
                log.info(
                    "Target %s: Missing '%s' or '%s' tag"
                    % (target_name, config.TAG_SOURCEPATH, config.TAG_DESTPATH)
                )
                self.root = None
                return False

            # optionally match on specific targets
            if target and not fnmatch.fnmatch(target_name, target):
                continue

            # wildcard support: expand sources if '*' in source
            if "*" in source:
                for source_path, dest_path in util.expand_wildcard_entry(source, dest):
                    dest = util.sanitize_path(util.replace_vars(dest_path))
                    targets.append((source_path, dest))
            else:
                try:
                    dest = util.sanitize_path(util.replace_vars(dest))
                except Exception as e:
                    log.info(
                        "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                    )
                    return False

                # relative path to the source file
                if source == ".":
                    source_path = self.directory
                else:
                    source_path = util.normalize_path(
                        os.path.join(self.directory, source)
                    )

                # make sure file exists
                if not os.path.exists(source_path):
                    log.info(
                        "Target %s: Source '%s' does not exist" % (target_name, source)
                    )
                    return False

                # check if file has uncommitted changes
                if (
                    not show
                    and not force
                    and (source_path in changed_files or source_path in changed_dirs)
                ):
                    log.info(
                        "Target %s: Source '%s' has uncommitted changes.  "
                        "Commit the changes or use --force." % (target_name, source)
                    )
                    return False

                # create destination directory if it does not exist (or exit)
                dest_dir = os.path.dirname(dest)
                if not show and not dryrun and not os.path.exists(dest_dir):
                    question = (
                        "Target %s: Destination directory '%s' does not "
                        "exist, create it now?" % (target_name, dest_dir)
                    )
                    if not yes and not util.yesNo(question):
                        return False
                    try:
                        os.makedirs(dest_dir)
                    except Exception as e:
                        log.info(
                            "ERROR: Failed to create directory '%s': %s"
                            % (dest_dir, str(e))
                        )
                        return False
                targets.append((source, dest))

        if not targets:
            if target:
                log.info("Target %s not found in %s", target, config.DIST_FILE)
            else:
                log.info("No targets found in %s", config.DIST_FILE)
            self.root = False
            return False

        # check if local git repo is behind remote
        if not show and not force and self.is_git_behind():
            return False

        if dryrun:
            log.info(config.DRYRUN_MESSAGE)

        # process targets listed in dist file
        for source, dest in targets:
            util.create_dest_folder(dest, dryrun, yes)

            # write the dist info file
            if not dryrun and not show:
                info = {
                    "name": self.name,
                    "origin": self.path,
                    "source": source,
                    "author": self.author,
                }
                util.write_dist_info(dest, info)

            # define dist version information
            version_num = 0
            version_file = ""
            version_list = util.get_file_versions(dest)

            # TODO: make show a separate method
            if show:
                if callable(getattr(os, "readlink", None)):
                    if not os.path.lexists(dest):
                        log.info("Missing: %s" % dest)
                    else:
                        log.info("%s => %s:" % (source, os.readlink(dest)))
                else:
                    log.info("%s:" % source)

                for version_file, version_num, version_commit in version_list:
                    log.info(
                        "%s: %s - %s"
                        % (
                            version_num,
                            version_file,
                            time.ctime(os.path.getmtime(version_file)),
                        )
                    )
                    if self.repo and verbose:
                        try:
                            commit = self.repo.commit(version_commit)
                            log.info("    %s" % commit.message.strip())
                            log.info(
                                "    %s - %s"
                                % (time.ctime(commit.committed_date), commit.author)
                            )
                        except Exception:
                            pass
                continue

            # relative path to the source file
            if source == ".":
                source_path = self.directory
            else:
                source_path = util.normalize_path(os.path.join(self.directory, source))

            if version_list:
                version_file, version_num, _ = version_list[-1]
                if version_file and util.compare_objects(source_path, version_file):
                    target_type = util.get_path_type(source_path)[0]
                    if os.path.exists(dest) and os.path.lexists(dest):
                        log.info(
                            "Unchanged: %s =%s> %s"
                            % (source, target_type, version_file)
                        )
                    else:
                        question = (
                            "Target %s: link '%s' missing or broken,"
                            " fix it now?" % (target_name, dest)
                        )
                        if yes or util.yesNo(question):
                            if dryrun:
                                log.info(
                                    "Fixed: %s =%s> %s"
                                    % (source, target_type, version_file)
                                )
                            else:
                                if os.path.lexists(dest):
                                    util.remove_object(dest)
                                link_created = util.link_object(
                                    config.DIR_VERSIONS
                                    + os.path.sep
                                    + os.path.basename(version_file),
                                    dest,
                                    version_file,
                                )
                                if link_created:
                                    log.info(
                                        "Fixed: %s =%s> %s"
                                        % (
                                            source,
                                            target_type,
                                            version_file,
                                        )
                                    )
                                else:
                                    log.warning(
                                        "Failed to fix: %s =%s> %s"
                                        % (source, target_type, dest)
                                    )

                    # skip to next target
                    continue

                version_num += 1

            # copy source file to the versioned destination
            versions_dir = os.path.dirname(dest) + "/" + config.DIR_VERSIONS
            if not dryrun and not os.path.exists(versions_dir):
                os.mkdir(versions_dir)
            version_dest = (
                versions_dir + "/" + os.path.basename(dest) + "." + str(version_num)
            )
            if self.short_head:
                version_dest += "." + self.short_head
                # note in file name if forced (not synced with current head)
                # if force:
                #     version_dest += "-forced"
            # note in file name if not on a main/master branch
            # FIXME: breaks if there is a / in the branch name (replace special chars)
            # if self.branch_name and self.branch_name not in config.MAIN_BRANCHES:
            #     version_dest += "." + self.branch_name
            # copy the file/directory to the versioned location
            if not dryrun:
                util.copy_object(source_path, version_dest, all_files=all)
            # delete existing symbolic link if it exists
            if not dryrun and os.path.lexists(dest):
                util.remove_object(dest)
            target_type = util.get_path_type(source)[0]
            # create the new symbolic link
            if dryrun and not versiononly:
                log.info("Updated: %s =%s> %s" % (source, target_type, version_dest))
            elif not versiononly:
                link_created = util.link_object(
                    config.DIR_VERSIONS + os.path.sep + os.path.basename(version_dest),
                    dest,
                    version_dest,
                )
                if link_created:
                    log.info(
                        "Updated: %s =%s> %s" % (source, target_type, version_dest)
                    )
                else:
                    log.warning("Failed to update: %s => %s" % (source, dest))

        if self.repo:
            try:
                self.repo.close()
            except:
                pass

        return True

    def reset_file_version(self, target: str, dryrun: bool = False):
        """Resets the symbolic link of a versioned file to point to the latest.

        :param target: target name in dist file.
        :param dryrun: Perform dry run.
        :return: True on success, False on failure.
        """
        targets_node = self.get_targets()

        if targets_node is None:
            return False

        if dryrun:
            log.info(config.DRYRUN_MESSAGE)

        any_found = False
        for target_name, target_dict in targets_node.items():
            if (
                target_dict.get(config.TAG_SOURCEPATH) is None
                or target_dict.get(config.TAG_DESTPATH) is None
            ):
                continue
            source = util.normalize_path(target_dict.get(config.TAG_SOURCEPATH))
            try:
                dest = util.sanitize_path(
                    util.replace_vars(target_dict.get(config.TAG_DESTPATH))
                )
            except Exception as e:
                log.info(
                    "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                )
                return False

            # optionally match on specific targets
            if target and not fnmatch.fnmatch(target_name, target):
                continue

            any_found = True
            version_list = util.get_file_versions(dest)
            if not version_list:
                log.info(
                    "Target %s: No versioned files found for '%s'"
                    % (target_name, source)
                )
            else:
                verfile = version_list[-1][0]
                # remove existing symbolic link
                if not dryrun and os.path.lexists(dest):
                    util.remove_object(dest)
                target_type = util.get_path_type(verfile)[0]
                # create new link to point to requested file
                if dryrun:
                    log.info("%s =%s> %s" % (source, target_type, verfile))
                else:
                    link_created = util.link_object(
                        config.DIR_VERSIONS + os.path.sep + os.path.basename(verfile),
                        dest,
                        verfile,
                    )
                    if link_created:
                        log.info("%s =%s> %s" % (source, target_type, verfile))

        if not any_found:
            log.info("No targets found to reset")

        return any_found

    def change_file_version(
        self,
        target: str,
        target_commit: str = None,
        target_version: str = None,
        dryrun: bool = False,
    ):
        """Changes the symbolic link of a versioned file to point to a different file.

        :param target: target name in dist file.
        :param target_commit: The commit hash to point to.
        :param target_version: The version number to point to.
        :param dryrun: Perform dry run.
        :return: True on success, False on failure.
        """
        targets_node = self.get_targets()

        if targets_node is None:
            return False

        if dryrun:
            log.info(config.DRYRUN_MESSAGE)

        any_found = False
        for target_name, target_dict in targets_node.items():
            if (
                target_dict.get(config.TAG_SOURCEPATH) is None
                or target_dict.get(config.TAG_DESTPATH) is None
            ):
                continue

            source = util.normalize_path(target_dict.get(config.TAG_SOURCEPATH))
            try:
                dest = util.sanitize_path(
                    util.replace_vars(target_dict.get(config.TAG_DESTPATH))
                )
            except Exception as e:
                log.error(
                    "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                )
                return False

            # optionally match on specific targets
            if target and not fnmatch.fnmatch(target_name, target):
                continue

            version_list = util.get_file_versions(dest)
            if not version_list:
                log.info(
                    "Target %s: No versioned files found for '%s'"
                    % (target_name, source)
                )
                continue
            any_found_this_target = False

            if target_version < 0:
                if abs(target_version) > len(version_list) - 1:
                    log.warning(
                        "Requested to roll back %d versions but there "
                        "are only %d previous versions for %s"
                        % (abs(target_version), len(version_list) - 1, source)
                    )
                    continue
                target_version = version_list[target_version - 1][1]

            for verfile, vernum, vercommit in version_list:
                if (not target_commit and vernum == target_version) or (
                    target_commit and util.hashes_equal(target_commit, vercommit)
                ):
                    # found matching versioned target
                    any_found = True
                    any_found_this_target = True
                    # remove existing symbolic link
                    if not dryrun and os.path.lexists(dest):
                        util.remove_object(dest)
                    target_type = util.get_path_type(verfile)[0]
                    # create new symbolic link to point to requested versioned file
                    if dryrun:
                        log.info("%s =%s> %s" % (source, target_type, verfile))
                    else:
                        link_created = util.link_object(
                            config.DIR_VERSIONS
                            + os.path.sep
                            + os.path.basename(verfile),
                            dest,
                            verfile,
                        )
                        if link_created:
                            log.info("%s =%s> %s" % (source, target_type, verfile))
                    break

            if not any_found_this_target:
                if target_commit:
                    log.info(
                        "Target commit %s not found for target %s"
                        % (target_commit, target_name)
                    )
                else:
                    log.info(
                        "Target version %d not found for target %s"
                        % (target_version, target_name)
                    )

        if not any_found:
            log.info("No targets found to change version")

        return any_found

    def delete_target(
        self,
        target: str,
        target_version: str = None,
        target_commit: str = None,
        yes: bool = False,
        dryrun: bool = False,
    ):
        """Delete a target's destination files. Deletes symlink, .dist and version
        files/directories.

        :param target: target name in dist file.
        :param target_version: The version number to delete.
        :param target_commit: The commit hash to delete.
        :param yes: Answer yes to all questions.
        :param dryrun: Perform dry run.
        :return: True on success, False on failure.
        """
        targets_node = self.get_targets()

        if targets_node is None:
            return False

        if dryrun:
            log.info(config.DRYRUN_MESSAGE)

        any_found = False
        for target_name, target_dict in targets_node.items():
            if (
                target_dict.get(config.TAG_SOURCEPATH) is not None
                and target_dict.get(config.TAG_DESTPATH) is not None
            ):
                # optionally match on specific targets
                if target and not fnmatch.fnmatch(target_name, target):
                    continue

                source = util.normalize_path(target_dict.get(config.TAG_SOURCEPATH))
                try:
                    dest = util.sanitize_path(
                        util.replace_vars(target_dict.get(config.TAG_DESTPATH))
                    )
                except Exception as e:
                    log.info(
                        "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                    )
                    return False

                version_list = util.get_file_versions(dest)

                # filter version list by version number or commit hash
                if version_list and target_version:
                    version_list = [
                        x for x in version_list if x[1] == int(target_version)
                    ]
                elif version_list and target_commit:
                    version_list = [
                        x
                        for x in version_list
                        if util.hashes_equal(target_commit, x[2])
                    ]

                question = "Delete target '%s' (%s => %s) and %d versions?" % (
                    target_name,
                    source,
                    dest,
                    len(version_list),
                )
                if yes or dryrun or util.yesNo(question):
                    any_found = True
                    distinfo = util.get_dist_info(dest=dest)
                    link_path = util.get_link_full_path(dest)

                    # if target is linked to the version being deleted, skip with warning
                    if (target_commit or target_version) and link_path in [
                        v[0] for v in version_list
                    ]:
                        log.warning(
                            """Cannot delete target '%s' because it is linked to the version being deleted"""
                            % target_name
                        )
                        continue

                    # delete link and dist info file
                    if not target_version and not target_commit:
                        if os.path.lexists(dest):
                            log.info("Deleting: %s" % dest)
                            if not dryrun:
                                util.remove_object(dest)
                        else:
                            log.info("Missing: %s" % dest)

                        if os.path.lexists(distinfo):
                            log.info("Deleting: %s" % distinfo)
                            if not dryrun:
                                os.remove(distinfo)
                        else:
                            log.info("Missing: %s" % distinfo)

                    # delete versioned files
                    for verFile, _, _ in version_list:
                        log.info("Deleting: %s" % verFile)
                        if not dryrun:
                            util.remove_object(verFile, recurse=True)

        if not any_found:
            log.info("No targets found to delete")

        return any_found
