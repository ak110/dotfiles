"""managed-tempのディレクトリの作成を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import os
import pathlib
import stat
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit._atk import managed_temp as subject
from agent_toolkit._atk.managed_temp import creation as creation_subject
from agent_toolkit._atk.managed_temp import registry as registry_subject
from agent_toolkit._testing.managed_temp_support import _isolated_cli_environment, setattr_in_managed_temp_modules

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
def test_creation_reclaims_unpublished_directory_on_every_exception(
    monkeypatch: pytest.MonkeyPatch, child: bool, error_type: type[BaseException]
) -> None:
    """作成後の例外と中断で、未公開の実体と登録を撤去して元の例外を再送出する。"""
    parent = subject.create_managed_temp("session", session_id="creation-interrupted")
    root = parent if child else parent.parent
    before = set(root.iterdir())
    registrations = set(registry_subject._state_root().glob("*.json"))
    target_name = "_validate_posix" if child and os.name == "posix" else "_validate_windows" if child else "_write_marker"
    original = getattr(creation_subject, target_name)
    calls = 0

    def fail(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if child and calls == 1:
            return original(*args, **kwargs)
        assert set(root.iterdir()) != before
        raise error_type("作成途中の失敗")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(creation_subject, target_name, fail)
            with pytest.raises(error_type, match="作成途中の失敗"):
                if child:
                    subject.create_session_temp("interrupted", parent)
                else:
                    subject.create_managed_temp("interrupted")
        assert calls == (2 if child else 1)
        assert set(root.iterdir()) == before
        assert set(registry_subject._state_root().glob("*.json")) == registrations
    finally:
        subject.cleanup_managed_temp(parent)


def test_create_parser_passes_repeated_awi_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`create --awi`の複数指定を順序どおり作成処理へ渡す。"""
    calls: list[tuple[str, pathlib.Path | str | None, tuple[str, ...]]] = []
    created = tmp_path / "created"

    def fake_create(
        prefix: str,
        root: pathlib.Path | str | None = None,
        awis: tuple[str, ...] = (),
    ) -> pathlib.Path:
        calls.append((prefix, root, awis))
        return created

    setattr_in_managed_temp_modules(monkeypatch, "create_managed_temp", fake_create)
    parser = argparse.ArgumentParser()
    subject.build_parser(parser)

    assert (
        subject.dispatch(
            parser.parse_args(
                [
                    "create",
                    "--prefix=implementation",
                    "--awi=20260830-061344-001.md",
                    "--awi=20260830-143611-001.md",
                ]
            )
        )
        == 0
    )
    assert calls == [
        (
            "implementation",
            None,
            ("20260830-061344-001.md", "20260830-143611-001.md"),
        )
    ]
    assert capsys.readouterr().out == f"{created}\n"


@pytest.mark.skipif(os.name == "nt", reason="Windowsの明示rootはACLを設定した実機テストで検証する")
def test_cli_explicit_root_returns_managed_temp_path(tmp_path: pathlib.Path) -> None:
    """CLIの明示root形が指定root直下の絶対pathを返す。"""
    env, _ = _isolated_cli_environment(tmp_path / "default")
    explicit_root = tmp_path / "shared-root"
    explicit_root.mkdir()
    created = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "create",
            "--prefix",
            "cli-explicit",
            "--root",
            str(explicit_root),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    target = pathlib.Path(created.stdout.strip())
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert created.returncode == 0
    assert target.parent == explicit_root
    assert cleaned.returncode == 0
    assert not target.exists()


def test_session_child_uses_parent_registration_only() -> None:
    """セッションのmanaged-temp直下の作業ディレクトリは親の回収単位へ含め、個別の登録を増やさない。"""
    session_root = subject.create_managed_temp("session", session_id="session-1")

    child = subject.create_session_temp("work", session_root)

    assert child.parent == session_root
    if os.name == "posix":
        assert stat.S_IMODE(child.stat().st_mode) == 0o700
    assert not (child / _MARKER_NAME).exists()
    assert [entry["path"] for entry in subject.list_managed_temp()] == [str(session_root)]
    subject.cleanup_managed_temp(session_root)
    assert not child.exists()


def test_session_child_rejects_non_session_managed_root() -> None:
    """通常の管理対象をセッションrootとして流用しない。"""
    root = subject.create_managed_temp("ordinary")

    with pytest.raises(subject.ManagedTempError, match="session_rootはセッション識別子を持つ"):
        subject.create_session_temp("work", root)

    subject.cleanup_managed_temp(root)


def test_session_outputs_share_one_session_area(monkeypatch: pytest.MonkeyPatch) -> None:
    """保存の反復・同時要求・再開が同じ親を使い、別会話と既存本文を保持する。"""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "outputs-session")
    prefixes = ["atk-output", "atk-command", "atk-diagnostics", "agents-wait", "plan-verify-results", "exec-review-input"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        paths = list(executor.map(subject.create_output_temp, prefixes * 2))
    parents = {path.parent for path in paths}
    assert len(parents) == 1
    assert len(set(paths)) == len(paths)
    parent = parents.pop()
    for index, path in enumerate(paths):
        (path / "result").write_text(str(index), encoding="utf-8")
    assert [entry["path"] for entry in subject.list_managed_temp()] == [str(parent)]
    monkeypatch.setenv("CODEX_THREAD_ID", "other-session")
    other = subject.create_output_temp("atk-output")
    assert other.parent != parent
    monkeypatch.setenv("CODEX_THREAD_ID", "outputs-session")
    with monkeypatch.context() as patch:
        patch.setattr(creation_subject, "list_managed_temp", lambda **_kwargs: pytest.fail("既存会話の全登録を探索した"))
        assert subject.create_output_temp("atk-output").parent == parent
    subject.cleanup_managed_temp(paths[0])
    assert not paths[0].exists()
    assert all((path / "result").read_text(encoding="utf-8") == str(index) for index, path in enumerate(paths) if index)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "foreign-session"])
def test_session_reference_is_rebuilt_from_valid_registration(damage: str) -> None:
    """直接参照の欠落・破損・別会話への変更を、既存の真正な登録から復元する。"""
    parent = subject.resolve_session_temp("first-session")
    other = subject.resolve_session_temp("other-session")
    reference = registry_subject._state_root() / ".sessions" / hashlib.sha256(b"first-session").hexdigest()
    if damage == "missing":
        reference.unlink()
    elif damage == "corrupt":
        reference.write_text("{", encoding="utf-8")
    else:
        reference.write_bytes(registry_subject._registry_path(other).read_bytes())
    assert subject.resolve_session_temp("first-session") == parent
    assert {entry["path"] for entry in subject.list_managed_temp()} == {str(parent), str(other)}


def test_invalid_session_reference_preserves_existing_area() -> None:
    """参照先のmarkerが成立しない場合も、既存領域の内容を保持する。"""
    parent = subject.resolve_session_temp("first-session")
    (parent / "evidence").write_text("保持する証拠", encoding="utf-8")
    (parent / _MARKER_NAME).unlink()
    replacement = subject.resolve_session_temp("first-session")
    assert replacement != parent
    assert (parent / "evidence").read_text(encoding="utf-8") == "保持する証拠"
