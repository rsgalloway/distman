# Installation architecture audit

This audit covers the working `feat/install` tree and the October 19 MVP revision.

## Current responsibilities and compatibility surface

| Module | Responsibility and coupling | Bounded change |
|---|---|---|
| `cli.py` | Suite parsing originally imported separate command parsers | Centralize all options, parser construction, and dispatch following rhps-ingest |
| `dist.py` | CLI, Git checks, targets, pipelines, version selection, publication | Move parser definitions to `cli.py`; preserve parser wrappers, `Distributor`, `run`, `main`, and helper imports |
| `source.py` | `Source` loads `dist.json`; `GitRepo` adds repository inspection | Preserve both classes and Git checks |
| `deployment.py` | New source-neutral revision publication; shares revision naming with `dist` | Type and document its staged-directory API |
| `config.py` | Legacy constants evaluated from the environment at import time | Preserve defaults for existing callers |
| `install/policy.py` | Optional envstack/pathbase policy mixed into command namespace | Move to `policy.py`; retain an import compatibility shim |
| `install/defaults.env` | Packaged configuration embedded in command implementation folder | Move to `resources/install.env` |
| `install/wheels.py` | pip resolution, wheel inspection, build, and staging | Explicitly type artifacts and provenance |
| `install/ownership.py` | Validate and compose temporary snapshots; writer locks | Share typed records; keep publication in deployment |
| `install/command.py` | Orchestrates acquisition and publication | Add explicit local-project planning |
| `install/cli.py` | Separate installer parser and error reporting | Remove; move options and error handling into the single `cli.py` |
| `pipeline.py`, `transform.py` | Transform specification, callable imports, shell execution | Preserve callable paths and behavior |
| `cache.py` | Cache CLI, mirroring, epoch/TTL checks, pruning | Preserve APIs and cache-specific filesystem operations |
| `util.py`, `logger.py` | Legacy path/copy/version helpers and logging configuration | Fill documentation/type gaps without changing semantics |

Public compatibility includes `dist`, `distcache`, `distman dist`, `distman cache`,
`distman.dist.Distributor`, `distman.source.Source`/`GitRepo`, parser/run/main
functions (including thin `dist.build_parser` and `cache.build_parser` wrappers), and the documented `distman.transform.*` callables. Revision naming
continues to be re-exported from `distman.dist`. No legacy module is renamed.

## Coupling found in the current tree

`Distributor` inherits `GitRepo` and loads Git metadata even for ad hoc sources.
`Source` is a configuration reader rather than a generic acquisition provider.
`dist.py` mixes argument parsing, safety checks, transformation, and publication.
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
