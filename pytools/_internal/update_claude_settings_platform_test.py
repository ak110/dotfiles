"""update_claude_settingsのplatformの試験。"""

# pylint: disable=protected-access

import json
import logging
from pathlib import Path

import pytest

from pytools._internal import update_claude_settings as mod
from pytools._internal.update_claude_settings import update_claude_settings
from pytools._internal.update_claude_settings_test_helpers import _PROD_MANAGED_CONFIG, _setup_run_paths


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


class TestCodexMcpTimeout:
    """`run()`経由で旧Codex MCP timeoutの一回限りの除去を検証する。"""

    def test_production_config_does_not_manage_codex_mcp(self) -> None:
        """配布設定は`mcpServers`自体を管理しない。"""
        managed = json.loads(_PROD_MANAGED_CONFIG.read_text(encoding="utf-8"))
        assert "mcpServers" not in managed

    def test_legacy_timeout_is_removed_from_generated_definition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """旧installerが生成する形の定義から管理値のtimeoutだけを除去する。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing_codex = {
            "type": "stdio",
            "command": "codex",
            "args": ["mcp-server"],
            "env": {},
            "timeout": 7_200_000,
        }
        config_path.write_text(json.dumps({"mcpServers": {"codex": existing_codex}}), encoding="utf-8")

        mod.run()

        result = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["codex"]
        assert result == {key: value for key, value in existing_codex.items() if key != "timeout"}

    def test_definition_with_extra_fields_is_preserved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ユーザーが加えたフィールドを持つ定義は変更しない。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing = {
            "mcpServers": {
                "codex": {
                    "type": "stdio",
                    "command": "codex",
                    "args": ["mcp-server"],
                    "env": {"CUSTOM": "value"},
                    "customField": True,
                    "timeout": 7_200_000,
                }
            }
        }
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8")) == existing

    def test_settings_json_does_not_strip_legacy_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """User scope設定ではないsettings.jsonの同名キーを変更しない。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {})
        existing_codex = {"command": "codex", "args": ["mcp-server"], "timeout": 7_200_000}
        settings_path.write_text(json.dumps({"mcpServers": {"codex": existing_codex}}), encoding="utf-8")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))["mcpServers"]["codex"]
        assert result == existing_codex

    @pytest.mark.parametrize(
        "existing_codex",
        [
            {"command": "other", "args": ["mcp-server"], "timeout": 7_200_000},
            {"type": "http", "command": "codex", "args": ["mcp-server"], "timeout": 7_200_000},
            {"command": "codex", "args": ["other"], "timeout": 7_200_000},
        ],
    )
    def test_non_legacy_definition_preserves_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        existing_codex: dict,
    ) -> None:
        """旧定義と一致しない同名serverのtimeoutを保持する。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        config_path.write_text(json.dumps({"mcpServers": {"codex": existing_codex}}), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["codex"] == existing_codex

    def test_custom_timeout_is_preserved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """従来管理値と異なるtimeoutはユーザー設定として保持する。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing = {"mcpServers": {"codex": {"command": "codex", "args": ["mcp-server"], "timeout": 1_000}}}
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8")) == existing

    @pytest.mark.parametrize(
        "existing",
        [
            {},
            {"mcpServers": {"codex": {"type": "stdio", "args": ["mcp-server"]}}},
            {"mcpServers": {"codex": {"type": "stdio", "command": ""}}},
            {"mcpServers": {"codex": {"type": "http", "command": "codex"}}},
        ],
    )
    def test_missing_or_incomplete_definition_does_not_create_timeout_only_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        existing: dict,
    ) -> None:
        """Codex定義が不在または不完全な場合はtimeoutを追加しない。"""
        _setup_run_paths(
            tmp_path,
            monkeypatch,
            {},
            {},
        )
        config_path = tmp_path / "claude.json"
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8")) == existing


class TestPlatformOverrideSelection:
    """`run()` 経由でのプラットフォーム別オーバーライド適用を検証する統合テスト。

    `sys.platform` をパッチして `_platform_overrides()` の選択ロジックを通す。
    オーバーライドファイル選択・読み込み・マージ結果反映までを一連で検証する。
    """

    def test_linux_applies_posix_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Linux (posix) 環境では `managed_settings.posix.json` が適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "english"})
        (tmp_path / "dotfiles-root" / "share" / "claude_settings_json_managed.posix.json").write_text(
            json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8"
        )
        (tmp_path / "dotfiles-root" / "share" / "claude_settings_json_managed.win32.json").write_text(
            json.dumps({"os": "win32"}, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setattr(mod.sys, "platform", "linux")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["os"] == "posix"
        assert result["language"] == "english"

    def test_darwin_applies_posix_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """macOS (darwin) 環境でも `managed_settings.posix.json` が適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "english"})
        (tmp_path / "dotfiles-root" / "share" / "claude_settings_json_managed.posix.json").write_text(
            json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setattr(mod.sys, "platform", "darwin")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["os"] == "posix"

    def test_win32_applies_win32_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Windows (win32) 環境では `managed_settings.win32.json` が適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "english"})
        (tmp_path / "dotfiles-root" / "share" / "claude_settings_json_managed.posix.json").write_text(
            json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8"
        )
        (tmp_path / "dotfiles-root" / "share" / "claude_settings_json_managed.win32.json").write_text(
            json.dumps({"os": "win32"}, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setattr(mod.sys, "platform", "win32")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["os"] == "win32"

    def test_missing_override_applies_base_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """対応オーバーライドファイルが存在しない場合はベース設定のみが適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "japanese"})
        monkeypatch.setattr(mod.sys, "platform", "linux")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}


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
