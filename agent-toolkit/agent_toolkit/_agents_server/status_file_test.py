"""agents_serverのstatusline向け状態ファイル契約を検証する。"""

# テストでは共有managerと状態モデルの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import datetime
import json
import logging
import os
import pathlib
import typing

import pytest

from agent_toolkit import agents_server_mcp
from agent_toolkit._agents_server import state
from agent_toolkit._agents_server import status_file as subject


async def _wait_now(manager: agents_server_mcp.AgentsServerManager) -> dict[str, typing.Any]:
    """待機せずに現在の終端状態を返すwaitを発行する。"""
    manager._wait_timeouts["main"] = 0.0  # pylint: disable=protected-access
    return await manager.wait()


def test_unavailable_candidates_are_kept_until_the_retention_period_elapses(tmp_path: pathlib.Path) -> None:
    """除外した候補を保持期間内は返し、経過後は返さない。"""
    recorded_at = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)
    subject.record_unavailable_candidate(
        "plan",
        "delegate",
        ("claude", "opus", "high"),
        "401",
        now=recorded_at,
        state_root=tmp_path,
    )
    within = recorded_at + datetime.timedelta(seconds=subject.UNAVAILABLE_CANDIDATES_RETENTION_SECONDS - 1)
    after = recorded_at + datetime.timedelta(seconds=subject.UNAVAILABLE_CANDIDATES_RETENTION_SECONDS)

    assert subject.load_unavailable_candidates("plan", "delegate", now=within, state_root=tmp_path) == {
        ("claude", "opus", "high"): "401"
    }
    assert not subject.load_unavailable_candidates("plan", "delegate", now=after, state_root=tmp_path)
    assert not subject.load_unavailable_candidates("plan", "explore", now=within, state_root=tmp_path)


def test_unavailable_candidates_hold_two_or_more_engines_and_clear_individually(tmp_path: pathlib.Path) -> None:
    """同じ起動条件で2件以上の候補を保持し、成立した候補だけを取り除く。"""
    now = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)
    subject.record_unavailable_candidate("plan", "delegate", ("claude", "opus", "high"), "401", now=now, state_root=tmp_path)
    subject.record_unavailable_candidate(
        "plan", "delegate", ("codex", "terra", "medium"), "usageLimitExceeded", now=now, state_root=tmp_path
    )

    assert subject.load_unavailable_candidates("plan", "delegate", now=now, state_root=tmp_path) == {
        ("claude", "opus", "high"): "401",
        ("codex", "terra", "medium"): "usageLimitExceeded",
    }

    subject.clear_unavailable_candidate("plan", "delegate", ("codex", "terra", "medium"), now=now, state_root=tmp_path)

    assert subject.load_unavailable_candidates("plan", "delegate", now=now, state_root=tmp_path) == {
        ("claude", "opus", "high"): "401"
    }


def test_clearing_an_unrecorded_candidate_creates_no_file(tmp_path: pathlib.Path) -> None:
    """記録の無い候補の解除は記録ファイルを作成しない。"""
    now = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)

    subject.clear_unavailable_candidate("plan", "delegate", ("claude", "opus", "high"), now=now, state_root=tmp_path)

    assert not subject.unavailable_candidates_path(tmp_path).exists()


def test_unavailable_candidates_record_is_a_file_and_not_a_root_session(tmp_path: pathlib.Path) -> None:
    """記録はルートsession識別子の列挙へ現れない単一ファイルとする。"""
    now = datetime.datetime(2026, 9, 16, 0, 0, tzinfo=datetime.UTC)
    subject.record_unavailable_candidate("plan", "delegate", ("claude", "opus", "high"), "401", now=now, state_root=tmp_path)

    assert subject.unavailable_candidates_path(tmp_path).is_file()
    assert subject.list_root_session_ids(tmp_path) == []


def test_process_root_identities_are_valid_and_collision_free() -> None:
    first = subject.create_process_root_identity()
    second = subject.create_process_root_identity()

    assert first.file_name == "root.json"
    assert first.host_session_id is None
    assert subject.valid_session_id(first.root_session_id)
    assert first.root_session_id != second.root_session_id


@pytest.mark.asyncio
async def test_writer_logs_result_write_and_delete_without_body(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """result操作へsessionと書込主体を記録し、結果本文を含めない。"""
    session = state.SessionState("session-1", str(tmp_path))
    session.status = "completed"
    session.agent_message = "秘密の結果本文"
    session.turn_completed = True
    session.touch()
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )

    with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.status-file"):
        writer.retain_result(session)
        writer.delete_result(session.session_id, collector="mcp-wait")

    assert "result_written session_id=session-1 writer=root.json" in caplog.text
    assert "result_deleted session_id=session-1 writer=root.json collector=mcp-wait" in caplog.text
    assert "秘密の結果本文" not in caplog.text


@pytest.mark.asyncio
async def test_status_file_includes_updated_at(tmp_path: pathlib.Path) -> None:
    """状態ファイルのsession射影は最終活動時刻を含む。"""
    session = state.SessionState("session-1", str(tmp_path))
    session.announced = True
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )

    writer.activate()
    saved = json.loads(writer.path.read_text(encoding="utf-8"))

    assert saved["sessions"][0]["updated_at"] == session.updated_at
    writer.deactivate()


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
    assert subject.resolve_root_session_id(environment) == expected


def test_conversation_root_resolution_uses_only_alias_with_existing_target(tmp_path: pathlib.Path) -> None:
    """索引の有無、妥当性及び参照先の実在を別々の解決状態として返す。"""
    environment = {"CLAUDE_CODE_SESSION_ID": "current-session"}
    resolution = subject.resolve_conversation_root(environment, tmp_path)
    assert resolution == subject.ConversationRootResolution(
        current_session_id="current-session",
        root_session_id="current-session",
        alias_present=False,
        alias_valid=False,
        mapping_confirmed=False,
    )

    aliases = subject.aliases_directory(tmp_path)
    aliases.mkdir(parents=True)
    alias_path = aliases / "current-session.json"
    alias_path.write_text(json.dumps({"version": 1, "root_session_id": "root-session"}), encoding="utf-8")
    resolution = subject.resolve_conversation_root(environment, tmp_path)
    assert resolution is not None
    assert resolution.root_session_id == "current-session"
    assert resolution.alias_present is True
    assert resolution.alias_valid is True
    assert resolution.mapping_confirmed is False

    subject.status_directory("root-session", tmp_path).mkdir()
    resolution = subject.resolve_conversation_root(environment, tmp_path)
    assert resolution is not None
    assert resolution.root_session_id == "root-session"
    assert resolution.mapping_confirmed is True


def test_conversation_root_resolution_confirms_direct_root_directory(tmp_path: pathlib.Path) -> None:
    """索引が無くても現行識別子自身の状態ディレクトリがあれば対応を確認済みとする。"""
    environment = {"CLAUDE_CODE_SESSION_ID": "root-session"}
    subject.status_directory("root-session", tmp_path).mkdir(parents=True)

    resolution = subject.resolve_conversation_root(environment, tmp_path)

    assert resolution is not None
    assert resolution.root_session_id == "root-session"
    assert resolution.alias_present is False
    assert resolution.mapping_confirmed is True


def test_wait_identity_uses_explicit_existing_root_without_environment(tmp_path: pathlib.Path) -> None:
    """明示した実在ルートは環境の別名を必要とせずroot書込主体へ解決する。"""
    subject.status_directory("mcp-root", tmp_path).mkdir(parents=True)

    identity = subject.resolve_wait_identity({}, "mcp-root", tmp_path)

    assert identity == subject.StatusFileIdentity("mcp-root", "root.json", None)


@pytest.mark.parametrize("root_session_id", ["../invalid", "missing-root"])
def test_wait_identity_rejects_invalid_or_missing_explicit_root(
    tmp_path: pathlib.Path,
    root_session_id: str,
) -> None:
    """不正な形式と実在しない明示ルートを待機対象として受理しない。"""
    with pytest.raises(ValueError):
        subject.resolve_wait_identity({}, root_session_id, tmp_path)


def test_wait_identity_rejects_explicit_root_different_from_confirmed_conversation(
    tmp_path: pathlib.Path,
) -> None:
    """確認済み会話rootと異なる明示値から別ルートの結果を回収しない。"""
    subject.status_directory("conversation-root", tmp_path).mkdir(parents=True)
    subject.status_directory("other-root", tmp_path).mkdir(parents=True)

    with pytest.raises(ValueError, match="一致しません"):
        subject.resolve_wait_identity(
            {"CLAUDE_CODE_SESSION_ID": "conversation-root"},
            "other-root",
            tmp_path,
        )


def test_conversation_root_resolution_rejects_invalid_alias(tmp_path: pathlib.Path) -> None:
    """不正な索引は現行識別子へ戻し、対応未確認として扱う。"""
    environment = {"CLAUDE_CODE_SESSION_ID": "current-session"}
    aliases = subject.aliases_directory(tmp_path)
    aliases.mkdir(parents=True)
    (aliases / "current-session.json").write_text("{}", encoding="utf-8")

    resolution = subject.resolve_conversation_root(environment, tmp_path)

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
    subject.status_directory("current-session", tmp_path).mkdir(parents=True)
    aliases = subject.aliases_directory(tmp_path)
    aliases.mkdir(parents=True)
    (aliases / "current-session.json").write_text(alias_text, encoding="utf-8")

    resolution = subject.resolve_conversation_root(environment, tmp_path)

    assert resolution == subject.ConversationRootResolution(
        current_session_id="current-session",
        root_session_id="current-session",
        alias_present=True,
        alias_valid=alias_valid,
        mapping_confirmed=True,
    )


def test_write_root_alias_removes_aliases_with_missing_targets(tmp_path: pathlib.Path) -> None:
    """索引更新時に参照先ディレクトリを失った既存索引を回収する。"""
    subject.status_directory("root-session", tmp_path).mkdir(parents=True)
    aliases = subject.aliases_directory(tmp_path)
    aliases.mkdir()
    stale = aliases / "stale-session.json"
    stale.write_text(json.dumps({"version": 1, "root_session_id": "missing-root"}), encoding="utf-8")

    subject.write_root_alias("current-session", "root-session", tmp_path)

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
            subject.StatusFileIdentity("root-session", "root.json", None),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
                "CLAUDE_CODE_SESSION_ID": "claude-child",
                "CODEX_THREAD_ID": "ignored-codex-child",
            },
            subject.StatusFileIdentity("owner", "claude-child.json", "claude-child"),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "CLAUDE_CODE_SESSION_ID": "root-session",
                "CODEX_THREAD_ID": "codex-child",
            },
            subject.StatusFileIdentity("owner", "codex-child.json", "codex-child"),
        ),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner",
                "AGENT_TOOLKIT_STATUS_HOST_SESSION": "writer-session",
            },
            subject.StatusFileIdentity("owner", "writer-session.json", "writer-session"),
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
def test_resolve_status_file_identity(environment: dict[str, str], expected: subject.StatusFileIdentity | None) -> None:
    """ルート・両backendの委譲先・識別不能な所有session・不正識別子を区別する。"""
    assert subject.resolve_status_file_identity(environment) == expected


def test_status_directory_uses_platform_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """状態ディレクトリをatk configと同じXDG規則から解決する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert subject.status_directory("root") == tmp_path / "agent-toolkit" / "agents-server" / "root"
    assert subject.notices_directory("root") == tmp_path / "agent-toolkit" / "agents-server" / "root" / "notices"
    assert subject.hosts_directory("root") == tmp_path / "agent-toolkit" / "agents-server" / "root" / "hosts"


def test_write_host_alias_resolves_writer_to_thread_id(tmp_path: pathlib.Path) -> None:
    """書込主体から起動元threadへの索引は形式を検証して保存する。"""
    subject.write_host_alias("root", "writer", "thread", tmp_path)

    assert json.loads((subject.hosts_directory("root", tmp_path) / "writer.json").read_text(encoding="utf-8")) == {
        "version": 1,
        "host_session_id": "thread",
    }
    with pytest.raises(ValueError, match="invalid session_id"):
        subject.write_host_alias("root", "bad/writer", "thread", tmp_path)


def test_resolve_status_owner_identity_uses_writer_alias(tmp_path: pathlib.Path) -> None:
    """Codex threadを内側MCPの書込主体へ逆引きする。"""
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}
    subject.write_host_alias("root", "writer", "thread", tmp_path)

    assert subject.resolve_status_owner_identity(environment, tmp_path) == subject.StatusFileIdentity(
        "root", "writer.json", "writer"
    )


def test_resolve_status_owner_identity_keeps_unindexed_identity(tmp_path: pathlib.Path) -> None:
    """索引が無い呼出主体は環境変数から解決した書込主体を維持する。"""
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}

    assert subject.resolve_status_owner_identity(environment, tmp_path) == subject.StatusFileIdentity(
        "root", "thread.json", "thread"
    )


def test_resolve_status_owner_identity_recovers_from_live_status_file(tmp_path: pathlib.Path) -> None:
    """索引が失われても、起動元threadを記録した状態から書込主体を一意に復元する。"""
    root = subject.status_directory("root", tmp_path)
    root.mkdir(parents=True)
    (root / "writer.json").write_text('{"version": 1, "host_session_id": "thread", "sessions": []}', encoding="utf-8")

    assert subject.resolve_status_owner_identity(
        {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}, tmp_path
    ) == subject.StatusFileIdentity("root", "writer.json", "writer")


def test_resolve_status_owner_identity_rejects_ambiguous_aliases(tmp_path: pathlib.Path) -> None:
    """同じthreadへ複数の書込主体が対応する索引を推測で選ばない。"""
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}
    subject.write_host_alias("root", "writer-a", "thread", tmp_path)
    subject.write_host_alias("root", "writer-b", "thread", tmp_path)

    with pytest.raises(ValueError, match="書込主体を一意に解決できません"):
        subject.resolve_status_owner_identity(environment, tmp_path)


@pytest.mark.asyncio
async def test_inner_writer_projects_parent_thread_id_into_host_session_id(tmp_path: pathlib.Path) -> None:
    """内側の3起動種別を親thread識別子へ射影して1つの状態ファイルへ集約する。"""
    identity = subject.resolve_status_file_identity(
        {"AGENT_TOOLKIT_OWNER_SESSION": "root", "AGENT_TOOLKIT_STATUS_HOST_SESSION": "writer"}
    )
    assert identity is not None
    sessions = {
        launch_kind: state.SessionState(
            f"{launch_kind}-session",
            str(tmp_path),
            launch_kind=launch_kind,
            announced=True,
        )
        for launch_kind in ("delegate", "explore", "shell")
    }
    writer = subject.StatusFileWriter(sessions, identity, state_root=tmp_path, aggregate_seconds=0)
    writer.activate()
    assert json.loads(writer.path.read_text(encoding="utf-8"))["host_session_id"] == "writer"

    subject.write_host_alias("root", "writer", "parent-thread", tmp_path)
    writer.flush()

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert payload["host_session_id"] == "parent-thread"
    assert len(payload["sessions"]) == 3
    assert writer.path in subject.list_status_files("root", tmp_path)
    writer.deactivate()


@pytest.mark.asyncio
async def test_api_error_record_reaches_status_file_without_activity(tmp_path: pathlib.Path) -> None:
    """API失敗の診断は活動時刻を変えず、CLIが読む状態ファイルへ届く。"""
    session = state.SessionState("claude-session", str(tmp_path), engine="claude", announced=True)
    session.updated_at = "2000-01-01T00:00:00+00:00"
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()

    session.record_api_error("rate_limit_error", 429)
    writer.flush()

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    recorded = payload["sessions"][0]
    assert recorded["updated_at"] == "2000-01-01T00:00:00+00:00"
    assert recorded["api_error"]["type"] == "rate_limit_error"
    assert recorded["api_error"]["http_status"] == 429
    assert recorded["api_error"]["count"] == 1
    writer.deactivate()


@pytest.mark.asyncio
async def test_hosts_entries_are_removed_after_retention(tmp_path: pathlib.Path) -> None:
    """保持期限を過ぎた書込主体索引をactivate時に回収する。"""
    host_path = subject.hosts_directory("root", tmp_path) / "writer.json"
    host_path.parent.mkdir(parents=True)
    host_path.write_text('{"version": 1, "host_session_id": "thread"}', encoding="utf-8")
    stale_at = datetime.datetime.now(datetime.UTC).timestamp() - state.RESULT_RETENTION_SECONDS - 1
    os.utime(host_path, (stale_at, stale_at))
    writer = subject.StatusFileWriter(
        {}, subject.StatusFileIdentity("root", "root.json", None), state_root=tmp_path, aggregate_seconds=0
    )

    writer.activate()

    assert not host_path.exists()
    assert not host_path.parent.exists()
    writer.deactivate()


@pytest.mark.asyncio
async def test_host_alias_outlives_uncollected_nested_result(tmp_path: pathlib.Path) -> None:
    """別書込主体の起動が古い索引を回収しても、複数turn後の結果の所有者を失わない。"""
    subject.write_host_alias("root", "writer", "thread", tmp_path)
    host_path = subject.hosts_directory("root", tmp_path) / "writer.json"
    stale_at = datetime.datetime.now(datetime.UTC).timestamp() - state.RESULT_RETENTION_SECONDS - 1
    os.utime(host_path, (stale_at, stale_at))
    results = subject.results_directory("root", tmp_path)
    results.mkdir(exist_ok=True)
    result_path = results / "nested.json"
    result_path.write_text(
        json.dumps({"status": "completed", "agent_message": "完了", "turn_seq": 3, "owner_status_file": "writer.json"}),
        encoding="utf-8",
    )
    other = subject.StatusFileWriter(
        {}, subject.StatusFileIdentity("root", "other.json", "other"), state_root=tmp_path, aggregate_seconds=0
    )

    other.activate()

    assert host_path.exists()
    assert subject.resolve_wait_identity(
        {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}, None, tmp_path
    ) == subject.StatusFileIdentity("root", "writer.json", "writer")
    assert subject.take_result("root", "nested", "writer.json", collector="test", state_root=tmp_path)[0] == {
        "status": "completed",
        "agent_message": "完了",
        "turn_seq": 3,
    }
    other.deactivate()
    assert not host_path.exists()


def test_status_directory_rejects_relative_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """相対XDG_STATE_HOMEではatk configと同じHOME配下へ状態を書き込む。"""
    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert subject.status_directory("root") == (
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
    directory = subject.status_directory("root", tmp_path)
    for file_name in file_names:
        path = directory / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    assert subject.list_status_files("root", tmp_path) == [directory / file_name for file_name in expected]


def test_take_notices_keeps_invalid_values_and_removes_ordered_valid_notices(tmp_path: pathlib.Path) -> None:
    """不正通知を保持し、正常通知だけを送信時刻とファイル名の順で回収する。"""
    directory = subject.notices_directory("root", tmp_path)
    directory.mkdir(parents=True)
    payloads = {
        "invalid-version.json": {"version": 2, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "本文"},
        "invalid-session.json": {"version": 1, "session_id": "other", "sent_at": "2026-09-07T01:00:00Z", "body": "本文"},
        "invalid-sent-at.json": {"version": 1, "session_id": "target", "sent_at": 1, "body": "本文"},
        "invalid-body.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": ["本文"]},
        "valid-late.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T02:00:00Z", "body": "後"},
        "valid-same-b.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "同時刻B"},
        "valid-same-a.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "同時刻A"},
    }
    for name, payload in payloads.items():
        (directory / name).write_text(json.dumps(payload), encoding="utf-8")

    notices = subject.take_notices("root", "target", tmp_path)

    assert notices == [
        {"sent_at": "2026-09-07T01:00:00Z", "body": "同時刻A"},
        {"sent_at": "2026-09-07T01:00:00Z", "body": "同時刻B"},
        {"sent_at": "2026-09-07T02:00:00Z", "body": "後"},
    ]
    assert {path.name for path in directory.iterdir()} == {
        "invalid-version.json",
        "invalid-session.json",
        "invalid-sent-at.json",
        "invalid-body.json",
    }


@pytest.mark.asyncio
async def test_writer_serializes_announced_sessions_and_removes_delivered(
    tmp_path: pathlib.Path,
) -> None:
    """公開済みで未回収のsessionだけを状態ファイルへ書く。"""
    sessions: dict[str, state.SessionState] = {}
    writer = subject.StatusFileWriter(
        sessions,
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    hidden = state.SessionState("hidden", str(tmp_path), announced=False)
    visible = state.SessionState(
        "visible",
        str(tmp_path),
        engine="claude",
        model="sonnet[1m]",
        effort="low",
        model_type="execute",
        launch_kind="delegate",
        label="実装",
        announced=True,
    )
    sessions.update(hidden=hidden, visible=visible)
    visible.set_progress("進捗")
    writer.flush()

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["host_session_id"] is None
    datetime.datetime.fromisoformat(payload["heartbeat_at"])
    assert [item["session_id"] for item in payload["sessions"]] == ["visible"]
    assert payload["sessions"][0]["progress"] == "進捗"
    datetime.datetime.fromisoformat(payload["sessions"][0]["started_at"])

    visible.status = "completed"
    visible.agent_message = "完了"
    visible.turn_completed = True
    visible.touch()
    writer.flush()
    result_path = subject.results_directory("root", tmp_path) / "visible.json"
    assert result_path.exists()
    result_path.unlink()
    writer.flush()
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert visible.result_delivered is True
    assert not result_path.exists()
    writer.deactivate()
    assert not writer.path.parent.exists()


@pytest.mark.asyncio
async def test_writer_removes_session_at_retention_deadline(tmp_path: pathlib.Path) -> None:
    """期限到達後はsession表示を除き、未回収の結果を保持する。"""
    session = state.SessionState("retained", str(tmp_path), announced=True, turn_seq=1)
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    session.retention_deadline = asyncio.get_running_loop().time() + 0.03
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()

    assert json.loads(writer.path.read_text(encoding="utf-8"))["sessions"][0]["session_id"] == "retained"
    result_path = subject.results_directory("root", tmp_path) / "retained.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["agent_message"] == "完了"
    assert result["turn_seq"] == 1
    datetime.datetime.fromisoformat(result["finalized_at"])
    assert writer._retention_handle is not None
    await asyncio.sleep(0.05)
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert result_path.exists()
    writer.deactivate()
    assert result_path.exists()


@pytest.mark.asyncio
async def test_writer_retains_result_without_live_session_after_deadline(tmp_path: pathlib.Path) -> None:
    """破棄済みsessionから保持した結果も期限到達後に維持する。"""
    session = state.SessionState("stopped", str(tmp_path), announced=True)
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    session.retention_deadline = asyncio.get_running_loop().time() + 0.03
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    writer.retain_result(session)
    writer.flush()
    result_path = subject.results_directory("root", tmp_path) / "stopped.json"
    assert result_path.exists()
    assert writer._retention_handle is None

    await asyncio.sleep(0.05)

    assert result_path.exists()
    writer.deactivate()
    assert result_path.exists()


@pytest.mark.asyncio
async def test_writer_removes_waited_result_at_retention_deadline(tmp_path: pathlib.Path) -> None:
    """waitで回収済みになった結果を次のflushで削除する。"""
    session = state.SessionState("waited", str(tmp_path), announced=True, turn_seq=1)
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    session.retention_deadline = asyncio.get_running_loop().time() + 0.03
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = agents_server_mcp.AgentsServerManager(writer)
    writer.activate()

    result_path = subject.results_directory("root", tmp_path) / "waited.json"
    assert (await _wait_now(manager))["agent_message"] == "完了"
    writer.flush()
    assert not result_path.exists()
    await manager.close()


@pytest.mark.asyncio
async def test_writer_excludes_already_expired_session(tmp_path: pathlib.Path) -> None:
    """再出力時点で保持期限を過ぎたsessionを表示対象から除く。"""
    session = state.SessionState("expired", str(tmp_path), announced=True)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    writer.deactivate()


@pytest.mark.asyncio
async def test_root_writer_removes_stale_files_on_activate(tmp_path: pathlib.Path) -> None:
    """ルートwriterは自身と保持期限切れの共有ファイルだけを除く。"""
    directory = subject.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    other_writer = directory / "other.json"
    other_writer.write_text("{}", encoding="utf-8")
    other_temporary = directory / ".other.json.token.tmp"
    other_temporary.write_text("temporary", encoding="utf-8")
    results = directory / "results"
    results.mkdir()
    stale_result = results / "stale-session.json"
    stale_result.write_text("{}", encoding="utf-8")
    retained_result = results / "retained-session.json"
    retained_result.write_text("{}", encoding="utf-8")
    notices = directory / "notices"
    notices.mkdir()
    stale_notice = notices / "stale-session.1.json"
    stale_notice.write_text("{}", encoding="utf-8")
    retained_notice = notices / "retained-session.1.json"
    retained_notice.write_text("{}", encoding="utf-8")
    stale_at = datetime.datetime.now(datetime.UTC).timestamp() - state.RESULT_RETENTION_SECONDS - 1
    for path in (stale_result, stale_notice):
        os.utime(path, (stale_at, stale_at))
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()
    assert other_writer.exists()
    assert other_temporary.exists()
    assert retained_result.exists()
    assert retained_notice.exists()
    assert stale_result.exists()
    assert not stale_notice.exists()
    writer.deactivate()
    assert other_writer.exists()
    assert other_temporary.exists()
    assert retained_result.exists()
    assert retained_notice.exists()
    assert stale_result.exists()


@pytest.mark.asyncio
async def test_writer_removes_state_file_with_expired_heartbeat(tmp_path: pathlib.Path) -> None:
    """生存の印が失効した他の状態ファイルを削除する。"""
    directory = subject.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    stale = directory / "stale.json"
    stale.write_text(
        json.dumps(
            {
                "heartbeat_at": (
                    datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=subject.HEARTBEAT_EXPIRY_SECONDS + 1)
                ).isoformat()
            }
        ),
        encoding="utf-8",
    )
    live = directory / "live.json"
    live.write_text(
        json.dumps({"heartbeat_at": datetime.datetime.now(datetime.UTC).isoformat()}),
        encoding="utf-8",
    )
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()

    assert not stale.exists()
    assert live.exists()
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_preserves_state_file_without_heartbeat(tmp_path: pathlib.Path) -> None:
    """旧形式の状態ファイルは他の書込主体が回収しない。"""
    directory = subject.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    legacy = directory / "legacy.json"
    legacy.write_text("{}", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()
    writer.flush()
    writer.deactivate()

    assert legacy.exists()


@pytest.mark.asyncio
async def test_manager_refreshes_heartbeat_until_close(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """managerは稼働中に生存の印を定期更新し、終了時に更新タスクを回収する。"""
    monkeypatch.setattr(subject, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = agents_server_mcp.AgentsServerManager(writer)
    manager.activate()
    initial = json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"]

    await asyncio.sleep(0.03)

    refreshed = json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"]
    assert refreshed > initial
    await manager.close()
    assert manager._heartbeat_task is None


@pytest.mark.asyncio
async def test_nested_writer_preserves_root_file_on_deactivate(tmp_path: pathlib.Path) -> None:
    """入れ子の書込主体は自身のファイルだけを回収する。"""
    directory = subject.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    root_file = directory / "root.json"
    root_file.write_text("{}", encoding="utf-8")
    notices = directory / "notices"
    notices.mkdir()
    notice_file = notices / "child.1.json"
    notice_file.write_text("{}", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "child.json", "child"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    writer.deactivate()
    assert root_file.exists()
    assert not writer.path.exists()
    assert notice_file.exists()


@pytest.mark.asyncio
async def test_manager_writes_three_launch_kinds_and_removes_waited_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """3つの起動入口と結果回収を状態ファイルへ反映する。"""
    writer = subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    manager._codex = backend
    _use_candidates(monkeypatch, ("codex", "gpt-5.6-terra", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()

    started = await manager.start("execute", "\n  実装を開始\n続き", str(tmp_path))
    await manager.start_explore(True, "調査する", str(tmp_path))
    await manager.start_shell("pytest -q", str(tmp_path), "結果を要約")
    writer.flush()
    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert [item["launch_kind"] for item in payload["sessions"]] == ["delegate", "explore", "shell"]
    assert [item["label"] for item in payload["sessions"]] == ["実装を開始", "explore", "shell-pytest"]

    session = manager.sessions[started["session_id"]]
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    await _wait_now(manager)
    writer.flush()
    result_path = subject.results_directory("root", tmp_path) / f"{session.session_id}.json"
    assert not result_path.exists()
    remaining = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert session.session_id not in {item["session_id"] for item in remaining}
    await manager.close()
    assert not subject.status_directory("root", tmp_path).exists()


@pytest.mark.asyncio
async def test_manager_removes_previous_result_when_new_turn_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じsessionの新しいturnを開始した時点で前の結果を削除する。"""
    writer = _status_writer(tmp_path)
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    manager._codex = backend
    _use_candidates(monkeypatch, ("codex", "model", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()
    started = await manager.start("execute", "実装", str(tmp_path))
    session = manager.sessions[started["session_id"]]
    session.status = "completed"
    session.agent_message = "前の結果"
    session.turn_completed = True
    session.touch()
    writer.flush()
    result_path = subject.results_directory("root", tmp_path) / f"{session.session_id}.json"
    assert result_path.exists()

    await manager.send_message(session.session_id, "続行")

    assert not result_path.exists()
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed", [False, True])
async def test_manager_writes_only_announced_candidate_after_fallback(
    delayed: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """候補切替で除外した試行を隠し、呼出元へ返したsessionだけを書く。"""
    writer = _status_writer(tmp_path)
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend: _FakeStatusBackend = (
        _DelayedUnavailableStatusBackend(
            manager.sessions,
            condition=manager._condition,
            unavailable_models={"first"},
        )
        if delayed
        else _UnavailableStatusBackend(manager.sessions, unavailable_models={"first"})
    )
    manager._codex = backend
    _use_candidates(monkeypatch, ("codex", "first", "high"), ("codex", "second", "high"))
    monkeypatch.setattr(agents_server_mcp, "START_AVAILABILITY_TIMEOUT", 0.01)
    writer.activate()

    response = await manager.start("execute", "実装", str(tmp_path))
    writer.flush()

    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [response["session_id"]]
    assert sessions[0]["model"] == "second"
    assert backend.release_calls == ["session-1"]
    await manager.close()


@pytest.mark.asyncio
async def test_manager_writes_only_last_failure_when_all_candidates_are_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """全候補が利用不能なら、応答へ載せた最後の失敗sessionだけを書く。"""
    writer = _status_writer(tmp_path)
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend = _UnavailableStatusBackend(manager.sessions, unavailable_models={"first", "second"})
    manager._codex = backend
    _use_candidates(monkeypatch, ("codex", "first", "high"), ("codex", "second", "high"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()

    response = await manager.start("execute", "実装", str(tmp_path))
    writer.flush()

    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [response["session_id"]]
    assert sessions[0]["model"] == "second"
    assert sessions[0]["status"] == "failed"
    assert backend.release_calls == ["session-1"]
    await manager.close()


@pytest.mark.asyncio
async def test_manager_removes_kill_result_but_keeps_uncollected_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """killで返した結果だけを除き、未回収の終端結果は表示に残す。"""
    writer = _status_writer(tmp_path)
    manager = agents_server_mcp.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    manager._codex = backend
    _use_candidates(monkeypatch, ("codex", "model", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()
    killed = await manager.start("execute", "kill対象", str(tmp_path))
    uncollected = await manager.start("execute", "未回収", str(tmp_path))
    for session_id in (killed["session_id"], uncollected["session_id"]):
        session = manager.sessions[session_id]
        session.status = "completed"
        session.agent_message = "完了"
        session.turn_completed = True
        session.touch()

    response = await manager.kill(killed["session_id"], timeout=0)
    writer.flush()

    assert response["agent_message"] == "完了"
    results = subject.results_directory("root", tmp_path)
    assert not (results / f"{killed['session_id']}.json").exists()
    assert (results / f"{uncollected['session_id']}.json").exists()
    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [uncollected["session_id"]]
    await manager.close()


@pytest.mark.asyncio
async def test_manager_without_writer_does_not_create_status_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """書込主体が無効なmanagerはsession開始後も状態ファイルを作成しない。"""
    manager = agents_server_mcp.AgentsServerManager(None)
    manager._codex = _FakeStatusBackend(manager.sessions)
    _use_candidates(monkeypatch, ("codex", "model", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))

    await manager.start("execute", "実装", str(tmp_path))

    assert not list(tmp_path.rglob("*.json"))
    await manager.close()


def _use_candidates(monkeypatch: pytest.MonkeyPatch, *candidates: tuple[str, str, str]) -> None:
    """session開始時に解決するモデル候補列を固定する。"""
    monkeypatch.setattr(
        agents_server_mcp._atk_config, "parse_unresolved_model_candidates", lambda _model_type: list(candidates)
    )


def _status_writer(tmp_path: pathlib.Path) -> subject.StatusFileWriter:
    """公開manager経路用のルートwriterを返す。"""
    return subject.StatusFileWriter(
        {},
        subject.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )


class _FakeStatusBackend:
    """状態ファイルの公開フローだけを通す偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState]) -> None:
        self.sessions = sessions
        self.count = 0
        self.release_calls: list[str] = []

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        self.count += 1
        session = state.SessionState(
            f"session-{self.count}",
            cwd,
            model=model,
            effort=effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            turn_seq=1,
        )
        self.sessions[session.session_id] = session
        state._initialize_turn(session)
        return session

    async def close(self) -> None:
        """外部資源を持たないため何もしない。"""

    async def release_session(self, session_id: str) -> None:
        """解放対象を検証用に記録する。"""
        self.release_calls.append(session_id)

    async def send_message(self, session: state.SessionState, _prompt: str) -> dict[str, object]:
        """新しいreply turnを開始する。"""
        session.turn_seq += 1
        state._initialize_turn(session)
        return {"delivery": "reply_started", "previous_result": None}


class _UnavailableStatusBackend(_FakeStatusBackend):
    """指定モデルをengine利用不能として終端させる偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], *, unavailable_models: set[str]) -> None:
        super().__init__(sessions)
        self._unavailable_models = unavailable_models

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        session = await super().start(
            _prompt,
            cwd,
            model,
            effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        if session.model in self._unavailable_models:
            session.status = "failed"
            session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
            session.turn_completed = True
            session.touch()
        return session


class _DelayedUnavailableStatusBackend(_FakeStatusBackend):
    """起動応答後に指定モデルを利用不能として終端させる偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        *,
        condition: asyncio.Condition,
        unavailable_models: set[str],
    ) -> None:
        super().__init__(sessions)
        self._condition = condition
        self._unavailable_models = unavailable_models
        self._pending: list[asyncio.Task[None]] = []

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        session = await super().start(
            _prompt,
            cwd,
            model,
            effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        if session.model in self._unavailable_models:
            self._pending.append(asyncio.create_task(self._fail_after_response(session)))
        return session

    async def _fail_after_response(self, session: state.SessionState) -> None:
        await asyncio.sleep(0)
        session.status = "failed"
        session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
        session.turn_completed = True
        session.touch()
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        await asyncio.gather(*self._pending)


def test_take_result_checks_owner_and_consumes_once(tmp_path: pathlib.Path) -> None:
    """異なる書込主体は結果を取得できず、正しい主体への配送は1回だけ成立する。"""
    directory = subject.results_directory("root-session", tmp_path)
    directory.mkdir(parents=True)
    result_path = directory / "child-session.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "agent_message": "完了",
                "owner_status_file": "delegate.json",
            }
        ),
        encoding="utf-8",
    )

    assert subject.take_result(
        "root-session",
        "child-session",
        "root.json",
        collector="test",
        state_root=tmp_path,
    ) == (None, None)
    assert result_path.exists()
    assert subject.take_result(
        "root-session",
        "child-session",
        "delegate.json",
        collector="test",
        state_root=tmp_path,
    ) == ({"status": "completed", "agent_message": "完了"}, None)
    assert subject.take_result(
        "root-session",
        "child-session",
        "delegate.json",
        collector="test",
        state_root=tmp_path,
    ) == (None, None)


def test_take_result_keeps_other_owner_result(tmp_path: pathlib.Path) -> None:
    """CLI用退避先を指定しても別の書込主体の結果は移動しない。"""
    result_path = subject.results_directory("root-session", tmp_path) / "child-session.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({"status": "completed", "owner_status_file": "delegate.json"}), encoding="utf-8")
    stash_path = tmp_path / "wait-run" / "results" / "child-session.json"
    writer = subject.StatusFileWriter({}, subject.StatusFileIdentity("root-session", "root.json", None), state_root=tmp_path)

    assert subject.take_result(
        "root-session", "child-session", "root.json", collector="cli", state_root=tmp_path, stash_path=stash_path
    ) == (None, None)
    assert not stash_path.exists()
    assert result_path.exists()
    assert writer.take_result("child-session", collector="mcp-wait") == (None, None)
    assert result_path.exists()

    owner = subject.StatusFileWriter(
        {}, subject.StatusFileIdentity("root-session", "delegate.json", "delegate"), state_root=tmp_path
    )
    assert owner.take_result("child-session", collector="mcp-wait") == ({"status": "completed"}, None)
    assert not result_path.exists()
