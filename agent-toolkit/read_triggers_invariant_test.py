"""配布物とdotfilesの全読込表・他資料参照をCIで検査する。"""

import pathlib

from agent_toolkit._common import read_triggers

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent


def test_all_read_tables_have_valid_declarations_and_routes():
    """未分類、資料の不在、未登録操作、経路の無い参照を拒否する。"""
    roots = [_PLUGIN_ROOT / "skills", _PLUGIN_ROOT / "share", _PLUGIN_ROOT.parent / ".claude/skills"]
    basics = _PLUGIN_ROOT.parent / "agent-basics/skills"
    if basics.is_dir():
        roots.append(basics)
    sources = sorted({path for root in roots for path in root.rglob("*.md")})
    assert sources
    for source in sources:
        rows = read_triggers.parse_tables(source, _PLUGIN_ROOT, strict=True)
        read_triggers.validate_reference_routes(source, _PLUGIN_ROOT, rows)


def test_every_role_has_explicit_parent_read_source():
    """全役割の共有契約か統合節が実在し、明示した範囲を取得できる。"""
    sources = sorted((_PLUGIN_ROOT / "share").glob("*.subagent.md"))
    assert sources
    for source in sources:
        targets = read_triggers.parent_contract_targets(source.name, _PLUGIN_ROOT)
        assert targets
        assert all(target.body().strip() for target in targets)
