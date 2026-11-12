"""setup_bin_path.run() のテスト。"""

from pathlib import Path
from unittest import mock

import pytest

from pytools._internal import common, setup_bin_path

from ._test_helpers import FakeEnvironmentRegistry

_PREVIOUS_ENTRIES = [
    r"%USERPROFILE%\dotfiles\bin",
    r"%USERPROFILE%\dotfiles\agent-toolkit\scripts",
    r"%USERPROFILE%\.local\bin",
]


@pytest.fixture(autouse=True, name="dotfiles_under_home")
def _dotfiles_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """作業ツリーが`~/dotfiles`にある環境を再現する。"""
    home = tmp_path / "home"
    monkeypatch.setattr(setup_bin_path.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: home / "dotfiles")
    return home


def test_entries_keep_previous_userprofile_form_when_root_is_home_dotfiles(dotfiles_under_home: Path) -> None:
    """ルートが`~/dotfiles`なら、登録値は変更前と同じ`%USERPROFILE%`形式の3件になる。

    絶対パスで登録すると、PATH整理が`%USERPROFILE%`形式へ戻して次の登録が再び追記する往復が起こる。
    """
    entries = setup_bin_path._bin_entries(dotfiles_under_home / "dotfiles", dotfiles_under_home)  # pylint: disable=protected-access
    assert list(entries) == _PREVIOUS_ENTRIES


def test_entries_outside_home_use_absolute_root() -> None:
    """ホーム外の作業ツリーは絶対パスで登録し、`.local\\bin`は`%USERPROFILE%`形式のまま登録する。"""
    entries = setup_bin_path._bin_entries(Path("/srv/dotfiles"), Path("/home/user"))  # pylint: disable=protected-access
    assert entries[2] == r"%USERPROFILE%\.local\bin"
    assert entries[0].endswith("dotfiles\\bin")
    assert entries[1].endswith("dotfiles\\agent-toolkit\\scripts")
    assert "%USERPROFILE%" not in entries[0]


def test_entries_without_root_register_only_local_bin() -> None:
    """作業ツリーを解決できない場合は`.local\\bin`だけを登録する。"""
    assert setup_bin_path._bin_entries(None, Path("/home/user")) == (r"%USERPROFILE%\.local\bin",)  # pylint: disable=protected-access


@pytest.mark.parametrize("existing", [0, 2, 3])
def test_run_appends_local_bin_once(monkeypatch: pytest.MonkeyPatch, existing: int) -> None:
    """既存項目と表記を保って不足分だけ保存し、再適用では書込も通知もしない。"""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\test")
    registry = FakeEnvironmentRegistry(
        user_path=";".join([r"C:\Windows", *_PREVIOUS_ENTRIES[:existing]]),
        system_path=r"C:\Windows\System32",
    )
    registry.install(monkeypatch)
    broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", broadcast)

    assert setup_bin_path.run().changed is (existing != 3)
    assert registry.read_user("Path") == (";".join([r"C:\Windows", *_PREVIOUS_ENTRIES]), registry.REG_EXPAND_SZ)
    assert registry.system == {"Path": (r"C:\Windows\System32", registry.REG_EXPAND_SZ)}
    assert broadcast.call_count == (1 if existing != 3 else 0)
    assert setup_bin_path.run().changed is False
    assert registry.user_path().split(";") == [r"C:\Windows", *_PREVIOUS_ENTRIES]
    assert len(registry.writes) == 3 - existing
    assert broadcast.call_count == (1 if existing != 3 else 0)


def test_run_entry_write_failure_continues_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """最初の書込失敗後も残りを保存し、失敗と保存変更の通知を返す。"""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\test")
    registry = FakeEnvironmentRegistry(user_path=r"C:\Windows")
    registry.install(monkeypatch)
    broadcast = mock.Mock()
    monkeypatch.setattr(setup_bin_path.winutils, "broadcast_environment_change", broadcast)

    def write(name: str, value: str, reg_type: int, **_kwargs: object) -> None:
        if _PREVIOUS_ENTRIES[0] in value.split(";"):
            raise PermissionError("boom")
        registry.write_user(name, value, reg_type)

    monkeypatch.setattr(setup_bin_path.winutils, "write_user_env_var", write)
    outcome = setup_bin_path.run()
    assert outcome.changed is True
    assert outcome.failure and "boom" in outcome.failure
    assert registry.read_user("Path") == (";".join([r"C:\Windows", *_PREVIOUS_ENTRIES[1:]]), registry.REG_EXPAND_SZ)
    broadcast.assert_called_once()
