"""長時間稼働する委譲サーバーが起動時の版の配布物を保持し、委譲先の起動に使う役割文書・開始指示・内側MCPへ同じ版を渡す。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import plugin_root_leases

_LOG = logging.getLogger("agent-toolkit.agents-server.plugin-root")

# 更新で消える版付き配布物だけを既存のmanaged-tempへ保持する。
STABLE_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root"
STABLE_PLUGIN_ROOT_EXCLUDED = (".venv", "__pycache__", ".git")
_stable_plugin_roots: dict[Path, Path] = {}
_SHARED_MARKER = ".agent-toolkit-plugin-root.json"


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


def resolve_stable_plugin_root(plugin_root: Path | None = None) -> Path:
    """役割文書・開始指示・内側MCPが共通で参照する保持済み配布物rootを返す。

    起動コマンドは委譲先のturnごとに実行されるため、解決結果は本プロセスの生存期間を通じて
    ディスク上の実体を必要とする。版別ディレクトリ配下の配布物は更新で除去されるため、
    managed-tempへ複製した実体を返す。同じ配布元・版の完成済み実体をプロセス間で共有し、
    使用する全プロセスの生存期間を回収側へ伝える。
    複製に失敗した場合は解決したrootをそのまま返し、複製の失敗を委譲の不成立へ変えない。
    `plugin_root`には解決済みrootを渡し、省略時は自身の位置から解決する。
    """
    root = Path(__file__).resolve().parents[2] if plugin_root is None else plugin_root
    cached = _stable_plugin_roots.get(root)
    if cached is not None:
        return cached
    if not _plugin_root_is_versioned(root):
        _stable_plugin_roots[root] = root
        return root
    try:
        destination = _resolve_shared_root(root)
    except (OSError, ValueError, _managed_temp.ManagedTempError) as exc:
        _LOG.warning("配布物rootを複製できないため解決したrootを使います: root=%s error=%s", root, exc)
        return root
    _LOG.info("版別ディレクトリの配布物rootを複製しました: source=%s destination=%s", root, destination)
    _stable_plugin_roots[root] = destination
    return destination


def _resolve_shared_root(root: Path) -> Path:
    """配布元別の排他の下で完成済み実体を見つけ、使用登録を済ませて返す。"""
    # 保持先から起動した内側MCPも同じ実体を使い、自身の使用記録を加える。
    if (root.parent / _SHARED_MARKER).is_file():
        with plugin_root_leases.plugin_root_lock(root.parent):
            _managed_temp.validate_managed_temp(root.parent)
            record = json.loads((root.parent / _SHARED_MARKER).read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("root_name") == root.name:
                plugin_root_leases.register_plugin_root_use(root.parent)
                return root
    version = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    identity = {"source": str(root.resolve()), "version": version, "root_name": root.name}
    with plugin_root_leases.plugin_root_lock(root):
        entries = [
            *_managed_temp.list_managed_temp(STABLE_PLUGIN_ROOT_PREFIX),
            *_managed_temp.list_managed_temp(plugin_root_leases.SHARED_PLUGIN_ROOT_PREFIX),
        ]
        for entry in entries:
            area = Path(entry["path"])
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


# backendが初めて使われる前に、サーバーが起動した版を確定して保持する。
SERVER_PLUGIN_ROOT = resolve_stable_plugin_root()
