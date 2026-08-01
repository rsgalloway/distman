# distman

distman is a **configuration-driven file distribution manager** for safe,
versioned software rollouts to predefined filesystem locations.

It is designed for production pipelines where deterministic deployments,
environment-aware paths, quick version switching, and rollback matter more
than building and installing packages.

```text
deploy/
├── bin/
│   ├── tool -> versions/tool.2.a1b2c3d
│   └── versions/
│       ├── tool.1.91c8a77
│       └── tool.2.a1b2c3d
└── .distman/
    └── epoch
```

## Why distman

### Deploy safely

- Copy files and directories into immutable numbered versions
- Switch the active deployment with a symlink
- Preview changes before touching the destination
- Refuse uncommitted source changes unless explicitly forced

### Configure once

- Describe deployments in a repository-level `dist.json`
- Resolve destination paths from environment variables
- Select targets by name or wildcard
- Expand source globs into matching destinations

### Prepare and cache

- Run Python functions or shell commands before distribution
- Replace tokens, change modes, byte-compile Python, and minify assets
- Mirror active deployments to a fast local cache
- Avoid unnecessary remote checks with epoch files and a configurable TTL

## Install

```bash
pip install -U distman
```

## Quickstart

Create `dist.json` at the root of a Git repository:

```json
{
  "author": "pipeline@example.com",
  "targets": {
    "tools": {
      "source": "tools",
      "destination": "{DEPLOY_ROOT}/tools"
    }
  }
}
```

Preview and then deploy:

```bash
dist --dryrun
dist --yes
```

The same operation is available through the suite command:

```bash
distman dist --dryrun
```

## Learn More

- [Getting Started](getting-started.md): first deployment and version layout
- [Distribution Configuration](distribution-config.md): `dist.json`, paths, and targets
- [CLI Reference](cli-reference.md): distribution and cache commands
- [Transform Pipelines](transform-pipelines.md): preprocessing files before deployment
- [Local Caching](caching.md): keeping a local mirror of active versions
