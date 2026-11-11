"""セッション振り返りの試験（session_evidence_candidates_failures_test）。"""

from __future__ import annotations

import json
import pathlib
import shlex

import pytest
import session_evidence_candidates as evidence_candidates
import session_review_evidence as evidence

from agent_toolkit._testing.failure_records import bash_failure_entries, failure_entries
from agent_toolkit._testing.helpers import _write_transcript


@pytest.mark.parametrize("mode", ["argv", "shell", "Bash"])
@pytest.mark.parametrize(
    "prefix",
    [
        "env -u NAME",
        "env -uNAME",
        "env --unset=NAME",
        "env --unset NAME",
        "env -i",
        "env --ignore-environment",
        "env -",
        "env -C /repo",
        "env -C/repo",
        "env --chdir=/repo",
        "env --chdir /repo",
        "env --",
        "env -i A=1",
        "A=1 env -u NAME B=2",
    ],
)
def test_env_failures_keep_execution_identity(tmp_path, capsys, prefix, mode) -> None:
    """既知の包装差を同じ署名へまとめ、同じ診断でも別の実行を別候補へ保つ。"""
    commands = ["git status", f"{prefix} git status", f"{prefix} pytest"]
    entries = []
    for index, command in enumerate(commands):
        if mode == "Bash":
            entries.extend(bash_failure_entries(command, "Exit code 2\n実行に失敗した", f"call-{index}"))
            continue
        argv = ["bash", "-lc", command] if mode == "shell" else shlex.split(command)
        entries.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "command": argv,
                        "exit_code": 2,
                        "stderr": "実行に失敗した",
                    },
                },
            }
        )
    transcript = _write_transcript(tmp_path, entries)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    rows = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    failures = [row for row in rows if row.get("candidate_kind") == ("tool-failure" if mode == "Bash" else "command-failure")]
    assert sorted(row["count"] for row in failures) == [1, 2]
    assert {tuple(json.loads(row["failure_signature"])[1:3]) for row in failures} == {("git", "status"), ("pytest", "")}


@pytest.mark.parametrize("prefix", ["env -S", "env --split-string", "env --unknown", "env -u", "env -C"])
def test_unresolved_env_failures_preserve_original_input(prefix: str) -> None:
    """未知・引数不足のenvをオプション名の共通署名へ縮めない。"""
    commands = [prefix, f"{prefix} git status", f"{prefix} pytest", "env", "env A=1"]
    signatures = []
    for command in commands:
        event = {"operation": json.dumps({"command": command}), "tool": "Bash", "exit_code": 2, "text": "同じ診断"}
        signature, summary = evidence_candidates.failure_signature("tool-failure", event)
        signatures.append(signature)
        assert command in summary
    # -u/-Cに後続値がある場合は既知の文法として解釈する。未知包装だけは全文を識別軸へ保持する。
    if prefix not in {"env -u", "env -C"}:
        assert len(set(signatures[:3])) == 3


@pytest.mark.parametrize("failure_kind", ["command-failure", "tool-failure"])
def test_failure_reasons_are_distinct(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], failure_kind: str) -> None:
    """同じコマンド・終了コードでも理由は区別し、対象値だけの差を共通署名へまとめる。"""
    diagnostics = [
        '{"error":"併用拒否"}',
        '{"error":"記録不明","record":"claude:first"}',
        '{"error":"記録不明","record":"claude:second"}',
        "理由 '値7'",
        "理由 '値8'",
        "理由 deadbeef",
        "理由 deadface",
        "対象 /a/first がない",
        "対象 /b/second がない",
    ]
    entries = [{"type": "user", "message": {"role": "user", "content": "依頼"}}]
    for index, diagnostic in enumerate(diagnostics):
        entries.extend(failure_entries(failure_kind, diagnostic, f"call-{index}"))
    transcript = _write_transcript(tmp_path, entries)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    rows = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    failures = [row for row in rows if row.get("candidate_kind") == failure_kind]
    assert len(failures) == 7
    assert len({row["failure_signature"] for row in failures}) == 7
    assert sum(row["count"] for row in failures) == 9


def test_candidate_events_separates_failures_with_different_exit_codes_and_diagnostics() -> None:
    """失敗署名を構成する終了コードと診断が異なる失敗は別候補にする。"""
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "tool": "toolu_01", "text": "Exit code 1\n詳細1"},
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "toolu_02", "text": "Exit code 2\n詳細2"},
        {"kind": "failed-tool", "record": "main", "line": 9, "tool": "toolu_03", "text": "Exit code 3\n詳細3"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert len(candidates[:-1]) == 3
    assert {candidate["candidate_kind"] for candidate in candidates[:-1]} == {"tool-failure"}
    assert sorted(candidate["count"] for candidate in candidates[:-1]) == [1, 1, 1]
    assert candidates[-1]["included_locators"] == [
        {"record": "main", "line": 2},
        {"record": "main", "line": 5},
        {"record": "main", "line": 9},
    ]


def test_candidate_events_classifies_auto_mode_denial_as_permission_denial() -> None:
    """auto mode classifierの拒否は、他の失敗と分かれた候補種別として抽出する。"""
    timeline = [
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 2,
            "tool": "toolu_01",
            "text": (
                "Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Self-Modification]."
            ),
        },
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "toolu_02", "text": "Exit code 1\n詳細"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    by_kind: dict[str, list[dict[str, object]]] = {}
    for candidate in candidates[:-1]:
        by_kind.setdefault(candidate["candidate_kind"], []).extend(candidate["locators"])
    assert by_kind["permission-denial"] == [{"record": "main", "line": 2}]
    assert {item["line"] for item in by_kind["tool-failure"]} == {2, 5}


def _bash_failure(line: int, command: str, text: str = "Exit code 1") -> dict[str, object]:
    return {
        "kind": "failed-tool",
        "record": "main",
        "line": line,
        "tool": f"toolu_{line}",
        "tool_name": "Bash",
        "operation": json.dumps({"command": command}, ensure_ascii=False),
        "text": text,
    }


def test_candidate_events_excludes_empty_negative_search_results_of_claude_bash() -> None:
    """検索が出力なしで一致0件を返した結果を除き、パイプ以外の連結・出力を伴う失敗と別の終了コードは残す。

    パイプラインは最終段が検索で終了コード1、または検索を起動する`xargs`で終了コード123の場合を一致0件とする。
    """
    timeline = [
        _bash_failure(1, "rg -n -F 'a|b' agent-toolkit"),
        _bash_failure(2, "git grep -n -F needle -- docs"),
        _bash_failure(3, "test -e /tmp/absent"),
        _bash_failure(4, "rg -n needle docs | head"),
        _bash_failure(5, "rg -n needle docs 2>/dev/null"),
        _bash_failure(6, "rg -n needle docs && echo found"),
        _bash_failure(7, "rg -n needle docs", "Exit code 2\nrg: docs: No such file or directory"),
        _bash_failure(8, "python3 check.py"),
        _bash_failure(9, "fd -e md . docs | xargs -r rg -n -F needle", "Exit code 123"),
        _bash_failure(10, "git ls-files -z | xargs -0 grep -n needle", "Exit code 123"),
        _bash_failure(11, "cat notes.txt | rg -n needle"),
        _bash_failure(12, "fd -e tmp . | xargs rm", "Exit code 123"),
        _bash_failure(13, "rg -l needle | xargs cat", "Exit code 123"),
        _bash_failure(14, "rg -n needle docs || true", "Exit code 123"),
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kept_lines = sorted(locator["line"] for candidate in candidates[:-1] for locator in candidate["locators"])
    assert kept_lines == [4, 6, 7, 8, 12, 13, 14]
    assert candidates[-1]["excluded"] == {"normal-negative-result": 7}


def test_candidate_events_treats_quoted_operator_shaped_search_terms_as_data() -> None:
    """引用・エスケープで渡した演算子形の検索語はデータとして扱い、実際の演算子を伴う失敗は残す。"""
    timeline = [
        _bash_failure(1, "rg -n -F '<<<<<<<' docs"),
        _bash_failure(2, "rg -n -F ';' docs"),
        _bash_failure(3, 'rg -n -F "&&" docs && git grep -n -F "<<<<<<<" -- docs'),
        _bash_failure(4, r"rg -n -F \| docs"),
        _bash_failure(5, "git ls-files | rg -F '<<<<<<<'"),
        _bash_failure(6, "git ls-files -z | xargs -0 rg -n -F '&&'", "Exit code 123"),
        _bash_failure(7, "rg -n -F '<<<<<<<' docs && echo found"),
        _bash_failure(8, "rg -n -F '<<<<<<<' docs > found.txt"),
        _bash_failure(9, "rg -n -F '<<<<<<<' docs", "Exit code 2"),
        _bash_failure(10, "rg -n -F ';' docs", "Exit code 1\nrg: docs: No such file or directory"),
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kept_lines = sorted(locator["line"] for candidate in candidates[:-1] for locator in candidate["locators"])
    assert kept_lines == [7, 9, 10]
    assert candidates[-1]["excluded"] == {"normal-negative-result": 7}


@pytest.mark.parametrize("kind", ["command-failure", "tool-failure"])
@pytest.mark.parametrize(
    ("command", "code", "excluded"),
    [
        ("rg x 2>/dev/null", 1, True),
        ("rg x 2>/dev/null", 2, False),
        ("python3 script.py 2>/dev/null", 1, False),
        ("rg x 2>/dev/null && echo ok", 1, False),
        ("rg x 2>/dev/null | head", 1, False),
    ],
)
def test_read_predicate_no_match_is_not_failure(kind, command, code, excluded):
    """両ホストの空診断の否定結果を除き、異常と帰属不能な終了を保持する。"""
    event = _bash_failure(1, command, f"Exit code {code}")
    if kind == "command-failure":
        event = {
            "tool": "CommandExecution",
            "exit_code": code,
            "diagnostic": "",
            "command": json.dumps(["bash", "-c", command]),
        }
    result = evidence_candidates.failure_candidate_exclusion(kind, event)
    assert (result == "normal-negative-result") is excluded


def test_public_bundle_excludes_claude_wait_continuations(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Bashの記録を公開bundleで処理し、包装の有無と出力形式が異なる待機継続を同じ区分へ集計する。"""
    entries = []
    cases = [
        ("atk agents wait", 3),
        ("/repo/agent-toolkit/bin/atk agents wait", 3),
        ("bash -lc 'atk agents wait'", 3),
        ("sh -c 'atk agents wait'", 3),
        ("atk agents wait && false", 3),
        ("atk agents wait", 2),
        # 起動名は共通の起動判定と同じ集合で認識する。
        ("/opt/plugin/agent_toolkit/atk.py agents wait", 3),
    ]
    for index, (command, code) in enumerate(cases):
        call_id = f"toolu_{index}"
        entries.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}},
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "is_error": True,
                                "content": f'Exit code {code}\n{{"status": "running"}}',
                            },
                        ],
                    },
                },
            ]
        )
    transcript = tmp_path / "claude-wait.jsonl"
    transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    records = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {locator["line"] for item in records[:-1] for locator in item["locators"]} == {10, 12}
    assert records[-1]["excluded"]["normal-nonterminal-result"] == 5


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r"rg 'a''b' 'C:\work\docs'", ["rg", "a'b", r"C:\work\docs"]),
        ('rg "a`"b" "C:\\work\\docs"', ["rg", 'a"b', r"C:\work\docs"]),
        ("rg a`|b '&&'", ["rg", "a|b", "&&"]),
        ("rg '$literal' docs", ["rg", "$literal", "docs"]),
        ("rg $expanded docs", None),
        ("rg needle docs | cat", None),
        ("rg 'unclosed", None),
    ],
)
def test_powershell_arguments_keep_their_quote_and_path_contract(command: str, expected: list[str] | None) -> None:
    """分類の公開結果だけでは分からない検索語とWindowsパスのデータ同一性を確かめる。"""
    actual = evidence_candidates._powershell_inner_args(["pwsh", "-Command", command])  # pylint: disable=protected-access
    assert actual == expected
