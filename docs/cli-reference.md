# CLI Reference

## Commands

The convenience commands are the primary user-facing interfaces:

```bash
dist [OPTIONS] [LOCATION]
distcache [OPTIONS]
```

The `distman` suite command provides equivalent namespaced interfaces:

```bash
distman dist [OPTIONS] [LOCATION]
distman cache [OPTIONS]
distman --version
```

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
distcache [OPTIONS]
```

`distman cache [OPTIONS]` is equivalent.

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
distcache --dryrun
distcache --ttl 0
distcache --diff
distcache --prune --dryrun
distcache --src /studio/apps/prod --dst /var/tmp/apps
```
