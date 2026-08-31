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
            "pick_feedbacks_model",
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

    def test_legacy_execute_fix_model_get_aliases_execute_model(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """旧キー名の`get`は現行キーの解決値を返す。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_fix_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "codex:gpt-5.6-sol/medium\n"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "execute_model", "claude:sonnet/high"], home=tmp_path)
        assert exc_info.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_fix_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "claude:sonnet/high\n"

    def test_stored_legacy_execute_fix_model_does_not_affect_execute_model(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """保存済み旧キーは現行キーの実効値へ影響しない。"""
        config_file = tmp_path / "config" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({"execute_fix_model": "claude:sonnet/high"}) + "\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_model"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "codex:gpt-5.6-sol/medium\n"

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

    def test_set_unknown_model_warns_and_persists(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """参考一覧に無いモデル名は警告を表示したうえで受理し、永続化する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "execute_fast_model", "claude:fable"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "設定を更新しました: execute_fast_model=claude:fable" in captured.out
        assert "警告: モデル名`fable`は主に使うモデルの一覧" in captured.err
        assert "利用可否は実行時に各engineが判定します" in captured.err

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_fast_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "claude:fable\n"

    def test_set_unknown_effort_warns_and_persists(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """参考一覧に無いeffortは警告を表示したうえで受理し、永続化する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "plan_model", "codex:gpt-5.6-sol/ultra"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "警告: effort`ultra`は主に使う値の一覧" in captured.err
        assert "利用可否は実行時に各engineが判定します" in captured.err
        assert "警告: モデル名" not in captured.err

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
        assert "警告: モデル名`new-model`は主に使うモデルの一覧" in captured.err
        assert "警告: effort`ultra`は主に使う値の一覧" in captured.err

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
