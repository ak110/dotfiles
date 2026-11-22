from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_toolkit._agents_server import role_runtime
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import inherited_venv, plugin_environment, plugin_root_leases

_LOG = logging.getLogger("agent-toolkit.agents-server.plugin-root")


STABLE_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root"
STABLE_PLUGIN_ROOT_EXCLUDED = (".venv", "__pycache__", ".git")
_stable_plugin_roots: dict[Path, Path] = {}
_SHARED_MARKER = ".agent-toolkit-plugin-root.json"


def plugin_source_root(root: Path) -> Path:
    marker = root.parent / _SHARED_MARKER
    if not marker.is_file():
        return root
    record = json.loads(marker.read_text(encoding="utf-8"))
    if (
        not isinstance(record, dict)
        or record.get("root_name") != root.name
        or not isinstance(record.get("source"), str)
        or not Path(record["source"]).is_absolute()
    ):
        raise ValueError(f"保持配布物の導入元が不正: {marker}")
    return Path(record["source"])


def _plugin_root_is_versioned(plugin_root: Path) -> bool:
    """配布物rootが更新で消える版別ディレクトリ配下にあるかを返す。

    Codexホストの配布物rootは`<cache>/agent-toolkit/<版>/`であり、プラグインの更新で
    その版のディレクトリが除去される。版数は配布物の内側の`plugin.json`から取得する。
    """
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError:
        return False
    if not isinstance(version, str) or not version:
        return False
    return any(parent.name == version for parent in (plugin_root, *plugin_root.parents))


def resolve_stable_plugin_root(plugin_root: Path | None = None, *, independent: bool = False) -> Path:
    """役割文書・開始指示・内側MCPが共通で参照する保持済み配布物rootを返す。

    起動コマンドは委譲先のturnごとに実行されるため、解決結果は本プロセスの生存期間を通じて
    ディスク上の実体を必要とする。版別ディレクトリ配下の配布物は更新で除去されるため、
    managed-tempへ複製した実体を返す。同じ配布元・版の完成済み実体をプロセス間で共有し、
    使用する全プロセスの生存期間を回収側へ伝える。
    複製に失敗した場合は解決したrootをそのまま返し、複製の失敗を委譲の不成立へ変えない。
    `plugin_root`には解決済みrootを渡し、省略時は自身の位置から解決する。
    `independent=True`では変更可能なcheckoutは新規に保持し、複製失敗を伝播させる。
    同じ保持rootからの子は完成マーカーを使い、自身のleaseを登録して同じ実体を使う。
    """
    root = Path(__file__).resolve().parents[2] if plugin_root is None else plugin_root
    cached = None if independent else _stable_plugin_roots.get(root)
    if cached is not None:
        return cached
    versioned = _plugin_root_is_versioned(root)
    if not independent and not versioned:
        _stable_plugin_roots[root] = root
        return root
    try:
        destination = _resolve_shared_root(root, fresh_input=independent and not versioned)
    except (OSError, ValueError, _managed_temp.ManagedTempError) as exc:
        if independent:
            raise
        _LOG.warning("配布物rootを複製できないため解決したrootを使います: root=%s error=%s", root, exc)
        return root
    _LOG.info("保持する配布物rootを解決しました: source=%s destination=%s", root, destination)
    if not independent:
        _stable_plugin_roots[root] = destination
    return destination


def _resolve_shared_root(root: Path, *, fresh_input: bool = False) -> Path:
    """配布元別の排他の下で完成済み実体を見つけ、使用登録を済ませて返す。"""

    if (root.parent / _SHARED_MARKER).is_file():
        with plugin_root_leases.plugin_root_lock(root.parent):
            area = root.parent
            managed_root = _managed_temp.temp_root_path()
            if managed_root.is_symlink() or managed_root.is_junction():
                raise _managed_temp.ManagedTempError(f"一時rootがリンクであるため使用しない: {managed_root}")
            try:
                if area.parent.samefile(managed_root):
                    area = managed_root / area.name
            except FileNotFoundError:
                pass
            _managed_temp.validate_managed_temp(area)
            record = json.loads((root.parent / _SHARED_MARKER).read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("root_name") == root.name:
                plugin_root_leases.register_plugin_root_use(root.parent)
                return root
    version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    identity = {"source": str(root.resolve()), "version": version, "root_name": root.name}
    with plugin_root_leases.plugin_root_lock(root):
        for area in () if fresh_input else _managed_temp.list_managed_temp(STABLE_PLUGIN_ROOT_PREFIX):
            with plugin_root_leases.plugin_root_lock(area):
                try:
                    marker = json.loads((area / _SHARED_MARKER).read_text(encoding="utf-8"))
                except OSError, ValueError:
                    continue
                if marker != identity or not (area / root.name).is_dir():
                    continue
                plugin_root_leases.register_plugin_root_use(area)
                return area / root.name
        # 作成途中も旧形式と区分し、コピー失敗時は同namespaceのMCPが生存中でも回収する。
        # 完成済みの意味はshared markerに置き、作成中の使用は領域のロックで保護する。
        area = _managed_temp.create_managed_temp(plugin_root_leases.SHARED_PLUGIN_ROOT_PREFIX)
        destination = Path(area) / root.name
        try:
            with plugin_root_leases.plugin_root_lock(area):
                shutil.copytree(
                    root,
                    destination,
                    ignore=shutil.ignore_patterns(*STABLE_PLUGIN_ROOT_EXCLUDED),
                    symlinks=True,
                )
                (area / _SHARED_MARKER).write_text(json.dumps(identity), encoding="utf-8")
                plugin_root_leases.register_plugin_root_use(area)
        except OSError:
            _managed_temp.cleanup_managed_temp(area)
            raise
        return destination


def main(arguments: list[str]) -> int:
    if len(arguments) != 2 or arguments[0] not in {"--wi-submit-worker", "--process-loop-worker"}:
        return 2
    root = resolve_stable_plugin_root(independent=True)
    executable = plugin_environment.prepared_python(root, os.environ)
    if executable is None:
        raise RuntimeError("独立投入の共有依存環境が未準備。公開launcherで依存環境を準備して再実行する")
    environment = plugin_environment.cli_environment(root, os.environ)
    environment["PYTHONPATH"] = str(root)

    inherited_venv.record_launch_value(environment, "PYTHONPATH", os.environ.get("PYTHONPATH"))
    command = (
        [str(executable), str(root / "agent_toolkit" / "atk.py"), "wi", "process-loop", "--internal-target", arguments[1]]
        if arguments[0] == "--process-loop-worker"
        else [str(executable), "-m", "agent_toolkit._atk.wi.submit", "worker", arguments[1]]
    )
    if os.name == "nt":
        return subprocess.run(command, env=environment, check=False).returncode
    os.execvpe(str(executable), command, environment)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
else:
    SERVER_RUNTIME_CODE = role_runtime.RuntimeCode.capture(Path(__file__).resolve().parents[2])
    SERVER_PLUGIN_ROOT = resolve_stable_plugin_root()
