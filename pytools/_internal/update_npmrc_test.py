"""pytools._internal.update_npmrc のテスト。"""

import logging
import subprocess
from pathlib import Path

import pytest

from pytools._internal import update_npmrc
from pytools._internal.update_npmrc import run


@pytest.fixture(name="pnpm_calls", autouse=True)
def _pnpm_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """既定ではpnpmを未検出とし、開発者の実環境のpnpm設定へ書き込まないようにする。"""
    monkeypatch.setattr(update_npmrc.shutil, "which", _which_none)
    return []


def _which_none(name: str, **kwargs: object) -> None:
    del name, kwargs


def _install_pnpm(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], current: str) -> None:
    """pnpmが検出され、`config get`が`current`を返す状態にする。"""

    def which(name: str, **kwargs: object) -> str | None:
        del kwargs
        return "/opt/pnpm/bin/pnpm" if name == "pnpm" else None

    def run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(cmd)
        stdout = f"{current}\n" if cmd[1:3] == ["config", "get"] else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(update_npmrc.shutil, "which", which)
    monkeypatch.setattr(update_npmrc.claude_common, "run_subprocess", run_subprocess)


class TestUpdateNpmrc:
    """~/.npmrc へのnpmの公開待機設定の反映テスト。"""

    def test_new_file_is_created(self, tmp_path: Path):
        """対象ファイルが存在しない場合、新規作成してnpmのキーを書き込む。"""
        path = tmp_path / ".npmrc"
        assert run(path) is True
        assert path.read_text(encoding="utf-8") == "min-release-age=1\n"

    def test_legacy_pnpm_key_is_replaced_by_npm_key(self, tmp_path: Path):
        """以前書いていたpnpmのキーを除き、npmのキーを追記して他の行は保持する。"""
        path = tmp_path / ".npmrc"
        path.write_text("registry=https://example.com/\nminimum-release-age=1440\n", encoding="utf-8")
        assert run(path) is True
        assert path.read_text(encoding="utf-8") == "registry=https://example.com/\nmin-release-age=1\n"

    def test_existing_npm_value_is_replaced(self, tmp_path: Path):
        """npmのキーが異なる値を持つ場合は値を置換する。"""
        path = tmp_path / ".npmrc"
        path.write_text("min-release-age=7\nregistry=https://example.com/\n", encoding="utf-8")
        assert run(path) is True
        assert path.read_text(encoding="utf-8") == "min-release-age=1\nregistry=https://example.com/\n"

    def test_matching_line_is_noop(self, tmp_path: Path):
        """既に設定済みでpnpmが無ければファイルを書き換えず、変更なしを返す。"""
        path = tmp_path / ".npmrc"
        path.write_text("min-release-age=1\n", encoding="utf-8")
        mtime_before = path.stat().st_mtime_ns
        assert run(path) is False
        assert path.stat().st_mtime_ns == mtime_before

    def test_create_log_names_file_as_object(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """新規作成時のログは、作成したファイルを目的語にした1文になる。"""
        path = tmp_path / ".npmrc"
        with caplog.at_level(logging.INFO, logger=update_npmrc.__name__):
            run(path)
        assert f"    {path} を作成し min-release-age=1 を設定しました" in caplog.messages

    def test_update_log_keeps_status_format(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """既存ファイルの更新時のログは`<対象>: <状態>`の形のままで、作成の語を含まない。"""
        path = tmp_path / ".npmrc"
        path.write_text("min-release-age=7\n", encoding="utf-8")
        with caplog.at_level(logging.INFO, logger=update_npmrc.__name__):
            run(path)
        assert f"    {path}: min-release-age=1 を設定しました" in caplog.messages
        assert not any("作成" in message for message in caplog.messages)

    def test_missing_trailing_newline(self, tmp_path: Path):
        """既存ファイルの末尾改行が無くても正しく追記される。"""
        path = tmp_path / ".npmrc"
        path.write_text("registry=https://example.com/", encoding="utf-8")
        assert run(path) is True
        assert path.read_text(encoding="utf-8").splitlines() == ["registry=https://example.com/", "min-release-age=1"]


class TestUpdatePnpmGlobal:
    """pnpmのグローバル設定への公開待機の反映テスト。"""

    def test_pnpm_unset_value_is_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pnpm_calls: list[list[str]]):
        """pnpmの現在値が未設定なら設定し、`~/.npmrc`が設定済みでも変更ありを返す。"""
        path = tmp_path / ".npmrc"
        path.write_text("min-release-age=1\n", encoding="utf-8")
        _install_pnpm(monkeypatch, pnpm_calls, "undefined")
        assert run(path) is True
        assert pnpm_calls[-1] == ["/opt/pnpm/bin/pnpm", "config", "set", "--location", "global", "minimum-release-age", "1440"]

    def test_pnpm_configured_value_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pnpm_calls: list[list[str]]):
        """pnpmの現在値が既に1440なら設定を呼ばず、変更なしを返す。"""
        path = tmp_path / ".npmrc"
        path.write_text("min-release-age=1\n", encoding="utf-8")
        _install_pnpm(monkeypatch, pnpm_calls, "1440")
        assert run(path) is False
        assert [cmd[1:3] for cmd in pnpm_calls] == [["config", "get"]]
