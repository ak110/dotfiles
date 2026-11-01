"""配布物とdotfilesの全読込表・他資料参照をCIで検査する。"""

import pathlib
import re
import shutil

import pytest

from agent_toolkit._common import plugin_roots, read_triggers
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent


@pytest.mark.parametrize("role", sorted(path.name for path in (_PLUGIN_ROOT / "share").glob("*.subagent.md")))
def test_role_writing_skill_matches_operation_bootstrap(role):
    """起動する作成規範の名前空間がbootstrapと一致することを確認する。"""
    bootstrap = (_PLUGIN_ROOT / "share/read-trigger-bootstrap.md").read_text(encoding="utf-8")
    line = next(line for line in bootstrap.splitlines() if "`body-writing`" in line)
    skill = re.search(r"`([^`]+/SKILL\.md)`", line)
    assert skill is not None
    if ":" in skill[1]:
        expected = skill[1].removesuffix("/SKILL.md")
    else:
        path = (_PLUGIN_ROOT / "share" / skill[1]).resolve()
        expected = f"{path.relative_to(_PLUGIN_ROOT.parent).parts[0]}:writing-standards"
    body = (_PLUGIN_ROOT / "share" / role).read_text(encoding="utf-8")
    started = re.findall(r"`(agent-[^`]+:writing-standards)`(?=[^\n]*を起動)", body)
    assert not started or set(started) == {expected}, (role, started, expected)


def test_all_read_tables_have_valid_declarations_and_routes():
    """未分類、資料の不在、未登録操作、経路の無い参照を拒否する。"""
    roots = [_PLUGIN_ROOT / "skills", _PLUGIN_ROOT / "share", _PLUGIN_ROOT.parent / ".claude/skills"]
    basics = _PLUGIN_ROOT.parent / "agent-basics/skills"
    if basics.is_dir():
        roots.append(basics)
    sources = sorted({path for root in roots for path in root.rglob("*.md")})
    assert sources
    errors: list[str] = []
    for source in sources:
        try:
            rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True)
            read_triggers.validate_reference_routes(source, _PLUGIN_ROOT, rows)
            read_triggers.validate_plugin_relative_references(source)
        except ValueError as error:
            errors.append(f"{source}: {error}")
    assert not errors, "\n".join(errors)


def test_every_role_has_explicit_parent_read_source():
    """全役割の共有契約か統合節が実在し、明示した範囲を取得できる。"""
    sources = sorted((_PLUGIN_ROOT / "share").glob("*.subagent.md"))
    assert sources
    for source in sources:
        targets = read_triggers.parent_contract_targets(source.name, _PLUGIN_ROOT)
        assert targets
        assert all(target.body().strip() for target in targets)


# 配布版で届かない参照が報告された文書（20261028-161251-001）。
_CROSS_PLUGIN_DOCUMENTS = (
    "skills/user-confirmation-and-report/references/analysis-operations.md",
    "skills/wi-standards/references/analysis-records.md",
    "skills/review-session/references/analysis-operations.md",
    "skills/user-confirmation-and-report/references/communication-methods.md",
    "skills/user-confirmation-and-report/references/user-utterance.md",
    "skills/wi-standards/references/draft-semantic-review.md",
)


@pytest.mark.parametrize(
    "skill,documents",
    [
        ("process-wi", {"lanes.md", "single-lane.md", "all-repos.md"}),
        ("review-session", {"current-session.md", "batch.md"}),
    ],
)
def test_mode_entries_keep_exclusive_judgment_routes(skill, documents):
    """入力に合う資料だけを選ぶ条件付きの取得契約を保持する。"""
    source = _PLUGIN_ROOT / "skills" / skill / "SKILL.md"
    rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True)
    assert {path.name for row in rows for path in row.documents} == documents
    assert all(row.kind == "判断" and len(row.documents) == 1 for row in rows)
    normal = "current-session.md" if skill == "review-session" else "lanes.md"
    text = (source.parent / "references" / normal).read_text(encoding="utf-8")
    if skill == "review-session":
        assert "references/batch.md" not in text
        assert "session-review-state -- finish" not in text
        assert "session-review-state -- complete" in text


def test_cross_plugin_references_resolve_in_codex_cache_layout(tmp_path, monkeypatch):
    """Codexの配布キャッシュ（版ごとの別ディレクトリ）でも、agent-basicsの資料へ不在の診断なく届く。"""
    cache = tmp_path / "codex/plugins/cache/mine"
    toolkit = cache / "agent-toolkit/9.9.9"
    basics = cache / "agent-basics/1.0.0"
    for name in ("skills", "share", ".claude-plugin"):
        shutil.copytree(_PLUGIN_ROOT / name, toolkit / name)
    for name in ("skills", ".claude-plugin"):
        shutil.copytree(_PLUGIN_ROOT.parent / "agent-basics" / name, basics / name)
    installed = [
        {"name": "agent-toolkit", "marketplaceName": "mine", "version": "9.9.9", "installed": True, "enabled": True},
        {"name": "agent-basics", "marketplaceName": "mine", "version": "1.0.0", "installed": True, "enabled": True},
    ]

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", codex_plugin_cli(installed))
    resolved_basics: list[pathlib.Path] = []
    for relative in _CROSS_PLUGIN_DOCUMENTS:
        rows = read_triggers.parse_tables(toolkit / relative, toolkit, host="codex", project=tmp_path)
        documents = [path for row in rows for path in row.documents]
        assert all(path.is_file() for path in documents), relative
        assert not [path for path in documents if not (path.is_relative_to(toolkit) or path.is_relative_to(basics))]
        resolved_basics.extend(path for path in documents if path.is_relative_to(basics.resolve()))
    assert resolved_basics


@pytest.mark.parametrize(
    "line",
    [
        "| 原因を分析する前 | `../../../agent-basics/skills/bugfix/SKILL.md` | 判断 |"
        " `read:agent-basics:*/skills/bugfix/SKILL.md` |",
        "詳細は[原因分析](../../../agent-basics/skills/bugfix/SKILL.md)に従う。",
    ],
)
def test_relative_reference_outside_owner_plugin_is_rejected(tmp_path, line):
    """所有プラグインのrootの外を指す相対パスを、読込表の行でも本文のリンクでも拒否し、論理名表記を案内する。"""
    toolkit = tmp_path / "agent-toolkit"
    (toolkit / ".claude-plugin").mkdir(parents=True)
    (toolkit / ".claude-plugin/plugin.json").write_text('{"name": "agent-toolkit"}', encoding="utf-8")
    skill = toolkit / "skills/sample"
    skill.mkdir(parents=True)
    (tmp_path / "agent-basics/skills/bugfix").mkdir(parents=True)
    (tmp_path / "agent-basics/skills/bugfix/SKILL.md").write_text("# 原因分析\n", encoding="utf-8")
    source = skill / "SKILL.md"
    header = "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
    source.write_text("# 例\n\n" + (header if line.startswith("|") else "") + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="所有プラグインのrootの外") as caught:
        read_triggers.validate_plugin_relative_references(source)
    assert "agent-basics:<スキル>/<パス>" in str(caught.value)
