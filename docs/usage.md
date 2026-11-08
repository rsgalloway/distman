# Usage

## Commands

Use the `distman` suite for these operations:

```bash
distman dist [OPTIONS] [LOCATION]
distman undist [OPTIONS] [LOCATION]
distman cache [OPTIONS]
distman install [OPTIONS] [REQUIREMENTS...]
distman uninstall [OPTIONS] PACKAGES...
distman recover [PATH] [--dryrun] [--yes]
distman --version
```

The `dist` command is a shorthand for `distman dist`.

## Deployment policy

`dist`, `undist`, `install`, and `uninstall` resolve deployment paths from the same
policy. The packaged `distman/resources/distman.env` defaults to
`${HOME}/.local/distman/${ENV}`. A `distman.env` on `ENVPATH` and active path variables
override that baseline for every command. See [Installing Python
Distributions](install.md#destinations) for the variables and a copyable policy.

## Logging

Every subcommand sends its user-facing status and diagnostics through the shared
logger. Interactive console output uses aligned status markers: green `+` for a new
package, green `✓` for an existing managed package or completed action, and red `−`
for planned removal. ANSI color is omitted when `NO_COLOR` is set, and redirected
output remains plain. Console output is written to stdout. Successful non-dry-run
commands are also recorded in
`${LOG_DIR}/distman.log`. `LOG_DIR` defaults to `~/log/distman`;
`LOG_LEVEL` defaults to `INFO`. Dry runs do not create or append to the deployment
log. Interactive confirmation prompts display `[Y/n]`; press Enter to accept or enter
`n` to decline. Use `--yes` where supported to skip the prompt.

## Distribution

```bash
dist [OPTIONS] [LOCATION]
```

`LOCATION` is the source directory and optional `dist.json` location. It
defaults to the current directory.

| Option | Meaning |
|---|---|
| `-t`, `--target TARGET...` | Select target names; wildcards are supported |
| `--source PATH` | Source file or directory; matches configured source patterns when `dist.json` is present |
| `--dest PATH` | Destination override; requires `--source` or `--target` |
| `-s`, `--show` | Show active distributed versions only |
| `-c`, `--commit HASH` | Point selected targets to a deployed commit |
| `-n`, `--number NUMBER` | Point selected targets to a version number |
| `--reset` | Point selected targets to the latest version |
| `--delete` | Delete selected deployed targets or versions |
| `-y`, `--yes` | Accept confirmation prompts |
| `--all` | Include normally ignored files |
| `--ignore-missing` | Skip missing source paths |
| `-f`, `--force` | Allow operations blocked by source-state checks |
| `--version-only` | Create the version without changing the active link |
| `-d`, `--dryrun` | Report actions without changing files |
| `-v`, `--verbose` | Increase output detail; repeat for debug output |
| `--version` | Print the distman version |

Examples:

```bash
dist /path/to/repository --target bin --dryrun
dist --target 'plugin-*' --yes
dist --target lib --dest /deploy/lib/python/distman --dryrun
dist --source build/tool --dest /deploy/bin/tool --dryrun
dist --show
dist --target tools --number 3
dist --target tools --delete --number 1 --dryrun
```

Use `--source` and `--dest` together to deploy directly without a `dist.json`
file. Direct ad hoc deployments use content matching by default because the
source may not belong to the current Git commit.

When `dist.json` is present, `--source` matches configured source paths. For
example, a target with `"source": "build/*"` matches
`--source build/package`; `%1` placeholders in the configured destination are
expanded from the matched wildcard group.

`--dest` overrides the selected destination. It must be paired with `--source`
or `--target` so one destination is not accidentally applied to every
configured target. When the selected source pattern contains wildcards,
`--dest` is also treated as a destination template and must include the
matching `%1`, `%2`, etc. placeholders.

`--commit`, `--number`, and `--reset` are mutually exclusive. Routine destination initialization and
directory-creation details are shown only with `-v`/`--verbose`.

## Removing distributed targets

`undist` withdraws active targets published by `dist` while preserving their
ownership receipts and retained revisions:

```bash
distman undist . --target tool
distman undist ./myapp --target 'plugin-*' --dryrun
distman undist . --target 'plugin-*' --all
distman undist --dest /deploy/bin/tool
distman undist --source build/tool --dest /deploy/bin/tool
```

`LOCATION` selects a `dist.json`; without `--target`, every configured target is
selected. Wildcard targets follow the current source tree, matching `dist`.
`--all` additionally selects active, same-project targets found through receipts from
earlier dists. `--dest` withdraws an ad hoc managed destination directly, and optional
`--source` verifies its recorded source identity. Missing sources for exact manifest
destinations do not block removal. Unmanaged destinations and links that do not select
a retained Distman revision are rejected. Repeating an undist operation succeeds without changing the
deployment. Running `dist` again can reactivate a matching retained revision. Use
`dist --delete` when retained revisions must be permanently removed.

## Cache Management

```bash
distman cache [OPTIONS]
```

| Option | Meaning |
|---|---|
| `--src PATH` | Deployment source; defaults to `DEPLOY_ROOT` |
| `--dst PATH` | Local destination; defaults to `CACHE_ROOT` |
| `--workers N` | Parallel copy threads; defaults to 4× CPUs, capped at 32 |
| `--delete` | Delete the cache |
| `-p`, `--prune` | Remove unreferenced cached versions |
| `--diff` | Show differences without copying |
| `-t`, `--ttl SECONDS` | Remote epoch-check TTL; `0` always checks |
| `-f`, `--force` | Refresh regardless of TTL and epoch state |
| `-d`, `--dryrun` | Check staleness without changing files |

Examples:

```bash
distman cache --dryrun
distman cache --ttl 0
distman cache --diff
distman cache --prune --dryrun
distman cache --src /studio/apps/prod --dst /var/tmp/apps
```

## Python installation

See [Installing Python Distributions](install.md) for runtime selection, optional
dependencies, path policy, ownership, and activation behavior.

```bash
distman install numpy
distman install -r requirements.txt --dryrun
distman install ./myapp --yes
```

Interactive installs show a yellow spinner while resolving, downloading, staging, and
publishing packages, followed by a green completion or red failure marker. Animation
is disabled for redirected output, CI, and verbose pip output. Use `-q`/`--quiet` to
disable it, or set `NO_COLOR` to retain status markers without ANSI colors.

## Python package removal

```bash
distman uninstall cowsay --dryrun
distman uninstall cowsay pygments --yes
distman uninstall 'numpy==1.24.4' --platform linux --python 3.8
distman uninstall cowsay --delete
```

Package names select all installed versions and runtimes in the configured deployment;
exact version pins and runtime filters narrow that selection. Options are `--platform`,
`--arch`, `--python`, `-d`/`--dryrun`, `-y`/`--yes`, `-f`/`--force`, and `--delete`.
`--delete` removes matching inactive payload history and can run after a normal uninstall.
Force permits broken retained dependencies; ownership and integrity checks still apply. See
[Removing packages](install.md#removing-packages) for ownership, dependency checks,
activation limits, and rollback history.

## recover

Recover interrupted journaled install, uninstall, or undist publication:

```bash
distman recover [PATH] [--dryrun] [--yes]
```

When omitted, `PATH` uses the configured deployment root. Preview is read-only.
Recovery requires the writer lock and confirmation unless `--yes` is supplied;
pressing Enter accepts the `[Y/n]` prompt. Unexpected links or missing prior revisions
block restoration. This is not a general snapshot rollback command. A canceled
command returns 130; failed or declined recovery returns 1.
