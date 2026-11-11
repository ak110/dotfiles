"""mcp_toolsの分割試験が共有する準備処理。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib

import pytest

from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._testing.agents_server_support import _manager_with_fake, _observed_input_params, _recording_candidates


def _write_review_plan(root: pathlib.Path) -> None:
    """継続配送の検査が使う計画を、関連WIのない通常ファイルとして用意する。"""
    (root / "plan.md").write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")


async def _start_exec_review_session(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    """実行レビュー担当の役割文書で偽のcodex sessionを公開`start`から起動し、継続入力の検証に使う。"""
    _write_review_plan(tmp_path)
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path="exec-review",
            extra_params=_observed_input_params("exec-review.subagent.md", tmp_path),
        )
        return manager, backend, started
