"""公開ランチャーを検証する試験の共有依存環境を、一時領域へ準備する。"""

import os
import pathlib
import shutil
import sys

from agent_toolkit._common import plugin_environment, process_tree


def prepare_plugin_environment(root: pathlib.Path, environment: dict[str, str], *, timeout: float = 60) -> pathlib.Path:
    """指定した一時runtimeを公開prepareで準備し、後続の通常起動へ同じ環境を渡す。

    依存を新規の環境へ複製する呼出元は、OSごとの複製時間に合わせて`timeout`（秒）を指定する。
    """
    assert environment.get(plugin_environment.RUNTIME_DATA_ENV), "試験専用のruntime領域を指定する"
    directories = []
    for directory in os.get_exec_path(environment):
        uv = shutil.which("uv", path=directory)
        # PATH隔離はshimへのsymlinkを数値名ディレクトリへ移すため、実体も確認する。
        if pathlib.Path(directory).name == "shims" or (uv and pathlib.Path(uv).resolve().stem.casefold() == "mise"):
            continue
        directories.append(directory)
    search_path = os.pathsep.join(directories)
    python_directory = pathlib.Path(sys.base_prefix) / "bin" if os.name == "posix" else pathlib.Path(sys.base_prefix)
    environment["PATH"] = str(python_directory) + os.pathsep + search_path
    environment.update(UV_PYTHON=sys.executable, UV_PYTHON_DOWNLOADS="never")
    result = process_tree.run_with_timeout(
        [sys.executable, str(root / "scripts/launch.py"), "--prepare", "agent_toolkit/agents_server_mcp.py"],
        cwd=root,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stderr
    python = plugin_environment.prepared_python(root, environment)
    assert python is not None
    return python
