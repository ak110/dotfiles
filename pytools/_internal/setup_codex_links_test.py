"""pytools._internal.setup_codex_links のテスト。"""

import logging
import sys
from pathlib import Path

import pytest

from pytools._internal import claude_common, setup_codex_links


@pytest.fixture(name="env")
def env_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """`find_dotfiles_root`と`CODEX_HOME`を一時ディレクトリ配下へ振り向ける。"""
    dotfiles_root = tmp_path / "dotfiles"
    codex_home = tmp_path / "home" / ".codex"
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: dotfiles_root)
    monkeypatch.setattr(setup_codex_links, "CODEX_HOME", codex_home)
    return dotfiles_root, codex_home


def _set_single_link(monkeypatch: pytest.MonkeyPatch, dest_rel: str, src_rel: str) -> None:
    monkeypatch.setattr(setup_codex_links, "_LINKS", {dest_rel: src_rel})


def test_creates_symlink_when_missing(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配布先未作成時はシンボリックリンクが新規作成され`True`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", "agent-toolkit/skills/foo")
    src = dotfiles_root / "agent-toolkit" / "skills" / "foo"
    src.mkdir(parents=True)

    assert setup_codex_links.run() is True

    dest = codex_home / "skills" / "foo"
    assert dest.is_symlink()
    assert dest.resolve() == src.resolve()


def test_no_op_when_link_already_correct(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既存リンクが期待ターゲットと一致するなら何もせず`False`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", "agent-toolkit/skills/foo")
    src = dotfiles_root / "agent-toolkit" / "skills" / "foo"
    src.mkdir(parents=True)
    dest = codex_home / "skills" / "foo"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(src, target_is_directory=True)

    assert setup_codex_links.run() is False
    assert dest.is_symlink()


def test_recreates_link_when_target_mismatched(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既存リンクが別ターゲットを指す場合は再生成し`True`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", "agent-toolkit/skills/foo")
    src = dotfiles_root / "agent-toolkit" / "skills" / "foo"
    src.mkdir(parents=True)
    other = dotfiles_root / "other"
    other.mkdir()
    dest = codex_home / "skills" / "foo"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(other, target_is_directory=True)

    assert setup_codex_links.run() is True

    assert dest.is_symlink()
    assert dest.resolve() == src.resolve()


def test_recreates_link_when_dangling(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既存リンクがリンク切れ（ターゲット不在）なら再生成し`True`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", "agent-toolkit/skills/foo")
    src = dotfiles_root / "agent-toolkit" / "skills" / "foo"
    src.mkdir(parents=True)
    dest = codex_home / "skills" / "foo"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(dotfiles_root / "missing", target_is_directory=True)
    assert not dest.exists()
    assert dest.is_symlink()

    assert setup_codex_links.run() is True

    assert dest.is_symlink()
    assert dest.resolve() == src.resolve()


def test_skips_when_regular_directory_exists(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """配布先に通常ディレクトリが存在するなら警告ログ・スキップ・該当件0なら`False`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", "agent-toolkit/skills/foo")
    (dotfiles_root / "agent-toolkit" / "skills" / "foo").mkdir(parents=True)
    dest = codex_home / "skills" / "foo"
    dest.mkdir(parents=True)

    with caplog.at_level(logging.WARNING):
        assert setup_codex_links.run() is False

    assert any("通常ファイル" in record.message for record in caplog.records)
    assert not dest.is_symlink()


def test_skips_when_src_missing(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """配布元が未存在なら警告ログ・該当件スキップ。"""
    _, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", "agent-toolkit/skills/foo")

    with caplog.at_level(logging.WARNING):
        assert setup_codex_links.run() is False

    assert any("配布元が存在しない" in record.message for record in caplog.records)
    assert not (codex_home / "skills" / "foo").exists()


def test_returns_false_when_dotfiles_root_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`find_dotfiles_root()`が`None`を返すなら何もせず`False`返却。"""
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: None)
    monkeypatch.setattr(setup_codex_links, "CODEX_HOME", tmp_path / ".codex")

    assert setup_codex_links.run() is False


def test_links_contains_process_feedback() -> None:
    """`_LINKS`辞書に`skills/process-feedback`エントリが含まれること。"""
    # 配布マップ定数の中身を直接確認するためアンダースコアプレフィックス属性へアクセスする。
    # pylint: disable=protected-access
    assert "skills/process-feedback" in setup_codex_links._LINKS
    assert setup_codex_links._LINKS["skills/process-feedback"] == ".chezmoi-source/dot_claude/skills/process-feedback"


def test_links_contains_add_feedback() -> None:
    """`_LINKS`辞書に`skills/add-feedback`エントリが含まれること。"""
    # 配布マップ定数の中身を直接確認するためアンダースコアプレフィックス属性へアクセスする。
    # pylint: disable=protected-access
    assert "skills/add-feedback" in setup_codex_links._LINKS
    assert setup_codex_links._LINKS["skills/add-feedback"] == ".chezmoi-source/dot_claude/skills/add-feedback"


def test_windows_creates_junction(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows相当環境では`_winapi.CreateJunction`が期待引数で呼ばれ`True`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", "agent-toolkit/skills/foo")
    src = dotfiles_root / "agent-toolkit" / "skills" / "foo"
    src.mkdir(parents=True)

    monkeypatch.setattr(setup_codex_links.sys, "platform", "win32")
    fake_winapi = type(sys)("_winapi_fake")
    calls: list[tuple[str, str]] = []

    def fake_create_junction(source: str, destination: str) -> None:
        calls.append((source, destination))
        # CreateJunctionは実体作成だがテストでは空ディレクトリで代替する。
        Path(destination).mkdir(parents=True, exist_ok=True)

    fake_winapi.CreateJunction = fake_create_junction  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "_winapi", fake_winapi)

    assert setup_codex_links.run() is True

    dest = codex_home / "skills" / "foo"
    assert calls == [(str(src), str(dest))]
