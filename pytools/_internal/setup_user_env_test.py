import contextlib
import types
from pathlib import Path

import pytest

from pytools._internal import common, setup_user_env, winutils

from ._test_helpers import FakeEnvironmentRegistry


@pytest.fixture(name="dotfiles_root")
def _dotfiles_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    (tmp_path / "share").mkdir()
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path)
    return tmp_path


def test_writes_only_changed_values(monkeypatch: pytest.MonkeyPatch, dotfiles_root: Path) -> None:
    (dotfiles_root / "share" / "user.env").write_text("# comment\n\nSAME=1\nNEW=value\n", encoding="utf-8")
    registry = FakeEnvironmentRegistry()
    registry.user["SAME"] = ("1", FakeEnvironmentRegistry.REG_SZ)
    registry.install(monkeypatch)

    assert setup_user_env.run().changed is True
    assert registry.writes == [("NEW", "value", FakeEnvironmentRegistry.REG_SZ)]

    registry.writes.clear()
    assert setup_user_env.run().changed is False
    assert not registry.writes


def test_invalid_file_is_reported_as_failure(monkeypatch: pytest.MonkeyPatch, dotfiles_root: Path) -> None:
    """書式に反する行があれば何も書き込まず失敗と数える。"""
    (dotfiles_root / "share" / "user.env").write_text("OK=1\nexport BAD=1\n", encoding="utf-8")
    registry = FakeEnvironmentRegistry()
    registry.install(monkeypatch)

    outcome = setup_user_env.run()

    assert outcome.failure is not None
    assert "2行目" in outcome.failure
    assert not registry.writes


@pytest.mark.parametrize(
    "line",
    [
        "KEY='quoted'",
        'KEY="quoted"',
        "export KEY=1",
        "KEY=$HOME",
        "KEY=1 # comment",
        "KEY=a b",
        " KEY=1",
        "1KEY=1",
    ],
)
def test_parse_rejects_lines_bash_and_python_read_differently(line: str) -> None:
    with pytest.raises(ValueError, match="1行目"):
        setup_user_env.parse_user_env(line + "\n")


def test_parse_reads_key_values_and_skips_comments() -> None:
    text = "# header\n\nA=1\n# B='x'\nC=\n"
    assert setup_user_env.parse_user_env(text) == [("A", "1"), ("C", "")]


@pytest.mark.parametrize("name", ["DISABLE_TELEMETRY", "PYTHONDONTWRITEBYTECODE"])
@pytest.mark.parametrize("previous", [None, "0", "1"])
def test_removes_only_former_distributed_value(
    monkeypatch: pytest.MonkeyPatch, dotfiles_root: Path, previous: str | None, name: str
) -> None:
    (dotfiles_root / "share/user.env").write_text("KEEP=yes\n", encoding="utf-8")
    registry = FakeEnvironmentRegistry()
    registry.user["KEEP"] = ("yes", registry.REG_SZ)
    if previous is not None:
        registry.user[name] = (previous, registry.REG_SZ)
    registry.install(monkeypatch)
    removed: list[str] = []
    notified: list[bool] = []

    def delete(_key: object, value_name: str, **_kwargs: object) -> None:
        del registry.user[value_name]
        removed.append(value_name)

    wr = types.SimpleNamespace(
        REG_SZ=registry.REG_SZ,
        HKEY_CURRENT_USER="user",
        KEY_SET_VALUE=2,
        OpenKey=lambda *_args: contextlib.nullcontext("Environment"),
        DeleteValue=delete,
    )
    monkeypatch.setattr(winutils, "import_winreg", lambda: wr)
    monkeypatch.setattr(winutils, "broadcast_environment_change", lambda: notified.append(True))
    outcome = setup_user_env.run()
    assert outcome.failure is None
    assert outcome.changed is (previous == "1")
    assert removed == ([name] if previous == "1" else [])
    assert bool(notified) is (previous == "1")
    assert registry.user["KEEP"][0] == "yes"
    if previous == "0":
        assert registry.user[name][0] == "0"
    else:
        assert name not in registry.user


def test_failed_telemetry_removal_is_reported(monkeypatch: pytest.MonkeyPatch, dotfiles_root: Path) -> None:
    """削除拒否は既存の失敗結果へ入り、他変数の反映は続ける。"""
    (dotfiles_root / "share/user.env").write_text("NEW=yes\n", encoding="utf-8")
    registry = FakeEnvironmentRegistry()
    registry.user["DISABLE_TELEMETRY"] = ("1", registry.REG_SZ)
    registry.install(monkeypatch)

    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("denied")

    wr = types.SimpleNamespace(
        REG_SZ=registry.REG_SZ,
        HKEY_CURRENT_USER="user",
        KEY_SET_VALUE=2,
        OpenKey=lambda *_args: contextlib.nullcontext("Environment"),
        DeleteValue=denied,
    )
    monkeypatch.setattr(winutils, "import_winreg", lambda: wr)
    outcome = setup_user_env.run()
    assert outcome.failure and "denied" in outcome.failure
    assert outcome.next_action
    assert registry.user["DISABLE_TELEMETRY"][0] == "1"
    assert registry.user["NEW"][0] == "yes"
