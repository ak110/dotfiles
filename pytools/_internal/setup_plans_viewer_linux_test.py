"""pytools._internal.setup_plans_viewer_linux のテスト。

各分岐 (非 Linux・euryale 以外・viewer 不在・unit 新規・unit 既存・unit 変化・linger 無効警告) を検証する。
"""

# `_ensure_unit_file` など内部実装定数を直接参照するため protected-access を許可する。
# pylint: disable=protected-access

import subprocess
from pathlib import Path

import pytest

from pytools._internal import setup_plans_viewer_linux
from pytools._internal.setup_plans_viewer_linux import _UNIT_CONTENT


def _make_subprocess_fake(
    responses: dict[str, subprocess.CompletedProcess[str]],
    calls: list[list[str]],
):
    """run_subprocess の stub を返す。

    calls に呼び出し履歴を記録し、responses のキーが cmd 文字列に部分一致するエントリの戻り値を返す。
    一致しない場合は returncode=0 の空 CompletedProcess を返す。
    """

    def fake(
        cmd: list[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        tag: str | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        del timeout, cwd, tag
        calls.append(list(cmd))
        cmd_key = " ".join(cmd)
        for pattern, result in responses.items():
            if pattern in cmd_key:
                return result
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return fake


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode=returncode, stdout=stdout, stderr="")


def test_unit_excludes_host_specific_args():
    """ユニット本文にホスト固有の引数（待受アドレス・リモートホスト）と外部 IP を直書きしないこと。

    待受アドレス・リモートホストは `~/.config/pytools/claude-plans-viewer.toml`
    経由で与える設計のため、unit 側には `--host=` も `--remote-host=` も含まない。
    """
    assert "192.168." not in _UNIT_CONTENT
    assert "--host=" not in _UNIT_CONTENT
    assert "--remote-host=" not in _UNIT_CONTENT


class TestRunPlatformGuard:
    """非 Linux および euryale 以外のホストでの no-op 動作。"""

    def test_non_linux_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """sys.platform が linux でなければ False を返し副作用ゼロ。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "win32")
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake({}, calls),
        )
        assert setup_plans_viewer_linux.run() is False
        assert not calls

    def test_non_euryale_hostname_returns_false(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """hostname が euryale 以外なら False を返し unit ファイルを書き込まない。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(setup_plans_viewer_linux.socket, "gethostname", lambda: "circe.local")
        monkeypatch.setattr(setup_plans_viewer_linux.pathlib.Path, "home", lambda: tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake({}, calls),
        )

        assert setup_plans_viewer_linux.run() is False
        assert not calls

        unit = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        assert not unit.exists()


class TestRunViewerMissing:
    """viewer 実行ファイルが未配置の場合のスキップ動作。"""

    def test_viewer_missing_returns_false_no_unit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        """euryale だが viewer が無い場合、False かつ unit ファイルを書き込まない。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(setup_plans_viewer_linux.socket, "gethostname", lambda: "euryale")
        monkeypatch.setattr(setup_plans_viewer_linux.pathlib.Path, "home", lambda: tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake({}, calls),
        )

        with caplog.at_level("INFO", logger=setup_plans_viewer_linux.logger.name):
            result = setup_plans_viewer_linux.run()

        assert result is False
        assert not calls
        unit = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        assert not unit.exists()
        assert any("実行ファイルが未配置" in record.message for record in caplog.records)


class TestRunUnitDeployment:
    """unit 配置と systemctl 連携の主要分岐を検証する。"""

    @pytest.fixture(name="prepared")
    def _prepared(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        """euryale + viewer 配置済みの状態を共通セットアップする。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(setup_plans_viewer_linux.socket, "gethostname", lambda: "EURYALE")
        monkeypatch.setattr(setup_plans_viewer_linux.pathlib.Path, "home", lambda: tmp_path)
        viewer = tmp_path / ".local" / "bin" / "claude-plans-viewer"
        viewer.parent.mkdir(parents=True, exist_ok=True)
        viewer.touch()
        return tmp_path

    def test_first_time_writes_unit_and_fires_full_sequence(
        self,
        prepared: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """unit 未配置 → 書き込み + daemon-reload + enable + restart が順に発火する。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake({"Linger": _ok(stdout="Linger=yes\n")}, calls),
        )

        result = setup_plans_viewer_linux.run()

        assert result is True
        unit = prepared / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        assert unit.is_file()
        assert unit.read_text(encoding="utf-8") == _UNIT_CONTENT

        cmd_strings = [" ".join(c) for c in calls]
        assert any("daemon-reload" in s for s in cmd_strings)
        assert any(s.endswith("enable claude-plans-viewer.service") for s in cmd_strings)
        assert any(s.endswith("restart claude-plans-viewer.service") for s in cmd_strings)

    def test_idempotent_skips_daemon_reload_but_still_restarts(
        self,
        prepared: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """unit 既存（同一内容）→ daemon-reload は呼ばないが enable + restart は毎回発火する。"""
        unit = prepared / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_UNIT_CONTENT, encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake({"Linger": _ok(stdout="Linger=yes\n")}, calls),
        )

        result = setup_plans_viewer_linux.run()

        assert result is True
        cmd_strings = [" ".join(c) for c in calls]
        assert not any("daemon-reload" in s for s in cmd_strings)
        assert any(s.endswith("enable claude-plans-viewer.service") for s in cmd_strings)
        assert any(s.endswith("restart claude-plans-viewer.service") for s in cmd_strings)

    def test_unit_changed_triggers_daemon_reload(
        self,
        prepared: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """unit 既存（旧内容）→ 書き込み + daemon-reload + restart。"""
        unit = prepared / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("old content\n", encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake({"Linger": _ok(stdout="Linger=yes\n")}, calls),
        )

        result = setup_plans_viewer_linux.run()

        assert result is True
        assert unit.read_text(encoding="utf-8") == _UNIT_CONTENT
        cmd_strings = [" ".join(c) for c in calls]
        assert any("daemon-reload" in s for s in cmd_strings)
        assert any(s.endswith("restart claude-plans-viewer.service") for s in cmd_strings)


class TestLingerWarning:
    """linger 無効時に警告ログが出力されること。"""

    def test_linger_disabled_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ):
        """loginctl の stdout が Linger=no のとき linger 無効を示すログを出力する。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(setup_plans_viewer_linux.socket, "gethostname", lambda: "euryale")
        monkeypatch.setattr(setup_plans_viewer_linux.pathlib.Path, "home", lambda: tmp_path)

        viewer = tmp_path / ".local" / "bin" / "claude-plans-viewer"
        viewer.parent.mkdir(parents=True, exist_ok=True)
        viewer.touch()
        unit = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_UNIT_CONTENT, encoding="utf-8")

        calls: list[list[str]] = []
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake({"Linger": _ok(stdout="Linger=no\n")}, calls),
        )

        with caplog.at_level("INFO", logger=setup_plans_viewer_linux.logger.name):
            setup_plans_viewer_linux.run()

        assert any("linger 無効" in record.message for record in caplog.records)
