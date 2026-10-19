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
