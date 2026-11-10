"""条件別規範の読取ヘルパーが欠落や別条件を受理しないことを確かめる。"""

import pytest

from agent_toolkit._testing.normative_contract import read_contract


@pytest.mark.parametrize("defect", ["", "condition", "target", "section", "empty"])
def test_contract_requires_selected_whole_document_route(tmp_path, defect):
    root = tmp_path / "agent-toolkit"
    share = root / "share"
    share.mkdir(parents=True)
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    source = share / "entry.md"
    target = share / "selected.md"
    target.write_text("" if defect == "empty" else "# 選択資料\n\n## 本文\n\n必要な本文。\n", encoding="utf-8")
    (share / "other.md").write_text("# 別資料\n\n別用途。\n", encoding="utf-8")
    condition = "別条件" if defect == "condition" else "実装前"
    material = "other.md" if defect == "target" else "selected.md"
    if defect == "section":
        material += "#本文"
    source.write_text(
        "# 入口\n\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        f"| {condition} | `{material}` | 判断 | `read:agent-toolkit:*/share/selected.md` |\n",
        encoding="utf-8",
    )
    if defect:
        with pytest.raises((AssertionError, ValueError)):
            read_contract(source, root, ("selected.md", "実装前"))
    else:
        text = read_contract(source, root, ("selected.md", "実装前"))
        assert "必要な本文。" in text
        assert "別用途。" not in text
