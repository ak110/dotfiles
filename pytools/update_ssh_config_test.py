"""update_ssh_configモジュールのテスト。"""

import sys
from pathlib import Path

import pytest

from pytools import update_ssh_config
from pytools._internal import claude_common as _claude_common


@pytest.mark.parametrize(("argv", "exit_code"), [(["--help"], 0), (["--unknown"], 2)])
def test_main_rejects_nondefault_arguments_before_ssh_update(
    argv: list[str], exit_code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """確認引数と未知引数ではSSHファイルを更新しない。"""
    config = tmp_path / "config"
    config.write_text("before\n", encoding="utf-8")

    def run() -> bool:
        config.write_text("after\n", encoding="utf-8")
        return True

    monkeypatch.setattr(update_ssh_config, "run", run)
    with pytest.raises(SystemExit) as exc_info:
        update_ssh_config.main(argv)

    assert exc_info.value.code == exit_code
    assert config.read_text(encoding="utf-8") == "before\n"
    captured = capsys.readouterr()
    if exit_code == 0:
        assert "usage:" in captured.out
        assert captured.err == ""
    else:
        assert "usage:" in captured.err
        assert "unrecognized arguments" in captured.err


def test_main_without_arguments_updates_ssh_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """引数なしの公開入口は既存の更新処理を実行する。"""
    called = False

    def run() -> bool:
        nonlocal called
        called = True
        return False

    monkeypatch.setattr(update_ssh_config, "run", run)
    monkeypatch.setattr(update_ssh_config.sys, "argv", ["update-ssh-config"])
    with pytest.raises(SystemExit) as exc_info:
        update_ssh_config.main()

    assert exc_info.value.code == 0
    assert called


def _run_public_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(SystemExit) as result:
        update_ssh_config.main([])
    assert result.value.code == 0
    return ssh_dir


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData12345678901234567890 user@host",
            True,
        ),
        ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC1234567890abcdef user@host", True),
        (
            'command="/usr/bin/false" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData12345678901234567890 user@host',
            True,
        ),
        ("# This is a comment", False),
        ("", False),
        ("   ", False),
        ("ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTZAAAAIbmlzdHAyNTY= user@host", True),
    ],
)
def test_public_update_adds_only_key_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, expected: bool) -> None:
    """公開コマンドは有効な鍵行だけをauthorized_keysへ追加する。"""
    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "authorized_keys").write_text(line, encoding="utf-8")

    _run_public_update(tmp_path, monkeypatch)

    target = ssh_dir / "authorized_keys"
    assert (target.read_text(encoding="utf-8") if target.exists() else "") == (f"{line}\n" if expected else "")


@pytest.mark.parametrize("content", ["text", "text\n", "", "text\n\n"])
def test_public_update_preserves_config_newline_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    """公開コマンドはconf.d本文を末尾改行付きで結合する。"""
    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "example.conf").write_text(content, encoding="utf-8")

    _run_public_update(tmp_path, monkeypatch)

    assert (ssh_dir / "config").read_text(encoding="utf-8") == (content if content.endswith("\n") else content + "\n")


class TestAtomicWriteText:
    """claude_common.atomic_write_text のテスト (update_ssh_config で使う機能を中心に確認)。"""

    def test_creates_file(self, tmp_path):
        target = tmp_path / "test_file"
        assert _claude_common.atomic_write_text(target, "hello\n") is True
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_mode_sets_permissions(self, tmp_path):
        target = tmp_path / "test_file"
        assert _claude_common.atomic_write_text(target, "data\n", mode=0o600) is True
        if sys.platform != "win32":
            assert oct(target.stat().st_mode & 0o777) == oct(0o600)

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "test_file"
        target.write_text("old content", encoding="utf-8")
        assert _claude_common.atomic_write_text(target, "new content\n") is True
        assert target.read_text(encoding="utf-8") == "new content\n"

    def test_no_leftover_on_success(self, tmp_path):
        target = tmp_path / "test_file"
        _claude_common.atomic_write_text(target, "content\n")
        # 一時ファイルが残っていないことを確認
        files = list(tmp_path.iterdir())
        assert files == [target]


class TestGenerateAuthorizedKeys:
    """authorized_keys生成の結合テスト。"""

    def test_merges_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        conf_d = ssh_dir / "conf.d"
        conf_d.mkdir()
        # 既存のauthorized_keys
        (ssh_dir / "authorized_keys").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExistingKey1234567890abcdefghijklmn existing@host\n",
            encoding="utf-8",
        )
        # conf.d/authorized_keysに新しい鍵を追加
        (conf_d / "authorized_keys").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExistingKey1234567890abcdefghijklmn existing@host\n"
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINewKeyData9876543210zyxwvutsrqpon new@host\n",
            encoding="utf-8",
        )
        _run_public_update(tmp_path, monkeypatch)
        content = (ssh_dir / "authorized_keys").read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line.strip()]
        # 既存1鍵 + 新規1鍵 = 2鍵
        assert len(lines) == 2
        assert "existing@host" in lines[0]
        assert "new@host" in lines[1]
