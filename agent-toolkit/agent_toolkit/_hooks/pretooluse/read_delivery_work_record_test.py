"""作業記録（メインの作業契約、委譲先の引き継ぎ記録）への書込を資料の案内の対象から外す挙動を、hookの入口から確かめるテスト。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_REPOSITORY = pathlib.Path(__file__).resolve().parents[4]
# 成果原稿の書込へ案内される執筆基準。作業記録の更新では使わない。
_WRITING_GUIDE = "writing-standards"


@pytest.fixture
def _contract(tmp_path, monkeypatch):
    """メインの作業契約のパスを固定し、委譲先の環境変数を持たない最上位メインとして呼ぶ。"""
    for name in ("AGENT_TOOLKIT_HANDOFF_PATH", "AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "session" / "work-contract.md"
    path.parent.mkdir()
    monkeypatch.setattr(read_delivery.work_contract, "record_path", lambda _session: path)
    return path


def _guidance(tool_name: str, tool_input: dict, *, session_id: str, agent_id: str = "", handoff: str = "") -> str:
    """公開hookへ書込の操作を渡し、エージェントへ届く本文を返す。"""
    payload = {"session_id": session_id, "cwd": str(_REPOSITORY), "tool_name": tool_name, "tool_input": tool_input}
    if agent_id:
        payload["agent_id"] = agent_id
    result = _run_hook_in_process("pretooluse", payload, {"AGENT_TOOLKIT_HANDOFF_PATH": handoff} if handoff else None)
    assert result.returncode == 0 and not result.stderr
    return json.dumps(json.loads(result.stdout), ensure_ascii=False) if result.stdout.strip() else ""


def _write(path: pathlib.Path) -> dict:
    return {"file_path": str(path), "content": "# 記録\n到達点を書く。\n"}


def test_main_work_contract_write_gets_no_writing_guidance(tmp_path, _contract):
    """メインが自分の作業契約だけを書く操作には執筆基準を案内せず、同じディレクトリの原稿の書込には案内する。"""
    assert _WRITING_GUIDE not in _guidance("Write", _write(_contract), session_id="record-main")
    assert _WRITING_GUIDE in _guidance("Write", _write(tmp_path / "session" / "draft.md"), session_id="record-draft")


def test_mixed_and_subagent_writes_stay_guided(tmp_path, _contract):
    """作業記録と原稿を1回の編集でまとめて書く操作と、サブエージェントによるメインの作業契約への書込は、案内の対象に保つ。"""
    draft = tmp_path / "session" / "draft.md"
    patch = f"*** Begin Patch\n*** Add File: {_contract}\n+到達点\n*** Add File: {draft}\n+原稿\n*** End Patch\n"
    assert _WRITING_GUIDE in _guidance("apply_patch", {"command": patch}, session_id="record-mixed")
    assert _WRITING_GUIDE in _guidance("Write", _write(_contract), session_id="record-sub", agent_id="sub-1")


def test_delegate_handoff_record_write_gets_no_writing_guidance(tmp_path, _contract):
    """委譲先の引き継ぎ記録1件への書込には案内せず、同じ委譲先の別のファイルへの書込には案内する。"""
    handoff = tmp_path / "handoff.md"
    assert _WRITING_GUIDE not in _guidance("Write", _write(handoff), session_id="record-handoff", handoff=str(handoff))
    other = _guidance("Write", _write(tmp_path / "other.md"), session_id="record-other", handoff=str(handoff))
    assert _WRITING_GUIDE in other
