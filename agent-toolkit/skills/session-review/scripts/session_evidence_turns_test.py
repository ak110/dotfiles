"""複数turnの返却を公開bundleから検証する。

終端の形は2026年10月10日のWIの実記録のtask_complete・end_turn・resultを使う。
同じ先行返却へ後続の作業を加えたときの候補欠落と、正常返却の混入を検出する。
"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing import session_evidence_support as support


def _turn(runtime: str, body: str) -> list[dict]:
    if runtime == "codex":
        return [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "作業を開始"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final",
                    "content": [{"type": "output_text", "text": body}],
                },
            },
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ]
    if runtime == "agy":
        return [{"event": "result", "result": {"status": "SUCCESS", "response": body}}]
    work = support.execution_tool_use("work")
    if runtime == "handback":
        return [
            work,
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "SubagentHandback", "id": "return", "input": {"message": body}}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "返却を渡した。"}],
                },
            },
        ]
    return [
        work,
        {
            "type": "assistant",
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": body}]},
        },
    ]


@pytest.mark.parametrize("runtime", ["claude", "handback", "codex", "agy"])
def test_public_bundle_preserves_each_completed_turn(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """後続turnを足しても先行の返却本文と由来行を失わず、正常返却を候補から除く。"""
    parent = tmp_path / "parent.jsonl"
    support.write_jsonl(parent, [{"type": "user", "message": {"role": "user", "content": "最初の依頼"}}])
    first_body = "実装完了\n検証結果: 成功\n画面差分: なし\n" + "長文" * 1500 + "\n気付いた改善点: 回収をまとめる。"
    first = _turn(runtime, first_body)
    bodies = [first_body, "続行できない理由: 必要な入力が無い。", "実装完了\n検証結果: 成功\n画面差分: なし"]
    all_entries = [entry for body in bodies for entry in _turn(runtime, body)]
    results: list[list[dict]] = []
    for name, entries in (("before", first), ("after", all_entries)):
        support.write_subagent(
            parent.with_suffix("") / "subagents", "agent-delegate", [{**entry, "isSidechain": True} for entry in entries]
        )
        output = tmp_path / name
        output.mkdir()
        assert evidence.main([str(parent), "--bundle", str(output)]) == 0
        capsys.readouterr()
        rows = [json.loads(line) for line in (output / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        results.append(
            [
                row
                for row in rows
                if row.get("kind") == "candidate" and row.get("candidate_kind") in {"delegate-return", "escalation"}
            ]
        )
        if name == "after":
            summary = next(row for row in rows if row.get("kind") == "candidate-summary")
            assert summary["excluded"]["normal-delegate-return"] == 1
    assert len(results[0]) == 1
    assert len(results[1]) == 2
    assert results[1][0]["text"] == results[0][0]["text"]
    assert results[1][0]["locators"] == results[0][0]["locators"]
    assert "気付いた改善点: 回収をまとめる。" in results[1][0]["text"]
    assert results[1][1]["candidate_kind"] == "escalation"
