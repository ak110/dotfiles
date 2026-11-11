"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
from types import SimpleNamespace as _SimpleNamespace


def _prepare_exec_review_plan(root: pathlib.Path) -> None:
    """既存の入力名テストへ、関連WIのない読取可能な計画を用意する。"""
    (root / "plan.md").write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")


class TaskManager(_SimpleNamespace):
    """起動入力を記録するテスト用の代替にも、taskが読む直接の委譲元の情報を返す`launcher_record_context`を持たせる。"""

    def launcher_record_context(self) -> dict[str, str]:
        """起動入力を比較するテストでは、委譲元の記録が未解決であることを明示する。"""
        return {"status": "unresolved"}

    def launcher_session_id(self) -> None:
        """委譲元を解決しない。全体作業契約は起動時に保持した会話から解決する。"""
