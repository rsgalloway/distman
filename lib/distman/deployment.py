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

"""Git-independent, byte-preserving publication of prepared directories.

Uses the same sibling ``versions`` layout as Distributor. Callers serialize
writers and validate ownership before publishing. Each link replacement is
atomic; multiple targets have exception rollback, not global atomic visibility.
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import Iterable, Optional, Tuple, Union

from distman import config, util


def get_version_dest(dest: str, version_num: int, short_head: Optional[str]) -> str:
    """Generate a destination using distman's existing revision naming scheme.

    :param dest: Active destination whose sibling versions directory will be used.
    :param version_num: Distman revision number, independent of the package version.
    :param short_head: Optional source revision suffix; None omits the suffix.
    :return: Path of the numbered revision without creating it.
    """
    versions_dir = os.path.join(os.path.dirname(dest), config.DIR_VERSIONS)
    version_dest = os.path.join(versions_dir, os.path.basename(dest) + f".{version_num}")
    if short_head:
        version_dest += f".{short_head}"
    return util.sanitize_path(version_dest)


def replace_link(destination: Union[str, Path], target: Union[str, Path]) -> None:
    """Replace an active symlink without first unlinking the old revision.

    :param destination: Active symlink path to replace; its parent must already exist.
    :param target: Link target, relative to the destination parent or absolute.
    :return: None.
    :raises OSError: If link creation or atomic replacement fails.
    """
    destination = Path(destination)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.link")
    try:
        os.symlink(target, temporary, target_is_directory=True)
        os.replace(temporary, destination)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def prepare_revision(source: Path, destination: Path) -> Path:
    """Copy all bytes and modes before exposing a complete numbered revision.

    :param source: Complete staged directory to copy without normalizing its contents.
    :param destination: Active destination used to select the next sibling revision.
    :return: Path of the completed, inactive revision.
    :raises OSError: If the staged content cannot be copied or renamed.
    """
    destination = Path(destination)
    versions = util.get_file_versions(str(destination))
    number = versions[-1][1] + 1 if versions else 0
    revision = Path(get_version_dest(str(destination), number, None))
    revision.parent.mkdir(parents=True, exist_ok=True)
    temporary = revision.with_name(f".{revision.name}.{uuid.uuid4().hex}.staging")
    try:
        shutil.copytree(source, temporary, symlinks=True)
        os.rename(temporary, revision)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return revision


def publish_directories(changes: Iterable[Tuple[Path, Path]], deploy_root: Path) -> None:
    """Publish (staged directory, destination) pairs, restoring links on error.

    Existing unmanaged destinations are never replaced. Completed revisions
    remain available for inspection/rollback after a failed activation.

    :param changes: Pairs of staged source directories and active destination paths.
    :param deploy_root: Selected write root whose deployment epoch will be updated.
    :return: None.
    :raises ValueError: If an existing destination is not a managed symlink.
    :raises OSError: If preparation, activation, or epoch publication fails.
    """
    prepared = []
    for source, destination in changes:
        destination = Path(destination)
        if os.path.lexists(destination) and not destination.is_symlink():
            raise ValueError(f"Refusing to replace unmanaged directory: {destination}")
        previous = os.readlink(destination) if destination.is_symlink() else None
        revision = prepare_revision(source, destination)
        prepared.append((destination, revision, previous))
    activated = []
    try:
        for destination, revision, previous in prepared:
            replace_link(destination, os.path.relpath(revision, destination.parent))
            activated.append((destination, previous))
        if prepared:
            util.write_epoch_file(str(deploy_root))
    except BaseException:
        for destination, previous in reversed(activated):
            if previous is None:
                destination.unlink()
            else:
                replace_link(destination, previous)
        raise
