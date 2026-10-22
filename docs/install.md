# Installing Python distributions

`distman install` installs Python packages and local projects into versioned
deployment directories. Install the optional dependencies first:

```bash
python -m pip install 'distman[install]'
```

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

Dry runs still resolve, download, and stage packages. Local builds run on a
temporary source copy, excluding Git metadata, common virtualenv directories,
and broken symlinks. Backends requiring Git metadata may not support preview.

## Runtime selection

By default, packages target the running CPython interpreter. Select other runtimes
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

## Destinations

Distman defines an opinionated environment-variable contract for installation.
These names and their meanings belong to distman; envstack supplies loading and
variable expansion without assigning them package or deployment semantics.

| Variable | Installer meaning | Packaged default |
|---|---|---|
| `ROOT` | Base directory used to derive the deployment root | `${HOME}/.local/distman` |
| `ENV` | Deployment tier used in the default root | `prod` |
| `DEPLOY_ROOT` | Deployment root reported by the installer and used for deployment metadata | `${ROOT}/${ENV}` |
| `TOOLS_ROOT` | Base directory used by the default package templates | `${DEPLOY_ROOT}/tools` |
| `DISTMAN_SHARED_LIB_PATH` | Shared library import root for platform-independent wheels | See templates below |
| `DISTMAN_RUNTIME_LIB_PATH` | Import path for a package/runtime variant | See templates below |
| `DISTMAN_TARGET_PATH` | Versioned publication boundary containing binary variants | See templates below |

Policy is resolved in this order, with later values taking precedence:

1. Packaged `distman/resources/install.env` supplies baseline defaults and includes
   `default` through envstack's normal lookup rules.
2. Discoverable `distman.env`, if present, overlays those values.
3. Nonempty active `ROOT`, `ENV`, `DEPLOY_ROOT`, and `TOOLS_ROOT` values override
   file policy. All active `DISTMAN_*` values are also overlaid.
4. Envstack expands the combined values; [pathbase](https://rsgalloway.github.io/pathbase/) formats the package templates.

`DISTMAN_*` is the policy override namespace; arbitrary names in that namespace
do not automatically gain behavior. The three path variables above are the
installer's recognized layout controls. Other variables can be referenced by
custom templates without becoming built-in settings.

An active `DEPLOY_ROOT` wins even if `ROOT` or `ENV` changes. Likewise, custom
`TOOLS_ROOT` or path templates can place packages outside `DEPLOY_ROOT`; that
root is not a containment restriction. `ENVPATH` controls policy discovery,
while `PATH` and `PYTHONPATH` control runtime lookup, not deployment destinations.
Resolving policy does not modify the caller's environment.

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

Add the shared and selected binary import roots to your runtime `PYTHONPATH`.
Wheel scripts currently live in the import root's `bin` directory and require a
matching Python interpreter on `PATH`. With the default pure layout, add
`${TOOLS_ROOT}/lib/python/bin` to `PATH` and `${TOOLS_ROOT}/lib/python` to
`PYTHONPATH`. Installation policy loading does not activate a shell environment.

The default shared pure-Python root represents one compatible environment: its
packages and entrypoints share revision history, with one active version of each
distribution. All consumers must accept that shared selection. Metadata checks
help enforce compatibility but do not guarantee application behavior. Use custom
per-runtime or package/version paths when a shared selection is unsuitable.

A separate `${TOOLS_ROOT}/bin` and configurable script templates are
[planned](install-planning.md#shared-libraries-scripts-and-activation-policy), not
implemented. The default remains shared pure libraries and commands; moving scripts
must preserve their coordinated upgrade and rollback behavior.

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
```

All three templates must match so the installer composes one shared revision.
Use this root for exactly one platform, architecture, and Python runtime, for example:

```bash
distman install numpy --platform linux --arch x86_64 --python 3.11 --dryrun
```

Add `/etc/tools/lib/python` to the application's `PYTHONPATH`. Later installs
preserve unrelated managed packages, and rollback restores the entire shared
root. A pre-existing unmanaged directory cannot be adopted automatically.

### One flat root per runtime

For several runtimes, give each its own root containing both pure and binary
packages:

```yaml
all:
  TOOLS_ROOT: /studio/tools
  DISTMAN_SHARED_LIB_PATH: '{TOOLS_ROOT}/{platform}/{arch}/python{python_version}/lib/python'
  DISTMAN_RUNTIME_LIB_PATH: '{TOOLS_ROOT}/{platform}/{arch}/python{python_version}/lib/python'
  DISTMAN_TARGET_PATH: '{TOOLS_ROOT}/{platform}/{arch}/python{python_version}/lib/python'
```

For example, Linux x86_64 Python 3.11 packages share
`/studio/tools/linux/x86_64/python3.11/lib/python`; Python 3.12 uses a separate root.
Pure packages are copied into each selected runtime root. Each root has its own
revision history and holds one active version of each distribution.

## Revisions

Installs create complete revisions and switch the active symlink. Unchanged
installs create no revision unless forced. Upgrades preserve unrelated managed
packages and reject file collisions, modified installations, and unmanaged
destinations; `--force` does not bypass these checks.

Use `dist`'s version options with a `dist.json` target to select an older revision.
Rolling back a shared import root rolls back all packages in that revision.

Activation is atomic per root, not across roots. Local `dist.json` publication is
separate: a later target failure can leave installed dependencies in place.
Windows deployment requires symlink permission.

See the [CLI reference](cli-reference.md) and [architecture guide](architecture.md)
for command and implementation details.
