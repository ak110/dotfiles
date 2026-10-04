"""公開コマンドで実行レビューの入力`完成条件証拠`が基準を満たすか判定する動作を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import typing

import pytest

from agent_toolkit._atk import review_table, run_script

FIRST_WI = "20260928-192559-001.md"
SECOND_WI = "20260928-192559-002.md"
REVIEWED_HEAD = "a" * 40
LEGACY_UNIT_MARKER = "証拠行 {source}"


def _condition(awi: str, condition: str) -> dict[str, str]:
    return {
        "awi": awi,
        "condition": condition,
        "outcome": "達成",
        "source": "WI本文",
        "evidence": f"条件『{condition}』の観測結果",
        "reviewed_head": REVIEWED_HEAD,
    }


def _requirement(awi: str, requirement: str) -> dict[str, str]:
    return {
        "awi": awi,
        "requirement": requirement,
        "origin": "WI本文",
        "outcome": "達成",
        "source": "WI本文",
        "evidence": f"要求『{requirement}』の観測結果",
        "reviewed_head": REVIEWED_HEAD,
    }


def _write_evidence(
    path: pathlib.Path, conditions: list[dict[str, str]], requirements: list[dict[str, str]] | None = None
) -> None:
    path.write_text(
        json.dumps({"wi_conditions": conditions, "user_requirements": requirements or []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _mock_wi(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, bodies: dict[str, str]) -> list[str]:
    requested: list[str] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[typing.Any]:
        del kwargs
        if args[0] == "git":
            if "cat-file" in args:
                reference = tmp_path / args[-1].split(":", maxsplit=1)[1]
                if reference.is_file():
                    return subprocess.CompletedProcess(args, 0, stdout=reference.read_bytes(), stderr=b"")
                return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"file is absent")
            value = str(tmp_path) if "--show-toplevel" in args else REVIEWED_HEAD
            return subprocess.CompletedProcess(args, 0, stdout=f"{value}\n", stderr="")
        filename = args[3]
        requested.append(filename)
        if filename not in bodies:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr=f"失敗: {filename}はありません")
        assert not any(arg.startswith("--output-file") for arg in args)
        output = tmp_path / (filename + ".stdout")
        output.write_text(f"## target_repo: example\n### {filename} [processing]\n---\n{bodies[filename]}", encoding="utf-8")
        # エージェント環境の`atk`は長い本文を標準出力へ書かないため、証拠を確かめる処理は保存先だけを読む必要がある。
        return subprocess.CompletedProcess(args, 0, stdout=f"保存先: {output}\n行数: 1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return requested


def _return_args(path: pathlib.Path, table: pathlib.Path, *extra: str) -> argparse.Namespace:
    return argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=[
            str(path),
            FIRST_WI,
            "--expected-head",
            REVIEWED_HEAD,
            "--review-table",
            str(table),
            "--round",
            "2",
            "--return-result",
            *extra,
        ],
    )


def _no_evidence_return_args(table: pathlib.Path, *extra: str) -> argparse.Namespace:
    """証拠要求なしの起動で、現在roundの表から返却を生成する引数。"""
    return argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=[
            "なし",
            "--expected-head",
            REVIEWED_HEAD,
            "--review-table",
            str(table),
            "--round",
            "2",
            *extra,
            "--return-result",
        ],
    )


def test_return_result_rejects_zero_issues_with_missing_evidence_and_recovers_after_registration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """旧形式の確認は成功しても、0件と証拠不足の返却は拒否し、実在指摘の登録後に回復する。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 保存\n"})
    row = _condition(FIRST_WI, "保存")
    row.update(outcome="証拠不足", evidence="保存操作の証拠をまだ取得できない")
    path = tmp_path / "evidence.json"
    table = tmp_path / "plan.exec-review.tsv"
    _write_evidence(path, [row])
    review_table.init(table)
    capsys.readouterr()
    legacy = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=[str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(legacy) == 0
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table)) == 1
    result = capsys.readouterr()
    assert not result.out and "wi_conditions[1]" in result.err and "現在round" in result.err
    review_table.add(table, "2", "exec-review", "保存操作", "保存の証拠を補う", "仕様")
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table)) == 0
    assert capsys.readouterr().out == (
        f"状態: completed\nレビューしたHEAD: {REVIEWED_HEAD}\n未解決の指摘数: 1\n完成条件証拠のパス: {path}\n"
    )


def test_return_result_recovers_after_evidence_is_observed_without_inventing_issue(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 保存\n"})
    path = tmp_path / "evidence.json"
    table = tmp_path / "plan.exec-review.tsv"
    review_table.init(table)
    row = _condition(FIRST_WI, "保存")
    row.update(evidence="test_save_settings 成功")
    _write_evidence(path, [row])
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table)) == 0
    assert "未解決の指摘数: 0\n" in capsys.readouterr().out
    assert not table.read_bytes()


@pytest.mark.parametrize("outcome,expected", [("証拠不足", 0), ("未達", 1)])
def test_return_result_distinguishes_optional_observation_from_observed_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str, expected: int
) -> None:
    condition = "任意の判断材料: 実機の観測値"
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: f"type: awi\nsource: agent\n---\n## 完成条件\n- {condition}\n"})
    path, table = tmp_path / "evidence.json", tmp_path / "plan.exec-review.tsv"
    review_table.init(table)
    row = _condition(FIRST_WI, condition)
    row.update(outcome=outcome, evidence="実機の観測に関する判定")
    _write_evidence(path, [row])
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table)) == expected


@pytest.mark.parametrize("kind", ["reject", "deferred", "publication", "parallel"])
def test_return_result_accepts_nonachievement_only_from_referenced_input_record(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 保存\n"})
    path, table = tmp_path / "evidence.json", tmp_path / "plan.exec-review.tsv"
    record = tmp_path / "input.md"
    bodies = {
        "reject": "## 実施内容\n\n| 実施内容 | 採否 | 根拠 |\n| --- | --- | --- |\n"
        f"| {FIRST_WI}の要求 | 不採用 | 前提が成立しない |\n",
        "deferred": f"AWI: {FIRST_WI}\n判定対象: 保存\n終端区分: 延期adopt\n"
        "後続工程: 公開後の再観測\n検収時機: 公開後に保存結果を取得した後\n",
        "publication": f"AWI: {FIRST_WI}\n判定対象: 保存\n判定工程: 公開工程\n",
        "parallel": f"AWI: {FIRST_WI}\n判定対象: 保存\n判定工程: ユーザビリティレビュー\n進行状態: 並行中\n",
    }
    record.write_text(bodies[kind], encoding="utf-8")
    review_table.init(table)
    row = _condition(FIRST_WI, "保存")
    row.update(
        outcome="証拠不足", source=str(record), evidence=f"{record} の判断に従いこの条件を後続工程または不採用へ対応付けた"
    )
    _write_evidence(path, [row])
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table, "--input-record", str(record))) == 0
    assert "未解決の指摘数: 0" in capsys.readouterr().out
    assert run_script.dispatch(_return_args(path, table)) == 1
    result = capsys.readouterr()
    assert not result.out and "--input-record" in result.err


def test_return_result_without_evidence_generates_three_lines_from_current_round(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_wi(monkeypatch, tmp_path, {})
    table = tmp_path / "plan.exec-review.tsv"
    review_table.init(table)
    review_table.add(table, "1", "exec-review", "旧指摘", "前回の指摘", "詳細")
    capsys.readouterr()
    assert run_script.dispatch(_no_evidence_return_args(table)) == 0
    assert capsys.readouterr().out == f"状態: completed\nレビューしたHEAD: {REVIEWED_HEAD}\n未解決の指摘数: 0\n"


@pytest.mark.parametrize("source_suffix", ["#存在しない節", "#別の節", ":99-100"])
def test_return_result_does_not_use_unreferenced_section_as_permission(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], source_suffix: str
) -> None:
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 保存\n"})
    path, table = tmp_path / "evidence.json", tmp_path / "plan.exec-review.tsv"
    record = tmp_path / "input.md"
    record.write_text(
        f"## 別の節\n\n通常の記録\n\n## 後続工程\n\nAWI: {FIRST_WI}\n判定対象: 保存\n"
        "終端区分: 延期adopt\n後続工程: 公開\n検収時機: 公開後\n",
        encoding="utf-8",
    )
    review_table.init(table)
    row = _condition(FIRST_WI, "保存")
    row.update(outcome="証拠不足", source=str(record) + source_suffix, evidence="後続工程を根拠とするという申告")
    _write_evidence(path, [row])
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table, "--input-record", str(record))) == 1
    result = capsys.readouterr()
    assert not result.out and "wi_conditions[1]" in result.err


def test_plan_only_template_and_return_keep_existing_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_wi(monkeypatch, tmp_path, {})
    path, table = tmp_path / "evidence.json", tmp_path / "plan.exec-review.tsv"
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n\nユーザーの要求は保存である。\n", encoding="utf-8")
    template = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--template", str(path), "--plan", str(plan)]
    )
    assert not path.exists()
    assert run_script.dispatch(template) == 0
    row = _requirement("", "保存")
    row.update(source=str(plan), evidence="test_save_settings 成功")
    _write_evidence(path, [], [row])
    before = path.read_bytes()
    assert run_script.dispatch(template) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["user_requirements"] == [row]
    review_table.init(table)
    capsys.readouterr()
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=[
            str(path),
            "--plan",
            str(plan),
            "--expected-head",
            REVIEWED_HEAD,
            "--review-table",
            str(table),
            "--round",
            "2",
            "--return-result",
        ],
    )
    assert run_script.dispatch(args) == 0
    assert "未解決の指摘数: 0" in capsys.readouterr().out
    assert json.loads(before) == json.loads(path.read_bytes())


@pytest.mark.parametrize("layout", ["same-wi", "cross-wi", "cross-array", "plan"])
@pytest.mark.parametrize(
    "evidence_text", ["実行結果", " 提出済み検証記録 ", "レビュー対象差分", "テスト成功", "missing.md", "観測した内容" * 100]
)
def test_public_command_rejects_shared_evidence_without_reference(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    layout: str,
    evidence_text: str,
) -> None:
    """所在のない共用をWI内外、両配列、指定WI集合外の計画要求でも拒否する。"""
    first = _condition(FIRST_WI, "保存")
    second = {
        "same-wi": _condition(FIRST_WI, "再読込"),
        "cross-wi": _condition(SECOND_WI, "再読込"),
        "cross-array": _requirement(FIRST_WI, "再読込後も保持"),
        "plan": _requirement("", "計画だけの要求"),
    }[layout]
    first["evidence"] = evidence_text
    second["evidence"] = evidence_text.strip()
    conditions = [first, second] if layout in {"same-wi", "cross-wi"} else [first]
    requirements = [] if layout in {"same-wi", "cross-wi"} else [second]
    body = "type: awi\nsource: agent\n---\n## 完成条件\n- 保存\n"
    if layout == "same-wi":
        body += "- 再読込\n"
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: body})
    path = tmp_path / "evidence.json"
    _write_evidence(path, conditions, requirements)
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 1
    diagnostic = capsys.readouterr().err
    assert "wi_conditions[1].evidence" in diagnostic
    assert ("wi_conditions[2]" if layout in {"same-wi", "cross-wi"} else "user_requirements[1]") in diagnostic
    assert ("計画由来" if layout == "plan" else second["awi"]) in diagnostic
    assert evidence_text.strip() in diagnostic and "証拠不足へ再判定" in diagnostic


def _shared_rows(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, evidence: str) -> pathlib.Path:
    """保存と再読込の2条件へ同じ根拠を記入した証拠と、参照先の記録を用意する。"""
    records = tmp_path / "records"
    records.mkdir()
    record = records / "観測.md"
    record.write_text("# 設定保存\n保存と再読込が成功した。\n" + "観測。\n" * 10, encoding="utf-8")
    (records / "test_settings.py").write_text("", encoding="utf-8")
    rows = [_condition(FIRST_WI, "保存"), _condition(FIRST_WI, "再読込")]
    for row in rows:
        row["evidence"] = evidence.format(absolute=record)
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\n---\n## 完成条件\n- 保存\n- 再読込\n"})
    path = tmp_path / "evidence.json"
    _write_evidence(path, rows)
    return path


@pytest.mark.parametrize(
    "reference",
    [
        "検証記録: records/観測.md#設定保存 で保存後の再読込まで観測した",
        "`records/観測.md:12`の保存と再読込の両観測",
        "[検証](records/観測.md#設定保存)の手順2で保存と再読込が成功",
        "{absolute} の設定保存節で両方の結果を確認",
        "`records/test_settings.py::test_save` 成功",
        "test_save_settings PASSED",
        "`test_save_settings` 成功",
        "test_save_settings: 成功",
    ],
)
def test_public_command_accepts_shared_reference_with_explanation_or_test_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    """同じ記録への参照に観測内容の説明を添えた共用と、具体的なテスト成功結果の共用は受理する。"""
    path = _shared_rows(tmp_path, monkeypatch, reference)
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 0


@pytest.mark.parametrize(
    "reference",
    [
        "records/観測.md",
        "records/観測.md:12",
        "`records/観測.md#設定保存`",
        "[検証](records/観測.md#設定保存)",
        "{absolute}",
        "`{absolute}:12`",
        "records/観測.md、records/test_settings.py",
        "検証: records/観測.md",
        "検証記録：`records/観測.md#設定保存`",
        "根拠: records/観測.md、確認: records/test_settings.py",
    ],
)
def test_public_command_rejects_shared_reference_without_explanation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], reference: str
) -> None:
    """行ごとの説明が無く同じファイル参照だけを異なる条件へ写した証拠を、両方の行について拒否する。"""
    path = _shared_rows(tmp_path, monkeypatch, reference)
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert "wi_conditions[1].evidence" in error and "wi_conditions[2].evidence" in error
    assert "行ごとの説明が無いファイル参照だけ" in error


def test_public_command_accepts_same_file_with_row_specific_explanations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じファイルを参照しても、行ごとに満たす箇所と内容を書いた根拠は受理する。"""
    path = _shared_rows(tmp_path, monkeypatch, "records/観測.md")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["wi_conditions"][0]["evidence"] = "records/観測.md の設定保存節で保存が成功"
    data["wi_conditions"][1]["evidence"] = "records/観測.md の設定保存節で再読込後も値が残る"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 0


@pytest.mark.parametrize("outcome", ["未達", "証拠不足", "失効", "割当外"])
def test_public_command_excludes_nonachieved_shared_reasons(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """公開待ちや不採用の理由を共有しても達成根拠の共用には含めない。

    失効と割当外は根拠の記録を確かめる判定値であるため、計画由来の行にも有効な記録を与える。
    """
    answer = "20260929-120000-001.md"
    _mock_wi(
        monkeypatch,
        tmp_path,
        {FIRST_WI: "type: awi\n---\n## 完成条件\n- 保存\n", answer: "type: uwi\n---\n## 回答\n条件を外す\n"},
    )
    plan = tmp_path / "plan.md"
    plan.write_text("## 実施内容\n\n| 保存と再読込は分割元の依頼全体として割当外 |\n", encoding="utf-8")
    source, evidence = {
        "失効": (f"{answer} の ## 回答", "公開工程待ち"),
        "割当外": (f"{plan} の ## 実施内容", "分割元の依頼全体（公開工程待ち）"),
    }.get(outcome, ("WI本文", "公開工程待ち"))
    first = {**_requirement("", "保存"), "outcome": outcome, "source": source, "evidence": evidence}
    second = {**_requirement("", "再読込"), "outcome": outcome, "source": source, "evidence": evidence}
    path = tmp_path / "evidence.json"
    _write_evidence(path, [_condition(FIRST_WI, "保存")], [first, second])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 0


def test_public_command_accepts_same_unit_duplicates_and_distinct_evidence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一単位の重複、単独の抽象根拠、異なる根拠を新判定では拒否しない。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\n---\n## 完成条件\n- 保存\n- 再読込\n"})
    first = {**_condition(FIRST_WI, "保存"), "evidence": "実行結果"}
    second = {**_condition(FIRST_WI, "再読込"), "evidence": "再読込の結果"}
    path = tmp_path / "evidence.json"
    _write_evidence(path, [first, first.copy(), second])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 0


THIRD_WI = "20260928-192559-003.md"


def _cross_wi_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, texts: dict[str, list[str]], evidence: str
) -> pathlib.Path:
    """WIごとの原文AWIの文を要求単位とし、全行へ同じ根拠を記入した証拠を用意する。"""
    record = tmp_path / "verification.md"
    record.write_text("# 受入シナリオ\n全シナリオが成功した。\n", encoding="utf-8")
    _mock_wi(monkeypatch, tmp_path, {awi: f"type: awi\n---\n# 題\n\n{''.join(lines)}\n" for awi, lines in texts.items()})
    rows = [
        {**_requirement(awi, text), "evidence": evidence.format(record=record)}
        for awi, lines in texts.items()
        for text in lines
    ]
    path = tmp_path / "evidence.json"
    _write_evidence(path, [], rows)
    return path


def test_public_command_rejects_explained_reference_shared_across_wi_requirements(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """異なるWIの異なる要求単位へ、パスに汎用的な説明を添えただけの同じ根拠を写した証拠を拒否する。

    受理すると、各行の要求を判定せずに空欄を埋めた証拠が確認を通り、未判定の行が達成として統合へ渡る。
    """
    texts = {
        FIRST_WI: ["報告見えてないんだけど、拡張思考の中で発話したつもりになったりしてない？"],
        SECOND_WI: ["既存規範優先ってどこまで？", "維持でいいよ。"],
        THIRD_WI: ["改善してほしい。", "設定にも加える (Recommended)"],
    }
    path = _cross_wi_rows(tmp_path, monkeypatch, texts, "{record} の該当実装・受入結果を確認。")
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", str(path), FIRST_WI, SECOND_WI, THIRD_WI, "--expected-head", REVIEWED_HEAD],
    )
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert error.count("異なるWIの異なる要求単位で同じ達成根拠を共用しています") == 5
    assert f"{THIRD_WI}: user_requirements[5].evidence" in error


@pytest.mark.parametrize(
    ("texts", "evidence"),
    [
        # 分割起票した兄弟WIが、同じ原文の要求を同じ根拠で記録する。
        ({FIRST_WI: ["設定を移して。"], SECOND_WI: ["設定を移して。"]}, "{record} の移行節で設定の移行を観測"),
        # 具体的なテスト名と成功結果は、異なるWIの要求へ共用しても行を判定した根拠として読める。
        ({FIRST_WI: ["設定を移して。"], SECOND_WI: ["旧入口を廃止して。"]}, "test_settings_migration: 成功"),
    ],
    ids=["sibling-same-text", "test-result"],
)
def test_public_command_accepts_sibling_or_test_result_shared_across_wi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    texts: dict[str, list[str]],
    evidence: str,
) -> None:
    """WIをまたぐ共用のうち、同じ原文の行どうしと具体的なテスト結果の共用は受理する。"""
    path = _cross_wi_rows(tmp_path, monkeypatch, texts, evidence)
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), *texts, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 0, capsys.readouterr().err


@pytest.mark.parametrize(
    ("section", "other_wi"),
    [("wi_conditions", FIRST_WI), ("wi_conditions", SECOND_WI), ("user_requirements", FIRST_WI), ("user_requirements", "")],
)
@pytest.mark.parametrize(
    "marker",
    ["（対象単位: {source}）", "証拠行 {source}", "要件原文「{text}」", "(対象単位: {source}) | 要件原文「{text}」"],
)
def test_public_command_rejects_row_markers_disguising_shared_observation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    section: str,
    other_wi: str,
    marker: str,
) -> None:
    """単位名と原文引用だけが異なる定型を、WI内外・配列間・計画由来の全達成行で拒否する。"""
    first_text, second_text = "保存（「設定」）", "再読込（「保持」）"
    record = tmp_path / "verification.md"
    record.write_text("# 観測\n保存と再読込。\n", encoding="utf-8")
    first = _condition(FIRST_WI, first_text)
    second = {"wi_conditions": _condition, "user_requirements": _requirement}[section](other_wi, second_text)
    for index, row in enumerate((first, second), 1):
        row["source"] = f"{row['awi'] or '計画'}#原文 {index}"
        suffix = marker.format(source=row["source"], text=(first_text, second_text)[index - 1])
        row["evidence"] = f"{record}:12-16 の該当箇所を確認した {suffix}"
    _mock_wi(
        monkeypatch,
        tmp_path,
        {
            FIRST_WI: f"type: awi\n---\n## 完成条件\n- {first_text}\n"
            + (f"- {second_text}\n" if section == "wi_conditions" and other_wi == FIRST_WI else "")
        },
    )
    path = tmp_path / "evidence.json"
    _write_evidence(
        path, [first, second] if section == "wi_conditions" else [first], [second] if section == "user_requirements" else []
    )
    assert _check(path, FIRST_WI) == 1
    diagnostic = capsys.readouterr().err
    assert "wi_conditions[1].evidence" in diagnostic
    assert f"{section}[{2 if section == 'wi_conditions' else 1}].evidence" in diagnostic
    assert "単位名・行標識・原文引用だけ" in diagnostic and "証拠不足へ再判定" in diagnostic


@pytest.mark.parametrize(
    ("first_body", "second_body", "texts", "expected"),
    [
        ("test_save_and_reload: 成功", "test_save_and_reload: 成功", ("保存", "再読込"), 0),
        ("観測.md:12 で値1を観測", "観測.md:13 で値2を観測", ("保存", "再読込"), 0),
        ("観測.md:12 保存が成功", "観測.md:12 再読込後に保持", ("保存", "再読込"), 0),
        ("観測.md:12 を確認", "観測.md:12 を確認", ("保存", "保存"), 0),
        ("観測.md:12 を確認", "観測.md:12 を確認", ("test_save: 成功", "test_reload: 成功"), 1),
    ],
)
def test_public_command_preserves_observation_and_excludes_success_inside_quote(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    first_body: str,
    second_body: str,
    texts: tuple[str, str],
    expected: int,
) -> None:
    """観測の差と正当なテスト共用を保ち、原文引用の成功文字列だけでは免除しない。"""
    (tmp_path / "観測.md").write_text("観測\n" * 13, encoding="utf-8")
    rows = [_condition(FIRST_WI, texts[0]), _condition(SECOND_WI, texts[1])]
    for row, body in zip(rows, (first_body, second_body), strict=True):
        row["evidence"] = f"{body}（対象単位: {row['awi']}） 要件原文「{row['condition']}」"
        row["source"] = row["awi"]
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: f"type: awi\n---\n## 完成条件\n- {texts[0]}\n"})
    path = tmp_path / "evidence.json"
    _write_evidence(path, rows)
    assert _check(path, FIRST_WI) == expected, capsys.readouterr().err


def test_public_command_keeps_markers_that_do_not_match_row_information(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """行情報と一致しない単位名や引用を保持し、観測内容が異なる根拠を受理する。"""
    path = _shared_rows(tmp_path, monkeypatch, "records/観測.md")
    data = json.loads(path.read_text(encoding="utf-8"))
    for row, detail in zip(data["wi_conditions"], ("対象単位: 別の保存記録", "要件原文「別の再読込記録」"), strict=True):
        row["evidence"] += f" で確認。{detail}"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert _check(path, FIRST_WI) == 0


def test_public_command_accepts_bullets_and_paragraph(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開されたコマンドで複数箇条書きと段落一件の条件数を判定する。"""
    evidence = tmp_path / "evidence.json"
    _write_evidence(
        evidence,
        [_condition(FIRST_WI, "第一条件"), _condition(FIRST_WI, "第二条件"), _condition(SECOND_WI, "段落の完成条件。")],
    )

    _mock_wi(
        monkeypatch,
        tmp_path,
        {
            FIRST_WI: "type: awi\nsource: agent\n---\n# WI\n## 完成条件\n\n- 第一条件\n- 第二条件\n",
            SECOND_WI: "type: awi\nsource: agent\n---\n# WI\n## 完成条件\n\n段落の完成条件。\n",
        },
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI, SECOND_WI],
    )
    assert run_script.dispatch(args) == 0


def test_public_command_rejects_numbered_condition_instead_of_original(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """行数が一致しても原文との対応が無ければ公開されたコマンドで拒否する。"""
    evidence = tmp_path / "evidence.json"
    _mock_wi(
        monkeypatch,
        tmp_path,
        {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 第一条件\n- 第二条件\n"},
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI]
    )

    _write_evidence(evidence, [_condition(FIRST_WI, "完成条件1"), _condition(FIRST_WI, "第二条件")])
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert FIRST_WI in error
    assert "完成条件1" in error
    assert "第一条件" in error

    _write_evidence(evidence, [_condition(FIRST_WI, "- 第一条件 "), _condition(FIRST_WI, "第二条件")])
    assert run_script.dispatch(args) == 0


def test_reports_every_wi_with_missing_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """WIが複数でも不足した全件を一度で返す。"""
    evidence = tmp_path / "evidence.json"
    _write_evidence(evidence, [])

    _mock_wi(
        monkeypatch,
        tmp_path,
        {
            FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 第一条件\n- 第二条件\n",
            SECOND_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 第一条件\n- 第二条件\n",
        },
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI, SECOND_WI],
    )
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert FIRST_WI in error and SECOND_WI in error
    assert error.count("期待 2 行、実数 0 行") == 2


def test_raw_awi_requires_each_original_sentence_and_comment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """原文AWIの文とユーザーコメントを対象WIの`完成条件証拠`の行へ結び付ける。"""
    evidence = tmp_path / "evidence.json"
    first = "検索範囲を変更して。"
    second = "選択が反映されるように直して。"
    comment = "再読込後も維持して。"
    _mock_wi(
        monkeypatch,
        tmp_path,
        {
            FIRST_WI: (
                f"type: awi\n---\n{first}{second}\n\n## ユーザーコメント\n\n- {comment}\n\n## 処理結果\n\n- 採否: adopted\n"
            )
        },
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI]
    )

    _write_evidence(evidence, [], [_requirement(FIRST_WI, first), _requirement(FIRST_WI, second)])
    assert run_script.dispatch(args) == 1
    assert comment in capsys.readouterr().err

    _write_evidence(
        evidence,
        [],
        [_requirement(FIRST_WI, first), _requirement(FIRST_WI, second), _requirement(FIRST_WI, comment)],
    )
    assert run_script.dispatch(args) == 0


def test_conditions_also_require_verbatim_requests_and_user_comment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "evidence.json"
    first = "設定を移して。"
    second = "旧入口を廃止して。"
    comment = "エンドユーザー向けの案内も直して。"
    _mock_wi(
        monkeypatch,
        tmp_path,
        {
            FIRST_WI: (
                "type: awi\nsource: agent\n---\n# WI\n"
                "## 完成条件\n- 新入口で操作できる\n"
                "## ユーザー指摘の逐語引用\n出所: 会話\n\n"
                f"```text\n{first}{second}\n```\n"
                f"## ユーザーコメント\n- {comment}\n"
            )
        },
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI]
    )

    _write_evidence(
        evidence, [_condition(FIRST_WI, "新入口で操作できる")], [_requirement(FIRST_WI, "設定と旧入口を変更して。")]
    )
    assert run_script.dispatch(args) == 1
    diagnostic = capsys.readouterr().err
    assert first in diagnostic and second in diagnostic and comment in diagnostic

    _write_evidence(
        evidence,
        [_condition(FIRST_WI, "新入口で操作できる")],
        [_requirement(FIRST_WI, first), _requirement(FIRST_WI, second), _requirement(FIRST_WI, comment)],
    )
    assert run_script.dispatch(args) == 0


def test_split_awi_accepts_unassigned_requirement_but_not_unassigned_condition(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """分割起票で他のWIへ割り当てた原文要求は割当外として受理し、完成条件の割当外は拒否する。

    完成条件の行で割当外を受理すると、そのWI自身が担う条件の未達が統合時の判定を通過する。
    """
    evidence = tmp_path / "evidence.json"
    own = "設定画面を直して。"
    whole = "処理全体を3時間以内に収めて。"
    _mock_wi(
        monkeypatch, tmp_path, {FIRST_WI: _split_awi(own, whole, f"- 引用の後半（所要時間）は{_WHOLE}として割当外とする\n")}
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI]
    )
    unassigned = {
        **_requirement(FIRST_WI, whole),
        "outcome": "割当外",
        "source": f"{FIRST_WI} ## 反映内容と反映先",
        "evidence": _WHOLE,
    }

    _write_evidence(evidence, [_condition(FIRST_WI, "設定画面で保存できる")], [_requirement(FIRST_WI, own), unassigned])
    assert run_script.dispatch(args) == 0

    _write_evidence(
        evidence,
        [{**_condition(FIRST_WI, "設定画面で保存できる"), "outcome": "割当外"}],
        [_requirement(FIRST_WI, own), unassigned],
    )
    assert run_script.dispatch(args) == 1
    stderr = capsys.readouterr().err
    assert "wi_conditions[1].outcome: 未知の判定です: 割当外（受理する値: " in stderr
    assert "\n次の操作: " in stderr


_WHOLE = "分割元の依頼全体"
_OTHER_TITLE = "設定の一括移行"


def _split_awi(own: str, other: str, record: str) -> str:
    """2つの要求を逐語引用に持ち、`## 反映内容と反映先`へ割当の記録を持つ分割起票のAWI本文を返す。"""
    return (
        "type: awi\nsource: agent\n---\n# WI\n"
        f"## 反映内容と反映先\n\n- 引用の前半は本AWIで扱う\n{record}\n"
        "## 完成条件\n- 設定画面で保存できる\n"
        "## ユーザー指摘の逐語引用\n出所: 会話\n\n"
        f"```text\n{own}{other}\n```\n"
    )


@pytest.mark.parametrize(
    ("record", "source", "evidence", "diagnostic"),
    [
        # 割当の記録が無い節を指す行。割当先の名前だけが割当を示さない行にある。
        (
            f"- 「{_OTHER_TITLE}」の担当範囲を確認した\n",
            "{wi} ## 反映内容と反映先",
            f"「{_OTHER_TITLE}」",
            "割当を示す行にありません",
        ),
        # sourceが割当の記録の所在（WIファイル名と節名）を持たない行。
        (f"- 引用の後半は「{_OTHER_TITLE}」へ割当\n", "WI本文", f"「{_OTHER_TITLE}」", "記録を特定できません"),
        # evidenceが割当先の表記を持たない行。
        (
            f"- 引用の後半は「{_OTHER_TITLE}」へ割当\n",
            "{wi} ## 反映内容と反映先",
            "対象プロジェクト宛ての別AWI",
            "割当先の表記がありません",
        ),
        # evidenceの割当先が記録の行に無い行。
        (f"- 引用の後半は「{_OTHER_TITLE}」へ割当\n", "{wi} ## 反映内容と反映先", f"{SECOND_WI}", "割当を示す行にありません"),
    ],
)
def test_unassigned_requirement_without_matching_record_is_rejected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record: str,
    source: str,
    evidence: str,
    diagnostic: str,
) -> None:
    """割当の記録の所在、割当先の表記、記録との一致のいずれかを欠く割当外行を拒否する。

    受理すると、自身の反映先へ対応する要求まで割当外として記録した証拠が統合時の読解まで残る。
    """
    own = "設定画面を直して。"
    other = "旧設定も一括で移して。"
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _split_awi(own, other, record)})
    path = tmp_path / "evidence.json"
    unassigned = {
        **_requirement(FIRST_WI, other),
        "outcome": "割当外",
        "source": source.format(wi=FIRST_WI),
        "evidence": evidence,
    }
    _write_evidence(path, [_condition(FIRST_WI, "設定画面で保存できる")], [_requirement(FIRST_WI, own), unassigned])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    line = next(line for line in error.splitlines() if "user_requirements[2]" in line)
    assert line.startswith(f"失敗: {FIRST_WI}: user_requirements[2].") and diagnostic in line
    assert "sourceへ書く" in line or "evidenceへ" in line


def test_unassigned_requirement_matching_record_is_accepted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """割当先が記録の同じ節の割当を示すいずれかの行にあれば、（同上）の行や計画の記録を所在とする行も受理する。"""
    own = "設定画面を直して。"
    others = ["旧設定も一括で移して。", "移行後に旧形式を削除して。", "全体を今週中に終えて。"]
    record = f"- 引用の2文目は「{_OTHER_TITLE}」へ割当（{SECOND_WI}）\n- 引用の3文目（同上）も割当外\n"
    body = _split_awi(own, "".join(others), record)
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: body})
    plan = tmp_path / "計画 レーン01.md"
    plan.write_text(f"## 実施内容\n\n| 引用の4文目は{_WHOLE}として割当外 | 人間由来のWI |\n\n## 検証\n", encoding="utf-8")
    source = f"{FIRST_WI} ## 反映内容と反映先"
    rows = [
        _requirement(FIRST_WI, own),
        {**_requirement(FIRST_WI, others[0]), "outcome": "割当外", "source": source, "evidence": f"「{_OTHER_TITLE}」"},
        {**_requirement(FIRST_WI, others[1]), "outcome": "割当外", "source": source, "evidence": f"{SECOND_WI}（同上）"},
        {**_requirement(FIRST_WI, others[2]), "outcome": "割当外", "source": f"{plan} の ## 実施内容", "evidence": _WHOLE},
    ]
    path = tmp_path / "evidence.json"
    _write_evidence(path, [_condition(FIRST_WI, "設定画面で保存できる")], rows)
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 0, capsys.readouterr().err


def test_expired_requirement_also_checks_user_answer_source(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """原文要求の失効行も、完成条件の失効行と同じくユーザー判断の参照先を確かめる。"""
    own = "設定画面を直して。"
    other = "旧設定も一括で移して。"
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _split_awi(own, other, "")})
    path = tmp_path / "evidence.json"
    expired = {**_requirement(FIRST_WI, other), "outcome": "失効", "source": "計画の不採用行"}
    _write_evidence(path, [_condition(FIRST_WI, "設定画面で保存できる")], [_requirement(FIRST_WI, own), expired])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 1
    assert f"失敗: {FIRST_WI}: user_requirements[2].source: 失効のユーザー判断を確認できません" in capsys.readouterr().err


@pytest.mark.parametrize("awi", [FIRST_WI, SECOND_WI])
@pytest.mark.parametrize(
    ("reference_body", "source", "valid"),
    [
        ("type: uwi\n---\n## 回答\n条件を外す\n", "20260929-120000-001.md の ## 回答: 条件を外す", True),
        ("type: uwi\n---\n## 回答\n<!-- 回答案内 -->\n", "20260929-120000-001.md の ## 回答", False),
        ("type: awi\n---\n## 回答\n条件を外す\n", "20260929-120000-001.md の UWI回答", False),
        (None, "20260929-120000-001.md の ## 回答", False),
        (None, "レビュー指摘管理表とreview_contract", False),
    ],
)
def test_expired_condition_checks_user_answer_source(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    awi: str,
    reference_body: str | None,
    source: str,
    valid: bool,
) -> None:
    """公開されたコマンドで失効根拠の種類と回答の有無が条件を満たすか判定し、別のWIでも同じ不足を検出する。"""
    reference = "20260929-120000-001.md"
    bodies = {awi: "type: awi\nsource: agent\n---\n## 完成条件\n- 取り除く条件\n"}
    if reference_body is not None:
        bodies[reference] = reference_body
    _mock_wi(monkeypatch, tmp_path, bodies)
    evidence = tmp_path / "evidence.json"
    _write_evidence(evidence, [{**_condition(awi, "取り除く条件"), "outcome": "失効", "source": source}])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), awi],
    )
    assert run_script.dispatch(args) == (0 if valid else 1)
    error = capsys.readouterr().err
    if not valid:
        assert awi in error and "wi_conditions[1].source" in error and "ユーザー" in error


@pytest.mark.parametrize("comment", ["条件を外す。", "<!-- コメント案内 -->"])
def test_expired_condition_checks_own_user_comment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, comment: str
) -> None:
    """ユーザーコメントの要求行を保った証拠を受理し、案内だけの空欄を拒否する。"""
    _mock_wi(
        monkeypatch,
        tmp_path,
        {FIRST_WI: f"type: awi\nsource: agent\n---\n## 完成条件\n- 条件\n## ユーザーコメント\n{comment}\n"},
    )
    evidence = tmp_path / "evidence.json"
    valid = not comment.startswith("<!--")
    _write_evidence(
        evidence,
        [{**_condition(FIRST_WI, "条件"), "outcome": "失効", "source": f"{FIRST_WI} の ## ユーザーコメント"}],
        [_requirement(FIRST_WI, comment)] if valid else [],
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI],
    )
    assert run_script.dispatch(args) == (0 if valid else 1)


@pytest.mark.parametrize("outcome", ["達成", "未達", "証拠不足"])
def test_nonexpired_rows_do_not_fetch_answer_references(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """rejectの未達や延期の証拠不足を含む非失効行へ、追加の回答取得を課さない。"""
    requested = _mock_wi(
        monkeypatch,
        tmp_path,
        {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 条件\n"},
    )
    evidence = tmp_path / "evidence.json"
    _write_evidence(evidence, [{**_condition(FIRST_WI, "条件"), "outcome": outcome, "source": "20260929-120000-001.md 回答"}])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI],
    )
    for _ in range(2):
        assert run_script.dispatch(args) == 0
    assert requested == [FIRST_WI, FIRST_WI]


def test_answered_uwi_checks_answer_only(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """回答だけを要求単位として数え、案内と質問を含めない。"""
    evidence = tmp_path / "evidence.json"
    answer = "その対応で問題無い"
    _mock_wi(
        monkeypatch,
        tmp_path,
        {
            SECOND_WI: (
                "type: uwi\nsource: agent\n---\n"
                "## 質問\n\nどうしますか？\n\n## 回答\n\n"
                f"<!-- ユーザーはこの行以降に回答を追記する -->\n{answer}\n\n## 処理結果\n\n- 採否: adopted\n"
            )
        },
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), SECOND_WI]
    )

    _write_evidence(evidence, [])
    assert run_script.dispatch(args) == 1
    assert answer in capsys.readouterr().err

    _write_evidence(evidence, [], [_requirement(SECOND_WI, answer)])
    assert run_script.dispatch(args) == 0


@pytest.mark.parametrize(
    ("frontmatter", "body", "diagnostic"),
    [
        ("type: uwi\nsource: agent", "## 回答\n<!-- 案内 -->\n## 処理結果\n採否: adopted", "『回答』節が空"),
        ("type: awi\nsource: agent", "# 通常AWI", "『完成条件』節がありません"),
        ("type: awi\nsource: agent", "## 完成条件\n\n## 処理結果\n採否: adopted", "『完成条件』節が空"),
    ],
)
def test_rejects_missing_required_wi_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    frontmatter: str,
    body: str,
    diagnostic: str,
) -> None:
    """正規の入力と区別して空回答、通常AWIの節欠落と空節を拒否する。"""
    evidence = tmp_path / "evidence.json"
    _write_evidence(evidence, [])
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: f"{frontmatter}\n---\n{body}\n"})
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI]
    )
    assert run_script.dispatch(args) == 1
    stderr = capsys.readouterr().err
    assert diagnostic in stderr
    assert "`atk wi show <ファイル名>`" in stderr.split("\n次の操作: ", maxsplit=1)[1]


@pytest.mark.parametrize(
    ("content", "diagnostic"),
    [
        ("{", "`完成条件証拠`を読めません"),
        ('{"wi_conditions": {}, "user_requirements": []}', "wi_conditions: 配列が必要"),
        ('{"wi_conditions": [{"awi": 1}], "user_requirements": []}', "wi_conditions[1].awi: 文字列が必要"),
        (
            json.dumps(
                {"wi_conditions": [{**_condition(FIRST_WI, "完成条件"), "outcome": "保留"}], "user_requirements": []},
                ensure_ascii=False,
            ),
            "未知の判定",
        ),
        ('{"wi_conditions": [], "user_requirements": [{}]}', "user_requirements[1].requirement: 文字列が必要"),
    ],
)
def test_rejects_invalid_json_schema_and_outcome(
    tmp_path: pathlib.Path, content: str, diagnostic: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """構文、必須キーと型、判定値域の不備を区別する。"""
    evidence = tmp_path / "evidence.json"
    evidence.write_text(content, encoding="utf-8")
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI]
    )
    assert run_script.dispatch(args) == 1
    stderr = capsys.readouterr().err
    assert diagnostic in stderr
    assert "`atk wi show <ファイル名>`" in stderr.split("\n次の操作: ", maxsplit=1)[1]


@pytest.mark.parametrize("stale_section", ["wi_conditions", "user_requirements", "plan-requirements"])
def test_public_command_rejects_partly_updated_review_heads(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stale_section: str,
) -> None:
    """片側配列と計画由来の行が更新されていないことを、実Gitの別commitと比べて検出する。"""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        ).stdout.strip()

    git("init", "-q")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "旧対象")
    old = git("rev-parse", "HEAD")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "新対象")
    current = git("rev-parse", "HEAD")
    real_run = subprocess.run

    def fake_wi(args: list[str], **kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        if args[0] == "git":
            check = kwargs.pop("check", False)
            return real_run(args, check=check, **kwargs)
        assert not any(arg.startswith("--output-file") for arg in args)
        output = tmp_path / "generated-wi.stdout"
        output.write_text(
            f"### {FIRST_WI} [processing]\n---\ntype: awi\nsource: agent\n---\n## 完成条件\n- 完成\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout=f"保存先: {output}\n", stderr="")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_wi)
    evidence = tmp_path / "evidence.json"
    conditions = [{**_condition(FIRST_WI, "完成"), "reviewed_head": current}]
    requirements = [
        {**_requirement(FIRST_WI, "要求"), "reviewed_head": current},
        {**_requirement("", "計画だけの要求"), "reviewed_head": current},
    ]
    stale = {"wi_conditions": conditions[0], "user_requirements": requirements[0], "plan-requirements": requirements[1]}[
        stale_section
    ]
    stale["reviewed_head"] = old
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", str(evidence), FIRST_WI, "--expected-head", current],
    )
    _write_evidence(evidence, conditions, requirements)
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert current in error and old in error and "再判定" in error
    assert (FIRST_WI if stale_section != "plan-requirements" else "計画由来") in error

    for invalid in ("", "unknown-commit", "HEAD"):
        stale["reviewed_head"] = invalid
        _write_evidence(evidence, conditions, requirements)
        assert run_script.dispatch(args) == 1
        assert "reviewed_head" in capsys.readouterr().err
    stale.pop("reviewed_head")
    _write_evidence(evidence, conditions, requirements)
    assert run_script.dispatch(args) == 1
    assert "reviewed_head" in capsys.readouterr().err

    stale["reviewed_head"] = git("rev-parse", "--short=7", current)
    _write_evidence(evidence, conditions, requirements)
    assert run_script.dispatch(args) == 0
    assert not capsys.readouterr().err

    for row in [*conditions, *requirements]:
        row["reviewed_head"] = old
    args.script_args[-1] = old
    _write_evidence(evidence, conditions, requirements)
    assert run_script.dispatch(args) == 0
    assert not capsys.readouterr().err


_FENCED_REQUIREMENTS = ["一覧から保存できるようにして。", "保存後は`wi show`の表示を更新して。"]
_SUPPLEMENTS = {
    "backtick-plain": "```\nSystem.Xml.XmlException: 不正な値. at Lc.Config.Load() in C:\\lc\\Config.cs:line 12.\n```",
    "backtick-lang": '```text\nTraceback (most recent call last):\n  File "x.py", line 1. ValueError: bad.\n```',
    "tilde-ini": "~~~ini\n[launcher.hotkey]\nmodifier = ctrl. key = space.\n~~~",
    "nested-long": "`````\n```\n内側のフェンス. 終了.\n```\n外側の続き. 完了.\n`````",
}


def _fenced_body(route: str, supplement: str) -> tuple[str, list[dict[str, str]]]:
    """2つの要求の間に補足フェンスを置いたWI本文と、`完成条件証拠`の行を入力の種類ごとに返す。"""
    content = f"{_FENCED_REQUIREMENTS[0]}\n\n{supplement}\n\n{_FENCED_REQUIREMENTS[1]}\n"
    if route == "raw-awi":
        return f"type: awi\n---\n# 題\n\n{content}", []
    if route == "uwi-answer":
        return f"type: uwi\n---\n# 確認\n\n## 回答\n\n{content}", []
    conditions = "## 完成条件\n- 保存\n"
    if route == "user-comment":
        return f"type: awi\nsource: agent\n---\n{conditions}\n## ユーザーコメント\n\n{content}", [_condition(FIRST_WI, "保存")]
    # 通常AWIの逐語引用は外側の`text`フェンスを要求原文の容器とし、内側だけを補足として除く。
    container = "`" * 6
    quote = f"## ユーザー指摘の逐語引用\n\n{container}text\n{content}{container}\n"
    return f"type: awi\nsource: agent\n---\n{conditions}\n{quote}", [_condition(FIRST_WI, "保存")]


@pytest.mark.parametrize("route", ["raw-awi", "quoted-awi", "user-comment", "uwi-answer"])
@pytest.mark.parametrize("supplement", list(_SUPPLEMENTS))
def test_fenced_supplements_are_not_requirements(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    route: str,
    supplement: str,
) -> None:
    """補足フェンスの断片を要求に数えず、前後の要求は1件省くと不足として拒否する。"""
    body, conditions = _fenced_body(route, _SUPPLEMENTS[supplement])
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: body})
    path = tmp_path / "evidence.json"
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    _write_evidence(path, conditions, [_requirement(FIRST_WI, text) for text in _FENCED_REQUIREMENTS])
    assert run_script.dispatch(args) == 0, capsys.readouterr().err

    for omitted in _FENCED_REQUIREMENTS:
        kept = [_requirement(FIRST_WI, text) for text in _FENCED_REQUIREMENTS if text != omitted]
        _write_evidence(path, conditions, kept)
        assert run_script.dispatch(args) == 1
        error = capsys.readouterr().err
        assert f"不足: 「{omitted}」" in error and "期待 2 行" in error


def test_unclosed_fence_keeps_following_requirements(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """閉じていないフェンスは補足として除かず、後続の要求を証拠の対象に残す。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\n---\n# 題\n\n要求A。\n\n```\n要求B。\n"})
    path = tmp_path / "evidence.json"
    _write_evidence(path, [], [_requirement(FIRST_WI, "要求A。")])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 1


_UTTERANCE = "サイトの掲載を確かめて。止まると困るので一時障害の扱いを決めたい。"
_ANSWER = "一時障害は警告扱い (Recommended)"
_ANSWER_RECORD = (
    "質問: 共通前提: 掲載はdocs.python.orgで確かめます。\n"
    "TLS障害は9月から続いています。一時障害をどう扱いますか。\n"
    "選択肢: 一時障害は警告扱い (Recommended): 定期実行を止めずに警告だけを表示します。\n"
    "選択肢: 失敗扱い: 定期実行を失敗させます。\n"
    "選択肢: 確認を省く: 掲載を確かめません。\n"
    f"回答: {_ANSWER}\n"
)


def _answer_record_wi(answer_record: str) -> str:
    """ユーザーの発話の`text`フェンスと、確認回答の記録の`text`フェンスを逐語引用に持つAWI本文を返す。"""
    return (
        "type: awi\nsource: agent\n---\n# WI\n## 完成条件\n- 掲載を確かめる\n"
        "## ユーザー指摘の逐語引用\n\n"
        f"```text\n{_UTTERANCE}\n```\n\n```text\n{answer_record}```\n"
    )


def test_answer_record_requires_only_user_utterances_and_answer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """確認回答の記録の質問・共通前提・選択肢を要求単位に数えず、ユーザーの発話の各文と回答の値だけを求める。

    質問や選択肢を要求に数えると、担当はユーザーが述べていない文へ判定を書くか、記録を入力欠陥として返す。
    """
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _answer_record_wi(_ANSWER_RECORD)})
    path = tmp_path / "evidence.json"
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    conditions = [_condition(FIRST_WI, "掲載を確かめる")]
    utterances = ["サイトの掲載を確かめて。", "止まると困るので一時障害の扱いを決めたい。"]
    _write_evidence(path, conditions, [_requirement(FIRST_WI, text) for text in [*utterances, _ANSWER]])
    assert run_script.dispatch(args) == 0, capsys.readouterr().err

    _write_evidence(path, conditions, [_requirement(FIRST_WI, text) for text in utterances])
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert f"不足: 「{_ANSWER}」" in error
    assert "質問" not in error and "選択肢" not in error and "docs." not in error


def test_answer_record_keeps_multiline_answer_and_free_text(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """複数行に続く回答の値と自由記述の値を要求単位に残す。"""
    record = _ANSWER_RECORD.replace(f"回答: {_ANSWER}\n", "回答: 失敗扱い\n確認を省く\n自由記述: 夜間だけ止めて。\n")
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _answer_record_wi(record)})
    path = tmp_path / "evidence.json"
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    _write_evidence(path, [_condition(FIRST_WI, "掲載を確かめる")], [])
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert "「失敗扱い 確認を省く」、「夜間だけ止めて。」" in error


@pytest.mark.parametrize("route", ["raw-awi", "quoted-awi", "user-comment", "uwi-answer"])
def test_periods_inside_words_do_not_split_requirements(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    """語の内部のピリオドと文末の閉じ括弧では分割せず、空白が続くASCIIの文末では従来どおり分割する。"""
    requirements = [
        "docs.python.orgとpyproject.tomlの記載をv0.24.2に合わせて。",
        "`a. b`の表記は変えない（~/.sshは対象外。）",
        "Keep the old name.",
        "Add a new one!",
    ]
    content = f"{requirements[0]}{requirements[1]}\n{requirements[2]} {requirements[3]}\n"
    conditions: list[dict[str, str]] = []
    if route == "raw-awi":
        body = f"type: awi\n---\n# 題\n\n{content}"
    elif route == "uwi-answer":
        body = f"type: uwi\n---\n# 確認\n\n## 回答\n\n{content}"
    else:
        conditions = [_condition(FIRST_WI, "保存")]
        section = (
            f"## ユーザーコメント\n\n{content}"
            if route == "user-comment"
            else f"## ユーザー指摘の逐語引用\n\n```text\n{content}```\n"
        )
        body = f"type: awi\nsource: agent\n---\n## 完成条件\n- 保存\n\n{section}"
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: body})
    path = tmp_path / "evidence.json"
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    _write_evidence(path, conditions, [_requirement(FIRST_WI, text) for text in requirements])
    assert run_script.dispatch(args) == 0, capsys.readouterr().err

    _write_evidence(
        path, conditions, [_requirement(FIRST_WI, text) for text in [*requirements[:2], " ".join(requirements[2:])]]
    )
    assert run_script.dispatch(args) == 1
    assert "不足: 「Keep the old name.」、「Add a new one!」" in capsys.readouterr().err


_REFERENCE_MARKDOWN = (
    "# 設定  保存 ##\n## 括弧 (完了)\n\nSetext *見出し*\n====\n\n"
    "~~~~text\n# 偽見出し\n~~~~\n\n## 重複\n結果。\n## 重複\n別結果。\n"
)


@pytest.fixture(name="reference_repository")
def _reference_repository(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, str, pathlib.Path]:
    """実Gitと通常のWI取得を使い、対象版の見出し・行を確かめる公開コマンドの入力を用意する。"""
    repository = tmp_path / "target repo"
    notes = tmp_path / "private notes"
    (repository / "docs").mkdir(parents=True)
    (notes / "inbox").mkdir(parents=True)
    (repository / "docs/record.md").write_text(_REFERENCE_MARKDOWN, encoding="utf-8")
    for command in (
        ["init", "-q"],
        ["remote", "add", "origin", "https://github.com/example/foo.git"],
        ["add", "docs/record.md"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "観測記録"],
    ):
        subprocess.run(["git", "-C", str(repository), *command], capture_output=True, check=True, timeout=30)
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    ).stdout.strip()
    (notes / "inbox" / FIRST_WI).write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: awi\nsource: agent\n---\n# 題\n## 完成条件\n- 完成\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.chdir(repository)
    return repository, head, tmp_path / "evidence.json"


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("docs/record.md の結果を読んだ", 0),
        ("`docs/record.md#設定  保存`の結果を読んだ", 0),
        ("`docs/record.md#括弧 (完了)`の結果を読んだ", 0),
        ("[記録](docs/record.md#Setext *見出し*)の結果を読んだ", 0),
        ("`{repository}/docs/record.md#重複`の結果を読んだ", 0),
        ("docs/record.md:14 の最終行を読んだ", 0),
        ("docs/record.md:1-14 の全行を読んだ", 0),
        ("対象単位: 20260929-120000-001.md 要件原文「完成」。docs/record.md:1 で確認", 0),
        ("対象単位: `20260929-120000-001.md`。docs/record.md:1 で確認", 0),
        (LEGACY_UNIT_MARKER + "。docs/record.md:1 で確認", 0),
        ("`docs/record.md:1`は旧H2/H3へのリンク付き入口を保持する。", 0),
        ("`git diff abc1234 def5678 -- docs/record.md`で観測した。", 0),
        ("`uv run python tools/check.py`で観測した。", 0),
        ("`pytest tests/absent_test.py::test_save`で観測した。", 0),
        ("`git diff -- docs/record.md`で観測。docs/missing.md:1 を参照", 1),
        ("[記録](docs/absent file.md)で確認", 1),
        ("`docs/absent file.md`で確認", 1),
        ("`absent file.md`で確認", 1),
        ("20260929-120000-001.md を根拠ファイルとして確認", 1),
        ("docs/record.md:1-2,5-7 で確認", 1),
        ("docs/record.md#偽見出し で確認", 1),
        ("docs/record.md#設定 で確認", 1),
        ("docs/record.md#setting-save で確認", 1),
        ("missing.md で確認", 1),
        ("docs/missing.md で確認", 1),
        ("docs/record.md:0 で確認", 1),
        ("docs/record.md:-1 で確認", 1),
        ("docs/record.md:15 で確認", 1),
        ("docs/record.md:2-1 で確認", 1),
        ("docs/record.md:1-15 で確認", 1),
        ("docs/record.md:1- で確認", 1),
        ("https://example.test/missing.md で公開結果を確認", 0),
        ("test_save: 成功", 0),
        ("条件に対応する観測の結果", 0),
    ],
)
def test_public_command_resolves_evidence_references(
    reference_repository: tuple[pathlib.Path, str, pathlib.Path],
    reference: str,
    expected: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対象commitのファイル・見出し・行の境界を公開コマンドで確かめ、パスのない成功結果と自由文を誤拒否しない。"""
    repository, head, evidence = reference_repository
    row = {
        **_condition(FIRST_WI, "完成"),
        "reviewed_head": head,
        "evidence": reference.format(repository=repository, source=FIRST_WI),
    }
    _write_evidence(evidence, [row])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI, "--expected-head", head]
    )
    assert run_script.dispatch(args) == expected
    error = capsys.readouterr().err
    if expected:
        assert "wi_conditions[1].evidence" in error
        assert "参照『" in error and "証拠不足へ再判定" in error
        if ":1-2,5-7" in reference:
            assert "範囲ごとにパスを再記載" in error
    else:
        assert not error


@pytest.mark.parametrize("layout", ["single", "outside-selection", "other-array", "plan"])
def test_reference_checks_every_achieved_row(
    reference_repository: tuple[pathlib.Path, str, pathlib.Path], layout: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """共用しない参照も、指定集合外・両配列・計画由来の達成行まで拒否する。"""
    _, head, evidence = reference_repository
    good = {**_condition(FIRST_WI, "完成"), "reviewed_head": head, "evidence": "test_complete: 成功"}
    invalid = {
        "single": _condition(FIRST_WI, "完成"),
        "outside-selection": _condition(SECOND_WI, "別条件"),
        "other-array": _requirement(FIRST_WI, "別要求"),
        "plan": _requirement("", "計画要求"),
    }[layout]
    invalid.update(reviewed_head=head, evidence="docs/absent.md の該当入力を確認")
    conditions = [invalid] if layout == "single" else [good]
    if layout == "outside-selection":
        conditions.append(invalid)
    _write_evidence(evidence, conditions, [invalid] if layout in {"other-array", "plan"} else [])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI, "--expected-head", head]
    )
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert "docs/absent.md" in error and (invalid["awi"] or "計画由来") in error


def test_reference_uses_reviewed_commit_and_external_records(
    reference_repository: tuple[pathlib.Path, str, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """現在本文だけの見出しを拒否し、対象版の参照とGit管理外の実際の記録を受理する。"""
    repository, head, evidence = reference_repository
    record = repository / "docs/record.md"
    record.write_text("# 現在本文だけ\n" * 20, encoding="utf-8")
    outside = repository.parent / "外部記録.md"
    outside.write_text("# 外部観測\n結果。\n", encoding="utf-8")
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI, "--expected-head", head]
    )
    for reference, expected in (
        ("docs/record.md#現在本文だけ", 1),
        ("docs/record.md:20", 1),
        ("docs/record.md#重複", 0),
        (f"{outside}#外部観測", 0),
        (f"{outside}:2", 0),
        (f"{outside}#不実在", 1),
        (f"{outside}:3", 1),
        (str(repository.parent / "不実在.md"), 1),
    ):
        row = {**_condition(FIRST_WI, "完成"), "reviewed_head": head, "evidence": f"`{reference}`の結果を確認"}
        _write_evidence(evidence, [row])
        assert run_script.dispatch(args) == expected, capsys.readouterr().err
    record.unlink()
    row.update(evidence="docs/record.md#重複 で対象版を確認")
    _write_evidence(evidence, [row])
    assert run_script.dispatch(args) == 0, capsys.readouterr().err


@pytest.mark.parametrize("outcome", ["未達", "証拠不足"])
def test_nonachieved_missing_reference_is_a_valid_reason(
    reference_repository: tuple[pathlib.Path, str, pathlib.Path], outcome: str
) -> None:
    """参照が無いという未達の説明へ、達成根拠の所在を要求しない。"""
    _, head, evidence = reference_repository
    row = {**_condition(FIRST_WI, "完成"), "reviewed_head": head, "outcome": outcome, "evidence": "docs/absent.md は未作成"}
    _write_evidence(evidence, [row])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI, "--expected-head", head]
    )
    assert run_script.dispatch(args) == 0


def test_public_command_runs_platform_launcher(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """subprocessを置き換えず、OS別のランチャーから空白を含むパスのWIを取得し、証拠の過不足を判定する。"""
    root = tmp_path / "work dir"
    notes = root / "private notes"
    repository = root / "target repo"
    (notes / "inbox").mkdir(parents=True)
    repository.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        ).stdout.strip()

    git("init", "-q")
    git("remote", "add", "origin", "https://github.com/example/foo.git")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "対象")
    head = git("rev-parse", "HEAD")
    body = f"# 題\n\n{_FENCED_REQUIREMENTS[0]}\n\n{_SUPPLEMENTS['backtick-plain']}\n\n{_FENCED_REQUIREMENTS[1]}\n"
    (notes / "inbox" / FIRST_WI).write_text(
        f"---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n{body}", encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.chdir(repository)
    path = root / "evidence.json"
    rows = [{**_requirement(FIRST_WI, text), "reviewed_head": head} for text in _FENCED_REQUIREMENTS]
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", head]
    )
    _write_evidence(path, [], rows)
    assert run_script.dispatch(args) == 0, capsys.readouterr().err

    _write_evidence(path, [], rows[:1])
    assert run_script.dispatch(args) == 1
    assert f"不足: 「{_FENCED_REQUIREMENTS[1]}」" in capsys.readouterr().err


_TEMPLATE_BODIES = {
    FIRST_WI: (
        "type: awi\nsource: agent\n---\n# WI\n"
        "## 完成条件\n- 保存できる\n- 再読込後も保持する\n"
        "## ユーザー指摘の逐語引用\n出所: 会話\n\n```text\n設定を移して。旧入口を廃止して。\n```\n"
        "## ユーザーコメント\n- 案内も直して。\n"
    ),
    SECOND_WI: "type: awi\n---\n# 題\n\n検索範囲を変更して。\n\n## 処理結果\n\n- 採否: adopted\n",
    "20260928-192559-003.md": "type: uwi\n---\n## 質問\n\nどうしますか？\n\n## 回答\n\n<!-- 案内 -->\nmediumのまま雛形で補助\n",
}


def _template(path: pathlib.Path, *wi: str) -> int:
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", "--template", str(path), *wi])
    return run_script.dispatch(args)


def _check(path: pathlib.Path, *wi: str) -> int:
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), *wi, "--expected-head", REVIEWED_HEAD]
    )
    return run_script.dispatch(args)


def _judge_all(path: pathlib.Path) -> dict[str, list[dict[str, str]]]:
    """雛形の全行を担当が判定した状態へ置き換え、行ごとに異なる根拠を記入する。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    for section in ("wi_conditions", "user_requirements"):
        for index, row in enumerate(data[section], start=1):
            row.update(outcome="達成", evidence=f"{section}の{index}行目を観測した結果", reviewed_head=REVIEWED_HEAD)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def test_template_writes_every_expected_row(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """完成条件、逐語引用、ユーザーコメント、原文AWI、UWI回答の全単位を原文と出所付きで、判定欄を空にして出力する。"""
    _mock_wi(monkeypatch, tmp_path, _TEMPLATE_BODIES)
    path = tmp_path / "evidence.json"
    wis = list(_TEMPLATE_BODIES)
    assert _template(path, *wis) == 0
    output = capsys.readouterr().out
    assert "追加 7 行" in output and "\n次の操作: " in output
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [(row["awi"], row["condition"], row["source"]) for row in data["wi_conditions"]] == [
        (FIRST_WI, "保存できる", f"{FIRST_WI}#完成条件 1"),
        (FIRST_WI, "再読込後も保持する", f"{FIRST_WI}#完成条件 2"),
    ]
    assert [(row["awi"], row["requirement"], row["origin"]) for row in data["user_requirements"]] == [
        (FIRST_WI, "設定を移して。", f"{FIRST_WI}#ユーザー指摘の逐語引用 ブロック1"),
        (FIRST_WI, "旧入口を廃止して。", f"{FIRST_WI}#ユーザー指摘の逐語引用 ブロック1"),
        (FIRST_WI, "案内も直して。", f"{FIRST_WI}#ユーザーコメント"),
        (SECOND_WI, "検索範囲を変更して。", f"{SECOND_WI}#本文"),
        ("20260928-192559-003.md", "mediumのまま雛形で補助", "20260928-192559-003.md#回答"),
    ]
    for row in [*data["wi_conditions"], *data["user_requirements"]]:
        assert (row["outcome"], row["evidence"], row["reviewed_head"]) == ("", "", "")


def test_unfilled_template_is_rejected_until_each_row_is_judged(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """雛形のまま、または判定とHEADだけを埋めて根拠を欠く証拠を拒否し、全欄の記入後に受理する。"""
    _mock_wi(monkeypatch, tmp_path, _TEMPLATE_BODIES)
    path = tmp_path / "evidence.json"
    wis = list(_TEMPLATE_BODIES)
    assert _template(path, *wis) == 0
    capsys.readouterr()

    assert _check(path, *wis) == 1
    error = capsys.readouterr().err
    assert error.count("判定が未記入") == 7 and error.count("根拠が未記入") == 7

    data = json.loads(path.read_text(encoding="utf-8"))
    for row in [*data["wi_conditions"], *data["user_requirements"]]:
        row.update(outcome="達成", reviewed_head=REVIEWED_HEAD)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert _check(path, *wis) == 1
    error = capsys.readouterr().err
    assert "判定が未記入" not in error and error.count("根拠が未記入") == 7

    _judge_all(path)
    assert _check(path, *wis) == 0, capsys.readouterr().err


def test_template_preserves_existing_rows_and_adds_only_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """再レビューで記入済みの行と他の行を保ち、不足行だけを追加し、2回目は何も追加しない。"""
    _mock_wi(monkeypatch, tmp_path, _TEMPLATE_BODIES)
    path = tmp_path / "evidence.json"
    judged = {**_condition(FIRST_WI, "- 再読込後も保持する"), "evidence": "記入済みの根拠"}
    plan_row = {**_requirement("", "計画だけの要求"), "evidence": "計画の観測"}
    _write_evidence(path, [judged], [plan_row])

    assert _check(path, FIRST_WI) == 1
    assert f"--template {path} {FIRST_WI}" in capsys.readouterr().err

    assert _template(path, FIRST_WI) == 0
    assert "追加 4 行、既存 2 行を保持" in capsys.readouterr().out
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["wi_conditions"][0] == judged and data["user_requirements"][0] == plan_row
    assert [row["condition"] for row in data["wi_conditions"]] == ["- 再読込後も保持する", "保存できる"]

    before = path.read_text(encoding="utf-8")
    assert _template(path, FIRST_WI) == 0
    assert "追加 0 行" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "content",
    ["{", '{"wi_conditions": {}, "user_requirements": []}', '{"wi_conditions": [{"awi": 1}], "user_requirements": []}'],
)
def test_template_keeps_invalid_evidence_untouched(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    """読めない既存の証拠へは書き込まず、診断と次の操作を返す。"""
    _mock_wi(monkeypatch, tmp_path, _TEMPLATE_BODIES)
    path = tmp_path / "evidence.json"
    path.write_text(content, encoding="utf-8")
    assert _template(path, FIRST_WI) == 1
    assert "\n次の操作: `完成条件証拠`は変更していない" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("with_plan", [False, True])
def test_wi_template_from_uncreated_output_reaches_return_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], with_plan: bool
) -> None:
    """WIだけと計画とWIの入力で、WI本文の取得後に未作成の出力先へ雛形を作成し、判定の記入後に返却を生成できる。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 保存\n"})
    path, table = tmp_path / "evidence.json", tmp_path / "plan.exec-review.tsv"
    plan = tmp_path / "plan.md"
    plan.write_text(f"# 計画\n\n- 関連WI:\n  - {FIRST_WI}: 保存\n", encoding="utf-8")
    plan_args = ["--plan", str(plan)] if with_plan else []
    assert not path.exists()
    template = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--template", str(path), FIRST_WI, *plan_args]
    )
    assert run_script.dispatch(template) == 0
    rows = json.loads(path.read_text(encoding="utf-8"))["wi_conditions"]
    assert [row["condition"] for row in rows] == ["保存"] and rows[0]["outcome"] == ""
    filled = _condition(FIRST_WI, "保存")
    filled.update(source=rows[0]["source"], evidence="test_save_settings 成功")
    _write_evidence(path, [filled])
    review_table.init(table)
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table, *plan_args)) == 0
    assert "未解決の指摘数: 0" in capsys.readouterr().out


def test_input_record_saved_outside_repository_is_resolved_by_template_and_return(
    tmp_path: pathlib.Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """担当がmanaged-tempへ保存したCI記録の入力記録を、雛形の生成と返却の生成の双方が解決できる。"""
    _mock_wi(monkeypatch, tmp_path, {})
    record = tmp_path_factory.mktemp("managed-temp") / "ci-record.md"
    record.write_text("# CI記録\n\n原因分析結果: lintの失敗\n修正認可根拠: CI失敗の修正\n", encoding="utf-8")
    path, table = tmp_path / "evidence.json", tmp_path / "ci.exec-review.tsv"
    template = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--template", str(path), "--input-record", str(record)]
    )
    assert run_script.dispatch(template) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {"wi_conditions": [], "user_requirements": []}
    review_table.init(table)
    review_table.add(table, "2", "exec-review", "lint", "設定の誤りを直す", "実装")
    capsys.readouterr()
    assert run_script.dispatch(_no_evidence_return_args(table, "--input-record", str(record))) == 0
    assert capsys.readouterr().out == f"状態: completed\nレビューしたHEAD: {REVIEWED_HEAD}\n未解決の指摘数: 1\n"


_OBSERVATION = "リリース直後はAPIエラー？"
_OBSERVATION_TAIL = "が頻発して手動で再開させてた（「止まってたので再開して」と送ってた）けど、最近は減った。"
_REQUEST = "過負荷なら自動で再試行してほしい。"
_BACKGROUND_REASON = "過去の観測を伝える文で、要求・制約・選好・回答を求める問いを含まない"


def _background_awi(record: str) -> str:
    """過去の観測2文と要求1文を逐語引用し、`## 反映内容と反映先`へ分類の記録を持つAWI本文を返す。"""
    return (
        "type: awi\nsource: process-wi\n---\n# WI\n"
        f"## 反映内容と反映先\n\n- 「{_REQUEST}」は本AWIで扱う\n{record}\n"
        "## 完成条件\n- 過負荷の後に同じsessionで続く\n"
        "## ユーザー指摘の逐語引用\n出所: 会話\n\n"
        f"```text\n{_OBSERVATION}{_OBSERVATION_TAIL}{_REQUEST}\n```\n"
    )


_ELIDED_RECORD = (
    f"- 「{_OBSERVATION}が頻発して手動で再開させてた（…）けど、最近は減った。」は背景の観測。本AWIの完成条件に含めない\n"
)


def _background_rows(source: str, evidence: str = f"分類の記録どおり、{_BACKGROUND_REASON}") -> list[dict[str, str]]:
    return [
        {**_requirement(FIRST_WI, unit), "outcome": "背景", "source": source, "evidence": evidence}
        for unit in (_OBSERVATION, _OBSERVATION_TAIL)
    ] + [_requirement(FIRST_WI, _REQUEST)]


def test_background_rows_from_template_are_accepted_while_request_stays_judged(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """雛形は観測文も原文行として残し、分類の記録が中略付きで覆う観測文を背景として受理する。

    要求の行は通常の判定に残り、証拠不足なら未解決0件の返却は従来どおり拒否される。
    """
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _background_awi(_ELIDED_RECORD)})
    path = tmp_path / "evidence.json"
    assert _template(path, FIRST_WI) == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [row["requirement"] for row in data["user_requirements"]] == [_OBSERVATION, _OBSERVATION_TAIL, _REQUEST]
    source = f"{FIRST_WI} ## 反映内容と反映先"
    _write_evidence(path, [_condition(FIRST_WI, "過負荷の後に同じsessionで続く")], _background_rows(source))
    capsys.readouterr()
    assert _check(path, FIRST_WI) == 0, capsys.readouterr().err

    table = tmp_path / "plan.exec-review.tsv"
    review_table.init(table)
    capsys.readouterr()
    assert run_script.dispatch(_return_args(path, table)) == 0, capsys.readouterr().err
    assert "未解決の指摘数: 0" in capsys.readouterr().out

    rows = _background_rows(source)
    rows[2].update(outcome="証拠不足", evidence="自動再試行の観測をまだ得ていない")
    _write_evidence(path, [_condition(FIRST_WI, "過負荷の後に同じsessionで続く")], rows)
    assert run_script.dispatch(_return_args(path, table)) == 1
    assert "user_requirements[3]" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("record", "source", "evidence", "diagnostic"),
    [
        # 分類の記録の所在を持たない行。
        (_ELIDED_RECORD, "WI本文", None, "分類の記録を特定できません"),
        # 存在しないレビュー指摘管理表を指す行。
        (_ELIDED_RECORD, "/nonexistent/plan.exec-review.tsv", None, "分類の記録を特定できません"),
        # 記録の背景の引用が観測の文だけを覆い、背景とした要求の文を覆っていない行。
        (
            f"- 「{_OBSERVATION}」は背景の観測\n",
            "{wi} ## 反映内容と反映先",
            None,
            "原文の範囲を位置参照または旧引用で示していません",
        ),
        # 記録の行に「背景」が無く、割当などの別の扱いを記録した行。
        (
            f"- 「{_OBSERVATION}」は別AWIへ割当\n",
            "{wi} ## 反映内容と反映先",
            None,
            "原文の範囲を位置参照または旧引用で示していません",
        ),
        # 記録を指す参照だけで、要求を含まない理由を持たない行。
        (_ELIDED_RECORD, "{wi} ## 反映内容と反映先", "{record_file}", "要求を含まない理由がありません"),
    ],
)
def test_background_without_record_range_or_reason_is_rejected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record: str,
    source: str,
    evidence: str | None,
    diagnostic: str,
) -> None:
    """記録の所在、原文の範囲との対応、理由のいずれかを欠く背景行を、対象行と直し方を伴って拒否する。

    受理すると、要求の文や根拠の無い文まで達成の要求から外れ、統合時の読解まで残る。
    """
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _background_awi(record)})
    path = tmp_path / "evidence.json"
    rows = _background_rows(source.format(wi=FIRST_WI))
    if evidence is not None:
        record_file = tmp_path / "record.md"
        record_file.write_text(record, encoding="utf-8")
        rows[0]["evidence"] = evidence.format(record_file=record_file)
    if "原文の範囲" in diagnostic:
        rows[0]["requirement"] = _REQUEST
        rows[0]["outcome"] = "背景"
        rows[2] = _requirement(FIRST_WI, _OBSERVATION)
    _write_evidence(path, [_condition(FIRST_WI, "過負荷の後に同じsessionで続く")], rows)
    assert _check(path, FIRST_WI) == 1
    line = next(line for line in capsys.readouterr().err.splitlines() if "user_requirements[1]" in line)
    assert line.startswith(f"失敗: {FIRST_WI}: user_requirements[1].") and diagnostic in line


def test_background_condition_is_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """完成条件はWI自身の達成対象であるため、背景を受理値に含めない。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _background_awi(_ELIDED_RECORD)})
    path = tmp_path / "evidence.json"
    condition = {**_condition(FIRST_WI, "過負荷の後に同じsessionで続く"), "outcome": "背景"}
    _write_evidence(path, [condition], _background_rows(f"{FIRST_WI} ## 反映内容と反映先"))
    assert _check(path, FIRST_WI) == 1
    assert "wi_conditions[1].outcome: 未知の判定です: 背景（受理する値: " in capsys.readouterr().err


@pytest.mark.parametrize("bad_reference", [None, "block", "range", "zero", "reverse", "overflow", "malformed", "reason"])
def test_background_quote_position_resolves_original_scope(
    bad_reference: str | None,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """原文を再掲せず公開チェックへ参照を渡し、存在・範囲・理由を検証する。"""
    observed = _OBSERVATION + _OBSERVATION_TAIL
    reference = f"逐語引用 text[1] 文字1-{len(observed)}"
    if bad_reference == "block":
        reference = f"逐語引用 text[3] 文字1-{len(observed)}"
    elif bad_reference == "range":
        reference = f"逐語引用 text[1] 文字{len(observed) + 1}-{len(observed + _REQUEST)}"
    elif bad_reference == "zero":
        reference = f"逐語引用 text[1] 文字0-{len(observed)}"
    elif bad_reference == "reverse":
        reference = "逐語引用 text[1] 文字2-1"
    elif bad_reference == "overflow":
        reference = "逐語引用 text[1] 文字1-9999"
    elif bad_reference == "malformed":
        reference += "と逐語引用 text[1] 文字不明"
    record = f"- `{reference}`は背景。{_BACKGROUND_REASON}。本AWIの完成条件に含めない\n"
    other = "昨日のログを取得した。"
    body = _background_awi(record).replace(f"- 「{_REQUEST}」は本AWIで扱う", "- 継続要求は本AWIで扱う")
    body += f"\n```text\n{other}\n```\n"
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: body})
    source = f"{FIRST_WI} ## 反映内容と反映先"
    rows = _background_rows(source)
    for row in rows:
        row["origin"] = f"{FIRST_WI}#ユーザー指摘の逐語引用 ブロック1"
    rows.append({**_requirement(FIRST_WI, other), "origin": f"{FIRST_WI}#ユーザー指摘の逐語引用 ブロック2"})
    if bad_reference == "reason":
        rows[0]["evidence"] = ""
    path = tmp_path / "evidence.json"
    _write_evidence(path, [_condition(FIRST_WI, "過負荷の後に同じsessionで続く")], rows)

    assert _check(path, FIRST_WI) == (0 if bad_reference is None else 1)
    diagnostic = capsys.readouterr().err
    if bad_reference:
        assert "user_requirements[1]" in diagnostic
    else:
        assert not diagnostic


def test_quote_position_does_not_cover_identical_text_in_other_block(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ文字列でも異なる発話ブロックへの参照を、その行の背景根拠として受理しない。"""
    record = f"- `逐語引用 text[2] 文字1-{len(_OBSERVATION)}`は背景。{_BACKGROUND_REASON}\n"
    body = _background_awi(record) + f"\n```text\n{_OBSERVATION}\n```\n"
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: body})
    rows = _background_rows(f"{FIRST_WI} ## 反映内容と反映先")
    rows[0]["origin"] = f"{FIRST_WI}#ユーザー指摘の逐語引用 ブロック1"
    rows.append({**_requirement(FIRST_WI, _OBSERVATION), "origin": f"{FIRST_WI}#ユーザー指摘の逐語引用 ブロック2"})
    path = tmp_path / "evidence.json"
    _write_evidence(path, [_condition(FIRST_WI, "過負荷の後に同じsessionで続く")], rows)
    assert _check(path, FIRST_WI) == 1
    assert "user_requirements[1]" in capsys.readouterr().err


@pytest.mark.parametrize("route", ["plan", "review-table"])
def test_background_record_in_plan_or_review_table_is_accepted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    """起草時の記録が無い古いAWIでも、計画の実施内容か実装着手後のレビュー指摘管理表へ補った記録で受理する。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: _background_awi("")})
    record = _ELIDED_RECORD.removeprefix("- ").strip()
    if route == "plan":
        plan = tmp_path / "計画 レーン01.md"
        plan.write_text(f"## 実施内容\n\n| 実施 | 由来 | 採否 | {record} |\n\n## 検証\n", encoding="utf-8")
        source = f"{plan} の ## 実施内容"
    else:
        table = tmp_path / "plan.exec-review.tsv"
        review_table.init(table)
        review_table.add(table, "1", "exec-review", "逐語引用ブロック1", record, "詳細")
        source = f"{table} round 1"
    path = tmp_path / "evidence.json"
    _write_evidence(path, [_condition(FIRST_WI, "過負荷の後に同じsessionで続く")], _background_rows(source))
    capsys.readouterr()
    assert _check(path, FIRST_WI) == 0, capsys.readouterr().err
