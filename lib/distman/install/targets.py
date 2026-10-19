"""Explicit CPython wheel targets; no target interpreter is executed."""

import platform
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Set

from packaging.tags import Tag, compatible_tags, cpython_tags, mac_platforms, sys_tags

from distman.install import InstallError


@dataclass(frozen=True)
class Target:
    """One CPython runtime and its OS/architecture wheel compatibility policy.

    :param platform: Normalized OS name: linux, windows, or darwin.
    :param arch: Normalized architecture used in installation paths.
    :param python: CPython version expressed as major.minor.
    :param native: If True, use the running interpreter's native compatibility tags.
    """

    platform: str
    arch: str
    python: str
    native: bool = False

    @property
    def label(self) -> str:
        """Return a stable runtime identity used in deployment receipts.

        :return: Runtime label such as linux-x86_64-cp311.
        """
        return f"{self.platform}-{self.arch}-cp{self.python.replace('.', '')}"

    @property
    def abi(self) -> str:
        """Return the version-specific CPython ABI tag.

        :return: Version-specific CPython ABI tag, such as cp311.
        """
        return f"cp{self.python.replace('.', '')}"

    @property
    def platforms(self) -> List[str]:
        """Return native or portable platform tags in preference order.

        :return: Ordered platform tags accepted by this runtime policy.
        """
        if self.native:
            return list(dict.fromkeys(tag.platform for tag in sys_tags() if tag.platform != "any"))
        if self.platform == "windows":
            return ["win_amd64"]
        if self.platform == "darwin":
            return list(mac_platforms((11, 0), self.arch))
        # Explicit portable Linux policy: glibc <= 2.28, down to manylinux1.
        tags = [f"manylinux_2_{minor}_{self.arch}" for minor in range(28, 4, -1)]
        tags.extend(f"manylinux{year}_{self.arch}" for year in (2014, 2010, 1))
        return tags

    @property
    def tags(self) -> Set[Tag]:
        """Return compatible wheel tags, including abi3 and pure-Python fallbacks.

        :return: Set of compatible interpreter, ABI, and platform tag combinations.
        """
        if self.native:
            return set(sys_tags())
        version = tuple(map(int, self.python.split(".")))
        return set(cpython_tags(version, [self.abi], self.platforms)) | set(
            compatible_tags(version, self.abi, self.platforms)
        )


def host_arch() -> str:
    """Normalize the host architecture to the names used in installation paths.

    :return: Lowercase host architecture, with amd64/aarch64 normalized.
    """
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )


def expand_targets(
    os_name: Optional[str] = None, arch: Optional[str] = None, python: Optional[str] = None
) -> List[Target]:
    """Expand the requested matrix, retaining host-native tags for the default.

    :param os_name: Comma-separated platform names or all; None selects the host OS.
    :param arch: Architecture name or alias; None selects the host architecture.
    :param python: Comma-separated CPython major.minor versions; None selects the host version.
    :return: Deduplicated runtime targets in request order.
    :raises InstallError: If the interpreter, platform, architecture, or Python version is
        unsupported.
    """
    if sys.implementation.name != "cpython":
        raise InstallError("The PoC supports CPython hosts only")
    host = platform.system().lower()
    systems = ["linux", "windows", "darwin"] if os_name == "all" else (os_name or host).split(",")
    versions = (python or f"{sys.version_info.major}.{sys.version_info.minor}").split(",")
    result = []
    for system in systems:
        system = {"osx": "darwin", "win64": "windows"}.get(system, system)
        if system not in ("linux", "windows", "darwin"):
            raise InstallError(f"Unsupported OS: {system}")
        architecture = arch or host_arch()
        architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(architecture, architecture)
        if architecture not in (("arm64", "x86_64") if system == "darwin" else ("x86_64",)):
            raise InstallError(f"Unsupported architecture: {system}/{architecture}")
        for version in versions:
            if not re.fullmatch(r"3\.(?:[8-9]|[1-9][0-9])", version):
                raise InstallError(f"Expected CPython major.minor >= 3.8, got {version!r}")
            target = Target(system, architecture, version, not any((os_name, arch, python)))
            if target not in result:
                result.append(target)
    return result
