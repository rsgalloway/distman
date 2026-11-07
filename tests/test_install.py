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
from distman.metadata import Store  # noqa: E402


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


def set_compatibility(root: Path, name: str, label: str, version: str) -> None:
    """Update fixture compatibility across centralized target records.

    :param root: Deployment root containing central metadata.
    :param name: Distribution whose records should change.
    :param label: Runtime label to update.
    :param version: Full Python version to record.
    :return: None.
    """
    store = Store(root)
    for reference in store.selections().values():
        if reference is None:
            continue
        state = store.read(reference)
        changed = False
        for artifact in state.get("state", {}).get("artifacts", {}).values():
            if artifact["name"] == name:
                artifact["compatibility"][label] = version
                changed = True
        if changed:
            store.write(reference, state)


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
    assert target.is_dir() and not target.is_symlink()
    assert (target / "sample").is_symlink()
    assert (target / "sample/__init__.py").read_bytes() == b"value = 42\r\n"
    assert (target / "share/sample.txt").read_bytes() == b"example\r\n"
    provided_script = root / "tools/bin/sample-tool"
    assert provided_script.exists()
    if os.name != "nt":
        process = subprocess.run([str(provided_script)], capture_output=True, text=True)
        assert process.returncode == 0, process.stderr
        assert process.stdout.strip() == "ok"
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
    first = os.readlink(target / "sample")
    assert invoke(wheels, "sample==1.0") == 0
    assert os.readlink(target / "sample") == first
    assert len(util.get_file_versions(str(target / "sample"))) == 1
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
    previous = os.readlink(target / "space/dep.py")
    assert invoke(wheels, "dep==2") == 1
    assert os.readlink(target / "space/dep.py") == previous
    assert read_state(target)["artifacts"]["dep"]["version"] == "1.0"


def test_collision_and_tampering_fail_closed(installation: Tuple[Path, Path]) -> None:
    """Refuse conflicting ownership and modified installed content, even with force."""
    root, wheels = installation
    wheel(wheels, "one", files={"shared.py": b"1\n"})
    wheel(wheels, "two", files={"shared.py": b"2\n"})
    assert invoke(wheels, "one") == 0
    target = root / "tools/lib/python"
    original = os.readlink(target / "shared.py")
    assert invoke(wheels, "two", "--force") == 1
    assert os.readlink(target / "shared.py") == original
    (target / "shared.py").write_bytes(b"changed")
    assert invoke(wheels, "one", "--force") == 1
    assert os.readlink(target / "shared.py") == original


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
    assert len(util.get_file_versions(str(target / "Some_Package"))) == 1
    assert target.is_dir() and not target.is_symlink()
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
        "  TOOLS_ROOT: ${DEPLOY_ROOT}/third-party\n"
        "  DISTMAN_SHARED_LIB_PATH: '{TOOLS_ROOT}/imports'\n"
    )
    policy = Policy()
    assert policy.values["ENV"] == "dev"
    assert policy.deploy_root == root
    wheel(wheels)
    assert invoke(wheels, "sample") == 0
    assert (root / "third-party/imports/sample").exists()
    assert os.environ["ENV"] == "dev"


@pytest.mark.parametrize("custom_target", [False, True])
def test_runtime_library_path_inherits_target(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, custom_target: bool
) -> None:
    """Expand the target variable before formatting artifact and runtime tokens.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture for an active target-template override.
    :param custom_target: Whether to override the default publication boundary.
    :return: None.
    """
    root, wheels = installation
    if custom_target:
        monkeypatch.setenv("DISTMAN_TARGET_PATH", "{TOOLS_ROOT}/custom/{version}/{package}")
    target = expand_targets("windows", "x86_64", "3.11")[0]
    artifact = inspect_wheel(wheel(wheels, tag="cp311-cp311-win_amd64"), target)
    policy = Policy()
    boundary = root / "tools" / ("custom/1.0/sample" if custom_target else "sample/1.0")
    assert policy.path("DISTMAN_TARGET_PATH", artifact, target) == boundary
    assert policy.path("DISTMAN_RUNTIME_LIB_PATH", artifact, target) == (
        boundary / "windows/x86_64/python3.11/lib/python"
    )


def test_standalone_fallback(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Use a writable standalone root when studio configuration is absent."""
    monkeypatch.delenv("DEPLOY_ROOT")
    monkeypatch.delenv("ENV")
    monkeypatch.setenv("HOME", str(tmp_path))
    policy = Policy()
    assert policy.deploy_root == tmp_path / ".local/distman/prod"


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_missing_wheel(
    installation: Tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    host_architecture: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """Reject an unavailable wheel for the explicitly selected runtime.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture overriding the host architecture.
    :param host_architecture: Simulated host default for explicit target selection.
    :param capsys: Captured CLI diagnostics.
    :return: None.
    """
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    root, wheels = installation
    wheel(wheels, tag="cp311-cp311-win_amd64")
    assert (
        invoke(wheels, "sample", "--platform", "linux", "--arch", "x86_64", "--python", "3.11") == 1
    )
    assert "Wheel resolution failed for linux-x86_64-cp311" in capsys.readouterr().out
    assert list(root.iterdir()) == [root / ".distman"]
    assert not (root / ".distman/locks/write").exists()


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
@pytest.mark.parametrize("target_platform", ["linux", "windows", "darwin"])
def test_unmanaged_shared_destination(
    installation: Tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    host_architecture: str,
    target_platform: str,
    capsys: pytest.CaptureFixture,
) -> None:
    """Refuse an unmanaged shared library directory even when force is requested.

    :param installation: Isolated deployment root and wheelhouse containing only a pure wheel.
    :param monkeypatch: Fixture overriding the host architecture.
    :param host_architecture: Simulated host default for explicit target selection.
    :param target_platform: Platform to target independently of the host.
    :param capsys: Captured CLI diagnostics.
    :return: None.
    """
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    root, wheels = installation
    wheel(wheels)
    target = root / "tools/lib/python"
    target.mkdir(parents=True)
    (target / "unowned.py").write_text("keep me")
    assert (
        invoke(
            wheels,
            "sample",
            "--force",
            "--platform",
            target_platform,
            "--arch",
            "x86_64",
            "--python",
            "3.11",
        )
        == 0
    )
    assert "Install failed" not in capsys.readouterr().out
    assert target.is_dir() and not target.is_symlink()
    assert (target / "unowned.py").read_text() == "keep me"
    assert (target / "sample").is_symlink()


def test_rejects_target_beneath_existing_managed_boundary(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Reject a new target nested beneath an unrelated managed symlink.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture configuring the unsafe nested binary layout.
    :param capsys: Captured overlap diagnostic.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels, "shared")
    assert invoke(wheels, "shared") == 0
    shared = root / "tools/lib/python"
    revision = shared.resolve()
    nested = "{TOOLS_ROOT}/lib/python/native/{package}/{version}"
    runtime = nested + "/{platform}/{arch}/python{python_version}"
    monkeypatch.setenv("DISTMAN_TARGET_PATH", nested)
    monkeypatch.setenv("DISTMAN_RUNTIME_LIB_PATH", runtime + "/lib/python")
    monkeypatch.setenv("DISTMAN_RUNTIME_BIN_PATH", runtime + "/bin")
    wheel(wheels, "native", tag="cp311-cp311-manylinux_2_17_x86_64")
    capsys.readouterr()
    target = ("--platform", "linux", "--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "native", *target) == 0
    assert "Install failed" not in capsys.readouterr().out
    assert shared.resolve() == revision
    assert (shared / "native/native/1.0").is_symlink()


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
    before = os.readlink(target / "sample")
    assert invoke(wheels, "sample==2.0", "--python", "3.12") == 1
    assert os.readlink(target / "sample") == before


def test_binary_flat_root_rejects_incompatible_runtime_replacement(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Reject a binary wheel that would replace bytes retained for another runtime.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture configuring a deliberately shared binary boundary.
    :param capsys: Captured compatibility diagnostics.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setenv("DISTMAN_TARGET_PATH", "{TOOLS_ROOT}/flat")
    monkeypatch.setenv("DISTMAN_RUNTIME_LIB_PATH", "{TOOLS_ROOT}/flat/lib/python")
    monkeypatch.setenv("DISTMAN_RUNTIME_BIN_PATH", "{TOOLS_ROOT}/flat/bin")
    wheel(wheels, tag="cp311-cp311-manylinux_2_17_x86_64")
    wheel(wheels, tag="cp312-cp312-manylinux_2_17_x86_64")
    common = ("--platform", "linux", "--arch", "x86_64")
    assert invoke(wheels, "sample", *common, "--python", "3.11") == 0
    boundary = root / "tools/flat"
    before = boundary.resolve()
    capsys.readouterr()
    assert invoke(wheels, "sample", *common, "--python", "3.12") == 1
    assert "sample is incompatible with linux-x86_64-cp311" in capsys.readouterr().out
    assert boundary.resolve() == before
    assert read_state(boundary)["artifacts"]["sample@lib/python"]["targets"] == [
        "linux-x86_64-cp311"
    ]


def test_cross_target_marker_omission_fails_closed(installation: Tuple[Path, Path]) -> None:
    """Detect foreign-platform dependencies omitted by host marker resolution."""
    root, wheels = installation
    wheel(wheels, "app", requires=['missing-windows-dep; sys_platform == "win32"'])
    assert invoke(wheels, "app", "--platform", "windows", "--python", "3.11") == 1
    assert not (root / "tools/lib/python").exists()


def command_wheel(
    directory: Path,
    name: str = "sample",
    version: str = "1.0",
    command: Optional[str] = "sample-cli",
    tag: str = "py3-none-any",
    entrypoint: Optional[str] = None,
) -> Path:
    """Create a wheel with an optional console entrypoint for publication tests.

    :param directory: Offline wheelhouse receiving the archive.
    :param name: Distribution and import module name.
    :param version: Upstream wheel version.
    :param command: Generated command name, or None to remove commands on upgrade.
    :param tag: Wheel compatibility tag.
    :param entrypoint: Optional import target used to make generated launcher bytes differ.
    :return: Created wheel archive.
    """
    files = {f"{name}.py": f"def main():\n    print({version!r})\n".encode()}
    if command:
        files[f"{name}-{version}.dist-info/entry_points.txt"] = (
            f"[console_scripts]\n{command} = {entrypoint or f'{name}:main'}\n"
        ).encode()
    return wheel(directory, name, version, tag, files=files)


def test_shared_commands_upgrade_remove_and_preserve(installation: Tuple[Path, Path]) -> None:
    """Upgrade both roots, remove obsolete commands, and retain unrelated commands.

    :param installation: Isolated deployment root and offline wheel directory.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    command_wheel(wheels, "other", command="other-cli")
    assert invoke(wheels, "sample", "other") == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    initial_sample = os.readlink(library / "sample.py")
    initial_other = os.readlink(scripts / "other-cli")
    command_wheel(wheels, version="2.0", command=None)
    assert invoke(wheels, "sample==2.0") == 0
    assert not list(scripts.glob("sample-cli*"))
    assert list(scripts.glob("other-cli*"))
    assert os.readlink(library / "sample.py") != initial_sample
    assert os.readlink(scripts / "other-cli") == initial_other
    updated = os.readlink(library / "sample.py")
    assert invoke(wheels, "sample==2.0") == 0
    assert os.readlink(library / "sample.py") == updated


def test_shared_command_collision_and_unmanaged_bin(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject command collisions and existing unmanaged directories before publishing.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture overriding the scripts destination.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    initial = (os.readlink(library / "sample.py"), os.readlink(scripts / "sample-cli"))
    command_wheel(wheels, "other", command="sample-cli")
    assert invoke(wheels, "other") == 1
    assert (os.readlink(library / "sample.py"), os.readlink(scripts / "sample-cli")) == initial
    unmanaged = root / "legacy-bin"
    unmanaged.mkdir()
    (unmanaged / "existing-tool").write_text("preserve")
    monkeypatch.setenv("DISTMAN_SHARED_BIN_PATH", str(unmanaged))
    assert invoke(wheels, "other") == 0
    assert (unmanaged / "existing-tool").read_text() == "preserve"
    assert (unmanaged / "sample-cli").is_symlink()
    assert (os.readlink(library / "sample.py"), os.readlink(scripts / "sample-cli")) == initial


def test_script_activation_failure_restores_libraries(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed bin activation restores the preceding library activation too.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture injecting one script-root activation failure.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    library_target, script_target = library / "sample.py", scripts / "sample-cli"
    initial = (os.readlink(library_target), os.readlink(script_target))
    command_wheel(wheels, version="2.0", entrypoint="sample:main2")
    replace = deployment.replace_link

    def fail_bin(destination: Path, target: Path) -> None:
        """Fail only the scripts activation, allowing library restoration.

        :param destination: Active link being replaced.
        :param target: Proposed revision link target.
        :return: None.
        :raises OSError: For the scripts destination.
        """
        if Path(destination) == script_target:
            raise OSError("script activation failed")
        replace(destination, target)

    monkeypatch.setattr(deployment, "replace_link", fail_bin)
    assert invoke(wheels, "sample==2.0") == 1
    assert (os.readlink(library_target), os.readlink(script_target)) == initial


def test_custom_shared_bin_record_paths(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """External scripts retain valid RECORD paths and checksums through active links.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture overriding the shared scripts template.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setenv("DISTMAN_SHARED_BIN_PATH", "{DEPLOY_ROOT}/commands")
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library = root / "tools/lib/python"
    assert not (library / "bin").exists()
    records = list(csv.reader((library / "sample-1.0.dist-info/RECORD").read_text().splitlines()))
    commands = [row for row in records if "sample-cli" in row[0]]
    assert len(commands) == 1
    name, checksum, size = commands[0]
    path = library / name
    assert path.parent == root / "commands"
    content = path.read_bytes()
    assert int(size) == len(content)
    assert checksum == "sha256=" + base64.urlsafe_b64encode(
        hashlib.sha256(content).digest()
    ).decode().rstrip("=")


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_runtime_commands_stay_with_binary_revisions(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, host_architecture: str
) -> None:
    """Runtime bins and libraries share one package boundary and preserve other runtimes.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture overriding the runtime bin suffix.
    :param host_architecture: Simulated host default; fixture wheels remain Windows x86-64.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    monkeypatch.setenv(
        "DISTMAN_RUNTIME_BIN_PATH",
        "${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/commands",
    )
    for version in ("3.11", "3.12"):
        tag = "cp" + version.replace(".", "")
        command_wheel(wheels, tag=f"{tag}-{tag}-win_amd64")
        assert (
            invoke(
                wheels, "sample", "--platform", "windows", "--arch", "x86_64", "--python", version
            )
            == 0
        )
    boundary = root / "tools/sample/1.0"
    for version in ("3.11", "3.12"):
        runtime = boundary / "windows/x86_64" / f"python{version}"
        assert (runtime / "commands/sample-cli.exe").read_bytes().startswith(b"MZ")
        assert (runtime / "lib/python/sample.py").exists()
        assert not (runtime / "lib/python/bin").exists()
        records = list(
            csv.reader(
                (runtime / "lib/python/sample-1.0.dist-info/RECORD").read_text().splitlines()
            )
        )
        script_path = next(row[0] for row in records if "sample-cli" in row[0])
        assert (runtime / "lib/python" / script_path).read_bytes().startswith(b"MZ")


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_custom_runtime_bin_retains_other_package_libraries(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, host_architecture: str
) -> None:
    """A common runtime bin validates companion libraries outside the requested package.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture selecting a common scripts root.
    :param host_architecture: Simulated host default; fixture wheels remain Windows x86-64.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    monkeypatch.setenv("DISTMAN_RUNTIME_BIN_PATH", "{TOOLS_ROOT}/commands")
    command_wheel(wheels, tag="cp311-cp311-win_amd64")
    command_wheel(wheels, name="other", command="other-cli", tag="cp311-cp311-win_amd64")
    options = ("--platform", "windows", "--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "sample", *options) == 0
    sample_root = root / "tools/sample/1.0"
    original = os.readlink(sample_root)
    assert invoke(wheels, "other", *options) == 0
    assert os.readlink(sample_root) == original
    assert (root / "tools/commands/sample-cli.exe").exists()
    assert (root / "tools/commands/other-cli.exe").exists()


def test_mismatched_shared_revisions_fail_before_publication(
    installation: Tuple[Path, Path]
) -> None:
    """Detect an independently rolled-back bin before installing another package.

    :param installation: Isolated deployment root and offline wheel directory.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample==1.0") == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    library_target, script_target = library / "sample.py", scripts / "sample-cli"
    old_bin = os.readlink(script_target)
    command_wheel(wheels, version="2.0", entrypoint="sample:main2")
    assert invoke(wheels, "sample==2.0") == 0
    deployment.replace_link(script_target, old_bin)
    before = (os.readlink(library_target), os.readlink(script_target))
    command_wheel(wheels, name="other", command="other-cli")
    assert invoke(wheels, "other") == 1
    assert (os.readlink(library_target), os.readlink(script_target)) == before


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
    script = root / "tools/bin" / ("sample-cli.exe" if os.name == "nt" else "sample-cli")
    assert script.exists()
    command = [str(script)]
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

    (source / "one").write_bytes(b"updated")
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
    previous = os.readlink(target / "local_project")
    assert invoke(wheels, str(project)) == 0
    assert os.readlink(target / "local_project") == previous


def test_packaging_project_installs_entrypoints_without_dist_json(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """A locally built wheel receives ordinary library and bin policy automatically.

    :param installation: Isolated deployment root and offline wheel directory.
    :param tmp_path: Directory receiving the local packaging project.
    :return: None.
    """
    root, wheels = installation
    project = make_project(tmp_path / "project")
    wheel(
        project,
        "local-project",
        files={
            "local_project.py": b"def main():\n    print('local-command')\n",
            "local_project-1.0.dist-info/entry_points.txt": (
                b"[console_scripts]\n" b"local-cli = local_project:main\n"
            ),
        },
    )
    assert not (project / "dist.json").exists()
    assert invoke(wheels, str(project)) == 0
    command = root / "tools/bin" / ("local-cli.exe" if os.name == "nt" else "local-cli")
    process = subprocess.run(
        [str(command)],
        env=dict(os.environ, PYTHONPATH=str(root / "tools/lib/python")),
        text=True,
        capture_output=True,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "local-command"


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


def test_dist_only_project(
    installation: Tuple[Path, Path], tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    """Dist-only installs distinguish explicit previews from internal validation.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param tmp_path: Directory containing the custom project.
    :param capfd: Capture of parent and child process output.
    :return: None.
    """
    root, wheels = installation
    project = tmp_path / "custom-project"
    project.mkdir()
    configure_dist(project)
    assert invoke(wheels, str(project), "--dryrun") == 0
    assert not root.exists()
    preview = capfd.readouterr()
    assert "NOTICE: Dry run" in preview.out + preview.err
    assert invoke(wheels, str(project)) == 0
    installed = capfd.readouterr()
    output = installed.out + installed.err
    assert "NOTICE: Dry run" not in output
    assert output.count("Updated: custom =d>") == 1
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
        [
            sys.executable,
            "-c",
            "from distman.dist.command import main; raise SystemExit(main())",
            "-df",
        ],
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
    assert list(root.iterdir()) == [root / ".distman"]
    assert not (root / ".distman/locks/write").exists()


def test_invalid_custom_targets_block_dependency_publication(
    installation: Tuple[Path, Path], tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    """Failed validation retains diagnostics and prevents dependency publication.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param tmp_path: Directory containing the invalid custom project.
    :param capfd: Capture of parent and child process output.
    :return: None.
    """
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
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert "Project validation failed (dry-run diagnostics):" in output
    assert "does-not-exist" in output
    assert "Local dist.json preflight failed; no packages were published" in output


@pytest.mark.parametrize("custom", [False, True])
def test_unrelated_install_retains_runtime_dependencies(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, custom: bool
) -> None:
    """Validate transitive installed runtime dependencies without republishing them.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture for selecting a custom deployment template.
    :param custom: Whether version lookup must respect a custom path layout.
    :return: None.
    """
    root, wheels = installation
    if custom:
        monkeypatch.setenv(
            "DISTMAN_TARGET_PATH", "{TOOLS_ROOT}/packages[local]/{version}/{package}"
        )
    tag = "cp311-cp311-manylinux_2_17_x86_64"
    wheel(wheels, "native", tag=tag, requires=["support>=1"])
    wheel(wheels, "support", tag=tag)
    wheel(wheels, "app", requires=["native>=1"])
    wheel(wheels, "unrelated")
    args = ("--platform", "linux", "--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "app", *args) == 0
    boundaries = [
        root / (f"tools/packages[local]/1.0/{name}" if custom else f"tools/{name}/1.0")
        for name in ("native", "support")
    ]
    original = [boundary.resolve() for boundary in boundaries]
    assert invoke(wheels, "unrelated", *args, "--force") == 0
    assert [boundary.resolve() for boundary in boundaries] == original
    assert (root / "tools/lib/python/unrelated").is_dir()


def test_dependency_discovery_preserves_retained_python_patch(
    installation: Tuple[Path, Path]
) -> None:
    """Use recorded patch versions when evaluating retained dependency markers.

    :param installation: Isolated deployment root and offline wheelhouse.
    :return: None.
    """
    root, wheels = installation
    tag = "cp311-cp311-manylinux_2_17_x86_64"
    target = ("--platform", "linux", "--arch", "x86_64", "--python", "3.11")
    wheel(wheels, "binarydep", tag=tag)
    assert invoke(wheels, "binarydep", *target) == 0
    dependency = root / "tools/binarydep/1.0"
    dependency_revision = dependency.resolve()

    wheel(wheels, "shared")
    wheel(
        wheels,
        "app",
        tag=tag,
        requires=["shared", 'binarydep; python_full_version >= "3.11.5"'],
    )
    assert invoke(wheels, "app", *target) == 0
    set_compatibility(root, "app", "linux-x86_64-cp311", "3.11.9")

    wheel(wheels, "shared", version="2.0")
    foreign = ("--platform", "linux", "--arch", "x86_64", "--python", "3.12")
    assert invoke(wheels, "shared==2.0", *foreign) == 0
    assert dependency.resolve() == dependency_revision
    shared = read_state(root / "tools/lib/python")["artifacts"]["shared"]
    assert shared["version"] == "2.0"


@pytest.mark.parametrize("failure", ["missing", "incompatible", "ambiguous"])
def test_retained_runtime_dependency_failures_do_not_publish(
    installation: Tuple[Path, Path], failure: str, capsys: pytest.CaptureFixture
) -> None:
    """Do not hide missing, conflicting, or ambiguous retained dependencies.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param failure: Retained dependency condition to exercise.
    :param capsys: Captured CLI diagnostics.
    :return: None.
    """
    root, wheels = installation
    tag = "cp311-cp311-manylinux_2_17_x86_64"
    wheel(wheels, "native", tag=tag)
    wheel(wheels, "app", requires=["native<2"])
    wheel(wheels, "unrelated")
    args = ("--platform", "linux", "--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "app", *args) == 0
    requested = ["unrelated"]
    if failure == "missing":
        (root / "tools/native/1.0").unlink()
    elif failure == "incompatible":
        wheel(wheels, "native", version="2.0", tag=tag)
        requested.append("native==2.0")
    else:
        wheel(wheels, "native", version="1.1", tag=tag)
        assert invoke(wheels, "native==1.1", *args) == 0
    shared = root / "tools/lib/python"
    original = shared.resolve()
    capsys.readouterr()
    result = invoke(wheels, *requested, *args)
    if failure == "missing":
        assert result == 1
        assert not (shared / "unrelated").exists()
    else:
        assert result == 0
        assert (shared / "unrelated").exists()
    assert shared.resolve() == original
    output = capsys.readouterr().out
    if failure == "missing":
        assert "Recorded target is missing" in output
    else:
        assert "Install failed" not in output


@pytest.mark.parametrize("platforms", [("all",), ("linux", "windows"), ("windows", "linux")])
def test_shared_multiplatform_launchers(
    installation: Tuple[Path, Path], platforms: Tuple[str, ...]
) -> None:
    """Preserve and upgrade both launcher forms with one shared library revision.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param platforms: Target selection order, including a combined installation.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    args = ("--arch", "x86_64", "--python", "3.11")
    for platform in platforms:
        assert invoke(wheels, "sample", "--platform", platform, *args) == 0
    library = root / "tools/lib/python"
    scripts = root / "tools/bin"
    assert (scripts / "sample-cli").read_bytes().startswith(b"#!/usr/bin/env python3\n")
    assert (scripts / "sample-cli.exe").read_bytes().startswith(b"MZ")
    entries = list(csv.reader((library / "sample-1.0.dist-info/RECORD").read_text().splitlines()))
    for script in ("sample-cli", "sample-cli.exe"):
        entry = next(row for row in entries if row[0].endswith("/" + script))
        assert Path(entry[0]).read_bytes() == (scripts / script).read_bytes()
    before = (library.resolve(), scripts.resolve())
    assert invoke(wheels, "sample", "--platform", platforms[-1], *args) == 0
    assert (library.resolve(), scripts.resolve()) == before
    command_wheel(wheels, version="2.0", command="renamed-cli")
    assert invoke(wheels, "sample==2.0", "--platform", "linux", *args) == 0
    assert not (scripts / "sample-cli").exists()
    assert not (scripts / "sample-cli.exe").exists()
    assert (scripts / "renamed-cli").exists()
    assert (scripts / "renamed-cli.exe").exists()
    command_wheel(wheels, version="3.0", command=None)
    assert invoke(wheels, "sample==3.0", "--platform", "windows", *args) == 0
    assert not (scripts / "renamed-cli").exists()
    assert not (scripts / "renamed-cli.exe").exists()


def test_foreign_install_does_not_expand_unrelated_application_targets(
    installation: Tuple[Path, Path], capsys: pytest.CaptureFixture
) -> None:
    """Keep Linux application dependencies scoped while adding Windows commands.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param capsys: Captured dependency diagnostics.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels, "native", tag="cp311-cp311-manylinux_2_17_x86_64")
    wheel(wheels, "app", requires=["native>=1", "shared<2"])
    wheel(wheels, "shared")
    command_wheel(wheels)
    args = ("--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "app", "sample", "--platform", "linux", *args) == 0
    runtime = (root / "tools/native/1.0").resolve()
    assert invoke(wheels, "sample", "--platform", "windows", *args) == 0
    assert (root / "tools/native/1.0").resolve() == runtime
    state = read_state(root / "tools/lib/python")
    assert state["artifacts"]["app"]["targets"] == ["linux-x86_64-cp311"]
    assert (root / "tools/bin/sample-cli").exists()
    assert (root / "tools/bin/sample-cli.exe").exists()
    wheel(wheels, "shared", version="2.0")
    before = (root / "tools/lib/python").resolve()
    assert invoke(wheels, "shared==2.0", "--platform", "windows", *args) == 1
    assert (root / "tools/lib/python").resolve() == before
    assert "app requires shared<2" in capsys.readouterr().out


def test_multiplatform_gui_launcher_metadata(installation: Tuple[Path, Path]) -> None:
    """Keep GUI launchers valid and recorded alongside their POSIX entrypoint.

    :param installation: Isolated deployment root and offline wheelhouse.
    :return: None.
    """
    root, wheels = installation
    wheel(
        wheels,
        files={
            "sample.py": b"def main():\n    pass\n",
            "sample-1.0.dist-info/entry_points.txt": b"[gui_scripts]\nsample-gui = sample:main\n",
        },
    )
    assert (
        invoke(wheels, "sample", "--platform", "all", "--python", "3.11", "--arch", "x86_64") == 0
    )
    scripts = root / "tools/bin"
    launcher = scripts / "sample-gui.exe"
    with zipfile.ZipFile(launcher) as archive:
        entry = archive.getinfo("__main__.py")
        assert entry.date_time == (1980, 1, 1, 0, 0, 0)
        assert b"from sample import main" in archive.read(entry)
        assert launcher.read_bytes()[: entry.header_offset].endswith(b"#!pythonw.exe\n")
    assert (scripts / "sample-gui").read_bytes().startswith(b"#!/usr/bin/env python3\n")
    records = read_state(scripts)["artifacts"]["sample"]["files"]
    assert set(records) == {"sample-gui", "sample-gui.exe"}


def remove(*args: str) -> int:
    """Run package removal without interactive confirmation.

    :param args: Package selectors and uninstall options.
    :return: CLI exit status.
    """
    return cli.main(["uninstall", "--yes", *args])


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_uninstall_shared_package_history_and_idempotency(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, host_architecture: str
) -> None:
    """Remove only selected ownership while preserving history and unrelated commands.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture overriding the host architecture.
    :param host_architecture: Simulated host default for explicit target selection.
    :return: None.
    """
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    root, wheels = installation
    command_wheel(wheels)
    command_wheel(wheels, name="other", command="other-cli")
    assert (
        invoke(
            wheels, "sample", "other", "--platform", "all", "--arch", "x86_64", "--python", "3.11"
        )
        == 0
    )
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    history = (library / "sample.py").resolve(), (scripts / "sample-cli.exe").resolve()
    active = os.readlink(library / "sample.py"), os.readlink(scripts / "sample-cli.exe")
    assert remove("sample", "--dryrun") == 0
    assert (os.readlink(library / "sample.py"), os.readlink(scripts / "sample-cli.exe")) == active
    assert remove("sample") == 0
    assert not (library / "sample.py").exists()
    assert not (library / "sample-1.0.dist-info").exists()
    assert not (scripts / "sample-cli").exists()
    assert not (scripts / "sample-cli.exe").exists()
    assert (scripts / "other-cli").exists()
    assert (scripts / "other-cli.exe").exists()
    assert history[0].exists()
    assert history[1].exists()
    assert remove("sample") == 0
    assert remove("other") == 0
    assert not read_state(library)["artifacts"]
    assert not read_state(scripts)["artifacts"]
    assert library.is_dir() and scripts.is_dir()
    assert not library.is_symlink() and not scripts.is_symlink()


def test_reinstall_reuses_unlinked_payload_history(installation: Tuple[Path, Path]) -> None:
    """Relink identical retained targets without creating duplicate revisions.

    :param installation: Isolated deployment root and offline wheelhouse.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    targets = [
        library / "sample.py",
        library / "sample-1.0.dist-info",
        scripts / "sample-cli",
    ]
    payloads = [target.resolve() for target in targets]
    assert remove("sample") == 0
    assert all(not target.exists() for target in targets)
    assert invoke(wheels, "sample") == 0
    assert [target.resolve() for target in targets] == payloads
    assert all(len(util.get_file_versions(str(target))) == 1 for target in targets)


@pytest.mark.parametrize("previous_uninstall", [False, True])
def test_uninstall_delete_removes_unlinked_payload_history(
    installation: Tuple[Path, Path], previous_uninstall: bool
) -> None:
    """Delete retained revisions during removal or after a prior unlink operation.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param previous_uninstall: Whether payload deletion follows an earlier normal uninstall.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    payloads = [
        (library / "sample.py").resolve(),
        (library / "sample-1.0.dist-info").resolve(),
        (scripts / "sample-cli").resolve(),
    ]
    assert all(path.exists() for path in payloads)
    if previous_uninstall:
        assert remove("sample") == 0
        assert all(path.exists() for path in payloads)
    assert remove("sample", "--delete") == 0
    assert all(not path.exists() for path in payloads)
    assert not (library / "sample.py").exists()
    assert not (library / "sample-1.0.dist-info").exists()
    assert not (scripts / "sample-cli").exists()
    assert remove("sample", "--delete") == 0


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_uninstall_runtime_selection_and_record(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, host_architecture: str
) -> None:
    """Keep POSIX launchers for retained platforms and update external RECORD paths.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture overriding the host architecture.
    :param host_architecture: Simulated host default for explicit target selection.
    :return: None.
    """
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    root, wheels = installation
    command_wheel(wheels)
    assert (
        invoke(wheels, "sample", "--platform", "all", "--arch", "x86_64", "--python", "3.11,3.12")
        == 0
    )
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    assert remove("sample", "--platform", "linux") == 0
    assert (scripts / "sample-cli").exists()  # macOS still needs it
    assert remove("sample", "--platform", "windows", "--python", "3.11") == 0
    assert (scripts / "sample-cli.exe").exists()  # Windows 3.12 still needs it
    assert remove("sample", "--platform", "windows") == 0
    assert not (scripts / "sample-cli.exe").exists()
    assert (scripts / "sample-cli").exists()
    record = read_state(library)["artifacts"]["sample"]
    assert record["targets"] == ["darwin-x86_64-cp311", "darwin-x86_64-cp312"]
    rows = list(csv.reader((library / "sample-1.0.dist-info/RECORD").read_text().splitlines()))
    assert any(row[0].endswith("sample-cli") for row in rows)
    assert read_state(scripts)["artifacts"]["sample"]["targets"] == record["targets"]
    assert (
        invoke(wheels, "sample", "--platform", "windows", "--arch", "x86_64", "--python", "3.11")
        == 0
    )
    assert (scripts / "sample-cli.exe").exists()


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_uninstall_binary_versions_and_reverse_dependencies(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, host_architecture: str
) -> None:
    """Check consumers in separate runtime roots and preserve unselected versions.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture overriding the host architecture.
    :param host_architecture: Simulated host default for explicit target selection.
    :return: None.
    """
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    root, wheels = installation
    tag = "cp311-cp311-manylinux_2_17_x86_64"
    wheel(wheels, "native", tag=tag)
    wheel(wheels, "app", tag=tag, requires=["native==1.0"])
    wheel(wheels, "native", version="2.0", tag=tag)
    args = ("--platform", "linux", "--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "app", *args) == 0
    assert invoke(wheels, "native==2.0", *args) == 0
    original = (root / "tools/native/1.0").resolve()
    assert remove("native==1.0") == 1
    assert (root / "tools/native/1.0").resolve() == original
    assert remove("app", "native==1.0") == 0
    assert not read_state(root / "tools/native/1.0")["artifacts"]
    assert read_state(root / "tools/native/2.0")["artifacts"]
    assert remove("native") == 0
    assert not read_state(root / "tools/native/2.0")["artifacts"]


def test_uninstall_keeps_dependencies_and_namespace_files(installation: Tuple[Path, Path]) -> None:
    """Keep dependencies and namespace contents owned by other distributions.

    :param installation: Isolated deployment root and offline wheelhouse.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels, "dependency", files={"namespace/shared.py": b"value = 1"})
    wheel(wheels, "app", files={"namespace/app.py": b"value = 2"}, requires=["dependency"])
    assert invoke(wheels, "app") == 0
    assert remove("dependency") == 1
    assert remove("app") == 0
    assert (root / "tools/lib/python/namespace/shared.py").exists()
    assert not (root / "tools/lib/python/namespace/app.py").exists()
    assert read_state(root / "tools/lib/python")["artifacts"]["dependency"]


def test_uninstall_failure_restores_links(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore the prior shared library and command revisions after activation failure.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture replacing the activation primitive.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    launcher = "sample-cli.exe" if os.name == "nt" else "sample-cli"
    active = [library / "sample.py", library / "sample-1.0.dist-info", scripts / launcher]
    original = {target: os.readlink(target) for target in active}
    unlink = Path.unlink
    calls = []

    def fail_once(path: Path, *args: object, **kwargs: object) -> None:
        """Fail the second active-target unlink and allow journal recovery.

        :param path: Filesystem entry being removed.
        :param args: Positional arguments passed to pathlib.
        :param kwargs: Keyword arguments passed to pathlib.
        :return: None.
        :raises OSError: On the second selected target unlink.
        """
        if path in active:
            calls.append(path)
            if len(calls) == 2:
                raise OSError("injected failure")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    assert remove("sample") == 1
    assert {target: os.readlink(target) for target in active} == original


def test_uninstall_scoped_roots_confirmation_and_integrity(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Respect the selected deployment and reject declined or corrupt removals.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture selecting deployment policy and confirmation responses.
    :param tmp_path: Root for the second independently managed environment.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    other = tmp_path / "production"
    monkeypatch.setenv("DEPLOY_ROOT", str(other))
    assert invoke(wheels, "sample") == 0
    monkeypatch.setattr(util, "yesNo", lambda prompt: False)
    assert cli.main(["uninstall", "sample"]) == 1
    assert (other / "tools/lib/python/sample.py").exists()
    assert remove("sample") == 0
    assert (root / "tools/lib/python/sample.py").exists()
    monkeypatch.setenv("DEPLOY_ROOT", str(root))
    (root / "tools/lib/python/sample.py").write_text("changed")
    assert remove("sample") == 1
    launcher = "sample-cli.exe" if os.name == "nt" else "sample-cli"
    assert (root / "tools/bin" / launcher).exists()


@pytest.mark.parametrize(
    "selector", ["sample>=1", "sample[extra]", "sample==1.*", "sample; python_version>'3'"]
)
def test_uninstall_rejects_ambiguous_selectors(
    installation: Tuple[Path, Path], selector: str
) -> None:
    """Reject unsupported selector syntax without creating deployment directories.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param selector: Unsupported requirement expression.
    :return: None.
    """
    root, _ = installation
    assert remove(selector) == 1
    assert not root.exists()


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_uninstall_binary_runtime_preserves_other_variants(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, host_architecture: str
) -> None:
    """Remove one runtime's library and commands without removing another runtime.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture overriding the host architecture.
    :param host_architecture: Simulated host default for explicit target selection.
    :return: None.
    """
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    root, wheels = installation
    for python in ("311", "312"):
        command_wheel(wheels, tag=f"cp{python}-cp{python}-win_amd64")
    assert (
        invoke(
            wheels, "sample", "--platform", "windows", "--arch", "x86_64", "--python", "3.11,3.12"
        )
        == 0
    )
    boundary = root / "tools/sample/1.0"
    assert remove("sample", "--platform", "windows", "--python", "3.11") == 0
    assert not (boundary / "windows/x86_64/python3.11").exists()
    assert (boundary / "windows/x86_64/python3.12/bin/sample-cli.exe").exists()
    assert len(read_state(boundary)["artifacts"]) == 1


def test_uninstall_custom_roots_and_unmanaged_destination(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Discover configured package roots without replacing unmanaged destinations.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture configuring versioned pure library and command roots.
    :param tmp_path: Parent for an unmanaged library destination.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setenv("DISTMAN_SHARED_LIB_PATH", "{TOOLS_ROOT}/custom/{package}/{version}/lib")
    monkeypatch.setenv("DISTMAN_SHARED_BIN_PATH", "{TOOLS_ROOT}/custom/{package}/{version}/bin")
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    assert remove("sample==1.0") == 0
    assert not read_state(root / "tools/custom/sample/1.0/lib")["artifacts"]
    assert not read_state(root / "tools/custom/sample/1.0/bin")["artifacts"]
    foreign = tmp_path / "unmanaged"
    foreign.mkdir()
    (foreign / "keep.py").write_text("unowned")
    monkeypatch.setenv("DISTMAN_SHARED_LIB_PATH", str(foreign))
    assert remove("sample") == 0
    assert (foreign / "keep.py").read_text() == "unowned"


def test_uninstall_honors_writer_locks(installation: Tuple[Path, Path]) -> None:
    """Do not remove packages while another writer owns a managed boundary lock.

    :param installation: Isolated deployment root and offline wheelhouse.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library = root / "tools/lib/python"
    lock = library.with_name(".python.install-lock")
    lock.mkdir()
    assert remove("sample") == 0
    assert not (library / "sample.py").exists()
    assert lock.is_dir()


@pytest.mark.parametrize("host_architecture", ["x86_64", "arm64"])
def test_uninstall_partial_shared_target_protects_dependents(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, host_architecture: str
) -> None:
    """Do not withdraw required target support even when shared files remain elsewhere.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture overriding the host architecture.
    :param host_architecture: Simulated host default for explicit target selection.
    :return: None.
    """
    monkeypatch.setattr("distman.install.targets.host_arch", lambda: host_architecture)
    root, wheels = installation
    command_wheel(wheels)
    wheel(wheels, "app", requires=["sample"])
    assert (
        invoke(wheels, "sample", "--platform", "all", "--arch", "x86_64", "--python", "3.11") == 0
    )
    assert (
        invoke(wheels, "app", "--platform", "windows", "--arch", "x86_64", "--python", "3.11") == 0
    )
    library = root / "tools/lib/python"
    before = library.resolve()
    assert remove("sample", "--platform", "windows") == 1
    assert library.resolve() == before
    assert (root / "tools/bin/sample-cli.exe").exists()
    assert remove("sample", "--platform", "linux") == 0


def test_uninstall_scopes_pure_dependencies_to_runtime_paths(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not use a pure dependency from another runtime root as a replacement.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture configuring runtime-specific shared roots.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setenv(
        "DISTMAN_SHARED_LIB_PATH", "{TOOLS_ROOT}/shared/python{python_version}/lib/python"
    )
    monkeypatch.setenv("DISTMAN_SHARED_BIN_PATH", "{TOOLS_ROOT}/shared/python{python_version}/bin")
    wheel(wheels, "dependency")
    wheel(wheels, "app", requires=["dependency"])
    assert invoke(wheels, "app", "--python", "3.11") == 0
    assert invoke(wheels, "dependency", "--python", "3.12") == 0
    runtime_311 = root / "tools/shared/python3.11/lib/python"
    runtime_312 = root / "tools/shared/python3.12/lib/python"
    before = runtime_311.resolve()
    assert remove("dependency", "--python", "3.11") == 1
    assert runtime_311.resolve() == before
    assert (runtime_311 / "dependency").exists()
    assert (runtime_312 / "dependency").exists()


def test_uninstall_uses_retained_patch_for_dependency_markers(
    installation: Tuple[Path, Path]
) -> None:
    """Keep a dependency required by a retained record at its saved patch level.

    :param installation: Isolated deployment root and offline wheelhouse.
    :return: None.
    """
    root, wheels = installation
    target = ("--platform", "linux", "--arch", "x86_64", "--python", "3.11")
    wheel(wheels, "dependency")
    assert invoke(wheels, "dependency", *target) == 0
    wheel(wheels, "app", requires=['dependency; python_full_version >= "3.11.5"'])
    assert invoke(wheels, "app", *target) == 0
    library = root / "tools/lib/python"
    set_compatibility(root, "app", "linux-x86_64-cp311", "3.11.9")
    revision = os.readlink(library / "app")

    assert remove("dependency", "--python", "3.11") == 1
    assert os.readlink(library / "app") == revision
    assert (library / "dependency").exists()


@pytest.mark.parametrize("dryrun", [False, True])
def test_uninstall_dependency_diagnostic_explains_unchanged_deployment(
    installation: Tuple[Path, Path], capsys: pytest.CaptureFixture, dryrun: bool
) -> None:
    """Explain every dependency blocker without implying planned files were deleted.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param capsys: Captured CLI diagnostics.
    :param dryrun: Whether removal is a preview.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels, "dependency")
    wheel(wheels, "app", requires=["dependency"])
    wheel(wheels, "other", requires=["dependency>=1"])
    assert invoke(wheels, "app", "other") == 0
    library = root / "tools/lib/python"
    before = library.resolve()
    capsys.readouterr()
    assert remove("dependency", *(["--dryrun"] if dryrun else [])) == 1
    output = capsys.readouterr().out
    assert "Planned removal: dependency==1.0" in output
    assert "Removal would break packages that remain installed" in output
    assert "app==1.0 requires dependency" in output
    assert "other==1.0 requires dependency>=1" in output
    assert "removal would withdraw dependency==1.0" in output
    assert "No packages were removed; deployment targets are unchanged" in output
    assert library.resolve() == before
    assert (library / "dependency").exists()


@pytest.mark.parametrize("dryrun", [False, True])
def test_uninstall_force_overrides_only_dependencies(
    installation: Tuple[Path, Path], capsys: pytest.CaptureFixture, dryrun: bool
) -> None:
    """Allow explicit dependency breakage while preserving dependents and preview semantics.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param capsys: Captured force warnings.
    :param dryrun: Whether publication should be suppressed.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels, "dependency")
    wheel(wheels, "app", requires=["dependency"])
    assert invoke(wheels, "app") == 0
    library = root / "tools/lib/python"
    previous = os.readlink(library / "dependency")
    payload = (library / "dependency").resolve()
    capsys.readouterr()
    assert remove("dependency", "--force", *(["--dryrun"] if dryrun else [])) == 0
    output = capsys.readouterr().out
    assert "WARNING: --force allows removal" in output
    assert "app==1.0 requires dependency" in output
    assert "may stop working" in output
    assert (library / "app").exists()
    assert (library / "dependency").exists() == dryrun
    if dryrun:
        assert os.readlink(library / "dependency") == previous
    assert payload.exists()


def test_uninstall_force_still_requires_confirmation_and_integrity(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force cannot authorize publication or bypass changed owned files.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param monkeypatch: Fixture declining the confirmation prompt.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels)
    assert invoke(wheels, "sample") == 0
    library = root / "tools/lib/python"
    before = library.resolve()
    monkeypatch.setattr(util, "yesNo", lambda prompt: False)
    assert cli.main(["uninstall", "sample", "-f"]) == 1
    assert library.resolve() == before
    (library / "sample/__init__.py").write_text("modified")
    assert remove("sample", "--force") == 1
    assert library.resolve() == before


def test_parallel_installs_and_dist_share_writer_lock(
    installation: Tuple[Path, Path], tmp_path: Path
) -> None:
    """Serialize independent CLI processes without losing shared package ownership.

    :param installation: Isolated deployment root and offline wheelhouse.
    :param tmp_path: Workspace for the separate dist target.
    :return: None.
    """
    from distman.locking import deployment_lock

    root, wheels = installation
    wheel(wheels, "first")
    wheel(wheels, "second")
    source = tmp_path / "source.txt"
    source.write_text("distributed\n")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p)
    environment["DISTMAN_LOCK_TIMEOUT"] = "30"
    command = [sys.executable, "-c", "from distman.cli import main; raise SystemExit(main())"]
    arguments = [
        ["install", "first", "--yes", "--no-index", "--find-links", str(wheels)],
        ["install", "second", "--yes", "--no-index", "--find-links", str(wheels)],
        ["dist", "--source", str(source), "--dest", str(root / "bin/tool"), "--yes", "--force"],
    ]
    processes = []
    try:
        with deployment_lock(root):
            for args in arguments:
                process = subprocess.Popen(
                    command + args,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                processes.append(process)
                assert "Waiting for deployment lock" in process.stderr.readline()
        for process in processes:
            output, error = process.communicate(timeout=60)
            assert process.returncode == 0, output + error
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    state = read_state(root / "tools/lib/python")
    assert {record["name"] for record in state["artifacts"].values()} == {"first", "second"}
    assert (root / "bin/tool").read_text() == "distributed\n"
    assert not (root / ".distman/locks/write").exists()


@pytest.mark.parametrize("dryrun", [False, True])
@pytest.mark.parametrize("multiple_versions", [False, True])
def test_shared_upgrade_checks_retained_binary_consumer(
    installation: Tuple[Path, Path],
    dryrun: bool,
    multiple_versions: bool,
    capsys: pytest.CaptureFixture,
) -> None:
    """Reject shared upgrades that break a consumer in an independent runtime root.

    :param installation: Isolated deployment and offline wheels.
    :param dryrun: Whether to preview the proposed upgrade.
    :param multiple_versions: Retain a second app version with a looser requirement.
    :param capsys: Captured dependency diagnostics.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels, "dep")
    wheel(wheels, "app", tag="cp311-cp311-manylinux_2_17_x86_64", requires=["dep<2"])
    options = ("--platform", "linux", "--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "app", *options) == 0
    if multiple_versions:
        wheel(wheels, "app", "2.0", tag="cp311-cp311-manylinux_2_17_x86_64", requires=["dep>=1"])
        assert invoke(wheels, "app==2", *options) == 0
    library = root / "tools/lib/python"
    previous = library.resolve()
    wheel(wheels, "dep", "2.0")
    assert invoke(wheels, "dep==2", *options, *(["--dryrun"] if dryrun else [])) == 1
    assert "app requires dep<2" in capsys.readouterr().out
    assert library.resolve() == previous
    assert read_state(library)["artifacts"]["dep"]["version"] == "1.0"


def test_upgrade_replaces_directory_with_file(installation: Tuple[Path, Path]) -> None:
    """Remove obsolete directories before copying a file into their old location.

    :param installation: Isolated deployment and offline wheels.
    :return: None.
    """
    root, wheels = installation
    wheel(wheels, files={"item/old.txt": b"old"})
    assert invoke(wheels, "sample==1") == 0
    wheel(wheels, version="2.0", files={"item": b"new"})
    assert invoke(wheels, "sample==2") == 1
    library = root / "tools/lib/python"
    assert (library / "item/old.txt").read_bytes() == b"old"
    assert read_state(library)["artifacts"]["sample"]["version"] == "1.0"
    assert remove("sample") == 0


def test_partial_runtime_rollback_rejects_mismatched_pairs(
    installation: Tuple[Path, Path], capsys: pytest.CaptureFixture
) -> None:
    """Reject equal-wheel-hash pairs whose recorded runtime selections differ.

    :param installation: Isolated deployment and offline wheels.
    :param capsys: Captured inconsistency diagnostics.
    :return: None.
    """
    root, wheels = installation
    command_wheel(wheels)
    options = ("--platform", "all", "--arch", "x86_64", "--python", "3.11")
    assert invoke(wheels, "sample", *options) == 0
    library, scripts = root / "tools/lib/python", root / "tools/bin"
    assert remove("sample", "--platform", "windows") == 0
    library_record = read_state(library)["artifacts"]["sample"]
    scripts_record = read_state(scripts)["artifacts"]["sample"]
    assert library_record["targets"] == scripts_record["targets"]
    wheel(wheels, "other")
    assert invoke(wheels, "other", *options) == 0
    assert "Library and scripts revisions disagree" not in capsys.readouterr().out
    assert remove("sample") == 0


def test_native_requires_python_retains_patch_version(installation: Tuple[Path, Path]) -> None:
    """Use the running patch level for acquisition, wheel checks, and shared metadata.

    :param installation: Isolated deployment and offline wheels.
    :return: None.
    """
    from distman.install.ownership import marker_environment

    root, wheels = installation
    full_version = ".".join(map(str, sys.version_info[:3]))
    target = expand_targets()[0]
    assert target.compatibility_python == full_version
    assert target.python == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert marker_environment(target)["python_full_version"] == full_version
    wheel(wheels, python=f">={full_version}")
    assert invoke(wheels, "sample") == 0
    library = root / "tools/lib/python"
    assert (library / "sample").exists()
    record = read_state(library)["artifacts"]["sample"]
    assert record["compatibility"] == {target.label: full_version}

    wheel(wheels, "other")
    foreign = "3.12" if target.python != "3.12" else "3.11"
    assert invoke(wheels, "other", "--python", foreign) == 0
    retained = read_state(library)["artifacts"]["sample"]
    assert retained["compatibility"][target.label] == full_version

    assert not (library / ".distman-install.json").exists()
    recovered = read_state(library)["artifacts"]["sample"]
    assert recovered["compatibility"][target.label] == full_version
