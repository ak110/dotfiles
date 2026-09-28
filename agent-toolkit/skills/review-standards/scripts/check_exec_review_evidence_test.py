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


def _condition(awi: str) -> dict[str, str]:
    return {
        "awi": awi,
        "condition": "利用者が結果を確認できる",
        "outcome": "達成",
        "source": "WI本文",
        "evidence": "実行結果",
    }


def _write_evidence(path: pathlib.Path, conditions: list[dict[str, str]]) -> None:
    path.write_text(
        json.dumps({"wi_conditions": conditions, "user_requirements": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_public_command_accepts_bullets_and_paragraph(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開入口で複数箇条書きと段落一件の条件数を判定する。"""
    evidence = tmp_path / "evidence.json"
    _write_evidence(evidence, [_condition(FIRST_WI), _condition(FIRST_WI), _condition(SECOND_WI)])

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{tmp_path}\n", stderr="")
        filename = args[3]
        body = "- 第一条件\n- 第二条件" if filename == FIRST_WI else "段落の完成条件。"
        return subprocess.CompletedProcess(
            args, 0, stdout=f"## target_repo: example\n### {filename} [processing]\n# WI\n## 完成条件\n\n{body}\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", str(evidence), FIRST_WI, SECOND_WI],
    )
    assert run_script.dispatch(args) == 0


def test_reports_every_wi_with_missing_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """WIが複数でも不足した全件を一度で返す。"""
    evidence = tmp_path / "evidence.json"
    _write_evidence(evidence, [])

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{tmp_path}\n", stderr="")
        filename = args[3]
        return subprocess.CompletedProcess(
            args, 0, stdout=f"### {filename} [processing]\n## 完成条件\n- 第一条件\n- 第二条件\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    args = argparse.Namespace(
        script_name="exec-review-evidence-check",
        script_args=["--", str(evidence), FIRST_WI, SECOND_WI],
    )
    assert run_script.dispatch(args) == 1
    error = capsys.readouterr().err
    assert FIRST_WI in error and SECOND_WI in error
    assert error.count("期待 2 行、実数 0 行") == 2


@pytest.mark.parametrize(
    ("content", "diagnostic"),
    [
        ("{", "証拠JSONを読めません"),
        ('{"wi_conditions": {}, "user_requirements": []}', "wi_conditions: 配列が必要"),
        ('{"wi_conditions": [{"awi": 1}], "user_requirements": []}', "wi_conditions[1].awi: 文字列が必要"),
        (
            json.dumps(
                {"wi_conditions": [{**_condition(FIRST_WI), "outcome": "保留"}], "user_requirements": []},
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
