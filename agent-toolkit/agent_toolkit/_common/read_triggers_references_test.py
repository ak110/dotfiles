"""同時に読む資料の参照行の重複を検証する。"""

import pathlib

from agent_toolkit._common import read_triggers


def _skill_with_references(tmp_path, body: str, *names: str) -> pathlib.Path:
    """pluginが所有するスキルと参照資料を置き、スキル本文のパスを返す。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    source = tmp_path / "skills/example/SKILL.md"
    (source.parent / "references").mkdir(parents=True)
    for name in names:
        (source.parent / "references" / name).write_text("# 基準\n", encoding="utf-8")
    source.write_text(body, encoding="utf-8")
    return source


def test_simultaneous_duplicate_rows_detects_rows_shared_by_documents_read_together(tmp_path):
    """入口の1行で同時に読む2資料が同じ行を持てば検出し、片方から消すと検出しない。"""
    table = "| 時点または条件 | 全文読む資料 |\n| --- | --- |\n"
    shared = "| 判定の前 | `rule.md` |\n"
    source = _skill_with_references(
        tmp_path,
        f"# 入口\n\n{table}| 起草の前 | `references/a.md`、`references/b.md` |\n| 確認の前 | `references/c.md` |\n",
        "rule.md",
    )
    references = source.parent / "references"
    first, second, alone = references / "a.md", references / "b.md", references / "c.md"
    first.write_text(f"# 分割先A\n\n{table}{shared}", encoding="utf-8")
    second.write_text(f"# 分割先B\n\n{table}{shared}| 保存の前 | `rule.md` |\n", encoding="utf-8")
    # 同時に読まない資料が同じ行を持っても重複読込にならない。
    alone.write_text(f"# 単独\n\n{table}{shared}", encoding="utf-8")

    def issues() -> list[str]:
        sources = (source, first, second, alone)
        return read_triggers.simultaneous_duplicate_rows(
            {path: read_triggers.parse_tables(path, tmp_path, strict=True) for path in sources}
        )

    found = issues()
    assert len(found) == 1
    assert f"{first}:5" in found[0] and f"{second}:5" in found[0] and f"{source}:5" in found[0]
    assert "判定の前" in found[0] and str(references / "rule.md") in found[0]
    second.write_text(f"# 分割先B\n\n{table}| 保存の前 | `rule.md` |\n", encoding="utf-8")
    assert not issues()
