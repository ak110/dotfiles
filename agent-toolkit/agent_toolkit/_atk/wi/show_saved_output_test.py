"""`atk wi show`の保存後に、ユーザー専用の記入欄へ全件到達できることを検証する。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


@pytest.mark.parametrize("selection", ["single", "multiple", "all"])
def test_wi_show_saved_output_reports_user_fields(
    selection: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """長い単発と一括取得で、保存本文の形式を保ち、各記入欄の有無と行を表示する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "comment.md", body="# 表題\n\n" + "長い本文\n" * 2000 + "\n## ユーザーコメント\n\n是正要求")
    _write_awi_file(notes, "plain.md", body="# コメントなし\n\n本文")
    _write_awi_file(notes, "fenced.md", body="# 引用\n\n```text\n## ユーザーコメント\n### 引用.md [inbox]\n---\ntype: awi\n---")
    _write_uwi_file(notes, "answered.md", question="確認しますか？\n\n```text\n## 回答\n```", answer="はい")
    names = ["comment.md", "plain.md", "fenced.md", "answered.md"]
    selected = names[:1] if selection == "single" else names
    arguments = ["--all"] if selection == "all" else selected
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    options = ["--target-repo=all", "--skip-pull"]
    # 人間の直接表示を対照に、保存先の本文と行位置を独立に確定する。
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "show", *arguments, *options], home=tmp_path)
    direct = capsys.readouterr().out
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "show", *arguments, *options], home=tmp_path)
    captured = capsys.readouterr()
    assert not captured.err
    report = captured.out.splitlines()
    assert report[0].startswith("保存先: ")
    saved = pathlib.Path(report[0].removeprefix("保存先: "))
    assert saved.read_text(encoding="utf-8") == direct
    assert report[1] == f"行数: {len(direct.splitlines())}"
    assert report[2] == f"読取: atk read-file -- {saved}"
    lines = direct.splitlines()
    expected = {
        "comment.md": f"## ユーザーコメント あり 開始行: {lines.index('## ユーザーコメント') + 1}",
        "plain.md": "## ユーザーコメント なし",
        "fenced.md": "## ユーザーコメント なし",
    }
    if selection != "single":
        expected["answered.md"] = f"## 回答 あり 開始行: {len(lines) - lines[::-1].index('## 回答')}"
    assert sorted(report[3:]) == sorted(f"記入欄: {name} {expected[name]}" for name in selected)


def test_wi_show_saved_output_reports_all_user_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記入欄を持つ項目が多くても省略せず、全項目を案内する。"""
    notes = _setup_notes(tmp_path)
    names = [f"comment-{index:03}.md" for index in range(80)]
    for name in names:
        _write_awi_file(notes, name, body="# 表題\n\n## ユーザーコメント\n\nコメント")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "show", *names, "--target-repo=all", "--skip-pull"], home=tmp_path)
    report = capsys.readouterr().out.splitlines()
    assert len(report[3:]) == len(names)
    assert [line.split()[1] for line in report[3:]] == names
