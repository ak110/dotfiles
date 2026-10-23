"""OpenCodeへの後処理導入を、設定保持・更新・撤去まで通して検証する。"""

from pathlib import Path

import pytest
import pytilpack.jsonc
import yaml

from pytools import post_apply
from pytools._internal import common, install_opencode_toolkit


def _source(root: Path) -> None:
    for plugin in ("agent-toolkit", "agent-basics"):
        skill = root / plugin / "skills" / "writing-standards"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: writing-standards\ndescription: 品質基準\n---\n本文と${CLAUDE_PLUGIN_ROOT}\n", encoding="utf-8"
        )
        (skill / "references").mkdir()
        (skill / "references" / "detail.md").write_text("詳細の原文\n", encoding="utf-8")
    manual = root / "agent-toolkit" / "skills" / "manual-task"
    manual.mkdir()
    (manual / "SKILL.md").write_text(
        "---\nname: manual-task\ndescription: 手動起動\ndisable-model-invocation: true\n---\n明示起動の本文\n",
        encoding="utf-8",
    )
    rules = root / "agent-toolkit" / "rules"
    rules.mkdir()
    (rules / "01-agent.md").write_text("# 共通常時規範\n\n原文を保持する。\n", encoding="utf-8")


@pytest.mark.parametrize("config_name", ["opencode.json", "opencode.jsonc"])
@pytest.mark.parametrize("custom_config_dir", [False, True])
def test_post_apply_sync_preserves_settings_and_updates_owned_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_name: str,
    *,
    custom_config_dir: bool,
) -> None:
    """後処理から導入し、独自設定・名前空間・自動選択設定と原文を保って再同期する。

    frontmatterの期待値はOpenCode 1.19.3の公式skills仕様のname/metadataから導く。
    """
    root = tmp_path / "dotfiles"
    _source(root)
    config_root = tmp_path / "config"
    destination = config_root / "opencode"
    destination.mkdir(parents=True)
    config = destination / config_name
    config.write_text(
        '{\n // ユーザーの接続設定\n "provider": {"local": {"name": "手元のモデル"}},'
        '\n "permission": {"bash": "ask"}, "mcp": {"custom": {"enabled": false}},'
        '\n "instructions": ["/user/rules.md"],\n}\n',
        encoding="utf-8",
    )
    before = pytilpack.jsonc.loads(config.read_text(encoding="utf-8"))
    agents = destination / "AGENTS.md"
    agents.write_text("# ユーザーの規範\n", encoding="utf-8")
    custom = destination / "skills" / "custom" / "SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("ユーザーのスキル", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    if custom_config_dir:
        monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(destination))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "unused-config"))
    else:
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_executable", lambda _name: tmp_path / "opencode")

    results, _ = post_apply.run([("OpenCode toolkit", install_opencode_toolkit.run)])
    assert results[0].ok and results[0].changed
    updated = pytilpack.jsonc.loads(config.read_text(encoding="utf-8"))
    assert updated == {**before, "instructions": ["/user/rules.md", (destination / "agent-toolkit.md").as_posix()]}
    assert agents.read_text(encoding="utf-8") == "# ユーザーの規範\n"
    assert custom.read_text(encoding="utf-8") == "ユーザーのスキル"
    for plugin in ("agent-toolkit", "agent-basics"):
        target = destination / "skills" / f"{plugin}-writing-standards"
        frontmatter, body = (target / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1:]
        assert yaml.safe_load(frontmatter)["name"] == target.name
        assert body == "\n本文と${CLAUDE_PLUGIN_ROOT}\n"
        assert (target / "references" / "detail.md").read_text(encoding="utf-8") == "詳細の原文\n"
    manual = destination / "skills" / "agent-toolkit-manual-task" / "SKILL.md"
    metadata = yaml.safe_load(manual.read_text(encoding="utf-8").split("---", 2)[1])["metadata"]
    assert metadata == {"opencode/autoinvoke": "false", "opencode/slash": "true"}
    rules = (destination / "agent-toolkit.md").read_text(encoding="utf-8")
    assert "# 共通常時規範\n\n原文を保持する。" in rules
    assert (root / "agent-toolkit").as_posix() in rules and (root / "agent-basics").as_posix() in rules
    assert not install_opencode_toolkit.run().changed

    source = root / "agent-toolkit" / "skills" / "manual-task" / "SKILL.md"
    source.unlink()
    detail = root / "agent-basics" / "skills" / "writing-standards" / "references" / "detail.md"
    detail.write_text("更新後の原文\n", encoding="utf-8")
    assert install_opencode_toolkit.run().changed
    assert not manual.parent.exists()
    assert (destination / "skills" / "agent-basics-writing-standards" / "references" / "detail.md").read_text(
        encoding="utf-8"
    ) == "更新後の原文\n"
    assert custom.is_file()


def test_missing_cli_and_owned_path_conflict_do_not_overwrite_user_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI不在はスキップとし、ユーザースキルとの衝突は書込前に失敗する。"""
    root = tmp_path / "dotfiles"
    _source(root)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(common, "resolve_executable", lambda _name: None)
    assert not install_opencode_toolkit.run().changed
    destination = tmp_path / "config" / "opencode"
    assert not destination.exists()
    conflict = destination / "skills" / "agent-toolkit-manual-task" / "SKILL.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("ユーザーのスキル", encoding="utf-8")
    monkeypatch.setattr(common, "resolve_executable", lambda _name: tmp_path / "opencode")
    results, _ = post_apply.run([("OpenCode toolkit", install_opencode_toolkit.run)])
    assert not results[0].ok and not results[0].changed
    assert conflict.read_text(encoding="utf-8") == "ユーザーのスキル"
    assert not (destination / "agent-toolkit.md").exists()
