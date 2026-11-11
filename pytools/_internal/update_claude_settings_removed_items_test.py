"""update_claude_settingsのremoved_itemsの試験。"""

# pylint: disable=protected-access

import json
from pathlib import Path

import pytest

from pytools._internal import removal_registry
from pytools._internal import update_claude_settings as mod
from pytools._internal.update_claude_settings import update_claude_settings
from pytools._internal.update_claude_settings_test_helpers import MANAGED_DENY, _setup_run_paths, _use_managed_files


class TestStripRemovedKeys:
    """配布元から廃止した設定キーの自動除去テスト。"""

    @pytest.mark.parametrize(
        ("existing", "expected"),
        [
            (
                {"autoMode": {"allowMode": "extend", "allow": ["ユーザー独自ルール"]}},
                {"autoMode": {"allow": ["ユーザー独自ルール"]}},
            ),
            (
                {"autoMode": {"allow": ["ユーザー独自ルール"]}},
                {"autoMode": {"allow": ["ユーザー独自ルール"]}},
            ),
        ],
    )
    def test_removed_key_is_deleted_or_absent_without_other_changes(
        self,
        tmp_path: Path,
        existing: dict,
        expected: dict,
    ) -> None:
        """登録キーを除去し、キーが存在しない場合は他の設定を変更しない。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, removed_keys=("autoMode.allowMode",))

        assert json.loads(target_path.read_text(encoding="utf-8")) == expected

    def test_run_safely_removes_key_from_settings_and_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`はsettings.jsonと.claude.jsonの廃止キーを安全に除去する。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing = {"autoMode": {"allowMode": "extend", "allow": ["ユーザー独自ルール"]}}
        settings_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        config_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        mod.run()

        expected = {"autoMode": {"allow": ["ユーザー独自ルール"]}}
        assert json.loads(settings_path.read_text(encoding="utf-8")) == expected
        assert json.loads(config_path.read_text(encoding="utf-8")) == expected

    def test_run_removes_verbose_from_claude_json_only(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`は`~/.claude.json`の`verbose`を除去し、settings.json側の値は保つ。

        表示の指定を`~/.claude/settings.json`の1箇所へ残し、両方が同じ表示を指定する状態を解く。
        """
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"verbose": False})
        config_path = tmp_path / "claude.json"
        settings_path.write_text(json.dumps({"verbose": True}, ensure_ascii=False), encoding="utf-8")
        config_path.write_text(json.dumps({"verbose": True, "other": 1}, ensure_ascii=False), encoding="utf-8")

        mod.run()

        assert json.loads(settings_path.read_text(encoding="utf-8"))["verbose"] is False
        assert json.loads(config_path.read_text(encoding="utf-8")) == {"other": 1}


class TestStripRemovedListItems:
    """配布元から削除された配列項目の自動削除テスト。"""

    def test_strip_removed_list_items_removes_matching(self, tmp_path: Path):
        """登録した部分文字列を含む配列要素が削除される。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["OLD_RULE_MARKER で始まる旧ルール文面", "新ルール文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["新ルール文面"]

    def test_strip_removed_list_items_preserves_others(self, tmp_path: Path):
        """部分文字列を含まない要素（ユーザーの独自追加項目）は保持される。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["ユーザー独自ルール1", "ユーザー独自ルール2"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["ユーザー独自ルール1", "ユーザー独自ルール2"]

    def test_strip_removed_list_items_missing_path(self, tmp_path: Path):
        """対象パスが存在しない場合は例外を送出せず処理が継続する。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}

    def test_strip_removed_list_items_non_list_target(self, tmp_path: Path):
        """パス先が list でない場合は何もしない。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"autoMode": {"allow": "not a list"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == "not a list"

    def test_run_applies_list_items_to_settings_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """`run()`経由で settings.json 側にのみ削除マッピングが適用される。

        .claude.json 側の同名構造（仮にあった場合）は保持されることを検証する回帰テスト。
        """
        managed_settings_path = tmp_path / "managed_settings.json"
        managed_settings_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        managed_config_path = tmp_path / "managed_config.json"
        managed_config_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        settings_path = tmp_path / "settings.json"
        old_rule_marker = next(
            marker
            for path, marker in removal_registry.values(mod._REMOVED_LIST_ITEM_SUBSTRINGS)  # pylint: disable=protected-access
            if path == "autoMode.allow"
        )
        settings_path.write_text(
            json.dumps(
                {"autoMode": {"allow": [f"{old_rule_marker} 旧ルール文面", "新ルール文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_path = tmp_path / "claude.json"
        config_path.write_text(
            json.dumps(
                {"autoMode": {"allow": [f"{old_rule_marker} を含む config 側項目"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        _use_managed_files(monkeypatch, tmp_path, managed_settings_path, managed_config_path)
        monkeypatch.setattr(mod, "_SETTINGS_PATH", settings_path)
        monkeypatch.setattr(mod, "_CONFIG_PATH", config_path)

        mod.run()

        # settings 側は旧ルール文面が削除される
        settings_result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings_result["autoMode"]["allow"] == ["新ルール文面"]
        # config 側は削除されない（仮に同名構造があっても保持される）
        config_result = json.loads(config_path.read_text(encoding="utf-8"))
        assert config_result["autoMode"]["allow"] == [f"{old_rule_marker} を含む config 側項目"]

    def test_run_removes_legacy_env_deny_and_preserves_user_deny(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`は旧管理denyだけを除去し、ユーザー独自denyと現行管理denyを保持する。

        撤去した秘匿ファイルの読取禁止3件と、以前に撤去した`Read(./.env)`が除去対象である。
        """
        settings_path = _setup_run_paths(
            tmp_path,
            monkeypatch,
            {"permissions": {"deny": MANAGED_DENY}},
        )
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {
                        "deny": [
                            "Read(./.env)",
                            "Read(./.secret)",
                            "Read(*.key)",
                            "Read(*.crt)",
                            "Read(//**/.credentials.json)",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["permissions"]["deny"] == ["Read(./.secret)", "Read(./secrets/**)"]

    def test_run_removes_legacy_personal_repo_push_rule(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`は移管前の個人リポジトリpushルールだけを除去する。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {})
        settings_path.write_text(
            json.dumps(
                {
                    "autoMode": {
                        "allow": [
                            "ak110の個人リポジトリ（dotfiles, pytilpack等）ではデフォルトブランチへの直接pushを許可。",
                            "ユーザー独自ルール",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["ユーザー独自ルール"]

    def test_run_removes_legacy_codex_mcp_permission(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`は旧Codex MCPの許可だけを除去し、現行MCPとユーザー独自許可を保持する。"""
        settings_path = _setup_run_paths(
            tmp_path,
            monkeypatch,
            {"permissions": {"allow": ["mcp__plugin_agent-toolkit_codex_app_server__codex_start"]}},
        )
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {
                        "allow": [
                            "mcp__codex__codex",
                            "mcp__codex__codex_reply",
                            "Bash(ls:*)",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["permissions"]["allow"] == [
            "Bash(ls:*)",
            "mcp__plugin_agent-toolkit_codex_app_server__codex_start",
        ]


class TestStripStaleLabeledListItems:
    """ラベル単位の旧文面自動削除テスト（配布原本のラベル改訂時の重複蓄積を防ぐ）。"""

    def test_stale_label_variant_is_removed_on_managed_text_revision(self, tmp_path: Path):
        """配布原本側でラベル本文を改訂すると、配布先の旧文面がラベル一致で除去される（再発防止テスト）。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["AWI-Originated Gate Revision: atk wi process-loop を使う新文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "autoMode": {
                        "allow": [
                            "ak110の個人リポジトリでのユーザー独自エントリ",
                            "AWI-Originated Gate Revision: atk fb process-loop を使う旧文面",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == [
            "ak110の個人リポジトリでのユーザー独自エントリ",
            "AWI-Originated Gate Revision: atk wi process-loop を使う新文面",
        ]

    def test_unlabeled_user_entry_is_preserved(self, tmp_path: Path):
        """ラベルを持たないユーザー独自エントリは除去対象にならない（再発防止テスト）。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"autoMode": {"allow": ["Some Label: 現行文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"autoMode": {"allow": ["ラベルを持たないユーザー独自ルール"]}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == [
            "ラベルを持たないユーザー独自ルール",
            "Some Label: 現行文面",
        ]

    def test_empty_paths_is_noop(self, tmp_path: Path):
        """`stale_labeled_list_paths`が空タプルの場合は何もしない（省略時の値で設定を変えないことを確かめる再発防止テスト）。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"autoMode": {"allow": ["Label: 現行文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"autoMode": {"allow": ["Label: 旧文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["Label: 旧文面", "Label: 現行文面"]

    def test_missing_managed_path_is_noop(self, tmp_path: Path):
        """配布原本（managed）側に対象パスが存在しない場合は例外を送出せず処理が継続する（再発防止テスト）。

        `_strip_stale_labeled_list_items`はmanaged側からラベル集合を抽出できず`labels`が
        空集合になるため、早期returnでtarget側の経路解決には到達しない分岐を検証する。
        """
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}

    def test_missing_target_path_is_noop(self, tmp_path: Path):
        """配布先（target）側に対象パスが存在しない場合は例外を送出せず通常マージが進む（再発防止テスト）。

        managed側にはラベル付き項目が存在するため`labels`は非空になり、target側の
        経路解決（`_resolve_dict_container`）がdictでない/存在しないキーで`None`を返す
        分岐を検証する。初回導入など`settings.json`に`autoMode`キー自体が無いケースに相当する。
        """
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"autoMode": {"allow": ["Label: 現行文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese", "autoMode": {"allow": ["Label: 現行文面"]}}

    def test_run_reproduces_and_fixes_prod_duplicate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """`run()`経由で実際の重複事象（旧文面atk fbと新文面atk wiの共存）が解消される（再現テスト）。"""
        managed_settings_path = tmp_path / "managed_settings.json"
        managed_settings_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["AWI-Originated Gate Revision: atk wi process-loop を使う新文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        managed_config_path = tmp_path / "managed_config.json"
        managed_config_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "autoMode": {
                        "allow": [
                            "ak110の個人リポジトリでのユーザー独自エントリ",
                            "AWI-Originated Gate Revision: atk fb process-loop を使う旧文面",
                            "AWI-Originated Gate Revision: atk wi process-loop を使う新文面",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_path = tmp_path / "claude.json"
        config_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

        _use_managed_files(monkeypatch, tmp_path, managed_settings_path, managed_config_path)
        monkeypatch.setattr(mod, "_SETTINGS_PATH", settings_path)
        monkeypatch.setattr(mod, "_CONFIG_PATH", config_path)

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == [
            "ak110の個人リポジトリでのユーザー独自エントリ",
            "AWI-Originated Gate Revision: atk wi process-loop を使う新文面",
        ]
