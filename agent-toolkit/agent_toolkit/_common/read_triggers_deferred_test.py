"""操作の対象外の資料を先行解決しない契約を確認する。"""

import pytest

from agent_toolkit._common import read_triggers


def _table(material):
    return (
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        f"| 必要な操作 | `{material}` | 操作 | `tool:Write` |\n"
    )


def test_deferred_tables_resolve_only_selected_material_and_refresh_next_scope(tmp_path):
    source = tmp_path / "SKILL.md"
    target = tmp_path / "first.md"
    target.write_text("最初の資料\n", encoding="utf-8")
    source.write_text(_table(target.name) + _table("missing.md"), encoding="utf-8")
    with read_triggers.resolution_scope():
        rows = read_triggers.parse_tables(source, tmp_path, defer_material=True)
        assert rows[0].operations == ("tool:Write",)
        assert rows[0].targets == (read_triggers.ReadTarget(target),)
        with pytest.raises(ValueError, match="資料が存在しない"):
            _ = rows[1].targets
    replacement = tmp_path / "second.md"
    replacement.write_text("次の資料\n", encoding="utf-8")
    source.write_text(_table(replacement.name), encoding="utf-8")
    with read_triggers.resolution_scope():
        current = read_triggers.parse_tables(source, tmp_path, defer_material=True)
        assert current[0].targets == (read_triggers.ReadTarget(replacement),)


def test_strict_validation_resolves_unselected_material(tmp_path):
    source = tmp_path / "SKILL.md"
    source.write_text(_table("missing.md"), encoding="utf-8")
    with pytest.raises(ValueError, match="資料が存在しない"):
        read_triggers.parse_tables(source, tmp_path, strict=True, defer_material=True)


def test_deferred_tables_still_reject_invalid_operation(tmp_path):
    source = tmp_path / "SKILL.md"
    source.write_text(_table("missing.md").replace("tool:Write", "unregistered"), encoding="utf-8")
    with pytest.raises(ValueError, match="未登録の配送操作"):
        read_triggers.parse_tables(source, tmp_path, defer_material=True)


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("kind", ["起動時", "操作"])
def test_startup_resolves_selected_rows_and_preserves_missing_material_diagnostic(tmp_path, host, kind):
    source = tmp_path / "example.subagent.md"
    target = tmp_path / "startup.md"
    target.write_text("# 起動資料\n起動前の基準\n", encoding="utf-8")
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 起動時 | `startup.md` | {kind} | `role:example` |\n"
        "| 編集前 | `missing.md` | 操作 | `tool:Write` |\n",
        encoding="utf-8",
    )
    assert read_triggers.startup_targets(source, tmp_path, host=host) == (read_triggers.ReadTarget(target),)
    assert "起動前の基準" in read_triggers.startup_context(source, tmp_path, {}, host=host)
    target.unlink()
    with pytest.raises(ValueError, match="資料が存在しない"):
        read_triggers.startup_context(source, tmp_path, {}, host=host)
