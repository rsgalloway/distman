#!/usr/bin/env python
#
# Copyright (c) 2024-2025, Ryan Galloway (ryan@rsgalloway.com)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#  - Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
#  - Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
#  - Neither the name of the software nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#

__doc__ = """
Contains transform pipeline step classes and functions.
"""

import os
import shlex
import shutil
import subprocess
import importlib
from typing import Callable, Optional, Dict, List, Tuple, Any

from distman import config, util
from distman.logger import log
from distman.transform import TransformError

# define the allowed keys for pipeline steps
ALLOWED_KEYS = {"func", "script", "options", "env"}


class ValidationError(Exception):
    """Raised when a pipeline or step is improperly defined."""

    pass


def resolve_dotted_path(path: str) -> Callable:
    """Resolve a dotted path to a callable object.

    :param path: The dotted path to the callable, e.g. 'module.submodule.function'.
    :return: The callable object.
    """
    mod_path, _, attr = path.rpartition(".")
    module = importlib.import_module(mod_path)
    return getattr(module, attr)


def sort_pipeline(pipeline: dict) -> List[Tuple[str, dict]]:
    """Sort the pipeline steps by their order attribute:

        "pipeline": {
            "replace_tokens": {
                "func": "distman.transform.replace_tokens",
                "options": { "tokens": { "__VERSION__": "0.6.0" } },
                "order": 10
            },
            "byte_compile": {
                "func": "distman.transform.byte_compile",
                "order": 20
            }
        }

    :param pipeline: The pipeline dictionary to sort.
    :return: A sorted list of tuples (step_name, step_definition).
    """
    return sorted(pipeline.items(), key=lambda item: item[1].get("order", 0))


def run_script_step(cmd: str, env: dict = None) -> None:
    """Run a script command in a subprocess.

    :param script_cmd: The command to run as a string.
    :param env: Optional environment variables to set for the subprocess.
    :raises TransformError: If the script fails with a non-zero exit code.
    """
    try:
        log.info("Running: '%s'", cmd)
        subprocess.run(
            cmd,
            shell=True,
            check=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Pipeline step failed: {cmd}")
        log.error(e.stderr.decode())
        raise TransformError(f"Pipeline step failed with exit code {e.returncode}")


def run_pipeline(
    target, pipeline: Dict[str, Any], input_path: str, build_dir: str
) -> str:
    """Run a series of pipeline steps defined in the pipeline dictionary.

    :param target: The target object containing metadata like name.
    :param pipeline: A dictionary defining the pipeline steps.
    :param input_path: The path to the input file or directory.
    :param build_dir: The directory where output will be stored.
    :return: The path to the final output after all pipeline steps.
    """

    current = input_path

    # the directory where transformed files will be stored
    transform_dir = config.TRANSFORM_DIR

    for step_name, step in sort_pipeline(pipeline):
        log.info("Pipeline Step: '%s'", step_name)

        if os.path.isfile(current):
            output = os.path.join(
                build_dir,
                transform_dir,
                target.name,
                step_name,
                os.path.basename(input_path),
            )
            os.makedirs(os.path.dirname(output), exist_ok=True)
            shutil.copy2(current, output)
        elif os.path.isdir(current):
            output = os.path.join(build_dir, transform_dir, target.name, step_name)
            os.makedirs(output, exist_ok=True)
            util.safe_copytree(current, output)

        if "script" in step:
            commands = step["script"]
            if isinstance(commands, str):
                commands = [commands]
            for cmd in commands:
                cmd = cmd.format(input=shlex.quote(current), output=shlex.quote(output))
                run_script_step(cmd, env=step.get("env", None))

        elif "func" in step:
            func = resolve_dotted_path(step["func"])
            func(input=current, output=output, **step.get("options", {}))

        current = output

    return current


def get_pipeline_for_target(
    global_pipeline: Optional[dict], target_pipeline: Optional[dict]
) -> dict:
    """Combine global and target-specific pipelines.

    :param global_pipeline: The global pipeline definition.
    :param target_pipeline: The target-specific pipeline definition.
    :return: A combined pipeline dictionary.
    """
    if target_pipeline is None:
        return {}
    if global_pipeline is None:
        return target_pipeline or {}
    return {**global_pipeline, **(target_pipeline or {})}


def validate_pipeline_spec(pipeline: Optional[dict], context: str = "global") -> None:
    """Validate the structure of a pipeline specification. Format:

            "step_name": {
                "func": "package.module.function",
                "options": { "key": value },
                "order": 10
            },

    :param pipeline: The pipeline specification to validate.
    :param context: Context for error messages, e.g., "global" or "target".
    """
    if pipeline is None:
        return
    if not isinstance(pipeline, dict):
        raise ValidationError(f"{context} pipeline must be a dict or null")

    for step_name, step in sort_pipeline(pipeline):
        if not isinstance(step, dict):
            raise ValidationError(f"{context} step '{step_name}' must be a dict")

        has_script = "script" in step
        has_func = "func" in step
        unknown_keys = set(step) - ALLOWED_KEYS

        # check for unknown keys in the step
        if unknown_keys:
            raise ValidationError(
                f"Pipeline step '{step_name}' contains unknown keys: {', '.join(unknown_keys)}"
            )

        # check for required keys
        if not (has_script or has_func):
            raise ValidationError(
                f"{context} step '{step_name}' must have 'script' or 'func'"
            )

        # check for conflicting keys
        if has_func and has_script:
            raise ValidationError(
                f"{context} step '{step_name}': cannot have both 'script' and 'func'"
            )

        # check types of script and func
        if has_script and not isinstance(step["script"], (str, list)):
            raise ValidationError(
                f"{context} step '{step_name}': 'script' must be string or list"
            )
        if has_func and not isinstance(step["func"], str):
            raise ValidationError(
                f"{context} step '{step_name}': 'func' must be string"
            )

        # check for options
        if "options" in step and not isinstance(step["options"], dict):
            raise ValidationError(
                f"{context} step '{step_name}': 'options' must be a dict"
            )
