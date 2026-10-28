# Installation roadmap: recipes and runtime environments

Status: proposed follow-up to the October 19 installation MVP. Updated October 25,
2026. This document extends `distman-mvp-planning_v1.md`; existing installation
behavior remains supported. Recipe acquisition and catalog commands below are not
implemented. This plan replaces the earlier suggestion to infer package installation
from arbitrary envstack variables.

## Independent installation operations

Direct package installation remains available for compatible packages from PyPI or
configured pip sources, whether or not a catalog recipe exists:

```bash
# Existing commands
distman install numpy
distman install 'numpy==2.2.6'
distman install -r requirements.txt
distman install ./myapp
```

Direct installation resolves and publishes wheels; it does not promise a curated
runtime environment file. Local projects retain their existing packaging,
requirements, and dist.json behavior.

Recipe installation explicitly selects an integration maintained by a project,
a studio, or the public catalog:

```bash
# Proposed commands
distman install --recipe numpy@2.2.6
distman install --recipe numpy@2.2.6 --platform linux --python 3.11
distman install --recipe ./recipes/numpy/2.2.6 --dryrun
```

Use an explicit recipe mode rather than adding another meaning to bare names.
`numpy==2.2.6` remains pip requirement syntax; `numpy@2.2.6` is a catalog selector
only with `--recipe`. Initially require an exact catalog package version. Recipe
mode must reject combinations with positional package/project inputs, `--project`,
`-r`, or `--env`; its requirements come from the recipe. Missing recipes fail
clearly and never silently fall back to PyPI. Missing catalog entries never block
direct package installs.

Both modes reuse staging, ownership validation, policy, and publication. Shared
options include runtime selection, dry run, confirmation, and verbosity. Package
specific source mappings belong in recipes, not new per-package CLI flags.

## Catalog and authoring

Start with a separate Git repository named `distman-packages`, not a hosted
PyPI-like service or a growing collection embedded in distman's source tree.
Support local recipe directories first, then fetching a catalog at an explicit
Git revision. Public and private catalogs should use the same contract; catalog
configuration and revision-selection CLI syntax remain to be designed.

A wheel recipe is a directory of existing formats:

```text
numpy/
  2.2.6/
    requirements.txt
    numpy.env
    dist.json
```

- `requirements.txt` pins acquisition, for example `numpy==2.2.6`.
- `numpy.env` supplies ordinary envstack variables and runtime paths.
- `dist.json` publishes environment files and any custom outputs.

Users can author and test these files locally. Projects can eventually ship the
same files themselves. Loading integrations bundled in a downloaded wheel needs
an explicit discovery contract; ordinary wheel installation must not implicitly
execute a newly discovered recipe.

Do not introduce a separate recipe JSON format for wheel installation. Investigate
an acquisition extension to dist.json for archives only after examining concrete
non-wheel cases. Existing dist.json does not currently describe archive fetching.

## Package versions and recipe revisions

Version the complete recipe by upstream package version. `numpy@2.2.6` identifies
its environment, requirements, and distribution configuration together. A Git
commit separately identifies corrections to that recipe. Record both identities
with installation provenance so a recipe can be reproduced after catalog updates.

Validate that the pinned package and the environment's default version agree.
Generation or validation should prevent these values drifting. Preserve runtime
overrides such as NUMPY_VERSION and NUMPY_ROOT.

Multiple installed package versions must retain their corresponding environment
files. Define an explicit default for an unqualified `include: [numpy]` and a way
to select a particular version using existing envstack discovery/include behavior.
The deployed layout and selection convention need validation before implementation;
this plan does not assume envstack understands `numpy@2.2.6` includes. Package
versions, recipe revisions, and distman deployment revisions remain distinct.

## Runtime environments

Follow pipeline-tools' numpy.env pattern: the package version has an overridable
default, the root incorporates that version, and platform-specific PYTHONPATH
entries use runtime variables. Python version can be evaluated at activation;
the `python` executable used must correspond to the application's interpreter.
Embedded applications may need explicit runtime configuration.

Distman's existing [installer variable contract](install.md#destinations) defines
its roots, path templates, and override precedence. Recipes must use that policy
rather than inventing independent deployment roots. Any new built-in variables
must document their meaning, defaults, scope, and precedence.

Envstack remains an environment-variable tool. No package resolver, new envstack
manifest, or package-specific envstack behavior is required. Distman can generate
routine templates from configured installation paths; maintainers can author
application-specific settings. Include architecture and the current lib/python
suffix instead of copying pipeline-tools' old layout literally.

Application authors still select includes and version overrides. Template updates
must not overwrite user customization without a defined ownership policy. Also,
the existing shared pure-Python root permits one active version per distribution;
versioned recipes do not create application dependency isolation.

## Shared libraries, scripts, and activation policy

Keep shared pure-Python packages as the default policy. A shared root represents
one compatible environment: one active version per distribution, compatible
requirements and supported runtimes, and no conflicting files or command names.
Its libraries and entrypoints must upgrade and roll back together. Compatibility
is an operator policy backed by metadata checks, not a guarantee that arbitrary
applications or native dependencies work together. Pure wheel tags alone do not
prove compatibility with every Python version or platform-specific launcher.

Users may instead configure package/version-specific pure roots or one shared
root per runtime. Do not require long per-package PATH and PYTHONPATH lists for
the default shared layout. Recipes must honor configured paths, not assume a
single deployment layout.

Implemented script locations, alongside the existing import layout:

| Payload | Import directory | Scripts directory |
|---|---|---|
| Shared pure Python | `{TOOLS_ROOT}/lib/python` | `{TOOLS_ROOT}/bin` |
| Binary/runtime-specific | `${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/lib/python` | `${DISTMAN_TARGET_PATH}/{platform}/{arch}/python{python_version}/bin` |

`DISTMAN_TARGET_PATH` defaults to `{TOOLS_ROOT}/{package}/{version}`. Referencing
it as `${DISTMAN_TARGET_PATH}` avoids repeated layout definitions: envstack expands
the variable first, then pathbase formats package/runtime tokens. The runtime
library template uses this composition; recursive pathbase formatting is not needed.
`DISTMAN_SHARED_BIN_PATH` and `DISTMAN_RUNTIME_BIN_PATH` select scripts from shared
and runtime-specific wheels respectively. They apply to generated console/GUI
entrypoints and wheel-provided scripts, including local packaging projects without
dist.json. Libraries, scripts, metadata, resources, and other wheel contents do not
require an additional manifest.

Runtime environment files add the resolved import directory to PYTHONPATH and the
resolved scripts directory to PATH. For shared pure packages, a common environment
included by applications can add `${TOOLS_ROOT}/lib/python` and `${TOOLS_ROOT}/bin`
once. A studio's distman.env can carry those settings if used for runtime
activation. Binary package environments select package version, platform,
architecture, and Python runtime as in pipeline-tools. Add the scripts directory,
not TOOLS_ROOT itself, and never export an unresolved artifact template to PATH.
A matching Python interpreter must still be available to launch the scripts.

Loading distman.env as installation policy does not activate the shell. The current
repository distman.env adds only `${DEPLOY_ROOT}/bin` and
`${DEPLOY_ROOT}/lib/python`; it does not yet add the proposed shared tools paths.

Implementation boundary: shared libraries and bins are separate managed revisions,
validated and published together with rollback on caught activation failures.
Their receipts link the artifacts and detect mismatched library/bin versions.
Manual rollback must select matching revisions of both roots. Runtime scripts
inside DISTMAN_TARGET_PATH share the package revision directly. Cross-root
activation is not globally atomic or durable across process termination.
Existing unmanaged bin directories and changing previously recorded layout mappings
are rejected. General migration and coexistence with legacy bin shims remain future
work. TOOLS_ROOT itself is not one managed boundary because binary package roots
also live beneath it.

## Named environments and conflict recovery (next branch milestone)

After shared/runtime bin deployment, design an explicit isolated-install and
activation workflow. Candidate interfaces are `distman install --env NAME` and
an environment-creation command; settle the CLI before implementation. The existing
reserved --env flag is not yet functional and must not silently change meanings.

Distman installs and validates files; envstack composes ordinary environment
variables. Named environments should use separate managed library/bin roots and
produce an envstack file for those roots, with no third user-authored manifest.
Record ownership in internal deployment receipts, keep distman.env as path policy,
and honor a project's optional dist.json custom targets.

A conflict with retained shared dependencies must leave the deployment unchanged,
report the requested and conflicting versions, and suggest an explicit isolated
installation. Never silently isolate or choose another environment. The resulting
envstack file must be discoverable through a documented ENVPATH configuration and
come with an exact activation/launch command. Support direct PyPI installs and
local projects as well as future recipes; users must not author path templates
per dependency.

Specify environment names/locations, root overrides, existing-file ownership,
activation discovery, generated-config updates, and coordinated rollback.
Do not modify studio default.env or rely on missing-stack fallback implicitly.
A distinct root alone is not interpreter isolation: inherited shared PYTHONPATH
entries and interpreter site-packages can still expose incompatible dependencies.
Decide whether the workflow filters inherited paths or optionally provisions a
virtual environment, and state its isolation guarantees. Test conflict recovery,
imports and entrypoints, explicit interpreter selection, and unchanged shared
packages before considering this milestone complete.

## Replacing pipeline-tools scaffolding

| Existing responsibility | Planned replacement |
|---|---|
| Repeated wheel fetch scripts | Current pip-backed wheel acquisition and complete wheel installation |
| Repeated platform/Python selection | Shared runtime selection |
| Downloaded staging trees | Temporary acquisition and staging |
| Central tool distribution mappings | Recipe-local dist.json targets |
| Package environment files | Versioned recipe content, generated where routine |
| Platform-specific binary downloads | Shared archive acquisition configured by recipes |
| Exceptional vendor APIs/builds | Explicit adapter or recipe script interface, only when needed |

Aim to replace all routine fetch/deploy scaffolding, preserving the package-specific
knowledge in recipes. Do not preserve partial wheel extraction as the default:
wheel metadata and dependencies must remain intact. Source builds, custom APIs,
and special transformations require separate design rather than arbitrary new
install flags.

## Delivery and validation

1. Prove the local wheel-recipe contract with NumPy 2.2.6 and a second wheel package.
   Test import through the deployed environment, runtime overrides, dependencies,
   custom path policy, and unchanged direct PyPI behavior.
2. Define versioned environment deployment and default selection. Test coexisting
   versions, recipe corrections, rollback, and user-owned configuration.
3. Add Git catalog acquisition with revision provenance and public/private sources.
   Test missing entries, invalid recipes, and explicit source selection.
4. Use FFmpeg to establish archive requirements: different platform providers,
   exact artifact identity, hashes, extraction, and publication. Its current
   pipeline-tools fetcher documents branch-based builds that can differ from a
   requested patch version; preserve actual artifact identity rather than claiming
   an exact match.
5. Migrate remaining pipeline-tools entries as their acquisition needs are covered.

Keep existing transaction limits visible: package publication and legacy dist
publication are separate operations today. Recipe support must not claim atomic
installation of packages and environment files without implementing it.

The public [package catalog](packages.md) lists validated recipe versions and target
coverage once available. It is not an allowlist for direct Python installation.
