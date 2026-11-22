"""review_eval.pyの公開コマンドを通した準備と採点の試験。"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
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
                        "revision": "0000000000000000000000000000000000000000",
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
    assert json.loads(table.with_suffix(".score.json").read_text(encoding="utf-8")) == result


def test_score_retains_worktree_prepared_with_legacy_directory_name(tmp_path, monkeypatch, capsys) -> None:
    """旧名だけでは同じ準備物だと決めず、採点を保って確認方法を示す。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    legacy = tmp_path / "missed"
    worktree = legacy / "worktree"
    worktree.mkdir(parents=True)
    table = _prepared_table(legacy / "case.exec-review.tsv", [], capsys)

    assert review_eval.main(["score", "--result", f"missed={table}"]) == 0
    captured = capsys.readouterr()
    assert str(worktree) in captured.err
    assert "git worktree list" in captured.err
    assert worktree.is_dir()


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


def _save_notes(notes: pathlib.Path, cases: pathlib.Path) -> str:
    """準備用原本を保存し、評価事例をその版へ固定する。"""
    _git(notes, "init", "-q")
    _git(notes, "add", ".")
    _git(notes, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "saved")
    revision = _git(notes, "rev-parse", "HEAD")
    data = json.loads(cases.read_text(encoding="utf-8"))
    data["cases"][0]["revision"] = revision
    cases.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return revision


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

    _save_notes(notes, review_eval.CASES_PATH)

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
    assert "--force" not in prepared["cleanup"]
    (worktree / "unsaved.txt").write_text("未保存成果", encoding="utf-8")
    assert subprocess.run(prepared["cleanup"], check=False, capture_output=True).returncode != 0
    assert (worktree / "unsaved.txt").exists()
    (worktree / "unsaved.txt").unlink()
    table = _prepared_table(pathlib.Path(prepared["table"]), [], capsys)
    assert review_eval.main(["score", "--result", f"missed={table}", "--retain-worktree"]) == 0
    assert worktree.exists()
    capsys.readouterr()
    monkeypatch.setattr(review_eval, "_cleanup_retained_environment", lambda _path: pytest.fail("全回収時の重複走査"))
    assert review_eval.main(["score", "--result", f"missed={table}"]) == 0
    assert not worktree.exists()
    assert table.with_suffix(".score.json").exists()


@pytest.mark.parametrize("form", ["list", "paragraph", "legacy"])
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
        + "# 新形式の要求\n\n目的の段落。\n続きの行。\n\n"
        + ("## 反映内容と反映先\n" + reflection if form != "legacy" else "")
        + "\n## 完成条件\n\n"
        + supplements,
        encoding="utf-8",
    )
    (entries / "20261001-000000-002.md").write_text(head + "# 旧形式\n## 実現性\n可能\n## 完成条件\n- 条件\n", encoding="utf-8")
    (entries / "20261001-000000-003.md").write_text(
        head + "# 旧形式\n## 適用範囲\n範囲\n## 完成条件\n- 条件\n", encoding="utf-8"
    )
    (entries / "20261001-000000-004.md").write_text(
        head.replace("source: fix\n", "") + "# 人間由来\n## 完成条件\n- 条件\n", encoding="utf-8"
    )
    plan_dir = notes / "plans/2026/10"
    plan_dir.mkdir(parents=True)
    (plan_dir / "01-0000_case.md").write_text(
        "# 計画\n## 概要\n### 計画メタ情報\n- 関連WI:\n"
        "  - 20261001-000000-001.md: 新形式\n  - 20261001-000000-002.md: 旧形式\n"
        "  - 20261001-000000-003.md: 旧形式\n  - 20261001-000000-004.md: 人間由来\n",
        encoding="utf-8",
    )
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text(
        f'{{"commits":["{old}","{new}"],"awi":["x.md"]}}\n', encoding="utf-8"
    )
    original_wi = entries / "20261001-000000-001.md"
    if form == "legacy" and supplement_form == "paragraph":
        original_wi.write_bytes(original_wi.read_bytes().replace(b"\n", b"\r\n"))
    original_bytes = original_wi.read_bytes()
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
    _save_notes(notes, review_eval.CASES_PATH)
    assert review_eval.main(["prepare", "--out", str(out), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 0
    (prepared,) = _results(capsys)
    extra_params = prepared["start"]["extra_params"]
    contract = pathlib.Path(extra_params["review_contract"]).read_text(encoding="utf-8")
    marker = "- " if supplement_form == "list" else ""
    assert f"| 20261001-000000-001.md | 1 | 補足条件 | {marker}条件A | 完成条件 1 |" in contract
    assert f"| 20261001-000000-001.md | 2 | 補足条件 | {marker}条件B \\| 縦線 | 完成条件 2 |" in contract
    assert "| 20261001-000000-001.md | 3 | 主要完成状態 |" in contract
    if form == "legacy":
        assert "| 3 | 主要完成状態 | 新形式の要求 目的の段落。 続きの行。 | H1と冒頭 |" in contract
        assert contract.count("| 主要完成状態 |") == 1
    else:
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
    assert "20261001-000000-003.md" not in contract
    assert "20261001-000000-004.md" not in contract
    originals = json.loads(pathlib.Path(extra_params["評価用WI原本"]).read_text(encoding="utf-8"))
    assert set(originals) == {f"20261001-000000-00{n}.md" for n in range(1, 5)}
    assert pathlib.Path(originals["20261001-000000-001.md"]).read_bytes() == original_bytes
    saved_text = pathlib.Path(originals["20261001-000000-001.md"]).read_text(encoding="utf-8")
    (entries / "20261001-000000-001.md").write_text(
        head + "# 後日の要求\n## 完成条件\n- 新条件1\n- 新条件2\n- 新条件3\n", encoding="utf-8"
    )
    (plan_dir / "01-0000_case.md").write_text("# 後日の計画\n", encoding="utf-8")
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text("後日の不正な記録\n", encoding="utf-8")
    repeated = tmp_path / "repeated"
    assert review_eval.main(["prepare", "--out", str(repeated), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 0
    (again,) = _results(capsys)
    assert pathlib.Path(again["start"]["extra_params"]["review_contract"]).read_text(encoding="utf-8") == contract
    assert pathlib.Path(again["start"]["extra_params"]["計画"]).read_bytes() == pathlib.Path(extra_params["計画"]).read_bytes()
    assert (
        pathlib.Path(again["start"]["cwd"]).parent.joinpath("wi-commits.jsonl").read_bytes()
        == pathlib.Path(prepared["start"]["cwd"]).parent.joinpath("wi-commits.jsonl").read_bytes()
    )
    again_originals = json.loads(pathlib.Path(again["start"]["extra_params"]["評価用WI原本"]).read_text(encoding="utf-8"))
    assert pathlib.Path(again_originals["20261001-000000-001.md"]).read_text(encoding="utf-8") == saved_text
    subprocess.run(again["cleanup"], check=True)
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
    data["cases"][0]["extra_params"] = {"確認段階": "方針", "計画": "{plan}", "起点": "{base}", "記録": "{case_dir}/handoff.md"}
    cases.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(review_eval, "CASES_PATH", cases)
    repo, _base, old, new = _case_repository(tmp_path)
    notes = tmp_path / "notes"
    plan_dir = notes / "plans/2026/10"
    plan_dir.mkdir(parents=True)
    (plan_dir / "01-0000_case.md").write_text("# 計画\n", encoding="utf-8")
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text(f'{{"commits":["{new}"],"awi":["x.md"]}}\n', encoding="utf-8")

    out = tmp_path / "out"
    _save_notes(notes, cases)
    assert review_eval.main(["prepare", "--out", str(out), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 0
    (prepared,) = _results(capsys)
    assert prepared["start"]["subagent_md_path"] == "wi-draft-review"
    case_dir = pathlib.Path(prepared["start"]["cwd"]).parent
    assert case_dir.parent == out
    assert prepared["start"]["extra_params"] == {
        "確認段階": "方針",
        "計画": str(case_dir / "01-0000_case.md"),
        "起点": old,
        "記録": f"{case_dir}/handoff.md",
    }
    subprocess.run(prepared["cleanup"], check=True)


def test_bundled_cases_load_and_compile() -> None:
    """同梱の評価セットが読み込め、事例IDが重複せず、照合語句が正規表現として成立する。"""
    cases = review_eval.load_cases()
    assert {case.kind for case in cases} >= {"見逃し", "正しい指摘", "誤指摘"}
    assert all(case.detect or case.absent for case in cases)


@pytest.mark.parametrize("defect", ["supplement", "major", "revision", "wi-revision", "missing-plan", "missing-wi"])
def test_prepare_rejects_incomplete_generation_or_saved_input(tmp_path, monkeypatch, capsys, defect) -> None:
    """生成器だけの欠落と保存版の不成立を公開prepareで拒否し、起動値を返さない。"""
    cases = _write_cases(tmp_path / "cases.json")
    monkeypatch.setattr(review_eval, "CASES_PATH", cases)
    repo, _base, _old, new = _case_repository(tmp_path)
    notes = tmp_path / "notes"
    plan = notes / "plans/2026/10/01-0000_case.md"
    plan.parent.mkdir(parents=True)
    name = "20261001-000000-001.md"
    plan.write_text(f"# 計画\n## 概要\n### 計画メタ情報\n- 関連WI:\n  - {name}: 要求\n", encoding="utf-8")
    plan.with_suffix(".wi-commits.jsonl").write_text(f'{{"commits":["{new}"]}}\n', encoding="utf-8")
    wi = notes / name
    wi.write_text("---\ntype: awi\nsource: fix\n---\n# 要求\n\n目的。\n\n## 完成条件\n- 条件\n", encoding="utf-8")
    if defect == "missing-plan":
        plan.unlink()
    if defect == "missing-wi":
        wi.unlink()
    _save_notes(notes, cases)
    if defect == "revision":
        data = json.loads(cases.read_text(encoding="utf-8"))
        data["cases"][0]["revision"] = "HEAD"
        cases.write_text(json.dumps(data), encoding="utf-8")
    if defect == "wi-revision":
        data = json.loads(cases.read_text(encoding="utf-8"))
        data["cases"][0]["wi_revisions"] = {name: "f" * 40}
        cases.write_text(json.dumps(data), encoding="utf-8")
    if defect in {"supplement", "major"}:
        # 公開prepareが生成欠落を拒否する境界を試すため、内部の生成処理だけを差し替える。
        # pylint: disable-next=protected-access
        original = review_eval._contract_rows

        def incomplete(*args, **kwargs):
            category = "補足条件" if defect == "supplement" else "主要完成状態"
            return [row for row in original(*args, **kwargs) if f"| {category} |" not in row]

        monkeypatch.setattr(review_eval, "_contract_rows", incomplete)
    out = tmp_path / "out"
    assert review_eval.main(["prepare", "--out", str(out), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    if defect in {"supplement", "major"}:
        assert name in captured.err
        assert "補足条件 0/1件" in captured.err if defect == "supplement" else "主要完成状態 0/1件" in captured.err
        assert "scripts/review_eval.py" in captured.err
    elif defect == "wi-revision":
        assert name in captured.err
        assert str(notes) in captured.err
        assert "f" * 40 in captured.err
        assert "--private-notes" in captured.err
        assert "wi_revisions" in captured.err
        assert "--repo <名前>" not in captured.err
    else:
        assert "revision" in captured.err
    assert not list(out.glob("*/worktree"))


def test_store_then_prepare_uses_saved_plan_and_contract(tmp_path, monkeypatch, capsys) -> None:
    """保存版を指定しない事例は自動生成の条件表と不在の証拠の件数を返し、保存版を指定した事例は保存した計画と条件表を渡す。

    保存版の条件表が指す保存時のWI原本の複製は、準備で書き出した同名の原本へ付け替える。
    """
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    repo, _base, old, new = _case_repository(tmp_path)
    notes = tmp_path / "notes"
    entries = notes / "2026" / "10"
    entries.mkdir(parents=True)
    (entries / "20261001-000000-001.md").write_text(
        "---\ntype: awi\nsource: fix\nstate: done\ntarget_repo: github.com/ak110/dotfiles\n---\n"
        "# 要求\n\n目的。\n\n## 反映内容と反映先\n\n1. 同じ読取の再実行は採らない。\n\n## 完成条件\n\n- 条件A\n\n"
        "## 方式見直し\n\n成功した読取の再取得を採用する。\n",
        encoding="utf-8",
    )
    missing_evidence = "/home/u/.cache/agent-toolkit/managed-temp/session-a/verify.stdout"
    plan_dir = notes / "plans/2026/10"
    plan_dir.mkdir(parents=True)
    (plan_dir / "01-0000_case.md").write_text(
        "# 計画\n## 概要\n### 計画メタ情報\n- 対象リポジトリ: `/original/worktree`\n- 関連WI:\n"
        "  - 20261001-000000-001.md: 要求\n## 進捗ログ\n"
        "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
        f"| 1 | 検証 | 出力は{missing_evidence}。指摘1を採用した。 |\n",
        encoding="utf-8",
    )
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text(
        f'{{"commits":["{old}","{new}"],"awi":["20261001-000000-001.md"]}}\n', encoding="utf-8"
    )
    _save_notes(notes, review_eval.CASES_PATH)
    _git(notes, "config", "user.name", "t")
    _git(notes, "config", "user.email", "t@example.com")
    arguments = ["--repo", f"target={repo}", "--private-notes", str(notes)]

    assert review_eval.main(["prepare", "--out", str(tmp_path / "generated"), *arguments]) == 0
    (generated,) = _results(capsys)
    assert generated["temporary_evidence"] == {"referenced": 1, "missing": [missing_evidence]}
    generated_contract = pathlib.Path(generated["start"]["extra_params"]["review_contract"]).read_text(encoding="utf-8")
    assert "同じ読取の再実行は採らない" in generated_contract

    evaluation_plan = tmp_path / "evaluation-plan.md"
    evaluation_plan.write_text(
        "# 評価用の計画\n## 概要\n### 計画メタ情報\n- 対象リポジトリ: `/saved/elsewhere/worktree`\n- 関連WI:\n"
        "  - 20261001-000000-001.md: 要求\n## 進捗ログ\n"
        "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n| 1 | 保存版の確認 | 実機証拠は欠測として扱う。 |\n",
        encoding="utf-8",
    )
    checked_contract = tmp_path / "checked-contract.md"
    stale_original = "/home/u/.cache/agent-toolkit/managed-temp/earlier/private-notes-input/2026/10/20261001-000000-001.md"
    checked_contract.write_text(
        generated_contract.replace("同じ読取の再実行は採らない", "成功した読取の再取得を採用する")
        + f"\n出典の原本: {stale_original}#完成条件\n",
        encoding="utf-8",
    )
    assert (
        review_eval.main(
            [
                "store",
                "--case",
                "missed",
                "--plan",
                str(evaluation_plan),
                "--contract",
                str(checked_contract),
                "--private-notes",
                str(notes),
            ]
        )
        == 0
    )
    (stored,) = _results(capsys)
    assert stored["fixtures"]["revision"] == _git(notes, "rev-parse", "HEAD")
    data = json.loads(review_eval.CASES_PATH.read_text(encoding="utf-8"))
    data["cases"][0]["fixtures"] = stored["fixtures"]
    review_eval.CASES_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert review_eval.main(["prepare", "--out", str(tmp_path / "saved"), *arguments]) == 0
    (saved,) = _results(capsys)
    plan_text = pathlib.Path(saved["start"]["extra_params"]["計画"]).read_text(encoding="utf-8")
    assert f"- 対象リポジトリ: `{saved['start']['cwd']}`" in plan_text
    assert "実機証拠は欠測として扱う" in plan_text and "指摘1を採用した" not in plan_text
    originals = json.loads(pathlib.Path(saved["start"]["extra_params"]["評価用WI原本"]).read_text(encoding="utf-8"))
    assert pathlib.Path(saved["start"]["extra_params"]["review_contract"]).read_text(
        encoding="utf-8"
    ) == checked_contract.read_text(encoding="utf-8").replace(stale_original, originals["20261001-000000-001.md"])
    assert saved["temporary_evidence"] == {"referenced": 0, "missing": []}
    for prepared in (generated, saved):
        subprocess.run(prepared["cleanup"], check=True)


@pytest.mark.parametrize("issues", [[], ["本文配送の方針違反\n  根拠を親指摘へ含める"], ["本文の問題", "配送の方針違反"]])
@pytest.mark.parametrize("unit", ["", "件"])
@pytest.mark.parametrize("reports", [False, True])
def test_score_draft_review_cli(tmp_path, monkeypatch, capsys, issues, unit, reports) -> None:
    """返却原文を公開scoreへ渡し、指摘間をまたぐ語句一致を防ぐ。"""
    cases = _write_cases(tmp_path / "cases.json")
    data = json.loads(cases.read_text(encoding="utf-8"))
    data["cases"][0]["role"] = "wi-draft-review"
    cases.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(review_eval, "CASES_PATH", cases)
    result = tmp_path / "result.md"
    result.write_text(
        f"判定: {'修正要' if issues else '合格'}\n確認段階: 方針\n対象資料: /plan.md\n"
        f"未解決指摘: {len(issues)}{unit}\n指摘:\n"
        + "\n".join(f"- {issue}" for issue in issues or ["なし"])
        + "\n確認した要求と根拠: 原要求と観測\n"
        + (
            "気付いた改善点: 本文配送の方針に関する読込費用\n"
            "気付いた改善点: 二つ目の読込費用\n"
            "- 気付いた改善点: 箇条書きの追加報告\n"
            "- 気付いた改善点: 二つ目の箇条書き報告\n"
            if reports
            else ""
        ),
        encoding="utf-8",
    )
    assert review_eval.main(["score", "--result", f"missed={result}"]) == 0
    scored = _results(capsys)[0]
    assert scored["detected"] == (["policy"] if len(issues) == 1 else [])
    assert result.with_suffix(".score.json").is_file()


@pytest.mark.parametrize("change", ["count", "decision", "required", "empty", "body"])
def test_score_draft_review_rejects_invalid_contract(tmp_path, monkeypatch, capsys, change) -> None:
    cases = _write_cases(tmp_path / "cases.json")
    data = json.loads(cases.read_text(encoding="utf-8"))
    data["cases"][0]["role"] = "wi-draft-review"
    cases.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(review_eval, "CASES_PATH", cases)
    text = "判定: 合格\n確認段階: 方針\n対象資料: /plan.md\n未解決指摘: 0\n指摘:\n- なし\n確認した要求と根拠: 原要求\n"
    replacements = {
        "count": ("未解決指摘: 0", "未解決指摘: 1"),
        "decision": ("合格", "修正要"),
        "required": ("対象資料: /plan.md\n", ""),
        "empty": ("- なし\n", ""),
        "body": ("方針", "本文"),
    }
    result = tmp_path / "result.md"
    result.write_text(text.replace(*replacements[change]), encoding="utf-8")
    assert review_eval.main(["score", "--result", f"missed={result}"]) == 1
    assert "不正" in capsys.readouterr().err
    assert not result.with_suffix(".score.json").exists()


_SMALL_CASES = (
    "small-verification-table-scope",
    "small-known-consumer-tests",
    "small-verification-evidence-control",
    "small-handoff-record-readers",
    "small-handoff-record-readers-control",
    "small-recheck-ledger",
    "small-recheck-ledger-control",
)


@pytest.mark.parametrize("case_id", ["intent-fixed-defect", "intent-fixed-control"])
def test_prepare_body_review_links_dry_run_to_the_same_draft(tmp_path, capsys, case_id) -> None:
    """本文の合成対照を公開dry-runへ渡し、レビューに必要な原稿・原要求・冒頭の対応を返す。"""
    assert review_eval.main(["prepare", "--out", str(tmp_path), "--case", case_id]) == 0
    (prepared,) = _results(capsys)
    params = prepared["start"]["extra_params"]
    assert params["確認段階"] == "本文"
    draft = pathlib.Path(params["原稿"])
    source = pathlib.Path(params["要求と出所"])
    evidence = pathlib.Path(params["成立性の証拠"]).read_text(encoding="utf-8")
    assert source.is_file()
    assert f"原稿: {draft}" in evidence.splitlines()
    (opening_path,) = [line.removeprefix("冒頭保存先: ") for line in evidence.splitlines() if line.startswith("冒頭保存先: ")]
    opening = pathlib.Path(opening_path).read_text(encoding="utf-8")
    body = draft.read_text(encoding="utf-8")
    assert opening.startswith("# ")
    assert body.startswith(opening)
    assert "## 反映内容と反映先" not in opening
    assert case_id not in str(draft)
    assert subprocess.run(prepared["cleanup"], check=False).returncode == 0


@pytest.mark.parametrize("failure", ["missing-input", "omitted-stage", "unreadable-file"])
def test_prepare_body_review_requires_inputs_before_creating_worktree(tmp_path, capsys, monkeypatch, failure) -> None:
    """本文の事例で必須の資料を指定し忘れた場合は、worktreeを作らず修正する項目を案内する。"""
    collection = pathlib.Path(review_eval.__file__).with_name("review_eval_draft_cases.json")
    cases = json.loads(collection.read_text(encoding="utf-8"))["cases"]
    case = next(case for case in cases if case["id"] == "intent-fixed-defect")
    params = case["extra_params"]
    if failure == "unreadable-file":
        params["成立性の証拠"] = "{worktree}/missing.md"
    else:
        del params["成立性の証拠"]
        if failure == "omitted-stage":
            del params["確認段階"]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(review_eval, "CASES_PATH", path)
    out = tmp_path / "out"
    assert review_eval.main(["prepare", "--out", str(out)]) == 1
    errors = capsys.readouterr().err
    assert "成立性の証拠" in errors and "次の操作: " in errors
    if failure == "unreadable-file":
        assert "新しい--out" in errors
        assert not list(out.glob("*/worktree"))
    else:
        assert not out.exists()


@pytest.mark.parametrize(
    "cleanup_failure",
    [None, "start", "dirty", "decode"],
)
def test_prepare_body_review_recovers_from_evidence_write_failure(tmp_path, capsys, monkeypatch, cleanup_failure) -> None:
    """抽出後の証拠保存に失敗したら、評価用worktreeを回収し、新しい出力先での再準備を案内する。"""

    def create_case_with_unwritable_evidence(case_dir: pathlib.Path) -> None:
        case_dir.mkdir(parents=True)
        (case_dir / "evidence.md").mkdir()

    monkeypatch.setattr(review_eval, "_new_case_dir", create_case_with_unwritable_evidence)

    run = subprocess.run

    def run_with_cleanup_failure(args, *, check=False, **kwargs):
        if args[0] == "git" and args[3:5] == ["worktree", "remove"]:
            if cleanup_failure == "start":
                raise FileNotFoundError("Gitを起動できない")
            if cleanup_failure == "decode":
                raise UnicodeError("Gitの出力を読めない")
            if cleanup_failure == "dirty":
                (pathlib.Path(args[-1]) / "test-local-change").write_text("回収しない変更", encoding="utf-8")
        return run(args, check=check, **kwargs)

    monkeypatch.setattr(subprocess, "run", run_with_cleanup_failure)
    out = tmp_path / "out"
    try:
        assert review_eval.main(["prepare", "--out", str(out), "--case", "intent-fixed-control"]) == 1
        error = capsys.readouterr().err
        assert "成立性の証拠を保存できない:" in error and "evidence.md" in error
        assert "書込権限と空き容量" in error and "新しい--out" in error
        remaining = list(out.glob("*/worktree"))
        if cleanup_failure is None:
            assert not remaining
        else:
            (worktree,) = remaining
            assert "評価用worktreeを回収できない:" in error and str(worktree) in error
            assert "fetch" not in error and "--force" not in error
            assert "変更や未追跡ファイルがあれば保存してから、強制削除せずに回収する" in error
            if cleanup_failure == "dirty":
                assert "git worktree removeが失敗した（終了コード" in error
                assert (worktree / "test-local-change").is_file()
            else:
                assert ("Gitを起動できない" if cleanup_failure == "start" else "Gitの出力を読めない") in error
    finally:
        monkeypatch.setattr(subprocess, "run", run)
        for worktree in out.glob("*/worktree"):
            (worktree / "test-local-change").unlink(missing_ok=True)
            _git(worktree.parent / "repository", "worktree", "remove", str(worktree))


def _unittest(worktree: pathlib.Path, *targets: str) -> int:
    # 準備したworktreeを回収できるよう、未追跡のバイトコードを書かせない。
    command = [sys.executable, "-B", "-m", "unittest", *(targets or ("discover", "-s", "tests", "-p", "*_test.py"))]
    return subprocess.run(command, cwd=worktree, capture_output=True, check=False).returncode


@pytest.mark.parametrize("case_id", _SMALL_CASES)
def test_prepare_builds_small_case_from_material(tmp_path, capsys, case_id) -> None:
    """合成素材から変更前後の2つのcommitと評価用の入力を作り、欠陥の事例だけが計画に無い試験で失敗する版を渡す。"""
    assert review_eval.main(["prepare", "--out", str(tmp_path), "--case", case_id]) == 0
    (prepared,) = _results(capsys)
    worktree = pathlib.Path(prepared["start"]["cwd"])
    extra_params = prepared["start"]["extra_params"]
    assert _git(worktree, "rev-parse", "HEAD^") == extra_params["開始時のHEAD"]
    assert _git(worktree, "diff", "--name-only", "HEAD^", "HEAD")
    plan_text = pathlib.Path(extra_params["計画"]).read_text(encoding="utf-8")
    assert f"- 対象リポジトリ: `{worktree}`" in plan_text
    originals = json.loads(pathlib.Path(extra_params["評価用WI原本"]).read_text(encoding="utf-8"))
    assert len(originals) == 1
    assert "| 補足条件 |" in pathlib.Path(extra_params["review_contract"]).read_text(encoding="utf-8")
    assert prepared["temporary_evidence"] == {"referenced": 0, "missing": []}
    if case_id == "small-recheck-ledger":
        assert _unittest(worktree, "tests.store_test", "tests.cli_test") == 0
        assert _unittest(worktree, "tests.help_test") != 0
    else:
        assert _unittest(worktree) == 0
    subprocess.run(prepared["cleanup"], check=True, capture_output=True)
    assert not worktree.exists()


def test_prepare_accepts_output_path_with_backslashes(tmp_path, capsys) -> None:
    """Windows形式の出力先（`C:\\Users\\...`）でも、計画の対象リポジトリの行を作業ツリーのパスどおりに付け替える。"""
    # POSIXではバックスラッシュを含む1つの名前、Windowsでは区切りになり、どちらも`\U`を含むパスになる。
    out = tmp_path / "eval\\Users\\aki"
    assert review_eval.main(["prepare", "--out", str(out), "--case", "small-known-consumer-tests"]) == 0
    (prepared,) = _results(capsys)
    worktree = prepared["start"]["cwd"]
    assert "\\U" in worktree
    plan_text = pathlib.Path(prepared["start"]["extra_params"]["計画"]).read_text(encoding="utf-8")
    assert f"- 対象リポジトリ: `{worktree}`" in plan_text
    subprocess.run(prepared["cleanup"], check=True, capture_output=True)


def _reviewer_view(prepared: dict, out: pathlib.Path) -> dict:
    """担当が受け取る起動入力、作業ツリーと計画の置き場所にある名前、commit件名を、出力先を伏せて返す。"""
    start = prepared["start"]
    worktree = pathlib.Path(start["cwd"])

    def shown(value: str) -> str:
        return value.replace(str(out), "<out>")

    return {
        "cwd": shown(start["cwd"]),
        "subagent_md_path": start["subagent_md_path"],
        "extra_params": {key: shown(value) for key, value in start["extra_params"].items()},
        "files": sorted(path.name for path in worktree.parent.iterdir()),
        "subjects": _git(worktree, "log", "--format=%s").splitlines(),
    }


@pytest.mark.parametrize("material", ["guide-split", "handoff-record", "recheck-ledger"])
def test_prepare_hides_case_identity_from_reviewer(tmp_path, capsys, material) -> None:
    """同じ素材の欠陥の事例と正常対照で、担当へ渡る入力は事例ごとの置き場所と開始時のHEADを除いて同じになり、
    事例ID、事例の種類、素材の差分と計画のファイル名を含まない。"""
    cases = [case for case in review_eval.load_cases() if case.material is not None and case.material.name == material]
    assert {case.kind for case in cases} > {"正常対照"}
    comparable = []
    for case in cases:
        assert case.material is not None
        assert review_eval.main(["prepare", "--out", str(tmp_path), "--case", case.id]) == 0
        (prepared,) = _results(capsys)
        view = _reviewer_view(prepared, tmp_path)
        text = json.dumps(view, ensure_ascii=False)
        cues = {case.id, case.kind, case.material.change, case.material.plan, "defect", "control"}
        assert not [cue for cue in cues if cue in text], text
        case_name = pathlib.Path(prepared["start"]["cwd"]).parent.name
        comparable.append(text.replace(case_name, "<case>").replace(view["extra_params"]["開始時のHEAD"], "<base>"))
        subprocess.run(prepared["cleanup"], check=True, capture_output=True)
    assert len(set(comparable)) == 1, comparable


_SMALL_FINDINGS = {
    "small-verification-table-scope": {
        "verification-table-scope": (
            "計画「検証」",
            "検証表がtests.catalog_testだけで、最終差分のdocs/usage.mdと直接利用側のtests/usage_links_test.pyを覆わない。",
        )
    },
    "small-known-consumer-tests": {
        "known-consumer-not-executed": (
            "計画「進捗ログ」",
            "探索で一致したtests/cli_list_test.pyが実行対象に無く、個別の結果も無い。",
        )
    },
    "small-handoff-record-readers": {
        "prompt-reader": (
            "relay/prompt.py:9",
            "前任が記録を作らない交代でも、委譲プロンプトが存在しない記録を最初に読ませる。",
        ),
        "accept-reader": ("relay/accept.py:11", "記録が無い交代の検収がFileNotFoundErrorで失敗する。"),
        "doc-reader": ("docs/handoff.md:13", "後任と委譲元の段が、記録を作らない前任の規定と食い違い、無条件に読ませる。"),
        "unreadable-vs-absent": (
            "relay/record.py:17",
            "記録がディレクトリか権限で読めない場合を不在と区別する手段が無く、記録にしか無い値を失う。",
        ),
    },
    "small-recheck-ledger": {
        "misreference": (
            "ledger/store.py:recheck",
            "--residualが別の指摘の残りを登録した行も指せ、元の指摘を引き継がずに収束する。",
        ),
        "verdict-only-round": (
            "ledger/store.py:current_round",
            "現在のroundを指摘の行だけから求めるため、判定だけのroundの後に前のroundへ記録でき、確認済みのroundの集計が変わる。",
        ),
        "response-after-verdict": ("ledger/store.py:respond", "判定の後にrespondで書き換えた応答が完了として数えられる。"),
        "help-example": (
            "ledger/cli.py:_ADD_MANY_EPILOG",
            "add-many --helpのJSON例は区分を欠き、表示どおりに渡すと拒否される。",
        ),
        "help-cross-test": ("tests/help_test.py", "横断試験help_testがrecheckの--row-idの型変換で失敗する。"),
        "bulk-respond-drops": ("ledger/store.py:respond_many", "respond-manyが行を作り直し、判定（checks）と区分が消える。"),
    },
}
_SMALL_CONTROLS = {
    "small-verification-table-scope": "small-verification-evidence-control",
    "small-known-consumer-tests": "small-verification-evidence-control",
    "small-handoff-record-readers": "small-handoff-record-readers-control",
    "small-recheck-ledger": "small-recheck-ledger-control",
}


def _small_table(path: pathlib.Path, findings: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]) -> pathlib.Path:
    review_table.init(path)
    for location, issue in findings:
        review_table.add(path, "1", "exec-review", location, issue)
    capsys.readouterr()
    return path


@pytest.mark.parametrize("case_id", sorted(_SMALL_FINDINGS))
def test_score_small_case_and_control_with_synthesized_tables(tmp_path, capsys, case_id) -> None:
    """期待ごとの指摘行は、欠陥の事例でその期待だけを検出し、正常対照では同じ指摘を誤指摘として数え、無関係な行は未分類に残す。"""
    findings = _SMALL_FINDINGS[case_id]
    control = _SMALL_CONTROLS[case_id]
    for expectation, finding in findings.items():
        table = _small_table(tmp_path / f"{expectation}.exec-review.tsv", [finding], capsys)
        assert review_eval.main(["score", "--result", f"{case_id}={table}"]) == 0
        result, _total = _results(capsys)
        assert result["detected"] == [expectation]
    unrelated = ("README.md", "見出しの表記が他の資料とそろっていない。")
    table = _small_table(tmp_path / "all.exec-review.tsv", [*findings.values(), unrelated], capsys)
    assert review_eval.main(["score", "--result", f"{case_id}={table}", "--result", f"{control}={table}"]) == 0
    defect, scored_control, _total = _results(capsys)
    assert (defect["detected"], defect["missed"], defect["false_findings"]) == (list(findings), [], [])
    assert set(findings) <= set(scored_control["false_findings"])
    assert defect["unclassified"] == scored_control["unclassified"] == [{"row": len(findings) + 1, "location": "README.md"}]
    clean = _small_table(tmp_path / "clean.exec-review.tsv", [unrelated], capsys)
    assert review_eval.main(["score", "--result", f"{control}={clean}"]) == 0
    result, _total = _results(capsys)
    assert (result["false_findings"], result["unclassified"]) == ([], [{"row": 1, "location": "README.md"}])


def test_bundled_small_cases_keep_origin_and_control() -> None:
    """合成素材ごとに欠陥の事例と正常対照がそろい、全履歴版を元にした事例はその元事例を評価セットに残す。"""
    cases = review_eval.load_cases()
    by_material: dict[str, set[str]] = {}
    for case in cases:
        if case.material is not None:
            by_material.setdefault(case.material.name, set()).add(case.kind)
    assert by_material
    assert all("正常対照" in kinds and kinds - {"正常対照"} for kinds in by_material.values())
    ids = {case.id for case in cases}
    assert {"correct-verification-table-scope", "missed-known-consumer-tests"} <= ids
    assert {origin for case in cases for origin in case.origin.get("cases", [])} <= ids


def _material(root: pathlib.Path) -> pathlib.Path:
    """1ファイルを変える最小の合成素材を作る。"""
    source = root / "source"
    source.mkdir(parents=True)
    _git(source, "init", "-q")
    (source / "value.txt").write_text("1\n", encoding="utf-8")
    _git(source, "add", "value.txt")
    _git(source, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    material = root / "materials" / "tiny"
    material.mkdir(parents=True)
    empty = _git(source, "hash-object", "-t", "tree", os.devnull)
    prefixes = ["--src-prefix=a/", "--dst-prefix=b/", "--no-color", "--no-ext-diff"]
    (material / "base.diff").write_text(_git(source, "diff", *prefixes, empty, "HEAD") + "\n", encoding="utf-8")
    (source / "value.txt").write_text("2\n", encoding="utf-8")
    (material / "change.diff").write_text(_git(source, "diff", *prefixes) + "\n", encoding="utf-8")
    (material / "plan.md").write_text(
        "# 計画\n## 概要\n### 計画メタ情報\n- 対象リポジトリ: `/path/to/worktree`\n"
        "- 関連WI:\n  - 20000101-000000-001.md: 要求\n",
        encoding="utf-8",
    )
    (material / "20000101-000000-001.md").write_text(
        "---\ntype: awi\nsource: fix\n---\n# 要求\n\n## 完成条件\n- 条件\n", encoding="utf-8"
    )
    return material.parent


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"revision": "0" * 40}, "revisionを持たない"),
        ({"origin": {"cases": ["gone"]}}, "元事例が評価セットに無い: gone"),
        ({"origin": {}}, "元事例との対応（origin）が必要"),
        ({"material": {"name": "tiny", "change": "missing.diff", "plan": "plan.md"}}, "missing.diffが無い"),
    ],
)
def test_material_case_definition_is_validated(tmp_path, monkeypatch, capsys, change, message) -> None:
    """合成素材の事例は保存版の項目と元事例の欠落と素材の欠落を拒否し、正しい定義はstoreを使わずに準備できる。"""
    monkeypatch.setattr(review_eval, "MATERIALS_PATH", _material(tmp_path))
    case = {
        "id": "small",
        "kind": "見逃し",
        "role": "exec-review",
        "material": {"name": "tiny", "change": "change.diff", "plan": "plan.md"},
        "origin": {"source": "合成"},
        "description": "最小の合成素材",
        "expect": {"detect": [{"id": "x", "description": "x", "patterns": ["x"]}], "absent": []},
    }
    cases = tmp_path / "cases.json"
    monkeypatch.setattr(review_eval, "CASES_PATH", cases)
    cases.write_text(json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8")
    assert review_eval.main(["store", "--case", "small", "--plan", str(tmp_path / "plan.md")]) == 1
    assert "合成素材の事例" in capsys.readouterr().err
    assert review_eval.main(["prepare", "--out", str(tmp_path / "out"), "--case", "small"]) == 0
    (prepared,) = _results(capsys)
    assert (pathlib.Path(prepared["start"]["cwd"]) / "value.txt").read_text(encoding="utf-8") == "2\n"
    subprocess.run(prepared["cleanup"], check=True, capture_output=True)
    cases.write_text(json.dumps({"cases": [{**case, **change}]}, ensure_ascii=False), encoding="utf-8")
    assert review_eval.main(["prepare", "--out", str(tmp_path / "again"), "--case", "small"]) == 1
    err = capsys.readouterr().err
    assert "評価セットを読み込めない" in err
    assert message in err


def test_every_case_has_selection_rule() -> None:
    """評価セットの全事例が、比べる事例を選ぶ表のいずれかの行に載る。載らない事例は改訂の評価で選ばれない。"""
    document = (
        pathlib.Path(review_eval.__file__).resolve().parent.parent
        / ".claude/skills/agent-toolkit-edit/references/review-eval-set.md"
    )
    text = document.read_text(encoding="utf-8")
    section = text.split("## 比べる事例の選び方", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ") and "`" in line]
    listed = {name for row in rows for name in re.findall(r"`([a-z0-9-]+)`", row)}
    assert {case.id for case in review_eval.load_cases()} <= listed
