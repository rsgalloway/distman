# distman

<p align="left">
  <img src="https://raw.githubusercontent.com/rsgalloway/distman/master/docs/distman.png" alt="distman logo" width="300">
</p>

[![PyPI](https://img.shields.io/pypi/v/distman.svg?color=blue)](https://pypi.org/project/distman/)
[![Tests](https://github.com/rsgalloway/distman/actions/workflows/tests.yml/badge.svg)](https://github.com/rsgalloway/distman/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](LICENSE)

Simple software distribution for production pipelines.

distman performs safe, versioned rollouts of software, scripts, and
configuration files to filesystem locations. It is useful when deterministic
deployments, environment-aware transforms, local caching, and quick rollback
matter more than conventional package installation.

That includes revision-controlled configuration repositories. distman can
publish configuration into runtime filesystem scopes without needing to
understand the contents of those files. In that model, Git remains the source
of truth, distman handles filesystem deployment, and runtime tools such as
[envstack](https://envstack.dev) can compose the deployed files later.

Deployed objects are stored as numbered versions, while an atomic symlink
selects the active version:

```text
bin/
├── tool -> versions/tool.2.a1b2c3d
└── versions/
    ├── tool.1.91c8a77
    └── tool.2.a1b2c3d
```

distman works well with [envstack](https://envstack.dev) when you want
filesystem deployment and runtime environment composition to remain separate
concerns.

## Installation

```bash
pip install -U distman
```

Install the optional JavaScript and HTML minifiers when a transform pipeline
needs them:

```bash
pip install -U "distman[minify]"
```

## Quickstart

For an ad hoc deployment, provide the source and destination directly:

```bash
dist --source path/to/tool --dest /deploy/bin/tool --dryrun
dist --source path/to/tool --dest /deploy/bin/tool --yes
```

Ad hoc deployments use the same versioned layout as configured deployments and
match previous versions by content by default.

For repeatable project deployments, create a `dist.json` file at the root of a
Git repository:

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

Preview and then perform the deployment:

```bash
dist --dryrun
dist --yes
```

Override a configured target source or destination from the CLI:

```bash
dist --source build/package --dest /deploy/lib/python/package --dryrun
dist --target tools --dest /deploy/tools --yes
```

The namespaced `distman dist` command provides the same distribution
interface. Cache a shared deployment locally with:

```bash
distcache
```

## Documentation

Full documentation is available in the [docs](docs/) folder:

- [Getting Started](docs/getting-started.md)
- [Distribution Configuration](docs/distribution-config.md)
- [CLI Reference](docs/cli-reference.md)
- [Transform Pipelines](docs/transform-pipelines.md)
- [Local Caching](docs/caching.md)

The documentation is also published through the repository's GitHub Pages
workflow.

## License

distman is distributed under the [BSD 3-Clause License](LICENSE).
