"""review_eval.pyの公開コマンドを通した準備と採点の試験。"""

from __future__ import annotations

import json
import pathlib
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import review_eval
from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._atk import review_table
from agent_toolkit._common.next_action import ActionableError


def _write_cases(path: pathlib.Path) -> pathlib.Path:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "missed",
                        "kind": "見逃し",
                        "role": "exec-review",
                        "repo": "target",
                        "plan": "plans/2026/10/01-0000_case",
                        "description": "見逃し事例",
                        "expect": {
                            "detect": [{"id": "policy", "description": "方針違反", "patterns": ["本文.{0,10}配送", "方針"]}],
                            "absent": [{"id": "noise", "description": "誤指摘", "patterns": ["バックスラッシュ"]}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _table(path: pathlib.Path, issues: list[str]) -> pathlib.Path:
    review_table.init(path)
    for index, issue in enumerate(issues):
        review_table.add(path, "1", "exec-review", f"file{index}.py", issue)
    return path


def _prepared_table(path: pathlib.Path, issues: list[str], capsys: pytest.CaptureFixture[str]) -> pathlib.Path:
    """管理表を作り、作成時の成功行を採点結果の出力から除く。"""
    _table(path, issues)
    capsys.readouterr()
    return path


def _results(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_score_counts_detected_false_and_unclassified(tmp_path, monkeypatch, capsys) -> None:
    """全ての照合語句が同じ行に一致した期待だけを検出とし、出すべきでない指摘を誤指摘、残りを未分類として数える。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    table = _prepared_table(
        tmp_path / "case.exec-review.tsv",
        ["hookが本文を追加コンテキストへ配送しており方針に反する", "バックスラッシュの戻り先", "無関係な指摘"],
        capsys,
    )

    assert review_eval.main(["score", "--result", f"missed={table}"]) == 0
    result, total = _results(capsys)
    assert result["detected"] == ["policy"]
    assert result["missed"] == []
    assert result["false_findings"] == ["noise"]
    assert result["unclassified"] == [{"row": 3, "location": "file2.py"}]
    assert total == {"total": {"detected": 1, "missed": 0, "false_findings": 1, "unclassified": 1}}


def test_score_reports_missed_when_patterns_match_different_rows(tmp_path, monkeypatch, capsys) -> None:
    """照合語句が別々の行にだけ一致する場合は検出とせず見逃しとして返す。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    table = _prepared_table(tmp_path / "case.exec-review.tsv", ["本文を配送する", "方針の確認"], capsys)

    assert review_eval.main(["score", "--result", f"missed={table}"]) == 0
    result, total = _results(capsys)
    assert result["missed"] == ["policy"]
    assert total["total"]["unclassified"] == 2


def test_score_rejects_unknown_case(tmp_path, monkeypatch, capsys) -> None:
    """評価セットに無い事例IDは失敗として終了し、理由を標準エラーへ出す。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    table = _prepared_table(tmp_path / "case.exec-review.tsv", [], capsys)

    assert review_eval.main(["score", "--result", f"other={table}"]) == 1
    err = capsys.readouterr().err
    assert "評価セットに無い事例ID: other" in err
    assert "有効なIDはmissed" in err


def test_score_reports_unreadable_table_with_next_action(tmp_path, monkeypatch, capsys) -> None:
    """読めない管理表は失敗として終了し、確かめる指定を標準エラーへ示す。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))

    assert review_eval.main(["score", "--result", f"missed={tmp_path / 'none.tsv'}"]) == 1
    err = capsys.readouterr().err
    assert "none.tsv" in err
    assert "次の操作: --result missed=" in err


def test_score_reports_malformed_table_with_next_action(tmp_path, monkeypatch, capsys) -> None:
    """形式が不正な管理表は失敗として終了し、検証コマンドと指定方法を標準エラーへ示す。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    table = tmp_path / "bad.exec-review.tsv"
    # 1列目が数値で、JSON文字列のセルという管理表の形式を満たさない。
    table.write_text('1\t"exec-review"\t"a"\t"b"\t"詳細"\t""\t""\n', encoding="utf-8")

    assert review_eval.main(["score", "--result", f"missed={table}"]) == 1
    err = capsys.readouterr().err
    assert "形式が不正" in err
    assert "次の操作: --result missed=" in err
    assert "atk review-table validate" in err


def test_load_cases_reports_malformed_set_with_next_action(tmp_path, monkeypatch, capsys) -> None:
    """必須項目を欠く評価セットは失敗として終了し、直す形式の所在を標準エラーへ示す。"""
    cases = tmp_path / "cases.json"
    cases.write_text('{"cases": [{"id": "x"}]}', encoding="utf-8")
    monkeypatch.setattr(review_eval, "CASES_PATH", cases)

    assert review_eval.main(["score", "--result", f"x={tmp_path / 'none.tsv'}"]) == 1
    err = capsys.readouterr().err
    assert "評価セットを読み込めない" in err
    assert "次の操作: " in err
    assert "レビュー評価セット" in err


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _case_repository(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str, str]:
    """3つのcommitを持つリポジトリを作り、base・書換え前・書換え後のOIDを返す。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "old")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "new")
    new = _git(repo, "rev-parse", "HEAD")
    return repo, base, old, new


def test_prepare_rebuilds_worktree_and_launch_values(tmp_path, monkeypatch, capsys) -> None:
    """対応記録の書換え後のOIDから範囲を求め、レビュー対象の作業ツリーと計画の複製、起動値を用意する。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    repo, base, old, new = _case_repository(tmp_path)
    notes = tmp_path / "notes"
    plan_dir = notes / "plans/2026/10"
    plan_dir.mkdir(parents=True)
    (plan_dir / "01-0000_case.md").write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text(
        f'{{"commits":["{old}"],"awi":["x.md"]}}\n{{"rewrite":{{"{old}":"{new}"}}}}\n', encoding="utf-8"
    )
    out = tmp_path / "out"

    assert review_eval.main(["prepare", "--out", str(out), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 0
    (prepared,) = _results(capsys)
    worktree = pathlib.Path(prepared["start"]["cwd"])
    assert _git(worktree, "rev-parse", "HEAD") == new
    assert prepared["start"]["extra_params"]["開始時のHEAD"] == _git(repo, "rev-parse", f"{new}^")
    assert prepared["start"]["extra_params"]["開始時のHEAD"] == old
    assert pathlib.Path(prepared["start"]["extra_params"]["計画"]).read_text(encoding="utf-8").startswith("# 計画\n")
    assert "review_contract" not in prepared["start"]["extra_params"]
    assert prepared["table"].endswith("01-0000_case.exec-review.tsv")
    assert prepared["start"]["subagent_md_path"] == "exec-review"
    assert base != old
    subprocess.run(prepared["cleanup"], check=True)
    assert not worktree.exists()


@pytest.mark.parametrize("form", ["list", "paragraph"])
@pytest.mark.parametrize("supplement_form", ["list", "paragraph"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_prepare_builds_condition_table_accepted_by_launch_preflight(
    tmp_path, monkeypatch, capsys, form, supplement_form
) -> None:
    """計画の新形式AWIへ条件表を生成し、agents_serverの起動前検査が受理する起動値を返す。旧形式AWIは表に含めない。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    repo, _base, old, new = _case_repository(tmp_path)
    notes = tmp_path / "notes"
    entries = notes / "2026" / "10"
    entries.mkdir(parents=True)
    head = "---\ntype: awi\nsource: fix\nstate: done\ntarget_repo: github.com/ak110/dotfiles\n---\n"
    reflection = (
        "背景の説明。\n\n1. ファイルAを変更する。\n\n   続く段落も保持する。\n\n"
        "   - 入れ子の具体成果。\n\n2. ファイルB | 文書を更新する。\n"
        if form == "list"
        else "ファイルAを変更する。\n続きの行も保持する。\n\nファイルB | 文書を更新する。\n"
    )
    supplements = "- 条件A\n- 条件B | 縦線\n" if supplement_form == "list" else "条件A\n\n条件B | 縦線\n"
    (entries / "20261001-000000-001.md").write_text(
        head
        + "# 新形式の要求\n\n目的の段落。\n続きの行。\n\n## 反映内容と反映先\n"
        + reflection
        + "\n## 完成条件\n\n"
        + supplements,
        encoding="utf-8",
    )
    (entries / "20261001-000000-002.md").write_text(head + "# 旧形式\n## 実現性\n可能\n## 完成条件\n- 条件\n", encoding="utf-8")
    plan_dir = notes / "plans/2026/10"
    plan_dir.mkdir(parents=True)
    (plan_dir / "01-0000_case.md").write_text(
        "# 計画\n## 概要\n### 計画メタ情報\n- 関連WI:\n"
        "  - 20261001-000000-001.md: 新形式\n  - 20261001-000000-002.md: 旧形式\n",
        encoding="utf-8",
    )
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text(
        f'{{"commits":["{old}","{new}"],"awi":["x.md"]}}\n', encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    sender = AsyncMock(return_value={"session_id": "review", "status": "running", "label": "担当"})
    manager = SimpleNamespace(
        start=sender, launcher_record_context=lambda: {"status": "unresolved"}, launcher_session_id=lambda: None
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: 評価用生成から公開startへの入力を検証する。\n", encoding="utf-8")
    params = {"レビュー基準": "計画", "計画": str(plan_dir / "01-0000_case.md"), "引き継ぎ記録先": "/x/handoff.md（新規）"}
    with pytest.raises(ActionableError, match="review_contract"):
        await mcp_tools.start(
            str(tmp_path), subagent_md_path="exec-review", extra_params=params, whole_contract_path=str(whole)
        )
    sender.assert_not_awaited()

    out = tmp_path / "out"
    assert review_eval.main(["prepare", "--out", str(out), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 0
    (prepared,) = _results(capsys)
    extra_params = prepared["start"]["extra_params"]
    contract = pathlib.Path(extra_params["review_contract"]).read_text(encoding="utf-8")
    marker = "- " if supplement_form == "list" else ""
    assert f"| 20261001-000000-001.md | 1 | 補足条件 | {marker}条件A | 完成条件 1 |" in contract
    assert f"| 20261001-000000-001.md | 2 | 補足条件 | {marker}条件B \\| 縦線 | 完成条件 2 |" in contract
    assert "| 20261001-000000-001.md | 3 | 主要完成状態 |" in contract
    assert "ファイルAを変更する。" in contract
    assert "続く段落も保持する。" in contract if form == "list" else "続きの行も保持する。" in contract
    if form == "list":
        assert "入れ子の具体成果。" in contract
        assert "背景の説明。" not in contract
    assert "| 20261001-000000-001.md 反映内容1 |" in contract
    assert "| 20261001-000000-001.md | 4 | 主要完成状態 |" in contract
    assert "ファイルB \\| 文書を更新する。" in contract
    assert "| 20261001-000000-001.md 反映内容2 |" in contract
    assert "20261001-000000-002.md" not in contract
    try:
        response = await mcp_tools.start(**prepared["start"], whole_contract_path=str(whole))
        assert response["session_id"] == "review"
        sender.assert_awaited_once()
        sender.reset_mock()
        pathlib.Path(extra_params["review_contract"]).write_text(
            "\n".join(line for line in contract.splitlines() if "| 2 | 補足条件 |" not in line), encoding="utf-8"
        )
        with pytest.raises(ActionableError, match="補足条件 1/2件"):
            await mcp_tools.start(**prepared["start"], whole_contract_path=str(whole))
        sender.assert_not_awaited()
    finally:
        subprocess.run(prepared["cleanup"], check=True)


def test_prepare_uses_case_extra_params_for_other_roles(tmp_path, monkeypatch, capsys) -> None:
    """事例が役割の起動入力を持つ場合は、置換した値をそのまま起動値にし、実行レビュー用の入力を足さない。"""
    cases = tmp_path / "cases.json"
    data = json.loads(_write_cases(cases).read_text(encoding="utf-8"))
    data["cases"][0]["role"] = "wi-draft-review"
    data["cases"][0]["extra_params"] = {"計画": "{plan}", "起点": "{base}", "記録": "{case_dir}/handoff.md"}
    cases.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(review_eval, "CASES_PATH", cases)
    repo, _base, old, new = _case_repository(tmp_path)
    notes = tmp_path / "notes"
    plan_dir = notes / "plans/2026/10"
    plan_dir.mkdir(parents=True)
    (plan_dir / "01-0000_case.md").write_text("# 計画\n", encoding="utf-8")
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text(f'{{"commits":["{new}"],"awi":["x.md"]}}\n', encoding="utf-8")

    out = tmp_path / "out"
    assert review_eval.main(["prepare", "--out", str(out), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 0
    (prepared,) = _results(capsys)
    assert prepared["start"]["subagent_md_path"] == "wi-draft-review"
    assert prepared["start"]["extra_params"] == {
        "計画": str(out / "missed" / "01-0000_case.md"),
        "起点": old,
        "記録": f"{out / 'missed'}/handoff.md",
    }
    subprocess.run(prepared["cleanup"], check=True)


def test_bundled_cases_load_and_compile() -> None:
    """同梱の評価セットが読み込め、事例IDが重複せず、照合語句が正規表現として成立する。"""
    cases = review_eval.load_cases()
    assert {case.kind for case in cases} >= {"見逃し", "正しい指摘", "誤指摘"}
    assert all(case.detect or case.absent for case in cases)
