"""セッションのmanaged-tempの決定的な解決、旧参照の再利用と、新規作成が旧状態を作成しないことを検証する。"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import paths


def _legacy_reference(session_id: str, target: pathlib.Path) -> pathlib.Path:
    reference = paths.state_root() / ".sessions" / hashlib.sha256(session_id.encode()).hexdigest()
    reference.parent.mkdir(mode=0o700, exist_ok=True)
    reference.write_text(json.dumps({"path": str(target), "session_id": session_id}), encoding="utf-8")
    return reference


def _tree(root: pathlib.Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")) if root.exists() else []


def test_new_session_resolves_deterministic_area_without_legacy_state() -> None:
    """同じ会話は同じ決定的パスへ解決し、登録簿・marker・参照・ロックを作成しない。"""
    assert managed_temp.find_session_temp("session-a") is None
    first = managed_temp.resolve_session_temp("session-a")
    digest = hashlib.sha256(b"session-a").hexdigest()[:16]
    assert first == managed_temp.temp_root_path() / f"session-{digest}"
    assert managed_temp.resolve_session_temp("session-a") == first
    assert managed_temp.find_session_temp("session-a") == first
    assert managed_temp.session_temp_path("session-a") == first
    assert managed_temp.resolve_session_temp("session-b") != first
    if os.name == "posix":
        assert stat.S_IMODE(first.stat().st_mode) == 0o700
    assert not list(first.iterdir())
    assert _tree(paths.state_root_path()) == []


def test_legacy_reference_is_reused_read_only() -> None:
    """旧参照が一時root直下の実在ディレクトリを指す会話は、旧パスを参照を変えずに使う。"""
    legacy = managed_temp.temp_root_path() / "session-legacyab"
    paths.temp_root()
    legacy.mkdir(mode=0o700)
    reference = _legacy_reference("old-session", legacy)
    before = reference.read_bytes()
    assert managed_temp.resolve_session_temp("old-session") == legacy
    assert managed_temp.find_session_temp("old-session") == legacy
    assert reference.read_bytes() == before
    assert not managed_temp.session_temp_path("old-session").exists()


@pytest.mark.parametrize("kind", ["missing", "outside", "nested", "link"])
def test_unusable_legacy_reference_falls_back_to_deterministic_area(kind: str, tmp_path: pathlib.Path) -> None:
    """旧参照の指す先が不在・root外・root直下以外・リンクなら、決定的パスを使う。"""
    root = paths.temp_root()
    target = {
        "missing": root / "session-missing1",
        "outside": tmp_path / "outside",
        "nested": root / "parent" / "child",
        "link": root / "session-link0001",
    }[kind]
    if kind == "outside":
        target.mkdir()
    elif kind == "nested":
        target.mkdir(parents=True)
    elif kind == "link":
        (tmp_path / "linked").mkdir()
        target.symlink_to(tmp_path / "linked", target_is_directory=True)
    _legacy_reference("fallback-session", target)
    assert managed_temp.resolve_session_temp("fallback-session") == managed_temp.session_temp_path("fallback-session")


def test_output_temp_is_child_of_session_area(monkeypatch: pytest.MonkeyPatch) -> None:
    """会話が分かる保存物はセッションのmanaged-tempの子へ、分からない保存物は一時root直下へ作成する。"""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "output-session")
    child = managed_temp.create_output_temp("atk-output")
    assert child.parent == managed_temp.session_temp_path("output-session")
    assert child.name.startswith("atk-output-")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    standalone = managed_temp.create_output_temp("atk-output")
    assert standalone.parent == managed_temp.temp_root_path()


def test_create_rejects_invalid_prefix_and_outside_session_root(tmp_path: pathlib.Path) -> None:
    with pytest.raises(managed_temp.ManagedTempError, match="prefix"):
        managed_temp.create_managed_temp("Bad_Prefix")
    with pytest.raises(managed_temp.ManagedTempError, match="一時rootの配下ではない"):
        managed_temp.create_session_temp("work", tmp_path)
    created = managed_temp.create_managed_temp("work")
    assert created.parent == managed_temp.temp_root_path()
    assert not list(created.iterdir())
