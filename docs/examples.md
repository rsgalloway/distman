# Installation and activation examples

The repository provides complete configurations for installing packages and
launching applications through envstack. Each defines TOOLS_ROOT once and includes
both installation policy and runtime search paths.

| Example | Configuration and layout |
|---|---|
| [Flat](https://github.com/rsgalloway/distman/tree/feat/install/examples/flat) | One default.env supplies all settings; distman.env explicitly includes it |
| [Default](https://github.com/rsgalloway/distman/tree/feat/install/examples/default) | default.env includes tools.env for shared runtime paths and distman.env for installation policy |
| [NumPy](https://github.com/rsgalloway/distman/tree/feat/install/examples/numpy) | An optional package environment layered over the default example |

Follow the [example instructions](https://github.com/rsgalloway/distman/blob/feat/install/examples/README.md)
for preview, installation, import checks, and command execution. These links target
the feature branch while the examples await merge.

Both base layouts add `${TOOLS_ROOT}/bin` to PATH, plus the current shared wheel
script directory `${TOOLS_ROOT}/lib/python/bin`. Scripts have not moved yet.
They do not depend on the proposed recipe catalog or script-path settings. The flat
layout is dedicated to one platform, architecture, and Python runtime; the
separate NumPy example demonstrates package/version/runtime binary activation.

Installation does not activate the parent shell. Run applications through envstack
with the example directory selected on ENVPATH. Each base default.env already
includes shared runtime setup. Select the NumPy
stack explicitly for that package; the default example has no NumPy dependency.

See [installation policy and path templates](install.md#destinations) for customization.
