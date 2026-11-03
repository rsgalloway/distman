# Deployment state, snapshots, and recovery

## Contract

Deployment history records selected managed revisions. It does not certify that all
files on disk remain unchanged. A normal dist operation must do work proportional
to the targets it changes, without hashing or copying metadata for the whole root.

Command orchestration owns policy, locks, staging, confirmation, and cancellation.
Domain operations execute within that boundary. Before publication, failures leave
active contents unchanged. During publication, recovery restores recorded previous
links when retained contents remain available. Unexpected external changes must
produce conflicts, not be overwritten.

Power loss and forced termination cannot run Python cleanup. Recovery therefore
uses persisted records rather than an in-memory list of completed steps. File and
directory flushes establish ordering where supported; actual durability still needs
validation on the intended operating system and shared filesystem.

## Record model

| Record | Purpose |
| --- | --- |
| Revision | Identifies one prepared payload and its ownership/provenance metadata |
| Snapshot | Timestamped selection changes with a unique ID and parent snapshot |
| Current pointer | Selects the latest committed journaled snapshot |
| Operation journal | Stores previous/intended links and snapshot IDs for recovery |
| Pending pointer | Identifies the one interrupted operation that blocks writers |
| Optional audit manifest | Records observed filesystem contents and verification scope |

Timestamps are descriptive; unique IDs and parent links establish history. Snapshots
reference existing revisions instead of copying payloads. A successful modification
adds only changed selections. Periodic metadata checkpoints can bound reconstruction
costs without rescanning contents. Retention must preserve payload revisions and
metadata referenced by retained snapshots; ordinary pruning must not invalidate
history silently.

A future whole-root rollback should create a new history entry selecting an older
snapshot. It is distinct from recovering an interrupted publication. Multiple link
switches are not globally atomic to readers; atomic application-wide visibility
would require another activation layout.

The package-file proposal in issue #13 can reuse this selection model for named,
frozen sets of revisions, including prepared but inactive versions. Portable export
would use root-relative destinations and verified artifact references or a payload
bundle. Journals are root-local operational records, not portable package files.
Cross-root restoration, path relocation, and export remain later work.

## Implemented foundation

The journaled directory publisher writes:

```text
.distman/
    targets/<target-id>/revisions/<operation-id>.json
    snapshots/<snapshot-id>.json
    operations/<operation-id>.json
    current.json
    pending.json
    epoch
    locks/write/owner.json
```

Revision records reference payloads in the existing `versions/` layout. Package
ownership still lives in embedded `.distman-install.json` receipts; revision records
identify that format without duplicating every file entry. Legacy sibling `.dist`
records and embedded receipt readers remain unchanged during this foundation slice.
No migration of historical payloads or ownership has occurred yet.

A snapshot contains schema version, ID, UTC creation time, parent ID, coverage, and
changed destination-to-revision-record selections. A null parent starts a partial
history; it does not claim that preexisting unrecorded targets are absent. Coverage
is currently `recorded-targets`: installer/uninstaller directory publications only.
Legacy dist still publishes through its existing implementation and does not yet
participate in snapshot history. Do not advertise these records as complete
whole-root snapshots until writer migration and baseline discovery are implemented.

Successful publication proceeds as follows:

1. Under the root writer lock, reject unresolved pending work and validate targets.
2. Copy and flush replacement revisions, retaining existing revisions.
3. Persist revision records, a candidate snapshot, and the operation journal.
4. Persist the pending pointer before switching any active links.
5. Switch links, persist the current snapshot, and durably update the epoch token.
6. Mark the operation committed, then remove the pending pointer.

Prepared but unselected snapshots/revisions may remain after failure. The current
pointer and its parent chain identify committed selections; merely finding a
snapshot file is not evidence that it was activated. Journal lookup and pending
checks do not walk the whole history or read unrelated payload contents.

## Recovery behavior

`distman recover --root PATH --dryrun` previews recovery.
`distman recover --root PATH --yes` performs it under the writer lock.
Without `--yes`, recovery asks for confirmation. Recovery never automatically
steals a stale lock; first establish that the recorded process has stopped.

For a prepared operation, validate every actual link against its recorded old and
intended new values, and verify required previous revisions still exist. Then restore
previous links and the prior snapshot pointer, update cache invalidation, mark the
operation restored, and clear pending state. Interrupted restoration is repeatable.
A committed operation with only pending cleanup remaining is finalized without
undoing its publication. Unknown links, non-link destinations, missing revisions,
or corrupt records block recovery before changes where possible.

Exceptions, including KeyboardInterrupt, use the same recovery path during journaled
activation. Failure to restore retains pending state and blocks later mutations.
The command reports cancellation with exit status 130 after cleanup. Retained
revisions remain available for inspection. Deletion/quarantine recovery for legacy
dist is not implemented by this directory publisher.

Cache replication excludes source lock directories, operation journals, and pending
state; these are not transferable ownership of work. Consistent cache observation
during multi-target publication and cache writer coordination remain separate work.

## Auditing and remaining work

Optional hashio manifests may attach to snapshots or audit results. Compare
snapshots to explain recorded deployment changes; compare a saved baseline to a
fresh scan to detect manual modifications, additions, and deletions. Preserve the
baseline and report scan scope, exclusions, completion, and verification time.
Full verification hashes contents, including files with unchanged timestamps.
Recovering manually edited bytes is outside scope.

Full scans are explicit or scheduled, never required for ordinary publication.
Hashio remains optional; no dependency has been added. Snapshot creation does not
implicitly run hashio or certify unrelated filesystem contents.

Next migrate legacy dist publication and revision selection to the shared publisher,
replace legacy metadata writes with revision ownership/provenance records, and add
baseline discovery for existing deployments. Define recoverable deletion using
retention/quarantine before migrating deletion. Then implement list/status against
shared metadata, snapshot-aware pruning and rollback, and optional audit integration.
