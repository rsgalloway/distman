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

"""Typed values exchanged by acquisition, ownership, and orchestration."""

from pathlib import Path
from typing import Dict, List, Tuple, TypedDict


class FileDigest(TypedDict):
    """Integrity and permission data for one installed relative file path.

    :ivar sha256: SHA-256 digest of installed file bytes.
    :ivar mode: Installed permission bits masked to 0o777.
    """

    sha256: str
    mode: int


class Provenance(TypedDict):
    """Immutable artifact identity and declared Python compatibility metadata.

    :ivar source_url: Sanitized wheel URL or original local project URI.
    :ivar name: Canonical distribution name.
    :ivar version: Normalized upstream version.
    :ivar wheel: Original wheel filename.
    :ivar sha256: SHA-256 digest of the wheel archive.
    :ivar tags: Sorted string representations of wheel compatibility tags.
    :ivar requires_python: Requires-Python constraint, or an empty string.
    :ivar requires_dist: Declared Requires-Dist metadata entries.
    """

    source_url: str
    name: str
    version: str
    wheel: str
    sha256: str
    tags: List[str]
    requires_python: str
    requires_dist: List[str]


class Selection(Provenance):
    """Provenance plus the runtimes sharing an artifact's installed payload.

    :ivar pure: Whether the wheel has only platform/ABI-independent tags.
    :ivar targets: Runtime labels sharing this selection.
    :ivar acquisition: Description of the packaging-tool resolution source.
    """

    pure: bool
    targets: List[str]
    acquisition: str


class InstalledArtifact(Selection):
    """A selected artifact and its complete inventory within a target boundary.

    :ivar files: Owned relative paths mapped to installed checksums and permission modes.
    """

    files: Dict[str, FileDigest]


class InstallState(TypedDict):
    """Internal deployment receipt for all artifacts in a composed revision.

    :ivar schema: Internal receipt schema version.
    :ivar artifacts: Ownership keys mapped to complete installed artifact records.
    """

    schema: int
    artifacts: Dict[str, InstalledArtifact]


# An addition names its ownership key, staged import root, receipt, and prefix.
Addition = Tuple[str, Path, Selection, Path]
Groups = Dict[Path, Dict[str, Addition]]
Changes = List[Tuple[Path, Path]]
