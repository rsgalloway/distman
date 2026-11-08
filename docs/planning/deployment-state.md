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
would require another activation layout. POSIX replaces each link atomically.
Windows directory links require unlinking the old link before renaming the new
one, so readers can briefly observe a missing path. Windows journals mark this
possible intermediate absence before activation; recovery restores the previous
revision after interruption in that gap. Missing paths remain conflicts for
journals without that marker and for committed operations.

The package-file proposal in issue #13 can reuse this selection model for named,
frozen sets of revisions, including prepared but inactive versions. Portable export
would use root-relative destinations and verified artifact references or a payload
bundle. Journals are root-local operational records, not portable package files.
Cross-root restoration, path relocation, and export remain later work.

## Implemented foundation

The journaled target publisher writes:

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

It publishes individual files and directories and records unlinks with a null target
selection. Revision records reference payloads in the existing sibling `versions/`
layout and contain the target inventory plus package ownership/provenance state. New
package publications write no embedded `.distman-install.json` receipt. Shared
library and bin paths are ordinary containers; package/module, metadata, data, and
command children are independent targets. Package-exclusive binary roots remain one
target containing their runtime layouts.

A snapshot contains schema version, ID, UTC creation time, parent ID, coverage, and
changed destination-to-revision-record selections. Newer changes override parent
selections; null selections prevent an unlinked target from reappearing while
traversing history. Coverage remains `recorded-targets`: targets journaled by install,
uninstall, or undist only. Legacy dist publication does not yet participate, so these are not
complete whole-deployment snapshots.

Successful publication proceeds as follows:

1. Under the deployment writer lock, reject unresolved pending work and validate targets.
2. Copy and flush new file/directory payload revisions; an unlink prepares no payload.
3. Persist target revision records, a candidate snapshot, and the operation journal.
4. Persist the pending pointer before changing active links.
5. Replace or unlink targets, persist the current snapshot, and update the epoch token.
6. Mark the operation committed, then remove the pending pointer.

Metadata-only runtime-selection changes reference the existing payload while writing
a new target record. Prepared but unselected records/payloads may remain after
failure. The current pointer and parent chain identify committed selections. Normal
uninstall retains inactive payloads; `uninstall --delete` removes revisions only
after the unlink operation commits.

## Recovery behavior

`distman recover --root PATH --dryrun` previews recovery.
`distman recover --root PATH --yes` performs it under the writer lock.
Without `--yes`, recovery asks for confirmation with `[Y/n]` and accepts an empty
answer. Recovery never automatically steals a stale lock; first establish that the
recorded process has stopped.

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
revisions remain available for inspection. Journaled package unlinks are recoverable. Legacy dist deletion/quarantine is not yet migrated to this publisher.

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
add explicit adapters/migration for old embedded installer receipts and sibling
`.dist` records, and define baseline discovery for existing deployments. Then
implement list/status against central metadata, snapshot-aware pruning and rollback,
and optional audit integration.
