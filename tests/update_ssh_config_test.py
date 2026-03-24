"""update_ssh_configモジュールのテスト。"""

import sys

from pytools.update_ssh_config import _ensure_trailing_newline, _extract_key_data


class TestExtractKeyData:
    """_extract_key_dataのテスト。"""

    def test_ed25519_key(self):
        line = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData12345678901234567890 user@host"
        assert _extract_key_data(line) == "AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData12345678901234567890"

    def test_rsa_key(self):
        line = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC1234567890abcdef user@host"
        assert _extract_key_data(line) == "AAAAB3NzaC1yc2EAAAADAQABAAABgQC1234567890abcdef"

    def test_key_with_options(self):
        line = 'command="/usr/bin/false" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData12345678901234567890 user@host'
        assert _extract_key_data(line) == "AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData12345678901234567890"

    def test_comment_line(self):
        assert _extract_key_data("# This is a comment") is None

    def test_empty_line(self):
        assert _extract_key_data("") is None

    def test_whitespace_only(self):
        assert _extract_key_data("   ") is None

    def test_ecdsa_key(self):
        line = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTZAAAAIbmlzdHAyNTY= user@host"
        assert _extract_key_data(line) == "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTZAAAAIbmlzdHAyNTY="


class TestEnsureTrailingNewline:
    """_ensure_trailing_newlineのテスト。"""

    def test_without_newline(self):
        assert _ensure_trailing_newline("text") == "text\n"

    def test_with_newline(self):
        assert _ensure_trailing_newline("text\n") == "text\n"

    def test_empty_string(self):
        assert _ensure_trailing_newline("") == "\n"

    def test_multiple_newlines(self):
        assert _ensure_trailing_newline("text\n\n") == "text\n\n"


class TestAtomicWrite:
    """_atomic_writeのテスト。"""

    def test_creates_file(self, tmp_path):
        from pytools.update_ssh_config import _atomic_write

        target = tmp_path / "test_file"
        _atomic_write(target, "hello\n")
        assert target.read_text(encoding="utf-8") == "hello\n"
        if sys.platform != "win32":
            assert oct(target.stat().st_mode & 0o777) == oct(0o600)

    def test_overwrites_existing(self, tmp_path):
        from pytools.update_ssh_config import _atomic_write

        target = tmp_path / "test_file"
        target.write_text("old content", encoding="utf-8")
        _atomic_write(target, "new content\n")
        assert target.read_text(encoding="utf-8") == "new content\n"

    def test_no_leftover_on_success(self, tmp_path):
        from pytools.update_ssh_config import _atomic_write

        target = tmp_path / "test_file"
        _atomic_write(target, "content\n")
        # 一時ファイルが残っていないことを確認
        files = list(tmp_path.iterdir())
        assert files == [target]


class TestGenerateAuthorizedKeys:
    """authorized_keys生成の結合テスト。"""

    def test_merges_keys(self, tmp_path):
        from pytools.update_ssh_config import _generate_authorized_keys

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
        _generate_authorized_keys(ssh_dir)
        content = (ssh_dir / "authorized_keys").read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line.strip()]
        # 既存1鍵 + 新規1鍵 = 2鍵
        assert len(lines) == 2
        assert "existing@host" in lines[0]
        assert "new@host" in lines[1]
