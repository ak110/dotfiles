"""指定節の照会がWIの種別とコードフェンスを通して本文を保持することを検証する。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


@pytest.mark.parametrize("selection", [["awi.md"], ["awi.md", "uwi.md"], ["--all"]])
def test_show_sections_preserves_repeated_sections_and_fenced_headings(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selection: list[str],
) -> None:
    """単件・複数・全件の照会は同名節を全て表示し、引用内のH2を本文に保つ。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(
        notes,
        "awi.md",
        body=(
            "# 表題\n\n## 完成条件\n第一条件\n```text\n## 内側の見出し\n引用本文\n```\n"
            "## 対象外\n出力しない本文\n## 完成条件\n第二条件\n"
        ),
    )
    _write_uwi_file(notes, "uwi.md", question="質問本文", answer="回答本文")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as result:
        atk.main(
            ["wi", "show", *selection, "--section", "完成条件", "--section", "回答", "--target-repo=all", "--skip-pull"],
            home=tmp_path,
        )
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "### awi.md [inbox]" in output
    assert output.count("#### 完成条件") == 2
    assert "第一条件\n```text\n## 内側の見出し\n引用本文\n```" in output
    assert "第二条件" in output
    assert "出力しない本文" not in output
    assert "# 表題" not in output
    assert "target_repo:" in output
    if selection != ["awi.md"]:
        assert "### uwi.md [inbox/answered]" in output
        assert "#### 回答\n回答本文" in output
        assert "質問本文" not in output


def test_show_section_and_summary_are_exclusive(tmp_path: pathlib.Path) -> None:
    """要約と指定節の照会は同時指定を拒否する。"""
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "show", "awi.md", "--summary-only", "--section", "完成条件"], home=tmp_path)
    assert result.value.code == 2
