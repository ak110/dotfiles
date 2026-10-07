"""`_agents_server/shared_roots.py`の振る舞いを検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._agents_server import (
    shared_layout,
    shared_roots,
)


def test_process_root_identities_are_valid_and_collision_free() -> None:
    first = shared_roots.create_process_root_identity()
    second = shared_roots.create_process_root_identity()

    assert first.file_name == "root.json"
    assert first.host_session_id is None
    assert shared_layout.valid_session_id(first.root_session_id)
    assert first.root_session_id != second.root_session_id


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"AGENT_TOOLKIT_OWNER_SESSION": "owner"}, "owner"),
        ({"CLAUDE_CODE_SESSION_ID": "root-session"}, "root-session"),
        ({}, None),
        ({"AGENT_TOOLKIT_OWNER_SESSION": "../invalid"}, None),
    ],
)
def test_resolve_root_session_id(environment: dict[str, str], expected: str | None) -> None:
    """読取対象のルートsessionは書込主体の識別要件から独立して解決する。"""
    assert shared_roots.resolve_root_session_id(environment) == expected


def test_conversation_root_resolution_uses_only_alias_with_existing_target(tmp_path: pathlib.Path) -> None:
    """索引の有無、妥当性および参照先の実在を別々の解決状態として返す。"""
    environment = {"CLAUDE_CODE_SESSION_ID": "current-session"}
    resolution = shared_roots.resolve_conversation_root(environment, tmp_path)
    assert resolution == shared_roots.ConversationRootResolution(
        current_session_id="current-session",
        root_session_id="current-session",
        alias_present=False,
        alias_valid=False,
        mapping_confirmed=False,
    )

    aliases = shared_layout.aliases_directory(tmp_path)
    aliases.mkdir(parents=True)
    alias_path = aliases / "current-session.json"
    alias_path.write_text(json.dumps({"version": 1, "root_session_id": "root-session"}), encoding="utf-8")
    resolution = shared_roots.resolve_conversation_root(environment, tmp_path)
    assert resolution is not None
    assert resolution.root_session_id == "current-session"
    assert resolution.alias_present is True
    assert resolution.alias_valid is True
    assert resolution.mapping_confirmed is False

    shared_layout.status_directory("root-session", tmp_path).mkdir()
    resolution = shared_roots.resolve_conversation_root(environment, tmp_path)
    assert resolution is not None
    assert resolution.root_session_id == "root-session"
    assert resolution.mapping_confirmed is True


def test_conversation_root_resolution_confirms_direct_root_directory(tmp_path: pathlib.Path) -> None:
    """索引が無くても現行識別子自身の状態ディレクトリがあれば対応を確認済みとする。"""
    environment = {"CLAUDE_CODE_SESSION_ID": "root-session"}
    shared_layout.status_directory("root-session", tmp_path).mkdir(parents=True)

    resolution = shared_roots.resolve_conversation_root(environment, tmp_path)

    assert resolution is not None
    assert resolution.root_session_id == "root-session"
    assert resolution.alias_present is False
    assert resolution.mapping_confirmed is True


def test_wait_identity_uses_explicit_existing_root_without_environment(tmp_path: pathlib.Path) -> None:
    """明示した実在ルートは環境の別名を必要とせずroot書込主体へ解決する。"""
    shared_layout.status_directory("mcp-root", tmp_path).mkdir(parents=True)

    identity = shared_roots.resolve_wait_identity({}, "mcp-root", tmp_path)

    assert identity == shared_roots.StatusFileIdentity("mcp-root", "root.json", None)


@pytest.mark.parametrize("root_session_id", ["../invalid", "missing-root"])
def test_wait_identity_rejects_invalid_or_missing_explicit_root(
    tmp_path: pathlib.Path,
    root_session_id: str,
) -> None:
    """不正な形式と実在しない明示ルートを待機対象として受理しない。"""
    with pytest.raises(ValueError):
        shared_roots.resolve_wait_identity({}, root_session_id, tmp_path)


def test_wait_identity_rejects_explicit_root_different_from_confirmed_conversation(
    tmp_path: pathlib.Path,
) -> None:
    """確認済みルートsessionと異なる明示値から別ルートの結果を回収しない。"""
    shared_layout.status_directory("conversation-root", tmp_path).mkdir(parents=True)
    shared_layout.status_directory("other-root", tmp_path).mkdir(parents=True)

    with pytest.raises(ValueError, match="一致しません"):
        shared_roots.resolve_wait_identity(
            {"CLAUDE_CODE_SESSION_ID": "conversation-root"},
            "other-root",
            tmp_path,
        )


def test_conversation_root_resolution_rejects_invalid_alias(tmp_path: pathlib.Path) -> None:
    """不正な索引は現行識別子へ戻し、対応未確認として扱う。"""
    environment = {"CLAUDE_CODE_SESSION_ID": "current-session"}
    aliases = shared_layout.aliases_directory(tmp_path)
    aliases.mkdir(parents=True)
    (aliases / "current-session.json").write_text("{}", encoding="utf-8")

    resolution = shared_roots.resolve_conversation_root(environment, tmp_path)

    assert resolution is not None
    assert resolution.root_session_id == "current-session"
    assert resolution.alias_present is True
    assert resolution.alias_valid is False
    assert resolution.mapping_confirmed is False


@pytest.mark.parametrize(
    ("alias_text", "alias_valid"),
    [
        ("{", False),
        ("{}", False),
        (json.dumps({"version": 1, "root_session_id": "missing-root"}), True),
    ],
)
def test_conversation_root_resolution_confirms_direct_root_after_alias_failure(
    tmp_path: pathlib.Path, alias_text: str, alias_valid: bool
) -> None:
    """索引を解釈できない場合も現行sessionの状態ディレクトリで対応を確認する。"""
    environment = {"CLAUDE_CODE_SESSION_ID": "current-session"}
    shared_layout.status_directory("current-session", tmp_path).mkdir(parents=True)
    aliases = shared_layout.aliases_directory(tmp_path)
    aliases.mkdir(parents=True)
    (aliases / "current-session.json").write_text(alias_text, encoding="utf-8")

    resolution = shared_roots.resolve_conversation_root(environment, tmp_path)

    assert resolution == shared_roots.ConversationRootResolution(
        current_session_id="current-session",
        root_session_id="current-session",
        alias_present=True,
        alias_valid=alias_valid,
        mapping_confirmed=True,
    )


def test_write_root_alias_removes_aliases_with_missing_targets(tmp_path: pathlib.Path) -> None:
    """索引更新時に参照先ディレクトリを失った既存索引を回収する。"""
    shared_layout.status_directory("root-session", tmp_path).mkdir(parents=True)
    aliases = shared_layout.aliases_directory(tmp_path)
    aliases.mkdir()
    stale = aliases / "stale-session.json"
    stale.write_text(json.dumps({"version": 1, "root_session_id": "missing-root"}), encoding="utf-8")

    shared_roots.write_root_alias("current-session", "root-session", tmp_path)

    assert json.loads((aliases / "current-session.json").read_text(encoding="utf-8")) == {
        "version": 1,
        "root_session_id": "root-session",
    }
    assert not stale.exists()


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        (
            {"CLAUDE_CODE_SESSION_ID": "root-session"},
            shared_roots.StatusFileIdentity("root-session", "root.json", None),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
                "CLAUDE_CODE_SESSION_ID": "claude-child",
                "CODEX_THREAD_ID": "ignored-codex-child",
            },
            shared_roots.StatusFileIdentity("owner", "claude-child.json", "claude-child"),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "CLAUDE_CODE_SESSION_ID": "root-session",
                "CODEX_THREAD_ID": "codex-child",
            },
            shared_roots.StatusFileIdentity("owner", "codex-child.json", "codex-child"),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "AGENT_TOOLKIT_STATUS_HOST_SESSION": "writer-session",
            },
            shared_roots.StatusFileIdentity("owner", "writer-session.json", "writer-session"),
        ),
        ({}, None),
        ({"AGENT_TOOLKIT_OWNER_SESSION": "owner"}, None),
        ({"CLAUDE_CODE_SESSION_ID": "../invalid"}, None),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "CODEX_THREAD_ID": "invalid/child",
            },
            None,
        ),
    ],
)
def test_resolve_status_file_identity(environment: dict[str, str], expected: shared_roots.StatusFileIdentity | None) -> None:
    """ルート・両backendの委譲先・識別不能な所有session・不正識別子を区別する。"""
    assert shared_roots.resolve_status_file_identity(environment) == expected


def test_write_host_alias_resolves_writer_to_thread_id(tmp_path: pathlib.Path) -> None:
    """書込主体から委譲元threadへの索引は形式を検証して保存する。"""
    shared_roots.write_host_alias("root", "writer", "thread", tmp_path)

    assert json.loads((shared_layout.hosts_directory("root", tmp_path) / "writer.json").read_text(encoding="utf-8")) == {
        "version": 1,
        "host_session_id": "thread",
    }
    with pytest.raises(ValueError, match="invalid session_id"):
        shared_roots.write_host_alias("root", "bad/writer", "thread", tmp_path)


def test_resolve_status_owner_identity_uses_writer_alias(tmp_path: pathlib.Path) -> None:
    """Codex threadを内側MCPの書込主体へ逆引きする。"""
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}
    shared_roots.write_host_alias("root", "writer", "thread", tmp_path)

    assert shared_roots.resolve_status_owner_identity(environment, tmp_path) == shared_roots.StatusFileIdentity(
        "root", "writer.json", "writer"
    )


def test_resolve_status_owner_identity_keeps_unindexed_identity(tmp_path: pathlib.Path) -> None:
    """索引が無い呼出主体は環境変数から解決した書込主体を維持する。"""
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}

    assert shared_roots.resolve_status_owner_identity(environment, tmp_path) == shared_roots.StatusFileIdentity(
        "root", "thread.json", "thread"
    )


def test_resolve_status_owner_identity_recovers_from_live_status_file(tmp_path: pathlib.Path) -> None:
    """索引が失われても、委譲元threadを記録した状態から書込主体を一意に復元する。"""
    root = shared_layout.status_directory("root", tmp_path)
    root.mkdir(parents=True)
    (root / "writer.json").write_text('{"version": 1, "host_session_id": "thread", "sessions": []}', encoding="utf-8")

    assert shared_roots.resolve_status_owner_identity(
        {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}, tmp_path
    ) == shared_roots.StatusFileIdentity("root", "writer.json", "writer")


def test_resolve_status_owner_identity_rejects_ambiguous_aliases(tmp_path: pathlib.Path) -> None:
    """同じthreadへ複数の書込主体が対応する索引を推測で選ばない。"""
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}
    shared_roots.write_host_alias("root", "writer-a", "thread", tmp_path)
    shared_roots.write_host_alias("root", "writer-b", "thread", tmp_path)

    with pytest.raises(ValueError, match="書込主体を一意に解決できません"):
        shared_roots.resolve_status_owner_identity(environment, tmp_path)
