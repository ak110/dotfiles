"""pytools._internal.restart_plans_viewer_linux のテスト。"""

import subprocess
import sys

import pytest

from pytools._internal import restart_plans_viewer_linux


def _fake_run_factory(invocations: list[list[str]], returncode: int = 0):
    """`claude_common.run_subprocess` 互換のスタブを返すヘルパー。"""

    def _fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        invocations.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout="", stderr="")

    return _fake


class TestRun:
    """`run()` のホスト名分岐と systemctl 発火動作を検証する。"""

    def test_skipped_on_non_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux 以外では即座に False を返し subprocess を呼ばない。"""
        monkeypatch.setattr(restart_plans_viewer_linux.sys, "platform", "win32")
        invocations: list[list[str]] = []
        monkeypatch.setattr(
            restart_plans_viewer_linux.claude_common,
            "run_subprocess",
            _fake_run_factory(invocations),
        )
        assert restart_plans_viewer_linux.run() is False
        assert not invocations

    def test_skipped_on_other_hostname(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """euryale 以外のホスト名では何もしない。"""
        monkeypatch.setattr(restart_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(restart_plans_viewer_linux.socket, "gethostname", lambda: "circe.example")
        invocations: list[list[str]] = []
        monkeypatch.setattr(
            restart_plans_viewer_linux.claude_common,
            "run_subprocess",
            _fake_run_factory(invocations),
        )
        assert restart_plans_viewer_linux.run() is False
        assert not invocations

    def test_restart_invoked_on_euryale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """euryale では `systemctl --user restart` がちょうど一度発火する。"""
        monkeypatch.setattr(restart_plans_viewer_linux.sys, "platform", "linux")
        # ホスト名は大文字小文字混在でも先頭ラベル化と lower() で正規化される。
        monkeypatch.setattr(restart_plans_viewer_linux.socket, "gethostname", lambda: "EURYALE")
        invocations: list[list[str]] = []
        monkeypatch.setattr(
            restart_plans_viewer_linux.claude_common,
            "run_subprocess",
            _fake_run_factory(invocations),
        )
        assert restart_plans_viewer_linux.run() is True
        assert invocations == [["systemctl", "--user", "restart", "claude-plans-viewer.service"]]

    def test_restart_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """systemctl が非ゼロ終了した場合は False を返す。"""
        monkeypatch.setattr(restart_plans_viewer_linux.sys, "platform", "linux")
        monkeypatch.setattr(restart_plans_viewer_linux.socket, "gethostname", lambda: "euryale")
        invocations: list[list[str]] = []
        monkeypatch.setattr(
            restart_plans_viewer_linux.claude_common,
            "run_subprocess",
            _fake_run_factory(invocations, returncode=1),
        )
        assert restart_plans_viewer_linux.run() is False


# Linux 以外の host 上で sys を直接書き換えるテストの再現性を担保するため、
# プラットフォームに依存しない最小確認も合わせて行う。
def test_module_attribute_exposes_target_hostname() -> None:
    """ホスト名定数は euryale 固定。"""
    # 内部定数だがモジュール契約として 1 ホスト固定であることを明示する。
    assert restart_plans_viewer_linux._TARGET_HOSTNAME == "euryale"  # pylint: disable=protected-access


# `sys.platform` の読み取り側がモジュール属性参照と直接 import の両方で
# 一貫しているか確認する。
def test_sys_platform_attribute_used() -> None:
    assert hasattr(restart_plans_viewer_linux, "sys")
    assert restart_plans_viewer_linux.sys is sys
