"""実行レビュー証拠の公開検査コマンドを検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import pytest

from agent_toolkit._atk import run_script

FIRST_WI = "20260928-192559-001.md"
SECOND_WI = "20260928-192559-002.md"


def _condition(awi: str, condition: str) -> dict[str, str]:
    return {
        "awi": awi,
        "condition": condition,
        "outcome": "達成",
        "source": "WI本文",
        "evidence": "実行結果",
    }


def _requirement(awi: str, requirement: str) -> dict[str, str]:
    return {
        "awi": awi,
        "requirement": requirement,
        "origin": "WI本文",
        "outcome": "達成",
        "source": "WI本文",
        "evidence": "実行結果",
    }


def _write_evidence(
    path: pathlib.Path, conditions: list[dict[str, str]], requirements: list[dict[str, str]] | None = None
) -> None:
    path.write_text(
        json.dumps({"wi_conditions": conditions, "user_requirements": requirements or []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _mock_wi(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, bodies: dict[str, str]) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{tmp_path}\n", stderr="")
        filename = args[3]
        output = pathlib.Path(next(arg for arg in args if arg.startswith("--output-file=")).removeprefix("--output-file="))
        output.write_text(f"## target_repo: example\n### {filename} [processing]\n---\n{bodies[filename]}", encoding="utf-8")
        # エージェント環境の`atk`は長い本文を標準出力へ書かないため、検査は保存先だけを読む必要がある。
        return subprocess.CompletedProcess(args, 0, stdout=f"保存先: {output}\n行数: 1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_public_command_accepts_bullets_and_paragraph(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開入口で複数箇条書きと段落一件の条件数を判定する。"""
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
        script_args=["--", str(evidence), FIRST_WI, SECOND_WI],
    )
    assert run_script.dispatch(args) == 0


def test_public_command_rejects_numbered_condition_instead_of_original(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """行数が一致しても原文との対応が無ければ公開入口で拒否する。"""
    evidence = tmp_path / "evidence.json"
    _mock_wi(
        monkeypatch,
        tmp_path,
        {FIRST_WI: "type: awi\nsource: agent\n---\n## 完成条件\n- 第一条件\n- 第二条件\n"},
    )
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI])

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
        script_args=["--", str(evidence), FIRST_WI, SECOND_WI],
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
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI])

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
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI])

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
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI])
    unassigned = {**_requirement(FIRST_WI, whole), "outcome": "割当外", "evidence": "分割元の依頼全体"}

    _write_evidence(evidence, [_condition(FIRST_WI, "設定画面で保存できる")], [_requirement(FIRST_WI, own), unassigned])
    assert run_script.dispatch(args) == 0

    _write_evidence(
        evidence,
        [{**_condition(FIRST_WI, "設定画面で保存できる"), "outcome": "割当外"}],
        [_requirement(FIRST_WI, own), unassigned],
    )
    assert run_script.dispatch(args) == 1
    assert "wi_conditions[1].outcome: 未知の判定です: 割当外" in capsys.readouterr().err


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
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", str(evidence), SECOND_WI])

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
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI])
    assert run_script.dispatch(args) == 1
    assert diagnostic in capsys.readouterr().err


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
    args = argparse.Namespace(script_name="exec-review-evidence-check", script_args=["--", str(evidence), FIRST_WI])
    assert run_script.dispatch(args) == 1
    assert diagnostic in capsys.readouterr().err
