"""update_claude_settings モジュールのテスト。"""

import json
import logging
from pathlib import Path

import pytest

from pytools._internal import update_claude_settings as mod
from pytools._internal.update_claude_settings import update_claude_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_MANAGED_SETTINGS = _REPO_ROOT / "share" / "claude_settings_json_managed.json"

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
    managed_path.write_text(json.dumps(managed, ensure_ascii=False), encoding="utf-8")
    target_path = tmp_path / "target.json"
    if existing is not None:
        target_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
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

    def test_env_merge_preserves_existing(self, tmp_path: Path):
        """env は dict として再帰マージされ、既存キーを壊さない。"""
        managed = {"env": {"CLAUDE_CODE_NO_FLICKER": "1"}}
        existing = {"env": {"FOO": "bar"}}
        result = _run(tmp_path, managed, existing)
        assert result["env"] == {"FOO": "bar", "CLAUDE_CODE_NO_FLICKER": "1"}


class TestProductionManagedSettings:
    """配布元の share/claude_settings_json_managed.json の内容を検証する。"""

    def test_env_has_no_flicker(self):
        """Claude Code のちらつき抑制フラグが env に設定されている。"""
        data = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        assert data["env"]["CLAUDE_CODE_NO_FLICKER"] == "1"


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
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(
            json.dumps(
                {"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "x"}]}]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["language"] == "japanese"
        assert result["hooks"]["PreToolUse"][0]["matcher"] == "Write"

    def test_override_adds_required_minimum_version(self, tmp_path: Path):
        """オーバーライド経由でトップレベルの`requiredMinimumVersion`がユーザー設定へ合成される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(
            json.dumps({"requiredMinimumVersion": "2.1.163"}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["requiredMinimumVersion"] == "2.1.163"
        assert result["language"] == "japanese"

    def test_override_replaces_scalar(self, tmp_path: Path):
        """オーバーライドはベースのスカラー値を上書きする。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "english"}, ensure_ascii=False), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["language"] == "japanese"

    def test_missing_override_is_ignored(self, tmp_path: Path):
        """存在しないオーバーライドはスキップされる (他 OS を壊さない)。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        # overrides に空リストを渡してオーバーライドなしの動作を検証する
        update_claude_settings(managed_path, target_path, overrides=[])
        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}


class TestPlatformOverrideSelection:
    """プラットフォーム別オーバーライドが `update_claude_settings` で適用されることを検証する。"""

    def test_posix_override_is_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Linux (posix) 環境では `managed.posix.json` のオーバーライドが適用される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "english"}, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "managed.posix.json").write_text(json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "managed.win32.json").write_text(json.dumps({"os": "win32"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        # posix プラットフォームのオーバーライドのみを適用する
        monkeypatch.setattr(mod, "_MANAGED_SETTINGS_PATH", managed_path)
        monkeypatch.setattr(mod.sys, "platform", "linux")
        overrides = [tmp_path / "managed.posix.json"]
        update_claude_settings(managed_path, target_path, overrides=overrides)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["os"] == "posix"
        assert "language" in result

    def test_darwin_uses_posix_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """macOS (darwin) 環境では `managed.posix.json` のオーバーライドが適用される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "english"}, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "managed.posix.json").write_text(json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        monkeypatch.setattr(mod.sys, "platform", "darwin")
        overrides = [tmp_path / "managed.posix.json"]
        update_claude_settings(managed_path, target_path, overrides=overrides)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["os"] == "posix"

    def test_win32_override_is_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Windows (win32) 環境では `managed.win32.json` のオーバーライドが適用される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "english"}, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "managed.posix.json").write_text(json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "managed.win32.json").write_text(json.dumps({"os": "win32"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        monkeypatch.setattr(mod.sys, "platform", "win32")
        overrides = [tmp_path / "managed.win32.json"]
        update_claude_settings(managed_path, target_path, overrides=overrides)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["os"] == "win32"

    def test_missing_override_is_noop(self, tmp_path: Path):
        """override ファイルが存在しない場合はベース設定のみが適用される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        # オーバーライドなしで適用
        update_claude_settings(managed_path, target_path, overrides=[])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}


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

    def test_schema_key_is_ignored(self, tmp_path: Path):
        """`$schema` はマージ対象外で、ユーザー設定に伝播しない。"""
        existing = {"language": "english"}
        managed = {"$schema": "https://example.com/schema.json", "language": "japanese"}
        result = _run(tmp_path, managed, existing)
        assert "$schema" not in result
        assert result["language"] == "japanese"

    def test_schema_key_does_not_overwrite_existing(self, tmp_path: Path):
        """既存の `$schema` があっても managed 側の `$schema` で上書きされない。"""
        existing = {"$schema": "user-defined"}
        managed = {"$schema": "managed-defined", "language": "japanese"}
        result = _run(tmp_path, managed, existing)
        assert result["$schema"] == "user-defined"

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


class TestDiffLogging:
    """update_claude_settings の差分ログ出力を公開経路経由で検証する。

    値の要約・リスト差分・再帰差分は内部実装のため、設定更新時に logger へ
    出力される差分行を通じてまとめて検証する。
    """

    @staticmethod
    def _update_and_capture(
        tmp_path: Path,
        managed: dict,
        existing: dict,
        caplog: pytest.LogCaptureFixture,
        *,
        removed_env_keys: tuple[str, ...] = (),
    ) -> list[str]:
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps(managed, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        with caplog.at_level(logging.INFO, logger="pytools._internal.update_claude_settings"):
            update_claude_settings(managed_path, target_path, removed_env_keys=removed_env_keys)
        return caplog.messages

    def test_scalar_changes_are_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """文字列・数値・真偽値のスカラー変更が old → new 形式で6スペース付き出力される。"""
        existing = {"s": "english", "n": 1, "b": False}
        managed = {"s": "japanese", "n": 42, "b": True}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        text = "\n".join(messages)
        assert 's: "english" → "japanese"' in text
        assert "n: 1 → 42" in text
        assert "b: false → true" in text
        assert any(line.startswith("      s:") for line in messages)

    def test_new_key_value_summaries(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """新規キーの値が dict/list サマリーと長文切り詰めで出力される。"""
        existing: dict = {}
        managed = {"d": {"a": 1, "b": 2}, "l": [1, 2, 3], "long": "x" * 100}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        text = "\n".join(messages)
        assert "d: (新規) {...} (2 keys)" in text
        assert "l: (新規) [...] (3 件)" in text
        long_line = next(line for line in messages if line.startswith("      ") and "long: (新規)" in line)
        assert long_line.endswith("...")  # 60文字上限で切り詰められる

    def test_nested_dict_diff(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """ネストした dict の変更が親.子のパス付きで出力される。"""
        existing = {"permissions": {"allow": ["Bash"], "defaultMode": "plan"}}
        managed = {"permissions": {"defaultMode": "auto"}}
        text = "\n".join(self._update_and_capture(tmp_path, managed, existing, caplog))
        assert 'permissions.defaultMode: "plan" → "auto"' in text

    def test_string_list_union_shows_inline_diff(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """文字列リストの union 追加が件数と追加要素付きで出力される。"""
        existing = {"items": ["a", "b"]}
        managed = {"items": ["a", "b", "c"]}
        text = "\n".join(self._update_and_capture(tmp_path, managed, existing, caplog))
        assert "items: 2 → 3 件" in text
        assert '+"c"' in text

    def test_many_list_additions_show_count_only(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """差分件数が上限を超えるリストは件数のみ出力される（追加要素のインライン表示なし）。"""
        existing = {"items": ["a"]}
        managed = {"items": ["a", "b", "c", "d", "e"]}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        items_line = next(line for line in messages if line.startswith("      ") and "items:" in line)
        assert "1 → 5 件" in items_line
        assert "+" not in items_line

    def test_dict_element_list_shows_count_only(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """dict を要素に持つリスト（hooks 等）の差分は件数のみ出力される（インライン表示なし）。

        inner hooks には除去対象外の command を持たせる（空だと _strip_removed_hooks が
        matcher ごと除去してしまうため）。
        """
        existing = {"hooks": {"PreToolUse": [{"matcher": "A", "hooks": [{"type": "command", "command": "a.py"}]}]}}
        managed = {"hooks": {"PreToolUse": [{"matcher": "B", "hooks": [{"type": "command", "command": "b.py"}]}]}}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        hooks_line = next(line for line in messages if line.startswith("      ") and "PreToolUse:" in line)
        assert "1 → 2 件" in hooks_line
        assert "+" not in hooks_line

    def test_removed_env_key_shows_deletion(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """env キー除去で env が削除され、削除差分が出力される。"""
        existing = {"env": {"OLD": "1"}}
        managed: dict = {}
        text = "\n".join(self._update_and_capture(tmp_path, managed, existing, caplog, removed_env_keys=("OLD",)))
        assert "env:" in text
        assert "→ (削除)" in text

    def test_no_change_logs_no_diff_rows(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """差分が無い場合は変更なしのみで差分行（6スペース始まり）が出力されない。"""
        existing = {"language": "japanese"}
        managed = {"language": "japanese"}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        assert any("変更なし" in m for m in messages)
        assert not [m for m in messages if m.startswith("      ")]

    def test_diff_rows_sorted_by_key(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """差分行はキーのアルファベット順で出力される。"""
        existing: dict = {}
        managed = {"z": 1, "a": 2, "m": 3}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        diff_rows = [m for m in messages if m.startswith("      ")]
        keys = [m.split(":")[0].strip() for m in diff_rows]
        assert keys == ["a", "m", "z"]


class TestStripRemovedHooks:
    """配布元から削除された hook エントリの自動除去テスト。"""

    def test_removed_hook_is_dropped_from_existing(self, tmp_path: Path):
        """既存の hooks に旧 command 部分文字列が残っている場合、マージ前に除去される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps(
                {"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "new-cmd"}]}]}},
                ensure_ascii=False,
            ),
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
                },
                ensure_ascii=False,
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
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_hook_substrings=("old_hook.py",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        inner = result["hooks"]["PreToolUse"][0]["hooks"]
        assert len(inner) == 1
        assert inner[0]["command"] == "keep_me.py"

    @pytest.mark.parametrize(
        ("command", "should_be_removed"),
        [
            # 旧形式 (`uv run --script`): 除去対象
            (
                "sh -c 'uv run --script ~/dotfiles/scripts/claude_hook_pretooluse.py; exit 0'",
                True,
            ),
            (
                "sh -c 'uv run --script ~/dotfiles/scripts/claude_hook_stop.py; exit 0'",
                True,
            ),
            (
                'pwsh -c "uv run --script $env:USERPROFILE\\dotfiles\\scripts\\claude_hook_pretooluse.py"',
                True,
            ),
            (
                'pwsh -c "uv run --script $env:USERPROFILE\\dotfiles\\scripts\\claude_hook_stop.py"',
                True,
            ),
            # 新形式 (`uv run --no-project --script`): 保持
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook_pretooluse.py; exit 0'",
                False,
            ),
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook_stop.py; exit 0'",
                False,
            ),
        ],
    )
    def test_no_project_substrings_default(self, tmp_path: Path, command: str, should_be_removed: bool):
        """既定の除去パターンが旧 `uv run --script` 形式を除去し新 `--no-project` 形式を保持する。"""
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
                                "hooks": [{"type": "command", "command": command}],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        if should_be_removed:
            assert "PreToolUse" not in result.get("hooks", {})
        else:
            assert result["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == command


class TestStripRemovedEnvKeys:
    """配布元から削除された env キーの自動除去テスト。"""

    @pytest.mark.parametrize(
        ("existing_env", "removed_keys", "expected_env"),
        [
            # 削除対象キーあり → 除去される
            (
                {"OLD_KEY": "1", "KEEP_KEY": "keep"},
                ("OLD_KEY",),
                {"KEEP_KEY": "keep"},
            ),
            # 削除対象キーなし（別の env キーのみ） → 無変更
            (
                {"OTHER_KEY": "value"},
                ("OLD_KEY",),
                {"OTHER_KEY": "value"},
            ),
            # 削除後 env が空 dict → env キーごと除去される
            (
                {"OLD_KEY": "1"},
                ("OLD_KEY",),
                None,  # env キーごと消える
            ),
        ],
    )
    def test_env_key_removal(
        self,
        tmp_path: Path,
        existing_env: dict,
        removed_keys: tuple[str, ...],
        expected_env: dict | None,
    ):
        """env キーの除去・無変更・空後のキー消去を検証する。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"env": existing_env}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=removed_keys)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        if expected_env is None:
            assert "env" not in result
        else:
            assert result["env"] == expected_env

    def test_no_env_key_is_noop(self, tmp_path: Path):
        """env キー自体なし → 無変更。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"language": "japanese"}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=("OLD_KEY",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert "env" not in result
        assert result["language"] == "japanese"

    def test_non_dict_env_is_untouched(self, tmp_path: Path):
        """env が dict でない場合、削除対象キーを渡しても env は無変更のまま保持される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"env": "invalid"}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=("OLD_KEY",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["env"] == "invalid"

    def test_empty_removed_env_keys_is_noop(self, tmp_path: Path):
        """removed_env_keys が空タプルの場合、既存の env は無変更のまま保持される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"env": {"FOO": "bar", "BAZ": "qux"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=())

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["env"] == {"FOO": "bar", "BAZ": "qux"}

    def test_agent_teams_removed_and_managed_env_preserved(self, tmp_path: Path):
        """update_claude_settings 経由で CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS が消え、
        managed の env (CLAUDE_CODE_NO_FLICKER 等) は保持される。
        """
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"env": {"CLAUDE_CODE_NO_FLICKER": "1"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1", "CLAUDE_CODE_NO_FLICKER": "1"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # removed_env_keys は既定値（_REMOVED_ENV_KEYS）を使用
        update_claude_settings(managed_path, target_path)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in result["env"]
        assert result["env"]["CLAUDE_CODE_NO_FLICKER"] == "1"
