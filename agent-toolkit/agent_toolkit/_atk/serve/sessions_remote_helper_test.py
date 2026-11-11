"""serveのsessionsに関するremote_helperの試験。"""

# pylint: disable=protected-access

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import typing

import pytest

from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import sessions, sessions_local
from agent_toolkit._testing import session_tree
from agent_toolkit._testing.serve_sessions_support import (
    _age,
    _context,
    _count_record_opens,
    _helper_list,
    _helper_runner,
    _load_remote_helper,
    _no_user_corpus,
    _runner_returning,
    _write,
)


def test_remote_helper_listing_returns_cwd_and_first_user_message(tmp_path: pathlib.Path) -> None:
    """リモート補助の一覧もローカル側と同じ識別項目を返す。"""
    claude = _write(
        tmp_path / ".claude" / "projects" / "encoded-path" / "remote-claude.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-09-05T00:00:00Z",
                "cwd": "/remote/claude",
                "message": {"content": "Claude先頭\n続き"},
            }
        ],
    )
    codex_home = tmp_path / "codex-home"
    codex = _write(
        codex_home / "sessions" / "2026" / "09" / "05" / "rollout-remote-codex.jsonl",
        [
            {
                "type": "session_meta",
                "payload": {"cwd": "/remote/codex", "timestamp": "2026-09-06T00:00:00Z"},
            },
            {"type": "response_item", "payload": {"role": "user", "content": [{"text": "Codex先頭\n続き"}]}},
        ],
    )
    os.utime(claude, (1_900_000_000, 1_900_000_000))
    os.utime(codex, (1_700_000_000, 1_700_000_000))
    environment = os.environ.copy()
    environment.update({"HOME": str(tmp_path), "CODEX_HOME": str(codex_home)})

    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"),
            "list",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    entries = {entry["path"]: entry for entry in json.loads(result.stdout)["entries"]}

    assert entries[str(claude)]["cwd"] == "/remote/claude"
    assert entries[str(claude)]["first_user_message"] == "Claude先頭"
    assert entries[str(claude)]["started_at"] == "2026-09-05T00:00:00Z"
    assert entries[str(codex)]["cwd"] == "/remote/codex"
    assert entries[str(codex)]["first_user_message"] == "Codex先頭"
    assert entries[str(codex)]["started_at"] == "2026-09-06T00:00:00Z"
    assert [entry["path"] for entry in json.loads(result.stdout)["entries"]] == [str(codex), str(claude)]
    assert all("project" not in entry for entry in entries.values())


def test_remote_helper_listing_links_recorded_delegation(tmp_path: pathlib.Path) -> None:
    """リモート補助も起動結果に現れる実在する子を親へ結ぶ。"""
    project = tmp_path / ".claude" / "projects" / "repo"
    parent = _write(
        project / "parent.jsonl",
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "mcp__agents_server__start", "id": "call"}]},
            },
            {
                "type": "user",
                "toolUseResult": {"session_id": "child"},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "content": "起動"}]},
            },
        ],
    )
    child = _write(project / "child.jsonl", [{"type": "user", "message": {"content": "子"}}])
    environment = os.environ.copy()
    environment.update({"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / "codex")})

    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"),
            "list",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    entries = {entry["path"]: entry for entry in json.loads(result.stdout)["entries"]}

    assert entries[str(child)]["parent_path"] == str(parent)


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True])
async def test_local_and_remote_detail_use_same_family_cost(
    tmp_path: pathlib.Path, host_environ: typing.Callable[[], dict[str, str]], unknown: bool
) -> None:
    """実際のremote helperのreadを経由し、親算出額・native子・委譲先・未知単価を同じ式で返す。"""
    parent = tmp_path / ".claude/projects/proj/parent.jsonl"
    identity = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    records = [
        {"type": "user", "timestamp": "2026-10-20T12:00:00Z", "cwd": "/work", "message": {"role": "user", "content": "依頼"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "launch", "name": "mcp__agents_server__start", "input": {}}],
            },
        },
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "launch", "content": "完了"}]},
            "toolUseResult": {"session_id": identity},
        },
        {"type": "cost-state", "timestamp": "2026-10-20T12:01:00Z", "totalCostUSD": 5},
    ]
    records.extend(
        [
            {
                "type": "api-request-blob",
                "timestamp": "2026-10-20T12:01:01Z",
                "message": {
                    "id": "request-copy",
                    "model": "claude-opus-5-5",
                    "usage": {"input_tokens": 1000, "output_tokens": 100},
                },
            },
            {
                "type": "api-request-blob",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "launch", "content": "写し"}]},
                "toolUseResult": {"session_id": "not-a-launched-session"},
            },
        ]
    )
    if unknown:
        records.append(
            {
                "type": "assistant",
                "timestamp": "2026-10-20T12:02:00Z",
                "message": {"id": "future", "model": "future-model", "usage": {"input_tokens": 10, "output_tokens": 20}},
            }
        )
    _write(parent, records)
    _write(
        parent.with_suffix("") / "subagents/agent-sub.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-10-20T12:00:01Z",
                "message": {"id": "sub", "model": "claude-opus-5-5", "usage": {"input_tokens": 1000, "output_tokens": 100}},
            },
        ],
    )
    _write(
        tmp_path / f"codex/sessions/2026/10/20/rollout-child-{identity}.jsonl",
        [
            {"type": "session_meta", "payload": {"id": identity}},
            {"type": "turn_context", "payload": {"model": "gpt-6-sol"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"input_tokens": 100, "output_tokens": 50},
                        "total_token_usage": {"total_tokens": 150},
                    },
                },
            },
        ],
    )
    environment = host_environ()
    environment.update(
        {
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "CODEX_HOME": str(tmp_path / "codex"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "LOCALAPPDATA": str(tmp_path / "state"),
        }
    )
    runner = _helper_runner(environment, only_op="read")

    context = sessions.create_context(
        hostname="local",
        claude_home=tmp_path / ".claude",
        codex_home=tmp_path / "codex",
        state_dir=tmp_path / "state/agent-toolkit",
        remote_hosts=["remote"],
        ssh_runner=runner,
    )
    local_detail = sessions.read_local_detail(context, "claude", str(parent))
    assert local_detail["cost"] is None
    assert local_detail["cost_pending"] is True
    local = await sessions.session_cost(context, "claude", "local", str(parent))
    remote = await sessions.session_detail(context, "claude", "remote", str(parent))
    assert remote["cost"] == local
    assert local["known_usd"] == pytest.approx(5.0007)
    assert not local["incomplete"]
    assert local["estimated_usd"] == (None if unknown else pytest.approx(5.0007))
    assert local["unknown_responses"] == int(unknown)
    assert local["breakdown"]["claude_subagents"]["known_usd"] == 0
    with pytest.raises(sessions.SessionNotFoundError):
        await sessions.session_cost(context, "claude", "remote", str(parent))


def test_local_and_remote_listing_link_children_from_every_launch_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """ローカルの一覧とリモート補助が、親子の全情報源と件数上限で外れた親について同じ親子を返す。

    実行系の異なる親子、Codexの親thread、登録簿の委譲元だけで親が決まる委譲先が親を持たないと、
    画面の第1階層へ並ぶ。親の記録に起動結果がある委譲先では、登録簿の委譲元より起動結果を優先する。
    件数上限で外れた親を戻さないと、その子が親を持たない項目として並ぶ。
    """
    state_dir = tmp_path / "state" / "agent-toolkit"
    tree = session_tree.write_session_tree(tmp_path / ".claude", tmp_path / "codex", state_dir)
    monkeypatch.setattr(sessions_local, "MAX_LIST_ENTRIES", session_tree.TOTAL_ENTRIES - 1)
    context = sessions.create_context(
        hostname="local-host", claude_home=tmp_path / ".claude", codex_home=tmp_path / "codex", state_dir=state_dir
    )

    local = sessions.list_local_sessions(context)

    local_parents = {entry.path: entry.parent_path for entry in local if entry.parent_path is not None}
    assert local_parents == tree.expected_parents
    assert len(local) == session_tree.TOTAL_ENTRIES
    assert str(tree.paths[session_tree.OLD_PARENT_ID]) in {entry.path for entry in local}

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir.parent))
    helper = _load_remote_helper()
    monkeypatch.setattr(helper, "MAX_LIST_ENTRIES", session_tree.TOTAL_ENTRIES - 1)
    remote = helper._list_payload()["entries"]
    remote_parents = {entry["path"]: entry["parent_path"] for entry in remote if entry.get("parent_path")}
    assert remote_parents == tree.expected_parents
    assert len(remote) == session_tree.TOTAL_ENTRIES
    assert all("codex_parent_thread_id" not in entry for entry in remote)


def test_remote_helper_applies_the_same_exclusion_as_the_local_listing(tmp_path: pathlib.Path) -> None:
    """リモート補助の一覧も同じ基準で除外し、ローカルの一覧と同じ記録の集合を返す。"""
    corpus = _no_user_corpus(tmp_path)
    context = sessions.create_context(
        hostname="local-host", claude_home=tmp_path / ".claude", codex_home=tmp_path / "codex-home"
    )

    remote = {entry["path"] for entry in _helper_list(tmp_path)}
    local = {entry.path for entry in sessions.list_local_sessions(context)}

    assert remote == local == {str(corpus["spoken"]), str(corpus["codex_spoken"])}
    assert all("has_user_message" not in entry for entry in _helper_list(tmp_path))


@pytest.mark.asyncio
async def test_entries_from_an_old_remote_helper_are_not_excluded(tmp_path: pathlib.Path) -> None:
    """判定材料を返さない旧版のリモート補助の項目は、最初の発話がnullでも除外しない。"""
    runner, _ = _runner_returning(
        {"ok": True, "entries": [{"engine": "codex", "path": "/r/rollout-x.jsonl", "first_user_message": None}]}
    )
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    entries, _warnings = await sessions.list_sessions(context)

    assert [(entry.host, entry.path) for entry in entries] == [("remote-host", "/r/rollout-x.jsonl")]


def test_remote_helper_is_started_with_watchdog_and_platformdirs() -> None:
    """リモート補助は変更監視に使うwatchdogと、登録簿の状態ディレクトリの解決に使うplatformdirsを伴って起動する。"""
    argv = _atk_serve_remote.remote_command_argv(sessions.REMOTE_BOOTSTRAP, "serve", [])

    with_values = [argv[index + 1] for index, value in enumerate(argv) if value == "--with"]
    assert with_values == ['"watchdog>=6.0.0,!=7.0.0"', '"platformdirs>=4.0"']


def test_remote_helper_serve_mode_reuses_unchanged_records(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """リモートヘルパーの常駐モードは、同じプロセスの2回目の`list`要求で変更のない記録を開き直さない。"""
    home = tmp_path / "home"
    record = _write(
        home / ".claude" / "projects" / "proj" / "session.jsonl",
        [{"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "やあ"}}],
    )
    _age(record)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    helper_path = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"
    spec = importlib.util.spec_from_file_location("_atk_serve_sessions_remote_helper_under_test", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    def no_watch() -> tuple[None, typing.Any]:
        return None, None

    monkeypatch.setattr(helper, "_start_watch", no_watch)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"id": 1, "op": "list"}\n{"id": 2, "op": "list"}\n'))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    opened = _count_record_opens(monkeypatch)

    assert helper._serve() == 0

    responses = [json.loads(line) for line in output.getvalue().splitlines() if '"response"' in line]
    assert [response["id"] for response in responses] == [1, 2]
    assert responses[0]["entries"] == responses[1]["entries"]
    assert [entry["path"] for entry in responses[1]["entries"]] == [str(record)]
    # 初回のメタ情報走査と表示本文取得だけが開き、2回目の要求は開かない。
    assert opened == [record, record]


@pytest.mark.asyncio
async def test_remote_detail_session_name_follows_local_rules(
    tmp_path: pathlib.Path, host_environ: typing.Callable[[], dict[str, str]]
) -> None:
    """実際のremote helperのreadを経由し、リモートの記録にもローカルと同じ規則でセッション名を付ける。

    Claude Codeは記録本文の`custom-title`から、Codexはリモートホストの`session_index.jsonl`からヘルパーが返す
    `thread_name`から得る。
    """
    remote_home = tmp_path / "remote-home"
    codex_home = remote_home / "codex"
    claude = _write(
        remote_home / ".claude" / "projects" / "proj" / "remote-claude.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-05T00:00:00Z", "cwd": "/remote", "message": {"content": "やあ"}},
            {"type": "ai-title", "aiTitle": "生成題名"},
            {"type": "custom-title", "customTitle": "リモートの名前"},
        ],
    )
    codex = _write(
        codex_home
        / "sessions"
        / "2026"
        / "09"
        / "05"
        / "rollout-2026-09-05T00-00-00-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl",
        [
            {"type": "session_meta", "payload": {"cwd": "/remote", "timestamp": "2026-09-05T00:00:00Z"}},
            {"type": "response_item", "payload": {"role": "user", "content": [{"text": "やあ"}]}},
        ],
    )
    unnamed = _write(
        codex_home
        / "sessions"
        / "2026"
        / "09"
        / "05"
        / "rollout-2026-09-05T00-00-01-ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl",
        [{"type": "response_item", "payload": {"role": "user", "content": [{"text": "名前なし"}]}}],
    )
    _write(
        codex_home / "session_index.jsonl",
        [
            {
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "thread_name": "リモートのthread",
                "updated_at": "2026-09-05T00:00:00Z",
            }
        ],
    )
    environment = host_environ()
    environment.update({"HOME": str(remote_home), "USERPROFILE": str(remote_home), "CODEX_HOME": str(codex_home)})
    environment.pop("CLAUDE_CONFIG_DIR", None)
    context = _context(tmp_path, remote_hosts=["remote"], ssh_runner=_helper_runner(environment))

    claude_detail = await sessions.session_detail(context, "claude", "remote", str(claude))
    codex_detail = await sessions.session_detail(context, "codex", "remote", str(codex))
    unnamed_detail = await sessions.session_detail(context, "codex", "remote", str(unnamed))

    assert claude_detail["session_name"] == "リモートの名前"
    assert codex_detail["session_name"] == "リモートのthread"
    assert unnamed_detail["session_name"] is None
    assert "project" not in codex_detail
