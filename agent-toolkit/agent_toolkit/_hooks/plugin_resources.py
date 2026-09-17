"""通知本文が読込を求める配布物の絶対パスを解決する。

通知の受領側はplugin rootの実値を保持しないため、スキル名と相対パスだけを示すと
組み立てたパスへの読取が失敗し、そのあとで所在を探索する往復が生じる。
通知を生成するhookは自身のモジュールの位置からplugin rootを解決できるため、本文へ絶対パスを載せる。
"""

from __future__ import annotations

import pathlib

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]


def skill_reference(skill_name: str, relative_path: str) -> str:
    """スキル配下の資料を、スキル名と絶対パスの併記で返す。

    plugin rootから解決した実体が存在しない場合はスキル名と相対パスだけを返し、
    配布形態が異なる実行環境でも従来の本文へ戻る。
    """
    resolved = _PLUGIN_ROOT / "skills" / skill_name / relative_path
    if not resolved.exists():
        return f"`agent-toolkit:{skill_name}`の`{relative_path}`"
    return f"`agent-toolkit:{skill_name}`の`{relative_path}`（`{resolved}`）"
