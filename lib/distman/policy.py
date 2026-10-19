"""Resolve optional envstack policy without changing the calling environment."""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from distman.install.targets import Target
    from distman.install.wheels import Artifact

from distman.install import InstallError


class Policy:
    """Resolved installation paths, with active tier overrides taking precedence."""

    def __init__(self) -> None:
        """Load packaged defaults and optional studio policy without leaking state.

        :raises ImportError: If the optional envstack or pathbase integration is unavailable.
        """
        # Some pathbase releases call envstack.init at import time. Contain that
        # side effect, including when this command is called from Python.
        active = dict(os.environ)
        python_path = list(sys.path)
        try:
            from envstack import load_environ, resolve_environ
            from pathbase import Template

            os.environ.clear()
            os.environ.update(active)
            values = load_environ(str(Path(__file__).parent / "resources" / "install.env"))
            values.update(load_environ("distman", ignore_missing=True))
            values.setdefault(
                "HOME", active.get("HOME") or active.get("USERPROFILE") or str(Path.home())
            )
            for key in ("ROOT", "ENV", "DEPLOY_ROOT", "TOOLS_ROOT"):
                if active.get(key):
                    values[key] = active[key]
            for key, value in active.items():
                if key.startswith("DISTMAN_"):
                    values[key] = value
            self.values = dict(resolve_environ(values))
            self.template = Template
        finally:
            sys.path[:] = python_path
            os.environ.clear()
            os.environ.update(active)

    @property
    def deploy_root(self) -> Path:
        """Return the selected write root, independent of runtime lookup paths.

        :return: Absolute selected deployment root with the user home expanded.
        """
        return Path(self.values["DEPLOY_ROOT"]).expanduser().absolute()

    def path(self, key: str, artifact: "Artifact", target: "Target") -> Path:
        """Format a policy template for an artifact/runtime and require an absolute path.

        :param key: Policy variable naming the pathbase template to format.
        :param artifact: Wheel identity supplying normalized package and upstream version
            fields.
        :param target: Runtime supplying platform, architecture, and Python version fields.
        :return: Absolute formatted installation or target path.
        :raises KeyError: If the named policy template is absent.
        :raises InstallError: If the result is relative or contains unresolved variable tokens.
        """
        fields = dict(self.values)
        fields.update(
            package=artifact.name,
            version=artifact.version,
            platform=target.platform,
            arch=target.arch,
            python_version=target.python,
        )
        value = self.template(self.values[key], env=self.values).format(**fields)
        if "$" in value or "{" in value or "}" in value:
            raise InstallError(f"Unresolved installation path: {value}")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise InstallError(f"Installation path must be absolute: {value}")
        return path
