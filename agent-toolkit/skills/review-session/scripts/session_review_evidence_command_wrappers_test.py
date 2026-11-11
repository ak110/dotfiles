"""セッション振り返りの試験（session_review_evidence_command_wrappers_test）。"""

from __future__ import annotations

import json
import pathlib
import shlex

import pytest

from agent_toolkit._atk import outcome
from agent_toolkit._testing.session_review_evidence_support import _bundle_failed_commands_of_runtime


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_excludes_cd_prefixed_commands_like_standalone(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """先頭の`cd <パス>;`と`cd <パス> &&`を付けた4区分の正常な結果を、前置の無いコマンドと同じ区分へ除外する。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    cases = [
        (f"{prefix}{command}", code, output)
        for prefix in ("cd /repo; ", "cd /repo && ")
        for command, code, output in [
            ("atk wi grep needle", 1, no_match),
            ("atk agents wait", 3, '{"status": "running"}'),
            ("rg missing docs", 1, ""),
            ("make test", 1, "FAILED test_example.py::test_case"),
        ]
    ]
    records, _lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)

    assert not [item for item in records if item["kind"] == "candidate"]
    excluded = records[-1]["excluded"]
    assert excluded["normal-negative-result"] == 4
    assert excluded["normal-nonterminal-result"] == 2
    assert excluded["check-detected"] == 2


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_keeps_cd_failures_and_unsupported_prefixes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """`cd`の失敗を示す結果と、外す対象にしない前置の形は候補に残す。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    cd_failure = "bash: line 1: cd: /missing: No such file or directory"
    cases = [
        ("cd /missing; atk wi grep needle", 1, f"{cd_failure}\n{no_match}"),
        ("cd /missing && make test", 1, cd_failure),
        ("cd /missing; rg missing docs", 1, cd_failure),
        ("cd -P /repo && make test", 1, "FAILED test_example.py::test_case"),
        ("cd; atk wi grep needle", 1, no_match),
        ("cd /a; cd /b; atk wi grep needle", 1, no_match),
        ("cd /repo | atk wi grep needle", 1, no_match),
        ("cd /repo\natk wi grep needle", 1, no_match),
        ("cd /repo; atk wi grep needle; false", 1, no_match),
        ("cd /repo && atk agents wait; false", 3, '{"status": "running"}'),
        # 対照: 同じ記録の中で、外す対象の前置だけを持つ正常な結果は除外される。
        ("cd /repo; atk wi grep needle", 1, no_match),
    ]
    records, lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)

    candidate_lines = {locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]}
    assert candidate_lines == set(lines[:-1])
    excluded = records[-1]["excluded"]
    assert excluded["normal-negative-result"] == 1
    assert excluded.get("normal-nonterminal-result", 0) == 0
    assert excluded.get("check-detected", 0) == 0


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_powershell_classification_preserves_unsupported_failures(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """PSの引用・パス・cd前置を共通分類へ渡し、診断・式・連結・対応外モードは元位置へ残す。"""
    normal = [
        ["pwsh", "-NoProfile", "-Command", "rg 'a''b' 'C:\\work\\docs'"],
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-COMMAND", 'test -e "C:\\work\\file"'],
        ["powershell", "-Command", "git grep -F -- 'a|b'"],
        ["pwsh.exe", "-Command", "rg a`|b docs"],
        ["pwsh", "-Command", "cd 'C:\\work'; cmp -s one two"],
        ["pwsh", "-Command", "cd 'C:\\work' && test -f absent"],
    ]
    kept = [
        (["pwsh", "-Command", "rg missing docs"], 2, "不正な入力"),
        (["pwsh", "-Command", "rg missing docs"], 1, "アクセス拒否"),
        (["pwsh", "-Command", "rg missing docs | cat"], 1, ""),
        (["pwsh", "-Command", "rg missing docs; false"], 1, ""),
        (["pwsh", "-Command", "rg $pattern docs"], 1, ""),
        (["pwsh", "-Command", "rg $(Get-Item .) docs"], 1, ""),
        (["pwsh", "-Command", "rg 'unclosed docs"], 1, ""),
        (["pwsh", "-Command", "cd /a; cd /b; pytest"], 1, "FAILED test_case"),
        (["pwsh", "-Command", "cd /missing; pytest"], 1, "Set-Location: パスが存在しない"),
        (["pwsh", "-File", "check.ps1"], 1, ""),
        (["pwsh", "-EncodedCommand", "encoded"], 1, ""),
        (["pwsh", "-Command", "-"], 1, ""),
        (["pwsh", "-Command", "rg missing docs > result"], 1, ""),
    ]
    cases = [(shlex.join(args), 1, "") for args in normal]
    cases += [(shlex.join(args), code, output) for args, code, output in kept]
    cases += [(shlex.join(["pwsh", "-Command", "cd /repo; uv run --frozen pytest"]), 1, "FAILED test_case")]
    records, lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == set(lines[len(normal) : -1])
    assert records[-1]["excluded"]["normal-negative-result"] == len(normal)
    assert records[-1]["excluded"]["check-detected"] == 1
    _assert_bundle_original_commands(tmp_path, candidates, runtime, cases, lines)
    assert any(json.loads(item["failure_signature"])[1:4] == ["rg", "missing", 2] for item in candidates)


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_run_command_uses_complete_child_result_only(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """公開run-commandの保存JSONで子へ帰属させ、包装の異常と子の診断を候補へ残す。"""

    def case(child: list[str], **changes: object) -> tuple[str, int, str]:
        result = {
            "argv": child,
            "child_exit_code": 1,
            "timed_out": False,
            "signal": None,
            "record_path": "/records/record.json",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
        }
        result.update(changes)
        output = json.dumps(result) + "\n失敗: 外部コマンドが終了コード1で終了した\n次の操作: 保存先を診断する"
        return shlex.join(["atk", "run-command", "--timeout", "30", "--", *child]), 1, output

    cases = [
        case(child)
        for child in [["rg", "missing", "docs"], ["git", "grep", "needle"], ["cmp", "-s", "a", "b"], ["test", "-e", "absent"]]
    ]
    cases += [case(["pytest"], stdout_bytes=120), case(["uv", "run", "--frozen", "pytest"], stderr_bytes=120)]
    kept = [
        case(["rg", "missing", "docs"], stderr_bytes=20),
        case(["test", "-e", "absent"], timed_out=True),
        case(["pytest"], signal=15),
        case(["pytest"], record_path=None),
        case(["pytest"], child_exit_code=2),
        case(["pytest"], stdout_bytes=-1),
        case(["pytest"], stdout_bytes=True),
    ]
    command, code, output = case(["test", "-e", "absent"])
    kept += [
        (command, code, "{invalid JSON}"),
        (command, code, '{"argv": ["test"], "child_exit_code": 1}'),
        (command + "; false", code, output),
    ]
    cases += kept
    records, lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == set(lines[6:])
    assert records[-1]["excluded"]["normal-negative-result"] == 4
    assert records[-1]["excluded"]["check-detected"] == 2
    _assert_bundle_original_commands(tmp_path, candidates, runtime, cases, lines)


def _assert_bundle_original_commands(
    tmp_path: pathlib.Path, candidates: list[dict], runtime: str, cases: list[tuple[str, int, str]], lines: list[int]
) -> None:
    """個別証拠が元の包装コマンドと位置を保持することを公開bundleで確かめる。"""
    for candidate in candidates:
        saved = json.loads(
            (tmp_path / "bundle" / "candidate-evidence" / f"{candidate['candidate_id']}.json").read_text(encoding="utf-8")
        )
        for event in saved["events"]:
            if runtime == "codex":
                original = cases[lines.index(event["line"])][0]
                assert event["command"][-1] == original
            elif event["kind"] == "tool-use":
                original = cases[lines.index(event["line"] + 1)][0]
                assert event["input"]["command"] == original
