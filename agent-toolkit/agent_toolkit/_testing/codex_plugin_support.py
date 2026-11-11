"""Codex試験の配布物保持と実MCPの準備・回収を共有する。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import sys
from collections.abc import AsyncGenerator, Callable
from typing import Any

import psutil
import pytest

from agent_toolkit._common import plugin_environment
from agent_toolkit._testing.isolation import CONFIG_DIRECTORY_ENVIRONMENT_NAMES, HOME_ENVIRONMENT_NAMES

# 試験が観測する内部境界を準備する。
# pylint: disable=protected-access

_CLEANUP_ENTRY_SOURCE = """\"\"\"内部の回収APIへ1件の対象を渡し、拒否を終了コード2で返す試験用スクリプト。\"\"\"
import sys

from agent_toolkit._atk import managed_temp

try:
    managed_temp.cleanup_managed_temp(sys.argv[1])
except managed_temp.ManagedTempError as error:
    print(error, file=sys.stderr)
    sys.exit(2)
"""


def _cleanup_entry(directory: pathlib.Path) -> pathlib.Path:
    """公開CLIを持たない回収APIを、別プロセスから実物のまま呼ぶスクリプトを作成する。"""
    entry = directory / "cleanup_entry.py"
    entry.write_text(_CLEANUP_ENTRY_SOURCE, encoding="utf-8")
    return entry


@contextlib.asynccontextmanager
async def _owned_async_child(*args: Any, **kwargs: Any) -> AsyncGenerator[asyncio.subprocess.Process]:
    """子の獲得直後からpipeと終了を所有し、正常終了待ちの失敗でもkillして回収する。"""
    process = await asyncio.create_subprocess_exec(*args, **kwargs)
    try:
        yield process
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
            await asyncio.wait_for(process.communicate(), timeout=5)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.communicate(), timeout=5)
            raise


def _make_plugin_root(base: pathlib.Path, version: str, *, versioned: bool, root_is_version: bool = False) -> pathlib.Path:
    """`plugin.json`を持つ配布物rootを、版別ディレクトリの有無を変えて作成する。"""
    root = base / version / "agent-toolkit" if versioned else base / "checkout" / "agent-toolkit"
    if root_is_version:
        root = base / version
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(f'{{"version": "{version}"}}\n', encoding="utf-8")
    (root / "agent_toolkit").mkdir()
    (root / "agent_toolkit" / "agents_server_mcp.py").write_text("# entry\n", encoding="utf-8")
    (root / "scripts").mkdir()
    for name in ("launch.py", "atk", "atk.cmd"):
        (root / "scripts" / name).write_text("# entry\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    return root


def _lease_plugin_source(base: pathlib.Path, version: str = "1.0.0") -> pathlib.Path:
    root = _make_plugin_root(base, version, versioned=True, root_is_version=True)
    (root / "payload.txt").write_text("起動した版", encoding="utf-8")
    return root


# 実MCP試験の依存導入（UV_LINK_MODE=copyで新規環境へ全パッケージを複製する）の上限（秒）。
# 60秒ではWindows CIで超過した。2026年10月23〜24日のWindows CI 9runでは、
# 旧形式試験のworker作成から判定までが43〜77秒（試験開始の遅れを含む上限値で、
# 導入単独の所要は未取得）だった。所要分布を保証する値ではないため、
# 導入方式や対象試験を変える場合は同じ区間の所要を測り直して比べる。
_REAL_MCP_PREPARE_TIMEOUT = 120


def _real_mcp_environment(
    tmp_path: pathlib.Path,
    tmp_path_factory: pytest.TempPathFactory,
    host_environ: Callable[[], dict[str, str]],
) -> dict[str, str]:
    """公開ランチャー用のホーム・状態を隔離し、ホストのPythonと取得済みパッケージを使う。"""
    environment = host_environ()
    for name in HOME_ENVIRONMENT_NAMES:
        environment[name] = str(tmp_path / "home")
    for name in CONFIG_DIRECTORY_ENVIRONMENT_NAMES:
        environment[name] = str(tmp_path / name.lower())
    state_base = tmp_path / "runtime-state"
    environment.update(XDG_STATE_HOME=str(state_base), LOCALAPPDATA=str(state_base))
    # hash配下の拡張DLLをWindowsのパス長制限内で読み込めるよう、短いworker直下へ置く。
    environment[plugin_environment.RUNTIME_DATA_ENV] = str(tmp_path_factory.mktemp("mcp"))
    environment["PATH"] = os.pathsep.join(
        directory for directory in os.get_exec_path(environment) if pathlib.Path(directory).name != "shims"
    )
    # 隔離HOMEでもテストを動かしているPythonを公開ランチャーから解決し、環境ごとの再取得を避ける。
    python_directory = pathlib.Path(sys.base_prefix) / "bin" if os.name == "posix" else pathlib.Path(sys.base_prefix)
    environment["PATH"] = str(python_directory) + os.pathsep + environment["PATH"]
    # CIのuv管理Pythonも使用する。内側のuvには実行中のPythonを明示する。
    environment.pop("UV_NO_MANAGED_PYTHON", None)
    environment.update(UV_PYTHON=sys.executable, UV_PYTHON_DOWNLOADS="never", UV_LINK_MODE="copy")
    for name in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        environment.pop(name, None)
    return environment


def _wait_mcp_processes(owned: dict[int, psutil.Process], *, timeout: float = 3) -> None:
    """所有プロセスの終了を待ち、待機の結果と失敗の両方で同じidentityの生存を再確認する。"""
    try:
        _, alive = psutil.wait_procs(list(owned.values()), timeout=timeout)
    except OSError as error:
        # pidfd_openが終了済みPIDでEINVALを返した実観測への対処。
        # 生存と取得不能は失敗を保ち、終了を確認した場合だけ待機失敗から進める。
        alive = []
        for process in owned.values():
            try:
                if process.is_running() and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    alive.append(process)
            except psutil.NoSuchProcess:
                continue
        if alive:
            raise
        print(f"MCP終了の再確認: wait_error={error}; 全所有identityの生存なし")
    # wait_procsの最終pollから返却までに終了したidentityを残存と扱わない。
    # is_runningはPIDと生成時刻の組で比べるため、再利用されたPIDを同じプロセスとみなさない。
    # zombieはis_runningが真を返すため、ここでは残存として失敗を保つ。
    remaining = []
    for process in alive:
        try:
            if process.is_running():
                remaining.append(f"pid={process.pid} status={process.status()}")
        except psutil.NoSuchProcess:
            continue
    assert not remaining, (
        f"MCP終了確認: timeout={timeout}秒の待機後も所有identityが生存: {remaining}。"
        "スタックトレースで_wait_mcp_processesの呼び出し元を確認し、"
        "そのテストのClientSessionとstdio_clientの終了処理を調べ、"
        "上記PIDの生成時刻・状態を確認して停止未達の処理を特定する。"
    )


# 90cad03ac601a24107d34758748f1d022ed5342cのplugin_root.pyにある、版別rootの
# 正常系の保持生成を固定するfixture。旧版と同じprefix/copytree/除外/cacheを使い、
# leaseと共有markerを生成しない。版判定と失敗時fallbackはこの試験の対象外。
# MCP本体・公開launcher・managed-temp・診断ログは現行のpluginコピーを使う。
_LEGACY_PLUGIN_ROOT_SOURCE = '''"""旧形式の正常系保持生成を実MCPへ提供する試験fixture。"""
from pathlib import Path
import shutil
from agent_toolkit._atk import managed_temp

STABLE_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root"
STABLE_PLUGIN_ROOT_EXCLUDED = (".venv", "__pycache__", ".git")
_stable_plugin_roots = {}

def resolve_stable_plugin_root(plugin_root=None):
    root = Path(__file__).resolve().parents[2] if plugin_root is None else plugin_root
    if root not in _stable_plugin_roots:
        area = managed_temp.create_managed_temp(STABLE_PLUGIN_ROOT_PREFIX)
        destination = Path(area) / root.name
        shutil.copytree(
            root, destination,
            ignore=shutil.ignore_patterns(*STABLE_PLUGIN_ROOT_EXCLUDED), symlinks=True,
        )
        _stable_plugin_roots[root] = destination
    return _stable_plugin_roots[root]

SERVER_PLUGIN_ROOT = resolve_stable_plugin_root()
'''
