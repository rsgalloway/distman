# Local Caching

distman can mirror a shared deployment to a local filesystem. This reduces
startup latency for users reading tools over VPN, SMB, NFS, or another remote
filesystem.

## Refresh a Cache

By default, the cache command reads from `DEPLOY_ROOT` and writes to
`CACHE_ROOT`:

```bash
distcache
```

Use explicit roots when needed:

```bash
distcache --src /studio/apps/prod --dst /var/tmp/studio-apps
```

Copies run in parallel. Symlinks are preserved when the destination supports
them; otherwise distman falls back to copying the referenced objects.

## Epoch and TTL Checks

Successful deployment mutations update `.distman/epoch` under the deployment
root. The cache stores the corresponding epoch and compares the two to decide
whether it is stale.

`CACHE_TTL` (600 seconds by default) limits how often the remote epoch is read.
This makes routine shell startup checks inexpensive:

```bash
distcache
distcache --ttl 0
distcache --force
```

- `--ttl 0` always performs the epoch check.
- `--force` refreshes even when the TTL or equal epochs indicate a fresh cache.
- `--dryrun` reports staleness without copying.

## Inspect Differences

```bash
distcache --diff
```

Diff mode compares the deployment and cache without copying data.

## Prune Old Versions

The cache may retain version objects that are no longer referenced by an
active symlink. Preview and remove them with:

```bash
distcache --prune --dryrun
distcache --prune
```

Pruning keeps referenced versions and removes only unreferenced objects from
cache `versions` directories.

## Delete a Cache

```bash
distcache --delete --dryrun
distcache --delete
```

Cache deletion includes safety checks that reject dangerous destination roots.
Use a dry run before deleting a cache configured with a custom path.
