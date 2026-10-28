"""宣言一覧の解決再利用をhookの要求単位で検証する。"""

import contextlib
import pathlib

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import read_delivery


def test_declarations_preserve_order_and_refresh_next_request(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """再利用の有無で宣言の順序と内容を保ち、次要求では転送先の変更を反映する。"""
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/sample/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 前 | `redirect.md` | 操作 | `tool:Judge` |\n"
        "| 後 | `redirect.md` | 操作 | `tool:Read` |\n",
        encoding="utf-8",
    )
    first, second, redirect = (skill.parent / name for name in ("first.md", "second.md", "redirect.md"))
    first.write_text("# 一\n", encoding="utf-8")
    second.write_text("# 二\n", encoding="utf-8")
    redirect.write_text(f"<!-- read-source: {'agent-toolkit'}:sample/first.md -->\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "plugin_root", lambda: root)
    monkeypatch.setattr(read_delivery, "sibling_plugin_root", lambda *_args: None)
    monkeypatch.setattr(read_triggers, "document_sources", lambda *_args: [skill])
    with monkeypatch.context() as baseline:
        baseline.setattr(read_triggers, "resolution_scope", contextlib.nullcontext)
        expected = read_delivery.declarations(str(tmp_path))
    actual = read_delivery.declarations(str(tmp_path))
    assert actual == expected
    assert len(actual) == 2 and all(row.documents == (first,) for row in actual)
    redirect.write_text(f"<!-- read-source: {'agent-toolkit'}:sample/second.md -->\n", encoding="utf-8")
    refreshed = read_delivery.declarations(str(tmp_path))
    assert [row.operations for row in refreshed] == [row.operations for row in actual]
    assert all(row.documents == (second,) for row in refreshed)
