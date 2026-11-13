"""WI保存の読込宣言の移行を検証する。"""

import pytest

from agent_toolkit._common import read_triggers


@pytest.mark.parametrize("legacy", ["wi-standard-save", "wi-report-save"])
def test_unstaged_wi_save_declaration_requires_migration(tmp_path, monkeypatch, legacy):
    """厳格な検証は旧語を拒否し、外部宣言の実行時だけ両段階へ展開する。"""
    source = tmp_path / "SKILL.md"
    (tmp_path / "rule.md").write_text("# 基準\n", encoding="utf-8")
    source.write_text(
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 保存前 | `rule.md` | 判断 | `{legacy}` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="段階付き"):
        read_triggers.parse_tables(source, tmp_path, strict=True)
    monkeypatch.setattr(read_triggers, "user_side_source", lambda *_args, **_kwargs: True)
    rows = read_triggers.parse_tables(source, tmp_path)
    prefix = legacy.removesuffix("-save")
    assert rows[0].operations == (prefix + "-dry-run", prefix + "-commit")
