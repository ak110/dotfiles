"""観点収集の公開操作が、原本対応・別観点・省略用の存在判定を保持することを確かめる。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import typing

import pytest
import recurrence_items

from agent_toolkit._testing import git_repository as git

_ITEM = (
    "# 再発防止セルフチェック観点\n\n## 共通の項目名\n\n"
    "- 対象条件: 変更がある\n- 確認内容: 契約を保つ\n- 根拠規範: 記録された規範\n"
)


def _write(root: pathlib.Path, relative: str, content: str) -> pathlib.Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("distribution_in_worktree", [False, True], ids=["outside-git", "inside-git"])
def test_cli_prefers_corresponding_tracked_original_and_keeps_other_and_split_items(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    distribution_in_worktree: bool,
) -> None:
    """配布と同じ原本は追跡側へまとめ、同名本文の別観点と分割ファイルは全て入力に残す。"""
    repository = git.init_repository(tmp_path / "repo")
    distribution = (repository if distribution_in_worktree else tmp_path) / "distribution"
    _write(distribution, ".claude-plugin/plugin.json", '{"name":"agent-toolkit"}')
    installed = _write(distribution, "share/recurrence-items.md", _ITEM)
    distribution_only = _write(distribution, "share/part.md", _ITEM)
    ignored = _write(distribution, "ignored.md", _ITEM)
    hidden_file = _write(distribution, ".hidden.md", _ITEM)
    _write(distribution, ".gitignore", "ignored.md\n")
    _write(distribution, ".hidden/part.md", _ITEM)
    _write(distribution, "other.txt", _ITEM)
    _write(distribution, "upper.MD", _ITEM)
    _write(repository, "vendor/agent-toolkit/.claude-plugin/plugin.json", '{"name":"agent-toolkit"}')
    original = _write(
        repository, "vendor/agent-toolkit/share/recurrence-items.md", _ITEM.replace("契約を保つ", "現在の契約を保つ")
    )
    other = _write(repository, "docs/other.md", _ITEM)
    split = _write(repository, ".claude/rules/nested/part.md", _ITEM)
    _write(repository, "docs/untracked.md", _ITEM)
    git.run_git(repository, "add", "vendor", "docs/other.md", ".claude")
    monkeypatch.setattr(recurrence_items, "PLUGIN_ROOT", distribution)
    output = tmp_path / "receipt.json"
    original_run = subprocess.run

    def without_rg(args: list[str], *, check: bool = False, **kwargs: typing.Any) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "rg":
            raise FileNotFoundError("rg is not installed")
        return original_run(args, check=check, **kwargs)

    monkeypatch.setattr(subprocess, "run", without_rg)

    assert recurrence_items.main(["--worktree", str(repository), "--output", str(output)]) == 0

    response = json.loads(capsys.readouterr().out)
    expected = sorted(str(path) for path in (original, other, split, distribution_only, ignored, hidden_file))
    assert json.loads(response["観点ファイル"]) == expected
    assert response["観点あり"] is True
    assert response["収集記録"] == str(output)
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["distributed"] == sorted(str(path) for path in (installed, distribution_only, ignored, hidden_file))
    assert receipt["tracked"] == sorted([str(original), str(other), str(split)])
    assert receipt["prefer_tracked"] == [
        {"distributed": str(installed), "tracked": [str(original)], "relative": "share/recurrence-items.md"}
    ]


@pytest.mark.parametrize(
    ("content", "present", "needs_review"),
    [
        ("", False, False),
        ("# 再発防止セルフチェック観点\n", False, False),
        ("# 再発防止セルフチェック観点\n\n```text\n" + _ITEM + "```\n", False, False),
        ("# 再発防止セルフチェック観点\n\n# 別の文書\n- 対象条件: 条件\n- 確認内容: 内容\n- 根拠規範: 規範\n", False, False),
        (
            "# 再発防止セルフチェック観点\n## 途中\n- 対象条件: 条件\n## 別項目\n- 確認内容: 内容\n- 根拠規範: 規範\n",
            True,
            True,
        ),
        (_ITEM, True, False),
        (
            _ITEM.replace("対象条件:", "**対象条件**:")
            .replace("確認内容:", "**確認内容**:")
            .replace("根拠規範:", "**根拠規範**:"),
            True,
            True,
        ),
        (
            "# 再発防止セルフチェック観点\n\n| 対象条件 | 確認内容 | 根拠規範 |\n"
            "| --- | --- | --- |\n| 変更がある | 契約を保つ | 記録された規範 |\n",
            True,
            True,
        ),
        (
            "# 再発防止セルフチェック観点\n\n## 対象条件\n変更がある\n## 確認内容\n契約を保つ\n## 根拠規範\n記録された規範\n",
            True,
            True,
        ),
    ],
    ids=[
        "empty",
        "heading-only",
        "fenced-only",
        "other-section",
        "fields-in-different-items",
        "complete-item",
        "bold-labels",
        "table-item",
        "heading-fields",
    ],
)
def test_cli_reports_presence_of_real_items_without_requiring_a_total_count(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    content: str,
    present: bool,
    needs_review: bool,
) -> None:
    """確定した空集合だけ省略し、未認識の本文は記法を限定せず意味判断へ返す。"""
    distribution = tmp_path / "distribution"
    _write(distribution, ".claude-plugin/plugin.json", '{"name":"agent-toolkit"}')
    items = _write(distribution, "share/items.md", content)
    repository = git.init_repository(tmp_path / "repo")
    monkeypatch.setattr(recurrence_items, "PLUGIN_ROOT", distribution)
    output = tmp_path / "receipt.json"

    assert recurrence_items.main(["--worktree", str(repository), "--output", str(output)]) == 0

    response = json.loads(capsys.readouterr().out)
    assert response["観点あり"] is present
    expected_review = [str(items)] if needs_review else []
    assert response["要確認"] == expected_review
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["has_items"] is present
    assert receipt["needs_review"] == expected_review


def test_cli_reports_collection_failure_without_overwriting_saved_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """収集の前提が成立しない場合は診断付き終了2とし、既存の記録を保持する。"""
    distribution = tmp_path / "distribution"
    _write(distribution, ".claude-plugin/plugin.json", '{"name":"agent-toolkit"}')
    worktree = tmp_path / "not-a-repository"
    worktree.mkdir()
    output = _write(tmp_path, "receipt.json", "existing result\n")
    monkeypatch.setattr(recurrence_items, "PLUGIN_ROOT", distribution)

    assert recurrence_items.main(["--worktree", str(worktree), "--output", str(output)]) == 2

    assert output.read_text(encoding="utf-8") == "existing result\n"
    error = capsys.readouterr().err
    assert "Git rootを取得できない" in error
    assert "次の操作:" in error


def test_cli_preserves_distributed_collection_failure_and_does_not_report_empty_success(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検索失敗を観点なしへ変換せず、生成元の診断を返して既存記録を保持する。"""
    distribution = tmp_path / "distribution"
    _write(distribution, ".claude-plugin/plugin.json", '{"name":"agent-toolkit"}')
    repository = git.init_repository(tmp_path / "repo")
    output = _write(tmp_path, "receipt.json", "existing result\n")
    monkeypatch.setattr(recurrence_items, "PLUGIN_ROOT", distribution)
    original_run = subprocess.run

    def _run(args: list[str], *, check: bool = False, **kwargs: typing.Any) -> subprocess.CompletedProcess[bytes]:
        if args[:3] == ["git", "grep", "--no-index"]:
            return subprocess.CompletedProcess(args, 2, b"", b"collection source is unreadable")
        return original_run(args, check=check, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)

    assert recurrence_items.main(["--worktree", str(repository), "--output", str(output)]) == 2

    assert output.read_text(encoding="utf-8") == "existing result\n"
    captured = capsys.readouterr()
    assert not captured.out
    assert "collection source is unreadable" in captured.err
    assert "次の操作:" in captured.err
