"""agent-toolkit/agent_toolkit/_hooks/posttooluse.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
plan file形式検査・SSOT検査・codex-review.md読み込み追跡は`posttooluse_plan_format_test.py`、
`session_edited_files`蓄積機構は`posttooluse_session_edited_files_test.py`へ分割している。
"""

# pylint: disable=protected-access

import asyncio
import functools
import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import types
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import agents_server_mcp
from agent_toolkit._agents_server import agents_wait
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE, _read_state, auto_message_opening_attributes

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"
_POSTTOOLUSE_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "posttooluse.py"
_HOOKS_JSON_PATH = pathlib.Path(__file__).resolve().parents[2] / "hooks" / "hooks.json"
_HOOKS_CODEX_JSON_PATH = pathlib.Path(__file__).resolve().parents[2] / "hooks" / "hooks.codex.json"


@functools.cache
def _load_posttooluse_module() -> types.ModuleType:
    """`scripts/posttooluse.py`を`importlib`で動的にインポートする。

    PostToolUseの内部dispatchと補助機構を直接呼ぶテストで使う。
    引数注入では到達不能なモジュール内部関数の単体検査のため、importlibによる直接参照を例外的に許容する。
    `_SCRIPT`（`hook.py`、サブプロセス起動用）とは別に本体ファイルのパスを参照する。
    """
    spec = importlib.util.spec_from_file_location("posttooluse", _POSTTOOLUSE_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# モジュールレベルでキャッシュ済みモジュールを参照し、引数注入では到達不能な内部関数を直接検査する。
_POSTTOOLUSE_MODULE = _load_posttooluse_module()


def test_kill_observation_attempt_clears_only_the_requested_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """中断の観測試行は入力のsessionだけを解消する。"""
    state = {
        "agents_server_sessions": {
            "remote-a": {"pending_observation": True, "owner_agent_id": "main"},
            "remote-b": {"pending_observation": True, "owner_agent_id": "main"},
        }
    }

    def apply(_session_id: str, mutator: Callable[[dict[str, Any]], object]) -> None:
        mutator(state)

    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", apply)
    _POSTTOOLUSE_MODULE._record_agents_server_observation_attempt("local", {"session_id": "remote-a"}, operation="kill")

    assert state["agents_server_sessions"]["remote-a"]["pending_observation"] is False
    assert state["agents_server_sessions"]["remote-b"]["pending_observation"] is True


def test_start_state_record_writes_conversation_root_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """start応答が明示したルート識別子の索引を書く。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)
    _POSTTOOLUSE_MODULE._agents_server_status_file.status_directory("root-session", tmp_path).mkdir(parents=True)

    _POSTTOOLUSE_MODULE._record_agents_server_root_alias(
        "current-session",
        {"root_session_id": "root-session"},
    )

    alias = tmp_path / "agents-server" / "aliases" / "current-session.json"
    assert json.loads(alias.read_text(encoding="utf-8")) == {"version": 1, "root_session_id": "root-session"}


def test_start_state_record_without_shared_status_does_not_write_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """応答が所有rootを明示しない場合は索引を書かない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)

    _POSTTOOLUSE_MODULE._record_agents_server_root_alias(
        "current-session",
        {"session_id": "remote-session"},
    )

    assert not (tmp_path / "agents-server" / "aliases" / "current-session.json").exists()


def test_root_alias_uses_explicit_response_without_scanning_other_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じ子識別子を持つ別rootがあっても応答の所有rootだけへ対応付ける。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)
    for root_session_id in ("root-a", "root-b"):
        root = _POSTTOOLUSE_MODULE._agents_server_status_file.status_directory(root_session_id, tmp_path)
        root.mkdir(parents=True)
        (root / "root.json").write_text(
            json.dumps({"version": 1, "sessions": [{"session_id": "remote-session"}]}),
            encoding="utf-8",
        )

    _POSTTOOLUSE_MODULE._record_agents_server_root_alias(
        "current-session",
        {"root_session_id": "root-b"},
    )

    alias = tmp_path / "agents-server" / "aliases" / "current-session.json"
    assert json.loads(alias.read_text(encoding="utf-8"))["root_session_id"] == "root-b"


def test_list_response_writes_conversation_root_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """空のlist応答でも明示された所有rootを現行会話へ対応付ける。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)
    _POSTTOOLUSE_MODULE._agents_server_status_file.status_directory("root-session", tmp_path).mkdir(parents=True)
    payload = {
        "session_id": "current-session",
        "cwd": str(tmp_path),
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__list",
        "tool_input": {},
        "tool_response": {"structuredContent": {"sessions": [], "omitted": 0, "root_session_id": "root-session"}},
    }

    assert _POSTTOOLUSE_MODULE.main(json.dumps(payload)) == 0

    alias = tmp_path / "agents-server" / "aliases" / "current-session.json"
    assert json.loads(alias.read_text(encoding="utf-8")) == {"version": 1, "root_session_id": "root-session"}


@pytest.mark.parametrize(
    ("operation", "structured"),
    [
        ("start", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("start_custom", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("start_explore", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("start_write", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("start_shell", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("send_message", {"delivery": "reply_started"}),
        ("send_message", {"delivery": "reply_ambiguous"}),
    ],
)
def test_start_and_reply_register_wait_target_for_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    operation: str,
    structured: dict[str, object],
) -> None:
    """開始又は再開したsessionを、PostToolUseの呼出主体の待機対象へ登録する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)
    tool_input: dict[str, object] = {"session_id": "remote-session"}
    if operation in _POSTTOOLUSE_MODULE._AGENTS_SERVER_START_OPERATIONS:
        tool_input = {"prompt": "委譲する", "cwd": str(tmp_path)}

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "root-session",
                "cwd": str(tmp_path),
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": tool_input,
                "tool_response": {"structuredContent": structured},
            }
        )
    )

    retained, error = _POSTTOOLUSE_MODULE._agents_server_status_file.read_wait_targets(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert exit_code == 0
    assert retained == {"remote-session"}
    assert error is None


def test_steer_does_not_register_wait_target(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """既存turnへ追送するsteerは、新しい待機対象として登録しない。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "root-session",
                "cwd": str(tmp_path),
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": "remote-session", "prompt": "補足する"},
                "tool_response": {"structuredContent": {"delivery": "steered"}},
            }
        )
    )

    retained, error = _POSTTOOLUSE_MODULE._agents_server_status_file.read_wait_targets(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert exit_code == 0
    assert retained == set()
    assert error is None


def test_start_registers_wait_target_for_delegated_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """委譲先が開始したsessionを、その委譲先の状態ファイルにだけ登録する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "delegate-session")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "delegate-session",
                "cwd": str(tmp_path),
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
                "tool_response": {"structuredContent": {"session_id": "remote-session", "status": "running"}},
            }
        )
    )

    retained, error = _POSTTOOLUSE_MODULE._agents_server_status_file.read_wait_targets(
        "root-session",
        "delegate-session.json",
        tmp_path,
    )
    assert exit_code == 0
    assert retained == {"remote-session"}
    assert error is None
    assert not (
        _POSTTOOLUSE_MODULE._agents_server_status_file.wait_targets_directory(
            "root-session",
            "root.json",
            tmp_path,
        )
    ).exists()


def _run_codex_delegate_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    operation: str,
    tool_input: dict[str, object],
    structured: dict[str, object],
) -> int:
    """分離したCodex threadのPostToolUseをテスト用共有状態で実行する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    for name in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_STATUS_HOST_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_THREAD_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)
    tool_input = dict(tool_input)
    if operation == "start":
        tool_input["cwd"] = str(tmp_path)
    return _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "codex-thread",
                "cwd": str(tmp_path),
                "tool_name": f"mcp__agents_server__{operation}",
                "tool_input": tool_input,
                "tool_response": {"structuredContent": structured},
            }
        )
    )


@pytest.mark.parametrize(
    ("operation", "tool_input", "structured"),
    [
        ("start", {"prompt": "委譲する"}, {"session_id": "remote-session", "status": "running"}),
        ("send_message", {"session_id": "remote-session", "prompt": "再開する"}, {"delivery": "reply_started"}),
    ],
)
def test_codex_delegate_hook_session_registers_wait_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    operation: str,
    tool_input: dict[str, object],
    structured: dict[str, object],
) -> None:
    """Codex委譲先はhook入力のthreadから内側MCPの書込主体へ待機対象を登録する。"""
    _POSTTOOLUSE_MODULE._agents_server_status_file.write_host_alias("root-session", "writer-session", "codex-thread", tmp_path)
    exit_code = _run_codex_delegate_hook(monkeypatch, tmp_path, operation, tool_input, structured)

    retained, error = _POSTTOOLUSE_MODULE._agents_server_status_file.read_wait_targets(
        "root-session",
        "writer-session.json",
        tmp_path,
    )
    assert (exit_code, retained, error) == (0, {"remote-session"}, None)
    root_targets = _POSTTOOLUSE_MODULE._agents_server_status_file.wait_targets_directory(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert not root_targets.exists()


@pytest.mark.parametrize(
    ("operation", "tool_input", "structured"),
    [
        ("start", {"prompt": "委譲する"}, {"session_id": "remote-session", "status": "running"}),
        ("send_message", {"session_id": "remote-session", "prompt": "再開する"}, {"delivery": "reply_started"}),
    ],
)
def test_codex_delegate_hook_reports_ambiguous_writer_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    tool_input: dict[str, object],
    structured: dict[str, object],
) -> None:
    """曖昧な書込主体索引を診断し、誤った待機対象を登録せず正常終了する。"""
    for writer in ("writer-a", "writer-b"):
        _POSTTOOLUSE_MODULE._agents_server_status_file.write_host_alias("root-session", writer, "codex-thread", tmp_path)
    exit_code = _run_codex_delegate_hook(monkeypatch, tmp_path, operation, tool_input, structured)

    output = json.loads(capsys.readouterr().out)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert exit_code == 0
    assert "agents_serverの待機対象を登録できない" in context
    assert "書込主体を一意に解決できません" in context
    for owner_status_file in ("writer-a.json", "writer-b.json", "codex-thread.json"):
        retained, error = _POSTTOOLUSE_MODULE._agents_server_status_file.read_wait_targets(
            "root-session", owner_status_file, tmp_path
        )
        assert retained == set()
        assert error is None


def test_codex_delegate_hook_without_host_alias_uses_hook_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """書込主体索引が無いCodex経路は検証済みhook sessionで待機対象を登録する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "codex-thread",
                "cwd": str(tmp_path),
                "tool_name": "mcp__agents_server__start",
                "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
                "tool_response": {"structuredContent": {"session_id": "remote-session", "status": "running"}},
            }
        )
    )

    retained, error = _POSTTOOLUSE_MODULE._agents_server_status_file.read_wait_targets(
        "root-session", "codex-thread.json", tmp_path
    )
    assert (exit_code, retained, error) == (0, {"remote-session"}, None)


@pytest.mark.parametrize(
    ("owner_session", "tool_name"),
    [
        (None, "mcp__agents_server__start"),
        ("root-session", "mcp__plugin_agent-toolkit_agents_server__start"),
    ],
)
def test_start_without_caller_identity_does_not_register_wait_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    owner_session: str | None,
    tool_name: str,
) -> None:
    """呼出主体又はCodex経路を解決できない開始応答は待機対象登録簿へ書き込まない。"""
    for name in (
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_STATUS_HOST_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_THREAD_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    if owner_session is not None:
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", owner_session)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "hook-session",
                "cwd": str(tmp_path),
                "tool_name": tool_name,
                "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
                "tool_response": {"structuredContent": {"session_id": "remote-session", "status": "running"}},
            }
        )
    )

    assert exit_code == 0
    assert not (tmp_path / "agents-server").exists()


def test_wait_collects_posttooluse_registered_result_and_releases_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """開始直後の待機はPostToolUseの登録から終端結果を回収し、登録を解除する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE._agents_server_status_file._atk_config, "state_dir", lambda: tmp_path)
    payload = {
        "session_id": "root-session",
        "cwd": str(tmp_path),
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
        "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
        "tool_response": {
            "structuredContent": {
                "session_id": "remote-session",
                "status": "running",
                "root_session_id": "root-session",
            }
        },
    }
    assert _POSTTOOLUSE_MODULE.main(json.dumps(payload)) == 0
    results = _POSTTOOLUSE_MODULE._agents_server_status_file.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    (results / "remote-session.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    exit_code = agents_wait.wait_for_result(
        environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
        state_root=tmp_path,
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "remote-session"}
    retained, error = _POSTTOOLUSE_MODULE._agents_server_status_file.read_wait_targets(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert retained == set()
    assert error is None


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path | None = None,
    home_dir: pathlib.Path | None = None,
    plan_mode_skill_invoked: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env.update({"TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)})
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    env.update(extra_env or {})
    # plan file形式検査はplan_mode_skill_invokedが真の場合のみ実行されるため、
    # 形式検査を期待するテストでは事前に状態ファイルへ同フラグを書き込んでおく。
    if plan_mode_skill_invoked and state_dir is not None and isinstance(payload, dict):
        sid = payload.get("session_id", "")
        if isinstance(sid, str) and sid:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
                json.dumps({"plan_mode_skill_invoked": True}, ensure_ascii=False),
                encoding="utf-8",
            )
    return _fork_runner.run_script(_SCRIPT, argv=("posttooluse",), input=text, env=env)


def _run_pretooluse(payload: dict, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """同じ一時状態ディレクトリでPreToolUse hookを実行する。"""
    env = os.environ.copy()
    env.update({"TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)})
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("pretooluse",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def test_successful_task_stop_consumes_stall_detection_record(tmp_path: pathlib.Path) -> None:
    """成功したTaskStopの対象記録だけを消費する。"""
    session_id = "task-stop-consume"
    state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    state_path.write_text(
        json.dumps(
            {"stall_detection_completed_at_by_task": {"task-1": 1.0, "task-2": 2.0}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = _run(
        {"session_id": session_id, "tool_name": "TaskStop", "tool_input": {"task_id": "task-1"}},
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    assert _read_state(tmp_path, session_id)["stall_detection_completed_at_by_task"] == {"task-2": 2.0}


class TestPlanModeSkillInvocation:
    """plan-mode スキル呼び出し検出 (Skill ツール)。"""

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:plan-mode", "plan-mode"])
    def test_skill_invocation_sets_flag(self, tmp_path: pathlib.Path, skill_name: str):
        sid = "skill-flag"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_mode_skill_invoked") is True

    def test_other_skill_does_not_set_flag(self, tmp_path: pathlib.Path):
        sid = "skill-other"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:writing-standards"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_mode_skill_invoked") is not True


class TestDelegationStateRemoval:
    """delegation起動が専用の状態を更新しないこと。"""

    @pytest.mark.parametrize("skill_name", ["delegation", "agent-toolkit:delegation"])
    @pytest.mark.parametrize("is_sidechain", [False, True])
    def test_skill_invocation_does_not_set_state(
        self,
        tmp_path: pathlib.Path,
        skill_name: str,
        is_sidechain: bool,
    ) -> None:
        sid = f"delegation-skill-{skill_name}-{is_sidechain}"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
                "isSidechain": is_sidechain,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert not (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).exists()


class TestUwiCompletionNotice:
    """UWI回答差分をPostToolUseの追加contextへ接続する。"""

    @staticmethod
    def _clear_delegation_marks(monkeypatch: pytest.MonkeyPatch) -> None:
        """メインの実行主体として判定されるよう、委譲先セッションの印を除く。"""
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
        monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)

    def test_dispatch_appends_answered_filename_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回答差分通知をLLM向けnoticeとして蓄積する。"""
        self._clear_delegation_marks(monkeypatch)
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda _session_id, _cwd, _transcript_path: "newly answered: answered.md",
        )
        notices: list[str] = []
        payload = {
            "session_id": "uwi-answer",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
        }

        result = _POSTTOOLUSE_MODULE._dispatch(  # pylint: disable=protected-access  # noqa: SLF001
            json.dumps(payload), notices
        )

        assert result == 0
        assert len(notices) == 1
        assert "newly answered: answered.md" in notices[0]

    @pytest.mark.parametrize("hook_event_name", ["PostToolUseFailure", "PermissionDenied"])
    def test_failure_events_skip_uwi_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        hook_event_name: str,
    ) -> None:
        """失敗イベントでは回答差分を問い合わせない。"""
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda *_args: pytest.fail("失敗イベントでUWI通知が呼ばれた"),
        )
        notices: list[str] = []
        payload = {
            "session_id": "uwi-failure",
            "hook_event_name": hook_event_name,
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
        }

        result = _POSTTOOLUSE_MODULE._dispatch(  # pylint: disable=protected-access  # noqa: SLF001
            json.dumps(payload), notices
        )

        assert result == 0
        assert not notices

    def test_in_process_subagent_skips_uwi_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`agent_id`を持つin-processのサブエージェントでは通知を組み立てない。"""
        self._clear_delegation_marks(monkeypatch)
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda *_args: pytest.fail("サブエージェントでUWI通知が呼ばれた"),
        )
        notices: list[str] = []
        payload = {
            "session_id": "uwi-subagent",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
            "agent_id": "agent-1",
        }

        result = _POSTTOOLUSE_MODULE._dispatch(  # pylint: disable=protected-access  # noqa: SLF001
            json.dumps(payload), notices
        )

        assert result == 0
        assert not notices

    @pytest.mark.parametrize(
        ("name", "value"),
        [("AGENT_TOOLKIT_DELEGATED_SESSION", "1"), ("AGENT_TOOLKIT_OWNER_SESSION", "owner-1")],
    )
    def test_delegated_session_skips_uwi_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
    ) -> None:
        """`agents_server`が起動した委譲先セッションでは通知を組み立てない。"""
        self._clear_delegation_marks(monkeypatch)
        monkeypatch.setenv(name, value)
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda *_args: pytest.fail("委譲先セッションでUWI通知が呼ばれた"),
        )
        notices: list[str] = []
        payload = {
            "session_id": "uwi-delegate",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
        }

        result = _POSTTOOLUSE_MODULE._dispatch(  # pylint: disable=protected-access  # noqa: SLF001
            json.dumps(payload), notices
        )

        assert result == 0
        assert not notices


class TestCurrentPlanFilePathTracking:
    """plan file編集時の`current_plan_file_path`記録。

    pretooluse.py側の遡及スキャン記録検査・process7完了検査が
    計画ファイル本文を再読み込みする際に使う。
    """

    def test_write_records_current_plan_file_path(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_path = plans_dir / "sample.md"
        sid = "plan-path-write"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("current_plan_file_path") == str(plan_path)

    def test_create_plan_files_bash_records_current_plan_file_path(self, tmp_path: pathlib.Path) -> None:
        """`create_plan_files.py`のBash標準出力からも現在の計画パスを記録する。"""
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_path = plans_dir / "15-0713_process-wi_レーン01.md"
        sid = "plan-path-bash"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "uv run --project /plugin /plugin/skills/plan-mode/scripts/create_plan_files.py"},
                "tool_response": {"stdout": f"{plan_path}\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("current_plan_file_path") == str(plan_path)

    @pytest.mark.parametrize("scenario", ["other-command", "no-plan-path"])
    def test_bash_without_plan_output_keeps_current_plan_file_path(self, tmp_path: pathlib.Path, scenario: str) -> None:
        """`create_plan_files.py`を含まない実行と、計画ファイルを出力しない実行では記録を変えない。"""
        home = tmp_path / "home"
        (home / ".claude" / "plans").mkdir(parents=True)
        sid = f"plan-path-bash-{scenario}"
        command = (
            "echo done"
            if scenario == "other-command"
            else "uv run --project /plugin /plugin/skills/plan-mode/scripts/create_plan_files.py"
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": {"stdout": "計画ファイルを作成できません\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert "current_plan_file_path" not in state

    def test_private_notes_write_records_current_plan_file_path(self, tmp_path: pathlib.Path) -> None:
        """新しいprivate-notes計画rootのWriteも現在の計画パスとして記録する。"""
        private_notes = tmp_path / "private-notes"
        plans_dir = private_notes / "plans" / "2026" / "08"
        plans_dir.mkdir(parents=True)
        plan_path = plans_dir / "30-計画保存先移行-a1b2.md"
        sid = "private-notes-plan-path-write"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            extra_env={"AGENT_TOOLKIT_PRIVATE_NOTES": str(private_notes)},
        )
        state = _read_state(tmp_path, sid)
        assert state.get("current_plan_file_path") == str(plan_path)

    def test_non_plan_file_write_does_not_record(self, tmp_path: pathlib.Path):
        sid = "plan-path-non-plan"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "a.py"), "content": "x"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert "current_plan_file_path" not in state


class TestDetailCurrentPlanPathTracking:
    """詳細計画の編集が既存の現在計画パス契約を変えないことを検証する。"""

    def test_detail_file_write_does_not_record_current_plan_file_path(self, tmp_path: pathlib.Path):
        """計画ファイル（詳細）は計画ファイル（メイン）述語で偽のため現在値を記録しない。"""
        home = tmp_path / "home"
        plans_dir = home / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        detail_path = plans_dir / "sample.detail.md"
        sid = "plan-path-detail-write"
        _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(detail_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        state = _read_state(tmp_path, sid)
        assert "current_plan_file_path" not in state


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_exits_zero(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0

    def test_missing_session_id(self, tmp_path: pathlib.Path):
        result = _run({"tool_input": {"command": "pytest"}}, state_dir=tmp_path)
        assert result.returncode == 0

    def test_missing_command(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "x", "tool_input": {}}, state_dir=tmp_path)
        assert result.returncode == 0

    def test_silent_output(self, tmp_path: pathlib.Path):
        """PostToolUse は stdout に何も書き込まない。"""
        result = _run({"session_id": "silent", "tool_input": {"command": "pytest"}}, state_dir=tmp_path)
        assert result.stdout == ""


class TestPlanFilePostWriteNotice:
    """計画ファイルのWrite成功時に書き込み後チェック案内をhookSpecificOutput経由で返す挙動。"""

    def _make_plan_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        return plans / "sample.md"

    def test_notice_emitted_on_plan_file_write(self, tmp_path: pathlib.Path) -> None:
        plan_path = self._make_plan_path(tmp_path)
        sid = "post-write-notice"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        message = payload["hookSpecificOutput"]["additionalContext"]
        assert "書き込み後の検査" in message
        assert "check_plan_file.py" in message
        assert auto_message_opening_attributes(message)["source"] == "agent-toolkit/posttooluse"

    def test_plan_file_write_notice_is_executable_as_written(self, tmp_path: pathlib.Path) -> None:
        """案内文がそのまま実行できる形であること。

        案内文を受け取った側はこれをシェルで実行する。実行するシェルの環境に
        `${CLAUDE_PLUGIN_ROOT}`は存在しないため、スクリプトは絶対パスで示す。
        照会先の既定は実行時の作業ディレクトリであり、対象リポジトリと一致する保証がないため、
        payloadの`cwd`を`--work-dir`へ明示する。
        """
        plan_path = self._make_plan_path(tmp_path)
        work_dir = tmp_path / "target repo"
        work_dir.mkdir()
        result = _run(
            {
                "session_id": "post-write-notice-executable",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
                "cwd": str(work_dir),
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        message = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "CLAUDE_PLUGIN_ROOT" not in message
        expected_script = pathlib.Path(__file__).resolve().parents[2] / "skills/plan-mode/scripts/check_plan_file.py"
        assert str(expected_script) in message
        # 空白を含むパスは引用しないと単語分割され、意図しない引数として渡る。
        assert f"--work-dir {shlex.quote(str(work_dir))}" in message

    def test_notice_on_removed_detail_file_write_is_skipped(self, tmp_path: pathlib.Path) -> None:
        """廃止した`.detail.md`への書込みを現行計画の検査対象にしない。"""
        plan_path = self._make_plan_path(tmp_path)
        detail_path = plan_path.with_name("sample.detail.md")
        sid = "post-write-notice-detail"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(detail_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_notice_skipped_when_plan_mode_not_invoked(self, tmp_path: pathlib.Path) -> None:
        plan_path = self._make_plan_path(tmp_path)
        sid = "post-write-no-plan-mode"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan_path), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
        )
        assert result.returncode == 0
        assert "post-write checks" not in result.stdout

    def test_no_notice_on_non_plan_file_write(self, tmp_path: pathlib.Path) -> None:
        sid = "post-write-non-plan"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "a.py"), "content": "x"},
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "" or "post-write checks" not in result.stdout

    def test_no_notice_on_plan_file_edit(self, tmp_path: pathlib.Path) -> None:
        plan_path = self._make_plan_path(tmp_path)
        plan_path.write_text("# t\n", encoding="utf-8")
        sid = "post-write-edit"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan_path), "old_string": "t", "new_string": "u"},
            },
            state_dir=tmp_path,
            home_dir=plan_path.parents[2],
        )
        assert result.returncode == 0
        assert "post-write checks" not in result.stdout

    def test_no_notice_on_sidecar_file_write(self, tmp_path: pathlib.Path) -> None:
        home = tmp_path / "home"
        plans = home / ".claude" / "plans"
        plans.mkdir(parents=True)
        sidecar = plans / "sample.review.md"
        sid = "post-write-sidecar"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Write",
                "tool_input": {"file_path": str(sidecar), "content": "# x\n"},
            },
            state_dir=tmp_path,
            home_dir=home,
        )
        assert result.returncode == 0
        assert "post-write checks" not in result.stdout


class TestAwiSkillFlags:
    """自動振り返りの起点となるスキル呼び出しの状態フラグ記録。"""

    @pytest.mark.parametrize(
        ("skill", "flag"),
        [
            ("agent-toolkit:process-wi", "process_wi_skill_invoked"),
            ("process-wi", "process_wi_skill_invoked"),
        ],
    )
    def test_skill_records_flag(self, tmp_path: pathlib.Path, skill: str, flag: str) -> None:
        sid = f"fb-{skill.replace(':', '-')}"
        _run({"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get(flag) is True


class TestExitSessionResetsProcessAwisFlag:
    """終了CLIの機械可読な応答で自動振り返り起点フラグをリセットする。"""

    @staticmethod
    def _invoke(session_id: str, state_dir: pathlib.Path) -> None:
        _run(
            {
                "session_id": session_id,
                "tool_name": "Bash",
                "tool_input": {"command": "atk agents-exit-session"},
                "tool_response": {"stdout": '{"exit_session_invoked":true,"status":"unsupported"}'},
            },
            state_dir=state_dir,
        )

    def test_reset_when_exit_session_invoked(self, tmp_path: pathlib.Path) -> None:
        sid = "exit-cli"
        # 事前に自動振り返り起点フラグを立てる。
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps(
                {
                    "process_wi_skill_invoked": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._invoke(sid, tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("process_wi_skill_invoked") is False
        assert state.get("autonomous_exit_invoked") is True

    def test_reset_idempotent_when_already_false(self, tmp_path: pathlib.Path) -> None:
        """既に偽の状態でも終了CLIの記録だけを追加する。"""
        sid = "exit-idem"
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps({"process_wi_skill_invoked": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._invoke(sid, tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("process_wi_skill_invoked") is False
        assert state.get("autonomous_exit_invoked") is True

    def test_no_rewrite_when_exit_and_reset_state_is_already_complete(self, tmp_path: pathlib.Path) -> None:
        """終了CLI記録とリセット済み状態がそろう場合は再書き込みしない。"""
        sid = "exit-no-rewrite"
        path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        path.write_text(
            json.dumps(
                {
                    "autonomous_exit_invoked": True,
                    "process_wi_skill_invoked": False,
                    "marker": "keep",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mtime_before = path.stat().st_mtime_ns
        self._invoke(sid, tmp_path)
        assert _read_state(tmp_path, sid)["marker"] == "keep"
        assert path.stat().st_mtime_ns == mtime_before


class TestProcessAwisInvokedNonIdempotent:
    """process-wiスキル再起動時のフラグ強制上書き。"""

    def test_reset_and_reinvoke_sets_flag_true(self, tmp_path: pathlib.Path) -> None:
        """終了CLI起動後の再起動でフラグが確実にTrueへ戻る。"""
        sid = "reinvoke"
        # 事前にフラグを立てる。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-wi"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is True
        # 終了CLI起動でリセット。
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "atk agents-exit-session"},
                "tool_response": {"stdout": '{"exit_session_invoked":true,"status":"unsupported"}'},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is False
        # 再起動でTrueへ確実に戻ることを確認する。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-wi"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is True


class TestAgentsServerSessionState:
    """agents_serverのツール応答とsessionごとのcwd状態記録を検証する。"""

    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "response"),
        (
            (
                "start",
                {"cwd": "/repo"},
                {"session_id": "remote", "status": "running", "root_session_id": "root-session"},
            ),
            (
                "start_explore",
                {"cwd": "/repo"},
                {"session_id": "remote", "status": "running", "root_session_id": "root-session"},
            ),
            (
                "start_shell",
                {"cwd": "/repo"},
                {"session_id": "remote", "status": "running", "root_session_id": "root-session"},
            ),
            ("wait", {}, {"session_id": "remote", "status": "completed", "agent_message": "完了"}),
            ("send_message", {"session_id": "remote"}, {"delivery": "reply_started"}),
            ("kill", {"session_id": "remote"}, {"status": "interrupted", "kill_requested": True}),
            ("stop", {"session_id": "remote"}, {}),
            ("list", {}, {"sessions": [], "omitted": 0, "root_session_id": "root-session"}),
        ),
    )
    def test_reduced_success_responses_have_no_missing_fields(
        self,
        tool_name: str,
        tool_input: dict[str, object],
        response: dict[str, object],
    ) -> None:
        """8ツールの削減後成功応答を欠落として報告しない。"""
        qualified_name = f"mcp__plugin_agent-toolkit_agents_server__{tool_name}"
        payload = {"tool_input": tool_input}
        assert (
            _POSTTOOLUSE_MODULE._agents_server_missing_response_fields("local-session", payload, response, qualified_name) == []
        )

    def test_reduced_terminal_start_status_is_recorded(self, tmp_path: pathlib.Path) -> None:
        """全候補不可用のstartが返す終端statusをそのまま記録する。"""
        sid = "terminal-start"
        remote_session_id = "remote-terminal-start"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "failed",
                        "engine": "codex",
                        "model": "gpt-test",
                        "effort": "high",
                        "model_type": "execute",
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["status"] == "failed"

    @pytest.mark.parametrize("tool_name", ("start", "start_explore", "start_write", "send_message", "kill"))
    def test_json_response_records_session_state(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """JSON文字列形状の成功応答を状態記録へ反映する。"""
        sid = f"json-response-{tool_name}"
        remote_session_id = "thread-json"
        start_tools = ("start", "start_explore", "start_write")
        status = "running" if tool_name in (*start_tools, "send_message") else "interrupted"
        tool_input = {"cwd": str(tmp_path)} if tool_name in start_tools else {"session_id": remote_session_id}
        if tool_name == "send_message":
            tool_input["prompt"] = "続行"
        response: dict[str, object] = {}
        if tool_name in start_tools:
            response.update({"session_id": remote_session_id, "status": status})
        elif tool_name == "kill":
            response["status"] = status
        elif tool_name == "send_message":
            response["delivery"] = "reply_started"
        if tool_name == "kill":
            response["kill_requested"] = True
        if tool_name not in start_tools:
            (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
                json.dumps(
                    {
                        "agents_server_cwd_by_session": {remote_session_id: str(tmp_path)},
                        "agents_server_sessions": {
                            remote_session_id: {
                                "session_id": remote_session_id,
                                "status": "running",
                                "pending_observation": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_name}",
                "tool_input": tool_input,
                "tool_response": json.dumps(response),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state["agents_server_sessions"][remote_session_id]["status"] == status
        if tool_name == "kill":
            assert state["agents_server_sessions"][remote_session_id]["kill_requested"] is True
        assert "cwd" not in state["agents_server_sessions"][remote_session_id]
        assert state["agents_server_cwd_by_session"][remote_session_id] == str(tmp_path)

    @pytest.mark.parametrize("tool_name", ("start", "start_explore"))
    def test_start_stores_input_cwd_under_response_session_id(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """開始ツールの入力cwdを応答session_idへ保存し、session記録へ複製しない。"""
        sid = f"{tool_name}-cwd"
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_name}",
                "tool_input": {"prompt": "調査", "cwd": str(tmp_path)},
                "tool_response": {
                    "structuredContent": {
                        "session_id": "thread-start",
                        "turn_id": "turn-start",
                        "status": "running",
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state["agents_server_cwd_by_session"] == {"thread-start": str(tmp_path)}
        assert "cwd" not in state["agents_server_sessions"]["thread-start"]
        assert state["agents_server_sessions"]["thread-start"]["owner_agent_id"] == "main"

    def test_show_records_live_child_session_cwds(self, tmp_path: pathlib.Path) -> None:
        """`show`の応答が返す子sessionの識別子と`cwd`の対を、続行判定が読むキーへ記録する。"""
        sid = "show-child-cwd"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__show",
                "tool_input": {"session_id": "thread-parent"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": "thread-parent",
                        "status": "running",
                        "live_child_sessions": [{"session_id": "thread-child", "cwd": str(tmp_path)}],
                        "live_child_session_ids_without_cwd": ["thread-unknown"],
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state["agents_server_cwd_by_session"] == {"thread-child": str(tmp_path)}
        assert "agents_server_sessions" not in state

    @pytest.mark.parametrize("tool_name", ("send_message", "kill", "stop"))
    def test_continuation_uses_cwd_map_without_mutating_it(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """継続・観測・中断ツールはcwd mapを参照し、session記録へcwdを保存しない。

        破棄ツールはsession記録を除去し、cwd mapは変更しない。
        """
        sid = f"continuation-cwd-{tool_name}"
        remote_session_id = "thread-continuation"
        state: dict[str, object] = {"agents_server_cwd_by_session": {remote_session_id: str(tmp_path)}}
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        tool_input: dict[str, object] = {"session_id": remote_session_id}
        if tool_name == "send_message":
            tool_input["prompt"] = "続行"
        response: dict[str, object] = {}
        if tool_name == "send_message":
            response["delivery"] = "reply_started"
        if tool_name == "kill":
            response.update({"status": "interrupted", "kill_requested": True})
        if tool_name in {"send_message", "stop"}:
            state["agents_server_sessions"] = {
                remote_session_id: {
                    "session_id": remote_session_id,
                    "status": "running",
                    "pending_observation": True,
                }
            }
            (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_name}",
                "tool_input": tool_input,
                "tool_response": {"structuredContent": response},
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        current = _read_state(tmp_path, sid)
        assert current["agents_server_cwd_by_session"] == {remote_session_id: str(tmp_path)}
        if tool_name == "stop":
            assert remote_session_id not in current.get("agents_server_sessions", {})
        else:
            assert "cwd" not in current["agents_server_sessions"][remote_session_id]

    def test_stop_removes_session_record(self, tmp_path: pathlib.Path) -> None:
        """stop成功応答で当該sessionのエントリーを状態キーから除去する。"""
        sid = "pending-stop"
        remote_session_id = "remote-stop"
        state = {
            "agents_server_cwd_by_session": {remote_session_id: str(tmp_path)},
            "agents_server_sessions": {
                remote_session_id: {
                    "session_id": remote_session_id,
                    "status": "completed",
                    "pending_observation": True,
                }
            },
        }
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__stop",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": {"structuredContent": {}},
            },
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        current = _read_state(tmp_path, sid)
        assert remote_session_id not in current["agents_server_sessions"]
        assert current["agents_server_cwd_by_session"] == {remote_session_id: str(tmp_path)}

    def test_pending_observation_transitions(self, tmp_path: pathlib.Path) -> None:
        """公開操作の応答だけから未観測作業の発生と解消を記録する。"""

        def run_operation(
            sid: str,
            remote_session_id: str,
            operation: str,
            *,
            status: str,
            delivery: str | None = None,
        ) -> bool:
            tool_input: dict[str, object] = {"session_id": remote_session_id}
            if operation in {"start", "start_explore", "start_write"}:
                tool_input = {"cwd": str(tmp_path), "prompt": "委譲する"}
            elif operation == "send_message":
                tool_input["prompt"] = "続行する"
            response: dict[str, object]
            if operation in {"start", "start_explore", "start_write"}:
                response = {"session_id": remote_session_id, "status": status}
            elif operation == "kill":
                response = {"status": status}
            elif operation == "send_message":
                response = {"delivery": delivery}
            else:
                response = {}
            if operation == "kill":
                response["kill_requested"] = True
            result = _run(
                {
                    "session_id": sid,
                    "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                    "tool_input": tool_input,
                    "tool_response": {"structuredContent": response},
                },
                state_dir=tmp_path,
            )
            assert result.returncode == 0
            return _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]["pending_observation"]

        def run_wait(sid: str, remote_session_id: str, *, status: str = "running") -> None:
            result = _run(
                {
                    "session_id": sid,
                    "tool_name": "Bash",
                    "tool_input": {"command": "atk agents wait"},
                    "tool_response": json.dumps({"session_id": remote_session_id, "status": status}),
                },
                state_dir=tmp_path,
            )
            assert result.returncode == 0

        for operation in ("start", "start_explore", "start_write"):
            sid = f"pending-{operation}"
            assert run_operation(sid, f"remote-{operation}", operation, status="running") is True

        for status in ("running", "completed"):
            sid = f"pending-wait-{status}"
            remote_session_id = f"remote-wait-{status}"
            assert run_operation(sid, remote_session_id, "start", status="running") is True
            run_wait(sid, remote_session_id, status=status)
            assert _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]["pending_observation"] is False

        for delivery in ("reply_started", "reply_ambiguous"):
            sid = f"pending-send-{delivery}"
            remote_session_id = f"remote-send-{delivery}"
            assert run_operation(sid, remote_session_id, "start", status="running") is True
            run_wait(sid, remote_session_id)
            assert (
                run_operation(
                    sid,
                    remote_session_id,
                    "send_message",
                    status="running",
                    delivery=delivery,
                )
                is True
            )

        sid = "pending-send-steered"
        remote_session_id = "remote-send-steered"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        run_wait(sid, remote_session_id)
        assert (
            run_operation(
                sid,
                remote_session_id,
                "send_message",
                status="running",
                delivery="steered",
            )
            is False
        )

        sid = "pending-send-reply-failed"
        remote_session_id = "remote-send-reply-failed"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        run_wait(sid, remote_session_id, status="completed")
        assert (
            run_operation(
                sid,
                remote_session_id,
                "send_message",
                status="completed",
                delivery="reply_failed",
            )
            is False
        )

        sid = "pending-send-reply-failed-preserves-true"
        remote_session_id = "remote-send-reply-failed-preserves-true"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        assert (
            run_operation(
                sid,
                remote_session_id,
                "send_message",
                status="completed",
                delivery="reply_failed",
            )
            is True
        )

        sid = "pending-kill"
        remote_session_id = "remote-kill"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        assert run_operation(sid, remote_session_id, "kill", status="interrupted") is False

    @pytest.mark.asyncio
    async def test_expired_kill_clears_pending_observation(self, tmp_path: pathlib.Path) -> None:
        """期限切れsessionへのkill成功応答で未観測状態を解消する。"""
        sid = "pending-expired-kill"
        remote_session_id = "remote-expired-kill"
        started = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": "running"}},
            },
            state_dir=tmp_path,
        )
        assert started.returncode == 0

        manager = agents_server_mcp.AgentsServerManager()
        session = SessionState(remote_session_id, str(tmp_path), engine="codex")
        session.status = "completed"
        session.turn_completed = True
        session.touch()
        session.retention_deadline = asyncio.get_running_loop().time() - 1
        manager.sessions[remote_session_id] = session
        response = await manager.kill(remote_session_id, timeout=0)
        killed = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__kill",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": {"structuredContent": response},
            },
            state_dir=tmp_path,
        )
        assert killed.returncode == 0

        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["status"] == "expired"
        assert record["kill_requested"] is False
        assert record["pending_observation"] is False

    def test_start_shell_records_pending_observation(self, tmp_path: pathlib.Path) -> None:
        """シェル実行委譲も観測を試みていない作業として記録する。"""
        sid = "pending-shell"
        remote_session_id = "remote-shell"
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start_shell",
                "tool_input": {"cwd": str(tmp_path), "command": "make test", "summary_policy": "終了状態だけ"},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": "running"}},
            },
            state_dir=tmp_path,
        )
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True
        assert record["owner_agent_id"] == "main"

    def test_pending_work_records_the_agent_that_triggered_it(self, tmp_path: pathlib.Path) -> None:
        """startと配送成立send_messageは、各作業を発生させた主体を観測責任者として記録する。"""
        sid = "pending-owner"
        remote_session_id = "remote-owner"
        _run(
            {
                "session_id": sid,
                "agent_id": "child-1",
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": "running"}},
            },
            state_dir=tmp_path,
        )
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True
        assert record["owner_agent_id"] == "child-1"

        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "atk agents wait"},
                "tool_response": json.dumps({"session_id": remote_session_id, "status": "completed"}),
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": remote_session_id, "prompt": "続行する"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "running",
                        "delivery": "reply_started",
                    }
                },
            },
            state_dir=tmp_path,
        )
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True
        assert record["owner_agent_id"] == "main"

    @staticmethod
    def _background_notice_response(operation: str) -> dict:
        """実行環境が上限到達で返す背景移行通知を模したtool_responseを組み立てる。"""
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f'MCP tool "plugin:agent-toolkit:agents_server/{operation}" is still running after 120s.'
                        " It was moved to the background as task task-bg-1 and keeps running;"
                    ),
                }
            ]
        }

    @staticmethod
    def _start_pending_session(tmp_path: pathlib.Path, sid: str, remote_session_id: str) -> None:
        """観測すべき作業を持つsessionを`start`の構造化応答から作成する。"""
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "running",
                        "turn_id": "turn-1",
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0

    def test_background_kill_notice_clears_pending_observation(self, tmp_path: pathlib.Path) -> None:
        """観測操作が背景タスクへ移った通知でも、観測を試みた事実として未観測作業を解消する。"""
        operation = "kill"
        sid = "background-kill"
        remote_session_id = "remote-background-kill"
        self._start_pending_session(tmp_path, sid, remote_session_id)
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": self._background_notice_response(operation),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is False
        # 移行通知は公開状態を伴わないため、`start`の応答で確定した値をそのまま保つ。
        assert record["status"] == "running"
        assert record["turn_id"] == "turn-1"

    @pytest.mark.parametrize("operation", ["start", "start_explore"])
    def test_background_notice_of_start_records_nothing(self, tmp_path: pathlib.Path, operation: str) -> None:
        """開始操作の移行通知は採番後のsession識別子を含まないため、記録を作成しない。"""
        sid = f"background-start-{operation}"
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": self._background_notice_response(operation),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert not _read_state(tmp_path, sid).get("agents_server_sessions", {})

    def test_background_notice_of_send_message_keeps_state(self, tmp_path: pathlib.Path) -> None:
        """配送成否を確定できない`send_message`の移行通知では、未観測作業の有無を変えない。"""
        sid = "background-send-message"
        remote_session_id = "remote-background-send-message"
        self._start_pending_session(tmp_path, sid, remote_session_id)
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": remote_session_id, "prompt": "続行する"},
                "tool_response": self._background_notice_response("send_message"),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True

    def test_background_notice_does_not_create_unknown_session(self, tmp_path: pathlib.Path) -> None:
        """移行通知の対象sessionに記録が無い場合は、新規の記録を作成しない。"""
        sid = "background-unknown-session"
        known_session_id = "remote-known"
        self._start_pending_session(tmp_path, sid, known_session_id)
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__kill",
                "tool_input": {"session_id": "remote-unknown"},
                "tool_response": self._background_notice_response("kill"),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        sessions = _read_state(tmp_path, sid)["agents_server_sessions"]
        assert "remote-unknown" not in sessions
        assert sessions[known_session_id]["pending_observation"] is True


class TestAgentsServerProcessLoopLog:
    """計画実行系`model_type`の`agents_server` sessionの起動時刻と終了時刻の記録。

    `model_type`は開始入力から解決するため、起動と終端を同じsessionへ通して記録の対応を確認する。
    """

    def _run_session(
        self,
        tmp_path: pathlib.Path,
        *,
        model_type: str,
        observe_tool: str = "kill",
        final_status: str = "completed",
        enable_env: bool = True,
        expected_session_id: str | None = None,
    ) -> str:
        xdg_state_home = tmp_path / "xdg-state"
        extra_env = {
            "XDG_STATE_HOME": str(xdg_state_home),
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1" if enable_env else "",
        }
        if expected_session_id is not None:
            extra_env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"] = expected_session_id
        sid = "process-loop"
        remote_session_id = "thread-process-loop"
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start_custom",
                "tool_input": {"prompt": "実装する", "model_type": model_type, "cwd": str(tmp_path)},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "running",
                    }
                },
            },
            state_dir=tmp_path,
            extra_env=extra_env,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{observe_tool}",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": final_status}},
            },
            state_dir=tmp_path,
            extra_env=extra_env,
        )
        log_path = xdg_state_home / "agent-toolkit" / "process-wi.log"
        return log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    @pytest.mark.parametrize("observe_tool", ("kill",))
    def test_terminal_status_logs_start_and_end(self, tmp_path: pathlib.Path, observe_tool: str) -> None:
        """観測対象の工程は起動時刻を記録し、終端した観測で終了時刻を記録する。"""
        text = self._run_session(tmp_path, model_type="execute", observe_tool=observe_tool)
        assert "event=subagent_start" in text
        assert "event=subagent_end" in text
        assert text.count("type=execute") == 2

    def test_untracked_model_type_is_not_logged(self, tmp_path: pathlib.Path) -> None:
        """探索起動など観測対象外の工程は記録しない。"""
        assert self._run_session(tmp_path, model_type="explore") == ""

    def test_running_status_does_not_log_end(self, tmp_path: pathlib.Path) -> None:
        """終端していない観測では終了時刻を記録しない。"""
        text = self._run_session(tmp_path, model_type="execute", final_status="running")
        assert "event=subagent_start" in text
        assert "event=subagent_end" not in text

    def test_disabled_env_suppresses_logging(self, tmp_path: pathlib.Path) -> None:
        """process-loop起動セッション以外では記録しない。"""
        assert self._run_session(tmp_path, model_type="execute", enable_env=False) == ""

    def test_nested_session_id_suppresses_logging(self, tmp_path: pathlib.Path) -> None:
        """別会話が親の環境印を継承しても常駐ログへ書かない。"""
        assert self._run_session(tmp_path, model_type="execute", expected_session_id="parent") == ""

    def test_matching_session_id_logs(self, tmp_path: pathlib.Path) -> None:
        """親会話のIDに一致すると起動と終端を記録する。"""
        text = self._run_session(tmp_path, model_type="execute", expected_session_id="process-loop")
        assert "event=subagent_start" in text
        assert "event=subagent_end" in text


class TestRemovedRecordsAreAbsent:
    """撤去した検査の入力だった状態と、常時規範の編集警告を記録・出力しないことを検証する。"""

    _REMOVED_KEYS = (
        "test_executed",
        "git_log_checked",
        "amend_pending_status_check",
        "session_edited_files",
        "atk_help_observed",
        "always_loaded_rule_before_sizes",
        "always_loaded_rule_added_texts",
    )

    def test_bash_and_edit_do_not_record_removed_state(self, tmp_path: pathlib.Path) -> None:
        sid = "removed-records"
        rule = pathlib.Path(__file__).resolve().parents[2] / "rules" / "01-agent.md"
        payloads = [
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "uv run pytest"}, "tool_response": {}},
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log -3"}, "tool_response": {}},
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "tool_response": {},
            },
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "atk wi list --help"}, "tool_response": {}},
            {
                "session_id": sid,
                "tool_name": "Edit",
                "tool_input": {"file_path": str(rule), "old_string": "a", "new_string": "b"},
            },
        ]
        for payload in payloads:
            result = _run(payload, state_dir=tmp_path)

            assert result.returncode == 0
            assert result.stdout == ""
        state = _read_state(tmp_path, sid)
        assert not [key for key in self._REMOVED_KEYS if key in state]


@pytest.mark.parametrize(
    ("operation", "tool_input", "expected"),
    (
        ("start_write", {"prompt": "起草する", "cwd": "/tmp/x"}, "write"),
        ("start_shell", {"command": "make test", "cwd": "/tmp/x"}, "explore_fast"),
        ("start_explore", {"prompt": "調べる", "cwd": "/tmp/x"}, "explore_fast"),
        ("start_explore", {"prompt": "調べる", "cwd": "/tmp/x", "fast": False}, "explore"),
    ),
)
def test_agents_server_model_type_matches_server_defaults(operation: str, tool_input: dict, expected: str) -> None:
    """記録する工程種別は、サーバーが各起動ツールの省略時に使う種別と一致する。

    `start_write`の既定は`write`であり、`start_shell`と同じ`explore_fast`を記録すると工程の集計が別種別へ混ざる。
    """
    assert _POSTTOOLUSE_MODULE._agents_server_model_type(tool_input, operation) == expected
