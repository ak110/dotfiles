"""既存の規範を理由に対処を見送る本文の遮断を確かめる。"""

import json

import pytest

from agent_toolkit._hooks import norm_excuse_gate, notice


@pytest.mark.parametrize(
    "text",
    [
        "残りのレビューでも採否を決める（agent-basics:review-standardsの既存の基準で足りるため、規範は足さない）。",
        "既存の規範で十分なので追加の対策はしない。",
        "既存の対策でカバーできる。",
        "規則にはもう書かれている。守れていなかっただけなので、再発防止は見送る。",
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
        "規範があるが防げなかったため、送達前の検査を追加する。",
        "別件は不具合ではなく、AWIは見送る。",
        "規範があるが防げなかったため、送達前の検査を追加する。\n別件は不具合ではなく、AWIは見送る。",
        "規範があるが防げなかったため、送達前の検査を追加する。別件は不具合ではなく、AWIは見送る。",
        "投稿の設定は、既存の素の`WebFetch`全面許可で足りる。",
    ],
)
def test_ignores_negation_quote_and_other_subjects(text: str) -> None:
    assert norm_excuse_gate.find_excuse(text) is None


def test_reissue_of_same_text_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    states: dict[str, dict] = {}

    def fake_update(session_id: str, updater) -> bool:
        states[session_id] = updater(states.get(session_id, {}))
        return True

    monkeypatch.setattr(norm_excuse_gate, "update_state", fake_update)
    payload = {"session_id": "s1"}
    text = "既存の規範で十分なので追加の対策はしない。"
    assert norm_excuse_gate.text_block(payload, text, "送達") is not None
    assert norm_excuse_gate.text_block(payload, text, "送達") is None
    assert norm_excuse_gate.text_block(payload, text, "送達") is None
    assert norm_excuse_gate.text_block(payload, "問題ない本文。", "送達") is None
    assert [entry.partition("\n")[0] for entry in notice.pending_resend_passed("s1", "main")] == [
        "既存の規範を理由に対処を見送る言い回しの本文を再発行した"
    ] * 2


@pytest.mark.parametrize("summarized", [False, True])
def test_stop_flags_intermediate_text_once(monkeypatch: pytest.MonkeyPatch, tmp_path, summarized: bool) -> None:
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


def test_stop_flags_codex_intermediate_text_once(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Codexの記録形式でも、現在のターンの途中の発話と思考の要約を判定する。前のターンの発話は対象外とする。"""
    states: dict[str, dict] = {}

    def fake_update(session_id: str, updater) -> bool:
        states[session_id] = updater(states.get(session_id, {}))
        return True

    monkeypatch.setattr(norm_excuse_gate, "update_state", fake_update)
    monkeypatch.setattr("agent_toolkit._hooks.stop_session.append_stop_log", lambda *_args, **_kwargs: None)

    def message(text: str) -> dict:
        return {
            "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
        }

    started = {"type": "event_msg", "payload": {"type": "task_started"}}
    summary = {
        "type": "response_item",
        "payload": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "既存の対策で防げるので追加しない。"}]},
    }
    cases = [
        ([started, message("既存の規範で十分なので見送る。"), started, message("完了した。")], "approve"),
        ([started, message("既存の規範で十分なので見送る。"), message("完了した。")], "block"),
        ([started, summary, message("完了した。")], "block"),
    ]
    for index, (records, expected) in enumerate(cases):
        transcript = tmp_path / f"codex-{index}.jsonl"
        transcript.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
        payload = {
            "session_id": f"c{index}",
            "turn_id": "t1",
            "transcript_path": str(transcript),
            "last_assistant_message": "完了した。",
        }
        assert norm_excuse_gate.evaluate(json.dumps(payload, ensure_ascii=False))[0] == expected


def test_stop_blocks_main_final_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(norm_excuse_gate, "update_state", lambda _session, updater: bool(updater({})))
    monkeypatch.setattr("agent_toolkit._hooks.stop_session.append_stop_log", lambda *_args, **_kwargs: None)
    payload: dict[str, object] = {"session_id": "s1", "last_assistant_message": "既存の規範で十分なので見送る。"}
    decision, body = norm_excuse_gate.evaluate(json.dumps(payload, ensure_ascii=False))
    assert decision == "block"
    assert "既存の規範で十分" in body
    payload["stop_hook_active"] = True
    assert norm_excuse_gate.evaluate(json.dumps(payload, ensure_ascii=False))[0] == "approve"


def test_antigravity_resend_is_scoped_to_user_step() -> None:
    from agent_toolkit._common import session_state  # pylint: disable=import-outside-toplevel
    from agent_toolkit._hooks.antigravity import LAST_USER_STEP  # pylint: disable=import-outside-toplevel

    payload = {"session_id": "agy-scope", "runtime": "agy"}
    text = "既存の規範で十分なので追加の対策はしない。"

    def step(index: int):
        assert session_state.update_state("agy-scope", lambda state: {**state, LAST_USER_STEP: index})

    step(0)
    assert norm_excuse_gate.text_block(payload, text, "送達") is not None
    assert norm_excuse_gate.text_block(payload, text, "送達") is None
    step(5)
    assert norm_excuse_gate.text_block(payload, text, "送達") is not None


_REPORT_2026_11_24 = (
    "**ご指摘への再発防止**: 新しいAWIは投入しない（AWI化ステータス: 不要）。"
    "「差分を追記せず全体を整理する」は、`process-user-comments`の手順5（古くなった記述は消す）にもう書かれている。"
    "規則が無かったのではなく、守れていなかっただけなので、同じ規則を足しても防げないと判断した。"
)


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        (_REPORT_2026_11_24, True),
        ("- 再発防止: 担当が注意する。", True),
        ("再発防止策：なし", True),
        ("AWIを登録した。進捗は次のとおり。", False),
        ("- 改善点: x（AWI化ステータス: 不要 被覆 agent-toolkit/hooks/send_to_user.tsx）", False),
        ("規範の追記では防げないため、送達前の検査を足す。", False),
        ("- 再発防止: 自動検出: 規範の追記では防げないため、送達前の検査を足す。", False),
        ("- **再発防止:** 被覆 agent-toolkit/hooks/send_to_user.tsx の検査が防ぐ。", False),
        ("- 再発防止: `被覆` 既存の検査で防ぐ。", False),
        ("再発防止: `例示だけ`", False),
        ("再発防止:\n- 文書: 実例を載せる。", False),
        ("`再発防止:`の行を書く。", False),
        ("> 再発防止: 守れていなかっただけ", False),
    ],
)
def test_detects_prevention_line_without_kind(text: str, *, flagged: bool) -> None:
    assert (norm_excuse_gate.find_untyped_prevention(text) is not None) is flagged


def test_untyped_prevention_blocks_until_reissued(monkeypatch: pytest.MonkeyPatch) -> None:
    states: dict[str, dict] = {}

    def fake_update(session_id: str, updater) -> bool:
        states[session_id] = updater(states.get(session_id, {}))
        return True

    monkeypatch.setattr(norm_excuse_gate, "update_state", fake_update)
    payload = {"session_id": "s2"}
    body = norm_excuse_gate.text_block(payload, _REPORT_2026_11_24, "送達")
    assert body is not None and "種類の語" in body
    assert norm_excuse_gate.text_block(payload, _REPORT_2026_11_24, "送達") is None
