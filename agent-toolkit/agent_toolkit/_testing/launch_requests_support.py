"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
from types import SimpleNamespace as _SimpleNamespace


def _prepare_exec_review_plan(root: pathlib.Path) -> None:
    (root / "plan.md").write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")


class TaskManager(_SimpleNamespace):
    def launcher_record_context(self) -> dict[str, str]:
        return {"status": "unresolved"}

    def launcher_session_id(self) -> None:
        """委譲元を解決しない。全体作業契約は起動時に保持した会話から解決する。"""
