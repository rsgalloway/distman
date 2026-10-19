"""Use pip for resolution and PyPA installer for complete wheel installation."""

import argparse
import email
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from email.message import Message
from pathlib import Path, PurePosixPath
from typing import FrozenSet, List, Optional, Sequence, Set
from urllib.parse import unquote, urlsplit, urlunsplit

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.exceptions import InstallerError
from installer.sources import WheelFile
from packaging.specifiers import SpecifierSet
from packaging.tags import Tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from distman.install import InstallError
from distman.install.models import Provenance
from distman.install.targets import Target


def sha256(path: Path) -> str:
    """Hash file bytes incrementally without loading the entire artifact into memory.

    :param path: File whose bytes will be hashed.
    :return: Lowercase hexadecimal SHA-256 digest.
    :raises OSError: If the file cannot be read.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Artifact:
    """A verified wheel and the metadata used to classify its installation layout.

    :param path: Local wheel archive retained for staging and hashing.
    :param name: Canonical distribution name, distinct from import package names.
    :param version: Normalized upstream package version.
    :param tags: Compatibility tags parsed from the wheel filename.
    :param metadata: Parsed wheel METADATA headers.
    :param origin: Sanitized artifact URL, or original directory URI for a built project.
    """

    path: Path
    name: str
    version: str
    tags: FrozenSet[Tag]
    metadata: Message
    origin: str = ""

    @property
    def pure(self) -> bool:
        """Whether every declared tag is independent of platform and binary ABI.

        :return: True when all declared tags have the none ABI and any platform.
        """
        return all(tag.abi == "none" and tag.platform == "any" for tag in self.tags)

    @property
    def provenance(self) -> Provenance:
        """Describe the original wheel and dependency declarations for its receipt.

        :return: Receipt fields describing the source, wheel hash, tags, and dependencies.
        """
        return {
            "source_url": self.origin,
            "name": self.name,
            "version": self.version,
            "wheel": self.path.name,
            "sha256": sha256(self.path),
            "tags": sorted(map(str, self.tags)),
            "requires_python": self.metadata.get("Requires-Python", ""),
            "requires_dist": self.metadata.get_all("Requires-Dist", []),
        }


def inspect_wheel(path: Path, target: Target) -> Artifact:
    """Validate member paths, identity, Python constraints, and target wheel tags.

    :param path: Wheel archive to inspect without extracting its payload.
    :param target: Runtime against which tags and Requires-Python are checked.
    :return: Validated wheel identity, tags, and parsed distribution metadata.
    :raises InstallError: If member paths, metadata identity, or runtime compatibility are
        invalid.
    :raises zipfile.BadZipFile: If the artifact is not a readable ZIP archive.
    :raises ValueError: If a wheel filename, version, or Python constraint is malformed.
    """
    name, version, _, tags = parse_wheel_filename(path.name)
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            relative = PurePosixPath(member)
            if relative.is_absolute() or ".." in relative.parts or "\\" in member or ":" in member:
                raise InstallError(f"Unsafe wheel member: {member}")
        metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise InstallError(f"Expected one METADATA in {path.name}")
        message = email.message_from_bytes(archive.read(metadata[0]))
    if (
        canonicalize_name(message.get("Name", "")) != name
        or Version(message.get("Version", "0")) != version
    ):
        raise InstallError(f"Wheel metadata does not match filename: {path.name}")
    if not tags & target.tags or not SpecifierSet(message.get("Requires-Python", "")).contains(
        target.python + ".0", prereleases=True
    ):
        raise InstallError(f"Incompatible wheel for {target.label}: {path.name}")
    return Artifact(path, name, str(version), tags, message)


def pip(command: Sequence[str], verbose: bool = False) -> None:
    """Run pip in the current interpreter and expose actionable failure output.

    :param command: Pip subcommand and arguments, excluding the Python executable prefix.
    :param verbose: If True, print captured pip output even after a successful command.
    :return: None.
    :raises InstallError: If pip exits with a nonzero status.
    :raises OSError: If the subprocess cannot be started.
    """
    process = subprocess.run(
        [sys.executable, "-m", "pip", *command, "--disable-pip-version-check", "--no-input"],
        capture_output=True,
        text=True,
    )
    if verbose or process.returncode:
        print(process.stdout, end="")
    if process.returncode:
        raise InstallError(process.stderr.strip() or "pip failed")


def acquire(
    args: argparse.Namespace,
    target: Target,
    directory: Path,
    project_wheel: Optional[Path] = None,
) -> List[Artifact]:
    """Resolve with pip, fetch its exact selections, and verify reported hashes.

    :param args: Install options containing packages, requirement files, index policy, and
        verbosity.
    :param target: Runtime whose compatibility flags constrain pip resolution.
    :param directory: New download directory; its parent must already exist.
    :param project_wheel: Optional locally built wheel to include in dependency resolution.
    :return: Verified artifacts downloaded from the exact selections in pip's report.
    :raises InstallError: If resolution fails, selects non-wheel artifacts, or fails
        hash/compatibility checks.
    """
    directory.mkdir()
    report = directory.parent / (directory.name + "-report.json")
    command = [
        "install",
        "--dry-run",
        "--ignore-installed",
        "--only-binary=:all:",
        "--report",
        str(report),
        "--target",
        str(directory.parent / "resolve"),
    ]
    command += ["--python-version", target.python, "--implementation", "cp"]
    for abi in (target.abi, "abi3", "none"):
        command += ["--abi", abi]
    for platform_tag in target.platforms:
        command += ["--platform", platform_tag]
    if args.no_index:
        command.append("--no-index")
    for location in args.find_links:
        command += ["--find-links", location]
    for requirement in args.requirement:
        command += ["--requirement", requirement]
    command += args.packages
    if project_wheel:
        command.append(str(project_wheel))
    try:
        pip(command, bool(args.verbose))
        resolved = json.loads(report.read_text())["install"]
        sources = {}
        urls = []
        for item in resolved:
            download = item["download_info"]
            url = urlsplit(download["url"])
            filename = PurePosixPath(unquote(url.path)).name
            if not filename.endswith(".whl"):
                raise InstallError(f"Only prebuilt wheels are supported: {filename}")
            digest = download.get("archive_info", {}).get("hashes", {}).get("sha256")
            if not digest:
                raise InstallError(f"pip did not report a wheel SHA-256: {filename}")
            urls.append(urlunsplit(url._replace(fragment="sha256=" + digest)))
            safe_url = urlunsplit(
                url._replace(netloc=url.netloc.rsplit("@", 1)[-1], query="", fragment="")
            )
            sources[filename] = (safe_url, digest)
        # Download the exact artifacts from pip's resolution report, avoiding a
        # second solve that could choose a different release/index artifact.
        fetch = ["download", "--no-deps", "--only-binary=:all:", "--dest", str(directory)]
        fetch += ["--python-version", target.python, "--implementation", "cp"]
        for abi in (target.abi, "abi3", "none"):
            fetch += ["--abi", abi]
        for platform_tag in target.platforms:
            fetch += ["--platform", platform_tag]
        if not urls:
            raise InstallError(f"No wheels selected for {target.label}")
        pip(fetch + urls, bool(args.verbose))
    except InstallError as exc:
        raise InstallError(f"Wheel resolution failed for {target.label}: {exc}") from exc
    files = sorted(directory.glob("*.whl"))
    if not files or len(files) != len(list(directory.iterdir())):
        raise InstallError(f"Expected only prebuilt wheels for {target.label}")
    artifacts = []
    for path in files:
        origin, expected = sources[path.name]
        if sha256(path) != expected:
            raise InstallError(f"Wheel hash differs from pip resolution: {path.name}")
        artifact = inspect_wheel(path, target)
        artifact.origin = (
            Path(args.project).absolute().as_uri()
            if project_wheel and path.name == project_wheel.name
            else origin
        )
        artifacts.append(artifact)
    return artifacts


def build_project(args: argparse.Namespace, directory: Path) -> Path:
    """Ask pip to build one local project wheel through its supported backend.

    :param args: Install options specifying the project path, dry run, index policy, and
        verbosity.
    :param directory: Output directory where pip should place the single built wheel.
    :return: Path of the wheel produced by the project's build backend.
    :raises InstallError: If packaging metadata is missing, the build fails, or output is
        ambiguous.
    """
    project = Path(args.project).absolute()
    from distman.install.projects import inspect_project

    if not inspect_project(project).buildable:
        raise InstallError("Local project does not contain buildable packaging metadata")
    if args.dryrun:
        # Build backends may write egg-info or other generated files. Isolate
        # those writes from the requested source tree during a read-only preview.
        copy = directory.parent / "project-source"
        ignored_patterns = shutil.ignore_patterns(
            ".git", ".venv", "venv", "venv3", "__pycache__", directory.parent.name
        )

        def ignore_unavailable(source: str, names: List[str]) -> Set[str]:
            """Exclude generated directories and dangling links from the snapshot.

            :param source: Source directory currently being copied.
            :param names: Entry names found in the source directory.
            :return: Names to omit from the temporary project copy.
            """
            ignored = ignored_patterns(source, names)
            for name in names:
                path = Path(source) / name
                if path.is_symlink() and not path.exists():
                    ignored.add(name)
            return ignored

        # Resolve relative link targets against their source directory. Keep
        # dereferencing valid links so backend writes affect only the copy.
        shutil.copytree(project, copy, ignore=ignore_unavailable)
        project = copy
    command = ["wheel", "--no-deps", "--wheel-dir", str(directory), str(project)]
    if args.no_index:
        command.append("--no-index")
    for location in args.find_links:
        command += ["--find-links", location]
    pip(command, bool(args.verbose))
    wheels = list(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise InstallError("Project build did not produce exactly one wheel")
    return wheels[0]


def stage(artifact: Artifact, directory: Path, target: Target) -> None:
    """Install one artifact; paths are relative to its eventual import root.

    Ancillary schemes stay below that root so a shared-root revision can switch
    all wheel files together. RECORD contains the resulting relative paths.

    :param artifact: Validated wheel whose complete installation schemes will be preserved.
    :param directory: Empty staging import root; ancillary schemes are placed beneath it.
    :param target: Runtime used to select script launcher and interpreter conventions.
    :return: None.
    :raises InstallError: If the wheel installer rejects the archive or its RECORD.
    :raises OSError: If a staged file cannot be written.
    """
    schemes = {"purelib": ".", "platlib": ".", "headers": "include", "scripts": "bin", "data": "."}
    destination = SchemeDictionaryDestination(
        {key: str(directory / value) for key, value in schemes.items()},
        interpreter="python.exe" if target.platform == "windows" else "/usr/bin/env python3",
        script_kind="win-amd64" if target.platform == "windows" else "posix",
        bytecode_optimization_levels=(),
    )
    try:
        with WheelFile.open(artifact.path) as source:
            source.validate_record()
            install(source, destination, additional_metadata={"INSTALLER": b"distman\n"})
    except (InstallerError, zipfile.BadZipFile) as exc:
        raise InstallError(f"Invalid wheel {artifact.path.name}: {exc}") from exc
