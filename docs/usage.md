# Usage

## Commands

Use the `distman` suite for these operations:

```bash
distman dist [OPTIONS] [LOCATION]
distman cache [OPTIONS]
distman install [OPTIONS] [REQUIREMENTS...]
distman uninstall [OPTIONS] PACKAGES...
distman recover --root PATH [--dryrun] [--yes]
distman --version
```

The `dist` command is a shorthand for `distman dist`.

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

`--commit`, `--number`, and `--reset` are mutually exclusive.

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

## Python package removal

```bash
distman uninstall cowsay --dryrun
distman uninstall cowsay pygments --yes
distman uninstall 'numpy==1.24.4' --platform linux --python 3.8
```

Package names select all installed versions and runtimes in the configured deployment;
exact version pins and runtime filters narrow that selection. Options are `--platform`,
`--arch`, `--python`, `-d`/`--dryrun`, `-y`/`--yes`, and `-f`/`--force`.
Force permits broken retained dependencies; ownership and integrity checks still apply. See
[Removing packages](install.md#removing-packages) for ownership, dependency checks,
activation limits, and rollback history.

## recover

Recover interrupted journaled install/uninstall publication:

```bash
distman recover --root PATH [--dryrun] [--yes]
```

The root is explicit. Preview is read-only; recovery requires the writer lock and
confirmation unless `--yes` is supplied. Unexpected links or missing prior revisions
block restoration. This is not a general snapshot rollback command. A canceled
command returns 130; failed or declined recovery returns 1.
