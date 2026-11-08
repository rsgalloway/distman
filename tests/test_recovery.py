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
from typing import List, Optional, Tuple

import pytest

from distman import cli, deployment, recovery
from distman.locking import LockError, locks
from distman.metadata import RecoveryRequired, Store
from distman.operations import operation


def test_recover_defaults_to_configured_deployment_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use deployment policy when the optional recovery path is omitted.

    :param tmp_path: Isolated configured deployment root.
    :param monkeypatch: Fixture overriding the resolved deployment policy.
    :return: None.
    """
    root = tmp_path / "deployment"
    root.mkdir()
    monkeypatch.setattr(recovery.config, "DEPLOY_ROOT", str(root))

    assert cli.main(["recover", "--dryrun"]) == 0


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes")
def test_metadata_writes_preserve_shared_record_permissions(tmp_path: Path) -> None:
    """Inherit shared access for new records and retain it on replacement.

    :param tmp_path: Isolated deployment root.
    :return: None.
    """
    root = tmp_path / "deployment"
    metadata = root / ".distman"
    metadata.mkdir(parents=True)
    metadata.chmod(0o770)
    store = Store(root)
    current = metadata / "current.json"

    store.write("current.json", {"schema": 1, "snapshot": "first"})
    assert stat.S_IMODE(current.stat().st_mode) == 0o660
    current.chmod(0o640)
    store.write("current.json", {"schema": 1, "snapshot": "second"})
    assert stat.S_IMODE(current.stat().st_mode) == 0o640
    assert not list(metadata.glob(".record-*"))

    metadata.chmod(0o2770)
    nested = metadata / "targets/sample/revisions"
    store.write("targets/sample/revisions/first.json", {"schema": 1})
    assert all(
        stat.S_IMODE(directory.stat().st_mode) == 0o2770
        for directory in (metadata / "targets", metadata / "targets/sample", nested)
    )
    assert stat.S_IMODE((nested / "first.json").stat().st_mode) == 0o660


def test_external_targets_coordinate_across_deployment_roots(tmp_path: Path) -> None:
    """Reject cross-root target races and ownership replacement.

    :param tmp_path: Isolated deployment roots, target, and staged payloads.
    :return: None.
    """
    roots = (tmp_path / "first-root", tmp_path / "second-root")
    target = tmp_path / "external/tools/sample"
    sources = (tmp_path / "first", tmp_path / "second")
    for source, content in zip(sources, ("first", "second")):
        source.write_text(content)

    with operation(roots[0]):
        deployment.publish_targets([deployment.TargetPublication(target, sources[0])], roots[0])
    first_link = os.readlink(target)

    with operation(roots[1]):
        with pytest.raises(RecoveryRequired, match="another deployment"):
            deployment.publish_targets([deployment.TargetPublication(target, sources[1])], roots[1])
    assert os.readlink(target) == first_link
    assert target.read_text() == "first"

    with locks([target]):
        with operation(roots[1]):
            with pytest.raises(LockError, match="Deployment lock exists"):
                deployment.publish_targets(
                    [deployment.TargetPublication(target, sources[1])], roots[1]
                )


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
    assert cli.main(["recover", str(root), "--dryrun"]) == 0
    assert Store(root).pending() == journal
    assert (changes[0][1] / "payload").read_text() == "updated"
    assert cli.main(["recover", str(root), "--yes"]) == 0
    assert Store(root).pending() is None
    assert all((destination / "payload").read_text() == "original" for _, destination in changes)


@pytest.mark.parametrize("during_gap", [False, True])
def test_process_death_is_recoverable_from_persisted_state(
    tmp_path: Path, during_gap: bool
) -> None:
    """A fresh process can restore publication without the dead process's memory.

    :param tmp_path: Isolated staging/deployment workspace.
    :param during_gap: Kill the process during Windows' unlink/rename interval.
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
if sys.argv[4] == 'gap':
    deployment.platform.system = lambda: 'Windows'
    rename = os.rename
    def crash_rename(source, destination):
        if destination == root / 'library':
            os._exit(73)
        rename(source, destination)
    deployment.os.rename = crash_rename
else:
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
        [
            sys.executable,
            "-c",
            code,
            str(root),
            str(changes[0][0]),
            str(changes[1][0]),
            "gap" if during_gap else "switched",
        ],
        env=environment,
    )
    assert result.returncode == 73
    assert Store(root).pending() is not None
    if during_gap:
        assert not os.path.lexists(changes[0][1])
        store = Store(root)
        journal = store.pending()
        journal["changes"][0].pop("allow_absent")
        store.write(f"operations/{journal['id']}.json", journal)
        with pytest.raises(RecoveryRequired, match="conflict"):
            deployment.recover_publication(root, dryrun=True)
        journal["changes"][0]["allow_absent"] = True
        store.write(f"operations/{journal['id']}.json", journal)
    else:
        assert (changes[0][1] / "payload").read_text() == "updated"
    assert (changes[1][1] / "payload").read_text() == "original"
    # The process is known to have exited; stale-lock removal is deliberately explicit.
    shutil.rmtree(root / ".distman/locks/write")
    assert cli.main(["recover", str(root), "--yes"]) == 0
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


def test_revision_allocation_reads_only_affected_target_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allocate revisions without loading unrelated snapshot-history records.

    :param tmp_path: Isolated deployment and staged-source root.
    :param monkeypatch: Fixture rejecting the former global-history scan.
    :return: None.
    """
    root = tmp_path / "deployment"
    publications = []
    for name in ("library", "scripts"):
        source = tmp_path / f"{name}-initial"
        source.write_text("initial")
        publications.append(deployment.TargetPublication(root / name, source))
    deployment.publish_targets(publications, root)

    def reject_global_history(self: Store) -> dict:
        """Fail if allocation traverses the global snapshot history.

        :param self: Deployment metadata store.
        :return: Never returns.
        :raises AssertionError: Always.
        """
        raise AssertionError("global history scan")

    original = Store.target_revision_records
    selected = []

    def record_target(self: Store, target: str) -> dict:
        """Record the indexed target and delegate to the real reader.

        :param self: Deployment metadata store.
        :param target: Recorded target path selected for allocation.
        :return: Validated revision records for that target.
        """
        selected.append(target)
        return original(self, target)

    monkeypatch.setattr(Store, "target_history_records", reject_global_history)
    monkeypatch.setattr(Store, "target_revision_records", record_target)
    updated = tmp_path / "library-updated"
    updated.write_text("updated")
    deployment.publish_targets([deployment.TargetPublication(root / "library", updated)], root)
    assert selected == ["library"]
    assert (root / "library").read_text() == "updated"
    assert (root / "scripts").read_text() == "initial"


def test_reserved_versions_target_is_rejected_before_publication(tmp_path: Path) -> None:
    """Reject a target that would occupy its own revision-storage directory.

    :param tmp_path: Isolated deployment and staged-source root.
    :return: None.
    """
    root = tmp_path / "deployment"
    source = tmp_path / "source"
    source.write_text("payload")
    destination = root / "lib/versions"
    with pytest.raises(ValueError, match="Reserved or invalid"):
        deployment.publish_targets([deployment.TargetPublication(destination, source)], root)
    assert not root.exists()


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


@pytest.mark.parametrize("failure", [None, OSError, KeyboardInterrupt])
def test_windows_directory_link_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Optional[type]
) -> None:
    """Publish directory links under Windows rename rules and restore interrupted swaps.

    :param tmp_path: Isolated deployment workspace.
    :param monkeypatch: Fixture emulating Windows directory replacement semantics.
    :param failure: Optional exception injected during the first unlink/rename gap.
    :return: None.
    """
    root, changes = initial_deployment(tmp_path)
    previous = Store(root).current()
    replace, rename = os.replace, os.rename
    interrupted = False

    def reject_directory_replace(source: Path, destination: Path) -> None:
        """Reject replacing directory links as Windows does.

        :param source: Prepared link or metadata file.
        :param destination: Destination to replace.
        :return: None.
        :raises PermissionError: If replacing an existing directory link.
        """
        if Path(destination).is_symlink() and Path(destination).is_dir():
            raise PermissionError("Windows refuses directory link replacement")
        replace(source, destination)

    def rename_link(source: Path, destination: Path) -> None:
        """Require absent destinations and optionally interrupt the swap.

        :param source: Prepared revision or temporary link.
        :param destination: Intended destination.
        :return: None.
        :raises BaseException: Requested interruption or existing destination.
        """
        nonlocal interrupted
        if destination in [target for _, target in changes]:
            assert not os.path.lexists(destination)
            if failure is not None and not interrupted:
                interrupted = True
                raise failure("interrupted link swap")
        rename(source, destination)

    monkeypatch.setattr(deployment.platform, "system", lambda: "Windows")
    monkeypatch.setattr(deployment.os, "replace", reject_directory_replace)
    monkeypatch.setattr(deployment.os, "rename", rename_link)
    with operation(root):
        if failure is None:
            deployment.publish_directories(changes, root)
        else:
            with pytest.raises(failure, match="interrupted link swap"):
                deployment.publish_directories(changes, root)
    assert Store(root).pending() is None
    assert (Store(root).current() == previous) == (failure is not None)
    expected = "original" if failure is not None else "updated"
    assert all((target / "payload").read_text() == expected for _, target in changes)


def test_windows_link_swap_preserves_unmanaged_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handle dangling link transitions without removing unmanaged directories.

    :param tmp_path: Isolated directory holding targets and links.
    :param monkeypatch: Fixture selecting the Windows link replacement path.
    :return: None.
    """
    monkeypatch.setattr(deployment.platform, "system", lambda: "Windows")
    revision = tmp_path / "revision"
    revision.mkdir()
    link = tmp_path / "active"
    deployment.replace_link(link, revision)
    deployment.replace_link(link, "missing")
    assert link.is_symlink() and not link.exists()
    deployment.replace_link(link, revision)
    assert link.resolve() == revision
    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / "keep").write_text("unowned")
    with pytest.raises(OSError):
        deployment.replace_link(unmanaged, revision)
    assert (unmanaged / "keep").read_text() == "unowned"


def test_publication_and_recovery_epochs_are_cache_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication and rollback retain the decimal timestamp contract used by cache.

    :param tmp_path: Isolated deployment workspace.
    :param monkeypatch: Fixture injecting activation failure.
    :return: None.
    """
    from distman import cache, util

    root, changes = initial_deployment(tmp_path)
    before = util.read_epoch_file(str(root))
    assert before.isdecimal()
    cache.print_staleness(before, before, threshold=0)
    replace = deployment.replace_link
    interrupted = False

    def fail_once(destination: Path, target: str) -> None:
        """Interrupt after activation to exercise restoration's epoch write.

        :param destination: Active link.
        :param target: Prepared revision.
        :return: None.
        :raises OSError: Once, after the first activation.
        """
        nonlocal interrupted
        replace(destination, target)
        if not interrupted:
            interrupted = True
            raise OSError("epoch rollback test")

    monkeypatch.setattr(deployment, "replace_link", fail_once)
    with pytest.raises(OSError, match="epoch rollback test"):
        with operation(root):
            deployment.publish_directories(changes, root)
    after = util.read_epoch_file(str(root))
    assert after.isdecimal() and int(after) >= int(before)
    cache.print_staleness(after, before, threshold=0)
