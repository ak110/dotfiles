"""公開run-scriptから未判定記録を生成・再生成・根拠更新する契約を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import entries, repo, sync

WI = "20261008-043514-001.md"


def _run(*args: str, name: str = "verification-record") -> int:
    return run_script.dispatch(argparse.Namespace(script_name=name, script_args=list(args)))


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


@pytest.mark.parametrize("mode", ["wi", "plan-wi", "plan-user"])
def test_public_record_roundtrip(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """入力形ごとの原文集合が雛形と一致し、同文別出所の一方だけを更新・保持できる。"""
    _wi(monkeypatch, tmp_path)
    record, template = tmp_path / "record.json", tmp_path / "template.json"
    plan = _plan(tmp_path, mode)
    args = ["--wi", WI] if mode == "wi" else ["--plan", str(plan)]
    assert _run("--output", str(record), *args) == 0
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
    assert _run("--output", str(record), *args) == 0
    final = json.loads(record.read_text(encoding="utf-8"))
    expected = first
    expected[chosen["section"]][1]["evidence"] = "別条件で再確認"
    assert final == expected


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
