"""公開run-scriptから未判定記録を生成・再生成・根拠更新する契約を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
import typing
import xml.etree.ElementTree as ET

import pytest

from agent_toolkit._atk import review_table, run_script
from agent_toolkit._atk.wi import entries, repo, sync
from agent_toolkit._testing import git_repository

WI = "20261008-043514-001.md"


def _run(*args: str, name: str = "verification-record") -> int:
    return run_script.dispatch(argparse.Namespace(script_name=name, script_args=list(args)))


@pytest.mark.parametrize("case", ["normal", "split", "invalid-map", "ambiguous", "outcome", "reviewed_head", "list"])
def test_public_pending_commit_rewrite_preserves_observation_and_judgment(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """所有CLIで参照を更新し、不正対応と判定済み入力は全記録を保持して拒否する。"""
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    payload = json.loads(record.read_text(encoding="utf-8"))
    old, new, other = "a" * 40, "b" * 40, "c" * 40
    reference = "a" * 7 if case == "ambiguous" else old
    for row in payload["user_requirements"]:
        row["evidence"] = f"commit:{reference} を確認。取得版HEAD={old}、裸のOID={old}"
    if case in {"outcome", "reviewed_head"}:
        payload["user_requirements"][0][case] = "達成" if case == "outcome" else old
    _json(record, payload)
    mapping = {old: new}
    if case == "split":
        mapping = {old: [{"commit": new, "awi": [WI]}, {"commit": other, "awi": [WI]}]}
    elif case == "ambiguous":
        mapping["a" * 39 + "d"] = other
    map_path = _json(tmp_path / "rewrite.json", [] if case == "invalid-map" else mapping)
    before = record.read_bytes()
    capsys.readouterr()
    args = ["--output", str(record), "--rewrite-map", str(map_path)]
    if case == "list":
        args.append("--list")
    successful = case in {"normal", "split"}
    assert _run(*args) == (0 if successful else 1)
    result = capsys.readouterr()
    if not successful:
        assert record.read_bytes() == before
        assert "次の操作:" in result.err
        return
    updated = json.loads(record.read_text(encoding="utf-8"))
    expected = f"commit:{new}" + (f"、commit:{other}" if case == "split" else "")
    for original, row in zip(payload["user_requirements"], updated["user_requirements"], strict=True):
        assert row == {**original, "evidence": original["evidence"].replace(f"commit:{old}", expected)}
        assert not row["outcome"] and not row["reviewed_head"]
    assert "未判定検証記録" in result.out and "--expected-head" not in result.out


def _two_array_record(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict[str, typing.Any]]:
    """両配列に2行ずつの未判定行を持つ記録を生成して返し、配列をまたぐ行選択を公開CLIで検収できるようにする。"""
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["wi_conditions"] = [
        {
            "awi": WI,
            "condition": f"保存する{number}。",
            "source": f"試験の完成条件{number}",
            "outcome": "",
            "reviewed_head": "",
            "evidence": "",
        }
        for number in (1, 2)
    ]
    _json(record, payload)
    return record, payload


@pytest.mark.parametrize("explicit", [False, True], ids=["omitted", "explicit"])
def test_row_options_select_rows_without_select_row(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], explicit: bool
) -> None:
    """行別指定の更新先だけで両配列の行を1回ずつ更新し、同じ集合の明示選択とも同じ結果を保存する。"""
    text = "def test_first():\n    assert True\ndef test_second():\n    assert True\n"
    trial = _saved_trial(tmp_path, text, "row-only")
    monkeypatch.chdir(tmp_path)
    diagnostics = _saved_diagnostics(
        tmp_path,
        [
            {
                "kind": "diagnostic",
                "command": "lint",
                "file": "trial_test.py",
                "messages": [{"line": 2, "col": 1, "severity": "warning", "rule": "W001", "msg": "行別の観測"}],
            }
        ],
        {"trial_test.py": text},
        "row-only-diagnostics",
    )
    results = _json(tmp_path / "results.json", {"junit": trial, "diagnostics": diagnostics})
    record, payload = _two_array_record(tmp_path)
    capsys.readouterr()
    assert _run("--output", str(record), "--results-file", str(results), "--list-results") == 0
    (diagnostic,) = json.loads(capsys.readouterr().out)["diagnostics"]
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("第一の保存結果を観測した。", encoding="utf-8")
    second.write_text("第二の保存結果を観測した。", encoding="utf-8")
    # 同じ行へ根拠・試験・診断の3種類を記し、別の配列の行へは根拠と試験を記す。
    args = ["--output", str(record), "--results-file", str(results), "--mode", "replace"]
    args += ["--row-evidence-file", "wi_conditions:2", str(first)]
    args += ["--row-result-test", "wi_conditions:2", "trial_test.py::test_first"]
    args += ["--row-result-diagnostic", "wi_conditions:2", diagnostic]
    args += ["--row-result-test", "user_requirements:1", "trial_test.py::test_second"]
    args += ["--row-evidence-file", "user_requirements:1", str(second)]
    if explicit:
        args += ["--select-row", "user_requirements:1", "--select-row", "wi_conditions:2"]
    assert _run(*args) == 0, capsys.readouterr().err
    updated = json.loads(record.read_text(encoding="utf-8"))
    assert updated["wi_conditions"][0] == payload["wi_conditions"][0]
    assert updated["user_requirements"][1] == payload["user_requirements"][1]
    for section, index, own, other, evidence in (
        ("wi_conditions", 1, "test_first", "test_second", first),
        ("user_requirements", 0, "test_second", "test_first", second),
    ):
        row, original = updated[section][index], payload[section][index]
        assert {**row, "evidence": ""} == original
        assert row["evidence"].count(f"trial_test.py::{own}") == 1 and other not in row["evidence"]
        assert row["evidence"].count(evidence.read_text(encoding="utf-8")) == 1
        assert ("after.message_position" in row["evidence"]) == (section == "wi_conditions")


def test_review_row_options_select_rows_without_select_row(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """レビュー更新は判定・出所・継承・受理済み参照の更新先だけを選び、第2要素が指す行を選ばない。"""
    repository = git_repository.init_repository(tmp_path / "repo", commit_message="基準")
    head = git_repository.git_output(repository, "rev-parse", "HEAD")
    monkeypatch.chdir(repository)
    current, payload = _two_array_record(tmp_path)
    payload["wi_conditions"].append({**payload["wi_conditions"][0], "condition": "保存する3。", "source": "試験の完成条件3"})
    _json(current, payload)
    # 未判定記録と受理済み証拠の行順を入れ替え、第2要素の行番号が現在の別の行を指すようにする。
    pending = tmp_path / "verification.json"
    pending_rows = [{**row, "evidence": f"未判定の観測{index}"} for index, row in enumerate(payload["user_requirements"], 1)]
    _json(pending, {"wi_conditions": [], "user_requirements": pending_rows[::-1]})
    accepted = tmp_path / "accepted.json"
    accepted_rows = [
        {**row, "outcome": "達成", "reviewed_head": head, "evidence": "以前の判定"} for row in payload["wi_conditions"]
    ]
    _json(accepted, {"wi_conditions": accepted_rows[1::-1], "user_requirements": []})
    reason, expired = tmp_path / "reason.txt", tmp_path / "expired.txt"
    reason.write_text("今回の差分は保存の入力を変えていない。", encoding="utf-8")
    expired.write_text("完成条件は失効の判断根拠へ従う。", encoding="utf-8")
    output = tmp_path / "review.json"
    args = [str(current), "--output", str(output), "--mode", "replace", "--reviewed-head", head]
    args += ["--row-outcome", "wi_conditions:1", "達成", "--row-evidence-file", "wi_conditions:1", str(reason)]
    args += ["--accepted-evidence", str(accepted), "--accepted-row", "wi_conditions:1", "wi_conditions:2"]
    args += ["--row-outcome", "wi_conditions:3", "失効", "--row-evidence-file", "wi_conditions:3", str(expired)]
    args += ["--row-source", "wi_conditions:3", "失効の判断根拠"]
    args += ["--verification-record", str(pending), "--verification-row", "user_requirements:1", "user_requirements:2"]
    args += ["--row-outcome", "user_requirements:1", "達成"]
    assert _run(*args, name="exec-review-evidence-check") == 0, capsys.readouterr().err
    saved = json.loads(output.read_text(encoding="utf-8"))
    accepted_row, unselected, expired_row = saved["wi_conditions"]
    assert unselected == payload["wi_conditions"][1]
    assert saved["user_requirements"][1] == payload["user_requirements"][1]
    assert accepted_row["outcome"] == "達成" and accepted_row["evidence"].startswith("受理済み判定: ")
    assert json.loads(accepted_row["evidence"].splitlines()[0].removeprefix("受理済み判定: "))["row"] == 2
    assert accepted_row["evidence"].count(reason.read_text(encoding="utf-8")) == 1
    assert expired_row["source"] == "失効の判断根拠" and expired_row["outcome"] == "失効"
    inherited = saved["user_requirements"][0]
    assert inherited["outcome"] == "達成" and inherited["evidence"] == "未判定の観測1"
    assert inherited["source"] == payload["user_requirements"][0]["source"] and inherited["reviewed_head"] == head
    assert json.loads(current.read_text(encoding="utf-8")) == payload


# 拒否の理由ごとに、診断が示す語を対にして、別の理由による拒否で通らないようにする。
_INVALID_SELECTIONS = {
    "outside": "--select-rowを明示した場合はその集合",
    "missing-row": "選択行が存在しません",
    "malformed": "行選択はwi_conditions:N",
    "duplicate": "各選択行へ一意に指定する",
    "common": "全選択行への共通指定は別々に使う",
    "mode": "--modeを指定する",
    "missing-file": "second.txt",
}


@pytest.mark.parametrize(
    ("name", "invalid"),
    [("verification-record", invalid) for invalid in _INVALID_SELECTIONS]
    + [("exec-review-evidence-check", invalid) for invalid in [*_INVALID_SELECTIONS, "outcome"]],
)
def test_row_options_reject_invalid_selection_without_partial_write(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], name: str, invalid: str
) -> None:
    """明示選択の集合外・不正な行・重複・共通値との混在・mode欠落・根拠の不在・判定不足は、入力と保存先を変えずに拒否する。"""
    record, _payload = _two_array_record(tmp_path)
    files = [tmp_path / "first.txt", tmp_path / "second.txt"]
    files[0].write_text("第一の保存結果を観測した。", encoding="utf-8")
    files[1].write_text("第二の保存結果を観測した。", encoding="utf-8")
    reviewing = name == "exec-review-evidence-check"
    output = tmp_path / "review.json" if reviewing else record
    args = [str(record)] if reviewing else []
    args += ["--output", str(output)]
    if invalid != "mode":
        args += ["--mode", "replace"]
    selectors = ["wi_conditions:2", "user_requirements:1"]
    for selector, path in zip(selectors, files, strict=True):
        args += ["--row-evidence-file", selector, str(path)]
        if reviewing and not (invalid == "outcome" and selector == selectors[1]):
            args += ["--row-outcome", selector, "達成"]
    if reviewing:
        args += ["--reviewed-head", "a" * 40]
    if invalid == "outside":
        args += ["--select-row", selectors[0]]
    elif invalid == "missing-row":
        args += ["--row-evidence-file", "user_requirements:99", str(files[0])]
    elif invalid == "malformed":
        args += ["--row-evidence-file", "wi_conditions", str(files[0])]
    elif invalid == "duplicate":
        args += ["--row-evidence-file", selectors[0], str(files[1])]
    elif invalid == "common":
        args += ["--evidence-file", str(files[0])]
    elif invalid == "missing-file":
        files.pop().unlink()
    inputs = {path: path.read_bytes() for path in (record, *files)}
    capsys.readouterr()
    assert _run(*args, name=name) == 1
    assert {path: path.read_bytes() for path in inputs} == inputs
    assert not reviewing or not output.exists()
    diagnostic = capsys.readouterr().err
    assert "次の操作:" in diagnostic
    assert {**_INVALID_SELECTIONS, "outcome": "判定の無い行"}[invalid] in diagnostic


@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
def test_help_describes_row_selection_contract(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """--select-rowのhelpが省略時に選ぶ全ての行別指定を挙げ、案内どおりの省略と共通値の行選択を受理する。"""
    monkeypatch.setenv("COLUMNS", "1000")
    assert _run("--help", name=name) == 0
    lines = capsys.readouterr().out.splitlines()
    start = next(index for index, line in enumerate(lines) if line.lstrip().startswith("--select-row "))
    end = next(index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith("-"))
    help_text = "".join(line.strip() for line in lines[start:end])
    reviewing = name == "exec-review-evidence-check"
    targets = ["--row-evidence-file", "--row-result-test", "--row-result-diagnostic"]
    if reviewing:
        targets += ["--row-outcome", "--row-source", "--verification-row", "--accepted-row"]
    assert all(target in help_text for target in targets), help_text
    record, payload = _two_array_record(tmp_path)
    explanation = tmp_path / "explanation.txt"
    explanation.write_text("保存結果を観測した。", encoding="utf-8")
    output = tmp_path / "review.json"
    prefix = [str(record), "--output", str(output)] if reviewing else ["--output", str(record)]
    prefix += ["--mode", "replace"]
    judgment = ["--row-outcome", "wi_conditions:1", "証拠不足", "--reviewed-head", "a" * 40] if reviewing else []
    assert _run(*prefix, "--row-evidence-file", "wi_conditions:1", str(explanation), *judgment, name=name) == 0
    saved = json.loads((output if reviewing else record).read_text(encoding="utf-8"))
    assert saved["wi_conditions"][0]["evidence"] == explanation.read_text(encoding="utf-8")
    assert saved["wi_conditions"][1] == payload["wi_conditions"][1]
    common = [*prefix, "--evidence-file", str(explanation)]
    if reviewing:
        common += ["--reviewed-head", "a" * 40]
    capsys.readouterr()
    assert _run(*common, name=name) == 1
    assert "--select-row" in capsys.readouterr().err
    selected = ["--select-row", "user_requirements:2"]
    if reviewing:
        selected += ["--row-outcome", "user_requirements:2", "証拠不足"]
    assert _run(*common, *selected, name=name) == 0
    saved = json.loads((output if reviewing else record).read_text(encoding="utf-8"))
    assert saved["user_requirements"][1]["evidence"] == explanation.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
def test_public_all_single_value_options_reject_conflicting_repetition(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """公開CLIが登録する単一値を列挙し、先の値を失う入力を一括して検出する。"""
    actions = []
    parse_args = argparse.ArgumentParser.parse_args

    def observe(parser: argparse.ArgumentParser, *args: typing.Any, **kwargs: typing.Any) -> argparse.Namespace:
        # argparseが登録した全単一値入力を列挙するAPIを持たないため、登録表から被覆を確かめる。
        actions.extend(parser._actions)  # pylint: disable=protected-access
        return parse_args(parser, *args, **kwargs)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", observe)
    assert _run("--help", name=name) == 0
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse_args)
    capsys.readouterr()
    record = tmp_path / "record.json"
    record.write_text("{}\n", encoding="utf-8")
    selected = [
        action for action in actions if action.option_strings and type(action).__name__ in {"_StoreAction", "_SingleValue"}
    ]
    assert selected
    for action in selected:
        option = action.option_strings[0]
        if action.choices is not None:
            first, second = list(action.choices)[:2]
        elif action.type is int:
            first, second = "1", "2"
        elif option == "--output":
            first, second = str(record), str(tmp_path / "other.json")
        else:
            first, second = str(tmp_path / "first"), str(tmp_path / "second")
        assert _run("--output", str(record), option, str(first), option, str(second), name=name) == 2, option
        assert record.read_bytes() == b"{}\n", option
        diagnostic = capsys.readouterr().err
        assert option in diagnostic and "呼び出しを分割" in diagnostic


@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
@pytest.mark.parametrize("kind", ["test", "diagnostic"])
def test_public_row_results_preserve_distinct_associations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str, kind: str
) -> None:
    """行別の完全試験名と診断識別子を保存し、重複・非選択行・未知の結果では部分保存しない。"""
    text = "def test_first():\n    assert True\ndef test_second():\n    assert True\n"
    trial = _saved_trial(tmp_path, text, "row-results")
    monkeypatch.chdir(tmp_path)
    diagnostics = _saved_diagnostics(
        tmp_path,
        [
            {
                "kind": "diagnostic",
                "command": "lint",
                "file": "trial_test.py",
                "messages": [
                    {"line": 2, "col": 1, "severity": "warning", "rule": "W001", "msg": "第一の観測"},
                    {"line": 4, "col": 1, "severity": "warning", "rule": "W002", "msg": "第二の観測"},
                ],
            }
        ],
        {"trial_test.py": text},
        "row-diagnostics",
    )
    results = _json(tmp_path / "results.json", {"junit": trial, "diagnostics": diagnostics})
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    prefix = [str(record)] if name == "exec-review-evidence-check" else []
    prefix += ["--output", str(record), "--results-file", str(results), "--mode", "replace"]
    selections = ["user_requirements:1", "user_requirements:2"]
    values = ["trial_test.py::test_first", "trial_test.py::test_second"]
    if kind == "diagnostic":
        capsys.readouterr()
        listing = ["--output", str(record)] if name == "verification-record" else []
        assert _run(*listing, "--results-file", str(results), "--list-results", name=name) == 0
        displayed = json.loads(capsys.readouterr().out)["diagnostics"]
        values = sorted(displayed, key=lambda identity: displayed[identity]["after"]["message_position"])
        assert len(values) == 2
    option = "--row-result-" + kind
    args = list(prefix)
    for selector, value in zip(selections, values, strict=True):
        args += ["--select-row", selector, option, selector, value]
        if name == "exec-review-evidence-check":
            args += ["--row-outcome", selector, "達成"]
    if name == "exec-review-evidence-check":
        args += ["--reviewed-head", _git(tmp_path, "rev-parse", "HEAD")]
    assert _run(*args, name=name) == 0, capsys.readouterr().err
    rows = json.loads(record.read_text(encoding="utf-8"))["user_requirements"]
    if kind == "test":
        for index, row in enumerate(rows):
            assert values[index] in row["evidence"] and values[1 - index] not in row["evidence"]
    else:
        for index, row in enumerate(rows, 1):
            assert f"after.message_position: `{index}`" in row["evidence"]
            assert f"after.message_position: `{3 - index}`" not in row["evidence"]
    before = record.read_bytes()
    for selector, value in [(selections[0], values[0]), ("user_requirements:99", values[0]), (selections[0], "unknown")]:
        assert _run(*args, option, selector, value, name=name) == 1
        assert record.read_bytes() == before


@pytest.mark.parametrize("extra", [{"new_source": "別の出所"}, {"outcome": "失効"}, {"reviewed_head": "a" * 40}])
def test_pending_json_update_preserves_source_and_rejects_review_fields_atomically(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], extra: dict[str, str]
) -> None:
    """手動根拠だけで未判定行を更新し、後続行にレビュー欄があれば全保存を拒否する。"""
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    original = json.loads(record.read_text(encoding="utf-8"))
    evidence = tmp_path / "observation.txt"
    evidence.write_text("保存値を観測した。", encoding="utf-8")
    update = {
        "section": "user_requirements",
        "row": 1,
        "source": original["user_requirements"][0]["source"],
        "evidence_file": str(evidence),
        "mode": "replace",
    }
    updates = tmp_path / "updates.json"
    updates.write_text(json.dumps([update]), encoding="utf-8")
    args = ["--output", str(record), "--updates-file", str(updates)]
    assert _run(*args) == 0, capsys.readouterr().err
    expected = json.loads(json.dumps(original))
    expected["user_requirements"][0]["evidence"] = "保存値を観測した。"
    assert json.loads(record.read_text(encoding="utf-8")) == expected
    evidence.write_text("追記する観測。", encoding="utf-8")
    bad = {**update, "row": 2, "source": original["user_requirements"][1]["source"], **extra}
    updates.write_text(json.dumps([{**update, "mode": "append"}, bad]), encoding="utf-8")
    before = record.read_bytes()
    assert _run(*args) == 1
    assert record.read_bytes() == before
    assert "更新行の項目" in capsys.readouterr().err


def _plan(tmp_path: pathlib.Path, mode: str) -> pathlib.Path:
    path = tmp_path / "計画.md"
    origin = f"エージェント由来のWI ({WI})" if mode == "plan-wi" else "ユーザー指示"
    path.write_text(
        "## 実施内容\n\n| 実施内容 | 由来 | 採否 | 根拠 |\n| --- | --- | --- | --- |\n"
        f"| 要約 | {origin} | 採用 | 原文を採用 |\n"
        + (
            ""
            if mode == "plan-wi"
            else "\n## 変更履歴\n\n### ユーザー発言1\n\n```text\n保存する。\n```\n\n"
            "### ユーザー発言2\n\n```text\n保存する。\n```\n"
        ),
        encoding="utf-8",
    )
    return path


def _wi(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(sync, "ensure_environment", lambda _home: tmp_path)
    monkeypatch.setattr(repo, "resolve_repo_id", lambda _path: "example")
    monkeypatch.setattr(
        entries,
        "read_named_entries",
        lambda _notes, _names, **_kwargs: (
            [
                (
                    tmp_path / WI,
                    "example",
                    "---\ntype: awi\nsource: agent\n---\n## 完成条件\n- 保存する。\n- 再読込する。\n",
                    "processing",
                    "awi",
                )
            ],
            [],
        ),
    )


def test_public_generation_guides_non_git_cwd_without_writing_and_recovers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """未判定記録の生成も対象worktreeへ同じ引数を持ち込める案内を返す。"""
    _wi(monkeypatch, tmp_path)
    plan = _plan(tmp_path, "plan-user")
    record = tmp_path / "pending.json"
    monkeypatch.chdir(tmp_path)
    argv = ["--output", str(record), "--plan", str(plan)]
    assert _run(*argv) == 1
    assert not record.exists()
    assert "Git worktree" in capsys.readouterr().err
    repository = tmp_path / "repo"
    repository.mkdir()
    git_repository.init_repository(repository)
    monkeypatch.chdir(repository)
    assert _run(*argv) == 0
    assert len(json.loads(record.read_text())["user_requirements"]) == 2


@pytest.mark.parametrize("mode", ["wi", "plan-wi", "plan-user"])
def test_public_generation_list_roundtrip(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """入力形ごとの原文集合が雛形と一致し、同文別出所の一方だけを更新・保持できる。"""
    _wi(monkeypatch, tmp_path)
    record, template = tmp_path / "record.json", tmp_path / "template.json"
    plan = _plan(tmp_path, mode)
    args = ["--wi", WI] if mode == "wi" else ["--plan", str(plan)]
    assert _run("--output", str(record), *args, "--list") == 0
    first = json.loads(record.read_text(encoding="utf-8"))
    template_args = [WI] if mode == "wi" else ["--plan", str(plan)]
    assert _run("--template", str(template), *template_args, name="exec-review-evidence-check") == 0
    assert json.loads(template.read_text(encoding="utf-8")) == first
    capsys.readouterr()
    assert _run("--output", str(record), "--list") == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(listed) == 2
    chosen = listed[1]
    assert chosen["original"] != "要約"
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("保存操作で再読込値を観測", encoding="utf-8")
    update = [
        "--output",
        str(record),
        "--section",
        chosen["section"],
        "--row",
        "2",
        "--source",
        chosen["source"],
        "--evidence-file",
        str(evidence),
    ]
    assert _run(*update, "--mode", "append") == 0
    evidence.write_text("別条件で再確認", encoding="utf-8")
    assert _run(*update, "--mode", "append") == 0
    added = json.loads(record.read_text(encoding="utf-8"))
    assert added[chosen["section"]][1]["evidence"] == "保存操作で再読込値を観測\n別条件で再確認"
    assert _run(*update, "--mode", "replace") == 0
    assert _run("--output", str(record), *args, "--list") == 0
    final = json.loads(record.read_text(encoding="utf-8"))
    expected = first
    expected[chosen["section"]][1]["evidence"] = "別条件で再確認"
    assert final == expected


def test_public_regeneration_adds_missing_row_and_keeps_existing_evidence(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じoutputの再生成で不足行を補い、原文別出所の既存根拠を保持する。"""
    plan = _plan(tmp_path, "plan-user")
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(plan), "--list") == 0
    assert _run("--output", str(record), "--list") == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("修正前から保持する根拠", encoding="utf-8")
    assert (
        _run(
            "--output",
            str(record),
            "--section",
            "user_requirements",
            "--row",
            "2",
            "--source",
            listed[1]["source"],
            "--evidence-file",
            str(evidence),
            "--mode",
            "replace",
        )
        == 0
    )
    before = json.loads(record.read_text(encoding="utf-8"))["user_requirements"]
    current = plan.read_text(encoding="utf-8")
    plan.write_text(current + "\n### ユーザー発言3\n\n```text\n不足行を追加する。\n```\n", encoding="utf-8")
    assert _run("--output", str(record), "--plan", str(plan), "--list") == 0
    after = json.loads(record.read_text(encoding="utf-8"))["user_requirements"]
    assert after[:2] == before
    assert after[2]["requirement"] == "不足行を追加する。"
    assert all(row["outcome"] == row["reviewed_head"] == "" for row in after)


def test_public_reference_updates_and_inheritance_reach_final_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """単一行の更新と更新配列の両方で両配列へ記録した新しい参照が、未判定根拠の継承を経て証拠の確認まで到達する。"""
    _wi(monkeypatch, tmp_path)
    repository = git_repository.init_repository(tmp_path / "repo", files={"base.txt": "基準\n"}, commit_message="基準")
    monkeypatch.chdir(repository)
    head = git_repository.git_output(repository, "rev-parse", "HEAD")
    plan = _plan(tmp_path, "plan-user")
    record, template = tmp_path / "pending.json", tmp_path / "review.json"
    assert _run("--output", str(record), "--wi", WI, "--plan", str(plan)) == 0
    assert _run("--template", str(template), WI, "--plan", str(plan), name="exec-review-evidence-check") == 0
    data = json.loads(record.read_text())
    observation = tmp_path / "saved output.txt"
    observation.write_text("保存結果\n再読込結果\n要求の保存結果\n要求の再確認結果\n", encoding="utf-8")
    updates, judged = [], []
    for section in ("wi_conditions", "user_requirements"):
        for index, row in enumerate(data[section], 1):
            number = index if section == "wi_conditions" else index + 2
            evidence = tmp_path / f"{section}-{index}.txt"
            evidence.write_text(f'stdout="{observation}:{number}": 観測した結果を確認\n', encoding="utf-8")
            update = {
                "section": section,
                "row": index,
                "source": row["source"],
                "evidence_file": str(evidence),
                "mode": "replace",
            }
            if index == 1:
                assert (
                    _run(
                        "--output",
                        str(record),
                        "--section",
                        section,
                        "--row",
                        str(index),
                        "--source",
                        row["source"],
                        "--evidence-file",
                        str(evidence),
                        "--mode",
                        "replace",
                    )
                    == 0
                )
            else:
                updates.append(update)
            judged.append(
                {
                    "section": section,
                    "row": index,
                    "source": row["source"],
                    "verification_source": row["source"],
                    "mode": "replace",
                    "outcome": "達成",
                    "reviewed_head": head,
                }
            )
    update_path = tmp_path / "updates.json"
    update_path.write_text(json.dumps(updates), encoding="utf-8")
    assert _run("--output", str(record), "--updates-file", str(update_path)) == 0
    preserved = record.read_bytes()
    assert _run("--output", str(record), "--wi", WI, "--plan", str(plan)) == 0
    assert record.read_bytes() == preserved
    update_path.write_text(json.dumps(judged), encoding="utf-8")
    assert (
        _run(
            str(template),
            "--verification-record",
            str(record),
            "--updates-file",
            str(update_path),
            "--output",
            str(template),
            name="exec-review-evidence-check",
        )
        == 0
    )
    assert _run(str(template), WI, "--plan", str(plan), "--expected-head", head, name="exec-review-evidence-check") == 0, (
        capsys.readouterr().err
    )
    assert record.read_bytes() == preserved


@pytest.mark.parametrize("invalid", ["source", "row", "outcome", "reviewed_head", "json", "empty-evidence", "missing-argument"])
def test_public_record_rejects_invalid_update_without_write(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """指定不成立・判定済み・不正書式は非0となり、記録のバイト列を保持する。"""
    record = tmp_path / "record.json"
    plan = _plan(tmp_path, "plan-user")
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    data = json.loads(record.read_text(encoding="utf-8"))
    if invalid in {"outcome", "reviewed_head"}:
        data["user_requirements"][0][invalid] = "達成" if invalid == "outcome" else "a" * 40
        record.write_text(json.dumps(data), encoding="utf-8")
    if invalid == "json":
        record.write_text("{", encoding="utf-8")
    before = record.read_bytes()
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("" if invalid == "empty-evidence" else "観測", encoding="utf-8")
    args = [
        "--output",
        str(record),
        "--section",
        "user_requirements",
        "--row",
        "9" if invalid == "row" else "1",
        "--source",
        "別出所" if invalid == "source" else data["user_requirements"][0]["source"],
        "--evidence-file",
        str(evidence),
    ]
    if invalid != "missing-argument":
        args.extend(["--mode", "replace"])
    assert _run(*args) == 1
    assert record.read_bytes() == before
    assert "次の操作:" in capsys.readouterr().err


def _reference_record(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, str, pathlib.Path]:
    """追跡ファイル・作業ツリーだけのファイル・リポジトリ外の記録を持つ対象worktreeで、未判定記録を生成する。"""
    repository = git_repository.init_repository(
        tmp_path / "repo", files={"docs/tracked.md": "# 記録\n本文\n"}, commit_message="基準"
    )
    # 作業ツリーにだけある未commitのファイル。実装担当は未commitの状態で結果を記録する。
    (repository / "docs/new.md").write_text("# 新規\n", encoding="utf-8")
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "record.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(repository)
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    source = json.loads(record.read_text(encoding="utf-8"))["user_requirements"][0]["source"]
    return record, source, saved


def _update_route(route: str, record: pathlib.Path, source: str, evidence: pathlib.Path) -> list[str]:
    """未判定記録の3つの更新方法（`--select-row`、`--updates-file`、従来の`--section`・`--row`）で、1行目へ根拠を追記する引数を返す。"""
    if route == "select-row":
        return [
            "--output",
            str(record),
            "--select-row",
            "user_requirements:1",
            "--evidence-file",
            str(evidence),
            "--mode",
            "append",
        ]
    if route == "updates-file":
        update = {"section": "user_requirements", "row": 1, "source": source, "evidence_file": str(evidence), "mode": "append"}
        return ["--output", str(record), "--updates-file", str(_json(evidence.with_suffix(".json"), [update]))]
    return [
        *("--output", str(record), "--section", "user_requirements", "--row", "1", "--source", source),
        *("--evidence-file", str(evidence), "--mode", "append"),
    ]


@pytest.mark.parametrize("route", ["select-row", "updates-file", "legacy"])
@pytest.mark.parametrize(
    ("text", "reference"),
    [
        ("{saved}/record.json、同dir/stdout.bin:1 を確認した", "dir/stdout.bin:1"),
        ("出力値はnested/note.md:1だった", "nested/note.md:1"),
    ],
    ids=["same-dir", "plain-value"],
)
def test_public_update_rejects_unresolved_references(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    route: str,
    text: str,
    reference: str,
) -> None:
    """返却前の確認が拒否する参照を、未判定記録の3つの更新方法が保存の時点で全件示して拒否し、記録を変えない。

    位置を持つ明示参照は、追跡ファイルにも作業ツリーにも無い場合に保存を拒否する。
    """
    record, source, saved = _reference_record(tmp_path, monkeypatch)
    evidence = tmp_path / "evidence.txt"
    evidence.write_text(text.format(saved=saved), encoding="utf-8")
    before = record.read_bytes()
    capsys.readouterr()
    assert _run(*_update_route(route, record, source, evidence)) == 1
    assert record.read_bytes() == before
    error = capsys.readouterr().err
    assert f"user_requirements[0]（行番号1）.evidence: 参照『{reference}』" in error
    assert "`evidence`のファイル参照は、対象worktreeのルートからの相対パスか絶対パスで書き" in error
    assert "直すのはその行の`evidence`だけ" in error
    assert "次の操作:" in error


@pytest.mark.parametrize("route", ["select-row", "updates-file", "legacy"])
@pytest.mark.parametrize(
    "text",
    ["保存結果は{saved}/record.jsonにある", "docs/tracked.md:2 の本文を確認した", "docs/new.md:1 の見出しを確認した"],
    ids=["absolute", "tracked", "worktree-only"],
)
def test_public_update_accepts_resolved_references(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, route: str, text: str
) -> None:
    """絶対パスの実在ファイル、HEADの追跡ファイル、作業ツリーだけにあるファイルの参照は保存する。"""
    record, source, saved = _reference_record(tmp_path, monkeypatch)
    evidence = tmp_path / "evidence.txt"
    evidence.write_text(text.format(saved=saved), encoding="utf-8")
    assert _run(*_update_route(route, record, source, evidence)) == 0
    assert json.loads(record.read_text(encoding="utf-8"))["user_requirements"][0]["evidence"] == text.format(saved=saved)


def test_updates_file_accepts_model_identifier_evidence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """行位置付きのモデル識別子は追跡ファイルの名前・拡張子と一致せず、`--updates-file`の保存で参照として拒否しない。"""
    record, source, _ = _reference_record(tmp_path, monkeypatch)
    evidence = tmp_path / "evidence.txt"
    text = "amazon.nova-lite-v1:0の料金はdocs/tracked.md:2 の表で確認した"
    evidence.write_text(text, encoding="utf-8")
    assert _run(*_update_route("updates-file", record, source, evidence)) == 0
    assert json.loads(record.read_text(encoding="utf-8"))["user_requirements"][0]["evidence"] == text


@pytest.mark.parametrize("cwd_kind", ["outside", "other"])
@pytest.mark.parametrize("mode", ["plan-wi", "plan-user", "mixed"])
def test_public_generation_uses_plan_repository_from_other_cwd(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, cwd_kind: str, mode: str
) -> None:
    """対象メタ情報を持つ入力は周囲のGitやWI所属と混同しない。"""
    _wi(monkeypatch, tmp_path)
    target = git_repository.init_repository(tmp_path / "target")
    other = git_repository.init_repository(tmp_path / "other") if cwd_kind == "other" else tmp_path
    resolved = []

    def resolve(path: str | pathlib.Path, **_kwargs: object) -> str:
        resolved.append(pathlib.Path(path))
        return "example" if pathlib.Path(path) == target else "wrong-repository"

    monkeypatch.setattr(repo, "resolve_repo_id", resolve)
    plan = _plan(tmp_path, "plan-user" if mode == "mixed" else mode)
    plan.write_text(
        f"## 概要\n\n### 計画メタ情報\n\n- 対象リポジトリ: `{target}`\n\n" + plan.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    record = tmp_path / "pending.json"
    monkeypatch.chdir(other)
    args = ["--output", str(record), "--plan", str(plan), "--list"]
    if mode == "mixed":
        args.extend(["--wi", WI])
    assert _run(*args) == 0
    data = json.loads(record.read_text(encoding="utf-8"))
    assert len(data["wi_conditions"]) == (2 if mode in {"plan-wi", "mixed"} else 0)
    assert len(data["user_requirements"]) == (0 if mode == "plan-wi" else 2)
    assert bool(resolved) == (mode != "plan-user")
    assert all(path == target for path in resolved)


@pytest.mark.parametrize("invalid", ["different", "ambiguous", "duplicate-section", "not-git", "empty", "judged"])
def test_public_generation_rejects_ambiguous_repository_without_write(tmp_path: pathlib.Path, invalid: str) -> None:
    """対象不成立と判定済みを一覧生成でも拒否し、保存済み根拠を保持する。"""
    target = git_repository.init_repository(tmp_path / "target")
    plan = _plan(tmp_path, "plan-user")
    prefix = f"## 概要\n\n### 計画メタ情報\n\n- 対象リポジトリ: `{target}`\n"
    plan.write_text(prefix + "\n" + plan.read_text(encoding="utf-8"), encoding="utf-8")
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(plan), "--list") == 0
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["user_requirements"][0]["evidence"] = "既存根拠"
    if invalid == "judged":
        payload["user_requirements"][0]["outcome"] = "達成"
    _json(record, payload)
    before = record.read_bytes()
    args = ["--output", str(record), "--plan", str(plan), "--list"]
    current = plan.read_text(encoding="utf-8")
    if invalid == "different":
        other = git_repository.init_repository(tmp_path / "other")
        second = tmp_path / "other-plan.md"
        second.write_text(current.replace(str(target), str(other)), encoding="utf-8")
        args.extend(["--plan", str(second)])
    elif invalid == "ambiguous":
        plan.write_text(current.replace(prefix, prefix + "- 対象リポジトリ: `/別対象`\n"), encoding="utf-8")
    elif invalid == "duplicate-section":
        plan.write_text(current.replace(prefix, prefix + "\n### 計画メタ情報\n\n"), encoding="utf-8")
    elif invalid in {"not-git", "empty"}:
        plan.write_text(current.replace(f"`{target}`", f"`{tmp_path}`" if invalid == "not-git" else "``"), encoding="utf-8")
    assert _run(*args) == 1
    assert record.read_bytes() == before


@pytest.mark.parametrize("operation", ["list", "update"])
def test_public_missing_record_guides_generation(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], operation: str
) -> None:
    """失敗診断が示す生成入力付きの操作を実行すると、保存した行から根拠の更新へ進める。"""
    record = tmp_path / "pending output.json"
    explanation = tmp_path / "evidence.txt"
    explanation.write_text("観測した保存結果", encoding="utf-8")
    args = (
        ["--list"]
        if operation == "list"
        else [
            "--section",
            "user_requirements",
            "--row",
            "1",
            "--source",
            "未取得",
            "--evidence-file",
            str(explanation),
            "--mode",
            "replace",
        ]
    )
    assert _run("--output", str(record), *args) == 1
    diagnostic = capsys.readouterr().err
    command = diagnostic.split("実行する: ", 1)[1].split("。WI直渡し", 1)[0]
    argv = shlex.split(command)
    assert argv[:4] == ["atk", "run-script", "verification-record", "--"]
    argv[argv.index("--plan") + 1] = str(_plan(tmp_path, "plan-user"))
    assert _run(*argv[4:]) == 0
    row = json.loads(capsys.readouterr().out.splitlines()[0])
    assert (
        _run(
            "--output",
            str(record),
            "--section",
            row["section"],
            "--row",
            str(row["row"]),
            "--source",
            row["source"],
            "--evidence-file",
            str(explanation),
            "--mode",
            "replace",
        )
        == 0
    )
    assert json.loads(record.read_text())["user_requirements"][0]["evidence"] == "観測した保存結果"


def _saved_diagnostics(tmp_path: pathlib.Path, diagnostics: list[dict], sources: dict[str, str], name: str) -> dict:
    """pyfltrの公開JSONL形式を有限コマンドで保存し、実行版と保存本文を付ける。"""
    producer = tmp_path / f"{name}_producer.py"
    producer.write_text("print(" + repr("\n".join(json.dumps(entry) for entry in diagnostics)) + ")\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_toolkit.atk",
            "run-command",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "30",
            "--",
            sys.executable,
            str(producer),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    saved = json.loads(result.stdout[result.stdout.index("{") :])
    paths = {}
    for index, (filename, content) in enumerate(sources.items()):
        snapshot = tmp_path / f"{name}_{index}.txt"
        snapshot.write_text(content, encoding="utf-8")
        paths[filename] = str(snapshot)
    return {
        "path": saved["stdout_path"],
        "run_record": saved["record_path"],
        "head": saved["git_head"],
        "conditions": {"scope": ["対象"], "options": [], "dependencies": {}, "parallelism": 1, "environment": {"OS": "検証"}},
        "sources": paths,
    }


def _saved_command(tmp_path: pathlib.Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """有限の公開実行でproducerの出力と実行記録を保存する。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_toolkit.atk",
            "run-command",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "60",
            "--",
            sys.executable,
            "-m",
            *argv,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def _saved_pyfltr(tmp_path: pathlib.Path) -> dict:
    """実pyfltr runの全JSONLを公開実行記録とともに保存する。"""
    if not (tmp_path / ".git").is_dir():
        git_repository.init_repository(tmp_path, files={"base.txt": "基準\n"}, commit_message="基準")
    ruff = pathlib.Path(sys.executable).parent / "ruff"
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pyfltr]\nruff-format-by-check = false\nruff-check-path = " + json.dumps(str(ruff)) + "\n",
        encoding="utf-8",
    )
    text = "print(undefined_value)\nprint(undefined_value)\n"
    (tmp_path / "bad.py").write_text(text, encoding="utf-8")
    result = _saved_command(
        tmp_path,
        "pyfltr",
        "run",
        "--commands=ruff-check",
        "--enable=ruff-check",
        "--no-fix",
        "--no-quiet",
        "--no-archive",
        "--no-cache",
        "--output-format=jsonl",
        "--ruff-check-args=--select=F821",
        "bad.py",
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    saved = json.loads(result.stdout[result.stdout.index("{") :])
    snapshot = tmp_path / "bad-source.txt"
    snapshot.write_text(text, encoding="utf-8")
    return {
        "path": saved["stdout_path"],
        "run_record": saved["record_path"],
        "head": saved["git_head"],
        "conditions": {
            "scope": ["bad.py"],
            "options": ["--select=F821"],
            "dependencies": {},
            "parallelism": 1,
            "environment": {},
        },
        "sources": {"bad.py": str(snapshot)},
    }


@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
def test_public_run_diagnostics_roundtrip(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    """実producerの付帯レコードを保ったまま両コマンドで選択・記入し、不正なら保存しない。"""
    spec = _saved_pyfltr(tmp_path)
    original = pathlib.Path(spec["path"]).read_text(encoding="utf-8")
    entries_in_run = [json.loads(line) for line in original.splitlines()]
    assert {"header", "command", "diagnostic", "summary"} <= {entry["kind"] for entry in entries_in_run}
    conditions = _json(tmp_path / "conditions.json", spec["conditions"])
    result_args = [
        "--diagnostics-file",
        spec["path"],
        "--diagnostics-record",
        spec["run_record"],
        "--diagnostics-conditions",
        str(conditions),
        *[part for name, snapshot in spec["sources"].items() for part in ("--diagnostics-source", name, snapshot)],
    ]
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user")), "--list") == 0
    output = tmp_path / "review.json" if name == "exec-review-evidence-check" else record
    if output != record:
        output.write_bytes(record.read_bytes())
    capsys.readouterr()
    listing = ["--output", str(record)] if name == "verification-record" else []
    assert _run(*listing, *result_args, "--list-results", "--results-summary", name=name) == 0
    summary = json.loads(capsys.readouterr().out)
    assert len(summary["diagnostics"]) == 1
    group = summary["diagnostics"][0]
    assert group["count"] == 2 and group["rule"] == "F821"
    selected = group["diagnostics"][1]
    assert _run(*listing, *result_args, "--list-results", "--result-diagnostic", selected, name=name) == 0
    detail = json.loads(capsys.readouterr().out)["diagnostics"]
    assert list(detail) == [selected]
    observed = detail[selected]["after"]
    source_entry = entries_in_run[observed["entry"] - 1]
    assert source_entry["kind"] == "diagnostic"
    assert source_entry["messages"][observed["message_position"] - 1]["msg"] == group["message"]
    update_args = ["--select-row", "user_requirements:1", "--result-diagnostic", selected, "--mode", "replace"]
    if output != record:
        update_args.extend(["--row-outcome", "user_requirements:1", "達成", "--reviewed-head", spec["head"]])
    args = ["--output", str(output)] if output == record else [str(output), "--output", str(output)]
    assert _run(*args, *result_args, *update_args, name=name) == 0
    final = json.loads(output.read_text())["user_requirements"][0]
    assert f"after.entry: `{observed['entry']}`" in final["evidence"]
    assert final["outcome"] == ("達成" if output != record else "")
    assert final["reviewed_head"] == (spec["head"] if output != record else "")
    before = output.read_bytes()
    for invalid in [original + '{"kind":"unknown"}\n', original.replace('"messages":[', '"messages":null,"discarded":[')]:
        invalid_path = tmp_path / "invalid.jsonl"
        invalid_path.write_text(invalid, encoding="utf-8")
        invalid_args = [*result_args]
        invalid_args[invalid_args.index("--diagnostics-file") + 1] = str(invalid_path)
        assert _run(*args, *invalid_args, *update_args, name=name) == 1
        assert output.read_bytes() == before


@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
def test_public_results_summary_and_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """状態・基準差の識別子から必要な詳細だけを読み、両コマンドで選択結果を記入する。"""
    monkeypatch.chdir(tmp_path)
    baseline = _saved_trial(tmp_path, "def test_old():\n    assert True\ndef test_changed():\n    assert True\n", "baseline")
    current = _saved_trial(
        tmp_path,
        "import pytest\ndef test_passed():\n    assert True\ndef test_changed():\n    assert False\n"
        '@pytest.fixture\ndef broken():\n    raise RuntimeError("条件")\ndef test_error(broken):\n    pass\n'
        '@pytest.mark.skip(reason="条件")\ndef test_skipped():\n    pass\n',
        "current",
    )
    results = _json(tmp_path / "results.json", {"junit": current, "baseline_junit": baseline})
    record = tmp_path / "pending.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    listing = ["--output", str(record)] if name == "verification-record" else []
    capsys.readouterr()
    assert _run(*listing, "--results-file", str(results), "--list-results", "--results-summary", name=name) == 0
    summary = json.loads(capsys.readouterr().out)
    assert {item["status"]: item["count"] for item in summary["tests"]} == {"passed": 1, "failed": 1, "error": 1, "skipped": 1}
    assert {item["change"] for item in summary["test_changes"]} == {"added", "deleted", "changed"}
    changed = next(item for item in summary["test_changes"] if item["change"] == "changed")
    assert (changed["before_status"], changed["after_status"]) == ("passed", "failed")
    selected = next(item["tests"][0] for item in summary["tests"] if item["status"] == "passed")
    assert _run(*listing, "--results-file", str(results), "--list-results", "--result-test", selected, name=name) == 0
    detail = json.loads(capsys.readouterr().out)
    assert list(detail["tests"]) == [selected]
    assert detail["tests"][selected]["xml"] == current["xml"]
    assert (
        _run(*listing, "--results-file", str(results), "--list-results", "--result-test", "trial_test.py::test_old", name=name)
        == 0
    )
    deleted = json.loads(capsys.readouterr().out)
    assert not deleted["tests"] and deleted["test_changes"][0]["before"]["xml"] == baseline["xml"]
    output = tmp_path / "review.json" if name == "exec-review-evidence-check" else record
    if output != record:
        output.write_bytes(record.read_bytes())
    row = json.loads(record.read_text())["user_requirements"][0]
    update = {"section": "user_requirements", "row": 1, "source": row["source"], "tests": [selected], "mode": "replace"}
    if output != record:
        update.update(outcome="達成", reviewed_head=_git(tmp_path, "rev-parse", "HEAD"))
    updates = _json(tmp_path / "updates.json", [update])
    args = ["--output", str(output)] if output == record else [str(output), "--output", str(output)]
    assert _run(*args, "--results-file", str(results), "--updates-file", str(updates), name=name) == 0
    assert f"`{selected}` passed" in json.loads(output.read_text())["user_requirements"][0]["evidence"]
    before = output.read_bytes()
    assert _run(*listing, "--results-file", str(results), "--list-results", "--result-test", "missing", name=name) == 1
    assert output.read_bytes() == before


def test_public_diagnostics_comparison_distinguishes_movement_multiplicity_and_incomparability(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gitの改名と保存本文の行移動、多重集合と位置なし警告を公開結果一覧で対応付ける。"""
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "試験担当")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    old_text = "\n".join(f"value_{number} = {number}" for number in range(20)) + "\n"
    (tmp_path / "old.py").write_text(old_text, encoding="utf-8")
    _git(tmp_path, "add", "old.py")
    _git(tmp_path, "commit", "--no-verify", "-m", "基準")

    def message(text: str, line: int | None) -> dict:
        return {"line": line, "col": 1 if line else None, "severity": "warning", "rule": "W001", "msg": text}

    before = _saved_diagnostics(
        tmp_path,
        [
            {"kind": "command", "command": "lint", "status": "succeeded"},
            {
                "kind": "diagnostic",
                "command": "lint",
                "file": "old.py",
                "messages": [message("同じ診断", 2), message("同じ診断", 2), message("旧内容", 3)],
            },
            {"kind": "diagnostic", "command": "lint", "file": None, "messages": [message(f"PID=123 cwd={tmp_path}", None)]},
        ],
        {"old.py": old_text},
        "before",
    )
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    new_text = "# 追加行\n" + old_text
    (tmp_path / "new.py").write_text(new_text, encoding="utf-8")
    _git(tmp_path, "add", "--", "old.py", "new.py")
    _git(tmp_path, "commit", "--no-verify", "-m", "改名と行移動")
    after = _saved_diagnostics(
        tmp_path,
        [
            {
                "kind": "diagnostic",
                "command": "lint",
                "file": "new.py",
                "messages": [message("同じ診断", 3)] * 3 + [message("新内容", 4)],
            },
            {"kind": "diagnostic", "command": "lint", "file": None, "messages": [message(f"PID=456 cwd={tmp_path}", None)]},
        ],
        {"new.py": new_text},
        "after",
    )
    results = _json(tmp_path / "results.json", {"diagnostics": after, "baseline_diagnostics": before})
    output = tmp_path / "record.json"
    assert _run("--output", str(output), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    capsys.readouterr()
    assert _run("--output", str(output), "--results-file", str(results), "--list-results") == 0
    listed = json.loads(capsys.readouterr().out)["diagnostics"]
    assert len([key for key in listed if key.startswith("unchanged:")]) == 3
    assert len([key for key in listed if key.startswith("added:")]) == 2
    assert len([key for key in listed if key.startswith("deleted:")]) == 1
    assert _run("--output", str(output), "--results-file", str(results), "--list-results", "--results-summary") == 0
    groups = json.loads(capsys.readouterr().out)["diagnostics"]
    assert {identity for group in groups for identity in group["diagnostics"]} == listed.keys()
    assert all(group["count"] == len(group["diagnostics"]) for group in groups)
    assert any(group["change"] == "unchanged" and group["count"] == 2 for group in groups)
    payload = json.loads(output.read_text(encoding="utf-8"))
    updates = _json(
        tmp_path / "updates.json",
        [
            {
                "section": "user_requirements",
                "row": 1,
                "source": payload["user_requirements"][0]["source"],
                "diagnostics": ["added:1"],
                "mode": "replace",
            }
        ],
    )
    assert _run("--output", str(output), "--results-file", str(results), "--updates-file", str(updates)) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))["user_requirements"][0]["evidence"]
    assert f"after.path: `{after['path']}`" in evidence
    capsys.readouterr()
    for change in ("sources", "conditions", "head"):
        current = (
            {**after, "sources": {}}
            if change == "sources"
            else {**after, "conditions": {**after["conditions"], "parallelism": 2}}
            if change == "conditions"
            else {**after, "head": "a" * 40}
        )
        _json(results, {"diagnostics": current, "baseline_diagnostics": before})
        if change == "head":
            assert _run("--output", str(output), "--results-file", str(results), "--list-results") == 1
            assert "次の操作:" in capsys.readouterr().err
        else:
            assert _run("--output", str(output), "--results-file", str(results), "--list-results") == 0
            compared = json.loads(capsys.readouterr().out)["diagnostics"]
            assert any(key.startswith("incomparable:") for key in compared)
            assert not any(
                item.get("after", {}).get("file") == "new.py" for key, item in compared.items() if key.startswith("unchanged:")
            )
            assert _run("--output", str(output), "--results-file", str(results), "--list-results", "--results-summary") == 0
            groups = json.loads(capsys.readouterr().out)["diagnostics"]
            assert {identity for group in groups for identity in group["diagnostics"]} == compared.keys()
            assert any(group["change"] == "incomparable" for group in groups)


def test_diagnostics_from_subproject_follow_renames(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """サブプロジェクトを作業ディレクトリとした診断を、worktree起点の改名と保存時本文へ対応付ける。"""
    text = "\n".join(f"value_{number} = {number}" for number in range(5)) + "\n"
    repository = git_repository.init_repository(tmp_path / "repo", files={"sub/old.py": text}, commit_message="基準")
    subproject = repository / "sub"

    def entry(file: str) -> dict:
        message = {"line": 2, "col": 1, "severity": "warning", "rule": "W001", "msg": "同じ診断"}
        return {"kind": "diagnostic", "command": "lint", "file": file, "messages": [message]}

    before = _saved_diagnostics(subproject, [entry("old.py")], {"sub/old.py": text}, "before")
    _git(repository, "mv", "sub/old.py", "sub/new.py")
    _git(repository, "-c", "user.name=試験担当", "-c", "user.email=test@example.invalid", "commit", "--no-verify", "-m", "改名")
    # 絶対パスの診断fileも、相対パスと同じworktree起点の名前へそろう。
    after = _saved_diagnostics(subproject, [entry(str(subproject / "new.py"))], {"sub/new.py": text}, "after")
    results = _json(tmp_path / "results.json", {"diagnostics": after, "baseline_diagnostics": before})
    output = tmp_path / "record.json"
    assert _run("--output", str(output), "--plan", str(_plan(repository, "plan-user"))) == 0
    capsys.readouterr()
    assert _run("--output", str(output), "--results-file", str(results), "--list-results") == 0, capsys.readouterr().err
    listed = json.loads(capsys.readouterr().out)["diagnostics"]
    assert list(listed) == ["unchanged:1"]
    assert (listed["unchanged:1"]["before"]["file"], listed["unchanged:1"]["after"]["file"]) == ("sub/old.py", "sub/new.py")


@pytest.mark.parametrize("invalid", ["missing-history", "missing-fence", "empty-utterance"])
def test_public_record_rejects_invalid_plan_without_write(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """逐語発言を取得できない計画では既存の検証記録を書き換えない。"""
    record = tmp_path / "record.json"
    plan = _plan(tmp_path, "plan-user")
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    before = record.read_bytes()
    text = plan.read_text(encoding="utf-8")
    if invalid == "missing-history":
        text = text.split("## 変更履歴", 1)[0]
    elif invalid == "missing-fence":
        text = text.replace("```text\n", "").replace("```\n", "")
    else:
        text = text.replace("保存する。\n", "")
    plan.write_text(text, encoding="utf-8")
    assert _run("--output", str(record), "--plan", str(plan)) == 1
    assert record.read_bytes() == before
    assert "次の操作:" in capsys.readouterr().err


def test_public_record_ignores_history_heading_inside_fence(tmp_path: pathlib.Path) -> None:
    """例示の変更履歴を原文出所にせず、実際の逐語発言だけを抽出する。"""
    record = tmp_path / "record.json"
    plan = _plan(tmp_path, "plan-user")
    original = plan.read_text(encoding="utf-8")
    plan.write_text(
        "````text\n## 変更履歴\n### ユーザー発言9\n```text\n例示だけ。\n```\n````\n" + original,
        encoding="utf-8",
    )
    assert _run("--output", str(record), "--plan", str(plan)) == 0
    rows = json.loads(record.read_text(encoding="utf-8"))["user_requirements"]
    assert [row["requirement"] for row in rows] == ["保存する。", "保存する。"]
    assert all("ユーザー発言9" not in row["source"] for row in rows)


def _json(path: pathlib.Path, value: object) -> pathlib.Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _runner_fixture(tmp_path: pathlib.Path, runner: str, *, baseline: bool = False) -> dict[str, str]:
    """WIの実物観測（vitest 5.0.3・Playwright 1.63.0・VSTest TRX）の欄を再現する。"""
    label = "baseline" if baseline else "current"
    path = tmp_path / f"{runner}-{label}.json"
    filename = tmp_path / "trial.spec.js"
    filename.write_text("// 結果の生成元\n", encoding="utf-8")
    git_repository.run_git(tmp_path, "add", "--", filename.name)
    git_repository.run_git(tmp_path, "commit", "--allow-empty", "--no-verify", "-m", "試験の生成元")
    names = ["PASSED", "changed", "skipped", "old" if baseline else "new"]
    statuses = ["passed", "passed" if baseline else "failed", "skipped", "passed"]
    if runner == "vitest":
        _json(
            path,
            {
                "testResults": [
                    {
                        "name": str(filename),
                        "assertionResults": [
                            {"ancestorTitles": ["suite"], "title": name, "status": status}
                            for name, status in zip(names, statuses, strict=True)
                        ]
                        + [{"ancestorTitles": ["other"], "title": "PASSED", "status": "passed"}],
                    }
                ]
            },
        )
        argv = ["vitest", "run", "--reporter=json", f"--outputFile={path}"]
    elif runner == "playwright":
        _json(
            path,
            {
                "config": {"rootDir": str(tmp_path)},
                "errors": [],
                "suites": [
                    {
                        "title": filename.name,
                        "suites": [
                            {
                                "title": "suite",
                                "specs": [
                                    {
                                        "title": name,
                                        "file": filename.name,
                                        "tests": [
                                            {
                                                "projectName": project,
                                                "status": {"passed": "expected", "failed": "unexpected", "skipped": "skipped"}[
                                                    status
                                                ],
                                            }
                                            for project in ("chromium", "firefox")
                                        ],
                                    }
                                    for name, status in zip(names, statuses, strict=True)
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        argv = ["env", f"PLAYWRIGHT_JSON_OUTPUT_NAME={path}", "playwright", "test", "--reporter=json"]
    else:
        root = ET.Element("TestRun", xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010")
        results = ET.SubElement(root, "Results")
        for name, status in zip(names, statuses, strict=True):
            for value in ("PASSED", "FAILED"):
                ET.SubElement(
                    results,
                    "UnitTestResult",
                    testName=f'T.C.{name}(v: "{value}")',
                    outcome={"passed": "Passed", "failed": "Failed", "skipped": "NotExecuted"}[status],
                )
        ET.ElementTree(root).write(path, encoding="utf-8")
        argv = ["dotnet", "test", "--logger", f"trx;LogFileName={path}"]
    stdout, stderr = tmp_path / "stdout.txt", tmp_path / "stderr.txt"
    stdout.write_text("保存出力\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    record = _json(
        tmp_path / f"{runner}-{label}-record.json",
        {
            "cwd": str(tmp_path),
            "argv": argv,
            "git_head": _git(tmp_path, "rev-parse", "HEAD"),
            "git_status": [],
            "child_exit_code": 0 if baseline else 1,
            "timed_out": False,
            "signal": None,
            "stdout_path": str(stdout),
            "stderr_path": str(stderr),
        },
    )
    return {"format": runner, "path": str(path), "run_record": str(record)}


@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
@pytest.mark.parametrize("runner", ["vitest", "playwright", "trx"])
def test_public_structured_runner_results_list_compare_select_and_reject(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str, runner: str
) -> None:
    """全形式の識別・状態・版・比較・選択保存と、不正入力での全記録保持を両公開操作で確かめる。"""
    git_repository.init_repository(tmp_path, commit_message="試験の基準")
    monkeypatch.chdir(tmp_path)
    before, after = _runner_fixture(tmp_path, runner, baseline=True), _runner_fixture(tmp_path, runner)
    output = tmp_path / "pending.json"
    assert _run("--output", str(output), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    listing = ["--output", str(output)] if name == "verification-record" else []
    result_args = ["--test-format", runner, "--test-file", after["path"], "--test-record", after["run_record"]]
    baseline_args = [
        "--baseline-test-format",
        runner,
        "--baseline-test-file",
        before["path"],
        "--baseline-test-record",
        before["run_record"],
    ]
    capsys.readouterr()
    assert _run(*listing, *result_args, *baseline_args, "--list-results", name=name) == 0
    results = json.loads(capsys.readouterr().out)
    head = _git(tmp_path, "rev-parse", "HEAD")
    assert len(results["tests"]) == (5 if runner == "vitest" else 8)
    assert {item["status"] for item in results["tests"].values()} == {"passed", "failed", "skipped"}
    assert {item["change"] for item in results["test_changes"]} == {"added", "deleted", "changed"}
    passed = [key for key, item in results["tests"].items() if "PASSED" in key and item["status"] == "passed"]
    assert passed
    for item in results["tests"].values():
        assert item["path"] == after["path"] and item["git_head"] == head and item["child_exit_code"] == 1
    if runner == "playwright":
        assert 'trial.spec.js::suite::PASSED::"chromium"' in results["tests"]
        assert 'trial.spec.js::suite::PASSED::"firefox"' in results["tests"]
    if runner == "vitest":
        assert "trial.spec.js::suite::PASSED" in results["tests"]
        assert "trial.spec.js::other::PASSED" in results["tests"]
    if runner == "trx":
        assert 'T.C.PASSED(v: "PASSED")' in results["tests"]
        assert 'T.C.PASSED(v: "FAILED")' in results["tests"]
    update_args = [
        "--output",
        str(output),
        *result_args,
        "--select-row",
        "user_requirements:2",
        "--result-test",
        passed[0],
        "--mode",
        "replace",
    ]
    if name == "exec-review-evidence-check":
        update_args = [str(output), *update_args, "--row-outcome", "user_requirements:2", "達成", "--reviewed-head", head]
    assert _run(*update_args, name=name) == 0, capsys.readouterr().err
    rows = json.loads(output.read_text(encoding="utf-8"))["user_requirements"]
    assert not rows[0]["evidence"] and f"`{passed[0]}` passed" in rows[1]["evidence"]
    assert after["path"] in rows[1]["evidence"]
    unchanged = output.read_bytes()
    saved_text = pathlib.Path(after["path"]).read_text(encoding="utf-8")
    for invalid in ("corrupt", "duplicate", "unknown-state", "argv", "exit", "unknown"):
        record = json.loads(pathlib.Path(after["run_record"]).read_text(encoding="utf-8"))
        original_record = dict(record)
        args = list(update_args)
        if invalid == "corrupt":
            pathlib.Path(after["path"]).write_text("<", encoding="utf-8")
        elif invalid == "duplicate":
            if runner == "trx":
                root = ET.fromstring(saved_text)
                result = next(e for e in root if e.tag.endswith("Results"))
                result.append(ET.fromstring(ET.tostring(result[0])))
                ET.ElementTree(root).write(after["path"], encoding="utf-8")
            else:
                data = json.loads(saved_text)
                if runner == "vitest":
                    data["testResults"] *= 2
                else:
                    data["suites"] *= 2
                _json(pathlib.Path(after["path"]), data)
        elif invalid == "argv":
            record["argv"] = ["unrelated"]
        elif invalid == "unknown-state":
            if runner == "trx":
                root = ET.fromstring(saved_text)
                next(e for e in root.iter() if e.tag.endswith("UnitTestResult")).set("outcome", "Unknown")
                ET.ElementTree(root).write(after["path"], encoding="utf-8")
            else:
                data = json.loads(saved_text)
                if runner == "vitest":
                    data["testResults"][0]["assertionResults"][0]["status"] = "unknown"
                else:
                    data["suites"][0]["suites"][0]["specs"][0]["tests"][0]["status"] = "unknown"
                _json(pathlib.Path(after["path"]), data)
        elif invalid == "exit":
            record["child_exit_code"] = 0
        else:
            spec = _json(tmp_path / "unknown.json", {"tests": {**after, "format": "unknown"}})
            start = args.index("--test-format")
            args[start : start + 6] = ["--results-file", str(spec)]
        _json(pathlib.Path(after["run_record"]), record)
        assert _run(*args, name=name) == 1
        assert output.read_bytes() == unchanged
        assert "次の操作:" in capsys.readouterr().err
        pathlib.Path(after["path"]).write_text(saved_text, encoding="utf-8")
        _json(pathlib.Path(after["run_record"]), original_record)


@pytest.mark.parametrize("runner", ["vitest", "playwright"])
@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
def test_public_runner_identity_uses_worktree_from_nested_cwd(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runner: str, name: str
) -> None:
    """サブディレクトリ起動でも記録cwdのGit境界からworktree相対名を使う。"""
    git_repository.init_repository(tmp_path, commit_message="取得時の基準")
    app = tmp_path / "app"
    app.mkdir()
    spec = _runner_fixture(app, runner)
    args = ["--output", str(tmp_path / "unused.json")] if name == "verification-record" else []
    capsys.readouterr()
    assert (
        _run(
            *args,
            "--test-format",
            runner,
            "--test-file",
            spec["path"],
            "--test-record",
            spec["run_record"],
            "--list-results",
            name=name,
        )
        == 0
    )
    observed = json.loads(capsys.readouterr().out)["tests"]
    assert all(identity.startswith("app/trial.spec.js::") for identity in observed)


@pytest.mark.parametrize("name", ["verification-record", "exec-review-evidence-check"])
def test_public_selected_row_imports_saved_results_without_transcribing_source(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """保存XMLを直接指定し、同文別出所の一方だけを更新して不正行の部分保存を拒否する。"""
    monkeypatch.chdir(tmp_path)
    trial = _saved_trial(tmp_path, "def test_saved():\n    assert True\n", "selected")
    pending = tmp_path / "pending.json"
    assert _run("--output", str(pending), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    original = json.loads(pending.read_text(encoding="utf-8"))
    review = name == "exec-review-evidence-check"
    output = tmp_path / "review.json" if review else pending
    if review:
        output.write_bytes(pending.read_bytes())
    args = [str(output), "--output", str(output)] if review else ["--output", str(output)]
    result_args = ["--junit-xml", trial["xml"], "--junit-record", trial["run_record"]]
    capsys.readouterr()
    listing = [] if review else ["--output", str(pending)]
    assert _run(*listing, *result_args, "--list-results", "--results-summary", name=name) == 0
    summary = json.loads(capsys.readouterr().out)
    test_name = summary["tests"][0]["tests"][0]
    evidence = tmp_path / "explanation.txt"
    evidence.write_text("公開呼出しで保存結果を観測した。", encoding="utf-8")
    update_args = [
        *result_args,
        "--select-row",
        "user_requirements:2",
        "--result-test",
        test_name,
        "--evidence-file",
        str(evidence),
        "--mode",
        "replace",
    ]
    judgment = (
        ["--row-outcome", "user_requirements:2", "達成", "--reviewed-head", _git(tmp_path, "rev-parse", "HEAD")]
        if review
        else []
    )
    assert _run(*args, *update_args, *judgment, name=name) == 0
    updated = json.loads(output.read_text(encoding="utf-8"))
    assert updated["user_requirements"][0] == original["user_requirements"][0]
    chosen = updated["user_requirements"][1]
    assert chosen["source"] == original["user_requirements"][1]["source"]
    assert f"`{test_name}` passed" in chosen["evidence"]
    assert evidence.read_text(encoding="utf-8") in chosen["evidence"]
    assert chosen["outcome"] == ("達成" if review else "")
    assert bool(chosen["reviewed_head"]) == review
    if review:
        before = output.read_bytes()
        source_args = ["--row-source", "user_requirements:2", str(evidence)]
        assert _run(*args, *update_args, *judgment, *source_args, name=name) == 1
        assert output.read_bytes() == before
        exemption = ["--row-outcome", "user_requirements:2", "背景", "--reviewed-head", _git(tmp_path, "rev-parse", "HEAD")]
        assert _run(*args, *update_args, *exemption, *source_args, name=name) == 0
        changed = json.loads(output.read_text(encoding="utf-8"))
        assert changed["user_requirements"][1]["source"] == str(evidence)
        assert changed["user_requirements"][1]["origin"] == original["user_requirements"][1]["origin"]
        assert changed["user_requirements"][0] == original["user_requirements"][0]
    before = output.read_bytes()
    assert _run(*args, *update_args, "--select-row", "user_requirements:99", *judgment, name=name) == 1
    assert output.read_bytes() == before


def test_public_review_selected_row_inherits_only_same_requirement(tmp_path: pathlib.Path) -> None:
    """同文でも出所が異なる行の継承を拒否し、対応する未判定行からの継承だけ保存する。"""
    pending, review = tmp_path / "pending.json", tmp_path / "review.json"
    plan = _plan(tmp_path, "plan-user")
    repository = git_repository.init_repository(tmp_path / "repo", commit_message="基準")
    head = git_repository.git_output(repository, "rev-parse", "HEAD")
    assert _run("--output", str(pending), "--plan", str(plan)) == 0
    evidence = tmp_path / "observed.txt"
    evidence.write_text("保存済み観測を参照する根拠", encoding="utf-8")
    assert (
        _run(
            "--output",
            str(pending),
            "--select-row",
            "user_requirements:2",
            "--evidence-file",
            str(evidence),
            "--mode",
            "append",
        )
        == 0
    )
    assert _run("--template", str(review), "--plan", str(plan), name="exec-review-evidence-check") == 0
    args = [
        str(review),
        "--output",
        str(review),
        "--verification-record",
        str(pending),
        "--select-row",
        "user_requirements:2",
        "--mode",
        "replace",
        "--row-outcome",
        "user_requirements:2",
        "達成",
        "--reviewed-head",
        head,
    ]
    before = review.read_bytes()
    assert (
        _run(*args, "--verification-row", "user_requirements:2", "user_requirements:1", name="exec-review-evidence-check") == 1
    )
    assert review.read_bytes() == before
    assert (
        _run(*args, "--verification-row", "user_requirements:2", "user_requirements:2", name="exec-review-evidence-check") == 0
    )
    row = json.loads(review.read_text(encoding="utf-8"))["user_requirements"][1]
    assert evidence.read_text(encoding="utf-8") in row["evidence"]


def _git(path: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()


@pytest.mark.parametrize("route", ["cli", "json"])
def test_public_nonterminal_source_rejection_guides_both_inputs_to_evidence(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    """通常判定で出所変更を拒否し、CLIと更新JSONの説明ファイル指定で出所を保持して回復できる。"""
    record = tmp_path / "review.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    original = json.loads(record.read_text(encoding="utf-8"))
    explanation = tmp_path / "explanation.txt"
    explanation.write_text("延期した観測の再開条件を記録した。", encoding="utf-8")
    update = {
        "section": "user_requirements",
        "row": 1,
        "source": original["user_requirements"][0]["source"],
        "new_source": "変更する出所",
        "outcome": "証拠不足",
        "reviewed_head": "a" * 40,
        "evidence_file": str(explanation),
        "mode": "replace",
    }
    prefix = [str(record), "--output", str(record)]
    updates = tmp_path / "updates.json"
    if route == "json":
        _json(updates, [update])
        args = [*prefix, "--updates-file", str(updates)]
    else:
        args = [
            *prefix,
            "--select-row",
            "user_requirements:1",
            "--row-outcome",
            "user_requirements:1",
            "証拠不足",
            "--reviewed-head",
            "a" * 40,
            "--evidence-file",
            str(explanation),
            "--mode",
            "replace",
            "--row-source",
            "user_requirements:1",
            "変更する出所",
        ]
    before = record.read_bytes()
    capsys.readouterr()
    assert _run(*args, name="exec-review-evidence-check") == 1
    assert record.read_bytes() == before
    diagnostic = capsys.readouterr().err
    assert "new_sourceまたは--row-source" in diagnostic
    assert "更新JSONではevidence_file、CLIでは--evidence-file" in diagnostic
    if route == "json":
        update.pop("new_source")
        _json(updates, [update])
    else:
        args = args[:-3]
    assert _run(*args, name="exec-review-evidence-check") == 0, capsys.readouterr().err
    final = json.loads(record.read_text(encoding="utf-8"))
    assert final["user_requirements"][0]["source"] == original["user_requirements"][0]["source"]
    assert final["user_requirements"][0]["evidence"] == explanation.read_text(encoding="utf-8")
    assert final["user_requirements"][1] == original["user_requirements"][1]


def test_public_repeated_rounds_replace_current_evidence_and_inherit_each_requirement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """複数roundで行別の現行根拠へ置換し、同一ログの各観測と必要な旧取得版を独立判定へ引き継ぐ。"""
    repository = git_repository.init_repository(
        tmp_path / "repo", files={"app.cfg": "setting=one\n"}, commit_message="観測時の版"
    )
    old_head = _git(repository, "rev-parse", "HEAD")
    (repository / "notes.txt").write_text("説明だけを改訂した。\n", encoding="utf-8")
    git_repository.commit_all(repository, "説明の改訂")
    current_head = _git(repository, "rev-parse", "HEAD")
    assert old_head != current_head
    monkeypatch.chdir(repository)
    plan = _plan(tmp_path, "plan-user")
    plan.write_text(
        plan.read_text(encoding="utf-8").replace(
            "### ユーザー発言2\n\n```text\n保存する。", "### ユーザー発言2\n\n```text\n再読込する。"
        )
        + "\n### ユーザー発言3\n\n```text\n削除する。\n```\n",
        encoding="utf-8",
    )
    observation = tmp_path / "observations.txt"
    observations = ["保存: setting=one", "再読込: setting=one", "削除: ファイル不在"]
    observation.write_text("\n".join(observations) + "\n", encoding="utf-8")
    captured = _json(
        tmp_path / "observation-record.json",
        {"git_head": old_head, "git_status": [], "cwd": str(repository), "stdout_path": str(observation)},
    )
    history = tmp_path / "review-history.tsv"
    history.write_text("round\t応答\n1\t旧説明を訂正する\n2\t行別の観測へ整理した\n", encoding="utf-8")
    pending, judged = tmp_path / "pending.json", tmp_path / "judged.json"
    assert _run("--output", str(pending), "--plan", str(plan)) == 0
    assert _run("--template", str(judged), "--plan", str(plan), name="exec-review-evidence-check") == 0
    original = json.loads(pending.read_text(encoding="utf-8"))
    for round_number in (1, 2):
        pending_args = ["--output", str(pending), "--mode", "replace"]
        review_args = [
            str(judged),
            "--output",
            str(judged),
            "--verification-record",
            str(pending),
            "--reviewed-head",
            current_head,
            "--mode",
            "replace",
        ]
        expected = []
        for index, observed in enumerate(observations, 1):
            selector = f"user_requirements:{index}"
            text = (
                f"{observation}:{index}: {observed}。取得版HEAD={old_head}。取得記録: {captured}。"
                "app.cfgと試験入力・実行環境は取得時と現行で同一のため、この観測を適用できる。"
            )
            if round_number == 1:
                text += "失効した旧説明。別要求の修正経緯も転記した。"
            else:
                text += f"反復履歴の所在: {history}:3。"
            explanation = tmp_path / f"round{round_number}-row{index}.txt"
            explanation.write_text(text, encoding="utf-8")
            expected.append(text)
            pending_args += ["--select-row", selector, "--row-evidence-file", selector, str(explanation)]
            review_args += [
                "--select-row",
                selector,
                "--verification-row",
                selector,
                selector,
                "--row-outcome",
                selector,
                "達成",
            ]
        assert _run(*pending_args) == 0, capsys.readouterr().err
        preserved_pending = pending.read_bytes()
        assert _run(*review_args, name="exec-review-evidence-check") == 0, capsys.readouterr().err
        assert pending.read_bytes() == preserved_pending
        current = json.loads(judged.read_text(encoding="utf-8"))
        for index, (source, row) in enumerate(zip(original["user_requirements"], current["user_requirements"], strict=True)):
            assert row["requirement"] == source["requirement"] and row["source"] == source["source"]
            assert row["evidence"] == expected[index]
            assert row["outcome"] == "達成" and row["reviewed_head"] == current_head
            assert old_head in row["evidence"] and str(captured) in row["evidence"]
            if round_number == 2:
                assert "失効した旧説明" not in row["evidence"] and "別要求の修正経緯" not in row["evidence"]
                assert observations[(index + 1) % len(observations)] not in row["evidence"]
    pending_rows = json.loads(pending.read_text(encoding="utf-8"))["user_requirements"]
    assert all(not row["outcome"] and not row["reviewed_head"] for row in pending_rows)
    assert _run(str(judged), "--plan", str(plan), "--expected-head", current_head, name="exec-review-evidence-check") == 0, (
        capsys.readouterr().err
    )


def _saved_trial(tmp_path: pathlib.Path, text: str, name: str) -> dict[str, str]:
    """隔離Gitで実pytestを実行し、公開run-commandの記録とXMLを受け取る。"""
    if not (tmp_path / ".git").is_dir():
        _git(tmp_path, "init", "--quiet")
        _git(tmp_path, "config", "user.name", "試験担当")
        _git(tmp_path, "config", "user.email", "test@example.invalid")
        _git(tmp_path, "commit", "--allow-empty", "--no-verify", "-m", "基準")
    trial = tmp_path / "trial_test.py"
    trial.write_text(text, encoding="utf-8")
    # 根拠へ写す試験名のファイルを、判定記入と返却前の確認が解決する版の追跡ファイルにする。
    _git(tmp_path, "add", "trial_test.py")
    _git(tmp_path, "commit", "--allow-empty", "--no-verify", "--quiet", "-m", name)
    xml = tmp_path / f"{name}.xml"
    result = _saved_command(
        tmp_path,
        "pytest",
        "-v",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit1",
        f"--junitxml={xml}",
        str(trial),
    )
    assert result.returncode in {0, 1}, result.stderr
    saved = json.loads(result.stdout[result.stdout.index("{") :])
    assert saved["child_exit_code"] in {0, 1}
    return {"xml": str(xml), "run_record": saved["record_path"]}


def test_public_import_uses_junit_states_and_updates_selected_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """実XMLの状態・クラス・パラメーターと基準版との差を公開操作で関連付ける。"""
    monkeypatch.chdir(tmp_path)
    before = _saved_trial(tmp_path, "def test_old():\n    assert True\ndef test_changed():\n    assert True\n", "before")
    after = _saved_trial(
        tmp_path,
        'import pytest\n@pytest.mark.parametrize("value", ["PASSED", "FAILED"])\n'
        "def test_names(value):\n    assert value\n"
        "def test_changed():\n    assert False\n"
        'class TestStates:\n    @pytest.fixture\n    def broken(self):\n        raise RuntimeError("診断")\n'
        "    def test_error(self, broken):\n        pass\n"
        '    @pytest.mark.skip(reason="条件")\n    def test_skipped(self):\n        pass\n',
        "after",
    )
    results = _json(tmp_path / "results.json", {"junit": after, "baseline_junit": before})
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    capsys.readouterr()
    assert _run("--output", str(record), "--results-file", str(results), "--list-results") == 0
    listed = json.loads(capsys.readouterr().out)
    tests = listed["tests"]
    assert tests["trial_test.py::test_names[PASSED]"]["status"] == "passed"
    assert tests["trial_test.py::test_names[FAILED]"]["status"] == "passed"
    assert tests["trial_test.py::TestStates::test_error"]["status"] == "error"
    assert tests["trial_test.py::TestStates::test_skipped"]["status"] == "skipped"
    assert {item["change"] for item in listed["test_changes"]} == {"added", "deleted", "changed"}
    payload = json.loads(record.read_text(encoding="utf-8"))
    updates = _json(
        tmp_path / "updates.json",
        [
            {
                "section": "user_requirements",
                "row": index,
                "source": row["source"],
                "tests": ["trial_test.py::test_names[PASSED]"],
                "mode": "replace",
            }
            for index, row in enumerate(payload["user_requirements"], 1)
        ],
    )
    assert _run("--output", str(record), "--results-file", str(results), "--updates-file", str(updates)) == 0
    final = json.loads(record.read_text(encoding="utf-8"))
    for row in final["user_requirements"]:
        assert not row["outcome"] and not row["reviewed_head"]
        assert f"xml: `{after['xml']}`" in row["evidence"]
        assert "`trial_test.py::test_names[PASSED]` passed" in row["evidence"]


def test_public_import_roundtrip_reaches_return_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """実pytestの根拠を未判定記録へ取り込み、同じ証拠へのレビュー更新と返却生成まで通す。"""
    _wi(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    text = "def test_save_and_reload():\n    assert {'saved': 1}.get('saved') == 1\n"
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "試験担当")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "trial_test.py").write_text(text, encoding="utf-8")
    plan = _plan(tmp_path, "plan-user")
    _git(tmp_path, "add", "--", "trial_test.py", plan.name)
    _git(tmp_path, "commit", "--no-verify", "-m", "受入試験")
    head = _git(tmp_path, "rev-parse", "HEAD")
    spec = _saved_trial(tmp_path, text, "acceptance")
    results = _json(tmp_path / "results.json", {"junit": spec})
    pending, evidence = tmp_path / "pending.json", tmp_path / "evidence.json"
    assert _run("--output", str(pending), "--plan", str(plan)) == 0
    payload = json.loads(pending.read_text(encoding="utf-8"))
    updates = [
        {
            "section": "user_requirements",
            "row": index,
            "source": row["source"],
            "tests": ["trial_test.py::test_save_and_reload"],
            "mode": "replace",
        }
        for index, row in enumerate(payload["user_requirements"], 1)
    ]
    updates_path = _json(tmp_path / "updates.json", updates)
    assert _run("--output", str(pending), "--results-file", str(results), "--updates-file", str(updates_path)) == 0
    pending_bytes = pending.read_bytes()
    assert _run("--template", str(evidence), "--plan", str(plan), name="exec-review-evidence-check") == 0
    for update in updates:
        update.pop("tests")
        update.update(outcome="達成", reviewed_head=head, verification_source=update["source"])
    _json(updates_path, updates)
    assert (
        _run(
            str(evidence),
            "--verification-record",
            str(pending),
            "--updates-file",
            str(updates_path),
            "--output",
            str(evidence),
            name="exec-review-evidence-check",
        )
        == 0
    )
    assert pending.read_bytes() == pending_bytes
    table = tmp_path / "計画.exec-review.tsv"
    review_table.init(table)
    capsys.readouterr()
    assert (
        _run(
            str(evidence),
            "--plan",
            str(plan),
            "--expected-head",
            head,
            "--review-table",
            str(table),
            "--round",
            "1",
            "--review-start",
            head,
            "--return-result",
            "--reader-fit-review",
            "文章成果物なし",
            name="exec-review-evidence-check",
        )
        == 0
    ), capsys.readouterr().err
    returned = capsys.readouterr().out
    assert "状態: completed" in returned and "未解決の指摘数: 0" in returned
    assert "読者別探索: 文章成果物なし" in returned


def _saved_pyfltr_trial(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, name: str, *options: str
) -> tuple[dict[str, str], subprocess.CompletedProcess[str]]:
    """実pyfltrでpytestを起動し、公開run-commandの記録とXMLを受け取る。

    `PYFLTR_CACHE_DIR`でpyfltrの実行記録をテストの一時領域へ書き、テスト外のキャッシュに依存しない。
    """
    monkeypatch.setenv("PYFLTR_CACHE_DIR", str(tmp_path / "pyfltr-cache"))
    pytest_path = pathlib.Path(sys.executable).parent / "pytest"
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pyfltr]\npytest = true\npytest-path = " + json.dumps(str(pytest_path)) + "\n", encoding="utf-8"
    )
    xml = tmp_path / f"{name}.xml"
    result = _saved_command(
        tmp_path,
        "pyfltr",
        "run-for-agent",
        "--commands=pytest",
        *options,
        f"--pytest-args=-v -p no:cacheprovider -o junit_family=xunit1 --junitxml={xml}",
        "trial_test.py",
    )
    assert result.returncode in {0, 1}, result.stderr
    saved = json.loads(result.stdout[result.stdout.index("{") :])
    return {"xml": str(xml), "run_record": saved["record_path"]}, result


def _list_junit(record: pathlib.Path, spec: dict[str, str]) -> int:
    return _run("--output", str(record), "--junit-xml", spec["xml"], "--junit-record", spec["run_record"], "--list-results")


def test_junit_accepts_pyfltr_run_record(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """pyfltr経由の記録とXMLの組を、直接起動の取込みと同じ形の完全テスト名と状態で取り込む。

    直接起動の名前の形は`test_public_import_uses_junit_states_and_updates_selected_rows`が確かめる。
    """
    text = "import pytest\n@pytest.mark.parametrize('value', [1, 2])\ndef test_value(value):\n    assert value == 1\n"
    git_repository.init_repository(tmp_path, files={"trial_test.py": text}, commit_message="基準")
    monkeypatch.chdir(tmp_path)
    spec, _ = _saved_pyfltr_trial(tmp_path, monkeypatch, "pyfltr", "--output-format=jsonl")
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    capsys.readouterr()
    assert _list_junit(record, spec) == 0, capsys.readouterr().err
    listed = {name: value["status"] for name, value in json.loads(capsys.readouterr().out)["tests"].items()}
    assert listed == {"trial_test.py::test_value[1]": "passed", "trial_test.py::test_value[2]": "failed"}


@pytest.mark.parametrize("invalid", ["no-archive", "text-output"])
def test_junit_rejects_missing_pyfltr_record_with_next_action(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """pyfltrの実行記録を読めない組を、受理する起動の形と再実行の操作を示して拒否する。"""
    git_repository.init_repository(
        tmp_path, files={"trial_test.py": "def test_one():\n    assert True\n"}, commit_message="基準"
    )
    monkeypatch.chdir(tmp_path)
    options = ["--output-format=jsonl", "--no-archive"] if invalid == "no-archive" else ["--output-format=text"]
    spec, _ = _saved_pyfltr_trial(tmp_path, monkeypatch, "pyfltr", *options)
    assert pathlib.Path(spec["xml"]).is_file()
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    capsys.readouterr()
    assert _list_junit(record, spec) == 1
    error = capsys.readouterr().err
    assert "pyfltr" in error and "--output-format=jsonl" in error
    assert "pytest -v -o junit_family=xunit1 --junitxml=<XMLの絶対パス>" in error


def test_junit_rejects_unmatched_junitxml_with_next_action(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """実行argvの--junitxmlと対応しないXMLを、受理する起動の形と再実行の操作を示して拒否する。"""
    monkeypatch.chdir(tmp_path)
    first = _saved_trial(tmp_path, "def test_one():\n    assert True\n", "first")
    # 同じ内容で、実行argvの--junitxmlが指していないXMLを用意する。
    other = tmp_path / "other.xml"
    other.write_bytes(pathlib.Path(first["xml"]).read_bytes())
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    capsys.readouterr()
    assert _list_junit(record, {"xml": str(other), "run_record": first["run_record"]}) == 1
    error = capsys.readouterr().err
    assert "--junitxml" in error and "pyfltr" in error
    assert "pytest -v -o junit_family=xunit1 --junitxml=<XMLの絶対パス>" in error


def _subproject_repository(tmp_path: pathlib.Path) -> pathlib.Path:
    """同名の試験ファイルを2つのサブプロジェクトへ置き、ファイル名だけでは1件に決まらないリポジトリを用意する。"""
    text = "def test_saved():\n    assert True\n"
    return git_repository.init_repository(
        tmp_path / "repo",
        files={"alpha/same_test.py": text, "beta/same_test.py": text},
        commit_message="基準",
    )


def _subproject_trial(repository: pathlib.Path, name: str, *pytest_args: str) -> dict[str, str]:
    """サブプロジェクトを作業ディレクトリとして実pytestを起動し、公開run-commandの記録とXMLを受け取る。"""
    xml = repository.parent / f"{name}.xml"
    result = _saved_command(
        repository / "alpha",
        "pytest",
        "-v",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit1",
        f"--junitxml={xml}",
        *pytest_args,
        "same_test.py",
    )
    assert result.returncode == 0, result.stderr
    saved = json.loads(result.stdout[result.stdout.index("{") :])
    assert saved["child_exit_code"] == 0
    return {"xml": str(xml), "run_record": saved["record_path"]}


def _listed_tests(record: pathlib.Path, spec: dict[str, str], capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    capsys.readouterr()
    assert _list_junit(record, spec) == 0, capsys.readouterr().err
    return {name: value["status"] for name, value in json.loads(capsys.readouterr().out)["tests"].items()}


def test_junit_names_from_subproject_and_root_rootdir_match(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """サブプロジェクトで実行した組と、rootdirにworktreeのルートを指定した組が同じworktree起点の名前になる。"""
    repository = _subproject_repository(tmp_path)
    monkeypatch.chdir(repository)
    subproject = _subproject_trial(repository, "subproject")
    root = _subproject_trial(repository, "root", f"--rootdir={repository}")
    record = repository / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(repository, "plan-user"))) == 0
    expected = {"alpha/same_test.py::test_saved": "passed"}
    assert _listed_tests(record, subproject, capsys) == expected
    assert _listed_tests(record, root, capsys) == expected


def test_junit_subproject_names_pass_reference_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """サブプロジェクトで取り込んだ名前を根拠へ保存し、保存時の参照の所在確認を通す。"""
    repository = _subproject_repository(tmp_path)
    monkeypatch.chdir(repository)
    spec = _subproject_trial(repository, "subproject")
    record = repository / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(repository, "plan-user"))) == 0
    payload = json.loads(record.read_text(encoding="utf-8"))
    name = "alpha/same_test.py::test_saved"
    updates = _json(
        tmp_path / "updates.json",
        [
            {"section": "user_requirements", "row": index, "source": row["source"], "tests": [name], "mode": "replace"}
            for index, row in enumerate(payload["user_requirements"], 1)
        ],
    )
    capsys.readouterr()
    assert (
        _run(
            "--output",
            str(record),
            "--junit-xml",
            spec["xml"],
            "--junit-record",
            spec["run_record"],
            "--updates-file",
            str(updates),
        )
        == 0
    ), capsys.readouterr().err
    for row in json.loads(record.read_text(encoding="utf-8"))["user_requirements"]:
        assert f"`{name}` passed" in row["evidence"]


def test_junit_rejects_rootdir_outside_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """worktreeの外のrootdirを持つ組を、起点とrootdirを示して拒否する。"""
    repository = _subproject_repository(tmp_path)
    monkeypatch.chdir(repository)
    spec = _subproject_trial(repository, "outside", f"--rootdir={tmp_path}")
    record = repository / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(repository, "plan-user"))) == 0
    capsys.readouterr()
    assert _list_junit(record, spec) == 1
    error = capsys.readouterr().err
    # worktreeのパスはtmp_pathを接頭辞に持つため、rootdirの表示は出現回数で区別する。
    assert str(repository) in error and error.count(str(tmp_path)) >= 2


@pytest.mark.parametrize("invalid", ["xml", "file", "duplicate", "head", "exit", "argv", "source", "row"])
def test_public_import_rejects_ambiguous_or_incomplete_results(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """破損・不整合と複数行の一部不正で、既存記録のバイト列を保持する。"""
    spec = _saved_trial(tmp_path, "def test_one():\n    assert True\n", "one")
    xml = pathlib.Path(spec["xml"])
    metadata = json.loads(pathlib.Path(spec["run_record"]).read_text(encoding="utf-8"))
    metadata_path = tmp_path / "metadata.json"
    spec["run_record"] = str(metadata_path)
    if invalid == "xml":
        xml.write_text("<", encoding="utf-8")
    elif invalid == "file":
        xml.write_text(xml.read_text(encoding="utf-8").replace('file="trial_test.py"', ""), encoding="utf-8")
    elif invalid == "duplicate":
        root = ET.parse(xml).getroot()
        suite = root.find("testsuite")
        assert suite is not None
        suite.append(ET.fromstring(ET.tostring(next(root.iter("testcase")))))
        ET.ElementTree(root).write(xml, encoding="utf-8")
    elif invalid == "head":
        metadata["git_head"] = None
    elif invalid == "exit":
        metadata["child_exit_code"] = None
    elif invalid == "argv":
        metadata["argv"] = ["pytest"]
    _json(metadata_path, metadata)
    results = _json(tmp_path / "results.json", {"junit": spec})
    record = tmp_path / "record.json"
    assert _run("--output", str(record), "--plan", str(_plan(tmp_path, "plan-user"))) == 0
    before = record.read_bytes()
    payload = json.loads(before)
    updates = [
        {
            "section": "user_requirements",
            "row": i,
            "source": row["source"],
            "tests": ["trial_test.py::test_one"],
            "mode": "append",
        }
        for i, row in enumerate(payload["user_requirements"], 1)
    ]
    if invalid == "source":
        updates[1]["source"] = "別出所"
    if invalid == "row":
        updates[1]["row"] = 10
    assert (
        _run(
            "--output",
            str(record),
            "--results-file",
            str(results),
            "--updates-file",
            str(_json(tmp_path / "updates.json", updates)),
        )
        == 1
    )
    assert record.read_bytes() == before
    assert "次の操作:" in capsys.readouterr().err
