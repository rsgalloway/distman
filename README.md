distman
=======

Simple software distribution for complex pipelines,

`distman` is a config-driven deployment tool that performs safe, versioned rollouts
of software, scripts, and configuration files to predefined locations on the
filesystem. It’s ideal for environments where deterministic deployments,
environment-aware transforms, and clean rollback are more important than packaging
or pip installation.

When used alongside [envstack](https://github.com/rsgalloway/envstack), distman
can act as a drop-in alternative to virtualenv or Python packaging, enabling you
to:

- Install Python tools and libraries via source-controlled builds
- Swap or rollback versions using atomic symlinks
- Configure and isolate runtime environments using .env files
- Avoid the overhead of pip/venv/pyproject setups for internal tools or scripts

## Installation

The easiest way to install:

```bash
$ pip install -U distman
```

Alternatively, use the provided Makefile to build the requirements and dist to
the deplyoment area:

```bash
$ make install
```

Files, directories and links can be distributed from any folder or git repo
containing a `dist.json` file.

## Quickstart

`distman` looks for a dist file called `dist.json` at the root of a directory or
git repo. The dist file defines the file distrubution instructions.

The basic format of the `dist.json` file is:

```json
{
    "author": "<email>",
    "targets": {
        "<target>": {
            "source": "<source-path>",
            "destination": "<target-path>"
        },
    }
}
```

where `<source-path>` is the relative path to the source file, directory or
link, and `<target-path>` is the target destination path, and `<target>` is a
named target label to use when running `distman` commands. You can define as
many targets as you need.

See the `dist.json` file in this repo for an example.

Target paths can include environment variables, such as those defined in the
`distman.env` envstack file, where variables in paths are defined with curly
brackets only, e.g.:

```bash
"{DEPLOY_ROOT}/lib/python/distman"
```

When files are distributed (or disted), they are copied to a `versions` folder
and a symlink is created to the version. When a new version is disted, the
version number is incremented and the link is updated.

#### Wildcards

You can use shell-style wildcards (e.g., *) in the "source" field of a target
definition to match multiple files or directories. This is useful when you want
to distribute a group of files without listing each one individually.

When using wildcards, you must also use numeric substitution variables (%1, %2,
etc.) in the "destination" path. These correspond to the wildcard matches in
order of appearance.

```json
"targets": {
  "build": {
    "source": "build/*.py",
    "destination": "{DEPLOY_ROOT}/lib/python/%1"
  }
}
```

In this example:

- `build/*.py` expands to all `.py` files in the `build/` folder.
- Each matched file is symlinked to `{DEPLOY_ROOT}/lib/python/filename.py`.

> Wildcards are expanded at runtime using Python's glob and fnmatch mechanisms.
Matching results are processed and symlinked individually.

#### Transforms

`distman` supports a customizable pipeline of transforms that operate on each
target before final distribution. These transforms can run:

- Custom Python functions (func)
- Shell commands (script)

Pipeline steps can be defined globally at the top of a dist.json file, or
per-target:

```json
"pipeline": {
    "formatting": {
        "script": ["black --check {input}"]
    },
    "replace_tokens": {
        "func": "distman.transform.replace_tokens",
        "options": {
            "tokens": { "__VERSION__": "1.2.3" }
        }
    }
}
```

## Usage

To dist files defined in a `dist.json` file (remove -d when ready):

```bash
$ dist -d
```

This will dist files to the `${DEPLOY_ROOT}` folder defined in the provided
`distman.env` [envstack](https://github.com/rsgalloway/envstack) file and might
look something like this when disted:

```
${DEPLOY_ROOT}
├── bin
│   ├── distman -> versions/distman.0.c73fe42
│   └── versions
│       └── distman.0.c73fe42
└── lib
    └── python
        ├── distman -> versions/distman.0.c73fe42
        └── versions
            └── distman.0.c73fe42
                ├── cli.py
                ├── config.py
                ├── dist.py
                ├── __init__.py
                ├── logger.py
                ├── source.py
                └── util.py
```

To override the deployment folder, update the `distman.env` environment stack
file then re-dist:

```bash
$ dist [-d]
```

By default, `distman` dists to a prod folder under `${DEPLOY_ROOT}`. This can be
changed at any time using `${ENV}` or updating or modifying the `distman.env`
envstack file:

```bash
$ ENV=dev dist [-d]
```

This will change `prod` to `dev` in the target deplyment path. This is useful
for deploying files or code to different development environments.

## Dist Info

When disting files, `distman` will create hidden dist info files that meta data
about the source files. For example, if the source file is called `foobar.py`
then the dist info file that will be created will be called `.foobar.py.dist`.
The dist info files will be created at the deployment root.

## Config

Most configuration is done in the `distman.env`
[envstack](https://github.com/rsgalloway/envstack) file.

Default config settings are in the config.py module. The following environment
variables are supported:

| Variable        | Description |
|-----------------|-------------|
| $DEPLOY_ROOT    | file deployment root directory |
| $ENV            | target environment (e.g. prod or dev) |
| $IGNORE_MISSING | ignore missing source paths in targets |
| $LOG_DIR        | directory to write log files |
| $LOG_LEVEL      | logging level to use (DEBUG, INFO, etc) |
| $ROOT           | dist root directory |
