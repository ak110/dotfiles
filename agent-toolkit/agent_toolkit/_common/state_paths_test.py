"""`agent_toolkit._common.state_paths`の状態ディレクトリの解決を検証する。"""

import os
import pathlib

import platformdirs
import pytest

from agent_toolkit._common import state_paths


@pytest.mark.skipif(os.name == "nt", reason="XDG_STATE_HOMEの規則はWindows以外で働く")
@pytest.mark.parametrize("condition", ["unset", "absolute", "relative", "empty"])
def test_state_dir_follows_xdg_state_home_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, condition: str
) -> None:
    """`XDG_STATE_HOME`が未設定・絶対・相対・空の4条件で、`platformdirs`と同じ規則の位置を返す。

    相対値だけは作業ディレクトリごとに別の場所を指すため、`HOME/.local/state`へ退避する。
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    values = {"absolute": str(tmp_path / "state"), "relative": "relative-state", "empty": ""}
    if condition == "unset":
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_STATE_HOME", values[condition])

    expected = {
        "unset": pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)),
        "absolute": tmp_path / "state" / "agent-toolkit",
        "relative": home / ".local" / "state" / "agent-toolkit",
        "empty": pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)),
    }[condition]
    assert state_paths.state_dir() == expected
    assert state_paths.lock_dir() == expected / "locks"


def test_state_dir_uses_local_app_data_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Windowsでは`LOCALAPPDATA`配下の`agent-toolkit`を使う。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert state_paths.state_dir(os_name="nt") == tmp_path / "local" / "agent-toolkit"
