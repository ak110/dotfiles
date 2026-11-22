import os
import pathlib
import subprocess
import sys
from typing import cast

import pytest

from agent_toolkit._testing import pytest_plugin

# pylint: disable=protected-access


class _Config:
    def __init__(self, rootpath: pathlib.Path) -> None:
        self.rootpath = rootpath


def _config(rootpath: pathlib.Path) -> pytest.Config:
    return cast(pytest.Config, _Config(rootpath))


def _run_child_pytest(
    tmp_path: pathlib.Path, *arguments: str, plugin: str = pytest_plugin.__name__
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYFLTR_CACHE_DIR"] = str(tmp_path / "pyfltr")
    argv = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts="]
    return subprocess.run(
        [*argv, *arguments, "-p", plugin],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
