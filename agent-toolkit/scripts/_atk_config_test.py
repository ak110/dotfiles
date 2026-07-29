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
        """XDG関連パスとcodex_model（未設定時は既定表示）が一覧表示される。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "show"], home=tmp_path)

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert f"config_dir: {tmp_path / 'config'}" in out
        assert f"state_dir: {tmp_path / 'state'}" in out
        assert f"data_dir: {tmp_path / 'data'}" in out
        assert "private_notes:" in out
        assert "codex_model: (未設定)" in out

    def test_no_subcommand_defaults_to_show(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`atk config`（サブコマンド省略）は`show`と同じ出力になる。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "config_dir:" in capsys.readouterr().out


class TestConfigGet:
    """`atk config get`の単一キー取得を検証する。"""

    def test_get_known_key(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの値のみを1行で出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "config_dir"], home=tmp_path)

        assert exc_info.value.code == 0
        assert capsys.readouterr().out == f"{tmp_path / 'config'}\n"

    def test_get_unknown_key_exits_2(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未知キーはexit 2でエラー案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "no-such-key"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "未知の設定キーです" in capsys.readouterr().err


class TestConfigSet:
    """`atk config set`の変更可能設定更新を検証する。"""

    def test_set_codex_model_persists_and_is_read_back(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`codex_model`を設定すると永続化され、以降の`show`・`get`へ反映される。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "codex_model", "gpt-5.6-sol"], home=tmp_path)
        assert exc_info.value.code == 0
        assert "設定を更新しました: codex_model=gpt-5.6-sol" in capsys.readouterr().out

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "get", "codex_model"], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "gpt-5.6-sol\n"

        config_file = tmp_path / "config" / "config.json"
        assert config_file.exists()
        assert "gpt-5.6-sol" in config_file.read_text(encoding="utf-8")

    def test_set_immutable_key_exits_2(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """XDGパス等の導出値キーは変更できずexit 2でエラー案内を出力する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["config", "set", "config_dir", "/tmp/somewhere"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "変更できない設定キーです" in capsys.readouterr().err
