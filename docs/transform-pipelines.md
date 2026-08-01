# Transform Pipelines

Pipelines prepare a target before its version is copied into the deployment.
Each step runs in sequence and passes its generated output to the next step.

## Python Function Steps

A function step identifies an importable callable. distman calls it with
`input`, `output`, and values from `options`:

```json
{
  "targets": {
    "config": {
      "source": "config/template.ini",
      "destination": "{DEPLOY_ROOT}/config/app.ini",
      "pipeline": {
        "tokens": {
          "func": "distman.transform.replace_tokens",
          "options": {
            "tokens": {
              "__ENV__": "{ENV}"
            }
          }
        }
      }
    }
  }
}
```

Built-in transform functions include:

| Function | Purpose |
|---|---|
| `distman.transform.replace_tokens` | Replace literal tokens in text files |
| `distman.transform.chmod` | Copy an object and apply an octal file mode |
| `distman.transform.byte_compile` | Compile Python files and remove source files in directories |
| `distman.transform.minify` | Minify JavaScript, CSS, HTML, or text |

JavaScript and HTML minification use optional dependencies. Install them when
the deployment pipeline needs those formats:

```bash
pip install "distman[minify]"
```

CSS and plain-text minification are implemented directly by distman.

## Shell Script Steps

`script` accepts one command or a list. Commands may use `{input}`, `{output}`,
and environment values:

```json
{
  "pipeline": {
    "format": {
      "script": [
        "black {output}",
        "python -m compileall {output}"
      ],
      "env": {
        "PYTHONHASHSEED": "0"
      }
    }
  },
  "targets": {
    "library": {
      "source": "lib/example",
      "destination": "{DEPLOY_ROOT}/lib/python/example",
      "pipeline": {}
    }
  }
}
```

Commands run through the system shell and a nonzero exit stops the deployment.
Treat pipeline configuration as trusted code.

## Global and Target Steps

A target pipeline is merged with the global pipeline. A target step with the
same name replaces the global definition. If a target omits its `pipeline`
key, no pipeline is run for that target; use an empty target pipeline when the
target should inherit global steps.

## Build Outputs

Intermediate outputs are created below:

```text
{BUILD_DIR}/{TRANSFORM_DIR}/{target}/{step}/
```

The defaults are `build/.distman`. The final intermediate object, rather than
the original source, becomes the deployed version.

Each step must contain exactly one of `func` or `script`. `options` must be an
object, and `env` supplies environment values for script steps.
