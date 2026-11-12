"""commandsのlistの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import argparse
import json
import os
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import commands, shared_layout, shared_roots
from agent_toolkit._atk import environment
from agent_toolkit._common import session_activity, session_registry, state_paths
from agent_toolkit._testing.agents_server_commands_support import _write_root_sessions


@pytest.mark.usefixtures("session_environment")
def test_agents_list_returns_diagnostic_fields_without_prompt(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """listは稼働と稼働時間に使う項目へ限定し、内部で追加した項目と詳細診断を返さない。"""
    monkeypatch.setenv("AI_AGENT", "1")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    session = payload["sessions"][0]
    assert session == {
        "session_id": "session-1",
        "status": "running",
        "label": "調査レーン",
        "engine": "codex",
        "model": "model",
        "effort": "medium",
        "last_action": "Bash: git status",
        "created_at": "2026-09-12T00:00:00+00:00",
        "started_at": "2026-09-13T00:00:00+00:00",
        "result_available": False,
        "seconds_since_activity": session["seconds_since_activity"],
    }


@pytest.mark.usefixtures("session_environment")
def test_agents_list_shows_tree_outside_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人間が読む環境ではrootとsessionの関係を表示し、委譲プロンプトを除く。"""
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    output = capsys.readouterr().out
    assert len(output.splitlines()) > 1
    assert "root root-session" in output
    assert "└─ session-1" in output
    assert "調査レーン" in output
    assert "調査せよ" not in output
    assert "running" in output


def test_agents_list_without_conversation_root_shows_all_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話識別子のない直接端末では全rootのsessionを表示する。"""
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    for root_session_id, remote_session_id in (("root-a", "session-a"), ("root-b", "session-b")):
        directory = shared_layout.status_directory(root_session_id, tmp_path)
        directory.mkdir(parents=True)
        (directory / "root.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "sessions": [
                        {
                            "session_id": remote_session_id,
                            "status": "running",
                            "started_at": "2026-09-14T00:00:00+00:00",
                            "updated_at": "2026-09-14T00:00:00+00:00",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    output = capsys.readouterr().out
    assert "root root-a" in output
    assert "root root-b" in output
    assert "session-a" in output
    assert "session-b" in output


@pytest.mark.parametrize("watch", [False, True])
def test_agents_list_shows_living_childless_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], watch: bool
) -> None:
    """SessionStartだけのrootを表示し、PID終了・再利用では表示から外す。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    session_activity.record_activity(
        {"session_id": "root-alone", "hook_event_name": "SessionStart"},
        process=session_activity.HostProcess("codex", identity),
        state_root=tmp_path,
    )
    parser = argparse.ArgumentParser()
    commands.build_parser(parser)
    args = parser.parse_args(["list", *(["--watch"] if watch else [])])

    def stop_watch(_seconds: float, **_kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(commands.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(commands.time, "sleep", stop_watch)
    assert commands.dispatch(args, environment={}) == 0
    assert "root root-alone" in capsys.readouterr().out
    for process in (
        session_registry.ProcessIdentity(identity.pid, identity.create_time - 1, "other-generation"),
        session_registry.ProcessIdentity(2147483647, 1),
    ):
        session_activity.record_activity(
            {"session_id": "root-alone", "hook_event_name": "SessionStart"},
            process=session_activity.HostProcess("codex", process),
            state_root=tmp_path,
        )
        assert commands.dispatch(args, environment={}) == 0
        assert "root root-alone" not in capsys.readouterr().out


def test_agents_list_human_tree_omits_empty_roots_without_liveness_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """生存記録のない空rootと管理用ディレクトリを省き、終了履歴の指定を保つ。"""
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    timestamps = {"started_at": "2026-09-30T00:00:00+00:00", "updated_at": "2026-09-30T00:00:00+00:00"}
    _write_root_sessions(tmp_path, "root-running", [{"session_id": "session-running", "status": "running", **timestamps}])
    _write_root_sessions(tmp_path, "root-empty", [])
    _write_root_sessions(tmp_path, "root-done", [{"session_id": "session-done", "status": "completed", **timestamps}])
    base = tmp_path / "agents-server"
    for reserved in ("aliases", "compaction", "sessions"):
        (base / reserved).mkdir()
        (base / reserved / "entry.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])
    default_output = capsys.readouterr().out
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list", "--include-terminated"])
    terminated_output = capsys.readouterr().out

    headings = {line for line in default_output.splitlines() if line.startswith("root ")}
    assert headings == {"root root-running"}
    headings = {line for line in terminated_output.splitlines() if line.startswith("root ")}
    assert headings == {"root root-running", "root root-done"}
    assert "session-done" in terminated_output


def test_agents_list_human_tree_reports_no_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人の端末で表示するsessionが無ければ見出しを出力せず、sessionが無いことを示して正常終了する。"""
    for key in ("CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    _write_root_sessions(tmp_path, "root-empty", [])
    (tmp_path / "agents-server" / "aliases").mkdir()

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    assert capsys.readouterr().out == "sessionはありません\n"


def test_agents_list_reports_unconfirmed_conversation_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """rootとの対応を確認できない空一覧は成功として扱わず、MCP一覧による復旧を案内する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents", "list"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "CLIが解決したroot=current-session" in captured.err
    assert "MCPの`list`を1回" in captured.err
    assert "`atk agents list`を再実行" in captured.err


def test_agents_list_returns_empty_for_confirmed_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """現行識別子自身の状態ディレクトリを確認できれば空一覧を通常結果として返す。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    shared_layout.status_directory("root-session", tmp_path).mkdir(parents=True)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    assert json.loads(capsys.readouterr().out) == {"sessions": []}


def test_agents_list_uses_explicit_alias_and_isolates_other_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """明示的な別名索引のrootだけを読み、別rootのsessionを混在させない。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    for root_session_id, remote_session_id in (("root-a", "session-a"), ("root-b", "session-b")):
        directory = shared_layout.status_directory(root_session_id, tmp_path)
        directory.mkdir(parents=True)
        (directory / "root.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "sessions": [{"session_id": remote_session_id, "status": "running"}],
                }
            ),
            encoding="utf-8",
        )
    shared_roots.write_root_alias("current-session", "root-a", tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert [session["session_id"] for session in payload["sessions"]] == ["session-a"]
