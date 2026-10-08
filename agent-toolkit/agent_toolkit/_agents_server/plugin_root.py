"""長命な委譲サーバーが起動時の配布物を保持し、全起動入力へ同じ版を提供する。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from agent_toolkit._atk import managed_temp as _managed_temp

_LOG = logging.getLogger("agent-toolkit.agents-server.plugin-root")

# 更新で消える版付き配布物だけを既存のmanaged-tempへ保持する。
STABLE_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root"
STABLE_PLUGIN_ROOT_EXCLUDED = (".venv", "__pycache__", ".git")
_stable_plugin_roots: dict[Path, Path] = {}


def _plugin_root_is_versioned(plugin_root: Path) -> bool:
    """配布物rootが更新で消える版別ディレクトリ配下にあるかを返す。

    Codexホストの配布物rootは`<cache>/agent-toolkit/<版>/`であり、プラグインの更新で
    その版のディレクトリが除去される。版数は配布物の内側の`plugin.json`から取得する。
    """
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return False
    if not isinstance(version, str) or not version:
        return False
    return any(parent.name == version for parent in (plugin_root, *plugin_root.parents))


def resolve_stable_plugin_root(plugin_root: Path | None = None) -> Path:
    """役割文書・開始指示・内側MCPが共通で参照する保持済み配布物rootを返す。

    起動コマンドは委譲先のturnごとに実行されるため、解決結果は本プロセスの生存期間を通じて
    ディスク上の実体を必要とする。版別ディレクトリ配下の配布物は更新で除去されるため、
    managed-tempへ複製した実体を返す。複製元ごとの結果を保持し、同じ複製元に対する
    複製を本プロセスで1回に限る。
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
        area = _managed_temp.create_managed_temp(STABLE_PLUGIN_ROOT_PREFIX)
        destination = Path(area) / root.name
        shutil.copytree(
            root,
            destination,
            ignore=shutil.ignore_patterns(*STABLE_PLUGIN_ROOT_EXCLUDED),
            symlinks=True,
        )
    except (OSError, _managed_temp.ManagedTempError) as exc:
        _LOG.warning("配布物rootを複製できないため解決したrootを使います: root=%s error=%s", root, exc)
        _stable_plugin_roots[root] = root
        return root
    _LOG.info("版別ディレクトリの配布物rootを複製しました: source=%s destination=%s", root, destination)
    _stable_plugin_roots[root] = destination
    return destination


# backendが初めて使われる前に、サーバーが起動した版を確定して保持する。
SERVER_PLUGIN_ROOT = resolve_stable_plugin_root()
