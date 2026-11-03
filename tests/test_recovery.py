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

"""Failure injection for durable publication journals and command orchestration."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

from distman import cli, deployment
from distman.metadata import RecoveryRequired, Store
from distman.operations import operation


def initial_deployment(tmp_path: Path) -> Tuple[Path, List[Tuple[Path, Path]]]:
    """Publish two initial revisions and prepare changed source contents.

    :param tmp_path: Isolated deployment and staging workspace.
    :return: Deployment root and source/destination pairs for another publication.
    """
    root = tmp_path / "deployment"
    changes = []
    for name in ("library", "scripts"):
        stage = tmp_path / name
        stage.mkdir()
        (stage / "payload").write_text("original")
        changes.append((stage, root / name))
    with operation(root):
        deployment.publish_directories(changes, root)
    for stage, _ in changes:
        (stage / "payload").write_text("updated")
    return root, changes


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_failure_after_link_switch_restores_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type
) -> None:
    """Rollback inspects actual links even when replacement raises after changing one.

    :param tmp_path: Isolated workspace.
    :param monkeypatch: Fixture injecting activation failure.
    :param failure: Ordinary failure or user cancellation.
    :return: None.
    """
    root, changes = initial_deployment(tmp_path)
    previous = Store(root).current()
    replace = deployment.replace_link
    interrupted = False

    def fail_after_replace(destination: Path, target: str) -> None:
        """Interrupt after one actual mutation, without interrupting restoration.

        :param destination: Active link path.
        :param target: Intended link text.
        :return: None.
        :raises BaseException: Selected injected failure after the first mutation.
        """
        nonlocal interrupted
        replace(destination, target)
        if not interrupted:
            interrupted = True
            raise failure("interrupted after switch")

    monkeypatch.setattr(deployment, "replace_link", fail_after_replace)
    with pytest.raises(failure):
        with operation(root):
            deployment.publish_directories(changes, root)
    assert Store(root).current() == previous
    assert Store(root).pending() is None
    assert all((destination / "payload").read_text() == "original" for _, destination in changes)
    assert not (root / ".distman/locks/write").exists()


def test_failed_restoration_blocks_writers_and_refuses_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed rollback stays recoverable and cannot overwrite unrecognized links.

    :param tmp_path: Isolated workspace.
    :param monkeypatch: Fixture injecting failed activation and restoration.
    :return: None.
    """
    root, changes = initial_deployment(tmp_path)
    replace = deployment.replace_link

    def fail(destination: Path, target: str) -> None:
        """Fail script activation and library restoration.

        :param destination: Active link path.
        :param target: Intended revision link.
        :return: None.
        :raises OSError: For the selected activation/restoration failures.
        """
        if destination == changes[1][1] or target.endswith("library.0"):
            raise OSError("storage unavailable")
        replace(destination, target)

    monkeypatch.setattr(deployment, "replace_link", fail)
    with pytest.raises(RecoveryRequired):
        with operation(root):
            deployment.publish_directories(changes, root)
    with pytest.raises(RecoveryRequired, match="needs recovery"):
        with operation(root):
            pass
    monkeypatch.setattr(deployment, "replace_link", replace)
    journal = Store(root).pending()
    assert journal is not None
    original = os.readlink(changes[1][1])
    replace(changes[1][1], "versions/unexpected")
    with pytest.raises(RecoveryRequired, match="conflict"):
        deployment.recover_publication(root)
    assert (changes[0][1] / "payload").read_text() == "updated"
    replace(changes[1][1], original)
    assert cli.main(["recover", "--root", str(root), "--dryrun"]) == 0
    assert Store(root).pending() == journal
    assert (changes[0][1] / "payload").read_text() == "updated"
    assert cli.main(["recover", "--root", str(root), "--yes"]) == 0
    assert Store(root).pending() is None
    assert all((destination / "payload").read_text() == "original" for _, destination in changes)


def test_process_death_is_recoverable_from_persisted_state(tmp_path: Path) -> None:
    """A fresh process can restore publication without the dead process's memory.

    :param tmp_path: Isolated staging/deployment workspace.
    :return: None.
    """
    root, changes = initial_deployment(tmp_path)
    before = Store(root).current()
    code = """
import os, sys
from pathlib import Path
from distman import deployment
from distman.operations import operation
root = Path(sys.argv[1])
replace = deployment.replace_link
def crash(destination, target):
    replace(destination, target)
    os._exit(73)
deployment.replace_link = crash
with operation(root):
    deployment.publish_directories([(Path(sys.argv[2]), root / 'library'),
                                   (Path(sys.argv[3]), root / 'scripts')], root)
"""
    environment = dict(os.environ)
    environment.pop("DISTMAN_PARENT_LOCK", None)
    environment["PYTHONPATH"] = os.pathsep.join(str(Path(p).resolve()) for p in sys.path if p)
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), str(changes[0][0]), str(changes[1][0])],
        env=environment,
    )
    assert result.returncode == 73
    assert Store(root).pending() is not None
    assert (changes[0][1] / "payload").read_text() == "updated"
    assert (changes[1][1] / "payload").read_text() == "original"
    # The process is known to have exited; stale-lock removal is deliberately explicit.
    shutil.rmtree(root / ".distman/locks/write")
    assert cli.main(["recover", "--root", str(root), "--yes"]) == 0
    assert Store(root).current() == before
    assert all((destination / "payload").read_text() == "original" for _, destination in changes)


def test_incremental_snapshots_do_not_scan_unrelated_payloads(tmp_path: Path) -> None:
    """Each snapshot references its parent and only records changed targets.

    :param tmp_path: Isolated deployment and staging workspace.
    :return: None.
    """
    root, changes = initial_deployment(tmp_path)
    store = Store(root)
    first = store.current()
    (changes[1][1] / "payload").write_text("manual edit")
    with operation(root):
        deployment.publish_directories(changes[:1], root)
    second = store.current()
    snapshot = store.read(f"snapshots/{second}.json")
    assert snapshot["parent"] == first
    assert set(snapshot["changes"]) == {"library"}
    assert snapshot["coverage"] == "recorded-targets"
    assert (changes[1][1] / "payload").read_text() == "manual edit"
    assert store.read(f"snapshots/{first}.json")["id"] == first


def test_committed_publication_cleanup_keeps_new_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interruption after commit only requires journal cleanup.

    :param tmp_path: Isolated workspace.
    :param monkeypatch: Fixture injecting a failure before journal cleanup.
    :return: None.
    """
    root, changes = initial_deployment(tmp_path)
    store = Store(root)
    remove = Store.remove

    def retain_pending(self: Store, relative: str) -> None:
        """Simulate interruption before clearing a committed journal.

        :param self: Metadata store receiving the cleanup.
        :param relative: Metadata path to remove.
        :return: None.
        :raises OSError: When clearing pending publication state.
        """
        if relative == "pending.json":
            raise OSError("cleanup interrupted")
        remove(self, relative)

    monkeypatch.setattr(Store, "remove", retain_pending)
    with pytest.raises(OSError):
        deployment.publish_directories(changes, root)
    journal = store.pending()
    assert journal["phase"] == "committed"
    monkeypatch.setattr(Store, "remove", remove)
    # A committed journal finalizes without rolling back a successful activation.
    deployment.recover_publication(root)
    assert store.pending() is None
    assert all((destination / "payload").read_text() == "updated" for _, destination in changes)


def test_invalid_pending_metadata_blocks_operations(tmp_path: Path) -> None:
    """Corrupt coordination metadata never falls back to an apparently empty state.

    :param tmp_path: Isolated deployment root.
    :return: None.
    """
    metadata = tmp_path / ".distman"
    metadata.mkdir()
    (metadata / "pending.json").write_text(json.dumps({"schema": 1, "operation": "../outside"}))
    with pytest.raises(RecoveryRequired, match="identifier"):
        with operation(tmp_path):
            pass
    assert not (metadata / "locks/write").exists()
    with operation(tmp_path, read_only=True):
        assert (metadata / "pending.json").exists()


def test_missing_baseline_payload_blocks_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse restoration before changing links if the old bytes no longer exist.

    :param tmp_path: Isolated workspace.
    :param monkeypatch: Fixture injecting interrupted activation and rollback.
    :return: None.
    """
    root, changes = initial_deployment(tmp_path)
    old = changes[0][1].resolve()
    replace = deployment.replace_link

    def fail_second(destination: Path, target: str) -> None:
        """Fail after the library has changed.

        :param destination: Active link path.
        :param target: Intended link text.
        :return: None.
        :raises OSError: For the second activation.
        """
        if destination == changes[1][1]:
            raise OSError("interrupted")
        replace(destination, target)

    def interrupted_recovery(*args: object, **kwargs: object) -> None:
        """Simulate failure to run rollback.

        :param args: Recovery arguments.
        :param kwargs: Recovery keyword arguments.
        :return: None.
        :raises OSError: Always, to preserve the pending journal.
        """
        raise OSError("recovery unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(deployment, "replace_link", fail_second)
        patch.setattr(deployment, "recover_publication", interrupted_recovery)
        with pytest.raises(RecoveryRequired):
            deployment.publish_directories(changes, root)
    shutil.rmtree(old)
    with pytest.raises(RecoveryRequired, match="revision is missing"):
        deployment.recover_publication(root)
    assert (changes[0][1] / "payload").read_text() == "updated"
    assert Store(root).pending() is not None


def test_dist_command_cancellation_releases_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation is translated only after command resources have unwound.

    :param tmp_path: Isolated deployment root.
    :param monkeypatch: Fixture injecting cancellation in command execution.
    :return: None.
    """
    from distman import config
    from distman.dist import command

    def cancel(args: object) -> int:
        """Cancel command execution.

        :param args: Parsed command request.
        :return: Never returns.
        :raises KeyboardInterrupt: Always.
        """
        raise KeyboardInterrupt()

    monkeypatch.setattr(config, "DEPLOY_ROOT", str(tmp_path))
    monkeypatch.setattr(command, "_run", cancel)
    assert command.run(command.parse_args([])) == 130
    assert not (tmp_path / ".distman/locks/write").exists()


def test_first_publication_interrupt_restores_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore absent links and snapshot pointers when the first publication fails.

    :param tmp_path: Isolated workspace.
    :param monkeypatch: Fixture injecting cancellation after link creation.
    :return: None.
    """
    source = tmp_path / "stage"
    source.mkdir()
    (source / "payload").write_text("new")
    root = tmp_path / "root"
    destination = root / "target"
    replace = deployment.replace_link

    def interrupt(destination: Path, target: str) -> None:
        """Cancel immediately after creating the new active link.

        :param destination: Active destination.
        :param target: New revision link.
        :return: None.
        :raises KeyboardInterrupt: After link creation.
        """
        replace(destination, target)
        raise KeyboardInterrupt()

    monkeypatch.setattr(deployment, "replace_link", interrupt)
    with pytest.raises(KeyboardInterrupt):
        with operation(root):
            deployment.publish_directories([(source, destination)], root)
    assert not os.path.lexists(destination)
    assert Store(root).current() is None
    assert Store(root).pending() is None
    assert (root / "versions/target.0/payload").read_text() == "new"


@pytest.mark.parametrize("fail_flush", [False, True])
def test_payload_flush_uses_writable_handles_and_preserves_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_flush: bool
) -> None:
    """Flush read-only payloads using Windows-compatible handles without altering bytes.

    :param tmp_path: Private staging directory.
    :param monkeypatch: Fixture enforcing writable descriptors during synchronization.
    :param fail_flush: Whether to inject an I/O failure during synchronization.
    :return: None.
    """
    payload = tmp_path / "payload"
    payload.write_bytes(b"original bytes\r\n")
    payload.chmod(stat.S_IREAD)
    original_mode = stat.S_IMODE(payload.stat().st_mode)
    fsync = os.fsync
    flushed = []

    def require_writable(descriptor: int) -> None:
        """Emulate Windows' writable handle requirement, optionally failing the flush.

        :param descriptor: Open payload or directory descriptor.
        :return: None.
        :raises OSError: If the handle is read-only or failure was requested.
        """
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.write(descriptor, b"")
            flushed.append(descriptor)
            if fail_flush:
                raise OSError("injected flush failure")
        fsync(descriptor)

    monkeypatch.setattr(deployment.os, "fsync", require_writable)
    try:
        if fail_flush:
            with pytest.raises(OSError, match="injected flush failure"):
                deployment.flush_payload(tmp_path)
        else:
            deployment.flush_payload(tmp_path)
        assert len(flushed) == 1
        assert payload.read_bytes() == b"original bytes\r\n"
        assert stat.S_IMODE(payload.stat().st_mode) == original_mode
    finally:
        payload.chmod(original_mode | stat.S_IWUSR)
