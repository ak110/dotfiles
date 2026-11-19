"""常時規範の読込宣言と起動時の配送を確かめる。"""

import json

import pytest

from agent_toolkit._common import read_triggers


@pytest.mark.parametrize(
    "location",
    [
        "AGENTS.md",
        "CLAUDE.md",
        ".claude/skills/local/references/rule.md",
        ".chezmoi-source/dot_claude/skills/local/SKILL.md",
        ".chezmoi-source/dot_claude/skills/local/references/rule.md",
        ".chezmoi-source/dot_claude/rules/rule.md",
        ".chezmoi-source/dot_claude/rules/rule.md.tmpl",
        ".chezmoi-source/dot_gemini/antigravity-cli/skills/local/SKILL.md",
        "plugin/skills/demo/references/rule.md",
        "plugin/share/rule.md",
        "plugin/rules/rule.md",
    ],
)
@pytest.mark.parametrize(
    "declaration",
    [
        "read",
        "material-section",
        "operation-section",
        "qualified-section",
        "unqualified-section",
    ],
)
def test_normative_declarations_require_full_files(tmp_path, location, declaration):
    """規範の資料列の節指定と、撤去した配送操作`section:`を拒否し、全文宣言を受理する。"""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    target = tmp_path / location
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# 基準\n## 対象\n本文\n## 後段\n必要な例外\n", encoding="utf-8")
    source = tmp_path / "source.md"
    material = f"`{target}`" + ("の「対象」" if declaration == "material-section" else "")
    operation = f"section:{target}#対象" if declaration == "operation-section" else f"read:{target}"
    if declaration == "qualified-section":
        pattern = "agent-toolkit:*/" + location.removeprefix("plugin/") if location.startswith("plugin/") else "*/" + location
        operation = f"section:{pattern}#対象"
    elif declaration == "unqualified-section":
        operation = "section:*/" + location.removeprefix("plugin/") + "#対象"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 判断前 | {material} | 判断 | `{operation}` |\n",
        encoding="utf-8",
    )
    if declaration == "read":
        row = read_triggers.parse_tables(source, root, strict=True)[0]
        assert row.targets == (read_triggers.ReadTarget(target),)
        assert "必要な例外" in row.targets[0].body()
    elif declaration == "material-section":
        with pytest.raises(ValueError, match="エージェント向け文書") as error:
            read_triggers.parse_tables(source, root, strict=True)
        assert "分割" in str(error.value) and "read:" in str(error.value)
    else:
        # 節単位の取得は判定しないため、`section:`は配送操作の語彙に無い。
        with pytest.raises(ValueError, match="未登録の配送操作"):
            read_triggers.parse_tables(source, root, strict=True)


@pytest.mark.parametrize("location", ["AGENTS.md", ".chezmoi-source/dot_claude/skills/local/references/rule.md"])
def test_normative_startup_expands_heading_and_preserves_selectors(tmp_path, location):
    target = tmp_path / location
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# 基準\n## 対象\n本文\n## 後段\n必要な例外\n", encoding="utf-8")
    first = read_triggers.ReadTarget(target, "対象")
    second = read_triggers.ReadTarget(target, "後段")
    context = read_triggers.startup_documents([first, second], tmp_path, {first: {"input:一"}, second: {"input:二"}})
    metadata = [json.loads(line.removeprefix("資料JSON: ")) for line in context.splitlines() if line.startswith("資料JSON: ")]
    assert len(metadata) == 1
    assert metadata[0]["heading"] == ""
    assert metadata[0]["selectors"] == ["input:一", "input:二"]
    assert context.count(target.read_text(encoding="utf-8")) == 1
    role = tmp_path / "example.subagent.md"
    role.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 起動時 | `{target}#対象` | 起動時 | `role:example` |\n",
        encoding="utf-8",
    )
    assert read_triggers.startup_targets(role, tmp_path) == (read_triggers.ReadTarget(target),)
    assert target.read_text(encoding="utf-8") in read_triggers.startup_context(role, tmp_path, {})


@pytest.mark.parametrize(
    "name,heading,body",
    [
        ("record.md", "対象", "# 記録\n## 対象\n記録本文\n## 後段\n対象外\n"),
        ("Makefile", "make:build", "VALUE = x\nbuild:\n\techo build\nother:\n\techo other\n"),
    ],
)
def test_record_and_makefile_sections_remain_valid_declarations(tmp_path, name, heading, body):
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# プロジェクト\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 調査時 | `{name}#{heading}` | 判断 | `read:*/{name}` |\n",
        encoding="utf-8",
    )
    row = read_triggers.parse_tables(source, tmp_path, strict=True)[0]
    assert row.targets == (read_triggers.ReadTarget(target, heading),)
    assert "対象外" not in row.targets[0].body() and "echo other" not in row.targets[0].body()


@pytest.mark.parametrize("host", ["claude", "codex", "agents", "gemini"])
@pytest.mark.parametrize(
    "relative,expected",
    [
        ("skills/local/SKILL.md", True),
        ("skills/local/references/rule.md", True),
        ("rules/rule.md", True),
        ("rules/rule.md.tmpl", True),
        ("skills/local/script.py", False),
        ("docs/record.md", False),
        ("notes/rule.md", False),
    ],
)
def test_chezmoi_normative_sources_keep_host_scope(tmp_path, host, relative, expected):
    """配布元の規範・テンプレートを認識し、同じ配布元の記録とコードを区別する。"""
    path = tmp_path / ".chezmoi-source" / f"dot_{host}" / relative
    assert read_triggers.is_normative_document(path) is expected
    assert not read_triggers.is_normative_document(tmp_path / f"dot_{host}" / relative)


def test_record_template_stays_outside_read_declarations(tmp_path):
    """規範原本のテンプレート対応を、通常の記録テンプレートへ広げない。"""
    record = tmp_path / ".chezmoi-source/dot_claude/docs/record.md.tmpl"
    record.parent.mkdir(parents=True)
    record.write_text("# 記録\n", encoding="utf-8")
    assert read_triggers.resolve_document(str(record), tmp_path / "source.md", tmp_path) is None
