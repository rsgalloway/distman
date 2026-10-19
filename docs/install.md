# Installing Python distributions

`distman install` installs Python packages and local projects into versioned
deployment directories. Install the optional dependencies first:

```bash
python -m pip install 'distman[install]'
```

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

Policy comes from envstack's `distman.env`. Active `ROOT`, `ENV`, `DEPLOY_ROOT`,
`TOOLS_ROOT`, and `DISTMAN_*` values override file policy. Without configuration,
the deployment root is `${HOME}/.local/distman/prod`. `ENVPATH` controls discovery,
not the write destination.

Default path templates are:

```yaml
all:
  TOOLS_ROOT: ${TOOLS_ROOT:-${DEPLOY_ROOT}/tools}
  DISTMAN_PURE_PATH: '{TOOLS_ROOT}/lib/python'
  DISTMAN_BINARY_PATH: '{TOOLS_ROOT}/{package}/{version}/{platform}/{arch}/python{python_version}/lib/python'
  DISTMAN_TARGET_PATH: '{TOOLS_ROOT}/{package}/{version}'
```

Pure wheels share an import root; binary wheels use runtime-specific paths.
Paths must be absolute and non-overlapping. Shared roots require compatible
package versions and contents across selected runtimes.

Add the shared and selected binary import roots to your runtime `PYTHONPATH`.
Wheel scripts live in the import root's `bin` directory and require a matching
Python interpreter on `PATH`. Installation does not activate a shell environment.

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
