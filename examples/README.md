# Installation and activation examples

These configurations keep installation policy and runtime paths together, with
TOOLS_ROOT defined once per layout. They use the current installer, without
requiring the proposed recipe catalog.

| Example | Files | Layout |
|---|---|---|
| [Flat](flat/default.env) | All settings in default.env; distman.env only includes default | Pure and binary packages share one root for one runtime |
| [Default](default/default.env) | default.env → tools.env → distman.env | Shared pure libraries, separate package/version/runtime binary roots |
| [NumPy](numpy/numpy.env) | A package environment layered over the default example | Selects NumPy's versioned binary import and script paths |

## Shared PATH setup

Both base layouts add DISTMAN_SHARED_BIN_PATH (default `${TOOLS_ROOT}/bin`) to
PATH and DISTMAN_SHARED_LIB_PATH to PYTHONPATH. The installer writes wheel
entrypoints into those configured bin directories.

The default example contains no package-specific settings. NumPy is an optional,
separate example; it does not get included automatically by default.env.

## Before starting

Use a Python environment with `distman[install]` and envstack available. From the
distman checkout, save the absolute examples location:

```bash
DISTMAN_EXAMPLES="$PWD/examples"
```

Use one base layout at a time and select the same files for installation and
activation through ENVPATH. Exported ROOT, ENV, DEPLOY_ROOT, TOOLS_ROOT, and
DISTMAN_* values still override file policy. Default deployments go beneath
`~/.local/distman-examples`. Commands below use a POSIX shell; envstack normalizes
search-path separators on Windows.

## Flat

All configuration lives in `flat/default.env`, which includes nothing. A minimal
`distman.env` explicitly includes default; it contains no settings of its own.
Envstack does not automatically substitute default for a missing named stack.

The current envstack checkout retains include-tracking state across independent
loads, which can skip this include after distman's packaged policy has loaded it.
For now, explicitly activate the flat stack for installation as well as runtime:

```bash
cd "$DISTMAN_EXAMPLES/flat"
export ENVPATH="$PWD"
envstack -- distman install packaging --dryrun
envstack -- distman install packaging --yes
envstack -- python -c "import packaging; print(packaging.__file__)"
```

Both pure and binary packages use `${TOOLS_ROOT}/lib/python`. No package-specific
environment file is needed. Use exactly one platform, architecture, and Python
runtime per root. Packages form one compatible environment; restore matching library and bin
revisions when rolling it back.
Change TOOLS_ROOT once in default.env to relocate both install and activation paths.

## Default

The layered configuration separates responsibilities without duplicating roots:

```text
default.env
  tools.env       shared PATH and PYTHONPATH
    distman.env   roots and installer policy
```

```bash
cd "$DISTMAN_EXAMPLES/default"
export ENVPATH="$PWD"
distman install packaging --dryrun
distman install packaging --yes
envstack -- python -c "import packaging; print(packaging.__file__)"
envstack tools -r TOOLS_ROOT PYTHONPATH PATH
```

TOOLS_ROOT is defined only in distman.env. The default stack activates shared pure
packages. Binary packages retain the default package/version/runtime layout and
need their own runtime paths; the separate NumPy example demonstrates that.

## NumPy

Layer numpy.env over the default configuration rather than copying the base files:

```bash
cd "$DISTMAN_EXAMPLES/numpy"
export ENVPATH="$DISTMAN_EXAMPLES/numpy:$DISTMAN_EXAMPLES/default"
export PYVERSION=$(python -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))")
export PYARCH=$(python -c "import platform; print({'amd64':'x86_64','aarch64':'arm64'}.get(platform.machine().lower(), platform.machine().lower()))")
export NUMPY_VERSION=2.2.6
distman install "numpy==$NUMPY_VERSION" --dryrun
distman install "numpy==$NUMPY_VERSION" --yes
envstack numpy -- python -c "import numpy; print(numpy.__version__, numpy.__file__)"
envstack numpy -- python -m numpy.f2py -v
```

Use a supported CPython runtime such as Python 3.11 for NumPy 2.2.6. Runtime
variables come from the application's Python; embedded applications may need
explicit values. Re-evaluate these variables when changing interpreters. Detection
runs before activation because command substitution inside envstack's default
stack can recursively reload that stack.

The include chain is `numpy → tools → distman`; the base default stack remains
package-independent. NumPy adds its resolved import root to PYTHONPATH and the sibling runtime
bin directory to PATH. For Linux x86_64 Python 3.11:

```text
${TOOLS_ROOT}/numpy/2.2.6/linux/x86_64/python3.11/lib/python
${TOOLS_ROOT}/numpy/2.2.6/linux/x86_64/python3.11/bin
```

The same NUMPY_VERSION selects acquisition and activation. Changing TOOLS_ROOT
once relocates all paths. Changing the binary layout template itself requires
updating numpy.env to match; generating this integration is future recipe work.
An application can include numpy when it needs it, or launch through the explicit
numpy stack as shown. Activation does not install missing builds.

Generated POSIX commands select `python3` from PATH; put the application's matching
interpreter there. Windows commands select `python.exe`. For example, after NumPy
activation, `envstack numpy -- f2py -v` runs its installed entrypoint directly.

## Adopt a layout

Copy the flat or default directory into your configuration repository and select
it through ENVPATH. Add package environments separately as needed. Review includes
before merging with an existing studio default.env. Installation does not alter
the parent shell; launch through envstack to activate the supplied paths.
