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


def command_wheel(
    directory: Path,
    name: str = "sample",
    version: str = "1.0",
    command: Optional[str] = "sample-cli",
    tag: str = "py3-none-any",
) -> Path:
    """Create a wheel with an optional console entrypoint for publication tests.

    :param directory: Offline wheelhouse receiving the archive.
    :param name: Distribution and import module name.
    :param version: Upstream wheel version.
    :param command: Generated command name, or None to remove commands on upgrade.
    :param tag: Wheel compatibility tag.
    :return: Created wheel archive.
    """
    files = {f"{name}.py": f"def main():\n    print({version!r})\n".encode()}
    if command:
        files[f"{name}-{version}.dist-info/entry_points.txt"] = (
            f"[console_scripts]\n{command} = {name}:main\n"
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
    initial = (os.readlink(library), os.readlink(scripts))
    command_wheel(wheels, version="2.0", command=None)
    assert invoke(wheels, "sample==2.0") == 0
    assert not list(scripts.glob("sample-cli*"))
    assert list(scripts.glob("other-cli*"))
    assert os.readlink(library) != initial[0]
    assert os.readlink(scripts) != initial[1]
    updated = (os.readlink(library), os.readlink(scripts))
    assert invoke(wheels, "sample==2.0") == 0
    assert (os.readlink(library), os.readlink(scripts)) == updated


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
    initial = (os.readlink(library), os.readlink(scripts))
    command_wheel(wheels, "other", command="sample-cli")
    assert invoke(wheels, "other") == 1
    assert (os.readlink(library), os.readlink(scripts)) == initial
    unmanaged = root / "legacy-bin"
    unmanaged.mkdir()
    (unmanaged / "existing-tool").write_text("preserve")
    monkeypatch.setenv("DISTMAN_SHARED_BIN_PATH", str(unmanaged))
    assert invoke(wheels, "other") == 1
    assert (unmanaged / "existing-tool").read_text() == "preserve"
    assert (os.readlink(library), os.readlink(scripts)) == initial


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
    initial = (os.readlink(library), os.readlink(scripts))
    command_wheel(wheels, version="2.0")
    replace = deployment.replace_link

    def fail_bin(destination: Path, target: Path) -> None:
        """Fail only the scripts activation, allowing library restoration.

        :param destination: Active link being replaced.
        :param target: Proposed revision link target.
        :return: None.
        :raises OSError: For the scripts destination.
        """
        if Path(destination) == scripts:
            raise OSError("script activation failed")
        replace(destination, target)

    monkeypatch.setattr(deployment, "replace_link", fail_bin)
    assert invoke(wheels, "sample==2.0") == 1
    assert (os.readlink(library), os.readlink(scripts)) == initial


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


def test_runtime_commands_stay_with_binary_revisions(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime bins and libraries share one package boundary and preserve other runtimes.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture overriding the runtime bin suffix.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setenv(
        "DISTMAN_RUNTIME_BIN_PATH",
        "${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/commands",
    )
    for version in ("3.11", "3.12"):
        tag = "cp" + version.replace(".", "")
        command_wheel(wheels, tag=f"{tag}-{tag}-win_amd64")
        assert invoke(wheels, "sample", "--platform", "windows", "--python", version) == 0
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


def test_custom_runtime_bin_retains_other_package_libraries(
    installation: Tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A common runtime bin validates companion libraries outside the requested package.

    :param installation: Isolated deployment root and offline wheel directory.
    :param monkeypatch: Fixture selecting a common scripts root.
    :return: None.
    """
    root, wheels = installation
    monkeypatch.setenv("DISTMAN_RUNTIME_BIN_PATH", "{TOOLS_ROOT}/commands")
    command_wheel(wheels, tag="cp311-cp311-win_amd64")
    command_wheel(wheels, name="other", command="other-cli", tag="cp311-cp311-win_amd64")
    options = ("--platform", "windows", "--python", "3.11")
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
    old_bin = os.readlink(scripts)
    command_wheel(wheels, version="2.0")
    assert invoke(wheels, "sample==2.0") == 0
    deployment.replace_link(scripts, old_bin)
    before = (os.readlink(library), os.readlink(scripts))
    command_wheel(wheels, name="other", command="other-cli")
    assert invoke(wheels, "other") == 1
    assert (os.readlink(library), os.readlink(scripts)) == before


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
    assert invoke(wheels, *requested, *args) == 1
    assert shared.resolve() == original
    assert not (shared / "unrelated").exists()
    output = capsys.readouterr().out
    assert (
        "Multiple installed versions" if failure == "ambiguous" else "requires native<2"
    ) in output


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
