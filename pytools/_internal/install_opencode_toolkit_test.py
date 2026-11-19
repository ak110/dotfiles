"""OpenCodeへの後処理導入を、設定保持・更新・撤去まで通して検証する。"""

from pathlib import Path

import pytest
import pytilpack.jsonc
import yaml

from pytools import post_apply
from pytools._internal import common, install_opencode_toolkit, setup_opencode_cli


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
    entry = root / "agent-toolkit" / "skills" / "atk-mode"
    (entry / "roles").mkdir(parents=True)
    (entry / "SKILL.md").write_text("---\nname: atk-mode\ndescription: 入口\n---\n入口の本文\n", encoding="utf-8")
    (entry / "roles" / "worker.subagent.md").write_text("役割の原文\n", encoding="utf-8")
    # 廃止した常時規範が配布元に残っていても、案内のファイルへ本文を連結しない。
    rules = root / "agent-toolkit" / "share" / "rules" / "common"
    rules.mkdir(parents=True)
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
    monkeypatch.setattr(setup_opencode_cli, "find_launcher", lambda: tmp_path / "opencode")

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
    assert (root / "agent-toolkit").as_posix() in rules and (root / "agent-basics").as_posix() in rules
    # hookを持たないホストへは入口の起動を案内するだけとし、入口と常時規範の本文は載せない。
    assert "作業の前と会話の圧縮の後に、`agent-toolkit-atk-mode`のスキルを読む。\n" in rules
    assert "入口の本文" not in rules and "共通常時規範" not in rules
    # 役割文書は入口のスキルの配下にあるため、スキルだけを配置するホストへも届く。
    entry = destination / "skills" / "agent-toolkit-atk-mode"
    assert (entry / "roles" / "worker.subagent.md").read_text(encoding="utf-8") == "役割の原文\n"
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
    monkeypatch.setattr(setup_opencode_cli, "find_launcher", lambda: None)
    assert not install_opencode_toolkit.run().changed
    destination = tmp_path / "config" / "opencode"
    assert not destination.exists()
    conflict = destination / "skills" / "agent-toolkit-manual-task" / "SKILL.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("ユーザーのスキル", encoding="utf-8")
    monkeypatch.setattr(setup_opencode_cli, "find_launcher", lambda: tmp_path / "opencode")
    results, _ = post_apply.run([("OpenCode toolkit", install_opencode_toolkit.run)])
    assert not results[0].ok and not results[0].changed
    assert conflict.read_text(encoding="utf-8") == "ユーザーのスキル"
    assert not (destination / "agent-toolkit.md").exists()


def test_resync_replaces_legacy_rules_body_and_requires_entry_skill(tmp_path: Path) -> None:
    """旧版が常時規範を連結した案内のファイルを案内だけへ置き換え、入口のスキルが無い配布元は書込前に失敗する。"""
    root = tmp_path / "dotfiles"
    _source(root)
    destination = tmp_path / "config" / "opencode"
    destination.mkdir(parents=True)
    legacy = destination / "agent-toolkit.md"
    legacy.write_text(
        '<atk-auto source="dotfiles" kind="opencode-host">\n# OpenCodeでのagent-toolkit\n\n'
        '<atk-auto source="agent-toolkit" kind="rules" path="agent-toolkit/share/rules/common/01-agent.md">\n'
        "# 01-agent.md: エージェントの振る舞い\n</atk-auto>\n</atk-auto><!-- opencode-host -->\n",
        encoding="utf-8",
    )
    config = destination / "opencode.json"
    config.write_text(f'{{"instructions": ["{legacy.as_posix()}"]}}\n', encoding="utf-8")

    assert install_opencode_toolkit.sync(root, destination)

    body = legacy.read_text(encoding="utf-8")
    assert 'kind="rules"' not in body and "01-agent.md" not in body
    assert "`agent-toolkit-atk-mode`のスキルを読む" in body
    # 登録は1件のまま保つ。
    assert pytilpack.jsonc.loads(config.read_text(encoding="utf-8"))["instructions"] == [legacy.as_posix()]

    (root / "agent-toolkit" / "skills" / "atk-mode" / "SKILL.md").unlink()
    legacy.write_text('<atk-auto source="dotfiles" kind="opencode-host">\n旧版\n', encoding="utf-8")
    with pytest.raises(ValueError, match="atk-mode"):
        install_opencode_toolkit.sync(root, destination)
    assert legacy.read_text(encoding="utf-8") == '<atk-auto source="dotfiles" kind="opencode-host">\n旧版\n'
