# Getting Started

distman reads deployment instructions from a `dist.json` file located at the
root of a directory or Git repository. Each named target maps a source object
to a destination.

## Install

```bash
pip install -U distman
```

This installs three commands:

- `distman`: suite command with `dist` and `cache` subcommands
- `dist`: direct distribution and version-management command
- `distcache`: direct cache-management command

## Create a Distribution File

```json
{
  "author": "pipeline@example.com",
  "targets": {
    "bin": {
      "source": "bin/tool",
      "destination": "{DEPLOY_ROOT}/bin/tool"
    },
    "library": {
      "source": "lib/example",
      "destination": "{DEPLOY_ROOT}/lib/python/example"
    }
  }
}
```

Sources are relative to the directory containing `dist.json`. Destinations may
contain `{NAME}` placeholders, which distman replaces from the current process
environment and its built-in defaults.

This works for configuration repositories as well as application payloads. For
example, a repository that stores `env/mytool.env` can deploy it to
`{DEPLOY_ROOT}/env/mytool.env` or any other filesystem scope you choose.

## Preview a Deployment

Always inspect a new configuration with a dry run first:

```bash
dist --dryrun --verbose
```

Deploy every configured target without interactive prompts:

```bash
dist --yes
```

Deploy selected targets:

```bash
dist --target bin library --yes
dist --target 'lib*' --yes
```

## Version Layout

For a destination such as `{DEPLOY_ROOT}/bin/tool`, distman stores numbered
objects under a sibling `versions` directory and makes the requested
destination a symlink to the active object:

```text
bin/
├── tool -> versions/tool.1.a1b2c3d
└── versions/
    └── tool.1.a1b2c3d
```

The suffix records a monotonically increasing version number and, for
commit-based matching, a short Git commit. Metadata is written beside deployed
targets.

## Inspect and Roll Back

```bash
dist --show
dist --target bin --number 1
dist --target bin --commit a1b2c3d
dist --target bin --reset
```

Use at least four characters when selecting a commit. `--reset` points the
selected target back to its latest version.

## Git Safety

distman uses Git information to identify the source and version. A normal
deployment stops when a selected source has uncommitted changes or the local
repository is behind its upstream. Use `--force` only when that behavior is
intentional.

## Runtime Composition

distman answers a deployment question: which revision of this repository is
published at this filesystem location? If you later compose environment files
at runtime with a tool such as [envstack](https://envstack.dev), keep that as a
separate concern.

For example, if project-specific configuration should override production
configuration, `ENVPATH` should list the project directory first:

```bash
export ENVPATH=/studio/project/foo/env:/studio/prod/env
```
