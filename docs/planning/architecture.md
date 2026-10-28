# Installation architecture audit

This document describes the installer module boundaries, compatibility surface,
and remaining architectural constraints.

## Current responsibilities and compatibility surface

| Module | Responsibility and coupling | Bounded change |
|---|---|---|
| `cli.py` | Suite parsing originally imported separate command parsers | Centralized options, parser construction, and dispatch following rhps-ingest |
| `dist.py` | CLI, Git checks, targets, pipelines, version selection, publication | Moved parser definitions to `cli.py`; preserved parser wrappers, `Distributor`, `run`, `main`, and helper imports |
| `source.py` | `Source` loads `dist.json`; `GitRepo` adds repository inspection | Preserved both classes and Git checks |
| `deployment.py` | New source-neutral revision publication; shares revision naming with `dist` | Typed and documented its staged-directory API |
| `config.py` | Legacy constants evaluated from the environment at import time | Preserved defaults for existing callers |
| `install/policy.py` | Optional envstack/pathbase policy mixed into command namespace | Moved to `policy.py`; retained an import compatibility shim |
| `resources/install.env` | Packaged fallback policy, formerly install/defaults.env | Configuration moved out of command implementation |
| `install/wheels.py` | pip resolution, wheel inspection, build, and staging | Typed artifacts and provenance; stages shared platform launchers |
| `install/ownership.py` | Validate and compose temporary snapshots; writer locks | Shared typed records and target-scoped checks; publication stays in deployment |
| `install/command.py` | Orchestrates acquisition and publication | Added explicit local-project planning and retained dependency discovery |
| Former `install/cli.py` | Separate installer parser and error reporting | Removed; options and error handling now live in `cli.py` |
| `pipeline.py`, `transform.py` | Transform specification, callable imports, shell execution | Preserved callable paths and behavior |
| `cache.py` | Cache CLI, mirroring, epoch/TTL checks, pruning | Preserved APIs and cache-specific filesystem operations |
| `util.py`, `logger.py` | Legacy path/copy/version helpers and logging configuration | Added documentation and type hints without changing semantics |

Public compatibility includes `dist`, `distcache`, `distman dist`, `distman cache`,
`distman.dist.Distributor`, `distman.source.Source`/`GitRepo`, parser/run/main
functions (including thin `dist.build_parser` and `cache.build_parser` wrappers), and the documented `distman.transform.*` callables. Revision naming
continues to be re-exported from `distman.dist`. No legacy module is renamed.

## Remaining coupling

`Distributor` inherits `GitRepo` and loads Git metadata even for ad hoc sources.
`Source` is a configuration reader rather than a generic acquisition provider.
`dist.py` retains Git safety checks, transformation, and legacy publication; argument
parser definitions now live in `cli.py`.
`util.copy_file` normalizes text, which is inappropriate for wheel payload hashes.
`config.py` and `logger.py` capture environment settings at import time. Pathbase
0.2 can initialize envstack at import time, changing environment and Python paths;
the policy boundary contains those side effects and resolves configuration once.

`util` imports the logger, and the logger imports `config`; neither should import
installer orchestration. The installer formerly imported `dist.confirm` only to
prompt, unnecessarily pulling its Git-facing command module into orchestration.
Project distribution deliberately enters the existing command boundary to retain
Git safety and transform behavior. Shared state types do not import policy or CLI.

## Dependency direction and scope

CLI → install orchestration → policy / project inspection / wheel acquisition →
staged artifacts and provenance → ownership composition → deployment publication.

Policy uses envstack for file loading and expansion and pathbase for path templates.
It does not acquire packages. Configuration resources belong to the package's
`resources` folder, separate from command implementation. The repository's
`distman.env` remains the studio-facing configuration example.

`install/projects.py` inspects only the supplied directory. It delegates builds
and dependency resolution to pip and configured custom targets to `dist.run`.
A combined project's built wheel supplies dependency metadata but is excluded
from third-party publication when `dist.json` owns the project deployment.

This is not a wholesale CLI, source, cache, or transform rewrite. Legacy activation
semantics remain intact. The new source-neutral publication API requires managed
symlink destinations, copies bytes unchanged, prepares revisions before activation,
and restores prior links on caught failures. Multi-root activation is not globally
atomic. Combined legacy distribution and package publication likewise do not form
a new cross-command transaction; this boundary is documented rather than hidden.

## Centralized CLI reference

The maintainer-provided rhps-ingest implementation uses one `cli.py` containing
`build_parser`, `_add_*_options`, and command dispatch while importing operations
from dedicated modules. Distman now follows that arrangement for `dist`, `cache`,
and `install`. Optional installer imports remain lazy; `dist` and `distcache`
retain their existing console entry points and parser APIs. Installer business
logic stays outside the CLI.

## Installer target and launcher ownership

A receipt records the runtimes selected for each package. Dependency validation
starts with those runtimes and follows reachable shared dependencies; it does not
assign every retained pure-Python application to every newly requested platform.
Installed dependencies in separate configured package roots participate in checks
and locking without being copied or republished.

Shared generated entrypoints combine POSIX and Windows launchers in one scripts
receipt. Staging regenerates variants for requested and previously recorded
platforms from the selected wheel, including complete wheel RECORD entries.
Normalized Windows ZIP timestamps keep comparisons deterministic. Library/scripts
pair validation and publication rollback remain in the ownership/deployment layers.
This supports cross-generation on Linux; native Windows execution is a separate
validation requirement. See the [installation roadmap](install-planning.md) for
remaining validation checks and unimplemented features.
