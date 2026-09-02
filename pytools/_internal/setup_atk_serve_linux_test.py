"""pytools._internal.setup_atk_serve_linux のテスト。

各分岐 (非 Linux・euryale 以外・uv 不在・ランチャー新規・ランチャー既存・共通処理への委譲) を検証する。
"""

# pylint: disable=protected-access

import pathlib
import stat
import subprocess
import typing

import pytest

from pytools._internal import claude_common, setup_atk_serve_linux, systemd_user_unit


def _run_linux_euryale(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """テスト共通の Linux + euryale + uv 配置済み環境をセットアップする。"""
    monkeypatch.setattr(claude_common.sys, "platform", "linux")
    monkeypatch.setattr(claude_common.socket, "gethostname", lambda: "euryale")
    monkeypatch.setattr(setup_atk_serve_linux.pathlib.Path, "home", lambda: tmp_path)
    uv = tmp_path / ".local" / "bin" / "uv"
    uv.parent.mkdir(parents=True, exist_ok=True)
    uv.touch()


def test_unit_excludes_host_specific_args() -> None:
    """ユニット本文が待受アドレス・ポートを固定しないことを検証する。"""
    assert "ExecStart=%h/.local/bin/atk-serve\n" in setup_atk_serve_linux._UNIT_CONTENT
    assert "--host" not in setup_atk_serve_linux._UNIT_CONTENT
    assert "--port" not in setup_atk_serve_linux._UNIT_CONTENT


class TestRunPlatformGuard:
    """非 Linux および euryale 以外のホストでの no-op 動作。"""

    @pytest.mark.parametrize(
        ("platform", "hostname"),
        [("win32", "euryale"), ("linux", "other-host")],
    )
    def test_non_target_environment_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        hostname: str,
    ) -> None:
        """Linux かつ euryale 以外では False を返し設定処理を開始しない。"""
        monkeypatch.setattr(claude_common.sys, "platform", platform)
        monkeypatch.setattr(claude_common.socket, "gethostname", lambda: hostname)
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: pytest.fail(str(kwargs)))
        assert not setup_atk_serve_linux.run()


class TestRunUvResolution:
    """uv 絶対パスの解決順序。"""

    def test_prefers_local_bin_uv_over_path_lookup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`~/.local/bin/uv` が存在する場合は PATH 探索結果を採用しない。"""
        _run_linux_euryale(monkeypatch, tmp_path)
        monkeypatch.setattr(claude_common.shutil, "which", lambda _name: "/shims/uv")
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: True)

        assert setup_atk_serve_linux.run()

        launcher = tmp_path / ".local" / "bin" / "atk-serve"
        content = launcher.read_text(encoding="utf-8")
        assert str(tmp_path / ".local" / "bin" / "uv") in content
        assert "/shims/uv" not in content

    def test_falls_back_to_path_lookup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`~/.local/bin/uv` が無い場合は PATH 探索結果を埋め込む。"""
        _run_linux_euryale(monkeypatch, tmp_path)
        (tmp_path / ".local" / "bin" / "uv").unlink()
        monkeypatch.setattr(claude_common.shutil, "which", lambda _name: "/opt/uv/bin/uv")
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: True)

        assert setup_atk_serve_linux.run()

        launcher = tmp_path / ".local" / "bin" / "atk-serve"
        assert "/opt/uv/bin/uv" in launcher.read_text(encoding="utf-8")

    def test_missing_uv_skips_setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """uv を解決できない場合は False を返しランチャーも unit も書き込まない。"""
        _run_linux_euryale(monkeypatch, tmp_path)
        (tmp_path / ".local" / "bin" / "uv").unlink()
        monkeypatch.setattr(claude_common.shutil, "which", lambda _name: None)
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: pytest.fail(str(kwargs)))

        with caplog.at_level("INFO", logger=setup_atk_serve_linux.logger.name):
            result = setup_atk_serve_linux.run()

        assert result is False
        assert not (tmp_path / ".local" / "bin" / "atk-serve").exists()
        assert any("uvが見つからない" in record.message for record in caplog.records)


class TestRunLauncherDeployment:
    """ランチャー配置と共通 systemd 処理への委譲。"""

    @pytest.fixture(name="prepared")
    def _prepared(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
        """euryale + uv 配置済みの状態を共通セットアップする。"""
        _run_linux_euryale(monkeypatch, tmp_path)
        return tmp_path

    @pytest.mark.parametrize("initial", [None, "stale launcher\n"])
    def test_writes_executable_launcher_before_unit(
        self,
        prepared: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        initial: str | None,
    ) -> None:
        """ランチャーを新設又は更新し、実行可能化した後に unit を設定する。"""
        launcher = prepared / ".local" / "bin" / "atk-serve"
        if initial is not None:
            launcher.write_text(initial, encoding="utf-8")
            launcher.chmod(0o600)
        events: list[str] = []
        expected = setup_atk_serve_linux._LAUNCHER_TEMPLATE.format(uv=prepared / ".local" / "bin" / "uv")

        def write(path: pathlib.Path, content: str, *, mode: int, tag: str) -> None:
            del tag
            events.append("write")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            path.chmod(mode)

        def setup(**kwargs: typing.Any) -> bool:
            events.append("unit")
            assert kwargs["executable_path"] == launcher
            assert launcher.stat().st_mode & stat.S_IXUSR
            assert kwargs["unit_content"] == setup_atk_serve_linux._UNIT_CONTENT
            assert kwargs["service_name"] == "atk-serve.service"
            return True

        monkeypatch.setattr(claude_common, "atomic_write_text", write)
        monkeypatch.setattr(systemd_user_unit, "setup", setup)

        assert setup_atk_serve_linux.run()
        assert launcher.read_text(encoding="utf-8") == expected
        assert events == ["write", "unit"]

    def test_identical_launcher_skips_write(
        self,
        prepared: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """内容が一致するランチャーは書き直さず unit 設定へ進む。"""
        launcher = prepared / ".local" / "bin" / "atk-serve"
        launcher.write_text(
            setup_atk_serve_linux._LAUNCHER_TEMPLATE.format(uv=prepared / ".local" / "bin" / "uv"),
            encoding="utf-8",
        )
        launcher.chmod(0o755)

        def unexpected_write(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise AssertionError("一致するランチャーを書き直した")

        monkeypatch.setattr(claude_common, "atomic_write_text", unexpected_write)
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: True)
        assert setup_atk_serve_linux.run()

    def test_launcher_invokes_uv_without_plugin_wrapper(
        self,
        prepared: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ランチャーが uv を直接起動し、PATH 依存の bin/atk を経由しないこと。"""
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: True)
        assert setup_atk_serve_linux.run()

        content = (prepared / ".local" / "bin" / "atk-serve").read_text(encoding="utf-8")
        assert "run --no-project --script" in content
        assert "scripts/atk.py" in content
        assert "bin/atk" not in content
        assert "exec uv " not in content


def test_run_propagates_setup_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """常駐確認の失敗を post_apply へ伝播させる。"""
    _run_linux_euryale(monkeypatch, tmp_path)

    def fail(**kwargs: object) -> bool:
        del kwargs
        raise systemd_user_unit.SetupError("not running")

    monkeypatch.setattr(systemd_user_unit, "setup", fail)
    with pytest.raises(systemd_user_unit.SetupError):
        setup_atk_serve_linux.run()


class TestLegacyPlansViewerUnit:
    """旧計画ビューアーのsystemd unitの停止・無効化と削除。"""

    @staticmethod
    def _place_legacy_unit(tmp_path: pathlib.Path) -> pathlib.Path:
        """旧unitファイルを配置し、そのパスを返す。"""
        path = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Unit]\n", encoding="utf-8")
        return path

    @staticmethod
    def _record_subprocess(
        monkeypatch: pytest.MonkeyPatch,
        returncode: int,
    ) -> list[list[str]]:
        """`systemctl`呼び出しを記録し、指定した終了コードを返すよう差し替える。"""
        calls: list[list[str]] = []

        def run_subprocess(cmd: list[str], **kwargs: typing.Any) -> typing.Any:
            del kwargs
            calls.append(cmd)
            if cmd[2] == "daemon-reload":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, returncode, "", "")

        monkeypatch.setattr(claude_common, "run_subprocess", run_subprocess)
        return calls

    def test_disables_legacy_unit_before_removing_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """停止と無効化に成功した場合だけunitファイルを削除し、設定を再読み込みする。"""
        _run_linux_euryale(monkeypatch, tmp_path)
        legacy = self._place_legacy_unit(tmp_path)
        calls = self._record_subprocess(monkeypatch, returncode=0)
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: True)

        assert setup_atk_serve_linux.run() is True

        assert calls[0] == ["systemctl", "--user", "disable", "--now", "claude-plans-viewer.service"]
        assert ["systemctl", "--user", "daemon-reload"] in calls
        assert not legacy.exists()

    def test_keeps_legacy_unit_when_disable_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """停止と無効化に失敗した場合はunitファイルを残し、後続の配置を止めない。"""
        _run_linux_euryale(monkeypatch, tmp_path)
        legacy = self._place_legacy_unit(tmp_path)
        calls = self._record_subprocess(monkeypatch, returncode=1)
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: True)

        with caplog.at_level("WARNING"):
            assert setup_atk_serve_linux.run() is True

        assert legacy.exists()
        assert ["systemctl", "--user", "daemon-reload"] not in calls
        assert "claude-plans-viewer.service" in caplog.text

    def test_absent_legacy_unit_does_not_invoke_systemctl(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """旧unitが存在しない環境では移行処理を行わず、後続の配置を止めない。"""
        _run_linux_euryale(monkeypatch, tmp_path)
        calls = self._record_subprocess(monkeypatch, returncode=1)
        monkeypatch.setattr(systemd_user_unit, "setup", lambda **kwargs: True)

        assert setup_atk_serve_linux.run() is True

        assert not calls
