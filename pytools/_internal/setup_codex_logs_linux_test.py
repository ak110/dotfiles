"""pytools._internal.setup_codex_logs_linuxのテスト。"""

import pathlib

import pytest

from pytools._internal import setup_codex_logs_linux


def test_non_linux_has_no_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """非Linuxではディレクトリを生成せずスキップする。"""
    monkeypatch.setattr(setup_codex_logs_linux.sys, "platform", "win32")
    home_dir = tmp_path / "home"
    shm_root = tmp_path / "shm"

    assert setup_codex_logs_linux.run(home_dir=home_dir, shm_root=shm_root) is False
    assert not home_dir.exists()
    assert not shm_root.exists()


def test_moves_existing_database_and_creates_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """既存DBを保持したまま共有メモリーへ移し、元のパスをリンクへ置換する。"""
    monkeypatch.setattr(setup_codex_logs_linux.sys, "platform", "linux")
    home_dir = tmp_path / "home"
    shm_root = tmp_path / "shm"
    database = home_dir / ".codex" / "logs_2.sqlite"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"existing diagnostic logs")
    shm_root.mkdir()

    assert setup_codex_logs_linux.run(home_dir=home_dir, shm_root=shm_root) is True

    target = shm_root / f"codex-{setup_codex_logs_linux.os.getuid()}-logs_2.sqlite"
    assert database.is_symlink()
    assert database.readlink() == target
    assert target.read_bytes() == b"existing diagnostic logs"
    assert target.stat().st_mode & 0o777 == 0o600


def test_missing_database_creates_dangling_symlink_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """DB未作成時はリンクだけを作成し、同じ設定の再実行では変更しない。"""
    monkeypatch.setattr(setup_codex_logs_linux.sys, "platform", "linux")
    home_dir = tmp_path / "home"
    shm_root = tmp_path / "shm"
    shm_root.mkdir()

    assert setup_codex_logs_linux.run(home_dir=home_dir, shm_root=shm_root) is True
    assert setup_codex_logs_linux.run(home_dir=home_dir, shm_root=shm_root) is False

    database = home_dir / ".codex" / "logs_2.sqlite"
    target = shm_root / f"codex-{setup_codex_logs_linux.os.getuid()}-logs_2.sqlite"
    assert database.is_symlink()
    assert database.readlink() == target
    assert not target.exists()


def test_replaces_wrong_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """別の場所を指すリンクは期待する共有メモリー上のリンクへ修正する。"""
    monkeypatch.setattr(setup_codex_logs_linux.sys, "platform", "linux")
    home_dir = tmp_path / "home"
    shm_root = tmp_path / "shm"
    database = home_dir / ".codex" / "logs_2.sqlite"
    database.parent.mkdir(parents=True)
    database.symlink_to(tmp_path / "old.sqlite")
    shm_root.mkdir()

    assert setup_codex_logs_linux.run(home_dir=home_dir, shm_root=shm_root) is True

    target = shm_root / f"codex-{setup_codex_logs_linux.os.getuid()}-logs_2.sqlite"
    assert database.readlink() == target


def test_missing_shm_root_fails_without_replacing_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共有メモリーが無い場合は既存DBを変更せず失敗する。"""
    monkeypatch.setattr(setup_codex_logs_linux.sys, "platform", "linux")
    home_dir = tmp_path / "home"
    database = home_dir / ".codex" / "logs_2.sqlite"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"keep me")

    with pytest.raises(FileNotFoundError, match="共有メモリーディレクトリ"):
        setup_codex_logs_linux.run(home_dir=home_dir, shm_root=tmp_path / "missing")

    assert database.read_bytes() == b"keep me"
    assert not database.is_symlink()
