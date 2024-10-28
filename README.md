distman
=======

Distributes files and directories to versioned destinations defined in `dist.json` files.

## Installation

The easiest way to install:

```bash
$ pip install distman
```

Alternatively,

```bash
$ git clone https://github.com/rsgalloway/distman
$ cd distman
$ python setup.py install
```

Files and directories can be distributed from any folder or git repo containing a
`dist.json` file.

## Quickstart

`distman` looks for a dist file called `dist.json` at the root of a directory or
git repo. The dist file defines the file distrubution instructions.

The basic format of the `dist.json` file is:

```json
{
    "version": "1",
    "author": "<email>",
    "targets": {
        "<target>": {
            "source": "<source-path>",
            "destination": "<target-path>"
        },
    }
}
```

where `<source-path>` is the relative path to the source file or directory,
and `<target-path>` is the target destination path, and `<target>` is a named
target label to use when running `distman` commands. You can define as many targets
as you need.

See the `dist.json` file in this repo for an example.

Target paths can include environment variables, such as those defined in the
`distman.env` envstack file, where variables are defined with curly brackets only, e.g.:

```bash
"{DEPLOY_ROOT}/lib/python/distman"
```

When files are distributed (or disted), they are copied to a `versions` folder and
a symlink is created to the version. When a new version is disted, the version number
is incremented and the link is updated.

## Usage

To dist files defined in the dist.json file (use -d for dryrun):

```bash
$ distman [-d]
```

This will dist files to the `$DEPLOY_ROOT` folder defined in the `distman.env`
[envstack](https://github.com/rsgalloway/envstack) file and might look something
like this using default values:

```
$HOME/.local/distman/prod/
├── env
│   ├── distman.env -> versions/distman.env.0.c73fe42
│   └── versions
│       └── distman.env.0.c73fe42
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

To override the root folder, set the `$ROOT` env var, or update the `distman.env` stack file:

```bash
$ ROOT=/var/tmp/tools distman [-d]
```

## Config

Most congifation is done in the `distman.env` [envstack](https://github.com/rsgalloway/envstack) file.

Default config settings are in the config.py module. The following environment variables are supported:

| Variable            | Description |
|---------------------|-------------|
| $ROOT               | root directory to distrute files |
