"""公開prepareで対象別の担当横断費用と候補への非昇格を検証する。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import session_review_prepare as prepare

from agent_toolkit._testing import session_evidence_support as support


def test_prepare_mode_costs_keep_observed_usage_and_unmeasured_records_separate(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """mode別の量に実usageを使い、未計測担当と記録されたモデルを別々に残す。"""
    parent = tmp_path / "parent.jsonl"
    call = support.claude_call("2026-10-10T00:00:01Z", "read", "Read", {"file_path": "/repo/note.md"})
    call["effort"] = "medium"
    call["message"].update(id="usage-1", model="test-model", usage={"input_tokens": 10, "output_tokens": 2})
    support.write_jsonl(parent, [support.timestamped_entry("2026-10-10T00:00:00Z", "確認する"), call])
    support.write_subagent(
        parent.with_suffix("") / "subagents",
        "agent-child",
        [support.timestamped_entry("2026-10-10T00:00:02Z", "調査する")],
        {"agentType": "Explore"},
    )
    work = tmp_path / "prepared"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    stats = [json.loads(line) for line in (work / "bundle/stats.jsonl").read_text(encoding="utf-8").splitlines()]
    modes = {row["mode"]: row for row in stats if row["kind"] == "stats-mode"}
    assert modes["main"]["tokens"]["input_tokens"] == 10
    assert modes["main"]["tokens"]["output_tokens"] == 2
    assert "test-model" in json.dumps(modes["main"]["observed_identities"])
    assert modes["Explore"]["tokens"] == {}
    assert modes["Explore"]["unmeasured_records"] == ["claude:parent/agent-child"]
    assert "mode別費用" in pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")


def test_prepare_exposes_generated_work_and_partial_repetition_without_replaying_body(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """正常なJSON転記を生成量・出所・部分一致から比較でき、数値の異なる文を混同しない。"""
    source = tmp_path / "parent.jsonl"
    shared = "確定した監査JSONを生成元で保存し、その絶対パスを受領担当へ渡す。"
    notice = '<atk-auto source="read-delivery" kind="warn">必要な資料を確認する。</atk-auto>'
    entries = [support.timestamped_entry("2026-10-10T00:00:00Z", "監査結果を処理する")]
    for index in range(2):
        entries.extend(
            [
                support.claude_call(
                    "2026-10-10T00:00:01Z",
                    f"edit-{index}",
                    "Write",
                    {"file_path": f"/tmp/managed-temp/result-{index}.json", "content": f"説明{index}。{shared}件数{index}。"},
                ),
                {
                    "type": "user",
                    "timestamp": "2026-10-10T00:00:02Z",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": f"edit-{index}", "content": notice}],
                    },
                },
            ]
        )
    support.write_jsonl(source, entries)
    work = tmp_path / "prepared"
    work.mkdir()
    assert prepare.main(["--transcript", str(source), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    resources = [json.loads(line) for line in (work / "bundle/resource-usage.jsonl").read_text(encoding="utf-8").splitlines()]
    owner = next(row for row in resources if row["kind"] == "stats-resource-usage")
    assert owner["generated_input_utf8_bytes"] > 0
    assert all(row["component"] == "editing" for row in owner["generated_inputs"])
    assert all(row["managed_temp_paths"] for row in owner["generated_inputs"])
    repeated = next(row for row in owner["partial_repetitions"] if row["excerpt"] == shared)
    assert repeated["count"] == 2
    assert len({row["location"] for row in repeated["locations"]}) == 2
    assert not any("件数0" in row["excerpt"] or "件数1" in row["excerpt"] for row in owner["partial_repetitions"])
    assert owner["hook_notices"][0]["source"] == "read-delivery"
    assert len(owner["hook_notices"][0]["locations"]) == 2
    stats = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    assert "生成入力" in stats and "文の部分一致候補" in stats
    assert "API履歴の再送量" in stats


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
    child.append(support.claude_call("2026-10-10T00:00:03Z", "child-skill", "Skill", {"skill": "agent-basics:bugfix"}))
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
            support.claude_call("2026-10-10T00:00:03Z", f"skill-{index}", "Skill", {"skill": "agent-basics:bugfix"})
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
    assert summary["skills_by_name"][0]["target"] == "agent-basics:bugfix"
    assert summary["skills_by_name"][0]["count"] == 3
    assert summary["shell_paths_by_target"][0]["target"] == "/repo/rules.md"
    assert summary["shell_paths_by_target"][0]["count"] == 22
    main = next(event for event in resources if event.get("record") == "claude:parent")
    assert main["reads"][0]["range"] == {"offset": 1, "limit": 8}
    assert main["omitted_largest_result_count"] == 2
    assert main["omitted_repeat_count"] == 3
    stats = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    assert "/repo/rules.md: 合計13回、観測結果13件・14字・42バイト" in stats
    assert "agent-basics:bugfix: 合計3回" in stats
    assert "上位一覧の省略: 結果7件、反復8組" in stats
    assert '範囲={"offset": 1, "limit": 8}、claude:parent:2' in stats
    assert '範囲={"offset": 2, "limit": 8}、claude:parent/agent-child:1' in stats
    assert "反復: Read 12回" in stats
    assert "結果量上位: Read 1字・3バイト、入力=claude:parent:" in stats
    assert "結果量上位: Read 2字・6バイト、入力=claude:parent/agent-child:1、結果=claude:parent/agent-child:2" in stats
    prefix = "- メインのtoken上位: "
    peaks = [json.loads(line.removeprefix(prefix)) for line in stats.splitlines() if line.startswith(prefix)]
    assert any(peak["input_tokens"] == 12 and peak["output_tokens"] == 2 and peak["line"] == 24 for peak in peaks)
    for key in ("candidate_total", "candidate_counts", "excluded_counts"):
        assert result[key] == baseline[key]
    assert pathlib.Path(result["candidates_path"]).read_text(encoding="utf-8") == baseline_candidates


@pytest.mark.parametrize("owners, targets", [(2, 3), (64, 40)])
def test_prepare_bounds_initial_documents_and_keeps_full_evidence(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], owners: int, targets: int
) -> None:
    """長い入力、多数担当・対象でも、初回予算と保存証拠への全件対応が同時に成立する。"""
    parent = tmp_path / "parent.jsonl"
    support.write_jsonl(parent, [support.timestamped_entry("2026-10-10T00:00:00Z", "対象を整える")])
    command = "python3 -c '" + "print(1);" * 1500 + "'"
    for owner in range(owners):
        entries = []
        for target in range(targets):
            identity = f"read-{target}"
            entries.extend(
                [
                    support.claude_call(
                        "2026-10-10T00:00:01Z", identity, "Read", {"file_path": f"/repo/owner-{owner}/file-{target}.md"}
                    ),
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": identity,
                                    "content": "読" * 4000 if target < 12 else "本文",
                                }
                            ],
                        },
                    },
                ]
            )
        entries.extend(
            [
                support.claude_call("2026-10-10T00:00:02Z", "command", "Bash", {"command": command}),
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "command",
                                "content": "x" * (8200 + owner),
                            }
                        ],
                    },
                },
            ]
        )
        support.write_subagent(parent.with_suffix("") / "subagents", f"agent-owner-{owner}", entries)
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert pathlib.Path(result["conversation_path"]).stat().st_size <= 100_000
    assert pathlib.Path(result["candidates_path"]).stat().st_size <= 70_000
    assert pathlib.Path(result["stats_path"]).stat().st_size <= 30_000
    index = [json.loads(line) for line in pathlib.Path(result["review_index_path"]).read_text(encoding="utf-8").splitlines()]
    candidates = {
        row["candidate_id"]: row
        for row in [json.loads(line) for line in (work / "bundle/candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        if row.get("kind") == "candidate"
    }
    assert len(index) == owners and result["candidate_total"] == owners
    assert len({row["candidate_id"] for row in index}) == owners
    for row in index:
        assert row["locators"] and row["evidence_path"]
        evidence = json.loads((work / "bundle" / row["evidence_path"]).read_text(encoding="utf-8"))
        assert evidence["locators"] == row["locators"]
        assert candidates[row["candidate_id"]]["calls"][0]["text"] == command
    resources = [json.loads(line) for line in (work / "bundle/resource-usage.jsonl").read_text(encoding="utf-8").splitlines()]
    resource_owners = [row for row in resources if row["kind"] == "stats-resource-usage" and row["role"] != "main"]
    assert len(resource_owners) == owners
    assert all(len(row["reads"]) == targets for row in resource_owners)
    assert sum(row["large_output_review_count"] for row in resource_owners) == owners
    assert all(row["expected_large_read_count"] == min(12, targets) for row in resource_owners)
    stats = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    ranking = stats.split("## 大出力の点検上位", 1)[1].split("## 集計の母集団と詳細", 1)[0]
    assert ranking.count("入力=") == min(10, owners)
    assert f"{8200 + owners - 1}バイト" in ranking
    assert "12000バイト" not in ranking
    assert "費用確認17観点" in stats and "非律速担当" in stats


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
