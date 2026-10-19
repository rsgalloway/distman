"""Offline wheels exercise real pip resolution, installation and publication."""

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

pytest.importorskip("installer")
if importlib.util.find_spec("pathbase") is None:
    pytest.skip("install extra is unavailable", allow_module_level=True)

from distman import cli, deployment, util  # noqa: E402
from distman.install import InstallError  # noqa: E402
from distman.install.ownership import read_state  # noqa: E402
from distman.install.policy import Policy  # noqa: E402
from distman.install.targets import expand_targets  # noqa: E402
from distman.install.wheels import inspect_wheel  # noqa: E402


def wheel(
    directory: Path,
    name: str = "sample",
    version: str = "1.0",
    tag: str = "py3-none-any",
    files: Optional[Dict[str, bytes]] = None,
    requires: Sequence[str] = (),
    python: str = ">=3.8",
) -> Path:
    """Write a valid wheel archive with hashed RECORD entries for offline tests."""
    normalized = name.replace("-", "_").replace(".", "_")
    info = f"{normalized}-{version}.dist-info"
    contents = dict(files or {f"{normalized}/__init__.py": f"VERSION = '{version}'\n".encode()})
    contents[f"{info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: {python}\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires)
    ).encode()
    contents[f"{info}/WHEEL"] = (
        f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {tag}\n".encode()
    )
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for path, data in contents.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        writer.writerow([path, f"sha256={digest}", len(data)])
    writer.writerow([f"{info}/RECORD", "", ""])
    contents[f"{info}/RECORD"] = output.getvalue().encode()
    path = directory / f"{normalized}-{version}-{tag}.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for filename, data in contents.items():
            archive.writestr(filename, data)
    return path


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tuple[Path, Path]:
    """Isolate installation policy and provide an offline wheelhouse."""
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith(("DISTMAN_", "PIP_")) or key in ("ROOT", "TOOLS_ROOT", "ENVPATH"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DEPLOY_ROOT", str(tmp_path / "dev"))
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("PIP_CONFIG_FILE", os.devnull)
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    return tmp_path / "dev", wheelhouse


def invoke(wheelhouse: Path, *args: str) -> int:
    """Run the install CLI noninteractively against a local wheelhouse."""
    return cli.main(["install", "--yes", "--no-index", "--find-links", str(wheelhouse), *args])


def test_metadata_multiple_roots_data_and_idempotency(installation: Tuple[Path, Path]) -> None:
    """Retain complete wheel schemes, metadata, bytes, and idempotent publication."""
    root, wheels = installation
    wheel(
        wheels,
        files={
            "sample/__init__.py": b"value = 42\r\n",
            "another.py": b"answer = 42\n",
            "sample-1.0.data/data/share/sample.txt": b"example\r\n",
            "sample-1.0.data/scripts/sample-tool": b"#!python\nprint('ok')\n",
        },
    )
    assert invoke(wheels, "sample==1.0") == 0
    target = root / "tools/lib/python"
    assert target.is_symlink()
    assert (target / "sample/__init__.py").read_bytes() == b"value = 42\r\n"
    assert (target / "share/sample.txt").read_bytes() == b"example\r\n"
    assert (target / "bin/sample-tool").exists()
    assert (target / "sample-1.0.dist-info/RECORD").exists()
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sample, another; from importlib.metadata import version; "
            "assert sample.value == another.answer == 42; assert version('sample') == '1.0'",
        ],
        env=dict(os.environ, PYTHONPATH=str(target)),
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    first = os.readlink(target)
    assert invoke(wheels, "sample==1.0") == 0
    assert os.readlink(target) == first
    assert len(util.get_file_versions(str(target))) == 1
    assert not (root / "lib/python").exists()


def test_dependencies_namespaces_and_upgrade(installation: Tuple[Path, Path]) -> None:
    """Keep unrelated namespace files and reject upgrades breaking retained dependencies."""
    root, wheels = installation
    wheel(wheels, "dep", files={"space/dep.py": b"VALUE=1\n"})
    wheel(
        wheels,
        "app",
        files={"space/app.py": b"VALUE=1\n", "obsolete.py": b"old\n"},
        requires=["dep<2"],
    )
    assert invoke(wheels, "app") == 0
    wheel(wheels, "app", "2.0", files={"space/app.py": b"VALUE=2\n"}, requires=["dep<2"])
    assert invoke(wheels, "app==2") == 0
    target = root / "tools/lib/python"
    assert (target / "space/dep.py").exists()
    assert not (target / "obsolete.py").exists()
    assert not (target / "app-1.0.dist-info").exists()
    wheel(wheels, "dep", "2.0", files={"space/dep.py": b"VALUE=2\n"})
    previous = os.readlink(target)
    assert invoke(wheels, "dep==2") == 1
    assert os.readlink(target) == previous
    assert read_state(target)["artifacts"]["dep"]["version"] == "1.0"


def test_collision_and_tampering_fail_closed(installation: Tuple[Path, Path]) -> None:
    """Refuse conflicting ownership and modified installed content, even with force."""
    root, wheels = installation
    wheel(wheels, "one", files={"shared.py": b"1\n"})
    wheel(wheels, "two", files={"shared.py": b"2\n"})
    assert invoke(wheels, "one") == 0
    target = root / "tools/lib/python"
    original = os.readlink(target)
    assert invoke(wheels, "two", "--force") == 1
    assert os.readlink(target) == original
    (target / "shared.py").write_bytes(b"changed")
    assert invoke(wheels, "one", "--force") == 1
    assert os.readlink(target) == original


def test_matrix_deduplicates_pure_wheels(installation: Tuple[Path, Path]) -> None:
    """Publish a universal distribution only once across a six-runtime matrix."""
    root, wheels = installation
    wheel(wheels, "Some.Package")
    assert (
        invoke(
            wheels,
            "some-package",
            "--platform",
            "linux,windows,darwin",
            "--arch",
            "x86_64",
            "--python",
            "3.11,3.12",
        )
        == 0
    )
    target = root / "tools/lib/python"
    record = read_state(target)["artifacts"]["some-package"]
    assert len(record["targets"]) == 6
    assert len(util.get_file_versions(str(target))) == 1
    assert not (root / "tools/some-package").exists()


def test_binary_monolithic_incremental_and_dryrun(installation: Tuple[Path, Path]) -> None:
    """Preview without writes and preserve older binary variants in new revisions."""
    root, wheels = installation
    wheel(wheels, "native", tag="cp311-cp311-manylinux_2_17_x86_64")
    wheel(wheels, "native", tag="cp312-cp312-manylinux_2_17_x86_64")
    args = ("native", "--platform", "linux", "--arch", "x86_64")
    assert invoke(wheels, *args, "--python", "3.11", "--dryrun") == 0
    assert not root.exists()
    assert invoke(wheels, *args, "--python", "3.11") == 0
    target = root / "tools/native/1.0"
    first = target.resolve()
    assert invoke(wheels, *args, "--python", "3.12") == 0
    assert target.resolve() != first
    assert (target / "linux/x86_64/python3.11/lib/python/native/__init__.py").exists()
    assert (target / "linux/x86_64/python3.12/lib/python/native/__init__.py").exists()
    assert not (first / "linux/x86_64/python3.12").exists()


def test_requirements_delegate_to_pip(installation: Tuple[Path, Path], tmp_path: Path) -> None:
    """Delegate includes, constraints, and comments to pip requirements handling."""
    root, wheels = installation
    wheel(wheels, "sample")
    (tmp_path / "constraints.txt").write_text("sample==1.0\n")
    (tmp_path / "requirements.txt").write_text("-c constraints.txt\nsample # comment\n")
    assert invoke(wheels, "-r", str(tmp_path / "requirements.txt")) == 0
    assert (root / "tools/lib/python/sample").exists()


def test_policy_active_tier_missing_include_and_templates(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preserve the active tier while loading optional includes and custom path templates."""
    root, wheels = installation
    (tmp_path / "distman.env").write_text(
        "include: [missing-optional]\nall:\n  ENV: prod\n  DEPLOY_ROOT: /not-the-write-root\n"
        "  TOOLS_ROOT: ${DEPLOY_ROOT}/third-party\n  DISTMAN_PURE_PATH: '{TOOLS_ROOT}/imports'\n"
    )
    policy = Policy()
    assert policy.values["ENV"] == "dev"
    assert policy.deploy_root == root
    wheel(wheels)
    assert invoke(wheels, "sample") == 0
    assert (root / "third-party/imports/sample").exists()
    assert os.environ["ENV"] == "dev"


def test_standalone_fallback(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Use a writable standalone root when studio configuration is absent."""
    monkeypatch.delenv("DEPLOY_ROOT")
    monkeypatch.delenv("ENV")
    monkeypatch.setenv("HOME", str(tmp_path))
    policy = Policy()
    assert policy.deploy_root == tmp_path / ".local/distman/prod"


def test_missing_wheel_and_unmanaged_destination(installation: Tuple[Path, Path]) -> None:
    """Abort missing runtime selection and refuse unmanaged active directories."""
    root, wheels = installation
    wheel(wheels, tag="cp311-cp311-win_amd64")
    assert invoke(wheels, "sample", "--platform", "linux", "--python", "3.11") == 1
    assert not root.exists()
    wheel(wheels)
    target = root / "tools/lib/python"
    target.mkdir(parents=True)
    (target / "unowned.py").write_text("keep me")
    assert invoke(wheels, "sample", "--force") == 1
    assert (target / "unowned.py").read_text() == "keep me"


def test_abi3_and_requires_python(tmp_path: Path) -> None:
    """Recognize stable ABI wheels and reject incompatible Python constraints."""
    artifact = wheel(tmp_path, tag="cp38-abi3-win_amd64", python=">=3.8")
    for target in expand_targets("windows", "x86_64", "3.11,3.12"):
        assert not inspect_wheel(artifact, target).pure
    incompatible = wheel(tmp_path, "new", python=">=3.13")
    with pytest.raises(InstallError, match="Incompatible"):
        inspect_wheel(incompatible, expand_targets("windows", "x86_64", "3.12")[0])


def test_activation_failure_restores_prior_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore all previously switched targets if a later activation fails."""
    stages = [tmp_path / "stage1", tmp_path / "stage2"]
    destinations = [tmp_path / "out/one", tmp_path / "out/two"]
    for stage in stages:
        stage.mkdir()
        (stage / "payload").write_bytes(b"original\r\n")
    changes = list(zip(stages, destinations))
    deployment.publish_directories(changes, tmp_path / "out")
    previous = [os.readlink(dest) for dest in destinations]
    for stage in stages:
        (stage / "payload").write_bytes(b"updated\r\n")
    replace = deployment.replace_link

    def fail_second(destination: Path, target: str) -> None:
        """Fail the second activation while allowing restoration of prior links."""
        if destination == destinations[1] and target.endswith("two.1"):
            raise OSError("activation failure")
        replace(destination, target)

    monkeypatch.setattr(deployment, "replace_link", fail_second)
    with pytest.raises(OSError, match="activation failure"):
        deployment.publish_directories(changes, tmp_path / "out")
    assert [os.readlink(dest) for dest in destinations] == previous
    assert (destinations[0] / "payload").read_bytes() == b"original\r\n"


def test_unsupported_env_has_clear_error(capsys: pytest.CaptureFixture) -> None:
    """Explain the deferred environment-selection integration without a traceback."""
    assert cli.main(["install", "--env", "myapp"]) == 1
    assert "deferred" in capsys.readouterr().out


def test_project_build_delegated_to_pip(installation: Tuple[Path, Path], tmp_path: Path) -> None:
    """Use a PEP 517 backend for the explicit project option."""
    root, wheels = installation
    project = tmp_path / "project"
    project.mkdir()
    artifact = wheel(project, "built-project")
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires = []\n" 'build-backend = "backend"\nbackend-path = ["."]\n'
    )
    (project / "backend.py").write_text(
        "import shutil\nfrom pathlib import Path\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        f"    name = {artifact.name!r}\n"
        "    shutil.copyfile(Path(__file__).parent / name, Path(wheel_directory) / name)\n"
        "    return name\n"
    )
    assert invoke(wheels, "--project", str(project)) == 0
    assert (root / "tools/lib/python/built_project-1.0.dist-info/METADATA").exists()


def test_pure_upgrade_preserves_prior_runtime_compatibility(
    installation: Tuple[Path, Path]
) -> None:
    """Reject shared upgrades that exclude a previously recorded Python runtime."""
    root, wheels = installation
    wheel(wheels, python=">=3.8")
    assert invoke(wheels, "sample", "--python", "3.11") == 0
    wheel(wheels, version="2.0", python=">=3.12")
    target = root / "tools/lib/python"
    before = os.readlink(target)
    assert invoke(wheels, "sample==2.0", "--python", "3.12") == 1
    assert os.readlink(target) == before


def test_cross_target_marker_omission_fails_closed(installation: Tuple[Path, Path]) -> None:
    """Detect foreign-platform dependencies omitted by host marker resolution."""
    root, wheels = installation
    wheel(wheels, "app", requires=['missing-windows-dep; sys_platform == "win32"'])
    assert invoke(wheels, "app", "--platform", "windows", "--python", "3.11") == 1
    assert not (root / "tools/lib/python").exists()


def test_entry_point_is_installed(installation: Tuple[Path, Path]) -> None:
    """Generate a usable console script with the wheel installer."""
    root, wheels = installation
    wheel(
        wheels,
        files={
            "sample.py": b"def main():\n    print('entrypoint works')\n",
            "sample-1.0.dist-info/entry_points.txt": (
                b"[console_scripts]\nsample-cli = sample:main\n"
            ),
        },
    )
    assert invoke(wheels, "sample") == 0
    target = root / "tools/lib/python"
    script = target / "bin" / ("sample-cli.exe" if os.name == "nt" else "sample-cli")
    assert script.exists()
    command = [str(script)] if os.name == "nt" else [sys.executable, str(script)]
    process = subprocess.run(
        command,
        env=dict(os.environ, PYTHONPATH=str(target)),
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "entrypoint works"


def test_core_help_without_optional_dependencies() -> None:
    """Keep legacy command help available without packaging or policy extras."""
    source = str(__import__("pathlib").Path(cli.__file__).parents[1])
    script = """
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('envstack', 'pathbase', 'installer', 'packaging'):
            raise ImportError('optional dependency blocked')
sys.meta_path.insert(0, BlockOptional())
from distman.cli import main
main(['dist', '--help'])
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(os.environ, PYTHONPATH=source),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_production_lookup_does_not_redirect_development_writes(
    installation: Tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep production fallback lookup separate from development publication."""
    root, wheels = installation
    production = tmp_path / "prod"
    production_env = production / "env"
    production_env.mkdir(parents=True)
    (production_env / "default.env").write_text(
        f"all:\n  ENV: prod\n  DEPLOY_ROOT: {production}\n" "  TOOLS_ROOT: ${DEPLOY_ROOT}/tools\n"
    )
    sentinel = production / "tools/lib/python/sentinel.py"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("untouched")
    monkeypatch.setenv("ENVPATH", str(production_env))
    wheel(wheels)
    assert invoke(wheels, "sample") == 0
    assert (root / "tools/lib/python/sample").exists()
    assert sentinel.read_text() == "untouched"
    assert not (production / "tools/lib/python/sample").exists()


def test_failed_copy_never_activates_partial_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave the active revision intact and remove an incomplete staging copy."""
    import shutil

    source = tmp_path / "source"
    source.mkdir()
    (source / "one").write_bytes(b"original")
    destination = tmp_path / "target"
    deployment.publish_directories([(source, destination)], tmp_path)
    previous = os.readlink(destination)

    def broken_copy(source: Path, destination: Path, **kwargs: Any) -> None:
        """Simulate a filesystem failure after writing part of a staging copy."""
        destination.mkdir()
        (destination / "one").write_bytes(b"partial")
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copytree", broken_copy)
    with pytest.raises(OSError, match="copy failed"):
        deployment.publish_directories([(source, destination)], tmp_path)
    assert os.readlink(destination) == previous
    assert (destination / "one").read_bytes() == b"original"
    assert len(util.get_file_versions(str(destination))) == 1
    assert not list((tmp_path / "versions").glob("*.staging"))


def make_project(directory: Path, requires: Sequence[str] = ()) -> Path:
    """Create an offline PEP 517 project whose backend returns a fixture wheel."""
    directory.mkdir()
    artifact = wheel(directory, "local-project", requires=requires)
    (directory / "pyproject.toml").write_text(
        '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n'
    )
    (directory / "backend.py").write_text(
        "import shutil\nfrom pathlib import Path\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        "    Path(__file__).with_name('backend-ran').write_text('built')\n"
        f"    name = {artifact.name!r}\n"
        "    shutil.copyfile(Path(__file__).parent / name, Path(wheel_directory) / name)\n"
        "    return name\n"
    )
    return directory


def configure_dist(project: Path) -> None:
    """Give a fixture project one ordinary custom-software distribution target."""
    (project / "custom").mkdir()
    (project / "custom/main.py").write_text("VALUE = 42\n")
    (project / "dist.json").write_text(
        json.dumps(
            {"targets": {"custom": {"source": "custom", "destination": "{DEPLOY_ROOT}/lib/custom"}}}
        )
    )


def test_positional_packaging_project(installation: Tuple[Path, Path], tmp_path: Path) -> None:
    """An explicit positional path builds and installs a packaging-only project."""
    root, wheels = installation
    project = make_project(tmp_path / "project")
    assert invoke(wheels, str(project)) == 0
    target = root / "tools/lib/python"
    assert (target / "local_project/__init__.py").exists()
    previous = os.readlink(target)
    assert invoke(wheels, str(project)) == 0
    assert os.readlink(target) == previous


def test_requirements_only_project(
    installation: Tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dot path recognizes requirements without inventing project packaging."""
    root, wheels = installation
    wheel(wheels)
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("sample==1.0\n")
    monkeypatch.chdir(project)
    assert invoke(wheels, ".") == 0
    assert (root / "tools/lib/python/sample").exists()


def test_dist_only_project(installation: Tuple[Path, Path], tmp_path: Path) -> None:
    """Custom-only projects reuse dist targets without creating third-party roots."""
    root, wheels = installation
    project = tmp_path / "custom-project"
    project.mkdir()
    configure_dist(project)
    assert invoke(wheels, str(project), "--dryrun") == 0
    assert not root.exists()
    assert invoke(wheels, str(project)) == 0
    assert (root / "lib/custom/main.py").read_text() == "VALUE = 42\n"
    assert not (root / "tools").exists()


def test_project_preview_matches_dist_output(
    installation: Tuple[Path, Path], tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    """Project previews preserve dist's relative paths and dirty-config warning.

    :param installation: Isolated deployment root and offline wheel directory.
    :param tmp_path: Temporary directory containing the Git fixture project.
    :param capfd: Capture of parent and subprocess output streams.
    :return: None.
    """
    from distman.install.projects import project_environment

    root, wheels = installation
    project = tmp_path / "custom-project"
    project.mkdir()
    configure_dist(project)
    for arguments in (
        ["init"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "Initial"],
    ):
        subprocess.run(["git", *arguments], cwd=project, check=True, capture_output=True)
    config = project / "dist.json"
    config.write_text(config.read_text() + "\n")
    direct = subprocess.run(
        [sys.executable, "-c", "from distman.dist import main; raise SystemExit(main())", "-df"],
        cwd=project,
        env=project_environment({}),
        capture_output=True,
        text=True,
        check=True,
    )
    assert invoke(wheels, str(project), "-df") == 0
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert "Uncommitted changes in dist.json" in output
    assert "Updated: custom =d>" in output
    for line in (direct.stdout + direct.stderr).splitlines():
        assert line in output
    assert not root.exists()


def test_combined_project_avoids_duplicate_publication(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """Combined projects publish dependencies once and leave project ownership to dist."""
    root, wheels = installation
    wheel(wheels, "dependency")
    wheel(wheels, "extra-dependency")
    project = make_project(tmp_path / "project", requires=["dependency==1.0"])
    (project / "requirements.txt").write_text("extra-dependency==1.0\n")
    configure_dist(project)
    assert invoke(wheels, str(project)) == 0
    assert (root / "tools/lib/python/dependency").exists()
    assert (root / "tools/lib/python/extra_dependency").exists()
    assert not (root / "tools/lib/python/local_project").exists()
    assert (root / "lib/custom/main.py").exists()


def test_tool_only_pyproject_is_not_packaging(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """Tool settings alone are not sent to pip as an installable Python project."""
    root, wheels = installation
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.black]\nline-length=100\n")
    assert invoke(wheels, str(project)) == 1
    assert not root.exists()
    configure_dist(project)
    assert invoke(wheels, str(project)) == 0


def test_project_preview_does_not_run_backend_in_source(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """Dry-run builds occur on a temporary copy and do not alter the selected source."""
    root, wheels = installation
    project = make_project(tmp_path / "project")
    assert invoke(wheels, str(project), "--dryrun") == 0
    assert not (project / "backend-ran").exists()
    assert not root.exists()


@pytest.mark.parametrize("absolute", [False, True])
def test_project_preview_skips_dangling_links(
    installation: Tuple[Path, Path], tmp_path: Path, absolute: bool
) -> None:
    """Broken links in temporary project directories do not prevent a preview.

    :param installation: Isolated deployment root and offline wheel directory.
    :param tmp_path: Temporary directory containing the fixture project.
    :param absolute: Whether the dangling link uses an absolute target path.
    :return: None.
    """
    root, wheels = installation
    project = make_project(tmp_path / "project")
    temporary = project / "tmp" / "link_test_repo"
    temporary.mkdir(parents=True)
    target = tmp_path / "missing" if absolute else Path("missing")
    link = temporary / "envstack"
    link.symlink_to(target, target_is_directory=True)

    assert invoke(wheels, str(project), "--dryrun") == 0
    assert link.is_symlink()
    assert not (project / "backend-ran").exists()
    assert not root.exists()


def test_project_preview_copies_valid_relative_links(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """Valid linked inputs remain usable without exposing originals to backend writes.

    :param installation: Isolated deployment root and offline wheel directory.
    :param tmp_path: Temporary directory containing the project and linked input.
    :return: None.
    """
    root, wheels = installation
    project = make_project(tmp_path / "project")
    original = tmp_path / "build-input.txt"
    original.write_text("original")
    (project / "linked-input.txt").symlink_to(Path("..") / original.name)
    backend = project / "backend.py"
    backend.write_text(
        backend.read_text()
        + "linked = Path(__file__).with_name('linked-input.txt')\n"
        + "assert linked.read_text() in ('original', 'changed by backend')\n"
        + "assert not linked.is_symlink()\n"
        + "linked.write_text('changed by backend')\n"
    )

    assert invoke(wheels, str(project), "--dryrun") == 0
    assert original.read_text() == "original"
    assert (project / "linked-input.txt").is_symlink()
    assert not (project / "backend-ran").exists()
    assert not root.exists()


def test_project_requires_confirmation(
    installation: Tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Without --yes, decline prevents publication after the effective root is displayed."""
    root, wheels = installation
    project = tmp_path / "project"
    project.mkdir()
    configure_dist(project)
    prompts = []

    def decline(prompt: str) -> bool:
        """Record the proposed write destination and decline the activation."""
        prompts.append(prompt)
        return False

    monkeypatch.setattr(util, "yesNo", decline)
    assert cli.main(["install", str(project), "--no-index", "--find-links", str(wheels)]) == 1
    assert str(root) in prompts[0]
    assert "Effective deployment root:" in capsys.readouterr().out
    assert not (root / "lib/custom").exists()


def test_project_discovery_is_exact_and_bare_names_remain_requirements(
    installation: Tuple[Path, Path],
    tmp_path: Path,
) -> None:
    """A same-name directory does not shadow PyPI, and ancestors are not inspected."""
    root, wheels = installation
    wheel(wheels)
    (tmp_path / "sample").mkdir()
    assert invoke(wheels, "sample") == 0
    configure_dist(tmp_path)
    assert invoke(wheels, str(tmp_path / "sample")) == 1
    assert not (root / "lib/custom").exists()


def test_combined_project_rejects_overlapping_destinations(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """A dist.json target cannot replace a composed third-party package root."""
    root, wheels = installation
    wheel(wheels, "dependency")
    project = make_project(tmp_path / "project", requires=["dependency==1.0"])
    configure_dist(project)
    (project / "dist.json").write_text(
        json.dumps(
            {
                "targets": {
                    "custom": {"source": "custom", "destination": "{DEPLOY_ROOT}/tools/lib/python"}
                }
            }
        )
    )
    assert invoke(wheels, str(project)) == 1
    assert not root.exists()


def test_invalid_custom_targets_block_dependency_publication(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """Legacy target preflight must pass before any resolved dependency is published."""
    root, wheels = installation
    wheel(wheels)
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("sample\n")
    (project / "dist.json").write_text(
        json.dumps(
            {
                "targets": {
                    "missing": {
                        "source": "does-not-exist",
                        "destination": "{DEPLOY_ROOT}/lib/missing",
                    }
                }
            }
        )
    )
    assert invoke(wheels, str(project)) == 1
    assert not (root / "tools/lib/python").exists()
