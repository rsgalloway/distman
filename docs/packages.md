# Package catalog

The proposed `distman-packages` catalog will provide versioned installation recipes
and runtime environment files. Catalog installation is not implemented yet, and
no catalog recipes are currently advertised as supported.

**You do not need a recipe to install Python packages.** Direct installation already
uses pip to resolve compatible wheels from PyPI or your configured sources:

```bash
distman install numpy
distman install 'numpy==2.2.6'
distman install -r requirements.txt
```

See [Installing Python distributions](install.md) for runtime selection and limits.
A package's absence from this catalog does not prevent direct installation.

## Planned catalog entries

These are migration candidates from pipeline-tools, not validated catalog releases.

| Package | Recipe package version | Target coverage | Status |
|---|---|---|---|
| NumPy | 2.2.6 | To be validated per OS, architecture, and Python version | First wheel recipe planned |
| FFmpeg | To be selected | To be validated per OS and architecture | Archive acquisition design needed |

As recipes are validated, this table will link to their source and record exact
package versions, recipe revisions, and tested targets. Other pipeline-tools
packages can be added as their integrations are migrated. The site table should
ultimately be generated from validated catalog data rather than maintained as a
second version list.

## What a recipe adds

A recipe pairs package acquisition with ordinary envstack environment files and
custom distribution targets. Application environments can include the installed
package environment without hand-writing its deployment paths.

The proposed explicit syntax is:

```bash
# Planned; not available yet
distman install --recipe numpy@2.2.6
```

Direct `distman install numpy` will retain its existing meaning and will not silently
select a catalog recipe. Local and private recipes are also planned; no new hosted
registry service is required.

See the [installation roadmap](install-planning.md) for authoring, versioning, and
migration decisions.
