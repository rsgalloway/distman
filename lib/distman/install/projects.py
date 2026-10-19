"""Inspect exact local project directories and preserve the legacy dist boundary."""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from distman.install import InstallError


@dataclass(frozen=True)
class Project:
    """Supported inputs discovered directly inside an explicitly selected directory.

    :param path: Absolute directory explicitly selected by the caller.
    :param buildable: Whether supported packaging metadata was discovered.
    :param requirement: Path of requirements.txt, or None if it is absent.
    :param distribution: Whether the directory contains dist.json.
    """

    path: Path
    buildable: bool
    requirement: Optional[Path]
    distribution: bool


def is_project_path(value: str) -> bool:
    """Distinguish explicit filesystem syntax from bare PyPI requirement names.

    :param value: Positional argument to classify without consulting filesystem contents.
    :return: True for supported explicit path syntax; False for a bare requirement name.
    """
    return (
        value in (".", "..")
        or value.startswith(("./", "../", ".\\", "..\\", "~/", "~\\"))
        or Path(value).is_absolute()
        or (len(value) > 2 and value[1:3] in (":/", ":\\"))
    )


def inspect_project(path: Path) -> Project:
    """Inspect packaging, requirements, and dist configuration without running code.

    Tool-only pyproject files do not make a project buildable. A declared build
    backend is delegated to pip; legacy setup.py discovery is static.
    No parents or conventional env subdirectories are searched.

    :param path: Exact project directory to inspect; no ancestor search is performed.
    :return: Discovered packaging, requirements, and custom-distribution inputs.
    :raises InstallError: If the directory is missing or contains no supported project inputs.
    :raises ValueError: If pyproject.toml cannot be parsed.
    """
    path = path.expanduser().absolute()
    if not path.is_dir():
        raise InstallError(f"Local project is not a directory: {path}")
    metadata = path / "pyproject.toml"
    buildable = (path / "setup.py").is_file()
    if metadata.is_file():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with metadata.open("rb") as stream:
            data = tomllib.load(stream)
        buildable = buildable or "project" in data or "build-system" in data
    requirement = path / "requirements.txt"
    project = Project(
        path,
        buildable,
        requirement if requirement.is_file() else None,
        (path / "dist.json").is_file(),
    )
    if not (project.buildable or project.requirement or project.distribution):
        raise InstallError(f"No packaging metadata, requirements.txt, or dist.json in {path}")
    return project


def prepare_arguments(args: argparse.Namespace) -> Tuple[argparse.Namespace, Optional[Project]]:
    """Copy CLI input, select at most one project, and add its requirements file.

    :param args: Parsed install options; the supplied namespace is not mutated.
    :return: Copied effective options and the selected local project, or None for package-only
        input.
    :raises InstallError: If multiple projects are selected or project inspection fails.
    """
    values = vars(args).copy()
    packages = []
    paths = [args.project] if args.project else []
    for value in args.packages:
        if is_project_path(value):
            paths.append(value)
        else:
            packages.append(value)
    if len(paths) > 1:
        raise InstallError("Select one local project per invocation")
    project = inspect_project(Path(paths[0])) if paths else None
    requirements = list(args.requirement)
    if project and project.requirement:
        requirements.append(str(project.requirement))
    values.update(
        packages=packages,
        requirement=requirements,
        project=str(project.path) if project and project.buildable else None,
    )
    return argparse.Namespace(**values), project


def distribute_project(
    project: Project, args: argparse.Namespace, environment: Mapping[str, str], dryrun: bool
) -> int:
    """Invoke the existing dist CLI with explicit policy and unchanged Git safeguards.

    A subprocess isolates legacy import-time configuration from the installer.
    The child inherits the source checkout on PYTHONPATH when used before release.

    :param project: Local project whose dist.json targets should be processed.
    :param args: Install options supplying force and verbosity for the legacy command.
    :param environment: Resolved policy values to overlay on the child process environment.
    :param dryrun: If True, invoke the legacy command in preview mode.
    :return: Exit status returned by the existing dist command.
    :raises OSError: If the child process cannot be started.
    """
    command = [
        sys.executable,
        "-c",
        "from distman.dist import main; raise SystemExit(main())",
        ".",
        "--yes",
    ]
    if dryrun:
        command.append("--dryrun")
    if args.force:
        command.append("--force")
    command.extend(["--verbose"] * args.verbose)
    return subprocess.run(
        command, env=project_environment(environment), cwd=project.path
    ).returncode


def project_environment(environment: Mapping[str, str]) -> Dict[str, str]:
    """Create explicit legacy-command policy without changing the parent process.

    :param environment: Resolved policy values to overlay on a copy of the current environment.
    :return: Child environment with explicit policy and a source-checkout Python import path.
    """
    result = dict(os.environ)
    result.update({key: str(value) for key, value in environment.items()})
    source_root = str(Path(__file__).resolve().parents[2])
    result["PYTHONPATH"] = os.pathsep.join([source_root, result.get("PYTHONPATH", "")])
    return result


def _distribution_paths(directory: str) -> List[str]:
    """Resolve configured targets inside the legacy configuration process boundary.

    :param directory: Project directory containing the dist.json file to load.
    :return: Absolute destination strings produced by existing dist target resolution.
    :raises InstallError: If the configuration cannot be read or has no valid targets.
    """
    from distman.dist import Distributor

    distributor = Distributor()
    try:
        if not distributor.read_dist_file(directory):
            raise InstallError(f"Could not read dist.json in {directory}")
        targets = distributor.iter_config_targets()
        if not targets:
            raise InstallError(f"No valid distribution targets in {directory}")
        return [str(Path(target.dest).absolute()) for target in targets]
    finally:
        distributor.close()


def distribution_paths(project: Project, environment: Mapping[str, str]) -> List[Path]:
    """Inspect legacy destinations so package publication cannot overlap custom targets.

    :param project: Project whose legacy target destinations should be inspected.
    :param environment: Resolved policy supplied to the isolated configuration process.
    :return: Resolved custom destination paths, without publishing any targets.
    :raises InstallError: If the child process cannot resolve the custom targets.
    """
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; from distman.install.projects import _distribution_paths; "
            "print(json.dumps(_distribution_paths(sys.argv[1])))",
            str(project.path),
        ],
        env=project_environment(environment),
        cwd=project.path,
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise InstallError(process.stderr.strip() or "Unable to inspect dist.json destinations")
    return [Path(value) for value in json.loads(process.stdout)]
