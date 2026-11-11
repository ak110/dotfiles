"""セッション振り返りの試験（session_review_evidence_failures_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_evidence_candidates as evidence_candidates
import session_evidence_extract as evidence_extract

from agent_toolkit._atk import outcome
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_review_evidence_support import (
    _bundle_failed_codex_commands,
    _bundle_failed_commands_of_runtime,
)


def test_codex_failed_command_without_output_keeps_failure_event(tmp_path: pathlib.Path) -> None:
    """出力が空でも非0終了のCommandExecutionを証拠から欠落させない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "aggregated_output": "",
                        "exit_code": 1,
                    },
                },
            }
        ],
    )

    events = evidence_extract.load_and_extract(str(transcript))

    assert len(events) == 1
    assert events[0]["kind"] == "failed-tool"
    assert events[0]["tool"] == "CommandExecution"
    assert events[0]["text"] == "CommandExecution failed"
    assert events[0]["diagnostic"] == ""


@pytest.mark.parametrize("command", [["git", "status"], ["tool", "one", "two"], []])
def test_codex_failed_command_keeps_structured_command_and_exit_code(tmp_path: pathlib.Path, command: list[str]) -> None:
    """失敗CommandExecutionは配列構造と終了コードを本文とは別に保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "command": command,
                        "exit_code": 17,
                        "stderr": "失敗",
                    },
                },
            }
        ],
    )

    event = evidence_extract.load_and_extract(str(transcript))[0]

    assert event["command"] == json.dumps(command, ensure_ascii=False)
    assert event["exit_code"] == 17
    assert event["text"] == "失敗"
    assert event["executable"] == (command[0] if command else "")
    assert event["diagnostic"] == "失敗"


def test_bundle_groups_wrapped_commands_by_failure_signature(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """包装とスクリプト名が異なる同じ原因を1署名へまとめ、別の終了理由は分ける。"""
    cases = [
        (["/bin/bash", "-lc", "timeout 900s uv run --frozen python a.py"], 127, "python: command not found"),
        (["python", "b.py"], 127, "python: command not found"),
        (["git", "push", "--dry-run", "--porcelain"], 128, "fatal: push destination differs"),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]

    assert sorted(item["count"] for item in candidates) == [1, 2]
    repeated = next(item for item in candidates if item["count"] == 2)
    assert repeated["candidate_kind"] == "command-failure"
    assert "python a.py" in repeated["failure_summary"]
    assert "127" in repeated["failure_signature"]
    assert len({item["failure_signature"] for item in candidates}) == 2


def test_bundle_excludes_negative_results_and_checks_without_hiding_argument_error(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """正常な否定結果と自動チェックによる検出を除外し、終了コード2の入力誤りは候補へ残す。"""
    cases = [
        (["/bin/bash", "-lc", "git -c color.ui=false grep -n -F needle -- docs"], 1, ""),
        (["git", "config", "--get", "unset.key"], 1, ""),
        (["/bin/bash", "-lc", "git grep needle && test -e /missing"], 1, ""),
        (["pyfltr", "run"], 1, "test failed"),
        (["python", "scripts/sync.py", "--check"], 1, "generated files differ"),
        (["atk", "review-table", "validate"], 2, "usage: invalid arguments"),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)

    assert [item["locators"] for item in records if item["kind"] == "candidate"] == [
        [{"record": "codex:transcript", "line": 6}]
    ]
    assert records[-1]["excluded"]["normal-negative-result"] == 3
    assert records[-1]["excluded"]["check-detected"] == 2


def test_bundle_treats_quoted_operator_shaped_search_terms_as_data(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """演算子形の検索語と保存先で一致0件の分類を変えず、帰属不能な連結・診断・異常終了を残す。"""
    cases = [
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F ';' docs"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F '&&' docs && git grep -n -F '<<<<<<<' -- docs"], 1, ""),
        (["/bin/bash", "-lc", r"rg -n -F \| docs"], 1, ""),
        (["/bin/bash", "-lc", "git ls-files | rg -F '&&'"], 1, ""),
        (["/bin/bash", "-lc", "git ls-files -z | xargs -0 rg -n -F ';'"], 123, ""),
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs && echo found"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs > found.txt"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs"], 2, ""),
        (["/bin/bash", "-lc", "rg -n -F ';' docs"], 1, "rg: docs: No such file or directory"),
        (["/bin/bash", "-lc", "python3 check.py > found.txt"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs > found.txt"], 2, ""),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    candidates = [item for item in records if item["kind"] == "candidate" and item["candidate_kind"] == "command-failure"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == {7, 9, 10, 11, 12}
    assert records[-1]["excluded"]["normal-negative-result"] == 7

    unresolved = [
        item for item in records if item["kind"] == "candidate" and item["candidate_kind"] == "definition-read-unresolved"
    ]
    assert {locator["line"] for item in unresolved for locator in item["locators"]} == {6}
    assert not any(item["kind"] == "candidate" and item["candidate_kind"] == "definition-body-read" for item in records)


def test_bundle_excludes_wait_continuations_but_keeps_real_failures(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """終了3の待機継続を出力の表記によらず除外し、別の契約の失敗は残す。"""
    cases = [
        (["atk", "agents", "wait"], 3, '{"status": "running"}'),
        (["/repo/agent-toolkit/bin/atk", "agents", "wait"], 3, "待機継続"),
        (["/bin/bash", "-lc", "/repo/agent-toolkit/bin/atk agents wait"], 3, "別の表示"),
        (["sh", "-c", "atk agents wait"], 3, ""),
        (["atk", "agents", "wait"], 2, "引数が不正"),
        (["atk", "agents", "show"], 3, "失敗"),
        (["other", "agents", "wait"], 3, "失敗"),
        (["bash", "-lc", "atk agents wait; false"], 3, "連結全体の失敗"),
        (["bash", "-lc", "echo 'atk agents wait'"], 3, "引用だけ"),
        (["rg", "missing", "docs"], 1, ""),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == {5, 6, 7, 8, 9}
    assert records[-1]["excluded"]["normal-nonterminal-result"] == 4
    assert records[-1]["excluded"]["normal-negative-result"] == 1


def test_candidates_exclude_explicit_help_failure_but_keep_usage_errors() -> None:
    """明示helpのusageだけを除外し、通常操作とhelp以外の診断は候補へ残す。"""
    timeline = [
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": 1,
            "text": "usage: git commit",
            "diagnostic": "usage: git commit",
            "command": json.dumps(["git", "commit", "-h"]),
        },
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": 2,
            "text": "usage: git commit",
            "diagnostic": "usage: git commit",
            "command": json.dumps(["git", "commit"]),
        },
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": 3,
            "text": "fatal: repository unavailable",
            "diagnostic": "fatal: repository unavailable",
            "command": json.dumps(["git", "commit", "--help"]),
        },
    ]

    records = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    candidates = [record for record in records if record["kind"] == "candidate"]
    assert {locator["line"] for candidate in candidates for locator in candidate["locators"]} == {2, 3}
    assert records[-1]["excluded"] == {"command-help": 1}


def test_candidates_exclude_normal_negative_results_but_keep_diagnostics() -> None:
    """述語の偽を件数へ分け、受理形式や対象の異常は候補へ残す。"""
    commands = [
        (["git", "grep", "missing"], ""),
        (["rg", "missing", "."], ""),
        (["bash", "-lc", "test -e /missing"], ""),
        (["git", "merge-base", "--is-ancestor", "A", "B"], ""),
        (["git", "grep", "missing"], "fatal: repository unavailable"),
        (["cp", "source", "target"], ""),
    ]
    timeline = [
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": index,
            "text": diagnostic or "CommandExecution failed",
            "diagnostic": diagnostic,
            "exit_code": 1,
            "command": json.dumps(command),
        }
        for index, (command, diagnostic) in enumerate(commands, start=1)
    ]

    records = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    candidates = [record for record in records if record["kind"] == "candidate"]
    assert {locator["line"] for candidate in candidates for locator in candidate["locators"]} == {5, 6}
    assert records[-1]["excluded"] == {"normal-negative-result": 4}


def test_codex_failed_command_clips_only_long_structured_command(tmp_path: pathlib.Path) -> None:
    """長大commandは既存の証拠上限と省略標識に従う。"""
    command = ["x" * 2100]
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "command": command,
                        "aggregated_output": "失敗",
                    },
                },
            }
        ],
    )

    event = evidence_extract.load_and_extract(str(transcript))[0]

    assert event["command"].endswith("…[省略]")
    assert len(event["command"]) == 2000 + len("…[省略]")


def test_bundle_excludes_atk_no_match_without_hiding_real_failures(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """結果行の契約を持つ終了1を除き、警告・失敗・連結・別コード・切り詰めは候補へ残す。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    cases = [
        (["atk", "wi", "grep", "needle"], 1, no_match),
        (["atk", "managed-temp", "list"], 1, no_match),
        (
            ["/repo/agent-toolkit/bin/atk", "wi", "grep", "needle", "--output-file=/file"],
            1,
            no_match + "\n保存先: /file\n行数: 0",
        ),
        (["timeout", "60", "env", "KEY=VALUE", "uv", "run", "--frozen", "bash", "-lc", "atk wi grep 'a|b'"], 1, no_match),
        (["atk", "wi", "grep", "|"], 1, no_match),
        (["atk", "wi", "grep", "needle"], 1, no_match + "\n" + outcome.WARNING_PREFIX + "要確認"),
        (["atk", "wi", "grep", "needle"], 1, no_match + "\n" + outcome.FAILURE_PREFIX + "入力が不正"),
        (["atk", "wi", "grep", "needle"], 2, no_match),
        (["other", "wi", "grep", "needle"], 1, no_match),
        (["bash", "-lc", "atk wi grep needle; false"], 1, no_match),
        (["bash", "-lc", "bash -lc 'atk wi grep needle' && false"], 1, no_match),
        (["bash", "-lc", "atk wi grep needle\nfalse"], 1, no_match),
        (["atk", "wi", "grep", "needle"], 1, no_match + "\n" + "出力" * 1500 + "\n" + outcome.WARNING_PREFIX + "末尾警告"),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    lines = {locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]}
    assert lines == set(range(6, 14))
    assert records[-1]["excluded"]["normal-negative-result"] == 5


def test_bundle_excludes_atk_no_match_of_claude_bash(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """引用した検索語と包装を保ち、Bashの終了1と結果行を公開bundleで同じ区分へ数える。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    commands = [
        "atk wi grep 'a|b'",
        "atk managed-temp list",
        "timeout 60 env KEY=VALUE uv run --frozen atk wi grep ';' --output-file=/file",
        "bash -lc \"atk wi grep '&&'\"",
        "atk wi grep needle && false",
        "atk wi grep needle | cat",
        "bash -lc 'atk wi grep needle' && false",
        "atk wi grep needle\nfalse",
    ]
    records, lines = _bundle_failed_commands_of_runtime(
        tmp_path, capsys, "claude", [(command, 1, no_match + "\n保存先: /file\n行数: 0") for command in commands]
    )
    candidate_lines = {locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]}
    assert candidate_lines == set(lines[4:])
    assert records[-1]["excluded"]["normal-negative-result"] == 4
