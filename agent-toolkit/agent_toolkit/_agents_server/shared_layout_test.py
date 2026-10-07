"""`_agents_server/shared_layout.py`の振る舞いを検証する。"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._agents_server import (
    shared_layout,
)


def test_root_session_listing_excludes_management_directories(tmp_path: pathlib.Path) -> None:
    """状態ディレクトリ直下の管理用ディレクトリはルートsession識別子として列挙しない。"""
    base = tmp_path / "agents-server"
    for name in ("aliases", "compaction", "sessions", "root-session"):
        (base / name).mkdir(parents=True)
    (base / "aliases" / "current.json").write_text('{"version": 1, "root_session_id": "root-session"}', encoding="utf-8")

    assert shared_layout.list_root_session_ids(tmp_path) == ["root-session"]


def test_status_directory_uses_platform_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """状態ディレクトリをatk configと同じXDG規則から解決する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert shared_layout.status_directory("root") == tmp_path / "agent-toolkit" / "agents-server" / "root"
    assert shared_layout.notices_directory("root") == tmp_path / "agent-toolkit" / "agents-server" / "root" / "notices"
    assert shared_layout.hosts_directory("root") == tmp_path / "agent-toolkit" / "agents-server" / "root" / "hosts"


def test_status_directory_rejects_relative_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """相対XDG_STATE_HOMEではatk configと同じHOME配下へ状態を書き込む。"""
    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert shared_layout.status_directory("root") == (
        tmp_path / "home" / ".local" / "state" / "agent-toolkit" / "agents-server" / "root"
    )


@pytest.mark.parametrize(
    ("file_names", "expected"),
    [
        (["root.json"], ["root.json"]),
        (["child.json"], ["child.json"]),
        (["root.json", "child.json"], ["child.json", "root.json"]),
        (["root.json", "results/result.json"], ["root.json"]),
    ],
)
def test_list_status_files_returns_direct_json_files_in_stable_order(
    tmp_path: pathlib.Path,
    file_names: list[str],
    expected: list[str],
) -> None:
    """状態ディレクトリ直下のJSON通常ファイルだけを絶対パスの安定順で返す。"""
    directory = shared_layout.status_directory("root", tmp_path)
    for file_name in file_names:
        path = directory / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    assert shared_layout.list_status_files("root", tmp_path) == [directory / file_name for file_name in expected]
