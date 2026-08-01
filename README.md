# distman

[![PyPI](https://img.shields.io/pypi/v/distman.svg)](https://pypi.org/project/distman/)
[![Tests](https://github.com/rsgalloway/distman/actions/workflows/tests.yml/badge.svg)](https://github.com/rsgalloway/distman/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](LICENSE)

Simple, configuration-driven software distribution for production pipelines.

distman performs safe, versioned rollouts of software, scripts, and
configuration files to predefined filesystem locations. It is useful when
deterministic deployments, environment-aware transforms, local caching, and
quick rollback matter more than conventional package installation.

Deployed objects are stored as numbered versions, while an atomic symlink
selects the active version:

```text
bin/
├── tool -> versions/tool.2.a1b2c3d
└── versions/
    ├── tool.1.91c8a77
    └── tool.2.a1b2c3d
```

distman works well with [envstack](https://github.com/rsgalloway/envstack) for
environment-specific deployment and runtime configuration.

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

Create a `dist.json` file at the root of a Git repository:

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
