"""`atk config`サブコマンド（`_atk_config`モジュール）のテスト。

`atk config show`（既定動作）・`get`・`set`の3操作と、XDG関連パスの解決結果を検証する。
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_config as config_module  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def _isolate_platformdirs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`platformdirs`の解決先を実環境から隔離する。"""
    monkeypatch.setattr(config_module.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    monkeypatch.setattr(config_module.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))
    monkeypatch.setattr(config_module.platformdirs, "user_data_dir", lambda _name, **_kwargs: str(tmp_path / "data"))
    for key in config_module._MUTABLE_KEY_DEFAULTS:  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.delenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", raising=False)


class TestConfigShow:
    """`atk config`（サブコマンド省略時はshow扱い）・`atk config show`の一覧表示を検証する。"""

    def test_show_lists_all_resolved_keys(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """XDG関連パスと工程別モデル設定の既定値が一覧表示される。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "show"], home=tmp_path)

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert f"config_dir: {tmp_path / 'config'}" in out
        assert f"state_dir: {tmp_path / 'state'}" in out
        assert f"data_dir: {tmp_path / 'data'}" in out
        assert "private_notes:" in out
        for key in (
            "pick_wi_model",
            "plan_model",
            "plan_review_model",
            "execute_fast_model",
            "execute_model",
            "execute_review_model",
            "session_review_model",
        ):
            assert f"{key}: codex:gpt-5.6-sol/medium" in out
        assert "execute_fix_model:" not in out
        assert "orchestrate_model: claude:opus[1m]/medium" in out
        assert "explore_model: codex:gpt-5.6-sol/medium" in out
        assert "explore_fast_model: codex:gpt-5.6-terra/medium" in out
        assert "codex_model:" not in out
        assert "merge_model:" not in out

    def test_no_subcommand_defaults_to_show(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`atk config`（サブコマンド省略）は`show`と同じ出力になる。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "config_dir:" in capsys.readouterr().out


class TestConfigGet:
    """`atk config get`の1件以上のキー取得を検証する。"""

    def test_get_known_key(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの値のみを1行で出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "config_dir"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{tmp_path / 'config'}\n"

    def test_get_state_dir_matches_public_helper(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`state_dir()`の戻り値と`atk config get state_dir`の出力が一致する。

        フックは`state_dir()`経由で状態ファイルを配置する。
        両者が一致しない場合、利用者が`atk config get`で確認した位置と実際の配置先が異なる。
        """
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "state_dir"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{config_module.state_dir()}\n"

    def test_get_state_dir_rejects_relative_xdg_state_home(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """相対XDG_STATE_HOMEはHOME配下へ退避し、状態読取側と同じ絶対パスを返す。"""
        monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setattr(
            config_module.platformdirs,
            "user_state_dir",
            lambda _name, **_kwargs: "relative-state/agent-toolkit",
        )

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "state_dir"], home=tmp_path)

        assert exc_info.value.code == 0
        expected = tmp_path / "home" / ".local" / "state" / "agent-toolkit"
        assert capsys.readouterr().out == f"{expected}\n"

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("execute_fast_model", "codex:gpt-5.6-sol/medium"),
            ("execute_model", "codex:gpt-5.6-sol/medium"),
            ("session_review_model", "codex:gpt-5.6-sol/medium"),
        ],
    )
    def test_get_execute_model_defaults(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key: str, expected: str
    ) -> None:
        """未設定の工程別モデルは共通の既定値を返す。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", key], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{expected}\n"

    def test_get_orchestrate_model_default(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未設定のオーケストレーター設定はClaude Codeの既定値を返す。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "orchestrate_model"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "claude:opus[1m]/medium\n"

    def test_get_multiple_keys_in_requested_order(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """複数キーの値を指定順に1行ずつ出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "data_dir", "config_dir"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{tmp_path / 'data'}\n{tmp_path / 'config'}\n"

    def test_get_unknown_key_exits_2(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未知キーはexit 2でエラー案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "no-such-key"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "execute_fast_model" in captured.err
        assert "execute_model" in captured.err
        assert "session_review_model" in captured.err
        assert "execute_fix_model" not in captured.err

    def test_get_known_and_unknown_keys_is_atomic(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの間に未知キーがある場合は値を出力せずexit 2とする。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "config_dir", "no-such-key", "data_dir"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "execute_fast_model" in captured.err
        assert "execute_model" in captured.err
        assert "session_review_model" in captured.err
        assert "execute_fix_model" not in captured.err

    def test_environment_override_applies_to_get_and_show_then_restores_saved_value(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空でない環境変数は保存値より優先され、解除後は保存値へ戻る。"""
        saved = "codex:gpt-5.6-sol/high"
        override = "claude:sonnet/low,codex:gpt-5.6-terra/medium"
        with pytest.raises(SystemExit):
            atk.main(["config", "set", "plan_model", saved], home=tmp_path)
        capsys.readouterr()

        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PLAN_MODEL", override)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "plan_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{override}\n"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "show"], home=tmp_path)
        assert exc_info.value.code == 0
        assert f"plan_model: {override}" in capsys.readouterr().out

        monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_PLAN_MODEL")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "plan_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{saved}\n"

    @pytest.mark.parametrize("subcommand", [["get", "plan_model"], ["show"]])
    def test_invalid_environment_override_exits_2(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        subcommand: list[str],
    ) -> None:
        """不正な環境変数値は実効値へ渡さず、変数名と値を示して拒否する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PLAN_MODEL", "codex:gpt-5.6-sol/medium, invalid")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", *subcommand], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "AGENT_TOOLKIT_CONFIG_PLAN_MODEL" in captured.err
        assert "codex:gpt-5.6-sol/medium, invalid" in captured.err

    def test_empty_environment_override_is_ignored(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """空文字列の環境変数は未指定として既定値を返す。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PLAN_MODEL", "")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "plan_model"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "codex:gpt-5.6-sol/medium\n"

    def test_immutable_environment_name_does_not_override_private_notes(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """変更可能キー以外を模した環境変数は既存の解決経路へ影響しない。"""
        with pytest.raises(SystemExit):
            atk.main(["config", "get", "private_notes"], home=tmp_path)
        original = capsys.readouterr().out

        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PRIVATE_NOTES", "/unexpected")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "private_notes"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == original


class TestConfigSet:
    """`atk config set`の変更可能設定更新を検証する。"""

    @pytest.mark.parametrize("key", ["execute_fast_model", "execute_model"])
    @pytest.mark.parametrize("value", ["codex:gpt-5.6-sol/medium", "claude:sonnet", "claude:opus/high"])
    def test_set_stage_model_persists_and_is_read_back(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key: str, value: str
    ) -> None:
        """正しい工程別モデル値を永続化し、以降の`get`へ反映する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", key, value], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"設定を更新しました: {key}={value}" in captured.out
        assert not captured.err

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", key], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{value}\n"

        config_file = tmp_path / "config" / "config.json"
        assert config_file.exists()
        assert value in config_file.read_text(encoding="utf-8")

    def test_legacy_execute_fix_model_is_rejected(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """旧`execute_fix_model`は公開設定キーとして変更できない。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "execute_fix_model", "codex:gpt-5.6-sol/medium"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "変更できない設定キーです: execute_fix_model" in capsys.readouterr().err
        assert not (tmp_path / "config" / "config.json").exists()

    def test_legacy_execute_fix_model_get_is_rejected(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """旧`execute_fix_model`は`get`でも未知キーとして拒否する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_fix_model"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "未知の設定キーです: execute_fix_model" in captured.err

    def test_known_claude_models_include_fable(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`claude:fable`は主に使うモデルの一覧に含まれ、警告を出力しない。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "execute_model", "claude:fable/medium"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not capsys.readouterr().err

    def test_set_preserves_unconfigured_defaults(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """旧キーがない設定保存では、未設定の既定値を永続化しない。"""
        config_file = tmp_path / "config" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({"other_setting": "keep"}) + "\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", "codex:gpt-5.6-terra/medium"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not capsys.readouterr().err
        assert json.loads(config_file.read_text(encoding="utf-8")) == {
            "other_setting": "keep",
            "plan_model": "codex:gpt-5.6-terra/medium",
        }

    def test_set_orchestrate_model_persists_and_is_read_back(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """オーケストレーター設定を永続化し、以降の`get`へ反映する。"""
        value = "codex:gpt-5.6-sol/high"
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "orchestrate_model", value], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"設定を更新しました: orchestrate_model={value}\n"
        assert not captured.err

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "orchestrate_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{value}\n"

    def test_set_candidate_list_persists_and_is_read_back(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """複数候補を指定順の文字列のまま保存して読み戻す。"""
        value = "codex:gpt-5.6-sol/medium,claude:opus[1m]/medium"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", value], home=tmp_path)
        assert exc_info.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "plan_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{value}\n"

    def test_invalid_second_candidate_does_not_update_saved_value(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """2候補目だけが不正でも候補列全体を拒否し、保存済み設定を維持する。"""
        saved = "codex:gpt-5.6-terra/high"
        with pytest.raises(SystemExit):
            atk.main(["config", "set", "plan_model", saved], home=tmp_path)
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", f"{saved},不正な値"], home=tmp_path)
        assert exc_info.value.code == 2
        capsys.readouterr()

        with pytest.raises(SystemExit):
            atk.main(["config", "get", "plan_model"], home=tmp_path)
        assert capsys.readouterr().out == f"{saved}\n"

    @pytest.mark.parametrize(
        "value",
        [
            "codex:gpt-5.6-sol/medium,",
            ",codex:gpt-5.6-sol/medium",
            "codex:gpt-5.6-sol/medium,,claude:sonnet/high",
            "codex:gpt-5.6-sol/medium, claude:sonnet/high",
        ],
    )
    def test_set_rejects_empty_or_space_padded_candidate(self, tmp_path: pathlib.Path, value: str) -> None:
        """空候補と前後に空白を持つ候補を拒否する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", value], home=tmp_path)

        assert exc_info.value.code == 2

    def test_candidate_warnings_identify_only_unknown_candidate(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """候補ごとの既知一覧照合で一覧外の候補だけを警告し、保存は成功する。"""
        known = "codex:gpt-5.6-sol/medium"
        unknown = "claude:unknown-model/medium"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", f"{known},{unknown}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"候補`{unknown}`" in captured.err
        assert f"候補`{known}`" not in captured.err

    def test_set_with_environment_override_warns_but_updates_saved_value(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """環境変数が優先中でも保存先を更新し、実効値にならない旨を警告する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PLAN_MODEL", "claude:sonnet/high")
        saved = "codex:gpt-5.6-terra/low"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", saved], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "AGENT_TOOLKIT_CONFIG_PLAN_MODEL" in captured.err
        config_file = tmp_path / "config" / "config.json"
        assert json.loads(config_file.read_text(encoding="utf-8"))["plan_model"] == saved

    def test_parse_candidates_normalizes_effort_and_preserves_distinct_values(self) -> None:
        """候補列を3つ組へ分解し、省略effortを補完して異なるeffortを保持する。"""
        assert config_module.parse_stage_model_candidates("codex:gpt-5.6-sol,claude:sonnet/high,claude:sonnet/low") == [
            ("codex", "gpt-5.6-sol", "medium"),
            ("claude", "sonnet", "high"),
            ("claude", "sonnet", "low"),
        ]

    def test_resolve_model_candidates_maps_model_type_and_rejects_unknown(self) -> None:
        """model_typeを対応設定の候補へ解決し、未知値は両方の受理形式を示して拒否する。"""
        assert config_module.resolve_model_candidates("explore_fast") == [("codex", "gpt-5.6-terra", "medium")]
        with pytest.raises(ValueError, match=r"unknown model_type: no-such.*explore_fast.*plan"):
            config_module.resolve_model_candidates("no-such")
        with pytest.raises(ValueError, match=r"or pass candidates like codex:gpt-5\.6-sol/medium"):
            config_module.resolve_model_candidates("no-such")

    def test_resolve_model_candidates_accepts_direct_candidates(self, tmp_path: pathlib.Path) -> None:
        """設定値と同じ書式の候補列を直接受理し、設定を読まずに当該候補を返す。"""
        config_file = tmp_path / "config" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"plan_model": "claude:haiku/low"}), encoding="utf-8")

        assert config_module.resolve_model_candidates("codex:gpt-5.6-sol/medium") == [("codex", "gpt-5.6-sol", "medium")]
        assert config_module.resolve_model_candidates("claude:opus[1m]") == [("claude", "opus[1m]", "medium")]
        assert config_module.resolve_model_candidates("codex:a/low,claude:opus/high") == [
            ("codex", "a", "low"),
            ("claude", "opus", "high"),
        ]

    @pytest.mark.parametrize("value", ["codex:", "nosuch:model", "codex:a/b/c"])
    def test_resolve_model_candidates_rejects_malformed_direct_candidates(self, value: str) -> None:
        """候補列としても種別としても成立しない値は`ValueError`で拒否する。"""
        with pytest.raises(ValueError, match=r"unknown model_type"):
            config_module.resolve_model_candidates(value)

    def test_set_unknown_model_warns_and_persists(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """参考一覧に無いモデル名は警告を表示したうえで受理し、永続化する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "execute_fast_model", "claude:unknown-model"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "設定を更新しました: execute_fast_model=claude:unknown-model" in captured.out
        assert "モデル名`unknown-model`は主に使うモデルの一覧" in captured.err
        assert "利用可否は実行時に各engineが判定します" in captured.err

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_fast_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "claude:unknown-model\n"

    def test_set_unknown_effort_warns_and_persists(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """参考一覧に無いeffortは警告を表示したうえで受理し、永続化する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", "codex:gpt-5.6-sol/ultra"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "effort`ultra`は主に使う値の一覧" in captured.err
        assert "利用可否は実行時に各engineが判定します" in captured.err
        assert "のモデル名" not in captured.err

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "plan_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "codex:gpt-5.6-sol/ultra\n"

    def test_set_unknown_model_and_effort_warns_both(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """モデル名とeffortがともに参考一覧外の場合は両方の警告を表示して受理する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "execute_review_model", "codex:new-model/ultra"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "モデル名`new-model`は主に使うモデルの一覧" in captured.err
        assert "effort`ultra`は主に使う値の一覧" in captured.err

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_review_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "codex:new-model/ultra\n"

    def test_removed_merge_model_is_unknown(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """廃止した`merge_model`は変更可能キーとして受理しない。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "merge_model", "codex:gpt-5.6-sol/medium"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "変更できない設定キーです: merge_model" in capsys.readouterr().err

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "merge_model"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "merge_model" in captured.err

    def test_set_known_value_without_effort_no_warning(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """参考一覧内のモデル名をeffort省略で設定した場合は警告を表示しない。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_review_model", "codex:gpt-5.6-terra"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not capsys.readouterr().err

    @pytest.mark.parametrize("value", ["gpt-5.6-sol", "other:model", "codex:", "claude:model/"])
    def test_set_invalid_stage_model_exits_2(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        """不正な工程別モデル値はexit 2で受理可能書式を案内する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", value], home=tmp_path)

        assert exc_info.value.code == 2
        assert "<claude|codex>:<model>[/<effort>]" in capsys.readouterr().err

    def test_removed_codex_model_is_unknown(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """廃止した`codex_model`は変更可能キーとして受理しない。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "codex_model", "codex:gpt-5.6-sol/medium"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "変更できない設定キーです" in capsys.readouterr().err

    def test_set_immutable_key_exits_2(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """XDGパス等の導出値キーは変更できずexit 2でエラー案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "config_dir", "/tmp/somewhere"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "変更できない設定キーです" in capsys.readouterr().err
