"""資料の節指定と本文の名指しの解決、互換入口の転送、checkoutごとの版の解決を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import read_triggers


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
    skill = tmp_path / "skills" / invocation.split()[0]
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    targets = read_triggers.material_targets(f"`agent-toolkit:{invocation}`", tmp_path / "caller.md", tmp_path)
    assert len(targets) == 1
    assert targets[0].path == tmp_path / "skills" / relative


@pytest.mark.parametrize("invocation", ["review-session", "review-session batch", "export-session"])
def test_entry_work_without_own_skill_names_no_material(tmp_path, invocation):
    """入口の配下へ移した作業の保存上の名前は、存在しない旧スキルの資料へ解決しない。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    assert not read_triggers.material_targets(f"`agent-toolkit:{invocation}`", tmp_path / "caller.md", tmp_path)


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
