# Installation roadmap: recipes and runtime environments

The wheel installer is implemented; named environments, recipes, and catalog
acquisition are planned. Installation uses explicit package requirements and path
policy rather than inferring packages from arbitrary envstack variables.

## Implementation status

| Area | Status | Evidence / remaining boundary |
|---|---|---|
| Direct PyPI requirements, pins, and requirements files | Implemented | pip-backed resolution and offline installer tests; preserving satisfactory installed versions is pending |
| Explicit upgrades | Planned | `-U` is not implemented |
| Package removal | Implemented | Receipt-owned revisions, version/runtime selection, dependency checks, and retained history |
| Local packaging, requirements-only, dist-only, and combined projects | Implemented | Exact-directory discovery; dist.json owns custom publication; project tests |
| Bounded architecture refactor | Implemented | Central CLI, shared policy, staged publication, compatibility wrappers; [architecture audit](architecture.md) |
| Configurable shared/runtime libraries and bins | Implemented | Five DISTMAN path templates, packaged fallback, active overrides, custom-layout tests |
| Complete wheel contents and ownership | Implemented | Metadata, resources, scripts, RECORD, namespaces, collision and integrity checks |
| Shared cross-platform generated entrypoints | Implemented | POSIX `tool` plus Windows `tool.exe`; combined/sequential installs, upgrades, removal, and idempotency tests |
| Retained dependency validation | Implemented | Configured runtime-root discovery, recorded target scopes, transitive shared dependencies, conflict rejection |
| Dry run and revision publication | Implemented with limits | No deployment writes on preview; rollback on caught activation failures; no global cross-root transaction |
| envstack activation | Examples implemented | Default, flat, and NumPy layouts; no automatic environment generation |
| `--env` environment installation / creation | Not implemented | Reserved flag rejects use; CLI meaning and isolation contract remain unresolved |
| Recipe selection, versioned environment files, catalog fetching | Proposed | No `--recipe` implementation or validated catalog releases |
| Archive/vendor acquisition | Proposed | Archive and vendor-specific acquisition need separate implementations |

The reserved `--env` option has no implementation. Installing an existing envstack
environment and creating an isolated environment are different operations; settle
the distinction before assigning that flag a meaning.

## Validation and remaining checks

Automated coverage lives in `tests/test_install.py` and the CLI, distribution,
and cache tests. It includes launcher coexistence, upgrades, ownership, rollback,
target-specific dependencies, and idempotency. Formatting and lint use Black,
isort, and flake8.

Live Linux checks include Pygments shared installation and NumPy 1.24.4 runtime
bin placement with `f2py` activation on Python 3.8. Windows and all-platform cowsay
previews succeeded against the existing Linux deployment.
Fixture tests cover both launcher forms and their metadata; native Windows command
execution is still unverified. These direct-install checks are not catalog certification.

Remaining validation and tooling work:

- [ ] Build a wheel, install `distman[install]` into a clean environment, and run
  outside the checkout using released dependencies rather than sibling envstack
  source. Verify packaged policy and console entrypoints.
- [ ] Verify the configured CI matrix: Linux Python 3.8–3.14, macOS and Windows
  Python 3.11. Local Linux results do not establish native platform support.
- [ ] Exercise actual Windows console/GUI launchers, interpreter lookup, arguments,
  paths with spaces, and exit codes. Cross-generation tests cannot prove execution.
- [ ] Add a Black/isort/flake8 CI gate; the current workflow runs pytest only.
- [ ] Confirm live NumPy reinstall idempotency; fixture idempotency is already covered.
- [ ] Decide whether named environments belong in the installer milestone or
  a separate milestone. The delivery boundary is not yet settled.

Recipe/catalog/archive work below remains proposed follow-up work. No new
manifest format, package solver, or hosted registry is required for the wheel installer.

## Install and upgrade semantics

The agreed contract uses the selected deployment's receipts as installed state.
Configuration through ENVPATH, distman.env, and active root/path overrides selects
that deployment. Packages do not need to be activated or importable by the Python
interpreter running distman. Development, test, production, and project deployments
may contain different packages and versions. Distinct names alone do not isolate
state: configurations pointing at the same library/bin roots share ownership.

| Operation | Intended behavior |
|---|---|
| `install PACKAGE` | Retain a satisfactory installed version; install missing packages and target variants |
| `install PACKAGE==VERSION` | Satisfy the explicit version, including an upgrade or downgrade when necessary |
| `install -U PACKAGE` / `--upgrade` | Select the latest compatible version permitted by the request and configured sources |
| Dependencies | Prefer satisfactory deployed versions; change them when required by the requested operation and reject conflicts with retained consumers |

This is not current behavior: resolution currently uses pip's `--ignore-installed`
and does not supply deployment receipts to the solve. An unpinned install can
therefore upgrade packages without an explicit upgrade flag. Adding an alias for
`-U` is insufficient; installed-state-aware resolution must be implemented using
pip tooling without introducing a separate solver.

Shared upgrades replace library contents in DISTMAN_SHARED_LIB_PATH and generated
entrypoints in DISTMAN_SHARED_BIN_PATH together, preserving supported launcher
variants and removing obsolete commands. Binary upgrades install into the configured
package/version boundary and its runtime library/bin paths, retaining older
version roots. Neither operation changes PYTHONPATH, PATH, package-version
environment variables, or envstack files automatically. Activation selects the
binary version separately.

Implementation and acceptance work:

- Discover managed state only through the selected policy; report the effective
  deployment and changed library/bin roots. Never infer deployed packages from
  distman's interpreter environment or a different tier's fallback paths.
- Preserve compatible deployed versions during pip resolution, including transitive
  dependencies. Document how version preferences are represented and how resolution
  changes them when a request requires it.
- For multiple installed binary versions, define deterministic reuse or an explicit
  ambiguity diagnostic; do not infer the active application version from installation.
- Preserve explicit requirements, constraints, target compatibility, and local-project
  rebuild behavior. An installed version does not justify skipping changed local source.
- Test no-upgrade installs when a newer wheel exists; explicit upgrades/downgrades;
  dependency changes only when required; retained-consumer conflicts; new target
  variants; launcher upgrades; dry runs; and idempotency.
- Test independent dev/test/prod/project roots with different versions, an unrelated
  package installed in distman's interpreter, and intentionally shared deployment paths.

## Package lifecycle follow-ups

`distman uninstall` is implemented. It composes revisions without selected owned
files, preserves unrelated distributions and rollback history, and rejects newly
broken dependencies in configured managed roots unless explicitly forced. Force
reports the affected consumers and does not bypass ownership or integrity checks. Unqualified names select all
versions/runtimes; exact pins and runtime filters narrow removal. Shared payloads
remain until their last runtime is removed, and empty roots retain managed receipts.
Dependencies are not automatically removed. See [package removal](../install.md#removing-packages)
for activation limits and legacy-receipt behavior.

Source-distribution fallback is another proposed extension. Named-package acquisition
currently requires prebuilt wheels, while local projects can be built into wheels.
Host source fallback could build a temporary wheel and reuse staging/publication.
Cross-target builds must not substitute host binaries for foreign artifacts; any
built wheel must match its selected targets. Build policy and a prebuilt-only option
need a separate design and tests.

## Ownership, verification, and recovery findings

Current behavior and limits:

- Legacy `.<target>.dist` contents have no parser in the current command paths.
  Their existence is checked when initializing destinations, and target deletion
  cleans them up. Verbose distribution display uses symlinks, revision filenames,
  filesystem timestamps, and Git metadata. Preserve these files during migration
  because their existence still affects behavior.
- `.distman-install.json` records ownership, package provenance/dependencies, and
  each managed revision's file paths, SHA-256 hashes, and permission modes. It is
  not a deployment-wide history log. Each historical revision retains its snapshot.
- Ownership inventories prevent collisions and identify exactly which files an
  upgrade or uninstall can remove. Hashes and modes detect changed or incomplete
  contents. These are separate responsibilities even though stored together.
- Hashing currently uses standard-library hashlib in streamed blocks. Validation
  hashes entire managed roots, sometimes repeatedly in one operation. Measure
  full-tree reads, metadata size, and revision duplication before scaling this
  design. Reuse verified results within a safe operation boundary where possible.
- Missing or invalid ownership metadata prevents updates to affected existing roots;
  force does not override this. Packages can still run because Python imports do
  not need the distman receipt. Uninstall discovery can skip versioned roots with
  missing receipts, which needs an explicit unknown/unmanaged-state diagnostic.
- There is no automatic repair or reconstruction command. Restoring a matching
  metadata backup or a complete known-good revision is currently required. A new
  file scan alone cannot recover historical ownership or original artifact identity.

### Deployment inventory and status (proposed)

Provide read-only operations for the deployment selected by installation policy,
including independent development, production, test, and project roots. These must
not depend on packages being activated on the running interpreter's PYTHONPATH.
Command names below are proposals, not implemented CLI options.

| Operation | Purpose | Baseline |
| --- | --- | --- |
| `distman list` | List installed packages and distributed targets, active revisions, versions, source when recorded, runtime targets, and deployment paths | Recorded ownership and distribution metadata; no full content scan |
| `distman status` | Report modified, missing, and untracked files, changed modes or active links, and missing/corrupt metadata | Recorded hashes, modes, ownership, and revision identity |
| Optional verification operation | Verify a saved snapshot or a transferred deployment, optionally using hashio | Explicitly selected snapshot and root mapping |

Keep package versions distinct from dist revision numbers. Include both installer
packages and ordinary dist targets, showing unavailable provenance explicitly.
Legacy metadata must be associated with the exact active revision before presenting
it as that revision's source; a new scan cannot establish historical provenance.
Legacy targets without content baselines must report verification as unavailable.

Status must collect diagnostics across roots even when normal install validation
would stop at the first damaged receipt. Do not report a root as clean when its
metadata is missing, invalid, or insufficient. Hash managed file contents for a
full status check so edits with preserved timestamps are detected. An optional fast
mode must identify its weaker guarantees. The cache generation marker cannot detect
external edits because those edits do not update it.

Distinguish added/untracked files from owned files; a scan must not claim ownership.
Document exclusions such as generated bytecode and verification output. Compare
symlink identity separately from target contents and detect changes during a scan
rather than presenting an inconsistent observation as clean. Report divergence from
the baseline without claiming whether a human or another program caused it.

Listing and status must not repair metadata, refresh baselines, or modify deployment
files. Define machine-readable output and exit statuses for clean, changed, and
unverifiable/error results. Test missing and edited files, preserved timestamps,
mode/link changes, corrupt metadata, untracked files, and independent policy roots.

### Supplemental hashio snapshots

Keep hashlib as the baseline; evaluate optional hashio integration for independent
inventory, verification, transfer checks, and diagnostics. Hashio's manifest remains
supplemental evidence rather than a replacement for package ownership records.
Its own format can be imported through an adapter without becoming distman's
installation schema or a new required configuration file.

Store optional snapshots beneath `${DEPLOY_ROOT}/.distman/verification/`, scoped by
managed root and revision, with an explicit snapshot destination for transfer use.
Exclude snapshot output from its own inventory. Keep the saved baseline immutable
during verification; creating or accepting a replacement baseline is a separate,
explicit operation. Missing hashio snapshots must not prevent installation, listing,
or receipt-based status checks.

Dependency review found that the published hashio 0.5.1 distribution declares
`lxml==5.3.0`, `tqdm==4.67.1`, and `xxhash==3.5.0`. The source tree's dependency
specification differs: it selects lxml 5.4.0 below Python 3.14 and 6.0.4 otherwise.
Use [published package metadata](https://pypi.org/pypi/hashio/0.5.1/json) when assessing
what installation actually requires, rather than assuming the source tree matches
the release. lxml and xxhash introduce native dependencies; tqdm is already used by
distman, but the exact pin narrows compatibility. Check wheel availability and
resolver compatibility across supported Python/platform targets before offering an
optional integration. No dependency compatibility matrix has been validated yet.
Keep standard-library hashlib for core status checks and do not make hashio a
required dependency. An external command or optional extra can supply the adapter;
choose that packaging after the compatibility checks.

The supplied sample contains 804 file entries, approximately 290 KB of JSON, with
xxh64 hashes, sizes, timestamps, inode/device identifiers, and names. Every entry
uses an absolute path under the legacy deployment's `lib/python/versions` tree;
it does not cover the installer's separate `tools/lib/python` root. No permission
modes or active symlink targets are recorded in this sample. The reported roughly
one-second scan is useful evidence for that workload, not a like-for-like benchmark
against strict installer validation.

For portable snapshots, use relative paths and an explicit source-root/destination-root
mapping. Hashio documents relative output and `--start` for relocation. Compare
content hashes and relevant permissions when transferring; inode/device identifiers,
access times, and change times need not remain equal across filesystems. Capture
active links, revision layout, and ownership records as part of a deployment backup.
Byte verification alone does not make an installation relocatable: current external
script RECORD entries and receipts may contain absolute paths, and application
activation configuration may need explicit updates.

Hashio's documented verification can accept matching modification times rather
than rehashing bytes. Define separate fast verification and full byte-verification
modes; do not treat cached/stat-only checks as equivalent to a strict integrity scan.
Test preserved timestamps with changed bytes, exclusions, symlinks, executable modes,
and missing/extra files before using it in destructive-operation preflight.
See the [hashio documentation](https://pypi.org/project/hashio/) for its API and
verification behavior.

Preserve the original snapshot before rescanning. Regeneration describes the files
as they exist now; it does not prove that changes were intended or restore missing
provenance. Keep wheel artifact identity separate from observed filesystem state.
A checksum documents content relative to a baseline, not who installed it or why.

### Recovery and rebuild workflow (proposed)

Design explicit inspect/verify, metadata recovery, and artifact rebuild operations;
command names remain undecided. Recovery must be usable when normal install
preflight cannot load its receipts.

1. Report missing/corrupt records, changed/missing/extra files, ownership ambiguity,
   and inconsistent active links without making changes.
2. Prefer restoring metadata from a backup for the exact revision, or restoring a
   complete known-good revision. Validate ownership and content before activation.
3. Where backups are unavailable, use installed `.dist-info/METADATA` and `RECORD`
   plus independently verified original wheels as reconstruction evidence. Account
   for generated launchers, external bins, target selection, and file modes. Do not
   invent missing provenance or claim unowned files from a directory scan.
4. If reconstruction is ambiguous, report unresolved ownership and rebuild from
   selected artifacts into a separate clean managed root. Keep the existing tree
   available until the rebuilt deployment is validated and explicitly activated.
5. Preview recovery changes, preserve damaged metadata for diagnosis, and publish
   replacements with the same locking and rollback guarantees as installation.

Acceptance coverage must include missing receipts, invalid JSON, stale metadata,
modified files with preserved timestamps, overlapping ownership, missing artifacts,
independent deployment environments, paired library/bin restoration, interrupted
recovery, and transfer to a different root. Preserve backups independently of the
active deployment; a single metadata file must not become the only recovery source.

## Snapshots and recovery direction

Use the [deployment state contract](deployment-state.md) for incremental timestamped
snapshots, pre-publication journals, optional audit manifests, and failure recovery.
The installer directory publisher has the journaled foundation; legacy dist and
historical metadata migration remain outstanding. Normal operations record changed
targets without scanning the whole deployment. Whole-root rollback, portable export,
and optional hashio audits build on this model but are not implemented commands.

## Unified deployment metadata (proposed)

Consolidate legacy target metadata, installer ownership, and the cache generation
marker behind one versioned metadata contract. The concrete proposal uses target
and immutable revision records beneath `${DEPLOY_ROOT}/.distman/`, with operation
records for interrupted publication and a cache invalidation token. See the
[architecture audit](architecture.md#proposed-metadata-layout-and-compatibility)
for the layout, legacy read policy, writer upgrade requirement, and implementation
sequence. This supersedes the initial single `state.json` sketch; current metadata
locations remain unchanged. Benchmark inventory storage and test recovery before
migration. Optional verification snapshots remain separate from ownership records.

The central state must retain immutable ownership inventories for historical
revisions, so rollback still restores the correct package/dependency information.
Define how active symlinks and state stay consistent after interrupted publication,
and serialize concurrent updates from dist, install, uninstall, and cache tooling.
Explicitly address custom destinations outside DEPLOY_ROOT and destinations shared
by multiple policies; a root-local index must not create conflicting ownership.
Measure metadata size and cache-check costs before finalizing a single-file format.

Provide backward-compatible reads of `.<target>.dist`, `.distman-install.json`, and
`.distman/epoch`, with a tested migration before stopping legacy writes. Test
rollback, cache freshness, concurrent writers, interrupted operations, and old
deployments. Consolidation is broader than renaming the installer receipt file.

### Shared network deployments (proposed)

Multi-host access to shared network storage is a primary deployment use case.
Install/uninstall and modifying dist operations now share a deployment-root writer
lock with bounded waiting and owner diagnostics. Planning reads state after acquiring
that lock. Transitional per-target locks still reject older concurrent installers
immediately. Cache-destination coordination remains outstanding; see the
[implementation progress](architecture.md#first-infrastructure-slice-implementation-progress).
Address overlapping paths, aliases, cross-user permissions, stale locks, disconnects,
and interrupted publication. Do not reclaim a lock solely because it is old or a
remote PID cannot be inspected. Validate concurrency on supported NFS/SMB setups;
a lock file alone does not establish network safety.

Avoid a required multi-client SQLite database on the deployment share. SQLite
explains its [network filesystem caveats](https://www.sqlite.org/useovernet.html)
and [WAL same-host requirement](https://www.sqlite.org/wal.html). Preserve portable
metadata and operation without a database service. Filesystem publication still
needs coordination and recovery regardless of the metadata storage format.

### Dashboard direction (long-term, optional)

A deployment dashboard is a long-term goal, not an installer requirement or a
release gate. A separate service could mount a deployment read-only and build a
query index, optionally using PostgreSQL and containers. Its database would be
rebuildable from deployment metadata; CLI operation would not depend on it.
No dashboard implementation or service dependency is planned for this work.

Keep metadata discoverable and versioned so a future service can bootstrap by
pointing at a managed deployment root. Legacy or unmanaged files must show unknown
ownership/provenance when evidence is unavailable. A future index could poll small
change markers and refresh changed revision records instead of repeatedly scanning
every file. Marker publication needs a concurrency/recovery contract; the current
epoch is not a durable event journal. Periodic metadata reconciliation would still
be needed after missed updates, interrupted publication, or rollback.

Inventory freshness and content verification are separate: external edits do not
update distman change markers. Scheduled or requested status scans would detect
those changes. Show last successful index refresh and content verification times,
coverage, and stale/unreachable states rather than promising instantaneous status.
These are future design considerations, not reasons to migrate storage now.

## Proposed release scope

Make package installation the focus of 0.9: the wheel installer, explicit install
versus upgrade behavior, shared/runtime launcher support, documentation, and the
remaining validation gates. Associate the package-installation work with that
milestone. This is a release recommendation, not a record of tracker changes.

Review existing milestone items for concrete dependencies before moving them.
General distribution operations, target hashing, diff, configuration includes,
post-deployment steps, and file-sequence support can move to a later milestone
unless they fix a demonstrated installer or regression requirement. Existing internal
ownership hashes do not establish completion of a general target-hashing feature.

Package uninstall is implemented. Proposed subsequent work is host source-build fallback, followed
by named-environment creation and recipe/catalog integration. These features can be
tracked separately; full environment lifecycle management is not required to ship
the wheel installer. Exact later release assignments remain open.

## Independent installation operations

Direct package installation remains available for compatible packages from PyPI or
configured pip sources, whether or not a catalog recipe exists:

```bash
# Existing commands
distman install numpy
distman install 'numpy==2.2.6'
distman install -r requirements.txt
distman install ./myapp
```

Direct installation resolves and publishes wheels; it does not promise a curated
runtime environment file. Local projects retain their existing packaging,
requirements, and dist.json behavior.

Recipe installation explicitly selects an integration maintained by a project,
a studio, or the public catalog:

```bash
# Proposed commands
distman install --recipe numpy@2.2.6
distman install --recipe numpy@2.2.6 --platform linux --python 3.11
distman install --recipe ./recipes/numpy/2.2.6 --dryrun
```

Use an explicit recipe mode rather than adding another meaning to bare names.
`numpy==2.2.6` remains pip requirement syntax; `numpy@2.2.6` is a catalog selector
only with `--recipe`. Initially require an exact catalog package version. Recipe
mode must reject combinations with positional package/project inputs, `--project`,
`-r`, or `--env`; its requirements come from the recipe. Missing recipes fail
clearly and never silently fall back to PyPI. Missing catalog entries never block
direct package installs.

Both modes reuse staging, ownership validation, policy, and publication. Shared
options include runtime selection, dry run, confirmation, and verbosity. Package
specific source mappings belong in recipes, not new per-package CLI flags.

## Catalog and authoring

Start with a separate Git repository named `distman-packages`, not a hosted
PyPI-like service or a growing collection embedded in distman's source tree.
Support local recipe directories first, then fetching a catalog at an explicit
Git revision. Public and private catalogs should use the same contract; catalog
configuration and revision-selection CLI syntax remain to be designed.

A wheel recipe is a directory of existing formats:

```text
numpy/
  2.2.6/
    requirements.txt
    numpy.env
    dist.json
```

- `requirements.txt` pins acquisition, for example `numpy==2.2.6`.
- `numpy.env` supplies ordinary envstack variables and runtime paths.
- `dist.json` publishes environment files and any custom outputs.

Users can author and test these files locally. Projects can eventually ship the
same files themselves. Loading integrations bundled in a downloaded wheel needs
an explicit discovery contract; ordinary wheel installation must not implicitly
execute a newly discovered recipe.

Do not introduce a separate recipe JSON format for wheel installation. Investigate
an acquisition extension to dist.json for archives only after examining concrete
non-wheel cases. Existing dist.json does not currently describe archive fetching.

## Package versions and recipe revisions

Version the complete recipe by upstream package version. `numpy@2.2.6` identifies
its environment, requirements, and distribution configuration together. A Git
revision separately identifies corrections to that recipe. Record both identities
with installation provenance so a recipe can be reproduced after catalog updates.

Validate that the pinned package and the environment's default version agree.
Generation or validation should prevent these values drifting. Preserve runtime
overrides such as NUMPY_VERSION and NUMPY_ROOT.

Multiple installed package versions must retain their corresponding environment
files. Define an explicit default for an unqualified `include: [numpy]` and a way
to select a particular version using existing envstack discovery/include behavior.
The deployed layout and selection convention need validation before implementation;
this plan does not assume envstack understands `numpy@2.2.6` includes. Package
versions, recipe revisions, and distman deployment revisions remain distinct.

## Runtime environments

Use an ordinary package environment file such as numpy.env: the package version has an overridable
default, the root incorporates that version, and platform-specific PYTHONPATH
entries use runtime variables. Python version can be evaluated at activation;
the `python` executable used must correspond to the application's interpreter.
Embedded applications may need explicit runtime configuration.

Distman's existing [installer variable contract](../install.md#destinations) defines
its roots, path templates, and override precedence. Recipes must use that policy
rather than inventing independent deployment roots. Any new built-in variables
must document their meaning, defaults, scope, and precedence.

Envstack remains an environment-variable tool. No package resolver, new envstack
manifest, or package-specific envstack behavior is required. Planned environment generation would produce
routine templates from configured installation paths; maintainers can author
application-specific settings. Include architecture and the current lib/python
suffix in generated paths.

Application authors still select includes and version overrides. Template updates
must not overwrite user customization without a defined ownership policy. Also,
the existing shared pure-Python root permits one active version per distribution;
versioned recipes do not create application dependency isolation.

## Shared libraries, scripts, and activation policy

Keep shared pure-Python packages as the default policy. A shared root represents
one compatible environment: one active version per distribution, compatible
requirements and supported runtimes, and no conflicting files or command names.
Its libraries and entrypoints must upgrade and roll back together. Compatibility
is an operator policy backed by metadata checks, not a guarantee that arbitrary
applications or native dependencies work together. Pure wheel tags alone do not
prove compatibility with every Python version or platform-specific launcher.
Validation follows each package's recorded targets and reachable dependencies;
adding Windows cowsay does not require Windows dependencies for Linux-only sview.
Shared upgrades must still satisfy existing consumers on their recorded targets.

Users may instead configure package/version-specific pure roots or one shared
root per runtime. Do not require long per-package PATH and PYTHONPATH lists for
the default shared layout. Recipes must honor configured paths, not assume a
single deployment layout.

Implemented script locations, alongside the existing import layout:

| Payload | Import directory | Scripts directory |
|---|---|---|
| Shared pure Python | `{TOOLS_ROOT}/lib/python` | `{TOOLS_ROOT}/bin` |
| Binary/runtime-specific | `${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/lib/python` | `${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/bin` |

`DISTMAN_TARGET_PATH` defaults to `{TOOLS_ROOT}/{package}/{version}`. Referencing
it as `${DISTMAN_TARGET_PATH}` avoids repeated layout definitions: envstack expands
the variable first, then pathbase formats package/runtime tokens. The runtime
library template uses this composition; recursive pathbase formatting is not needed.
`DISTMAN_SHARED_BIN_PATH` and `DISTMAN_RUNTIME_BIN_PATH` select scripts from shared
and runtime-specific wheels respectively. They apply to generated console/GUI
entrypoints and wheel-provided scripts, including local packaging projects without
dist.json. Libraries, scripts, metadata, resources, and other wheel contents do not
require an additional manifest.

Generated entrypoints support `--platform all` from a Linux host: POSIX and Windows
launchers coexist in the shared bin. Sequential target additions preserve the
existing variants; upgrades regenerate all recorded variants and remove obsolete
commands. Windows launcher ZIP timestamps are normalized for idempotency. This does
not translate arbitrary wheel-provided scripts between platforms; conflicting
script payloads still need separate paths. Native Windows execution remains a
validation item.

Runtime environment files add the resolved import directory to PYTHONPATH and the
resolved scripts directory to PATH. For shared pure packages, a common environment
included by applications can add `${TOOLS_ROOT}/lib/python` and `${TOOLS_ROOT}/bin`
once. A studio's distman.env can carry those settings if used for runtime
activation. Binary package environments select package version, platform,
architecture, and Python runtime. Add the scripts directory,
not TOOLS_ROOT itself, and never export an unresolved artifact template to PATH.
A matching Python interpreter must still be available to launch the scripts.

Loading distman.env as installation policy does not activate the shell. The current
repository distman.env adds only `${DEPLOY_ROOT}/bin` and
`${DEPLOY_ROOT}/lib/python`. The default example's tools.env adds
DISTMAN_SHARED_LIB_PATH and DISTMAN_SHARED_BIN_PATH; the flat example carries those
activation settings in default.env. Installing a package does not add these paths
to the caller's shell.

Implementation boundary: shared libraries and bins are separate managed revisions,
validated and published together with rollback on caught activation failures.
Their receipts link the artifacts and detect mismatched library/bin versions.
Manual rollback must select matching revisions of both roots. Runtime scripts
inside DISTMAN_TARGET_PATH share the package revision directly. Cross-root
activation is not globally atomic or durable across process termination.
Existing unmanaged bin directories and changing previously recorded layout mappings
are rejected. General migration and coexistence with legacy bin shims remain future
work. TOOLS_ROOT itself is not one managed boundary because binary package roots
also live beneath it.

## Named environments and conflict recovery (scope decision pending)

Shared/runtime bin deployment is complete. The next feature proposal is an explicit
isolated-install and activation workflow; its delivery milestone is unresolved. Candidate interfaces are `distman install --env NAME` and
an environment-creation command; settle the CLI before implementation. The existing
reserved --env flag is not yet functional and must not silently change meanings.

Distman installs and validates files; envstack composes ordinary environment
variables. Named environments should use separate managed library/bin roots and
produce an envstack file for those roots, with no third user-authored manifest.
Record ownership in internal deployment receipts, keep distman.env as path policy,
and honor a project's optional dist.json custom targets.

A conflict with retained shared dependencies must leave the deployment unchanged,
report the requested and conflicting versions, and suggest an explicit isolated
installation. Never silently isolate or choose another environment. The resulting
envstack file must be discoverable through a documented ENVPATH configuration and
come with an exact activation/launch command. Support direct PyPI installs and
local projects as well as future recipes; users must not author path templates
per dependency.

Specify environment names/locations, root overrides, existing-file ownership,
activation discovery, generated-config updates, and coordinated rollback.
Do not modify studio default.env or rely on missing-stack fallback implicitly.
A distinct root alone is not interpreter isolation: inherited shared PYTHONPATH
entries and interpreter site-packages can still expose incompatible dependencies.
Decide whether the workflow filters inherited paths or optionally provisions a
virtual environment, and state its isolation guarantees. Test conflict recovery,
imports and entrypoints, explicit interpreter selection, and unchanged shared
packages before considering this milestone complete.

## Replacing custom acquisition scaffolding

| Existing responsibility | Planned replacement |
|---|---|
| Repeated wheel fetch scripts | Current pip-backed wheel acquisition and complete wheel installation |
| Repeated platform/Python selection | Shared runtime selection |
| Downloaded staging trees | Temporary acquisition and staging |
| Central tool distribution mappings | Recipe-local dist.json targets |
| Package environment files | Versioned recipe content, generated where routine |
| Platform-specific binary downloads | Shared archive acquisition configured by recipes |
| Exceptional vendor APIs/builds | Explicit adapter or recipe script interface, only when needed |

Aim to replace all routine fetch/deploy scaffolding, preserving the package-specific
knowledge in recipes. Do not preserve partial wheel extraction as the default:
wheel metadata and dependencies must remain intact. Source builds, custom APIs,
and special transformations require separate design rather than arbitrary new
install flags.

## Recipe and catalog delivery (proposed)

1. Prove the local wheel-recipe contract with NumPy 2.2.6 and a second wheel package.
   Test import through the deployed environment, runtime overrides, dependencies,
   custom path policy, and unchanged direct PyPI behavior.
2. Define versioned environment deployment and default selection. Test coexisting
   versions, recipe corrections, rollback, and user-owned configuration.
3. Add Git catalog acquisition with revision provenance and public/private sources.
   Test missing entries, invalid recipes, and explicit source selection.
4. Use FFmpeg to establish archive requirements: different platform providers,
   exact artifact identity, hashes, extraction, and publication. Download providers
   may offer builds that differ from a requested patch version; preserve actual
   artifact identity rather than claiming an exact match.
5. Migrate additional package integrations as their acquisition needs are covered.

Keep existing transaction limits visible: package publication and legacy dist
publication are separate operations today. Recipe support must not claim atomic
installation of packages and environment files without implementing it.

The public [package catalog](../packages.md) lists validated recipe versions and target
coverage once available. It is not an allowlist for direct Python installation.
