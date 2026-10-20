"""公開prepareで対象別の担当横断費用と候補への非昇格を検証する。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import session_review_prepare as prepare

from agent_toolkit._testing import session_evidence_support as support


def _read_entries(identity: str, body: str, *, offset: int) -> list[dict[str, Any]]:
    return [
        support.claude_call(
            "2026-10-10T00:00:01Z", identity, "Read", {"file_path": "/repo/rules.md", "offset": offset, "limit": 8}
        ),
        {
            "type": "user",
            "timestamp": "2026-10-10T00:00:02Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": identity, "content": body}],
            },
        },
    ]


def test_prepare_aggregates_targets_across_owners_without_promoting_cost_to_candidates(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同一ファイル・Skillの担当横断量と省略数を返し、反復の追加で問題候補を増やさない。"""
    parent = tmp_path / "parent.jsonl"
    initial = [support.timestamped_entry("2026-10-10T00:00:00Z", "依頼の本文")]
    child = [*_read_entries("child-read", "子供", offset=2)]
    child.append(support.claude_call("2026-10-10T00:00:03Z", "child-skill", "Skill", {"skill": "agent-toolkit:bugfix"}))
    support.write_subagent(parent.with_suffix("") / "subagents", "agent-child", child)
    support.write_jsonl(parent, [*initial, *_read_entries("first", "親", offset=1)])
    baseline_work = tmp_path / "baseline"
    baseline_work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(baseline_work)]) == 0
    baseline = json.loads(capsys.readouterr().out)
    baseline_candidates = pathlib.Path(baseline["candidates_path"]).read_text(encoding="utf-8")

    expanded = list(initial)
    for index in range(12):
        reads = _read_entries(f"read-{index}", "親", offset=1)
        # usageはsession_evidence_stats_test.pyと同じClaudeの応答記録形式を使う。
        reads[0]["message"].update(
            id=f"response-{index}",
            usage={"input_tokens": index + 1, "output_tokens": 2},
        )
        expanded.extend(reads)
    for index in range(2):
        expanded.append(
            support.claude_call("2026-10-10T00:00:03Z", f"skill-{index}", "Skill", {"skill": "agent-toolkit:bugfix"})
        )
    for index in range(11):
        for repetition in range(2):
            expanded.append(
                support.claude_call(
                    "2026-10-10T00:00:04Z",
                    f"shell-{index}-{repetition}",
                    "Bash",
                    {"command": f"echo /repo/rules.md /repo/output-{index}.txt"},
                )
            )
    support.write_jsonl(parent, expanded)
    work = tmp_path / "expanded"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    resources = [
        json.loads(line) for line in (work / "bundle" / "resource-usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summary = next(event for event in resources if event["kind"] == "stats-resource-summary")
    reads = summary["reads_by_target"]
    assert len(reads) == 1
    assert (reads[0]["target"], reads[0]["count"], reads[0]["characters"], reads[0]["utf8_bytes"]) == (
        "/repo/rules.md",
        13,
        14,
        42,
    )
    assert {record: values["count"] for record, values in reads[0]["by_record"].items()} == {
        "claude:parent": 12,
        "claude:parent/agent-child": 1,
    }
    assert summary["skills_by_name"][0]["target"] == "agent-toolkit:bugfix"
    assert summary["skills_by_name"][0]["count"] == 3
    assert summary["shell_paths_by_target"][0]["target"] == "/repo/rules.md"
    assert summary["shell_paths_by_target"][0]["count"] == 22
    main = next(event for event in resources if event.get("record") == "claude:parent")
    assert main["reads"][0]["range"] == {"offset": 1, "limit": 8}
    assert main["omitted_largest_result_count"] == 2
    assert main["omitted_repeat_count"] == 3
    stats = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    assert "/repo/rules.md: 合計13回、観測結果13件・14字・42バイト" in stats
    assert "agent-toolkit:bugfix: 合計3回" in stats
    assert "上位一覧の省略: 結果2件、反復3組" in stats
    assert '範囲={"offset": 1, "limit": 8}、claude:parent:2' in stats
    assert '範囲={"offset": 2, "limit": 8}、claude:parent/agent-child:1' in stats
    assert "反復: Read 12回" in stats
    assert "結果量上位: Read 1字・3バイト、入力=claude:parent:2、結果=claude:parent:3" in stats
    assert "結果量上位: Read 2字・6バイト、入力=claude:parent/agent-child:1、結果=claude:parent/agent-child:2" in stats
    prefix = "- メインのtoken上位: "
    peaks = [json.loads(line.removeprefix(prefix)) for line in stats.splitlines() if line.startswith(prefix)]
    assert any(peak["input_tokens"] == 12 and peak["output_tokens"] == 2 and peak["line"] == 24 for peak in peaks)
    for key in ("candidate_total", "candidate_counts", "excluded_counts"):
        assert result[key] == baseline[key]
    assert pathlib.Path(result["candidates_path"]).read_text(encoding="utf-8") == baseline_candidates


@pytest.mark.parametrize(
    "command, targets",
    [
        ('cat "/repo/a b.md" /repo/plain.md /repo/plain.md', {"/repo/a b.md": 1, "/repo/plain.md": 2}),
        (r"""type "C:\repo\a b.md" C:\repo\plain.md""", {r"C:\repo\a b.md": 1, r"C:\repo\plain.md": 1}),
        ("curl https://example.test/path relative/file.md", {}),
    ],
)
def test_prepare_shell_path_mentions_are_distinct_from_file_reads(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], command: str, targets: dict[str, int]
) -> None:
    """シェル内の文字列出現だけを数え、URL・相対パスを絶対パス読取へ変換しない。"""
    parent = tmp_path / "parent.jsonl"
    support.write_jsonl(parent, [support.claude_call("2026-10-10T00:00:00Z", "shell", "Bash", {"command": command})])
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    capsys.readouterr()
    resources = [
        json.loads(line) for line in (work / "bundle" / "resource-usage.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summary = next(event for event in resources if event["kind"] == "stats-resource-summary")
    assert not summary["reads_by_target"]
    assert {row["target"]: row["count"] for row in summary["shell_paths_by_target"]} == targets
