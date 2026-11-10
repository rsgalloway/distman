# Deployment restoration

## Goal

Restore every journaled target in a deployment root to a previously committed
selection while preserving a complete, append-only audit trail. The same operation
supports rollback to an older state and roll forward to a later historical state.

Restoration is distinct from `distman recover`. Recovery repairs one interrupted
publication identified by `.distman/pending.json`. Restoration deliberately changes a
clean deployment from its current committed snapshot to another committed snapshot.

## Command shape

```bash
distman restore --snapshot SNAPSHOT_ID [PATH]
distman restore --at TIMESTAMP [PATH]
distman restore --snapshot SNAPSHOT_ID [PATH] --dryrun
distman restore --snapshot SNAPSHOT_ID [PATH] --yes
```

`PATH` is optional and defaults to the configured deployment root. Exactly one of
`--snapshot` and `--at` is required. `--at` selects the newest committed snapshot at
or before the supplied UTC timestamp and reports its resolved snapshot ID. Snapshot
IDs and parent links are authoritative; timestamps are descriptive and may not be
unique.

The command must display the deployment root, current snapshot, selected historical
snapshot, selected timestamp, and every target link that will be added, changed, or
removed. Interactive publication uses the standard `[Y/n]` confirmation. Dry run is
read-only.

## History model

A restore does not move `current.json` backward or reuse an old operation journal.
It resolves the complete target selection at the chosen historical snapshot, compares
that selection with the current state, and publishes the difference as a new
operation. The resulting snapshot has the current snapshot as its parent and records
only changed selections.

For example, restoring `S2` while `S5` is current creates `S6` whose effective
selection matches `S2`. Restoring the former `S5` selection later creates another new
snapshot. This keeps history linear, makes every restore auditable, and leaves normal
recovery able to repair an interrupted restore.

## Publication algorithm

1. Resolve the deployment policy and acquire its writer lock.
2. Reject pending recovery work and validate the current snapshot chain.
3. Resolve the requested snapshot ID or timestamp within the current root's history.
4. Materialize effective target selections for the current and requested snapshots.
5. Compute changed, added, and removed target links without scanning unrelated payload
   contents.
6. Validate every required revision record, retained payload, destination boundary,
   and current active link. Refuse external changes instead of overwriting them.
7. Present the full plan and ask for confirmation unless `--yes` was supplied.
8. Publish through the existing target journal, creating a new incremental snapshot.
9. Update the epoch token and preserve normal interruption recovery behavior.

A restore with no selection changes succeeds without creating another snapshot.
Multiple target links are not globally atomic to readers; the existing per-target
platform guarantees and recovery journal still apply.

## Coverage and retention

Restoration covers only targets represented in central snapshot metadata. Until
legacy `dist` publication is migrated to the journaled publisher, a snapshot does not
represent the entire physical deployment root. The command must report its coverage
as `recorded-targets` and must not remove unknown or unmanaged files.

A target can be restored only while its revision record and retained payload still
exist. Snapshot-aware deletion and pruning must refuse to remove history that a
retained snapshot needs, or explicitly expire that snapshot first. `uninstall
--delete` and future pruning behavior must share this retention contract before
restore can promise durable historical availability.

Restoration reproduces recorded target selections. It does not recover manually
edited file bytes, delete untracked files, or certify that unrelated contents are
unchanged. A separate status or audit operation can compare recorded inventories and
optional audit manifests against disk before or after restoration.

## Timestamp selection

Timestamp input must require an unambiguous ISO 8601 value and normalize it to UTC.
Selection walks committed snapshot history and chooses the newest snapshot whose
creation time is not later than the requested time. If timestamps collide, parent
order and snapshot identity break the tie. Invalid timestamps, times before the first
snapshot, snapshots outside the current root's ancestry, and corrupt or cyclic
history fail without mutation.

The plan and resulting operation record store both the user's timestamp expression
and resolved snapshot ID so later audits do not depend on repeating timestamp
resolution.

## Validation

Tests must cover:

- restoration by exact snapshot and by timestamp;
- rollback followed by roll forward to a formerly current selection;
- additions, replacements, and removals in one operation;
- a no-op restore;
- missing revision records or retained payloads;
- modified, missing, unmanaged, or externally relinked destinations;
- pending recovery work and writer-lock serialization;
- interruption during restoration followed by `distman recover`;
- timestamp boundaries, collisions, time zones, and invalid input;
- dry run and declined confirmation without metadata or deployment writes;
- POSIX and Windows link behavior;
- explicit reporting of incomplete `recorded-targets` coverage.

## Non-goals

The initial implementation does not relocate a snapshot to another deployment root,
bundle payloads, restore unmanaged files, provide application-wide atomic visibility,
or add a graphical history browser. Export, relocation, auditing, and dashboard work
can consume the same snapshot model later.
