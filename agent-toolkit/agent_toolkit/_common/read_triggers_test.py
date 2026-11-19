"""自然文の節指定と、起動時の判断前配送を確かめる。"""

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


@pytest.mark.parametrize(
    "operation,expected,excluded",
    [
        ("body-writing", "writing.md", {"program-messages.md", "writing-genres.md"}),
        ("skill:writing-standards", "program-messages.md", {"writing-genres.md"}),
        (
            "read:agent-basics:*/skills/writing-standards/references/writing-genres.md",
            "writing-genres.md",
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
    hook_material = source.parent / "references/claude-hooks-block-warn.md"
    program_material = source.parent / "references/program-messages.md"
    routes = [row for row in selected if hook_material in row.documents]
    assert routes and all(row.kind == "判断" for row in routes)
    # 本文から別の本文を読ませる連鎖を作らず、入口の同じ行が通知基準と出力基準の両方を選ぶ。
    assert all(program_material in row.documents for row in routes)
    targets = [target for row in routes for target in row.targets if target.path in {hook_material, program_material}]
    assert {target.path for target in targets} == {hook_material, program_material}
    assert all(target.body().strip() for target in targets)
