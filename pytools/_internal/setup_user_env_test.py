"""pytools._internal.setup_user_env のテスト。"""

from pathlib import Path

import pytest

from pytools._internal import common, setup_user_env

from ._test_helpers import FakeEnvironmentRegistry


@pytest.fixture(name="dotfiles_root")
def _dotfiles_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    (tmp_path / "share").mkdir()
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path)
    return tmp_path


def test_writes_only_changed_values(monkeypatch: pytest.MonkeyPatch, dotfiles_root: Path) -> None:
    """現在値と同じ変数は書き込まず、異なる変数だけを書き込んで変更ありを返す。2回目は書き込まない。"""
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
    """bashの`set -a; .`とPythonの読み取りで値が変わり得る行を拒否する。"""
    with pytest.raises(ValueError, match="1行目"):
        setup_user_env.parse_user_env(line + "\n")


def test_parse_reads_key_values_and_skips_comments() -> None:
    """空行とコメント行を値として扱わず、`KEY=VALUE`を順に返す。空の値も受け付ける。"""
    text = "# header\n\nA=1\n# B='x'\nC=\n"
    assert setup_user_env.parse_user_env(text) == [("A", "1"), ("C", "")]
