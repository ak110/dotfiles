"""`agent_toolkit._common.host_homes`のClaude Codeの設定ディレクトリとCodexのホームの解決を検証する。"""

import pathlib

import pytest

from agent_toolkit._common import host_homes


@pytest.mark.parametrize(
    ("value", "uses_value"),
    [(None, False), ("", False), ("relative/config", False), ("absolute", True)],
    ids=["unset", "empty", "relative", "absolute"],
)
def test_claude_config_dir_accepts_only_absolute_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, value: str | None, uses_value: bool
) -> None:
    """`CLAUDE_CONFIG_DIR`は空でない絶対パスだけを採用し、それ以外はホーム配下の`.claude`を返す。"""
    configured = tmp_path / "configured"
    if value is None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(configured) if value == "absolute" else value)

    expected = configured if uses_value else pathlib.Path.home() / ".claude"
    assert host_homes.claude_config_dir() == expected
    assert host_homes.claude_config_dir(home=tmp_path / "home") == (configured if uses_value else tmp_path / "home" / ".claude")


def test_claude_config_dir_reads_given_environment(tmp_path: pathlib.Path) -> None:
    """子プロセスへ渡す環境を指定した場合は、その環境の値で解決する。"""
    assert host_homes.claude_config_dir({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path
    assert host_homes.claude_config_dir({}) == pathlib.Path.home() / ".claude"


@pytest.mark.parametrize("value", [None, "", "set"], ids=["unset", "empty", "set"])
def test_codex_home_treats_empty_value_as_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, value: str | None
) -> None:
    """`CODEX_HOME`は空でなければその値、空か未設定なら`~/.codex`を返す。"""
    if value is None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
    else:
        monkeypatch.setenv("CODEX_HOME", str(tmp_path) if value == "set" else "")

    assert host_homes.codex_home() == (tmp_path if value == "set" else pathlib.Path.home() / ".codex")
