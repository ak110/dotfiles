"""Claude Codeの現行・旧診断形式を公開の証拠照会から検証する。

入力の前置きは2026年11月1日のClaude Codeのtool_result観測と、既存の角括弧付き記録に基づく。
"""

import argparse
import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl


@pytest.mark.parametrize("prefix", ["PreToolUse:Write hook error: ", "PreToolUse:Write hook error: [hook.py]: "])
@pytest.mark.parametrize("with_copies", [False, True])
def test_hook_failures_count_only_actual_results(tmp_path: pathlib.Path, capsys, prefix: str, with_copies: bool) -> None:
    """21回の遮断を数え、stdoutへの引用とAPIの控えを加えても件数を保つ。"""
    body = '<atk-auto source="pretooluse" kind="block">必須資料を取得せよ。\n取得後に再実行せよ。</atk-auto>'
    results = [
        {"type": "tool_result", "tool_use_id": f"blocked-{index}", "is_error": True, "content": prefix + body}
        for index in range(21)
    ]
    entries = [{"type": "user", "message": {"role": "user", "content": results}}]
    if with_copies:
        entries.extend(
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "stdout-copy", "content": prefix + body}],
                    },
                },
                {"type": "api_error", "error": {"content": prefix + body}},
            ]
        )
    for entry in entries:
        entry["timestamp"] = "2026-10-06T12:00:00+00:00"
    claude = tmp_path / "claude"
    claude.mkdir()
    transcript = _write_transcript(claude, entries)

    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    notices = read_jsonl(capsys)
    assert notices[-1] == {"kind": "summary", "count": 21}
    assert sum(event.get("count", 0) for event in notices[:-1]) == 21

    assert evidence.main([str(transcript)]) == 0
    failures = [event for event in read_jsonl(capsys) if event["kind"] == "failed-tool"]
    assert len(failures) == 21
    assert all(event["diagnostic_last_line"] == "必須資料を取得せよ。\n取得後に再実行せよ。" for event in failures)

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    hook_candidates = [event for event in candidates if event.get("candidate_kind") == "hook-notice"]
    # 候補は記録行を単位にまとめる。同じ行の21結果は1候補になる。
    assert len(hook_candidates) == 1
    assert all("hook error:" not in event["text"] for event in hook_candidates)

    notes = git_repository.init_repository(tmp_path / "notes")
    (notes / "README.md").write_text("# 検証用記録\n", encoding="utf-8")
    git_repository.commit_all(notes, "検証用記録")
    arguments = [
        "--",
        "--claude-projects-dir",
        str(claude),
        "--codex-sessions-dir",
        str(tmp_path / "codex"),
        "--private-notes",
        str(notes),
        "--since",
        "2026-10-05",
        "--until",
        "2026-10-12",
    ]
    assert run_script.dispatch(argparse.Namespace(script_name="cost-metrics", script_args=arguments)) == 0
    metrics = read_jsonl(capsys)
    assert sum(event["count"] for event in metrics if event["kind"] == "cost-metrics-hook-notice") == 21
