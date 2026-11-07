# Environment activation

## Goal

Distman should make an installed environment as easy to enter and leave as a
virtualenv or Conda environment. Installation selects and publishes files;
activation selects the deployment policy and asks envstack to compose the runtime
environment. Envstack remains responsible for environment-variable composition,
while Distman defines the paths and package conventions it owns.

The canonical interactive workflow is:

```bash
distman activate <env>
lss
distman deactivate
```

For example:

```bash
distman activate foobar
```

Automation should use the same explicit environment name without changing the
calling shell:

```bash
distman activate foobar -- lss
```

## Shell model

A normal executable cannot change its parent shell. True virtualenv/Conda-style
activation therefore requires shell integration rather than pretending that the
Python process can export variables after it exits.

Envstack already provides command execution, an interactive environment shell,
`--export`, `--clear`, and the `export_env_to_shell`, `export`, and `clear` Python
APIs. Distman should delegate those generic operations rather than implement a
second activation engine. Its shell hook should only resolve the Distman
environment, supply its managed variables to envstack, and evaluate envstack's
result. Installation instructions can offer one-time shell setup comparable to
`conda init`. Users who do not install the hook can still run a command or use
envstack's existing interactive subshell.

The initial shell support should cover Bash and Zsh on POSIX and PowerShell on
Windows. Cmd support can follow if envstack can represent restoration safely.
Shell output must quote values using the target shell's rules; hand-built generic
quoting is not sufficient. Envstack's current export/clear implementation needs
hardening before Distman relies on it for in-process activation: it records only
one `_ES_OLD_*` layer, does not distinguish every unset and empty value, and emits
some values without robust shell quoting. These are general envstack concerns and
should be fixed there, with Distman consuming the improved API.

Activation must save the previous values of every variable it changes. Deactivation
restores those exact values, including whether a variable was originally unset.
Nested activation should use a stack so leaving an inner environment restores the
outer environment. Repeated activation of the current environment should be
idempotent.

## Environment selection

`distman activate <env>` should resolve the explicit environment name through the
same policy boundary as install, list, status, and uninstall. In the default policy,
the name supplies `ENV`, producing `${ROOT}/${ENV}`. An explicit deployment-root
option should remain available for project and studio layouts where a short
environment name is ambiguous.

Selection order should be consistent across commands:

1. The environment name and explicit deployment-root or policy options.
2. Active `ROOT`, `DEPLOY_ROOT`, `TOOLS_ROOT`, and `DISTMAN_*` overrides.
3. A discoverable Distman policy loaded through envstack.
4. Packaged standalone defaults.

The explicit positional name wins over an inherited `ENV` value. The command must
report the selected deployment root before entering it. Missing, ambiguous, or
incompatible environments should fail with a diagnostic that shows the policy
inputs and expected location.

## Persisted activation descriptor

An installation needs to retain enough resolved policy to reproduce its shared
runtime paths later. That descriptor belongs in centralized deployment metadata
beneath `.distman/`; it should not introduce package manifests or scatter generated
`.env` files through installed package roots.

The descriptor should record at least:

- schema version and deployment-root identity;
- environment name and effective policy source;
- shared library and command paths;
- unresolved runtime path templates used by binary packages;
- the variables Distman owns and their resolved values;
- creation/update provenance needed by status and diagnostics.

Secrets, package-index credentials, and unrelated inherited environment variables
must never be persisted. Updating the descriptor belongs in the same deployment
operation as package publication so activation never observes half-written policy.
Existing project-owned envstack files remain authoritative and can be referenced
rather than copied.

The exact record schema and interaction with deployment snapshots need design before
implementation. Activation metadata should participate in status and recovery, but
ordinary package uninstall must not remove the environment descriptor while other
packages remain.

## Runtime composition

For the default shared layout, activation adds:

```yaml
PYTHONPATH: ${DISTMAN_SHARED_LIB_PATH}:${PYTHONPATH}
PATH: ${DISTMAN_SHARED_BIN_PATH}:${PATH}
```

This makes pure-Python libraries and their entrypoints available together. Distman
must avoid duplicating path entries during repeated or nested activation.

Runtime-specific binary packages cannot all be activated at once merely because
they are installed. Their platform, architecture, Python runtime, and package
version must be selected through package environments or recipes. Activation should
compose those envstack environments and reject incompatible selections with a clear
explanation. It should not guess a binary version from the newest directory on disk.

## Command behavior

The planned commands are:

| Command | Behavior |
| --- | --- |
| `distman activate <env>` | With the shell hook, modify the current shell and record restoration state |
| `distman activate <env> -- COMMAND` | Run one command in the selected environment and return its status |
| `distman activate <env> --show` | Display the selected root, policy sources, and resulting managed variables |
| `distman deactivate` | Restore the preceding environment through the shell hook |

Without a shell hook, interactive activation should launch envstack's existing
activated subshell or print a concise setup command. It must not silently claim that
the parent shell was changed. Command execution should use envstack's Python API so
Distman can preserve exit codes, signals, logging, and platform behavior without
depending on discovery of an `envstack` executable in PATH.

Keyboard interrupts and termination signals should reach the child command. POSIX
can replace the wrapper process when appropriate; Windows needs equivalent child
process and exit-code handling. An interactive subshell should use the user's
configured shell (`SHELL` or `COMSPEC`) and mark the active Distman environment in
the prompt only through normal envstack composition.

## Acceptance criteria

- A standalone `pip install distman` followed by package installation can use
  `distman activate <env>` without requiring the user to author an envstack file.
- Bash, Zsh, and PowerShell can activate and deactivate while restoring exact prior
  values.
- `distman activate <env> -- COMMAND` works without shell initialization and
  returns the command's exit status.
- Shared libraries and entrypoints are usable immediately after activation.
- Nested activation, repeated activation, paths containing spaces, unset variables,
  and interrupted commands are covered on Linux, macOS, and Windows.
- Custom studio policy and project-owned envstack files remain composable and
  inspectable; Distman does not hide which sources produced the environment.
- Binary package activation remains explicit and compatibility-checked.
- Install, list, status, uninstall, and activate resolve the same deployment when
  given the same environment name and policy inputs.

## Implementation sequence

1. Define named-environment resolution and the centralized activation descriptor.
2. Implement noninteractive `activate <env> -- COMMAND` and `activate <env> --show`.
3. Harden envstack's shell quoting, unset/empty restoration, and nested state model.
4. Add thin Bash/Zsh hooks and `deactivate`, followed by PowerShell.
5. Delegate interactive subshell behavior to envstack for users without a hook.
6. Integrate package recipes and runtime-specific envstack environments.
