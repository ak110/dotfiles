"""自然文の節指定と、起動時の判断前配送を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import read_triggers


def test_body_reading_table_keeps_targets_and_scope(tmp_path):
    owner = tmp_path / "SKILL.md"
    target = tmp_path / "rule.md"
    target.write_text("# 資料\n## 対象\n判断基準\n## 別節\n別用途\n", encoding="utf-8")
    owner.write_text(
        "# 本体\n\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 選択時 | `rule.md#対象` | 判断 | `read:*/rule.md` |\n",
        encoding="utf-8",
    )
    rows = read_triggers.parse_tables(owner, tmp_path, strict=True)
    assert len(rows) == 1
    assert rows[0].source == owner
    assert rows[0].documents == (target,)
    assert rows[0].headings == ("対象",)
    assert rows[0].condition == "選択時"
    assert "判断基準" in rows[0].targets[0].body()
    assert "別用途" not in rows[0].targets[0].body()
    read_triggers.validate_reference_routes(owner, tmp_path, rows)


def test_dynamic_document_identity_is_canonical(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    path = tmp_path / "rule.md"
    path.write_text("# 基準\n", encoding="utf-8")
    target = read_triggers.ReadTarget(directory / "../rule.md")
    assert target.path == path
    assert target == read_triggers.ReadTarget(path)


def test_resolution_scope_shares_manifests_and_refreshes_the_next_request(tmp_path, monkeypatch):
    toolkit, basics = tmp_path / "agent-toolkit", tmp_path / "agent-basics"
    for root in (toolkit, basics):
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(f'{{"name":"{root.name}"}}', encoding="utf-8")
        for name in ("sample", "other"):
            target = root / "skills" / name / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("# 基準\n", encoding="utf-8")
    reads: dict[pathlib.Path, int] = {}
    original = pathlib.Path.read_text

    def counted(path, *args, **kwargs):
        if path.name == "plugin.json":
            reads[path] = reads.get(path, 0) + 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", counted)
    with read_triggers.resolution_scope():
        for filename in ("one.md", "two.md"):
            source = toolkit / "share" / filename
            for root in (toolkit, basics):
                for name in ("sample", "other"):
                    assert read_triggers.resolve_document(f"{root.name}:{name}", source, toolkit) == (
                        root / "skills" / name / "SKILL.md"
                    )
        assert reads == {root / ".claude-plugin/plugin.json": 1 for root in (toolkit, basics)}
    (toolkit / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    with read_triggers.resolution_scope():
        assert read_triggers.resolve_document("agent-basics:sample", toolkit / "share/one.md", toolkit) == (
            toolkit / "skills/sample/SKILL.md"
        )
    assert reads[toolkit / ".claude-plugin/plugin.json"] == 2


def test_nested_fence_keeps_search_headings_and_tables_outside_contract(tmp_path):
    path = tmp_path / "rule.md"
    text = (
        "# 基準\n## 対象\n本文\n````md\n```text\n## 偽境界\n```\n"
        "| 条件 | 全文読む資料 |\n| 例 | `missing.md` |\n````\n## 後段\n後段本文\n"
    )
    path.write_text(text, encoding="utf-8")
    assert "偽境界" in read_triggers.ReadTarget(path, "対象").body()
    assert "後段本文" not in read_triggers.ReadTarget(path, "対象").body()
    assert not read_triggers.parse_tables(path, tmp_path, strict=True)


def test_scope_with_inline_code_is_one_document_target(tmp_path):
    target = tmp_path / "references/rule.md"
    target.parent.mkdir()
    heading = "`<役割名>.parent.md`と`<役割名>.subagent.md`の自己完結"
    target.write_text("# 基準\n## " + heading + "\n対象本文\n## 後段\n後段本文\n", encoding="utf-8")
    targets = read_triggers.material_targets("`references/rule.md`の「" + heading + "」", tmp_path / "SKILL.md", tmp_path)
    assert len(targets) == 1
    assert targets[0].heading == "「" + heading + "」"
    assert "対象本文" in targets[0].body()
    assert "後段本文" not in targets[0].body()


def test_heading_symbol_scope_keeps_only_the_named_section(tmp_path):
    path = tmp_path / "references/rule.md"
    path.parent.mkdir()
    path.write_text("# 基準\n## 対象\n対象本文\n## 後段\n後段本文\n", encoding="utf-8")
    targets = read_triggers.material_targets("`references/rule.md`の`## 対象`", tmp_path / "SKILL.md", tmp_path)
    assert len(targets) == 1
    assert targets[0].heading == "「対象」"
    assert "対象本文" in targets[0].body()
    assert "後段本文" not in targets[0].body()


def test_heading_containing_preamble_word_keeps_its_scope(tmp_path):
    path = tmp_path / "rule.md"
    path.write_text("# 資料\n前置き\n## 冒頭の責務と目的\n対象節\n## 後段\n後段本文\n", encoding="utf-8")
    assert (
        read_triggers.material_targets("`rule.md`の「冒頭の責務と目的」", path, tmp_path)[0].heading == "「冒頭の責務と目的」"
    )
    body = read_triggers.ReadTarget(path, "「冒頭の責務と目的」").body()
    assert "対象節" in body
    assert "前置き" not in body
    assert "後段本文" not in body


@pytest.mark.parametrize(
    "header,row,diagnostic",
    [
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `rule.md` | 不明 | `tool:Do` |", "不明な契機"),
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `rule.md` | 判断 | |", "配送操作が無い"),
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `rule.md` | 操作 | `unknown:Do` |", "未登録の配送操作"),
        ("| 条件 | 全文読む資料 | 契機 | 配送操作 |", "| 条件 | `missing.md` | 操作 | `tool:Do` |", "資料が存在しない"),
        (
            "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |",
            "| 条件 | `rule.md` | 操作 | `tool:Do` | `modified-agent-documents` |",
            "未登録の動的資料選択子",
        ),
    ],
)
def test_invalid_table_rows_fail_the_ci_parser(tmp_path, header, row, diagnostic):
    reference = tmp_path / "rule.md"
    reference.write_text("# 基準\n", encoding="utf-8")
    source = tmp_path / "SKILL.md"
    source.write_text(header + "\n" + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        read_triggers.parse_tables(source, tmp_path, strict=True)


def test_registered_dynamic_selector_passes_the_ci_parser(tmp_path):
    (tmp_path / "rule.md").write_text("# 基準\n", encoding="utf-8")
    source = tmp_path / "SKILL.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 操作時 | `rule.md` | 操作 | `tool:Do` | `same-document` |\n",
        encoding="utf-8",
    )
    assert read_triggers.parse_tables(source, tmp_path, strict=True)[0].selector == "same-document"


def test_input_selector_without_a_registered_main_resolution_source_fails_ci(tmp_path):
    source = tmp_path / "SKILL.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 起動時 | 起動入力 | 起動時 | `tool:Example` | `input:未登録` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未登録の動的資料選択子"):
        read_triggers.parse_tables(source, tmp_path, strict=True)


@pytest.mark.parametrize("name", ["bugfix", "review-standards"])
@pytest.mark.parametrize("qualified", [False, True])
def test_skill_operation_requires_the_declared_plugin_owner(tmp_path, name, qualified):
    """他pluginのスキルを短縮名で誤解決せず、完全名で宣言できる。"""
    toolkit = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    for root in (toolkit, basics):
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(f'{{"name":"{root.name}"}}', encoding="utf-8")
    skill = basics / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 基準\n", encoding="utf-8")
    source = toolkit / "share/source.md"
    source.parent.mkdir()
    operation = f"skill:agent-basics:{name}" if qualified else f"skill:{name}"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 判断前 | `agent-basics:{name}/SKILL.md` | 判断 | `{operation}` |\n",
        encoding="utf-8",
    )
    if qualified:
        row = read_triggers.parse_tables(source, toolkit, strict=True)[0]
        assert row.documents == (skill,) and row.operations == (operation,)
    else:
        with pytest.raises(ValueError, match="配送操作のスキルが存在しない"):
            read_triggers.parse_tables(source, toolkit, strict=True)


@pytest.mark.parametrize("operation", ["skill:bugfix", "skill:agent-toolkit:missing"])
def test_missing_skill_operation_explains_repair_and_revalidation(tmp_path, operation):
    toolkit = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    for root in (toolkit, basics):
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(f'{{"name":"{root.name}"}}', encoding="utf-8")
    skill = basics / "skills/bugfix/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 基準\n", encoding="utf-8")
    source = toolkit / "share/source.md"
    source.parent.mkdir()
    (source.parent / "rule.md").write_text("# 規則\n", encoding="utf-8")
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 判断前 | `rule.md` | 判断 | `{operation}` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="配送操作のスキルが存在しない") as failure:
        read_triggers.parse_tables(source, toolkit, strict=True)
    diagnostic = str(failure.value)
    print(diagnostic)
    assert f"{source}:3:" in diagnostic and operation in diagnostic
    assert "同一plugin" in diagnostic and "skill:<スキル名>" in diagnostic
    assert "他plugin" in diagnostic and "skill:<plugin名>:<スキル名>" in diagnostic
    assert "skills/<スキル名>/SKILL.md" in diagnostic
    assert "対象worktreeのルート" in diagnostic
    assert (
        "uv run --frozen --no-sync pytest -n 0 "
        "agent-toolkit/read_triggers_invariant_test.py::test_all_read_tables_have_valid_declarations_and_routes"
    ) in diagnostic


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("state", ["redirected", "missing-entry", "missing-target", "cycle", "restored"])
def test_skill_operation_validates_entry_and_redirected_material(tmp_path, qualified, state):
    """スキルの実在と全文資料への転送を分け、原本へ戻した次の解析も成立させる。"""
    root = tmp_path / "agent-basics-agy"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    entry = root / "skills/long/SKILL.md"
    full = entry.parent / "references/antigravity-full-instructions.md"
    full.parent.mkdir(parents=True)
    original = "# 長文スキル\n原本の基準\n"
    entry.write_text(original, encoding="utf-8")
    full.write_text("# 長文スキル\n全文の基準\n", encoding="utf-8")
    source = root / "share/source.md"
    source.parent.mkdir()
    operation = "skill:agent-basics:long" if qualified else "skill:long"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 起動時 | `../skills/long/SKILL.md` | 起動時 | `{operation}` |\n",
        encoding="utf-8",
    )
    with read_triggers.resolution_scope():
        assert read_triggers.parse_tables(source, root, strict=True)[0].documents == (entry,)
    entry.write_text("<!-- read-source: agent-basics:long/references/antigravity-full-instructions.md -->\n", encoding="utf-8")
    if state == "missing-entry":
        entry.unlink()
    elif state == "missing-target":
        full.unlink()
    elif state == "cycle":
        full.write_text("<!-- read-source: agent-basics:long/SKILL.md -->\n", encoding="utf-8")
    if state in {"missing-entry", "missing-target", "cycle"}:
        diagnostic = {
            "missing-entry": "配送操作のスキルが存在しない",
            "missing-target": "read-sourceの実体が無い",
            "cycle": "循環",
        }
        with pytest.raises(ValueError, match=diagnostic[state]), read_triggers.resolution_scope():
            read_triggers.parse_tables(source, root, strict=True)
        return
    with read_triggers.resolution_scope():
        row = read_triggers.parse_tables(source, root, strict=True)[0]
        assert row.documents == (full,) and row.operations == (operation,)
    if state == "restored":
        entry.write_text(original, encoding="utf-8")
        with read_triggers.resolution_scope():
            row = read_triggers.parse_tables(source, root, strict=True)[0]
            assert row.documents == (entry,) and row.operations == (operation,)


def test_role_document_accepts_its_startup_input_selector(tmp_path):
    source = tmp_path / "example.subagent.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 起動時 | 起動入力 | 起動時 | `role:example` | `input:委譲入力` |\n",
        encoding="utf-8",
    )
    assert read_triggers.parse_tables(source, tmp_path, strict=True)[0].selector == "input:委譲入力"


def test_role_document_rejects_an_empty_input_selector(tmp_path):
    source = tmp_path / "example.subagent.md"
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 起動時 | 起動入力 | 起動時 | `role:example` | `input:` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="未登録の動的資料選択子"):
        read_triggers.parse_tables(source, tmp_path, strict=True)


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


def test_named_reference_without_row_gets_supplied_declaration(tmp_path):
    """本文が名指しし表に行の無い資料へ、定型の条件と資料自身の読取の宣言を補う。"""
    source = _skill_with_references(tmp_path, "判定は`references/rule.md`に従う。\n", "rule.md")
    reference = source.parent / "references/rule.md"
    read_triggers.validate_reference_routes(source, tmp_path, [])
    rows = read_triggers.parse_tables(source, tmp_path, strict=True, named=True)
    assert [(row.line, row.kind, row.condition, row.documents) for row in rows] == [
        (1, "判断", read_triggers.NAMED_REFERENCE_CONDITION, (reference,))
    ]
    assert rows[0].effective_operations == ("read:agent-toolkit:*/skills/example/references/rule.md",)
    # 全文書を毎回読む呼出しは補完を求めない。
    assert not read_triggers.parse_tables(source, tmp_path, strict=True)


def test_named_reference_with_explicit_row_or_location_note_is_not_supplied(tmp_path):
    """明示の行がある資料、所在参照の行、自身への参照には補わない。"""
    source = _skill_with_references(
        tmp_path,
        "判定は`references/rule.md`に従う。\n"
        "定義は`references/other.md`にある（所在参照）。\n"
        "本書の`SKILL.md`を読む。\n\n"
        "| 時点または条件 | 全文読む資料 |\n| --- | --- |\n| 判定の前 | `references/rule.md` |\n",
        "rule.md",
        "other.md",
    )
    rows = read_triggers.parse_tables(source, tmp_path, strict=True, named=True)
    assert [(row.condition, row.documents) for row in rows] == [("判定の前", (source.parent / "references/rule.md",))]


def test_named_reference_to_missing_document_fails_ci(tmp_path):
    source = _skill_with_references(tmp_path, "判定は`references/missing.md`に従う。\n")
    with pytest.raises(ValueError, match="参照先が存在しない") as caught:
        read_triggers.validate_reference_routes(source, tmp_path, [])
    assert f"{source}:1:" in str(caught.value)


def test_omitted_trigger_columns_derive_judgment_and_self_read(tmp_path):
    """2列の表と両セルが空の行は、契機`判断`と資料自身の読取として解析する。"""
    source = _skill_with_references(
        tmp_path,
        "| 時点または条件 | 全文読む資料 |\n| --- | --- |\n| 判定の前 | `references/rule.md` |\n\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 別の判定の前 | `references/other.md` | | |\n"
        "| 検索の前 | `references/rule.md` | 操作 | `search` |\n",
        "rule.md",
        "other.md",
    )
    rows = read_triggers.parse_tables(source, tmp_path, strict=True)
    assert [(row.kind, row.operations, row.derived_read) for row in rows] == [
        ("判断", (), True),
        ("判断", (), True),
        ("操作", ("search",), False),
    ]
    assert [row.effective_operations for row in rows] == [
        ("read:agent-toolkit:*/skills/example/references/rule.md",),
        ("read:agent-toolkit:*/skills/example/references/other.md",),
        ("search",),
    ]
    deferred = read_triggers.parse_tables(source, tmp_path, defer_material=True)
    assert [row.effective_operations for row in deferred] == [row.effective_operations for row in rows]


@pytest.mark.parametrize(
    "cells,message",
    [
        ("| 判断 | |", "配送操作が無い"),
        ("| | `read:agent-toolkit:*/skills/example/references/rule.md` |", "不明な契機"),
    ],
)
def test_partially_omitted_trigger_columns_are_rejected(tmp_path, cells, message):
    """片方だけの省略は、宣言の書き漏れと区別できないため拒否する。"""
    source = _skill_with_references(
        tmp_path,
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 判定の前 | `references/rule.md` {cells}\n",
        "rule.md",
    )
    with pytest.raises(ValueError, match=message):
        read_triggers.parse_tables(source, tmp_path, strict=True)


def test_selector_row_requires_explicit_trigger_columns(tmp_path):
    source = _skill_with_references(
        tmp_path,
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n| --- | --- | --- | --- | --- |\n"
        "| 判定の前 | `references/rule.md` | | | `same-document` |\n",
        "rule.md",
    )
    with pytest.raises(ValueError, match="資料選択子を持つ行は契機と配送操作を宣言する"):
        read_triggers.parse_tables(source, tmp_path, strict=True)


def _reference_method_documents(tmp_path, package="share"):
    root = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    for plugin in (root, basics):
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin/plugin.json").write_text(f'{{"name":"{plugin.name}"}}', encoding="utf-8")
    method = basics / "skills/writing-standards/references/read-method.md"
    method.parent.mkdir(parents=True)
    method.write_text("# 読込方法\n全文を判断前に取得する。\n## 前提\n必須の前提\n", encoding="utf-8")
    source = root / ("skills/example/references/rule.md" if package == "skill" else "share/example.subagent.md")
    source.parent.mkdir(parents=True)
    source.write_text(
        "# 消費側\n## 名指し参照の読込方法\n名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。\n",
        encoding="utf-8",
    )
    return root, source, method


@pytest.mark.parametrize("package", ["skill", "share"])
def test_common_reference_method_has_a_full_common_route(tmp_path, package):
    root, source, method = _reference_method_documents(tmp_path, package)
    targets = read_triggers.reference_method_targets(source, root)
    assert targets == (read_triggers.ReadTarget(method),)
    assert targets[0].body() == method.read_text(encoding="utf-8")
    read_triggers.validate_reference_routes(source, root, [])
    # 共通の読込方法の定義元には宣言を補わず、他の名指しにだけ補う。
    assert not read_triggers.named_reference_rows(source, root)
    with source.open("a", encoding="utf-8") as stream:
        stream.write("\n別の判断は`other.md`に従う。\n")
    other = source.parent / "other.md"
    other.write_text("# 他資料\n", encoding="utf-8")
    assert [row.documents for row in read_triggers.named_reference_rows(source, root)] == [(other,)]
    # `../`で始まる相対パスは名指しに数えず、宣言を補わない。候補として案内する資料は、表の明示の行で宣言する。
    sibling = source.parent.parent / "sibling" / "guide.md"
    sibling.parent.mkdir()
    sibling.write_text("# 隣の資料\n", encoding="utf-8")
    with source.open("a", encoding="utf-8") as stream:
        stream.write("\n隣の判断は`../sibling/guide.md`「隣の資料」に従う。\n")
    assert [row.documents for row in read_triggers.named_reference_rows(source, root)] == [(other,)]
    with source.open("a", encoding="utf-8") as stream:
        stream.write("\n| 時点または条件 | 全文読む資料 |\n| --- | --- |\n| 隣の判断の前 | `../sibling/guide.md` |\n")
    declared = [row for row in read_triggers.parse_tables(source, root, strict=True) if row.documents == (sibling,)]
    assert [(row.kind, row.condition) for row in declared] == [("判断", "隣の判断の前")]


def test_role_startup_receives_the_common_reference_method(tmp_path):
    root, role, method = _reference_method_documents(tmp_path)
    context = read_triggers.startup_context(role, root, {})
    assert context.count(method.read_text(encoding="utf-8")) == 1
    assert read_triggers.startup_targets(role, root) == (read_triggers.ReadTarget(method),)


@pytest.mark.parametrize("defect", ["heading", "wrong-owner", "missing-file"])
def test_invalid_common_reference_method_is_not_an_implicit_route(tmp_path, defect):
    root, source, method = _reference_method_documents(tmp_path)
    text = source.read_text(encoding="utf-8")
    if defect == "heading":
        source.write_text(text.replace("read-method.md`", "read-method.md#前提`"), encoding="utf-8")
    elif defect == "wrong-owner":
        other = method.with_name("other.md")
        other.write_bytes(method.read_bytes())
        source.write_text(text.replace("read-method.md", "other.md"), encoding="utf-8")
    else:
        method.unlink()
    with pytest.raises(ValueError, match="ファイル全体|定義元|存在しない"):
        read_triggers.reference_method_targets(source, root)


@pytest.mark.parametrize("location", ["note.md", "other/share/note.md"])
def test_common_reference_method_does_not_read_documents_outside_its_package(tmp_path, location):
    source = tmp_path / location
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"# note\n\xff")
    assert read_triggers.reference_method_targets(source, tmp_path / "plugin") == ()


def test_make_target_scope_includes_children_and_shared_variables(tmp_path):
    path = tmp_path / "Makefile"
    path.write_text(
        "VAR := shared\n\nhelp:\n\t@cat Makefile\nupdate: sync\n\t$(MAKE) test\n"
        "sync:\n\tuv sync\ntest:\n\tpytest\nother:\n\techo other\n",
        encoding="utf-8",
    )
    body = read_triggers.ReadTarget(path, "make:update").body()
    assert all(value in body for value in ("VAR := shared", "update:", "sync:", "test:"))
    assert "@cat Makefile" not in body
    assert "echo other" not in body
    with pytest.raises(ValueError, match="targetが存在しない"):
        read_triggers.ReadTarget(path, "make:missing").body()


@pytest.fixture(name="source")
def _source(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "reference.md"
    path.write_text(
        "# 基準\n前置き\n\n## 共通\n共通本文\n```md\n## 偽境界\n```\n"
        "\n### 下位\n下位本文\n\n## API設計\nAPI本文\n\n## 後段\n後段本文\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "selector,expected,excluded",
    [
        ("共通", ("共通本文", "下位本文", "偽境界"), ("API本文", "後段本文")),
        ("の「共通」「API設計」", ("共通本文", "API本文"), ("後段本文", "前置き")),
        ("の「共通」から「API設計」まで", ("共通本文", "API本文"), ("後段本文",)),
        ("冒頭", ("前置き",), ("共通本文", "API本文")),
    ],
)
def test_section_union_range_preamble_and_fences(source, selector, expected, excluded):
    body = read_triggers.ReadTarget(source, selector).body()
    assert all(text in body for text in expected)
    assert all(text not in body for text in excluded)


def test_material_qualifier_and_japanese_selector(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    skill = tmp_path / "skills/search"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (skill / "references").mkdir()
    reference = skill / "references/rule.md"
    reference.write_text("# 基準\n\n## 前段\n前段本文\n\n## 後段\n後段本文\n", encoding="utf-8")
    targets = read_triggers.material_targets(
        "`agent-basics:search`の`references/rule.md`の「前段」", tmp_path / "caller.md", tmp_path
    )
    assert len(targets) == 1
    assert targets[0].path == reference
    assert "後段本文" not in targets[0].body()


@pytest.mark.parametrize(
    "invocation,relative",
    [
        ("process-wi single-lane", "process-wi/references/single-lane.md"),
        ("process-wi all-repos", "process-wi/references/all-repos.md"),
        ("process-wi single-lanex", "process-wi/SKILL.md"),
        ("review-session batch auto", "review-session/references/batch.md"),
        ("review-session batch review.md", "review-session/references/batch.md"),
        ("review-session focus.md", "review-session/SKILL.md"),
        ("review-session batchx", "review-session/SKILL.md"),
    ],
)
def test_material_mode_invocations_resolve_exactly(tmp_path, invocation, relative):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    targets = read_triggers.material_targets(f"`agent-toolkit:{invocation}`", tmp_path / "caller.md", tmp_path)
    assert len(targets) == 1
    assert targets[0].path == tmp_path / "skills" / relative


@pytest.mark.parametrize(
    "text,foreign",
    [
        ("`agent-basics:search`の`references/rule.md`", True),
        ("`agent-basics:search`を起動し、`references/rule.md`", True),
        ("`agent-basics:search`の`references/other.md`と`references/rule.md`", True),
        ("`agent-basics:search`を使う。同スキルの`references/rule.md`", True),
        ("`agent-basics:search`を使う。`references/rule.md`に従う。", False),
        ("`agent-basics:search`を使い、`references/rule.md`を読む。", False),
    ],
)
def test_body_reference_owner_requires_connection(tmp_path, text, foreign):
    """所属は接続された名指しだけから決め、無関係な名指しの後も自スキルを保つ。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    source = tmp_path / "skills/current/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 現在\n", encoding="utf-8")
    targets = read_triggers.material_targets(text, source, tmp_path, references_only=True)
    target = next(item for item in targets if item.path.name == "rule.md")
    assert target.path == tmp_path / "skills" / ("search" if foreign else "current") / "references/rule.md"


def test_body_reference_same_skill_across_lines_and_no_unique_fallback(tmp_path):
    """前行の名指しを同スキルで参照し、一意の別所属ファイルへは補完しない。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    source = tmp_path / "share/review.subagent.md"
    only = tmp_path / "skills/search/references/rule.md"
    only.parent.mkdir(parents=True)
    only.write_text("# 基準\n", encoding="utf-8")
    target = read_triggers.material_targets(
        "同スキルの`references/rule.md`",
        source,
        tmp_path,
        references_only=True,
        context="`agent-basics:search`を起動する。\n",
    )[0]
    assert target.path == only
    unowned = read_triggers.material_targets("`references/rule.md`", source, tmp_path, references_only=True)[0]
    assert unowned.path == source.parent / "references/rule.md"


@pytest.mark.parametrize("foreign_plugin", [False, True])
def test_body_reference_rejects_moved_and_cross_plugin_unique_targets(tmp_path, foreign_plugin):
    """移動先や別プラグインの一意な資料があっても、所属なしの本文参照を拒否する。"""
    toolkit = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    for root in (toolkit, basics):
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(f'{{"name": "{root.name}"}}', encoding="utf-8")
    owner = basics if foreign_plugin else toolkit
    source = owner / "skills/current/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 現在\n判定は`references/rule.md`に従う。\n", encoding="utf-8")
    destination = toolkit / "skills/moved/references/rule.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("# 基準\n", encoding="utf-8")
    with pytest.raises(ValueError, match="参照先が存在しない") as caught:
        read_triggers.validate_reference_routes(source, toolkit, [])
    assert str(source) in str(caught.value)
    assert "`agent-toolkit:<スキル>`の`references/...`" in str(caught.value)


def test_startup_delivery_is_part_of_role_reception(tmp_path):
    source = tmp_path / "search.subagent.md"
    reference = tmp_path / "rule.md"
    reference.write_text("# 初動\n判断前の必須基準\n", encoding="utf-8")
    source.write_text(
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 受領時 | `rule.md` | 起動時 | `role:search` |\n",
        encoding="utf-8",
    )
    context = read_triggers.startup_context(source, tmp_path, {})
    assert "判断前の必須基準" in context
    assert str(reference) in context
    assert "相対参照の基点" in context


def test_duplicate_heading_does_not_fallback_to_whole_document(tmp_path):
    path = tmp_path / "duplicate.md"
    path.write_text("# 基準\n## 共通\nA\n## 共通\nB\n", encoding="utf-8")
    with pytest.raises(ValueError, match="一意"):
        read_triggers.ReadTarget(path, "共通").body()


def test_body_reference_scope_stops_before_other_concepts(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    skill = tmp_path / "skills/search"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    reference = skill / "references/rule.md"
    reference.write_text("# 規定\n## 共通\n基準\n", encoding="utf-8")
    targets = read_triggers.material_targets(
        "`agent-basics:search`の`references/rule.md`「共通」に従う。その後の「対象」は元の文書が定める。",
        tmp_path / "caller.md",
        tmp_path,
        references_only=True,
    )
    assert len(targets) == 1 and targets[0].heading == "「共通」"


def test_pure_compatibility_alias_preserves_actual_section(tmp_path):
    toolkit = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    (basics / ".claude-plugin").mkdir(parents=True)
    (basics / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    actual = basics / "skills/writing-standards/references/rule.md"
    actual.parent.mkdir(parents=True)
    actual.write_text("# 品質\n## 共通\n共通本文\n## 後段\n後段本文\n", encoding="utf-8")
    wrapper = toolkit / "skills/sample/references/rule.md"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        "# 互換入口\n\n<!-- read-source: agent-basics:writing-standards/references/rule.md -->\n\n条件と節指定を引き継ぐ。\n",
        encoding="utf-8",
    )
    resolved = read_triggers.resolve_document("references/rule.md", wrapper.parent.parent / "SKILL.md", toolkit)
    assert resolved is not None
    assert resolved == actual
    assert "後段本文" not in read_triggers.ReadTarget(resolved, "共通").body()


def test_mixed_compatibility_document_is_not_unconditionally_redirected(tmp_path):
    wrapper = tmp_path / "mixed.md"
    wrapper.write_text("# 混在入口\n\n正式資料はagent-basicsにある。\n\n## 運用条件\n保持する本文\n", encoding="utf-8")
    assert read_triggers.resolve_read_source(wrapper, tmp_path) == wrapper


def test_redirect_cycle_is_actionable_error(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    directory = tmp_path / "skills/search/references"
    directory.mkdir(parents=True)
    first, second = directory / "first.md", directory / "second.md"
    first.write_text("<!-- read-source: agent-basics:search/references/second.md -->\n", encoding="utf-8")
    second.write_text("<!-- read-source: agent-basics:search/references/first.md -->\n", encoding="utf-8")
    with pytest.raises(ValueError, match="循環"):
        read_triggers.resolve_read_source(first, tmp_path)


def test_single_lane_declarations_cover_phases_without_overlapping_full_reads():
    root = pathlib.Path(__file__).resolve().parents[2]
    sources = (root / "skills/process-wi/references/single-lane.md", root / "share/single-lane-execution.md")
    rows = [row for source in sources for row in read_triggers.parse_tables(source, root, strict=True)]
    targets = [target for row in rows for target in row.targets]
    assert len(targets) == len(set(targets))
    for target in targets:
        if not target.heading:
            assert not any(other.path == target.path and other.heading for other in targets)
    for suffix in (
        "skills/process-wi/references/selection-procedure.md",
        "skills/wi-standards/SKILL.md",
        "skills/write-plan/SKILL.md",
        "share/single-lane-execution.md",
        "share/exec-review.parent.md",
        "share/verification-evidence.md",
        "skills/commit/references/publish.md",
        "skills/report-completion/SKILL.md",
    ):
        assert any(str(target.path).endswith(suffix) for target in targets), suffix
    for suffix, condition in (
        ("selection-procedure.md", "対象WIの選定"),
        ("write-plan/SKILL.md", "計画の要否"),
        ("exec-review.parent.md", "実装に着手する前"),
        ("verification-evidence.md", "検証結果の記録前"),
        ("review-loop-coordination.md", "レビュー指摘への対応"),
        ("commit/references/publish.md", "公開する前"),
        ("report-completion/SKILL.md", "終了処理の前"),
    ):
        assert any(
            any(str(target.path).endswith(suffix) for target in row.targets)
            and condition in row.source.read_text(encoding="utf-8").splitlines()[row.line - 1]
            for row in rows
        ), suffix


def _plugin_checkout(base: pathlib.Path, *, with_new: bool, broken: bool = False) -> pathlib.Path:
    """agent-toolkitとagent-basicsを同居させたcheckoutを作り、そのrootを返す。"""
    for name in ("agent-toolkit", "agent-basics"):
        root = base / name
        (root / ".claude-plugin").mkdir(parents=True)
        manifest_name = "other" if broken and name == "agent-basics" else name
        (root / ".claude-plugin/plugin.json").write_text(f'{{"name":"{manifest_name}"}}', encoding="utf-8")
        (root / "skills/bugfix/references").mkdir(parents=True)
        (root / "share").mkdir()
    (base / "agent-toolkit/share/exec.parent.md").write_text("# 起動\n\n## 入力\n説明\n", encoding="utf-8")
    (base / "agent-toolkit/share/exec.subagent.md").write_text("# 役割\n", encoding="utf-8")
    if with_new:
        (base / "agent-basics/skills/bugfix/references/new-reference.md").write_text("# 新設\n", encoding="utf-8")
        (base / "agent-toolkit/share/new-review.subagent.md").write_text("# 新設役割\n", encoding="utf-8")
        (base / "agent-toolkit/share/new-review.parent.md").write_text("# 新設契約\n\n## 入力\n説明\n", encoding="utf-8")
    (base / ".claude-plugin").mkdir(exist_ok=True)
    (base / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {"name": "agent-basics", "source": "./agent-basics"},
                    {"name": "agent-toolkit", "source": "./agent-toolkit"},
                ]
            }
        ),
        encoding="utf-8",
    )
    skill = base / ".claude/skills/local/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# プロジェクトスキル\n", encoding="utf-8")
    return base


@pytest.mark.parametrize("case", ["worktree", "running-only", "same-root", "missing", "broken-manifest"])
def test_project_skill_reference_resolves_the_same_checkout_version(tmp_path, case):
    """プロジェクトスキルのplugin修飾参照は、所属checkoutの版を使い、稼働版の欠落へ戻さない。"""
    running = _plugin_checkout(tmp_path / "running", with_new=False)
    target = running if case in {"running-only", "same-root"} else tmp_path / "worktree"
    if target != running:
        _plugin_checkout(target, with_new=case != "missing", broken=case == "broken-manifest")
    elif case == "same-root":
        (running / "agent-basics/skills/bugfix/references/new-reference.md").write_text("# 新設\n", encoding="utf-8")
    source = target / ".claude/skills/local/SKILL.md"
    with read_triggers.resolution_scope():
        resolved = read_triggers.resolve_document(
            "agent-basics:bugfix/references/new-reference.md", source, running / "agent-toolkit"
        )
    expected_root = running if case in {"running-only", "same-root", "broken-manifest"} else target
    assert resolved is not None
    assert resolved == (expected_root / "agent-basics/skills/bugfix/references/new-reference.md").resolve()
    # 実際の欠落は存在しないパスとして返し、別版の同名資料へ置き換えない。
    assert resolved.is_file() is (case in {"worktree", "same-root"})


def test_share_variable_and_absolute_role_keep_their_own_version(tmp_path):
    """share文書の`${CLAUDE_PLUGIN_ROOT}`と絶対パスの役割指定は、文書が属するrootで解決する。"""
    running = _plugin_checkout(tmp_path / "running", with_new=False)
    worktree = _plugin_checkout(tmp_path / "worktree", with_new=True)
    share = worktree / "agent-toolkit/share/exec.subagent.md"
    resolved = read_triggers.resolve_document(
        "${CLAUDE_PLUGIN_ROOT}/share/new-review.parent.md", share, running / "agent-toolkit"
    )
    assert resolved == (worktree / "agent-toolkit/share/new-review.parent.md").resolve()
    role = worktree / "agent-toolkit/share/new-review.subagent.md"
    targets = read_triggers.parent_contract_targets(str(role), running / "agent-toolkit")
    assert [target.path for target in targets] == [worktree / "agent-toolkit/share/new-review.parent.md"]
    # 役割名だけの指定は従来どおり渡されたrootで解決し、稼働版に無い役割は欠落として報告する。
    with pytest.raises(ValueError, match="役割文書が存在しない"):
        read_triggers.parent_contract_targets("new-review", running / "agent-toolkit")


@pytest.mark.parametrize(
    "operation,expected,excluded",
    [
        ("body-writing", "writing.md", {"program-messages.md", "input-material.md"}),
        ("skill:writing-standards", "program-messages.md", {"input-material.md"}),
        (
            "read:agent-basics:*/skills/writing-standards/references/input-material.md",
            "input-material.md",
            {"program-messages.md"},
        ),
    ],
    ids=["ordinary-prose", "code-edit", "input-material"],
)
def test_writing_entry_routes_select_purpose_specific_material(operation, expected, excluded):
    """現物の読込表を解析し、文章・コード・素材の各操作が必要な専用資料を選ぶ。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    source = root.parent / "agent-basics/skills/writing-standards/SKILL.md"
    rows = read_triggers.parse_tables(source, root, strict=True)
    selected = [row for row in rows if operation in row.effective_operations]
    targets = [target for row in selected for target in row.targets]
    assert any(target.path.name == expected and target.body().strip() for target in targets)
    assert not excluded.intersection(target.path.name for target in targets)
    if operation == "body-writing":
        prose = "\n".join(target.body() for target in targets if target.path.name == "writing.md")
        assert "## ユーザー入力素材の取扱い" not in prose
        assert "プログラムが出力するメッセージ" not in prose


@pytest.mark.parametrize(
    "condition_terms",
    [("通知本文", "新設"), ("次の操作", "変更")],
    ids=["typescript-notice-creation", "python-next-action-change"],
)
def test_hook_message_judgment_route_reaches_message_and_program_material(condition_terms):
    """通知の新設・次操作の変更で選ぶ条件から、通知基準と出力基準の実本文へ辿れる。"""
    root = pathlib.Path(__file__).resolve().parents[2]
    source = root.parent / "agent-basics/skills/writing-standards/SKILL.md"
    rows = read_triggers.parse_tables(source, root, strict=True)
    selected = [row for row in rows if all(term in row.condition for term in condition_terms)]
    hook_material = source.parent / "references/claude-hooks-messages.md"
    routes = [row for row in selected if hook_material in row.documents]
    assert routes and all(row.kind == "判断" for row in routes)
    targets = [target for row in routes for target in row.targets if target.path == hook_material]
    assert all(target.body().strip() for target in targets)
    # 出力基準は整形の条件別資料が本文で名指しし、解析器が補う宣言で届く。
    format_material = source.parent / "references/claude-hooks-messages-format.md"
    assert any(format_material in row.documents for row in read_triggers.parse_tables(hook_material, root, strict=True))
    followups = read_triggers.parse_tables(format_material, root, strict=True, named=True)
    program_material = source.parent / "references/program-messages.md"
    assert any(program_material in row.documents for row in followups)
    assert any(target.path == program_material and target.body().strip() for row in followups for target in row.targets)


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
