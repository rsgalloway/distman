# Installing Python distributions

`distman install` installs Python packages and local projects into versioned
deployment directories. Its runtime dependencies are included by a normal install:

```bash
python -m pip install distman
```

The former `distman[install]` extra remains accepted for command and automation
compatibility, but it is no longer required.

Direct Python installation does not require a catalog recipe. See the
[package catalog](packages.md) for the planned, separate recipe workflow.

## Usage

```bash
distman install numpy
distman install 'numpy==2.2.6' --python 3.11
distman install -r requirements.txt
distman install . --dryrun
distman install ./myapp --yes
distman install sample --no-index --find-links ./wheelhouse
```

Bare names are pip requirements. Use an explicit path (`.`, `./myapp`, or an
absolute path) to select a local project; `--project` is also supported.

| Project contents | Behavior |
|---|---|
| Packaging metadata | Build and install the project and its dependencies |
| `requirements.txt` | Install the listed dependencies |
| `dist.json` | Run the existing distribution targets |
| Combined inputs | Install dependencies and let `dist.json` publish the project |

Packaging metadata means a `pyproject.toml` project/build-system declaration or
`setup.py`. Only the selected directory is inspected. Local distribution retains
`dist`'s Git checks; `dist.json` remains responsible for preparing target outputs.

| Option | Effect |
|---|---|
| `-d`, `--dryrun` | Preview without changing deployment directories |
| `-y`, `--yes` | Accept publication without prompting |
| `-f`, `--force` | Create a revision even when unchanged; also forward force to local `dist` targets |
| `-v`, `--verbose` | Show pip output |

When publication requires confirmation, the prompt displays `[Y/n]`; press Enter to
publish or enter `n` to cancel. `--yes` remains useful for noninteractive commands.
Uninstall and recovery continue to require an explicit answer.

Dry runs still resolve, download, and stage packages. Local builds run on a
temporary source copy, excluding Git metadata, common virtualenv directories,
and broken symlinks. Backends requiring Git metadata may not support preview.

## Version selection and compatibility

An unpinned `distman install PACKAGE` resolves against the configured package
sources and can install a newer version, including changes to dependencies. It
does not yet prefer satisfactory versions already present in the deployment.
Use explicit pins or a pinned requirements file when versions must remain fixed.
`-U` / `--upgrade` and installed-state-aware resolution are planned together.

The standalone `distcache` command and its bin shims have been removed; use
`distman cache`. The `dist` command remains available. Internal Python imports may
change; executable commands, configuration, and documented transform callables
are the supported integration points.

## Runtime selection

By default, packages target the running CPython interpreter, including its patch
version for compatibility checks. Runtime paths still use major.minor. Explicit
major.minor selections use patch zero as their compatibility baseline. Select other runtimes
without installing their interpreters:

```bash
distman install 'numpy==2.2.6' --platform linux,windows,darwin \
    --arch x86_64 --python 3.11,3.12 --dryrun
distman install 'numpy==2.2.6' --platform darwin --arch arm64 --python 3.12
```

Platforms are `linux`, `windows`, and `darwin`; `all` selects all three.
Architecture defaults to the host. Explicit Linux/Windows targets support x86_64;
macOS supports x86_64 and arm64. Explicit compatibility baselines are glibc 2.28
and macOS 11.

Dependencies require compatible prebuilt wheels. Local projects build on the
host; native cross-compilation is not provided. Foreign dependency-marker and
extras resolution is limited, so use tested pinned requirements for complex
cross-platform installs. `--env` selection is not yet supported.

## Zero-configuration layout

Without `ENVPATH`, a discoverable `distman.env`, or active path overrides, every
deployment command uses the packaged policy and resolves this root:

```text
~/.local/distman/prod
```

Pure Python libraries are installed beneath
`~/.local/distman/prod/tools/lib/python`, their commands beneath
`~/.local/distman/prod/tools/bin`, and native packages beneath versioned
package/runtime directories in `~/.local/distman/prod/tools`. Installation does not
change the current shell. A simple POSIX activation for shared pure packages is:

```bash
export PYTHONPATH="$HOME/.local/distman/prod/tools/lib/python${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$HOME/.local/distman/prod/tools/bin:$PATH"
```

Use an envstack example or studio policy before installing when that personal `prod`
root is not the intended destination. Always preview a new policy with `--dryrun`.

## Destinations

Distman defines an opinionated environment-variable contract for deployment.
These names and their meanings belong to distman; envstack supplies loading and
variable expansion without assigning them package or deployment semantics.

| Variable | Meaning | Packaged default |
|---|---|---|
| `ROOT` | Base directory used to derive the deployment root | `${HOME}/.local/distman` |
| `ENV` | Deployment tier used in the default root | `prod` |
| `DEPLOY_ROOT` | Deployment root reported by the installer and used for deployment metadata | `${ROOT}/${ENV}` |
| `TOOLS_ROOT` | Base directory used by the default package templates | `${DEPLOY_ROOT}/tools` |
| `DISTMAN_SHARED_LIB_PATH` | Shared library import root for platform-independent wheels | See templates below |
| `DISTMAN_RUNTIME_LIB_PATH` | Import path for a package/runtime variant | See templates below |
| `DISTMAN_SHARED_BIN_PATH` | Executables from platform-independent wheels | `${TOOLS_ROOT}/bin` |
| `DISTMAN_RUNTIME_BIN_PATH` | Executables for a package/runtime variant | See templates below |
| `DISTMAN_TARGET_PATH` | Versioned publication boundary containing binary variants | See templates below |

Policy is resolved in this order, with later values taking precedence:

1. Packaged `distman/resources/distman.env` supplies baseline defaults for `dist`,
   `undist`, `install`, and `uninstall`, and includes `default` through envstack's
   normal lookup rules.
2. Discoverable `distman.env`, if present, overlays those values.
3. Nonempty active `ROOT`, `ENV`, `DEPLOY_ROOT`, and `TOOLS_ROOT` values override
   file policy. All active `DISTMAN_*` values are also overlaid.
4. Envstack expands the combined values; [pathbase](https://rsgalloway.github.io/pathbase/) formats the package templates.

`DISTMAN_*` is the policy override namespace; arbitrary names in that namespace
do not automatically gain behavior. The five path variables above are the
package installer's recognized layout controls. Other variables can be referenced by
custom templates without becoming built-in settings.

An active `DEPLOY_ROOT` wins even if `ROOT` or `ENV` changes. Likewise, custom
`TOOLS_ROOT` or path templates can place packages outside `DEPLOY_ROOT`; that
root is not a containment restriction. `ENVPATH` controls policy discovery,
while `PATH` and `PYTHONPATH` control runtime lookup, not deployment destinations.
Resolving policy does not modify the caller's environment.

The packaged file is an immutable application default. To customize it, copy
[the default environment example](examples.md) into a directory on
`ENVPATH`, or author a smaller `distman.env` containing only overrides. For example:

```yaml
all:
  ROOT: ${HOME}/deployments
  ENV: ${ENV:-dev}
  DEPLOY_ROOT: ${DEPLOY_ROOT:-${ROOT}/${ENV}}
```

All deployment commands then resolve the same root and manifest tokens from that
file. Editing the copy does not modify the installed distman package.

For example, select a deployment root for one invocation:

```bash
DEPLOY_ROOT=/studio/apps/dev distman install numpy --dryrun
```

`DISTMAN_SHARED_LIB_PATH` selects platform-independent wheels; runtime-specific
wheels use `DISTMAN_RUNTIME_LIB_PATH`. The names describe their default layout.
You can configure both to share a root for a single runtime, as shown below.

The default shared path is resolved by envstack, so runtime environments can reuse
it directly without a second library-root variable:

```yaml
all:
  DISTMAN_SHARED_LIB_PATH: ${TOOLS_ROOT}/lib/python
  PYTHONPATH: ${DISTMAN_SHARED_LIB_PATH}:${PYTHONPATH}
```

If a custom shared path contains artifact/runtime tokens such as `{package}` or
`{python_version}`, it must be formatted for the selected package/runtime before
being added to PYTHONPATH; envstack alone does not expand those tokens.

Default path templates are:

```yaml
all:
  TOOLS_ROOT: ${TOOLS_ROOT:-${DEPLOY_ROOT}/tools}
  DISTMAN_TARGET_PATH: '{TOOLS_ROOT}/{package}/{version}'
  DISTMAN_SHARED_LIB_PATH: ${TOOLS_ROOT}/lib/python
  DISTMAN_RUNTIME_LIB_PATH: '${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/lib/python'
  DISTMAN_SHARED_BIN_PATH: ${TOOLS_ROOT}/bin
  DISTMAN_RUNTIME_BIN_PATH: '${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/bin'
```

Envstack expands `${DISTMAN_TARGET_PATH}` into the runtime library template first;
[pathbase](https://rsgalloway.github.io/pathbase/) then formats its package and runtime
tokens. Overriding the target template therefore also relocates the default runtime
library path without repeating the package/version prefix.

Path templates use brace-delimited tokens, expanded by [pathbase](https://rsgalloway.github.io/pathbase/):

| Token | Meaning | Example |
|---|---|---|
| `{ROOT}` | Resolved base deployment directory | `/studio` |
| `{ENV}` | Resolved deployment tier | `dev` |
| `{DEPLOY_ROOT}` | Resolved deployment root | `/studio/dev` |
| `{TOOLS_ROOT}` | Resolved third-party tools directory | `/studio/dev/tools` |
| `{package}` | Normalized distribution name, not necessarily its Python import name | `pyside6` |
| `{version}` | Upstream package version, not the distman revision number | `2.2.6` |
| `{platform}` | Selected target platform | `linux`, `windows`, `darwin` |
| `{arch}` | Selected target architecture | `x86_64`, `arm64` |
| `{python_version}` | Selected Python major.minor version | `3.11` |

Other resolved policy variables can also be used as tokens. The five package/runtime
tokens (`package`, `version`, `platform`, `arch`, `python_version`) come from the
selected artifact and target, overriding policy variables with those names.
Unresolved tokens cause installation to fail.

Use `${VARIABLE}` for envstack variable expansion in environment values and
`{token}` for [pathbase](https://rsgalloway.github.io/pathbase/) formatting in the `DISTMAN_*_PATH` templates.

Pure wheels share an import root; binary wheels use runtime-specific paths.
Paths must be absolute. Packages can share an identical publication root, but
distinct roots must not nest inside one another. Shared roots require compatible
package versions and contents across selected runtimes.

Add the selected library roots to `PYTHONPATH` and bin directories to `PATH`.
POSIX launchers use `python3` from PATH; Windows launchers use `python.exe`.
Installation policy loading does not activate the parent shell. A shared runtime
stack can use:

```yaml
all:
  PYTHONPATH: ${DISTMAN_SHARED_LIB_PATH}:${PYTHONPATH}
  PATH: ${DISTMAN_SHARED_BIN_PATH}:${PATH}
```

Generated console/GUI entrypoints and wheel-provided scripts use the bin templates.
Package resources, metadata, bundled native libraries, headers, and other wheel
data retain their library-side layout. No dist.json or recipe is needed for these
standard wheel contents, including a locally built project's wheel.

Shared library and bin paths are ordinary container directories. Distman versions
each wheel-owned top-level package, module, metadata directory, data target, and
command independently beneath the container. For example:

```text
tools/lib/python/
    numpy -> versions/numpy.0
    numpy-2.2.6.dist-info -> versions/numpy-2.2.6.dist-info.0
tools/bin/
    f2py -> versions/f2py.0
```

A package upgrade switches only that package's targets. Other packages and unmanaged
neighbors in the shared container are left alone. Namespace package directories are
ordinary containers too; their owned children are independent targets so several
distributions can share a namespace. A binary package/version path remains one
package-exclusive target containing its selected runtime layouts.

Generated entrypoints can coexist in a shared bin: Linux/macOS use `tool`, Windows
uses `tool.exe`. `--platform all` generates both forms, including when run on Linux.
Installing another platform later preserves existing launcher variants; upgrading
regenerates recorded variants and removes obsolete commands. A command, module, or
package target already owned by another distribution is a collision. Unmanaged files
with other names in a shared container are preserved.

Dependency validation follows each package's recorded runtimes and considers all
versions in package-exclusive binary roots. Adding Windows support for one package
does not assign Windows dependencies to an unrelated Linux-only application. A
shared upgrade must still satisfy retained consumers on their recorded runtimes.
Metadata checks do not guarantee application compatibility. Changing a recorded
package's library/bin mapping requires reinstalling it into the intended deployment;
automatic migration between arbitrary layouts is not implemented.

## distman.env examples

For complete configurations including `default.env`, runtime paths, and launch
commands, see the [installation and activation examples](examples.md).

Save one of these configurations as `distman.env` in a directory discoverable
through envstack (for example, a directory on `ENVPATH`). Exported policy variables
still take precedence over these file values. Preview with `distman install numpy
--dryrun` before publishing.

### Change the tools location

Keep the default layout, but move third-party packages under `/etc/tools`:

```yaml
all:
  TOOLS_ROOT: /etc/tools
```

Pure packages use `/etc/tools/lib/python`; binary packages retain the default
package/version/platform/architecture/Python hierarchy beneath `/etc/tools`.
The installing user needs write access to the destination.

### One flat root for one runtime

Put both pure and binary packages into `/etc/tools/lib/python`:

```yaml
all:
  TOOLS_ROOT: /etc/tools
  DISTMAN_SHARED_LIB_PATH: ${TOOLS_ROOT}/lib/python
  DISTMAN_RUNTIME_LIB_PATH: '{TOOLS_ROOT}/lib/python'
  DISTMAN_TARGET_PATH: '{TOOLS_ROOT}/lib/python'
  DISTMAN_SHARED_BIN_PATH: ${TOOLS_ROOT}/bin
  DISTMAN_RUNTIME_BIN_PATH: ${DISTMAN_SHARED_BIN_PATH}
```

The library and target templates match, while both bin templates select the same
shared command container. Use this layout for exactly one platform, architecture,
and Python runtime, for example:

```bash
distman install numpy --platform linux --arch x86_64 --python 3.11 --dryrun
```

Add `/etc/tools/lib/python` to the application's `PYTHONPATH`. Later installs
preserve unrelated packages and files. Each package/module and command has its own
active link and retained revision history; there is no shared-environment link to
roll back as a unit.

### One flat root per runtime

For several runtimes, give each its own root containing both pure and binary
packages:

```yaml
all:
  TOOLS_ROOT: /studio/tools
  DISTMAN_SHARED_LIB_PATH: '{TOOLS_ROOT}/{platform}/{arch}/python{python_version}/lib/python'
  DISTMAN_RUNTIME_LIB_PATH: '{TOOLS_ROOT}/{platform}/{arch}/python{python_version}/lib/python'
  DISTMAN_TARGET_PATH: '{TOOLS_ROOT}/{platform}/{arch}/python{python_version}/lib/python'
  DISTMAN_SHARED_BIN_PATH: '{TOOLS_ROOT}/{platform}/{arch}/python{python_version}/bin'
  DISTMAN_RUNTIME_BIN_PATH: ${DISTMAN_SHARED_BIN_PATH}
```

For example, Linux x86_64 Python 3.11 packages share
`/studio/tools/linux/x86_64/python3.11/lib/python`; Python 3.12 uses a separate root.
Pure package targets are published into each selected runtime container. Each
distribution target has its own revision history.

## Installed ownership metadata

All new distman-owned package metadata lives under `${DEPLOY_ROOT}/.distman/`. Target
revision records contain the package version, sanitized wheel source, declared
dependencies, runtime selections, active payload, file hashes, modes, and owned
targets. Incremental snapshots select the active revision record for each target.
There is no `.distman-install.json` inside new package payloads or shared containers.

Python's standard `.dist-info` directories remain package content. Older deployments
may still contain embedded `.distman-install.json` receipts and legacy `.<target>.dist`
files; compatibility readers can inspect those formats, but new package publications
do not author them. The metadata is generated internal state, not configuration users
need to edit. Packages acquired as dependencies are recorded too.

## Removing packages

```bash
distman uninstall cowsay --dryrun
distman uninstall cowsay --yes
distman uninstall 'numpy==1.24.4' --platform linux --python 3.8
distman uninstall cowsay --delete
```

Uninstall reads the selected deployment's centralized records; it does not inspect
distman's own Python environment or need network access. An unqualified name selects
all recorded versions and runtimes. Exact `name==version` pins and `--platform`,
`--arch`, and `--python` narrow the selection. Omitted runtime filters mean every
installed runtime, not the host defaults.

Normal uninstall removes the selected package's active links and records null target
selections in the deployment journal. Retained payloads under sibling `versions/`
directories remain available for audit or later relinking. `--delete` also removes the
selected package's inactive retained payloads. It may be supplied during uninstall or
run later after a normal uninstall. It does not delete active payloads or unrelated
package history. Shared container directories remain in place.

For partial runtime removal, common library targets remain active while their central
runtime selection is updated. Generated Windows or POSIX launchers are unlinked when
no retained platform needs that launcher form. Binary package boundaries are revised
only when other runtime layouts remain inside the same package/version target.
Dependencies are not automatically removed. Removal is rejected if it would break a
retained package unless `--force` is supplied; force reports the affected dependents
and leaves them installed. It never bypasses ownership, integrity, the writer lock,
confirmation, or dry-run behavior.

A dependency refusal is preflight-only: no active targets change. Already absent
selections succeed without a new snapshot. Modified, missing, or externally relinked
managed targets fail closed. `Planned removal` describes intended changes, not a
completed deletion.

## Revisions

Each installed target is copied to a numbered sibling under `versions/`, then its
active symlink is created or replaced. Shared library and bin directories themselves
are not versioned. Installation reuses the newest retained revision whose hashes and
modes match the staged target, including when recreating a link after uninstall.
Unchanged targets create no new payload revision unless forced.
Upgrades replace only the selected distribution's targets and reject ownership
collisions, modified payloads, missing targets, and active links that differ from the
central snapshot.

Package-exclusive binary paths such as `tools/numpy/2.2.6` remain single versioned
targets because all runtime layouts beneath them belong to that package version.
Pure packages and shared commands instead use one link per package/module, metadata
directory, data target, or command. Restoring one payload link manually is detected
as a state conflict even when its bytes happen to match another revision. A future
snapshot rollback command can select a recorded set of target revisions; it is not
implemented by `install` or `uninstall`.

POSIX activation replaces each created/revised target link atomically. Multiple links
are not globally atomic to readers. Windows directory links require an unlink/rename
swap with a brief visibility gap; the publication journal permits recovery after an
interruption. Unlinks are journaled too, so caught exceptions restore links already
removed by the operation. Windows deployment requires symlink permission.

See the [usage guide](usage.md) and [architecture guide](planning/architecture.md)
for command and implementation details.

### Concurrent writers

`install`, `uninstall`, and modifying `dist` operations share one writer lock per
resolved `DEPLOY_ROOT`, at `.distman/locks/write/`. A competing command waits up to
30 seconds, then fails with the lock owner's details. Set the process environment
variable `DISTMAN_LOCK_TIMEOUT` to change the wait in seconds (`0` fails immediately).
For example:

```bash
DISTMAN_LOCK_TIMEOUT=120 ENVPATH=examples/default distman install pygments
```

The lock covers planning, acquisition, confirmation, and publication. Waiting writers
read current state after acquiring it. Independent deployment roots can proceed in
parallel. Dry runs do not lock or create deployment directories, so previews may
become outdated. A failed real operation may leave empty `.distman/locks/` directories.

Use the same resolved deployment root and metadata-directory configuration for all
writers sharing targets. Different policy roots that overlap the same files are not
safe merely because they each have a lock. Existing per-target installer locks are
also checked temporarily; these still fail immediately when occupied. Older dist
commands do not honor the shared lock, so upgrade all writers for full coordination.

Never delete a lock without checking that its owner has stopped. A killed process
can leave a stale lock; stale-lock removal is never automatic. Local concurrent
process tests pass, but multi-host NFS/SMB behavior remains to be validated. Multiple
library/bin links still switch individually, and manual edits do not honor locks.

### Interrupted publication

Install/uninstall publication records previous links before activation and attempts
restoration on failure or Ctrl-C. If restoration cannot finish, further writes are
blocked until recovery:

```bash
distman recover --root /path/to/deployment --dryrun
distman recover --root /path/to/deployment --yes
```

Recovery refuses unexpected links and missing previous revisions. A dead writer may
leave a lock: confirm its owner has stopped before removing that specific stale lock.
Legacy dist publication is not yet journaled. These recovery records cover interrupted
installation, not whole-deployment rollback or manual-edit restoration.
