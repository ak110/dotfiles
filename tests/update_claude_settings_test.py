"""update_claude_settingsモジュールのテスト。"""

import json
from pathlib import Path

import pytest

from pytools import update_claude_settings as mod
from pytools.update_claude_settings import update_claude_settings

MANAGED_ALLOW = [
    "Bash",
    "Edit",
    "Glob",
    "Grep",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
    "mcp__serena",
    "mcp__plugin_serena_serena",
]
MANAGED_DENY = ["Read(*.key)", "Read(*.crt)", "Read(./.env)"]
MANAGED = {
    "language": "japanese",
    "permissions": {
        "allow": MANAGED_ALLOW,
        "deny": MANAGED_DENY,
        "defaultMode": "plan",
    },
}


def _run(tmp_path: Path, managed: dict, existing: dict | None = None) -> dict:
    managed_path = tmp_path / "managed.json"
    managed_path.write_text(json.dumps(managed), encoding="utf-8")
    target_path = tmp_path / "target.json"
    if existing is not None:
        target_path.write_text(json.dumps(existing), encoding="utf-8")
    update_claude_settings(managed_path, target_path)
    return json.loads(target_path.read_text(encoding="utf-8"))


class TestUpdateClaudeSettings:
    """~/.claude/settings.json 向けマージテスト。"""

    def test_new_file(self, tmp_path: Path):
        """settings.json が存在しない場合、managed 設定がそのまま出力される。"""
        result = _run(tmp_path, MANAGED)
        assert result["language"] == "japanese"
        assert result["permissions"]["defaultMode"] == "plan"
        assert result["permissions"]["allow"] == MANAGED_ALLOW
        assert result["permissions"]["deny"] == MANAGED_DENY

    def test_merge_preserves_existing_keys(self, tmp_path: Path):
        """既存キーが保持され、permissions が正しく union マージされる。"""
        existing = {
            "enabledPlugins": {"foo@bar": True},
            "remote": {"enabled": True},
            "permissions": {
                "allow": ["CustomTool", "Bash"],
                "deny": ["Read(./.secret)"],
            },
        }
        result = _run(tmp_path, MANAGED, existing)

        # 既存キーが保持されている
        assert result["enabledPlugins"] == {"foo@bar": True}
        assert result["remote"] == {"enabled": True}

        # allow: 既存順維持 + managed の新規追加、重複排除
        allow: list[str] = result["permissions"]["allow"]
        assert allow[0] == "CustomTool"
        assert allow[1] == "Bash"
        assert allow.count("Bash") == 1
        for item in MANAGED_ALLOW:
            assert item in allow

        # deny: union マージ
        deny: list[str] = result["permissions"]["deny"]
        assert "Read(./.secret)" in deny
        for item in MANAGED_DENY:
            assert item in deny

        # defaultMode が追加される
        assert result["permissions"]["defaultMode"] == "plan"


class TestUpdateClaudeConfig:
    """~/.claude.json 向けの単純上書きマージテスト。"""

    def test_new_file(self, tmp_path: Path):
        """claude.json が存在しない場合、managed 設定がそのまま出力される。"""
        result = _run(tmp_path, {"verbose": True})
        assert result == {"verbose": True}

    def test_merge_preserves_existing_keys(self, tmp_path: Path):
        """既存キーが保持され、managed キーで上書きされる。"""
        existing = {"numStartups": 100, "verbose": False, "theme": "light"}
        result = _run(tmp_path, {"verbose": True}, existing)
        assert result["numStartups"] == 100
        assert result["theme"] == "light"
        assert result["verbose"] is True


class TestPlatformOverride:
    """OS 別オーバーライド JSON が重ねマージされることを検証する。"""

    def test_override_adds_hooks(self, tmp_path: Path):
        """ベースに無い hooks セクションをオーバーライド経由で追加できる。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "x"}]}]}}),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["language"] == "japanese"
        assert result["hooks"]["PreToolUse"][0]["matcher"] == "Write"

    def test_override_replaces_scalar(self, tmp_path: Path):
        """オーバーライドはベースのスカラー値を上書きする。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "english"}), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps({"language": "japanese"}), encoding="utf-8")
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["language"] == "japanese"

    def test_missing_override_is_ignored(self, tmp_path: Path):
        """存在しないオーバーライドはスキップされる (他 OS を壊さない)。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}), encoding="utf-8")
        target_path = tmp_path / "target.json"

        # _platform_overrides は実在チェック済みのリストを返すため、そちらを経由
        # tmp_path 内の managed.json には対応 override が無いため空リストになる
        overrides = mod._platform_overrides(managed_path)  # pylint: disable=protected-access
        assert not overrides

        update_claude_settings(managed_path, target_path, overrides=overrides)
        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}


class TestPlatformOverrideSelection:
    """_platform_overrides のプラットフォーム判定テスト。"""

    def test_posix_selects_posix_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(mod.sys, "platform", "linux")
        base = tmp_path / "managed.json"
        base.write_text("{}", encoding="utf-8")
        (tmp_path / "managed.posix.json").write_text("{}", encoding="utf-8")
        (tmp_path / "managed.win32.json").write_text("{}", encoding="utf-8")

        result = mod._platform_overrides(base)  # pylint: disable=protected-access

        assert result == [tmp_path / "managed.posix.json"]

    def test_darwin_selects_posix_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(mod.sys, "platform", "darwin")
        base = tmp_path / "managed.json"
        base.write_text("{}", encoding="utf-8")
        (tmp_path / "managed.posix.json").write_text("{}", encoding="utf-8")

        result = mod._platform_overrides(base)  # pylint: disable=protected-access

        assert result == [tmp_path / "managed.posix.json"]

    def test_win32_selects_win32_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(mod.sys, "platform", "win32")
        base = tmp_path / "managed.json"
        base.write_text("{}", encoding="utf-8")
        (tmp_path / "managed.posix.json").write_text("{}", encoding="utf-8")
        (tmp_path / "managed.win32.json").write_text("{}", encoding="utf-8")

        result = mod._platform_overrides(base)  # pylint: disable=protected-access

        assert result == [tmp_path / "managed.win32.json"]

    def test_missing_override_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """override が存在しない場合は空リスト。"""
        monkeypatch.setattr(mod.sys, "platform", "linux")
        base = tmp_path / "managed.json"
        base.write_text("{}", encoding="utf-8")

        result = mod._platform_overrides(base)  # pylint: disable=protected-access

        assert not result


class TestMergeRecursive:
    """_merge の再帰マージロジックテスト。"""

    def test_nested_dict_merge(self, tmp_path: Path):
        """ネストした dict が再帰的にマージされる。"""
        existing = {"outer": {"keep": 1, "override": "old"}}
        result = _run(tmp_path, {"outer": {"override": "new", "add": 2}}, existing)
        assert result["outer"] == {"keep": 1, "override": "new", "add": 2}

    def test_list_union_dedup(self, tmp_path: Path):
        """list が union マージされ、重複排除・順序維持される。"""
        existing = {"items": ["a", "b", "c"]}
        result = _run(tmp_path, {"items": ["b", "d"]}, existing)
        assert result["items"] == ["a", "b", "c", "d"]

    def test_dict_list_union_dedup(self, tmp_path: Path):
        """dict を要素に持つ list もマージ可能で、同一内容の重複は排除される。

        hooks 配列のように非 hashable な要素を含む list をマージする際の回帰テスト。
        """
        hook_entry = {
            "matcher": "Write|Edit|MultiEdit",
            "hooks": [{"type": "command", "command": "claude-hook-check-mojibake"}],
        }
        existing = {"hooks": {"PreToolUse": [hook_entry]}}
        managed = {
            "hooks": {
                "PreToolUse": [
                    dict(hook_entry),  # 同一内容 → 重複排除される
                    {"matcher": "Bash", "hooks": []},
                ]
            }
        }
        result = _run(tmp_path, managed, existing)
        pretooluse = result["hooks"]["PreToolUse"]
        assert len(pretooluse) == 2
        assert pretooluse[0] == hook_entry
        assert pretooluse[1]["matcher"] == "Bash"


class TestStripRemovedHooks:
    """配布元から削除された hook エントリの自動除去テスト。"""

    def test_removed_hook_is_dropped_from_existing(self, tmp_path: Path):
        """既存の hooks に旧 command 部分文字列が残っている場合、マージ前に除去される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "new-cmd"}]}]}}),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Write|Edit|MultiEdit",
                                "hooks": [
                                    {"type": "command", "command": "sh -c 'uv run --script ~/legacy/old_hook.py'"},
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_hook_substrings=("old_hook.py",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        # 旧エントリは PostToolUse ごと消える (空になったため)
        assert "PostToolUse" not in result["hooks"]
        # 新エントリはマージされて残る
        assert result["hooks"]["PreToolUse"][0]["matcher"] == "Write"

    def test_removed_hook_keeps_sibling_in_same_matcher(self, tmp_path: Path):
        """同じ matcher 内の他の hook は残す。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [
                                    {"type": "command", "command": "old_hook.py"},
                                    {"type": "command", "command": "keep_me.py"},
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_hook_substrings=("old_hook.py",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        inner = result["hooks"]["PreToolUse"][0]["hooks"]
        assert len(inner) == 1
        assert inner[0]["command"] == "keep_me.py"
