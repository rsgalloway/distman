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

"""Filesystem and process coverage for shared deployment coordination."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

import pytest

from distman import config
from distman.dist.distribution import Distributor
from distman.inventory import inventory
from distman.locking import LockError, deployment_lock, lock_environment


def child_environment() -> Dict[str, str]:
    """Build an environment that can import the working checkout.

    :return: Subprocess environment with absolute Python search paths.
    """
    env = dict(os.environ)
    env.pop("DISTMAN_PARENT_LOCK", None)
    env["PYTHONPATH"] = os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p)
    return env


def test_waiter_rereads_state_and_independent_root(tmp_path: Path) -> None:
    """Waiting processes must observe the preceding writer's publication.

    :param tmp_path: Isolated filesystem root.
    :return: None.
    """
    root = tmp_path / "deploy"
    code = """
import sys
from pathlib import Path
from distman.locking import deployment_lock
root = Path(sys.argv[1])
with deployment_lock(root, timeout=5):
    assert (root / 'state').read_text() == 'published'
print('acquired')
"""
    with deployment_lock(root):
        with deployment_lock(tmp_path / "other", timeout=0):
            pass
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(root)],
            env=child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert "Waiting for deployment lock" in process.stderr.readline()
            (root / "state").write_text("published")
        except BaseException:
            process.kill()
            process.communicate()
            raise
    output, error = process.communicate(timeout=10)
    assert process.returncode == 0, error
    assert output.strip() == "acquired"
    assert not (root / ".distman/locks/write").exists()


def test_delegated_child_and_exception_release(tmp_path: Path) -> None:
    """Only an explicitly delegated direct child may borrow a held lock.

    :param tmp_path: Isolated deployment root.
    :return: None.
    """
    code = """
import sys
from pathlib import Path
from distman.locking import deployment_lock
with deployment_lock(Path(sys.argv[1]), timeout=0):
    pass
"""
    with pytest.raises(RuntimeError, match="cancel"):
        with deployment_lock(tmp_path):
            env = child_environment()
            blocked = subprocess.run(
                [sys.executable, "-c", code, str(tmp_path)], env=env, capture_output=True, text=True
            )
            assert blocked.returncode != 0
            assert "Deployment lock timeout" in blocked.stderr
            env.update(lock_environment(tmp_path))
            borrowed = subprocess.run(
                [sys.executable, "-c", code, str(tmp_path)], env=env, capture_output=True, text=True
            )
            assert borrowed.returncode == 0, borrowed.stderr
            assert (tmp_path / ".distman/locks/write/owner.json").exists()
            raise RuntimeError("cancel")
    with deployment_lock(tmp_path, timeout=0):
        pass


def test_stale_lock_dryrun_and_invalid_timeout(tmp_path: Path) -> None:
    """Previews do not write and abandoned locks are never removed automatically.

    :param tmp_path: Isolated deployment root.
    :return: None.
    """
    root = tmp_path / "deploy"
    with deployment_lock(root, dryrun=True):
        pass
    assert not root.exists()
    for timeout in (-1, float("nan"), float("inf")):
        with pytest.raises(LockError, match="timeout"):
            with deployment_lock(root, timeout=timeout):
                pass
    lock = root / ".distman/locks/write"
    lock.mkdir(parents=True)
    with pytest.raises(LockError, match="owner information unavailable"):
        with deployment_lock(root, timeout=0):
            pass
    assert lock.is_dir()


@pytest.fixture
def distributor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Distributor:
    """Isolate Git inspection while retaining real distribution filesystem operations.

    :param tmp_path: Isolated source and deployment workspace.
    :param monkeypatch: Fixture for configuration and Git inspection overrides.
    :return: Distributor with one content-matched target.
    """
    root = tmp_path / "deploy"
    monkeypatch.setattr(config, "DEPLOY_ROOT", str(root))
    monkeypatch.setattr(config, "BUILD_DIR", str(tmp_path / "build"))
    instance = Distributor()
    instance.directory = str(tmp_path)
    instance.short_head = "abcdef0"
    instance.root = {
        "targets": {
            "sample": {
                "source": str(tmp_path / "source"),
                "destination": str(root / "sample"),
                "options": {"match": "content"},
            }
        }
    }
    monkeypatch.setattr(instance, "read_git_info", lambda: True)
    monkeypatch.setattr(instance, "git_changed_files", lambda: [])
    monkeypatch.setattr(instance, "is_git_behind", lambda: False)
    return instance


def test_file_revisions_modes_rollback_and_delete(tmp_path: Path, distributor: Distributor) -> None:
    """Preserve normalization, immutable revisions, version-only and link selection.

    :param tmp_path: Isolated source and deployment workspace.
    :param distributor: Distributor using real filesystem operations.
    :return: None.
    """
    source, target = tmp_path / "source", tmp_path / "deploy/sample"
    source.write_bytes(b"first\r\n")
    source.chmod(0o755)
    assert distributor.dist(yes=True)
    first = target.resolve()
    assert target.read_bytes() == b"first\n"
    assert target.stat().st_mode & 0o777 == source.stat().st_mode & 0o777
    source.write_text("first\n")
    assert distributor.dist(yes=True)
    assert target.resolve() == first
    source.write_text("second\n")
    assert distributor.dist(yes=True, versiononly=True)
    assert target.resolve() == first
    assert distributor.reset_file_version()
    second = target.resolve()
    assert second != first
    assert distributor.change_file_version(target_version=0)
    assert target.resolve() == first
    assert first.read_text() == "first\n"
    assert second.read_text() == "second\n"
    assert distributor.delete_target(yes=True)
    assert not target.exists()
    assert not first.exists() and not second.exists()


def test_directory_pipeline_and_failure_cleanup(
    tmp_path: Path, distributor: Distributor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dist transforms use private staging and leave active contents on failure.

    :param tmp_path: Isolated workspace.
    :param distributor: Distributor using real filesystem operations.
    :param monkeypatch: Fixture for observing transform staging.
    :return: None.
    """
    from distman.pipeline import run_pipeline

    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_bytes(b"value = 'OLD'\r\n")
    target = tmp_path / "deploy/sample"
    distributor.root["targets"]["sample"]["pipeline"] = {
        "tokens": {
            "func": "distman.transform.replace_tokens",
            "options": {"tokens": {"OLD": "NEW"}},
        }
    }
    workspaces = []

    def observe(**kwargs: object) -> str:
        """Record private staging while executing the real transform.

        :param kwargs: Pipeline invocation arguments.
        :return: Transformed payload path.
        """
        workspaces.append(Path(kwargs["build_dir"]))
        return run_pipeline(**kwargs)

    monkeypatch.setattr("distman.dist.distribution.run_pipeline", observe)
    assert distributor.dist(yes=True)
    active = target.resolve()
    assert "NEW" in (target / "module.py").read_text()
    assert "OLD" in (source / "module.py").read_text()
    distributor.root["targets"]["sample"]["pipeline"]["tokens"]["func"] = "missing.module"
    with pytest.raises((ImportError, AttributeError)):
        distributor.dist(yes=True, force=True)
    assert target.resolve() == active
    assert len(set(workspaces)) == 2
    assert all(not path.exists() for path in workspaces)
    assert not (tmp_path / "deploy/.distman/locks/write").exists()


@pytest.mark.parametrize(
    "operation", ["dist", "reset_file_version", "change_file_version", "delete_target"]
)
def test_dist_mutators_respect_shared_lock(
    tmp_path: Path, distributor: Distributor, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Every distribution mutation checks the same deployment lock before work.

    :param tmp_path: Isolated workspace.
    :param distributor: Distributor under test.
    :param monkeypatch: Fixture controlling timeout.
    :param operation: Distribution mutation method.
    :return: None.
    """
    lock = tmp_path / "deploy/.distman/locks/write"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(json.dumps({"user": "another-writer"}))
    monkeypatch.setenv("DISTMAN_LOCK_TIMEOUT", "0")
    from distman.dist.command import parse_args, run

    options = {
        "dist": [],
        "reset_file_version": ["--reset"],
        "change_file_version": ["--number", "1"],
        "delete_target": ["--delete"],
    }
    assert run(parse_args([str(tmp_path), *options[operation]])) == 1
    assert lock.exists()


def test_inventory_detects_preserved_timestamp_edits(tmp_path: Path) -> None:
    """Shared hashing detects changed bytes independently of modification times.

    :param tmp_path: Isolated payload directory.
    :return: None.
    """
    path = tmp_path / "payload"
    path.write_text("old")
    before = inventory(tmp_path)
    stat = path.stat()
    path.write_text("new")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert inventory(tmp_path) != before


def test_cache_does_not_copy_writer_locks(tmp_path: Path) -> None:
    """A replica must not inherit another deployment's live or stale lock ownership.

    :param tmp_path: Isolated source and cache roots.
    :return: None.
    """
    from distman.cache import cache

    source, destination = tmp_path / "source", tmp_path / "cache"
    with deployment_lock(source):
        (source / "payload").write_text("data")
        (source / ".distman/pending.json").write_text("source-only recovery state")
        (source / ".payload.install-lock").mkdir()
        cache(source, destination, workers=1)
        assert (source / ".distman/locks/write/owner.json").exists()
    assert (destination / "payload").read_text() == "data"
    assert not (destination / ".distman/locks").exists()
    assert not (destination / ".distman/pending.json").exists()
    assert not (destination / ".payload.install-lock").exists()
