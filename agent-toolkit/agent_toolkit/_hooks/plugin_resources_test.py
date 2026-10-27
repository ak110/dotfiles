"""agent-toolkit/agent_toolkit/_hooks/plugin_resources.py のテスト。"""

import pathlib

import pytest

from agent_toolkit._common import delegated_session
from agent_toolkit._common.plugin_roots import PluginRootError
from agent_toolkit._hooks import plugin_resources


def test_skill_reference_includes_an_existing_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """実在する資料では、スキル名に加えて実在する絶対パスを併記する。"""
    monkeypatch.delenv(delegated_session.PLUGIN_ROOT_ENV, raising=False)
    reference = plugin_resources.skill_reference("user-confirmation-and-report", "SKILL.md")

    assert "`agent-toolkit:user-confirmation-and-report`の`SKILL.md`" in reference
    quoted = reference.split("（`", 1)[1].removesuffix("`）")
    assert pathlib.Path(quoted).is_absolute()
    assert pathlib.Path(quoted).is_file()


def test_skill_reference_omits_the_path_when_the_resource_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """実体が無い場合は従来どおりスキル名と相対パスだけを返す。"""
    monkeypatch.delenv(delegated_session.PLUGIN_ROOT_ENV, raising=False)
    reference = plugin_resources.skill_reference("user-confirmation-and-report", "references/absent.md")

    assert reference == "`agent-toolkit:user-confirmation-and-report`の`references/absent.md`"


@pytest.mark.parametrize("value", ["", "relative", "/missing-plugin-root", "wrong-plugin"])
def test_explicit_invalid_root_is_diagnosed_without_fallback(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """明示指定の不正を別版の資料案内で隠さない。"""
    if value == "wrong-plugin":
        root = tmp_path / value
        (root / "skills").mkdir(parents=True)
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin/plugin.json").write_text('{"name":"other"}', encoding="utf-8")
        value = str(root)
    monkeypatch.setenv(delegated_session.PLUGIN_ROOT_ENV, value)
    with pytest.raises(PluginRootError, match="資料rootが不正"):
        plugin_resources.skill_reference("search", "SKILL.md")
