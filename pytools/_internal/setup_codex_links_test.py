"""pytools._internal.setup_codex_links のテスト。"""

import logging
import sys
from pathlib import Path

import pytest

from pytools._internal import claude_common, setup_codex_links, sync_agent_toolkit_rules

_TOOLKIT_PREFIX = "agent-" + "toolkit"


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
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")
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
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")
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
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")
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


def test_sync_directory_link_uses_relative_target(tmp_path: Path) -> None:
    """POSIXリンクは移動可能な相対参照先で作成する。"""
    target = tmp_path / "cache" / "2.0.0"
    target.mkdir(parents=True)
    dest = tmp_path / "cache" / "1.0.0"

    assert setup_codex_links.sync_directory_link(dest, target) is True

    assert dest.readlink() == Path("2.0.0")
    assert setup_codex_links.sync_directory_link(dest, target) is False


@pytest.mark.parametrize("entry_kind", ["file", "directory"])
def test_sync_directory_link_rejects_regular_entry(tmp_path: Path, entry_kind: str) -> None:
    """通常ファイルと通常ディレクトリは置換しない。"""
    target = tmp_path / "target"
    target.mkdir()
    dest = tmp_path / "dest"
    if entry_kind == "file":
        dest.write_text("保持", encoding="utf-8")
    else:
        dest.mkdir()

    with pytest.raises(FileExistsError):
        setup_codex_links.sync_directory_link(dest, target)

    assert not dest.is_symlink()


def test_recreates_link_when_dangling(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既存リンクがリンク切れ（ターゲット不在）なら再生成し`True`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")
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
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")
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
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")

    with caplog.at_level(logging.WARNING):
        assert setup_codex_links.run() is False

    assert any("配布元が存在しない" in record.message for record in caplog.records)
    assert not (codex_home / "skills" / "foo").exists()


@pytest.mark.parametrize("failure_side", ["source", "destination"])
def test_os_error_skips_only_affected_link(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_side: str,
) -> None:
    """配布元又は配布先の検査失敗後も、残るリンクを処理する。"""
    dotfiles_root, codex_home = env
    monkeypatch.setattr(
        setup_codex_links,
        "_LINKS",
        {"skills/failing": "sources/failing", "skills/succeeding": "sources/succeeding"},
    )
    failing_source = dotfiles_root / "sources" / "failing"
    succeeding_source = dotfiles_root / "sources" / "succeeding"
    failing_source.mkdir(parents=True)
    succeeding_source.mkdir(parents=True)
    failing_dest = codex_home / "skills" / "failing"
    real_exists = Path.exists
    real_sync = setup_codex_links.sync_directory_link

    if failure_side == "source":
        monkeypatch.setattr(
            Path,
            "exists",
            lambda path: (_ for _ in ()).throw(OSError("検査失敗")) if path == failing_source else real_exists(path),
        )
    else:
        monkeypatch.setattr(
            setup_codex_links,
            "sync_directory_link",
            lambda dest, target: (
                (_ for _ in ()).throw(OSError("検査失敗")) if dest == failing_dest else real_sync(dest, target)
            ),
        )

    with caplog.at_level(logging.WARNING):
        assert setup_codex_links.run() is True

    assert "検査失敗" in caplog.text
    assert str(failing_dest) in caplog.text
    assert (codex_home / "skills" / "succeeding").is_symlink()


def test_returns_false_when_dotfiles_root_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`find_dotfiles_root()`が`None`を返すなら何もせず`False`返却。"""
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: None)
    monkeypatch.setattr(setup_codex_links, "CODEX_HOME", tmp_path / ".codex")

    assert setup_codex_links.run() is False


def test_run_leaves_the_rules_destination_to_the_rules_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ルールの配布先はリンクの対象から外れ、同期処理が書いた本文のまま残る。

    `pytools/post_apply.py`はルールの同期の直後にリンクの同期を実行する。
    ルールがリンクの対象へ戻ると、リンクの同期が同じ配布先を扱おうとして警告を残し、
    配布経路が同期とリンクの2つへ分かれる。post-applyと同じ順序で両方を実行し、
    配布先に対する警告が無いことと、境界標識付きの本文が残ることを確かめる。
    """
    dotfiles_root = tmp_path / "dotfiles"
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: dotfiles_root)
    monkeypatch.setattr(claude_common, "CLAUDE_HOME", tmp_path / "home" / ".claude")
    codex_home = tmp_path / "home" / ".codex"
    monkeypatch.setattr(sync_agent_toolkit_rules, "CODEX_HOME", codex_home)
    monkeypatch.setattr(setup_codex_links, "CODEX_HOME", codex_home)
    rules_src = dotfiles_root / "agent-toolkit" / "rules"
    rules_src.mkdir(parents=True)
    (rules_src / "01-agent.md").write_text("条文\n", encoding="utf-8")

    assert sync_agent_toolkit_rules.run() is True
    rules_dest = codex_home / "agent-toolkit" / "rules"
    with caplog.at_level(logging.WARNING):
        setup_codex_links.run()

    assert str(rules_dest) not in caplog.text
    assert not rules_dest.is_symlink()
    distributed = (rules_dest / "01-agent.md").read_text(encoding="utf-8")
    assert distributed.startswith(f"<{sync_agent_toolkit_rules.NORMATIVE_ELEMENT} ")
    assert distributed.endswith(f"</{sync_agent_toolkit_rules.NORMATIVE_ELEMENT}>\n")
    assert "条文" in distributed


def test_windows_recreates_link_when_junction_like_dangling(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows相当環境でリンク切れジャンクション（`is_symlink`は偽だが`_is_link_like`が真）を
    検出した場合、`_remove_link`実行後に`CreateJunction`を呼ぶこと。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")
    src = dotfiles_root / "agent-toolkit" / "skills" / "foo"
    src.mkdir(parents=True)
    dest = codex_home / "skills" / "foo"

    monkeypatch.setattr(setup_codex_links.sys, "platform", "win32")
    monkeypatch.setattr(setup_codex_links, "_is_link_like", lambda path: path == dest)

    calls: list[str] = []
    monkeypatch.setattr(setup_codex_links, "_remove_link", lambda path: calls.append("remove"))

    fake_winapi = type(sys)("_winapi_fake")

    def fake_create_junction(source: str, destination: str) -> None:
        del source, destination
        calls.append("create")

    fake_winapi.CreateJunction = fake_create_junction  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "_winapi", fake_winapi)

    assert setup_codex_links.run() is True

    assert calls == ["remove", "create"]


def test_windows_creates_junction(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows相当環境では`_winapi.CreateJunction`が期待引数で呼ばれ`True`返却。"""
    dotfiles_root, codex_home = env
    _set_single_link(monkeypatch, "skills/foo", f"{_TOOLKIT_PREFIX}/skills/foo")
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
