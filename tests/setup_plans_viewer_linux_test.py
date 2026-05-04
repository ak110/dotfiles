"""pytools._internal.setup_plans_viewer_linux のテスト。

各分岐（非Linux・euryale以外・viewer不在・初回enable・再実行no-op・
inactive復帰・unit変化時restart・linger無効警告）を検証する。
"""

# `_ensure_unit_file` など内部実装を経由するテストのため protected-access を許可する。
# pylint: disable=protected-access

import subprocess
from pathlib import Path

import pytest

from pytools._internal import setup_plans_viewer_linux
from pytools._internal.setup_plans_viewer_linux import _UNIT_CONTENT

# ---------------------------------------------------------------------------
# テスト補助
# ---------------------------------------------------------------------------


def _make_subprocess_fake(
    responses: dict[str, subprocess.CompletedProcess[str]],
    calls: list[list[str]],
):
    """run_subprocess の stub を返す。

    calls に呼び出し履歴を記録し、responses の先頭キーが cmd 先頭要素と前方一致する
    エントリの戻り値を返す。一致しない場合は returncode=0 の空 CompletedProcess を返す。
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


# ---------------------------------------------------------------------------
# テスト本体
# ---------------------------------------------------------------------------


class TestRunPlatformGuard:
    """非Linuxおよびeuryale以外のホストでのno-op動作。"""

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
        unit = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        assert not unit.exists()
        assert any("実行ファイルが未配置" in record.message for record in caplog.records)


class TestRunInitialEnable:
    """初回セットアップで unit 配置 + enable --now が発火すること。"""

    def test_first_time_enable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """unit 未存在 + is-enabled=disabled → unit 書き込み + daemon-reload + enable --now。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(setup_plans_viewer_linux.socket, "gethostname", lambda: "euryale")
        monkeypatch.setattr(setup_plans_viewer_linux.pathlib.Path, "home", lambda: tmp_path)

        # viewer 実行ファイルを配置
        viewer = tmp_path / ".local" / "bin" / "claude-plans-viewer"
        viewer.parent.mkdir(parents=True, exist_ok=True)
        viewer.touch()

        # is-enabled=disabled、linger=yes
        calls: list[list[str]] = []
        responses = {
            "is-enabled": _ok(stdout="disabled\n"),
            "Linger": _ok(stdout="Linger=yes\n"),
        }
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake(responses, calls),
        )

        result = setup_plans_viewer_linux.run()

        assert result is True
        unit = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        assert unit.is_file()
        assert unit.read_text(encoding="utf-8") == _UNIT_CONTENT

        cmd_strings = [" ".join(c) for c in calls]
        assert any("daemon-reload" in s for s in cmd_strings)
        assert any("enable" in s and "--now" in s for s in cmd_strings)
        assert not any("restart" in s for s in cmd_strings)


class TestRunNoOp:
    """再実行時に unit と systemd 状態が変わらない場合の no-op。"""

    def test_idempotent_noop(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """unit 既存（同一内容）+ is-enabled=enabled + is-active ok → write/enable/start/restart なし。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(setup_plans_viewer_linux.socket, "gethostname", lambda: "euryale")
        monkeypatch.setattr(setup_plans_viewer_linux.pathlib.Path, "home", lambda: tmp_path)

        # viewer と unit を事前配置（同一内容）
        viewer = tmp_path / ".local" / "bin" / "claude-plans-viewer"
        viewer.parent.mkdir(parents=True, exist_ok=True)
        viewer.touch()
        unit = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_UNIT_CONTENT, encoding="utf-8")

        # is-enabled=enabled、is-active=0（active）
        calls: list[list[str]] = []
        responses = {
            "is-enabled": _ok(stdout="enabled\n"),
            "is-active": _ok(returncode=0),
            "Linger": _ok(stdout="Linger=yes\n"),
        }
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake(responses, calls),
        )

        result = setup_plans_viewer_linux.run()

        assert result is False
        cmd_strings = [" ".join(c) for c in calls]
        assert not any("daemon-reload" in s for s in cmd_strings)
        assert not any("enable --now" in s for s in cmd_strings)
        assert not any(" start " in s for s in cmd_strings)
        assert not any("restart" in s for s in cmd_strings)
        # is-enabled と is-active の確認は発火する
        assert any("is-enabled" in s for s in cmd_strings)
        assert any("is-active" in s for s in cmd_strings)


class TestRunInactiveRestart:
    """enabled だが inactive のとき start が発火すること。"""

    def test_start_when_inactive(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """is-enabled=enabled + is-active returncode=3 → start 発火。"""
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
        responses = {
            "is-enabled": _ok(stdout="enabled\n"),
            "is-active": _ok(returncode=3),
            "Linger": _ok(stdout="Linger=yes\n"),
        }
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake(responses, calls),
        )

        setup_plans_viewer_linux.run()

        cmd_strings = [" ".join(c) for c in calls]
        assert any("start" in s and "restart" not in s for s in cmd_strings)
        assert not any("restart" in s for s in cmd_strings)


class TestRunUnitChanged:
    """unit 内容が変化したとき restart が発火すること。"""

    def test_restart_when_unit_changed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """既存 unit 内容が旧内容 → 新 unit 書き込み + daemon-reload + restart（enable --now でなく）。"""
        monkeypatch.setattr(setup_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(setup_plans_viewer_linux.socket, "gethostname", lambda: "euryale")
        monkeypatch.setattr(setup_plans_viewer_linux.pathlib.Path, "home", lambda: tmp_path)

        viewer = tmp_path / ".local" / "bin" / "claude-plans-viewer"
        viewer.parent.mkdir(parents=True, exist_ok=True)
        viewer.touch()
        # 旧内容を書き込んで unit_changed=True を誘発させる
        unit = tmp_path / ".config" / "systemd" / "user" / "claude-plans-viewer.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("old content\n", encoding="utf-8")

        calls: list[list[str]] = []
        responses = {
            "is-enabled": _ok(stdout="enabled\n"),
            "is-active": _ok(returncode=0),
            "Linger": _ok(stdout="Linger=yes\n"),
        }
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake(responses, calls),
        )

        result = setup_plans_viewer_linux.run()

        assert result is True
        cmd_strings = [" ".join(c) for c in calls]
        assert any("daemon-reload" in s for s in cmd_strings)
        assert any("restart" in s for s in cmd_strings)
        assert not any("enable" in s and "--now" in s for s in cmd_strings)


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
        responses = {
            "is-enabled": _ok(stdout="enabled\n"),
            "is-active": _ok(returncode=0),
            "Linger": _ok(stdout="Linger=no\n"),
        }
        monkeypatch.setattr(
            setup_plans_viewer_linux.claude_common,
            "run_subprocess",
            _make_subprocess_fake(responses, calls),
        )

        with caplog.at_level("INFO", logger=setup_plans_viewer_linux.logger.name):
            setup_plans_viewer_linux.run()

        assert any("linger 無効" in record.message for record in caplog.records)
