import sys
from pathlib import Path

import pytest

from pytools import update_ssh_config
from pytools._internal import common as _common
from pytools._internal import post_apply_outcome


@pytest.mark.parametrize(("argv", "exit_code"), [(["--help"], 0), (["--unknown"], 2)])
def test_main_rejects_nondefault_arguments_before_ssh_update(
    argv: list[str], exit_code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = tmp_path / "config"
    config.write_text("before\n", encoding="utf-8")

    def run() -> post_apply_outcome.PostApplyOutcome:
        config.write_text("after\n", encoding="utf-8")
        return post_apply_outcome.PostApplyOutcome(changed=True)

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
    called = False

    def run() -> post_apply_outcome.PostApplyOutcome:
        nonlocal called
        called = True
        return post_apply_outcome.PostApplyOutcome()

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
    ssh_dir = tmp_path / ".ssh"
    (ssh_dir / "conf.d").mkdir(parents=True)
    (ssh_dir / "conf.d" / "example.conf").write_text(content, encoding="utf-8")

    _run_public_update(tmp_path, monkeypatch)

    assert (ssh_dir / "config").read_text(encoding="utf-8") == (content if content.endswith("\n") else content + "\n")


@pytest.mark.parametrize(
    ("fragments", "expected"),
    [((), ""), (("Host first", "", "Host last\n\n"), "Host first\n\nHost last\n\n")],
)
def test_build_ssh_config_content(fragments: tuple[str, ...], expected: str) -> None:
    assert update_ssh_config._build_ssh_config_content(fragments) == expected  # pylint: disable=protected-access


def test_merge_authorized_keys_preserves_order_and_first_occurrences() -> None:
    existing_key, first_key, last_key = ("ssh-ed25519 " + letter * 32 for letter in "ABC")
    existing = f"# keep this comment\n\n{existing_key} old"
    first = f'command="/usr/bin/false" {first_key} first'
    fallback = "fallback line"
    sources = (
        f"# ignore this comment\n\n{existing_key} duplicate\n{first}\n{fallback}\n",
        f"{first_key} duplicate\n {fallback} \n{last_key} last",
    )

    assert update_ssh_config._merge_authorized_keys(existing, sources) == (  # pylint: disable=protected-access
        f"{existing}\n{first}\n{fallback}\n{last_key} last\n",
        3,
    )


def test_merge_authorized_keys_does_not_emit_content_without_additions() -> None:
    existing = "ssh-ed25519 " + "A" * 32

    assert update_ssh_config._merge_authorized_keys(existing, (f"{existing} duplicate\n",)) == (  # pylint: disable=protected-access
        None,
        0,
    )


def test_public_update_keeps_config_backup_and_does_not_rewrite_unchanged_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssh_dir = tmp_path / ".ssh"
    conf_d = ssh_dir / "conf.d"
    conf_d.mkdir(parents=True)
    config = ssh_dir / "config"
    config.write_text("original\n", encoding="utf-8")
    (conf_d / "b.conf").write_text("Host second", encoding="utf-8")
    (conf_d / "a.conf").write_text("Host first\n", encoding="utf-8")
    (ssh_dir / "localconfig").write_text("Host local", encoding="utf-8")
    key = "ssh-ed25519 " + "A" * 32
    keys_path = ssh_dir / "authorized_keys"
    keys_path.write_text(key, encoding="utf-8")
    (conf_d / "authorized_keys").write_text(f"{key} duplicate\n", encoding="utf-8")

    _run_public_update(tmp_path, monkeypatch)

    assert config.read_text(encoding="utf-8") == "Host first\nHost second\nHost local\n"
    backup = config.with_suffix(".bk")
    assert backup.read_text(encoding="utf-8") == "original\n"
    assert keys_path.read_bytes() == key.encode("ascii")
    if sys.platform != "win32":
        assert config.stat().st_mode & 0o777 == 0o600

    (ssh_dir / "localconfig").write_text("Host updated", encoding="utf-8")
    _run_public_update(tmp_path, monkeypatch)

    assert config.read_text(encoding="utf-8") == "Host first\nHost second\nHost updated\n"
    assert backup.read_text(encoding="utf-8") == "original\n"


class TestAtomicWriteText:
    def test_creates_file(self, tmp_path):
        target = tmp_path / "test_file"
        assert _common.atomic_write_text(target, "hello\n") is True
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_mode_sets_permissions(self, tmp_path):
        target = tmp_path / "test_file"
        assert _common.atomic_write_text(target, "data\n", mode=0o600) is True
        if sys.platform != "win32":
            assert oct(target.stat().st_mode & 0o777) == oct(0o600)

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "test_file"
        target.write_text("old content", encoding="utf-8")
        assert _common.atomic_write_text(target, "new content\n") is True
        assert target.read_text(encoding="utf-8") == "new content\n"

    def test_no_leftover_on_success(self, tmp_path):
        target = tmp_path / "test_file"
        _common.atomic_write_text(target, "content\n")

        files = list(tmp_path.iterdir())
        assert files == [target]


class TestGenerateAuthorizedKeys:
    def test_merges_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        conf_d = ssh_dir / "conf.d"
        conf_d.mkdir()

        (ssh_dir / "authorized_keys").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExistingKey1234567890abcdefghijklmn existing@host\n",
            encoding="utf-8",
        )

        (conf_d / "authorized_keys").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExistingKey1234567890abcdefghijklmn existing@host\n"
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINewKeyData9876543210zyxwvutsrqpon new@host\n",
            encoding="utf-8",
        )
        _run_public_update(tmp_path, monkeypatch)
        target = ssh_dir / "authorized_keys"
        content = target.read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line.strip()]

        assert len(lines) == 2
        assert "existing@host" in lines[0]
        assert "new@host" in lines[1]
        if sys.platform != "win32":
            assert target.stat().st_mode & 0o777 == 0o600
