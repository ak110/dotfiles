"""`atk config`サブコマンド（`_atk_config`モジュール）のテスト。

`atk config show`（既定動作）・`get`・`set`の3操作と、XDG関連パスの解決結果を検証する。
"""

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
            "execute_model",
            "execute_review_model",
            "merge_model",
        ):
            assert f"{key}: codex:gpt-5.6-sol/medium" in out
        assert "codex_model:" not in out

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

    def test_get_stage_model_default(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未設定の工程別モデルは共通の既定値を返す。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_model"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "codex:gpt-5.6-sol/medium\n"

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
        assert captured.err == (
            "未知の設定キーです: no-such-key（利用可能: config_dir, data_dir, execute_model, "
            "execute_review_model, merge_model, pick_feedbacks_model, plan_model, plan_review_model, "
            "private_notes, state_dir）\n"
        )

    def test_get_known_and_unknown_keys_is_atomic(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの間に未知キーがある場合は値を出力せずexit 2とする。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "config_dir", "no-such-key", "data_dir"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert captured.err == (
            "未知の設定キーです: no-such-key（利用可能: config_dir, data_dir, execute_model, "
            "execute_review_model, merge_model, pick_feedbacks_model, plan_model, plan_review_model, "
            "private_notes, state_dir）\n"
        )


class TestConfigSet:
    """`atk config set`の変更可能設定更新を検証する。"""

    @pytest.mark.parametrize("value", ["codex:gpt-5.6-sol/medium", "claude:sonnet", "claude:opus/high"])
    def test_set_stage_model_persists_and_is_read_back(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        """正しい工程別モデル値を永続化し、以降の`get`へ反映する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "execute_model", value], home=tmp_path)
        assert exc_info.value.code == 0
        assert f"設定を更新しました: execute_model={value}" in capsys.readouterr().out

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "execute_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{value}\n"

        config_file = tmp_path / "config" / "config.json"
        assert config_file.exists()
        assert value in config_file.read_text(encoding="utf-8")

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
