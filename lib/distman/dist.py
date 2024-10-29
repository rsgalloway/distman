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
Contains file distribution classes and functions.
"""

import filecmp
import os
import shutil
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

    @staticmethod
    def __get_file_versions(target):
        """Find the highest numeric version number for a file.

        :param target: Path to file to check.
        :return: List of tuples with version number and file.
        """
        filedir = os.path.dirname(target) + "/" + config.DIR_VERSIONS
        if not os.path.exists(filedir):
            return []

        filename = os.path.basename(target)
        version_list = []

        for f in os.listdir(filedir):
            file_name_length = len(filename)
            # get files that match <target>.<version>.<commit>
            if (
                f.startswith(filename)
                and len(f) > file_name_length + 1
                and f[file_name_length] == "."
                and str(f[file_name_length + 1]).isnumeric()
            ):
                # parse the number from the rest of the file name
                info = f[file_name_length + 1 :]
                dotPos = info.find(".")
                if -1 != dotPos:
                    ver = int(info[:dotPos])
                else:
                    ver = int(info)
                commit = ""
                if -1 != dotPos:
                    # trim potential remaining dotted portions
                    dotPos2 = info.find(".", dotPos + 1)
                    if -1 == dotPos2:
                        commit = info[dotPos + 1 :]
                    else:
                        commit = info[dotPos + 1 : dotPos2]
                    # trim '-forced' if present
                    dashPos = commit.find("-")
                    if -1 != dashPos:
                        commit = commit[:dashPos]
                version_list.append((filedir + "/" + f, ver, commit))

        return sorted(version_list, key=lambda tup: tup[1])

    @staticmethod
    def __hashes_equal(commitA, commitB):
        """Compares two hash strings regardless of length or case

        :param commitA: First hash string.
        :param commitB: Second hash string.
        :return: True if hashes are equal.
        """
        if len(commitA) > len(commitB):
            return commitA.upper().startswith(commitB.upper())
        else:
            return commitB.upper().startswith(commitA.upper())

    def __copy_file(self, source, dest):
        """Copies a file. Converts line endings to linux LF.

        :param source: Path to source file.
        :param dest: Path to destination file.
        """
        try:
            destdir = os.path.dirname(dest)
            if not os.path.isdir(destdir):
                os.makedirs(destdir)
            with open(source, "r") as infile, open(dest, "wb") as outfile:
                for line in infile:
                    text = line.rstrip("\r\n")
                    outfile.write((text + "\n").encode("UTF-8"))

        except UnicodeDecodeError:
            shutil.copyfile(source, dest)

    def __copy_directory(self, source, dest):
        """Recursively copies a directory (ignores hidden files).

        :param source: Path to source directory.
        :param dest: Path to destination directory.
        """
        source = os.path.relpath(source)
        all_files = self.get_files(source)

        for filepath in all_files:
            target = os.path.join(dest, filepath[len(source) + 1 :])
            self.__copy_file(filepath, target)

    def __copy_object(self, source, dest):
        """Copies a file or directory recursively (ignores hidden files).

        :param source: Path to source file or directory.
        :param dest: Path to destination file or directory.
        """
        if os.path.isfile(source):
            self.__copy_file(source, dest)
            return
        self.__copy_directory(source, dest)

    def __compare_files(self, filePathA, filePathB):
        """Compares two files, ignoring end of lines in text files.

        :param filePathA: Path to first file.
        :param filePathB: Path to second file.
        :return: True if files are equal.
        """
        try:
            with open(filePathA, "r") as file1, open(filePathB, "r") as file2:
                while True:
                    line1 = next(file1, None)
                    line2 = next(file2, None)
                    # if either file is finished return true
                    if line1 is None or line2 is None:
                        return line1 is None and line2 is None
                    # compare lines regardless of EOL
                    if line1.rstrip("\r\n") != line2.rstrip("\r\n"):
                        return False

        # do binary comparison if there are invalid characters
        except UnicodeDecodeError:
            return filecmp.cmp(filePathA, filePathB, shallow=False)

        except FileNotFoundError:
            return False

    def __compare_objects(self, objectA, objectB):
        """Compares two files or two directories.

        :param objectA: Path to first file or directory.
        :param objectB: Path to second file or directory.
        :return: True if objects are equal.
        """
        if os.path.isfile(objectA) and os.path.isfile(objectB):
            return self.__compare_files(objectA, objectB)

        objectA = os.path.relpath(objectA)
        all_files = self.get_files(objectA)

        for filepath in all_files:
            destPath = os.path.join(objectB, filepath[len(objectA) + 1 :])
            if not self.__compare_files(filepath, destPath):
                return False

        return True

    @staticmethod
    def __link_object(target, link, actualTarget):
        """Creates symbolic link to file or directory.

        :param target: Path to target file or directory.
        :param link: Path to symbolic link.
        :param actualTarget: Path to actual target file or directory.
        """
        isdir = os.path.isdir(actualTarget)
        try:
            os.symlink(target, link, target_is_directory=isdir)

        except OSError as e:
            log.error(
                "Failed to create %s symoblic link '%s => %s': %s"
                % ("directory" if isdir else "file", link, target, str(e))
            )
            return None
        return isdir

    @staticmethod
    def __replace_vars(pathstr):
        """Replaces tokens in string with environment variable or config default.

        :param pathstr: Path string with tokens.
        :return: Path string with tokens replaced.
        """
        while True:
            openBracket = pathstr.find(config.PATH_TOKEN_OPEN)
            closeBracket = pathstr.find(config.PATH_TOKEN_CLOSE)
            if -1 == openBracket or closeBracket <= openBracket:
                return pathstr
            var = pathstr[openBracket + 1 : closeBracket].upper()
            replacement = os.getenv(var, config.DEFAULT_ENV.get(var))
            if not replacement:
                raise Exception("Cannot resolve env var '%s'" % var)
            pathstr = pathstr[0:openBracket] + replacement + pathstr[closeBracket + 1 :]

    def get_files(self, start):
        """Returns the list of files to be disted.

        :param start: Starting directory.
        :return: List of relative file paths.
        """
        if self.repo:
            try:
                return [f for f in self.get_repo_files(start)]
            except Exception as e:
                log.warning("Failed to get files from git: %s" % str(e))
        return [f for f in util.walk(start)]

    def dist(
        self,
        target=None,
        show=False,
        force=False,
        yes=False,
        dryrun=False,
        verbose=False,
    ):
        """Performs the file distribution.

        :param target: target name in dist file.
        :param show: Show file versions.
        :param force: Force distribution.
        :param yes: Assume yes to all questions.
        :param dryrun: Perform dry run.
        :param verbose: Show more information.
        :return: True if successful.
        """
        if self.root is None:
            log.error("dist file not found or invalid")
            return False

        if not self.read_git_info():
            return False

        targets_node = self.get_targets()

        if targets_node is None:
            return False

        changed_files = self.git_changed_files()
        if config.DIST_FILE in changed_files:
            log.info(
                "WARNING: %s should be checked into git repository!" % config.DIST_FILE
            )

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

            # are we looking for a specific target?
            if target and target != target_name:
                continue

            try:
                dest = util.normalize_path(self.__replace_vars(dest))
            except Exception as e:
                log.info(
                    "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                )
                return False

            # make sure file exists
            if not os.path.exists(self.directory + os.path.sep + source):
                log.info(
                    "Target %s: Source '%s' does not exist" % (target_name, source)
                )
                return False

            # check if file has uncommitted changes
            if (
                not show
                and not force
                and os.path.abspath(self.directory + os.path.sep + source)
            ) in changed_files:
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
            version_list = self.__get_file_versions(dest)

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
            source_path = os.path.join(self.directory, source)

            if version_list:
                version_file, version_num, _ = version_list[-1]
                if version_file and self.__compare_objects(source_path, version_file):
                    if os.path.exists(dest) and os.path.lexists(dest):
                        log.info("Unchanged: %s => %s" % (source, version_file))
                    else:
                        question = (
                            "Target %s: link '%s' missing or broken,"
                            " fix it now?" % (target_name, dest)
                        )
                        if yes or util.yesNo(question):
                            if dryrun:
                                log.info("Fixed: %s => %s" % (source, version_file))
                            else:
                                if os.path.lexists(dest):
                                    util.remove_object(dest)
                                isdir = self.__link_object(
                                    config.DIR_VERSIONS
                                    + os.path.sep
                                    + os.path.basename(version_file),
                                    dest,
                                    version_file,
                                )
                                if isdir is not None:
                                    log.info(
                                        "Fixed: %s =%s> %s"
                                        % (
                                            source,
                                            "d" if isdir else "f",
                                            version_file,
                                        )
                                    )
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
                self.__copy_object(source_path, version_dest)
            # delete existing symbolic link if it exists
            if not dryrun and os.path.lexists(dest):
                util.remove_object(dest)
            # create the new symbolic link
            if dryrun:
                log.info("Updated: %s => %s" % (source, version_dest))
            else:
                isdir = self.__link_object(
                    config.DIR_VERSIONS + os.path.sep + os.path.basename(version_dest),
                    dest,
                    version_dest,
                )
                if isdir is not None:
                    log.info(
                        "Updated: %s =%s> %s"
                        % (source, "d" if isdir else "f", version_dest)
                    )

        if self.repo:
            self.repo.close()

        return True

    def reset_file_version(self, target, dryrun=False):
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
                dest = util.normalize_path(
                    self.__replace_vars(target_dict.get(config.TAG_DESTPATH))
                )
            except Exception as e:
                log.info(
                    "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                )
                return False

            if target and target != target_name:
                continue

            any_found = True
            version_list = self.__get_file_versions(dest)
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
                # create new link to point to requested file
                if dryrun:
                    log.info("%s => %s" % (source, verfile))
                else:
                    isdir = self.__link_object(
                        config.DIR_VERSIONS + os.path.sep + os.path.basename(verfile),
                        dest,
                        verfile,
                    )
                    if isdir is not None:
                        log.info(
                            "%s =%s> %s" % (source, "d" if isdir else "f", verfile)
                        )

        if not any_found:
            log.info("No targets found to reset")

        return any_found

    def change_file_version(self, target, target_commit, target_version, dryrun=False):
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
                dest = util.normalize_path(
                    self.__replace_vars(target_dict.get(config.TAG_DESTPATH))
                )
            except Exception as e:
                log.error(
                    "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                )
                return False

            if target and target != target_name:
                continue

            version_list = self.__get_file_versions(dest)
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
                    target_commit and self.__hashes_equal(target_commit, vercommit)
                ):
                    # found matching versioned target
                    any_found = True
                    any_found_this_target = True
                    # remove existing symbolic link
                    if not dryrun and os.path.lexists(dest):
                        util.remove_object(dest)
                    # create new symbolic link to point to requested versioned file
                    if dryrun:
                        log.info("%s => %s" % (source, verfile))
                    else:
                        isdir = self.__link_object(
                            config.DIR_VERSIONS
                            + os.path.sep
                            + os.path.basename(verfile),
                            dest,
                            verfile,
                        )
                        if isdir is not None:
                            log.info(
                                "%s =%s> %s" % (source, "d" if isdir else "f", verfile)
                            )
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

    def delete_target(self, target, yes=False, dryrun=False):
        """Delete a target's destination files. Deletes symlink, .dist and version
        files/directories.

        :param target: target name in dist file.
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
                if target and target != target_name:
                    continue

                source = util.normalize_path(target_dict.get(config.TAG_SOURCEPATH))
                try:
                    dest = util.normalize_path(
                        self.__replace_vars(target_dict.get(config.TAG_DESTPATH))
                    )
                except Exception as e:
                    log.info(
                        "%s in <%s> for %s" % (str(e), config.TAG_DESTPATH, target_name)
                    )
                    return False
                version_list = self.__get_file_versions(dest)
                question = "Delete target '%s' (%s => %s) and %d versions?" % (
                    target_name,
                    source,
                    dest,
                    len(version_list),
                )
                if yes or dryrun or util.yesNo(question):
                    any_found = True
                    if os.path.lexists(dest):
                        log.info("Deleting: %s" % dest)
                        if not dryrun:
                            util.remove_object(dest)
                    else:
                        log.info("Missing: %s" % dest)
                    if os.path.lexists(dest + config.DIST_INFO_EXT):
                        log.info("Deleting: %s" % dest + config.DIST_INFO_EXT)
                        if not dryrun:
                            os.remove(dest + config.DIST_INFO_EXT)
                    else:
                        log.info("Missing: %s" % dest + config.DIST_INFO_EXT)
                    for verFile, _, _ in version_list:
                        log.info("Deleting: %s" % verFile)
                        if not dryrun:
                            util.remove_object(verFile, recurse=True)

        if not any_found:
            log.info("No targets found to delete")

        return any_found
