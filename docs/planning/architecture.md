# Installation architecture audit

This document describes the installer module boundaries, compatibility surface,
and remaining architectural constraints.

## Current responsibilities and compatibility surface

| Module | Responsibility and coupling | Bounded change |
|---|---|---|
| `cli.py` | Suite parsing originally imported separate command parsers | Centralized options, parser construction, and dispatch following rhps-ingest |
| `dist/command.py`, `dist/distribution.py` | Command orchestration and existing Git-aware distribution operations | Replaced `dist.py`; updated internal callers and entry points without an import facade |
| `source.py` | `Source` loads `dist.json`; `GitRepo` adds repository inspection | Preserved both classes and Git checks |
| `deployment.py` | Git-independent target publication for install/uninstall/undist | Versions files/directories, journals replacements/unlinks, and restores interrupted publication |
| `operations.py`, `metadata.py`, `recovery.py` | Writer coordination, durable state storage, and explicit recovery | Root lock and pending-operation checks surround command execution |
| `config.py` | Legacy constants evaluated from the environment at import time | Preserved defaults for existing callers |
| `install/policy.py` | Optional envstack/pathbase policy mixed into command namespace | Moved to `policy.py`; retained an import compatibility shim |
| `resources/install.env` | Packaged fallback policy, formerly install/defaults.env | Configuration moved out of command implementation |
| `install/wheels.py` | pip resolution, wheel inspection, build, and staging | Typed artifacts and provenance; stages shared platform launchers |
| `install/ownership.py` | Package compatibility and dependency validation | Central records are primary; legacy embedded receipt reads remain transitional |
| `inventory.py`, `locking.py` | Generic SHA-256 inventory and writer coordination | No package acquisition dependencies; deployment-root locks with transitional target locks |
| `install/uninstall.py` | Package removal from central deployment state | Per-target unlink, partial binary revision, dependency checks, retained history, and `--delete` |
| `install/command.py` | Orchestrates acquisition and publication | Decomposes shared wheels into independent targets and keeps binary roots package-exclusive |
| Former `install/cli.py` | Separate installer parser and error reporting | Removed; options and error handling now live in `cli.py` |
| `pipeline.py`, `transform.py` | Transform specification, callable imports, shell execution | Preserved callable paths and behavior |
| `cache.py` | Cache CLI, mirroring, epoch/TTL checks, pruning | Preserved APIs and cache-specific filesystem operations |
| `util.py`, `logger.py` | Legacy path/copy/version helpers and logging configuration | Added documentation and type hints without changing semantics |

Compatibility requirements cover executable commands and options, configuration,
deployment behavior, and documented `distman.transform.*` callable paths used in
pipeline configuration. No external Python API consumers are currently known.
Existing Python class/function imports are not a compatibility requirement: rename
or remove them and update repository callers, tests, and entry points together.
Do not add facades, aliases, or re-exports solely to preserve the old module layout.
The remaining installer-policy shim is transitional, not an API guarantee.
Revisit a specific import only if an actual external consumer is identified.

The standalone `distcache` executable and its bin shims were intentionally removed;
use `distman cache`. The `dist` executable, `distman dist`, existing configuration,
and documented transform callable paths remain supported. These command and
internal-import decisions narrow the original compatibility goal explicitly.

## Remaining coupling

`Distributor` inherits `GitRepo` and loads Git metadata even for ad hoc sources.
`Source` is a configuration reader rather than a generic acquisition provider.
`dist/distribution.py` retains Git safety checks, transformation, and legacy
publication; argument parser definitions now live in `cli.py`.
`util.copy_file` normalizes text, which is inappropriate for wheel payload hashes.
`config.py` and `logger.py` capture environment settings at import time. Pathbase
0.2 can initialize envstack at import time, changing environment and Python paths;
the policy boundary contains those side effects and resolves configuration once.

`util` imports the logger, and the logger imports `config`; neither should import
installer orchestration. The installer formerly imported `dist.confirm` only to
prompt, unnecessarily pulling its Git-facing command module into orchestration.
Project distribution deliberately enters the existing command boundary to retain
Git safety and transform behavior. Shared state types do not import policy or CLI.

## Dependency direction and scope

CLI → install orchestration → policy / project inspection / wheel acquisition →
staged artifacts and provenance → ownership composition → deployment publication.

Policy uses envstack for file loading and expansion and pathbase for path templates.
It does not acquire packages. Configuration resources belong to the package's
`resources` folder, separate from command implementation. The repository's
`distman.env` remains the studio-facing configuration example.

`install/projects.py` inspects only the supplied directory. It delegates builds
and dependency resolution to pip and configured custom targets to `dist/command.py`.
A combined project's built wheel supplies dependency metadata but is excluded
from third-party publication when `dist.json` owns the project deployment.

This is not a wholesale CLI, source, cache, or transform rewrite. Legacy activation
semantics remain intact. The new source-neutral publication API requires managed
symlink destinations, copies bytes unchanged, prepares revisions before activation,
and restores prior links on caught failures. Multi-root activation is not globally
atomic. Combined legacy distribution and package publication likewise do not form
a new cross-command transaction; this boundary is documented rather than hidden.

## Centralized CLI reference

The maintainer-provided rhps-ingest implementation uses one `cli.py` containing
`build_parser`, `_add_*_options`, and command dispatch while importing operations
from dedicated modules. Distman now follows that arrangement for `dist`, `cache`,
and `install`. Optional installer imports remain lazy; `dist` retains its console
entry point, while caching is exposed through `distman cache`. Installer business
logic stays outside the CLI.

## Installer target and launcher ownership

A receipt records the runtimes selected for each package. Dependency validation
starts with those runtimes and follows reachable shared dependencies; it does not
assign every retained pure-Python application to every newly requested platform.
Installed dependencies in separate configured package roots participate in checks
and locking without being copied or republished.

Shared generated entrypoints become independent command targets, with POSIX and
Windows variants recorded under one package component. Staging regenerates variants
for requested and previously recorded platforms from the selected wheel. Normalized
Windows ZIP timestamps keep comparisons deterministic. Library/scripts selection
validation and publication rollback remain in the ownership/deployment layers.
This supports cross-generation on Linux. Native Windows CI executes generated
console launchers for wheel and local-project installs; GUI launchers and broader
argument/interpreter discovery cases remain separate validation work. See the
[installation roadmap](install-planning.md) for remaining validation checks and
unimplemented features.

## Dist and install boundaries

`dist` and `install` are acquisition and planning interfaces over the same deployment
model. `dist` reads repository-owned filesystem targets, Git state, and transforms;
`install` resolves Python distributions, dependency metadata, runtime compatibility,
and generated entrypoints. Both should converge on shared locking, target publication,
content matching, centralized metadata, recovery, and logging rather than developing
separate filesystem semantics. A package-provided `.env` file remains ordinary content:
a future recipe or wheel integration may select and publish it, while envstack remains
responsible for composing it at runtime.

## Refactoring direction (proposed)

Review distribution from its responsibilities outward before choosing new module
boundaries. Preserve established dist behavior with characterization coverage for
Git checks, transforms, revision selection, and publication. Internal imports may
change with their callers.
The goal is clearer acquisition, artifact, planning, and deployment responsibilities,
not a rewrite of working behavior or a class hierarchy created for symmetry.

`GitRepo` represents repository behavior, while a wheel `Artifact` represents a
selected payload. A package-index acquisition interface, if needed, should support
indexes beyond PyPI. Keep source acquisition separate from artifact identity and
shared publication. Extract metadata, inventory, and locking from installer-specific
modules so distribution and package lifecycle commands share them. Consider `dist/`
and `pipeline/` packages only where cohesive responsibilities justify the split.

The intended scope is uninstall, list, status, shared writer coordination, and
metadata under `.distman/`. Preserve limited reads of existing legacy dist info
files, document the compatibility boundary, and stop authoring new legacy records
when the new metadata writer is introduced. Preserve historical ownership needed
for rollback. Finalize storage and migration rules before building list/status.

Review CLI consistency alongside these boundaries: help, progress, diagnostics,
confirmation, and exit statuses. Optional terminal animations must not obscure errors
or contaminate redirected or machine-readable output. Keep noninteractive behavior
predictable and assess the existing tqdm dependency before adding presentation
libraries. CLI polish follows the current uninstall implementation checkpoint.

## Concrete audit findings

These findings describe the audit baseline; the implementation progress below
identifies the addressed items. They are not guarantees of the proposed design.

| Area | Evidence | Refactoring consequence |
| --- | --- | --- |
| Configuration and Git inheritance | `Source.read_dist_file` parses configuration; `GitRepo(Source)` adds Git; `Distributor(GitRepo)` orchestrates operations | Separate configuration data, repository inspection, and orchestration by composition |
| Two publication paths | `Distributor.dist` copies directly to numbered versions and `update_symlink` removes the old destination first; `publish_directories` stages directories and atomically replaces each link | Share publication while preserving file targets, directory targets, naming, and version-only behavior |
| Metadata written too early | `Distributor.dist` calls `write_dist_file` before transforms and payload publication | Record successful revision provenance alongside prepared contents, not as evidence of an attempted deployment |
| Legacy initialization | `create_dest_folder` uses `.dist` existence to decide whether a destination is initialized | Replace this check with explicit ownership lookup before stopping legacy writes |
| Mixed validation and reading | `install.ownership.read_state` reads metadata and hashes the full payload, raising on the first discrepancy | Separate schema loading from integrity checking so list is cheap and status can collect diagnostics |
| Generic code under install | Inventory and writer locks live in `install/ownership.py`; SHA-256 lives in `install/wheels.py` | Move generic filesystem concerns out of package acquisition |
| Distinct byte semantics | `util.copy_file` normalizes text; wheel publication preserves bytes | Normalize dist content before inventory; never normalize wheel payloads |
| Shared transform workspace | `run_pipeline` stages under build/transform/target/step | Use operation-specific temporary workspaces to prevent concurrent source builds colliding |
| Invalidation gaps | `write_epoch_file` logs exceptions without raising; `change_file_version` and `delete_target` do not update epoch | Publication, rollback, and deletion must report metadata failures and invalidate caches consistently |
| Package coordination | Install/uninstall lock roots; combined projects later call legacy dist | Include all affected destinations in one planned operation, without pretending multiple links switch atomically |
| Presentation coupling | Operations print/log directly; CLI imports dist/cache eagerly; cache already uses tqdm | Introduce common reporting and lazy optional imports without moving business logic into CLI |

Existing tests cover target expansion, direct distribution, pipeline execution,
revision helpers, installer ownership, and exception rollback. Many dist mutation
tests mock filesystem operations. Add real-filesystem characterization before
replacing those paths; existing coverage is not evidence of multi-process safety.

## Proposed responsibility boundaries

A fresh design would compose these stages:

```text
command request + policy
    -> source/configuration inspection
    -> acquisition and transformation in private staging
    -> desired deployment plan
    -> locked reconciliation with current state
    -> revision preparation and publication
    -> inventory/status results and CLI rendering
```

Acquisition can happen before locking, but composing shared directories, choosing
revision numbers, checking retained dependencies, and accepting ownership must use
fresh state under the writer lock. A deployment plan describes desired changes,
not a promise that a preview remains valid after another writer publishes.

| Proposed location | Responsibility | Migration approach |
| --- | --- | --- |
| `dist/command.py`, `dist/planning.py`, `dist/configuration.py` | Distribution orchestration, target expansion, dist.json parsing | Update CLI dispatch, entry points, and repository callers; no legacy re-exports required |
| `sources/git.py`, `sources/projects.py`, `sources/packages.py` | Git inspection, local project preparation, pip-backed acquisition | Replace old source imports at their callers; no facade or common source base class required |
| `install/artifacts.py`, `install/wheels.py` | WheelArtifact identity/metadata and wheel staging/launchers | Rename Artifact and update its callers directly |
| `deployment.py` | Revision naming/preparation, activation, rollback, deletion coordination | Extend current implementation to files and version-only publication before using it for dist |
| `metadata/` | Versioned records, storage, legacy readers, discovery | No CLI, pip, Git, or envstack imports |
| `inventory.py`, `locking.py` | Filesystem observations, digest checks, shared writer coordination | Package dependency checks remain installer-specific |
| `pipeline.py`, `transform.py` | Transform validation and execution | Retain these modules for now; split pipeline into a package only when size/responsibilities justify it |
| `cli.py`, `reporting.py` | Parsing/dispatch and terminal/plain/JSON rendering | Preserve standalone shims and existing options |

Names are proposed. Prefer functions and data records unless an object needs
persistent state or a resource lifetime. `PackageIndexSource` is only justified if
it owns reusable acquisition behavior; a pip adapter does not need an empty wrapper
class. Rename wheel `Artifact` to `WheelArtifact` for precision. Use distinct
`DistributionTarget` and `RuntimeTarget` names instead of conflating the two existing
Target concepts. Keep source provenance separate from deployed file ownership.

## Proposed metadata layout and compatibility

Prefer small revision records under a single metadata directory over one large
JSON document containing all historical inventories:

```text
<deployment-root>/.distman/
    deployment.json                 # schema and stable deployment identity
    epoch                           # cache-compatible change token
    locks/write/owner.json           # proposed deployment writer lock
    targets/<target-id>/target.json  # destination and target kind
    targets/<target-id>/revisions/<revision-id>.json
    operations/<operation-id>.json   # intent, old/new links, recovery state
    verification/                   # optional supplemental snapshots
```

Revision records contain provenance, inventory, package ownership where applicable,
and paired library/bin identity. Keep package versions separate from deployment
revision identifiers. Store paths relative to their declared root where possible;
validate paths when loading. Record file kind, content digest and supported mode
bits, and link targets without following arbitrary links. Preserve existing payload
revision paths and active symlinks; moving metadata does not relocate payloads.

Active links remain the observable activation state. Readers compare them with
recorded revisions and pending operations, reporting discrepancies instead of
silently choosing one conflicting record. Metadata writes use temporary files in
the same filesystem and replacement, with durability behavior explicitly tested.
Back up metadata with payload revisions; centralization must not discard rollback
ownership or make a scan appear capable of restoring provenance.

Initially use one writer lock per deployment root for correctness, including nested
target conflicts. Independent deployments can proceed concurrently. For configured
paths outside that root, require an explicit owning deployment boundary; different
policies pointing at the same payload must use the same owner and lock. The exact
boundary mapping must be implemented and tested before supporting such writes under
the new protocol. Do not guess boundaries from a common path prefix. Shared mounts
with different host-local spellings must still address the same lock on storage.

New writes use only `.distman/` for distman-owned metadata. PyPA `.dist-info` remains
part of package contents. Read legacy sibling `.dist` files and embedded installer
receipts through dedicated adapters. If new metadata exists but is corrupt, fail
or report unknown; never silently fall back to a stale legacy record. When both
formats exist, validate their identity and report conflicts.

Migrate metadata for an existing revision under the lock before managing it with
the new writer. Leave historical payloads and receipts untouched; no dual writes.
A legacy dist record cannot establish historical file hashes or prove that its
source describes the currently active revision. Preserve such information as
unverified and require a known artifact/redeployment to establish a trusted baseline.
Reads of old formats remain supported through this migration; their removal needs
an explicitly documented later compatibility decision. Older binaries are not safe
concurrent writers once deployment metadata migrates, because they ignore the new
lock/state contract. Document a coordinated writer upgrade requirement.

## Publication and concurrency contract

1. Acquire/build/transform into private staging. Resolve all deployment boundaries.
2. Acquire writer locks in deterministic order with bounded waiting, an owner token,
   hostname, user, PID, and operation description. A timeout is an actionable failure.
3. Read current links and records, plan independent targets, validate ownership and
   dependencies, and present the final plan. If locks are released for confirmation,
   reacquire and revalidate; changed plans require renewed confirmation.
4. Prepare payload revisions and matching immutable metadata. Record operation
   intent and previous/new link identities before activation.
5. Switch links individually, record completion, and update invalidation state.
   On exceptions attempt restoration; retain diagnostic state if restoration fails.
6. Release only locks owned by this operation. Never break a remote lock merely
   because it is old; interrupted operations require evidence-based recovery.

A hard crash between steps must be diagnosable. Recovery inspects actual links and
prepared records under locks; it must not discard potentially active revisions.
Multiple targets are not globally atomic to applications. Readers retry or report
an operation in progress; application-wide atomic activation would need a separate
layout/design. The lock coordinates cooperating distman writers, not human edits.

Dist publish, reset/change-version, delete, install, and uninstall use this protocol.
Cache mutations need coordination for their own cache destination; cache reads of a
publishing deployment need consistent revision checks. Do not hold deployment writer
locks for an entire cache transfer. Validate lock creation, visibility, permissions,
rename behavior, disconnects, and crash recovery on actual supported network shares.
Local subprocess tests are necessary but cannot certify NFS/SMB behavior.

## Inventory and CLI contract

`list` reads recorded inventory and active links without hashing the whole tree.
`status` compares observations with revision baselines, collecting missing/modified/
untracked files, link changes, permissions, and metadata errors across targets.
Separate tolerant inspection from strict mutation preflight; neither inspection
operation repairs metadata. Dist and package targets share inventory models while
package dependency validation remains a separate concern.

Proposed new list/status options are root selection, target/package filters, and
`--json`; policy/environment selection must retain existing precedence. Define
stable JSON fields including schema version, observed revision, verification scope,
and diagnostics. For status, propose exit 0 for fully checked and unchanged, 1 for
changes, and 2 for incomplete/error results, with errors taking precedence. Preserve
existing command exit conventions until deliberately documented and tested.

Use stdout for requested results and stderr for diagnostics/progress. Centralize
confirmation and dry-run summaries; never prompt in JSON mode or silently accept
noninteractive destructive operations. Progress is optional and TTY-only, with a
plain mode and no ANSI output to redirected streams. Use existing tqdm where useful,
not invented percentages for pip resolution. Defer a full-screen TUI. Cancellation
must clean private staging, release owned locks, and preserve recovery evidence if
publication was interrupted.

## Implementation sequence and acceptance gates

1. **Characterize and extract:** real-filesystem tests for files/directories, LF
   normalization, modes, links, commit/content matching, version-only, rollback,
   deletion, wildcard targets, direct sources, and failed copies/transforms. Extract
   generic hashing, metadata reads, and lock primitives without changing storage.
2. **Coordinate writers:** integrate shared locking into every mutation path and
   isolate transform staging. Test two processes installing different packages into
   shared roots, dist/install contention, dependency changes, timeout, cancellation,
   and non-overlapping deployments. Existing locks need a transition strategy;
   do not allow old and new binaries to silently use different locks.
3. **Migrate publication/metadata:** file/directory/unlink publication and new package
   records are implemented; add dist migration and legacy initialization replacement.
   Test new-only writes,
   mixed formats, corrupt records, interruption at every publication boundary,
   rollback ownership, version-only records, cache invalidation, and custom roots.
4. **Finish lifecycle inspection:** adapt uninstall, then add list/status with
   CLI tests for complete/incomplete metadata, external edits preserving timestamps,
   independent environments, symlinks, and machine-readable results.
5. **Organize and polish:** move orchestration into the proposed packages, updating
   internal imports and testing executable entry points and configured transform
   callables, then unify reporting and terminal progress. Run the
   full regression suite and Black/isort/flake8; document remaining network and
   native-platform validation separately from passing local tests.

Start with extraction and characterization, not a directory rename or a database
migration. Recipe catalogs, dashboard, environment creation, source-build fallback,
and upgrade policy changes are outside these slices. Installed-state-aware upgrades
remain follow-up work outside the initial wheel-installer delivery boundary.


## First infrastructure slice: implementation progress

Implemented generic SHA-256/file inventories in `inventory.py` and shared locking in
`locking.py`. Dist publish, reset, change-version, and delete now use the same
per-deployment writer lock as install/uninstall. The lock waits for a configurable
bounded interval and records ownership. Combined local-project installs explicitly
delegate the held lock to their direct dist subprocess; unrelated children cannot
borrow it simply by inheriting normal environment settings. Receipt locations and
legacy dist info writes are unchanged in this slice.

For now installation holds the lock through acquisition, planning, confirmation,
and publication, avoiding stale acquisition-time state reads. Moving expensive
acquisition outside it requires separating acquisition from retained-state selection.
Existing per-target installer locks remain as transition checks and still fail
immediately on contention. They do not make older dist binaries cooperative.
Cache copying excludes live/stale writer lock directories so replicas cannot inherit
lock ownership. Transform staging uses unique temporary workspaces under BUILD_DIR and removes them
on success or exceptions. Read-only previews do not create deployment lock directories.

Regression coverage includes real filesystem revision selection, LF normalization,
permissions, version-only publication, rollback, deletion, transform failure cleanup,
process waiting, direct-child delegation, stale-lock refusal, and simultaneous
install/install/dist commands preserving both package records.

Still outstanding: cache-destination mutation coordination, explicit ownership for
custom roots shared across different policies, network-share validation, metadata
migration and journaled publication for legacy dist. Install/uninstall journals
and recovery are implemented as described below. Epoch update failures and legacy
copy/link failure handling remain part of the publication refactor. This slice serializes cooperating writers; it does not make
multi-target publication crash-atomic or certify network filesystem durability.


## Orchestration and recovery foundation

The distribution package now separates `dist/command.py` (CLI-independent command
coordination and standalone entry point) from `dist/distribution.py` (existing domain
operations). Internal callers and executable entry points use the new modules;
there is no Python import compatibility facade. `operations.operation` owns the root
writer lock and checks pending recovery state before command execution. Domain
methods no longer acquire the deployment-root lock themselves. Temporary per-target
locks remain until the coordinated metadata migration.

The directory publisher used by install, uninstall, and undist now persists incremental
snapshots
and pre-publication journals, flushes prepared contents and metadata, and inspects
actual links during rollback. `recover` previews or performs explicit restoration.
Legacy dist publication still needs migration; its command layer is coordinated but
its payload changes are not yet journaled. See the
[deployment state contract](deployment-state.md) for record semantics, recovery phases,
snapshot coverage, performance constraints, audit separation, and remaining work.
