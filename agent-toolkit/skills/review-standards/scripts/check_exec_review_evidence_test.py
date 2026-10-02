"""公開コマンドで実行レビュー証拠が基準を満たすか判定する動作を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import typing

import pytest

from agent_toolkit._atk import run_script

FIRST_WI = "20260928-192559-001.md"
SECOND_WI = "20260928-192559-002.md"
REVIEWED_HEAD = "a" * 40


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

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if args[0] == "git":
            value = str(tmp_path) if "--show-toplevel" in args else REVIEWED_HEAD
            return subprocess.CompletedProcess(args, 0, stdout=f"{value}\n", stderr="")
        filename = args[3]
        requested.append(filename)
        if filename not in bodies:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr=f"失敗: {filename}はありません")
        output = pathlib.Path(next(arg for arg in args if arg.startswith("--output-file=")).removeprefix("--output-file="))
        output.write_text(f"## target_repo: example\n### {filename} [processing]\n---\n{bodies[filename]}", encoding="utf-8")
        # エージェント環境の`atk`は長い本文を標準出力へ書かないため、証拠を確かめる処理は保存先だけを読む必要がある。
        return subprocess.CompletedProcess(args, 0, stdout=f"保存先: {output}\n行数: 1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return requested


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
    record.write_text("# 設定保存\n保存と再読込が成功した。\n", encoding="utf-8")
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
    """公開待ちや不採用の理由を共有しても達成根拠の共用には含めない。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\n---\n## 完成条件\n- 保存\n"})
    first = {**_requirement("", "保存"), "outcome": outcome, "evidence": "公開工程待ち"}
    second = {**_requirement("", "再読込"), "outcome": outcome, "evidence": "公開工程待ち"}
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
    """原文AWIの文とユーザーコメントを対象WIの証拠行へ結び付ける。"""
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
    comment = "利用者向けの案内も直して。"
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
        monkeypatch,
        tmp_path,
        {
            FIRST_WI: (
                "type: awi\nsource: agent\n---\n# WI\n"
                "## 完成条件\n- 設定画面で保存できる\n"
                "## ユーザー指摘の逐語引用\n出所: 会話\n\n"
                f"```text\n{own}{whole}\n```\n"
            )
        },
    )
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", "--expected-head", REVIEWED_HEAD, str(evidence), FIRST_WI]
    )
    unassigned = {**_requirement(FIRST_WI, whole), "outcome": "割当外", "evidence": "分割元の依頼全体"}

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
        ("{", "証拠JSONを読めません"),
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
        output = pathlib.Path(next(arg.removeprefix("--output-file=") for arg in args if arg.startswith("--output-file=")))
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
    """2つの要求の間に補足フェンスを置いたWI本文と、完成条件の証拠行を入力の種類ごとに返す。"""
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
        assert f"不足: {omitted}" in error and "期待 2 行" in error


def test_unclosed_fence_keeps_following_requirements(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """閉じていないフェンスは補足として除かず、後続の要求を証拠の対象に残す。"""
    _mock_wi(monkeypatch, tmp_path, {FIRST_WI: "type: awi\n---\n# 題\n\n要求A。\n\n```\n要求B。\n"})
    path = tmp_path / "evidence.json"
    _write_evidence(path, [], [_requirement(FIRST_WI, "要求A。")])
    args = argparse.Namespace(
        script_name="exec-review-evidence-check", script_args=["--", str(path), FIRST_WI, "--expected-head", REVIEWED_HEAD]
    )
    assert run_script.dispatch(args) == 1


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
    assert f"不足: {_FENCED_REQUIREMENTS[1]}" in capsys.readouterr().err


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
    assert "\n次の操作: 証拠JSONは変更していない" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == content
