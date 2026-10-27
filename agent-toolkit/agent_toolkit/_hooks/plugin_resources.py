"""通知本文が読込を求める配布物の絶対パスを解決する。

通知の受領側はplugin rootの実値を保持しないため、スキル名と相対パスだけを示すと
組み立てたパスへの読取が失敗し、そのあとで所在を探索する往復が生じる。
通知を生成するhookは委譲元が指定した資料rootを使い、無指定時は自身のモジュールの位置から
plugin rootを解決して本文へ絶対パスを載せる。
"""

from __future__ import annotations

import json
import os
import pathlib

from agent_toolkit._common import delegated_session
from agent_toolkit._common.plugin_roots import PluginRootError

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]


def plugin_root() -> pathlib.Path:
    """委譲元が指定した資料rootを検証し、無指定ならhook自身の配布物を使う。"""
    value = os.environ.get(delegated_session.PLUGIN_ROOT_ENV)
    if value is None:
        return _PLUGIN_ROOT
    root = pathlib.Path(value)
    try:
        if not value or not root.is_absolute() or not (root / "skills").is_dir():
            raise ValueError("絶対パスまたはskillsが不正")
        manifest = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("name") != "agent-toolkit":
            raise ValueError("pluginの識別が不正")
    except (OSError, UnicodeError, ValueError) as error:
        raise PluginRootError(
            f"{delegated_session.PLUGIN_ROOT_ENV}の資料rootが不正: {value}: {error}。委譲元へ起動時のroot指定の修正を求めよ"
        ) from error
    return root.resolve()


def skill_document_path(skill_name: str, relative_path: str) -> pathlib.Path:
    """スキル配下の資料の、稼働版のplugin rootから解決した絶対パスを返す。実在は確かめない。"""
    return plugin_root() / "skills" / skill_name / relative_path


def skill_reference(skill_name: str, relative_path: str) -> str:
    """スキル配下の資料を、スキル名と絶対パスの併記で返す。

    plugin rootから解決した実体が存在しない場合はスキル名と相対パスだけを返し、
    配布形態が異なる実行環境でも従来の本文へ戻る。
    """
    resolved = skill_document_path(skill_name, relative_path)
    if not resolved.exists():
        return f"`agent-toolkit:{skill_name}`の`{relative_path}`"
    return f"`agent-toolkit:{skill_name}`の`{relative_path}`（`{resolved}`）"
