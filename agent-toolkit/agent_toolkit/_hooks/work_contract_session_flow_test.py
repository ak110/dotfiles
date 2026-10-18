"""作業ツリー版の公開hookと、fixtureが作成する作業契約の通知・圧縮復元を確認する。

記録を書く主体はfixtureで模擬する。実モデルによる生成とメイン実セッションでの復元は検証の範囲外である。
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
from typing import Any

import pytest

from agent_toolkit import hook
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import session_state
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


def test_public_hooks_preserve_pending_input_and_restore_fixture_contract(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開dispatcherから作業用領域を作成し、記録の所在・未処理区間と復元に必要な項目を確認する。"""
    session_id = "contract-main-fixture"
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-state")
    # 活動表示の保存は本試験から分離し、記録生成・状態保存・通知は実装を動かす。
    monkeypatch.setattr(hook, "_record_activity", lambda *_args, **_kwargs: None)

    def invoke(command: str, payload: dict[str, Any]) -> str:
        stream = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")), encoding="utf-8")
        monkeypatch.setattr(sys, "stdin", stream)
        assert hook.main([command]) == 0
        captured = capsys.readouterr()
        assert not captured.err
        response = json.loads(captured.out) if captured.out else {}
        return response.get("hookSpecificOutput", {}).get("additionalContext", "")

    startup = invoke("rules_context", {"hook_event_name": "SessionStart", "source": "startup", "session_id": session_id})
    directory = managed_temp.find_session_temp(session_id)
    assert directory is not None and directory.is_relative_to(tmp_path)
    assert str(directory) in startup
    path = directory / "work-contract.md"
    assert not path.exists()

    original = directory / "original.md"
    original.write_text("A全体を直し、Xを保持する。", encoding="utf-8")
    invoke("user_prompt_submit", {"session_id": session_id, "source": "user", "prompt": original.read_text(encoding="utf-8")})
    assert session_state.read_state(session_id)["work_contract_input_sequence"] == 1

    # 共通仕様を読むメインが記録を書く段階をfixtureで表す。hookによる自動生成とは区別する。
    path.write_text(
        "目的: A全体の修正\n有効要件: Xを保持し、A-bはcへ変更\n認可: 対象Aへの修正\n"
        f"原要求: {original}\n追加指示: 次の実ユーザー入力\n成果物: A、担当: メイン\n"
        "残る完成条件: A全体の検証\n次の工程: 残るAの実装\n"
        f"処理済み入力: {session_id}:1\n",
        encoding="utf-8",
    )
    notice = invoke("user_prompt_submit", {"session_id": session_id, "source": "user", "prompt": "A-bはc'へ変更して"})
    assert str(path) in notice
    assert f"未処理入力: {session_id}:2..2" in notice
    compact = invoke("rules_context", {"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id})
    assert str(path) in compact
    assert f"未処理入力: {session_id}:2..2" in compact

    # 通知が示す記録を1回読み、元の目的・制約・担当・残工程が取得できることを確かめる。
    recovered = path.read_text(encoding="utf-8")
    for value in (
        "A全体の修正",
        "Xを保持",
        "認可: 対象A",
        str(original),
        "担当: メイン",
        "A全体の検証",
        "次の工程: 残るAの実装",
    ):
        assert value in recovered
    assert session_state.read_state(session_id)["work_contract_input_sequence"] == 2
