"""agent-toolkit/scripts/posttooluse.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
plan file形式検査・SSOT検査・codex-review.md読み込み追跡は`posttooluse_plan_format_test.py`、
`session_edited_files`蓄積機構は`posttooluse_session_edited_files_test.py`へ分割している。
"""

import asyncio
import functools
import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import types

import _fork_runner
import agents_server_mcp
import pytest
from _agents_server_state import SessionState
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _read_state

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hook.py"
_POSTTOOLUSE_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "posttooluse.py"
_HOOKS_JSON_PATH = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"
_PYFLTR_RUN_FOR_AGENT_TOOL_NAME = "mcp__plugin_agent-toolkit_pyfltr__run_for_agent"


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


class TestAgentsServerBackgroundResponse:
    """agents_serverの背景移行応答を正常系として扱う。"""

    @staticmethod
    def _payload(tool_response: object) -> dict:
        return {
            "tool_name": "mcp__agents_server__wait",
            "tool_input": {"session_id": "remote-session"},
            "tool_response": tool_response,
            "session_id": "local-session",
        }

    def test_background_transition_does_not_warn(self) -> None:
        result = _run(self._payload({"content": [{"type": "text", "text": "moved to the background as task task-1"}]}))
        assert result.returncode == 0
        assert result.stdout == ""

    def test_malformed_regular_response_still_warns(self) -> None:
        result = _run(self._payload("invalid response"))
        assert result.returncode == 0
        assert "応答でresponse, session_id, statusが欠落しているか不正" in result.stdout


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


class TestTestExecution:
    """テスト実行検出。"""

    @pytest.mark.parametrize(
        "command",
        [
            # 直接実行系
            "pytest",
            "uv run pytest -v",
            "python -m pytest tests/",
            "pyfltr run-for-agent",
            "uv run pyfltr ci",
            "uv run pyfltr fast",
            "uv run pyfltr run-for-agent",
            "uvx pyfltr run-for-agent",
            "uvx pyfltr ci",
            "pre-commit run",
            "pre-commit run --all-files",
            "uvx pre-commit run -a",
            "prek run",
            "prek run --all-files",
            "uvx prek run -a",
            "cargo test",
            # タスクランナー経由（test / check / validateアクションを各ランナーで網羅）
            "make test",
            "make check",
            "make validate",
            "mise run test",
            "mise run check",
            "npm test",
            "npm run test",
            "pnpm test",
            "pnpm run test",
            "pnpm run check",
            "yarn test",
            "yarn run validate",
            "just test",
            "just check",
            "task test",
            "task validate",
            # 環境変数代入接頭辞付き（境界値: 1個・2個連続・セグメント区切り直後）
            "LOCALAPPDATA=/tmp/dummy uvx pyfltr run-for-agent",
            "LOCALAPPDATA=x FOO=bar uvx pyfltr ci",
            "cd /tmp && LOCALAPPDATA=x uvx pre-commit run",
            # 時間制限接頭辞付き（境界値: 秒指定・単位付き・環境変数代入との併用・セグメント区切り直後）
            "timeout 600 uvx pyfltr run-for-agent",
            "timeout 10m make test",
            "timeout 300 LOCALAPPDATA=x uv run pytest",
            "cd /tmp && timeout 120 cargo test",
        ],
    )
    def test_test_commands_detected(self, tmp_path: pathlib.Path, command: str):
        sid = "test-exec-detect"
        result = _run({"session_id": sid, "tool_input": {"command": command}}, state_dir=tmp_path)
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is True, f"command={command!r} not detected"

    def test_unrelated_command_no_change(self, tmp_path: pathlib.Path):
        sid = "test-unrelated"
        _run({"session_id": sid, "tool_input": {"command": "echo hello"}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is not True

    @pytest.mark.parametrize(
        "tool_response",
        [
            "Command running in background with ID: bg-task-1. Output is being written to: /tmp/bg-task-1.output",
            {"stdout": "Command running in background with ID: bg-task-1.", "stderr": ""},
        ],
        ids=["text", "structured"],
    )
    def test_background_command_records_task_id(self, tmp_path: pathlib.Path, tool_response: object):
        """背景実行の応答から取得したタスクIDを自セッションの起動記録として保存する。"""
        sid = "background-task-record"
        result = _run(
            {
                "session_id": sid,
                "tool_input": {"command": "sleep 120", "run_in_background": True},
                "tool_response": tool_response,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("background_task_ids") == ["bg-task-1"]

    def test_foreground_command_does_not_record_task_id(self, tmp_path: pathlib.Path):
        """前景実行では起動記録を残さない。"""
        sid = "background-task-foreground"
        _run(
            {
                "session_id": sid,
                "tool_input": {"command": "echo hello"},
                "tool_response": "Command running in background with ID: bg-task-1.",
            },
            state_dir=tmp_path,
        )
        assert "background_task_ids" not in _read_state(tmp_path, sid)

    def test_background_task_ids_are_recorded_without_duplication(self, tmp_path: pathlib.Path):
        """同じタスクIDを重複して記録せず、別のIDは追記する。"""
        sid = "background-task-multi"
        for task_id in ("bg-task-1", "bg-task-1", "bg-task-2"):
            _run(
                {
                    "session_id": sid,
                    "tool_input": {"command": "sleep 120", "run_in_background": True},
                    "tool_response": f"Command running in background with ID: {task_id}.",
                },
                state_dir=tmp_path,
            )
        assert _read_state(tmp_path, sid).get("background_task_ids") == ["bg-task-1", "bg-task-2"]

    def test_pyfltr_mcp_run_for_agent_detected(self, tmp_path: pathlib.Path):
        """pyfltr MCPの検証成功をCLI経由と同じ状態へ記録する。"""
        sid = "test-mcp-run-for-agent"
        _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUse",
                "tool_name": _PYFLTR_RUN_FOR_AGENT_TOOL_NAME,
                "tool_input": {"paths": ["."], "work_dir": "/repo"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("test_executed") is True

    def test_pyfltr_mcp_run_for_agent_failure_not_detected(self, tmp_path: pathlib.Path):
        """失敗イベントは正式な検証完了として記録しない。"""
        sid = "test-mcp-run-for-agent-failure"
        _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": _PYFLTR_RUN_FOR_AGENT_TOOL_NAME,
                "tool_input": {"paths": ["."]},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("test_executed") is not True

    def test_other_pyfltr_mcp_tool_not_detected(self, tmp_path: pathlib.Path):
        """検索など検証以外のpyfltr MCPツールでは状態を変更しない。"""
        sid = "test-mcp-grep"
        _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__plugin_agent-toolkit_pyfltr__grep",
                "tool_input": {"pattern": "x", "paths": ["."]},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("test_executed") is not True

    def test_posttooluse_matcher_routes_pyfltr_mcp_run_for_agent(self):
        """MCP成功イベントがPostToolUse実装へ配送されるmatcherを維持する。"""
        hooks = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
        matcher = hooks["hooks"]["PostToolUse"][0]["matcher"]
        assert re.fullmatch(matcher, _PYFLTR_RUN_FOR_AGENT_TOOL_NAME) is not None
        assert (
            re.fullmatch(
                matcher,
                "mcp__plugin_agent-toolkit_agents_server__send_message",
            )
            is not None
        )
        assert re.fullmatch(matcher, "mcp__plugin_agent-toolkit_agents_server__start_explore") is not None
        assert re.fullmatch(matcher, "mcp__plugin_agent-toolkit_agents_server__kill") is not None

    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp__plugin_agent-toolkit_agents_server__start",
            "mcp__plugin_agent-toolkit_agents_server__start_explore",
            "mcp__plugin_agent-toolkit_agents_server__send_message",
            "mcp__plugin_agent-toolkit_agents_server__wait",
            "mcp__plugin_agent-toolkit_agents_server__kill",
        ],
    )
    def test_posttooluse_failure_matcher_excludes_agents_server(self, tool_name: str):
        """agents_server専用のPostToolUseFailure配送を撤去する。"""
        hooks = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
        matcher = hooks["hooks"]["PostToolUseFailure"][0]["matcher"]
        assert re.fullmatch(matcher, tool_name) is None

    def test_posttooluse_failure_matcher_excludes_wait(self):
        """waitの失敗はPostToolUseFailure matcherへ配送しない。"""
        hooks = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
        matcher = hooks["hooks"]["PostToolUseFailure"][0]["matcher"]
        assert re.fullmatch(matcher, "mcp__plugin_agent-toolkit_agents_server__wait") is None

    def test_posttooluse_failure_matcher_keeps_agent_task(self):
        """agents_server撤去後もAgent・Taskの失敗イベントmatcherを維持する。"""
        hooks = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
        matcher = hooks["hooks"]["PostToolUseFailure"][0]["matcher"]
        matcher_tools = set(matcher.split("|"))
        assert matcher_tools == {"Agent", "Task"}


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
                "tool_input": {"skill": "agent-toolkit:coding-standards"},
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

    def test_dispatch_appends_answered_filename_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回答差分通知をLLM向けnoticeとして蓄積する。"""
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


class TestGitLogChecked:
    """git_log_checked 状態の管理。

    cwdを伴うpayloadではcwd別辞書`{cwd: True}`で記録する。
    cwdを静的に解決できないイベントは記録しない。
    リセット対象は対象コミットの親子関係が変化する操作（commit / rebase / reset）に限定する。
    push・Write / Edit / MultiEditはリセットしない
    （push・ファイル編集はコミット木を書き換えないため再確認を強制する必要がない）。
    """

    def test_git_log_sets_checked_dict_when_cwd_present(self, tmp_path: pathlib.Path):
        sid = "log-check"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline -5"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("git_log_checked") == {"/repo/a": True}

    def test_unresolved_git_log_does_not_record(self, tmp_path: pathlib.Path):
        """shell展開を含むgit logはcwdを解決できないため状態を作成しない。"""
        sid = "log-check-unresolved"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": 'cd "$HOME/repo" && git log --oneline -5'},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert "git_log_checked" not in state

    def test_unresolved_git_log_does_not_allow_following_amend(self, tmp_path: pathlib.Path):
        """解決不能なgit log後に、payload cwdの確認済み状態としてamendを許可しない。"""
        sid = "log-check-unresolved-amend"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "cd ~/repo && git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        result = _run_pretooluse(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "cwd": "/repo/a",
            },
            tmp_path,
        )
        assert result.returncode == 2
        assert "amend" in result.stderr

    @pytest.mark.parametrize(
        ("label", "reset_command", "reset_cwd"),
        [
            ("commit", "git commit -m 'x'", "/repo/a"),
            ("rebase", "GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "/repo/a"),
            ("reset", "git reset --hard HEAD~1", "/repo/a"),
        ],
    )
    def test_same_cwd_reset_removes_only_target_entry(
        self, tmp_path: pathlib.Path, label: str, reset_command: str, reset_cwd: str
    ):
        """同cwdでのcommit/rebase/resetは該当cwdのみリセットする。"""
        sid = f"log-reset-{label}"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/b",
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/a": True, "/repo/b": True}
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": reset_command},
                "cwd": reset_cwd,
            },
            state_dir=tmp_path,
        )
        # `/repo/a`のみリセットされ、`/repo/b`のエントリは残る。
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/b": True}

    def test_resolved_log_reset_removes_target_entry(self, tmp_path: pathlib.Path):
        """解決済みcwdのcommitは該当cwdの確認状態だけをリセットする。"""
        sid = "log-resolved-reset"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/a": True}
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m 'x'"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") == {}

    @pytest.mark.parametrize(
        ("edit_payload"),
        [
            {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt", "content": "x"}},
            {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.txt"}},
        ],
    )
    def test_edit_does_not_reset_dict(self, tmp_path: pathlib.Path, edit_payload: dict):
        """Write/Editはコミット木を書き換えないためリセットしない。"""
        sid = "log-no-reset-edit"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run({"session_id": sid, **edit_payload}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/a": True}

    def test_push_does_not_reset(self, tmp_path: pathlib.Path):
        """pushは対象コミットの親子関係を変えないためリセットしない。"""
        sid = "log-no-reset-push"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin master"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/a": True}

    def test_unrelated_bash_no_reset(self, tmp_path: pathlib.Path):
        sid = "log-no-reset"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git log --oneline"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "echo hello"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") == {"/repo/a": True}

    @pytest.mark.parametrize(
        ("label", "command", "payload_cwd", "expected_keys"),
        [
            # `git -C <dir>` でcwdを切り替えたlog記録は当該ディレクトリで記録される
            ("dash_c_absolute", "git -C /repo/x log --oneline", "/elsewhere", ["/repo/x"]),
            # `cd <dir>` 後のlog
            ("cd_then_log", "cd /repo/x && git log --oneline", "/elsewhere", [os.path.normpath("/repo/x")]),
            # `cd a; git -C b` の組合せ（a/b で記録）
            (
                "cd_and_dash_c",
                "cd /repo && git -C x log --oneline",
                "/elsewhere",
                [os.path.normpath("/repo/x")],
            ),
            # 1つのBashコマンドで複数の log がある場合は各cwdで記録される
            (
                "multiple_log_per_segment",
                "git -C /repo/a log; git -C /repo/b log",
                "/elsewhere",
                [os.path.normpath("/repo/a"), os.path.normpath("/repo/b")],
            ),
        ],
    )
    def test_effective_cwd_records_correct_keys(
        self,
        tmp_path: pathlib.Path,
        label: str,
        command: str,
        payload_cwd: str,
        expected_keys: list[str],
    ) -> None:
        """`git -C`・`cd`・両者併用で実効cwdが切り替わるケースで該当cwdに記録される。"""
        sid = f"eff-{label}"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "cwd": payload_cwd,
            },
            state_dir=tmp_path,
        )
        recorded = _read_state(tmp_path, sid).get("git_log_checked")
        assert isinstance(recorded, dict)
        for key in expected_keys:
            assert recorded.get(key) is True, f"{key} not recorded in {recorded}"


class TestReadHandlerNoop:
    """Readは対象パスによらずセッション状態を更新しない。"""

    @pytest.mark.parametrize(
        "file_path",
        [
            "/home/user/dotfiles/agent-toolkit/skills/writing-standards/references/textlint-violations.md",
            r"C:\Users\user\dotfiles\agent-toolkit\skills\writing-standards\references\textlint-violations.md",
            "/tmp/random.txt",
        ],
    )
    def test_read_does_not_set_removed_tracking_flag(
        self,
        tmp_path: pathlib.Path,
        file_path: str,
    ) -> None:
        """旧追跡対象と無関係パスのいずれでも撤去済みフラグを記録しない。"""
        sid = f"read-textlint-{len(file_path)}"
        _run(
            {
                "session_id": sid,
                "tool_name": "Read",
                "tool_input": {"file_path": file_path},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("textlint_violations_read") is not True


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
        assert "[auto-generated: agent-toolkit/posttooluse]" in message

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
        expected_script = pathlib.Path(__file__).resolve().parents[1] / "skills/plan-mode/scripts/check_plan_file.py"
        assert str(expected_script) in message
        # 空白を含むパスは引用しないと単語分割され、意図しない引数として渡る。
        assert f"--work-dir {shlex.quote(str(work_dir))}" in message

    def test_notice_on_detail_file_write_targets_main_path(self, tmp_path: pathlib.Path) -> None:
        """計画ファイル（詳細）`.detail.md`書込み時も検査案内は対応する計画ファイル（メイン）パスを対象にする。"""
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
        message = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert str(plan_path) in message
        assert str(detail_path) not in message

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
    """exit-sessionスキル起動検知時の自動振り返り起点フラグリセット。

    `agent-toolkit:process-wi`の`references/finish-session.md`がexit-sessionで終端するため、
    exit-session起動を完了シグナルとする。
    """

    @pytest.mark.parametrize(
        "skill",
        ["agent-toolkit:exit-session", "exit-session"],
    )
    def test_reset_when_exit_session_invoked(self, tmp_path: pathlib.Path, skill: str) -> None:
        """exit-session起動でprocess_wi_skill_invokedが偽になる。"""
        sid = f"exit-{skill.replace(':', '-')}"
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
        _run({"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("process_wi_skill_invoked") is False
        assert state.get("autonomous_exit_invoked") is True

    def test_reset_idempotent_when_already_false(self, tmp_path: pathlib.Path) -> None:
        """既に偽の状態でもexit-sessionの記録だけを追加する。"""
        sid = "exit-idem"
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps({"process_wi_skill_invoked": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:exit-session"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("process_wi_skill_invoked") is False
        assert state.get("autonomous_exit_invoked") is True

    def test_no_rewrite_when_exit_and_reset_state_is_already_complete(self, tmp_path: pathlib.Path) -> None:
        """exit-session記録とリセット済み状態がそろう場合は再書き込みしない。"""
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
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:exit-session"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid)["marker"] == "keep"
        assert path.stat().st_mtime_ns == mtime_before


class TestProcessAwisInvokedNonIdempotent:
    """process-wiスキル再起動時のフラグ強制上書き。"""

    def test_reset_and_reinvoke_sets_flag_true(self, tmp_path: pathlib.Path) -> None:
        """exit-session後の再起動でフラグが確実にTrueへ戻る。"""
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
        # exit-session起動でリセット。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:exit-session"},
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


class TestAmendPendingStatusCheck:
    """`amend_pending_status_check` cwd別フラグの管理（fb3）。

    `git commit --amend` / `git commit --fixup=<sha>` / `git commit --fixup <sha>`成功時に
    該当cwdでフラグを立て、実送出`git push`成功時に該当cwdを解除する
    （`git status`実行では解除しない）。
    """

    @staticmethod
    def _flag(state: dict, cwd: str) -> bool:
        flags = state.get("amend_pending_status_check")
        return bool(flags.get(cwd, False)) if isinstance(flags, dict) else False

    @pytest.mark.parametrize(
        ("label", "command"),
        [
            ("amend", "git commit --amend --no-edit"),
            ("fixup_eq", "git commit --fixup=abc123"),
            ("fixup_space", "git commit --fixup abc123"),
        ],
    )
    def test_amend_or_fixup_sets_flag(self, tmp_path: pathlib.Path, label: str, command: str):
        sid = f"amend-flag-{label}"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/repo/a"},
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True

    def test_normal_commit_does_not_set_flag(self, tmp_path: pathlib.Path):
        sid = "amend-flag-normal"
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}, "cwd": "/repo/a"},
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is False

    def test_dash_c_absolute_amend_records_dash_c_cwd(self, tmp_path: pathlib.Path):
        sid = "amend-flag-dash-c"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git -C /repo/x commit --amend --no-edit"},
                "cwd": "/elsewhere",
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert self._flag(state, "/repo/x") is True
        assert self._flag(state, "/elsewhere") is False

    def test_cd_then_amend_records_cd_cwd(self, tmp_path: pathlib.Path):
        sid = "amend-flag-cd"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "cd /repo/x && git commit --amend --no-edit"},
                "cwd": "/elsewhere",
            },
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), os.path.normpath("/repo/x")) is True

    def test_unresolved_amend_does_not_set_flag_or_affect_following_push(self, tmp_path: pathlib.Path):
        """解決不能なamendは状態を作成せず、後続pushへ確認待ちを持ち越さない。"""
        sid = "amend-flag-unresolved"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": 'cd "$HOME/repo" && git commit --amend --no-edit'},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert "amend_pending_status_check" not in state
        result = _run_pretooluse(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin master"},
                "cwd": "/repo/a",
            },
            tmp_path,
        )
        assert result.returncode == 0

    def test_unresolved_push_does_not_clear_existing_flag(self, tmp_path: pathlib.Path):
        """解決不能なpushは空文字列キーを操作せず、既存worktreeのフラグを保持する。"""
        sid = "amend-flag-push-unresolved"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git -C ~/repo push origin master"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert self._flag(state, "/repo/a") is True
        assert not (isinstance(state.get("amend_pending_status_check"), dict) and "" in state["amend_pending_status_check"])

    def test_git_status_does_not_reset_flag(self, tmp_path: pathlib.Path):
        sid = "amend-flag-status-noop"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": "/repo/a"},
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True

    def test_real_push_success_resets_flag(self, tmp_path: pathlib.Path):
        sid = "amend-flag-push-real"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git push origin master"}, "cwd": "/repo/a"},
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is False

    def test_dry_run_push_does_not_reset_flag(self, tmp_path: pathlib.Path):
        sid = "amend-flag-push-dryrun"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git push --dry-run origin master"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True

    def test_dash_n_push_does_not_reset_flag(self, tmp_path: pathlib.Path):
        sid = "amend-flag-push-dashn"
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "git commit --amend --no-edit"},
                "cwd": "/repo/a",
            },
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git push -n origin master"}, "cwd": "/repo/a"},
            state_dir=tmp_path,
        )
        assert self._flag(_read_state(tmp_path, sid), "/repo/a") is True


class TestAgentsServerSessionState:
    """agents_serverのツール応答とsessionごとのcwd状態記録を検証する。"""

    @pytest.mark.parametrize("tool_name", ("start", "start_explore", "send_message", "wait", "kill"))
    def test_json_response_records_session_state(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """JSON文字列形状の成功応答を状態記録へ反映する。"""
        sid = f"json-response-{tool_name}"
        remote_session_id = "thread-json"
        start_tools = ("start", "start_explore")
        status = "running" if tool_name in (*start_tools, "send_message") else "interrupted"
        tool_input = {"cwd": str(tmp_path)} if tool_name in start_tools else {"session_id": remote_session_id}
        if tool_name == "send_message":
            tool_input["prompt"] = "続行"
        response: dict[str, object] = {"session_id": remote_session_id, "turn_id": "turn-json", "status": status}
        if tool_name == "kill":
            response["kill_requested"] = True
        if tool_name not in start_tools:
            (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
                json.dumps({"agents_server_cwd_by_session": {remote_session_id: str(tmp_path)}}),
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

    @pytest.mark.parametrize("tool_name", ("wait", "send_message", "kill"))
    def test_continuation_uses_cwd_map_without_mutating_it(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """wait・send_message・killはcwd mapを参照し、session記録へcwdを保存しない。"""
        sid = f"continuation-cwd-{tool_name}"
        remote_session_id = "thread-continuation"
        state = {"agents_server_cwd_by_session": {remote_session_id: str(tmp_path)}}
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        tool_input = {"session_id": remote_session_id}
        if tool_name == "send_message":
            tool_input["prompt"] = "続行"
        response: dict[str, object] = {"session_id": remote_session_id, "turn_id": "turn-next", "status": "running"}
        if tool_name == "kill":
            response.update({"status": "interrupted", "kill_requested": True})
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
        assert "cwd" not in current["agents_server_sessions"][remote_session_id]

    def test_missing_cwd_map_does_not_fallback_to_session_record(self, tmp_path: pathlib.Path) -> None:
        """cwd map欠落時も古いsession記録のcwdへフォールバックしない。"""
        sid = "continuation-no-cwd-map"
        remote_session_id = "thread-no-cwd-map"
        state = {
            "agents_server_sessions": {
                remote_session_id: {
                    "session_id": remote_session_id,
                    "status": "running",
                    "cwd": "/fallback/that/must/not/be/used",
                }
            }
        }
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__wait",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "completed",
                        "agent_message": "完了",
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["status"] == "completed"
        assert "cwd" not in record

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
            if operation in {"start", "start_explore"}:
                tool_input = {"cwd": str(tmp_path), "prompt": "委譲する"}
            elif operation == "send_message":
                tool_input["prompt"] = "続行する"
            response: dict[str, object] = {"session_id": remote_session_id, "status": status}
            if delivery is not None:
                response["delivery"] = delivery
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

        for operation in ("start", "start_explore"):
            sid = f"pending-{operation}"
            assert run_operation(sid, f"remote-{operation}", operation, status="running") is True

        for status in ("running", "completed"):
            sid = f"pending-wait-{status}"
            remote_session_id = f"remote-wait-{status}"
            assert run_operation(sid, remote_session_id, "start", status="running") is True
            assert run_operation(sid, remote_session_id, "wait", status=status) is False

        for delivery in ("steered", "reply_started", "reply_ambiguous"):
            sid = f"pending-send-{delivery}"
            remote_session_id = f"remote-send-{delivery}"
            assert run_operation(sid, remote_session_id, "start", status="running") is True
            assert run_operation(sid, remote_session_id, "wait", status="running") is False
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

        sid = "pending-send-reply-failed"
        remote_session_id = "remote-send-reply-failed"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        assert run_operation(sid, remote_session_id, "wait", status="completed") is False
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
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__wait",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": "completed"}},
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

    @pytest.mark.parametrize("operation", ["wait", "kill"])
    def test_background_notice_clears_pending_observation(self, tmp_path: pathlib.Path, operation: str) -> None:
        """観測操作が背景タスクへ移った通知でも、観測を試みた事実として未観測作業を解消する。"""
        sid = f"background-{operation}"
        remote_session_id = f"remote-background-{operation}"
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
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__wait",
                "tool_input": {"session_id": "remote-unknown"},
                "tool_response": self._background_notice_response("wait"),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        sessions = _read_state(tmp_path, sid)["agents_server_sessions"]
        assert "remote-unknown" not in sessions
        assert sessions[known_session_id]["pending_observation"] is True


class TestAgentsServerProcessLoopLog:
    """計画実行系`model_type`の`agents_server` sessionの起動時刻と終了時刻の記録。

    `model_type`は`start`応答にだけ現れるため、起動と終端を同じsessionへ通して記録の対応を確認する。
    """

    def _run_session(
        self,
        tmp_path: pathlib.Path,
        *,
        model_type: str,
        observe_tool: str = "wait",
        final_status: str = "completed",
        enable_env: bool = True,
    ) -> str:
        xdg_state_home = tmp_path / "xdg-state"
        extra_env = {
            "XDG_STATE_HOME": str(xdg_state_home),
            "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1" if enable_env else "",
        }
        sid = "process-loop"
        remote_session_id = "thread-process-loop"
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "実装する", "cwd": str(tmp_path)},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "running",
                        "model_type": model_type,
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

    @pytest.mark.parametrize("observe_tool", ("wait", "kill"))
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
        text = self._run_session(tmp_path, model_type="plan", final_status="running")
        assert "event=subagent_start" in text
        assert "event=subagent_end" not in text

    def test_disabled_env_suppresses_logging(self, tmp_path: pathlib.Path) -> None:
        """process-loop起動セッション以外では記録しない。"""
        assert self._run_session(tmp_path, model_type="execute", enable_env=False) == ""
