"""既存の規範を理由に対処を見送る本文の遮断を確かめる。"""

import json

import pytest

from agent_toolkit._hooks import norm_excuse_gate


@pytest.mark.parametrize(
    "text",
    [
        # 2026年11月22日にユーザーへ送り、是正を受けた本文。
        "残りのレビューでも採否を決める（agent-basics:review-standardsの既存の基準で足りるため、規範は足さない）。",
        "既存の規範で十分なので追加の対策はしない。",
        "既存の対策でカバーできる。",
    ],
)
def test_detects_excuse(text: str) -> None:
    assert norm_excuse_gate.find_excuse(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "既存の対策が防げなかったため、検査を足す。",
        "既存の規範では足りないので構造を変える。",
        "既存の規範で十分ではないと分かった。",
        "既存の規範で足りていないため構造を変える。",
        "既存の対策が防げていない。",
        "既存の規範では防げず、検査を足す。",
        "ユーザーは「既存の基準で足りる」という言い回しを止めるよう求めた。",
        "`既存の基準で足りる`という言い回しを検出する。",
        "> 既存の基準で足りる\n\nこの引用への対処を書く。",
        "投稿の設定は、既存の素の`WebFetch`全面許可で足りる。",
    ],
)
def test_ignores_negation_quote_and_other_subjects(text: str) -> None:
    assert norm_excuse_gate.find_excuse(text) is None


def test_reissue_of_same_text_passes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    states: dict[str, dict] = {}

    def fake_update(session_id: str, updater) -> bool:
        states[session_id] = updater(states.get(session_id, {}))
        return True

    monkeypatch.setattr(norm_excuse_gate, "update_state", fake_update)
    payload = {"session_id": "s1"}
    text = "既存の規範で十分なので追加の対策はしない。"
    assert norm_excuse_gate.text_block(payload, text, "送達") is not None
    assert norm_excuse_gate.text_block(payload, text, "送達") is None
    assert norm_excuse_gate.text_block(payload, text, "送達") is not None
    assert norm_excuse_gate.text_block(payload, "問題ない本文。", "送達") is None


@pytest.mark.parametrize("summarized", [False, True])
def test_stop_flags_intermediate_text_once(monkeypatch: pytest.MonkeyPatch, tmp_path, summarized: bool) -> None:
    """send_to_userの遮断を途中の通常発話で迂回した場合も、ターンの終了で一度だけ止める。"""
    states: dict[str, dict] = {}

    def fake_update(session_id: str, updater) -> bool:
        states[session_id] = updater(states.get(session_id, {}))
        return True

    monkeypatch.setattr(norm_excuse_gate, "update_state", fake_update)
    monkeypatch.setattr("agent_toolkit._hooks.stop_session.append_stop_log", lambda *_args, **_kwargs: None)
    transcript = tmp_path / "t.jsonl"
    records = [
        {"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": "直して"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "既存の規範で十分なので見送る。"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "完了した。"}]}},
    ]
    if summarized:
        records[1] = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "既存の規範で十分なので見送る。", "signature": "x"}]},
        }
    transcript.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    payload = {"session_id": "s1", "transcript_path": str(transcript), "last_assistant_message": "完了した。"}
    text = json.dumps(payload, ensure_ascii=False)
    assert norm_excuse_gate.evaluate(text)[0] == "block"
    assert norm_excuse_gate.evaluate(text)[0] == "approve"


def test_stop_blocks_main_final_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(norm_excuse_gate, "update_state", lambda _session, updater: bool(updater({})))
    monkeypatch.setattr("agent_toolkit._hooks.stop_session.append_stop_log", lambda *_args, **_kwargs: None)
    payload: dict[str, object] = {"session_id": "s1", "last_assistant_message": "既存の規範で十分なので見送る。"}
    decision, body = norm_excuse_gate.evaluate(json.dumps(payload, ensure_ascii=False))
    assert decision == "block"
    assert "既存の規範で十分" in body
    payload["stop_hook_active"] = True
    assert norm_excuse_gate.evaluate(json.dumps(payload, ensure_ascii=False))[0] == "approve"
