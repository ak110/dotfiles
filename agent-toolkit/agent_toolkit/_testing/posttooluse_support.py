"""posttooluse_testの共通試験準備。"""

import functools
import importlib.util
import json
import os
import pathlib
import subprocess
import types

import pytest

from agent_toolkit._agents_server import shared_layout
from agent_toolkit._common import session_registry, session_state, state_paths
from agent_toolkit._testing import fork_runner as _fork_runner

_SCRIPT = (pathlib.Path(__file__).resolve().parents[1] / "_hooks/posttooluse_test.py").parents[1] / "hook.py"


_POSTTOOLUSE_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "_hooks/posttooluse_test.py"
).parent / "posttooluse.py"


_HOOKS_JSON_PATH = (
    (pathlib.Path(__file__).resolve().parents[1] / "_hooks/posttooluse_test.py").parents[2] / "hooks" / "hooks.json"
)


_QUESTION = "Issue対応の回までマージを待ちますか？"


_MULTI_QUESTION = "どの対応を進めますか？"


_LABELS = ("待つ (Recommended)", "今すぐマージする")


@functools.cache
def _load_posttooluse_module() -> types.ModuleType:
    """`scripts/posttooluse.py`を`importlib`で動的にインポートする。

    PostToolUseの内部dispatchと補助機構を直接呼ぶテストで使う。
    引数注入では到達不能なモジュール内部関数の単体テストのため、importlibによる直接参照を例外的に許容する。
    `_SCRIPT`（`hook.py`、サブプロセス起動用）とは別に本体ファイルのパスを参照する。
    """
    spec = importlib.util.spec_from_file_location("posttooluse", _POSTTOOLUSE_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_POSTTOOLUSE_MODULE = _load_posttooluse_module()


def _observe(payload: dict) -> list[str]:
    """PostToolUseの入力を解析して記録処理へ渡し、コーディングエージェントへ返す通知を返す。"""
    parsed = _POSTTOOLUSE_MODULE._parse_hook_payload(json.dumps(payload))  # pylint: disable=protected-access
    if parsed is None:
        return []
    return _POSTTOOLUSE_MODULE._observe_tool(*parsed)  # pylint: disable=protected-access


def _isolate_conversation(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, conversation_session_id: str) -> None:
    """会話側の現行session識別子と状態ディレクトリをテスト用に固定する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", conversation_session_id)
    for name in ("AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_STATUS_HOST_SESSION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)


def _write_root_status(tmp_path: pathlib.Path, root_session_id: str, sessions: list[dict[str, object]]) -> pathlib.Path:
    """指定ルートの書込主体`root.json`を作成し、その状態ディレクトリを返す。"""
    directory = shared_layout.status_directory(root_session_id, tmp_path)
    directory.mkdir(parents=True)
    (directory / "root.json").write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")
    return directory


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
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
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


def _state_path(session_id: str) -> pathlib.Path:
    """試験用のJSONを現行の公開パスへ準備する。"""
    path = session_state.state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
    # plan fileの書式を判定する処理はplan_mode_skill_invokedが真の場合のみ実行されるため、
    # 書式の判定を期待するテストでは事前に状態ファイルへ同フラグを書き込んでおく。
    if plan_mode_skill_invoked and state_dir is not None and isinstance(payload, dict):
        sid = payload.get("session_id", "")
        if isinstance(sid, str) and sid:
            state_dir.mkdir(parents=True, exist_ok=True)
            _state_path(sid).write_text(
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


def _ask_user_question_payload(session_id: str, tool_response: dict) -> dict:
    """`AskUserQuestion`のPostToolUse payloadを組み立てる。

    `tool_response`の形はClaude Code 2.1.285と2.1.283の`~/.claude/projects`配下の記録にある
    `toolUseResult`（`questions`・`answers`・`annotations`、注記だけの回答の`(notes only)`、
    複数選択の`, `区切り）から写した。`response`と`afkTimeoutMs`はClaude Code 2.1.291本体の
    `AskUserQuestion`の`call`が返す`data`の項目である。
    """
    response = {
        "questions": [
            {"question": _QUESTION, "multiSelect": False, "options": [{"label": label} for label in _LABELS]},
            {"question": _MULTI_QUESTION, "multiSelect": True, "options": [{"label": label} for label in _LABELS]},
        ],
        "answers": {_QUESTION: _LABELS[0], _MULTI_QUESTION: ", ".join(_LABELS)},
        "annotations": {},
        "response": None,
        "afkTimeoutMs": None,
    }
    response.update(tool_response)
    return {"session_id": session_id, "tool_name": "AskUserQuestion", "tool_input": {}, "tool_response": response}


def _single_notice_body(result: subprocess.CompletedProcess[str], source: str) -> str:
    """hookの出力から指定した生成元の`notice`の本文を1件取り出す。"""
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    opening = f'<atk-auto source="{source}" kind="notice">\n'
    assert context.startswith(opening) and context.endswith("\n</atk-auto>")
    return context[len(opening) : -len("\n</atk-auto>")]


def _registered_posttooluse_names(event: str = "PostToolUse") -> set[str]:
    """現在の完全一致matcherが選択するツール集合を返す。"""
    hooks = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))["hooks"][event]
    return {name for entry in hooks for name in entry["matcher"].split("|")}


def _assert_tool_specific_posttooluse_registration(registered: set[str]) -> None:
    tool_names = {
        "AskUserQuestion",
        "Skill",
        "Agent",
        "Task",
        "TaskStop",
        "Bash",
        "PowerShell",
        "Write",
        "Edit",
        "MultiEdit",
        "Read",
        "WebFetch",
    }
    tool_names.update(
        f"mcp__plugin_agent-toolkit_agents_server__{operation}"
        for operation in ("start", "send_message", "kill", "stop", "list", "show")
    )
    assert tool_names <= registered


def _shell_payload(tool_name: str, response: dict) -> dict:
    """シェル系ツールのPostToolUse payloadを組み立てる。

    `tool_response`の項目は、Claude Code 2.1.291で`bashOutputMaxChars`を超える出力を返したBashのPostToolUse入力
    （`stdout`・`stderr`・`interrupted`・`isImage`・`noOutputExpected`・`persistedOutputPath`・`persistedOutputSize`）から写した。
    """
    base = {"stdout": "", "stderr": "", "interrupted": False, "isImage": False, "noOutputExpected": False}
    base.update(response)
    return {
        "session_id": "persisted-output",
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": "cat docs/large.md"},
        "tool_response": base,
    }


class _TestAgentsServerSessionStateSupport:
    @staticmethod
    def _publish_owned_child(sid: str, tmp_path: pathlib.Path) -> dict[str, object]:
        session_registry.publish(
            "thread-child",
            state=session_registry.PublicationState.PENDING_RUNNING,
            engine="claude",
            cwd=str(tmp_path),
            launcher_session_id=sid,
            state_root=state_paths.state_dir(),
        )
        record: dict[str, object] = {
            "session_id": "thread-child",
            "status": "running",
            "launcher_agent_id": "main",
            "owner_agent_id": "main",
            "pending_observation": True,
        }
        _state_path(sid).write_text(json.dumps({"agents_server_sessions": {"thread-child": record}}), encoding="utf-8")
        return record

    @staticmethod
    def _background_notice_response(operation: str) -> dict:
        """実行環境が上限到達で返すバックグラウンドタスクへの移行通知を模したtool_responseを組み立てる。"""
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
