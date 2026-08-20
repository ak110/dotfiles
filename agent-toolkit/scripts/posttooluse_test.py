"""agent-toolkit/scripts/posttooluse.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
plan file形式検査・SSOT検査・codex-review.md読み込み追跡は`posttooluse_plan_format_test.py`、
`session_edited_files`蓄積機構は`posttooluse_session_edited_files_test.py`へ分割している。
"""

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
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _read_state

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"
_POSTTOOLUSE_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "posttooluse.py"
_HOOKS_JSON_PATH = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"
_PYFLTR_RUN_FOR_AGENT_TOOL_NAME = "mcp__plugin_agent-toolkit_pyfltr__run_for_agent"


@functools.cache
def _load_posttooluse_module() -> types.ModuleType:
    """`scripts/posttooluse.py`を`importlib`で動的にインポートする。

    `TestDiffRemoteSnapshots`で`_diff_remote_snapshots`等の内部関数を直接呼ぶために使う。
    引数注入では到達不能なモジュール内部関数の単体検査のため、importlibによる直接参照を例外的に許容する。
    `_SCRIPT`（`claude_hook.py`、サブプロセス起動用）とは別に本体ファイルのパスを参照する。
    """
    spec = importlib.util.spec_from_file_location("posttooluse", _POSTTOOLUSE_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# モジュールレベルでキャッシュ済みモジュールを参照し、内部関数（`_diff_remote_snapshots`等）を直接呼ぶ。
# 引数注入では到達不能なモジュール内部関数の参照のため直接アクセスする。
_POSTTOOLUSE_MODULE = _load_posttooluse_module()


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


def _run_stop(payload: dict, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """同じ一時状態ディレクトリでStop hookを実行する。"""
    env = os.environ.copy()
    env.update({"TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)})
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("stop_advisor",),
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

    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp__plugin_agent-toolkit_codex_app_server__codex_start",
            "mcp__plugin_agent-toolkit_codex_app_server__codex_start_reply",
        ],
    )
    def test_posttooluse_failure_matcher_routes_codex_start_points(self, tool_name: str):
        """Codex開始点の内部失敗をPostToolUseFailureへ配送するmatcherを維持する。"""
        hooks = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
        matcher = hooks["hooks"]["PostToolUseFailure"][0]["matcher"]
        assert re.fullmatch(matcher, tool_name) is not None


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


class TestSessionReviewSkillInvocation:
    """振り返りスキル呼び出し検出 (Skill ツール) と EnterPlanMode によるリセット。"""

    _REVIEW_SKILL = "agent-toolkit:session-review"
    _OTHER_REVIEW_KEY = "extension-review-skill-example"

    def test_session_review_skill_invocation_sets_key(self, tmp_path: pathlib.Path):
        sid = "review-flag"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": self._REVIEW_SKILL},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        invoked = state.get("session_review_invoked")
        assert isinstance(invoked, dict)
        assert invoked.get(self._REVIEW_SKILL) is True

    def test_other_skill_does_not_set_review_key(self, tmp_path: pathlib.Path):
        sid = "review-other"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:commit"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("session_review_invoked") is None

    def test_enter_plan_mode_resets_session_review_invoked(self, tmp_path: pathlib.Path):
        sid = "review-reset"
        # 事前に複数キーのフラグを書き込み、リセットが辞書全体を空にすることを確認する。
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps(
                {
                    "session_review_invoked": {
                        self._REVIEW_SKILL: True,
                        self._OTHER_REVIEW_KEY: True,
                    },
                    "marker": 1,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "EnterPlanMode",
                "tool_input": {},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("session_review_invoked") == {}
        # 他のキーは保持される。
        assert state.get("marker") == 1

    def test_enter_plan_mode_no_write_when_absent(self, tmp_path: pathlib.Path):
        """`session_review_invoked`が未設定の場合、状態ファイルへ書き込みが発生しない（境界）。"""
        sid = "review-reset-noop"
        _run(
            {
                "session_id": sid,
                "tool_name": "EnterPlanMode",
                "tool_input": {},
            },
            state_dir=tmp_path,
        )
        # 状態ファイル自体が作成されないことを期待する。
        assert not (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).exists()

    def test_idempotent_no_rewrite_when_already_true(self, tmp_path: pathlib.Path):
        """既に対象キーが真の場合、状態ファイルへの再書き込みが発生しない（冪等性）。"""
        sid = "review-flag-idem"
        path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        path.write_text(
            json.dumps(
                {"session_review_invoked": {self._REVIEW_SKILL: True}, "other": "keep"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mtime_before = path.stat().st_mtime_ns
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": self._REVIEW_SKILL},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state == {"session_review_invoked": {self._REVIEW_SKILL: True}, "other": "keep"}
        assert path.stat().st_mtime_ns == mtime_before


class TestDelegationTracking:
    """delegation起動の状態記録。"""

    @pytest.mark.parametrize("skill_name", ["delegation", "agent-toolkit:delegation"])
    def test_skill_invocation_sets_flag(self, tmp_path: pathlib.Path, skill_name: str) -> None:
        sid = f"delegation-skill-{skill_name}"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("delegation_skill_invoked") is True


class TestTbdCompletionNotice:
    """TBD回答差分をPostToolUseの追加contextへ接続する。"""

    def test_dispatch_appends_answered_filename_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回答差分通知をLLM向けnoticeとして蓄積する。"""
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._tbd_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda _session_id, _cwd, _transcript_path: "newly answered: answered.md",
        )
        notices: list[str] = []
        payload = {
            "session_id": "tbd-answer",
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
    def test_failure_events_skip_tbd_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        hook_event_name: str,
    ) -> None:
        """失敗イベントでは回答差分を問い合わせない。"""
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._tbd_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda *_args: pytest.fail("失敗イベントでTBD通知が呼ばれた"),
        )
        notices: list[str] = []
        payload = {
            "session_id": "tbd-failure",
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


class TestSubagentEndProcessLoopLog:
    """`_TRACKED_SUBAGENT_TYPES`対象種別終了時の`_process_loop_log`記録（fb-1、`enable_env`偽は空文字列で継承無効化）。"""

    @pytest.mark.parametrize(
        ("subagent_type", "enable_env", "expect_logged"),
        [("plan-impl-executor", True, True), ("plan-impl-executor", False, False), ("claude", True, False)],
    )
    def test_subagent_end_logging(self, tmp_path: pathlib.Path, subagent_type: str, enable_env: bool, expect_logged: bool):
        xdg_state_home = tmp_path / "xdg-state"
        extra_env = {"XDG_STATE_HOME": str(xdg_state_home), "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1" if enable_env else ""}
        payload = {"session_id": "sid", "tool_name": "Agent", "tool_input": {"subagent_type": subagent_type}}
        _run(payload, state_dir=tmp_path, extra_env=extra_env)
        log_path = xdg_state_home / "agent-toolkit" / "process-feedbacks.log"
        assert log_path.exists() == expect_logged
        if expect_logged:
            assert "event=subagent_end" in (text := log_path.read_text(encoding="utf-8")) and f"type={subagent_type}" in text


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
                "tool_input": {"command": 'cd "$TARGET" && git log --oneline -5'},
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
                "tool_input": {"command": 'cd "$TARGET" && git log --oneline'},
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
        assert "post-write checks" in message
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


class TestFeedbackSkillFlags:
    """process-feedbacksスキル呼び出しのセッション状態フラグ記録。"""

    @pytest.mark.parametrize(
        ("skill", "flag"),
        [
            ("agent-toolkit:process-feedbacks", "process_feedbacks_skill_invoked"),
            ("process-feedbacks", "process_feedbacks_skill_invoked"),
        ],
    )
    def test_skill_records_flag(self, tmp_path: pathlib.Path, skill: str, flag: str) -> None:
        sid = f"fb-{skill.replace(':', '-')}"
        _run({"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get(flag) is True


class TestExitSessionResetsProcessFeedbacksFlag:
    """exit-sessionスキル起動検知時のprocess_feedbacks_skill_invokedフラグリセット。

    `agent-toolkit:process-feedbacks`「6. 振り返りと終了」節がexit-sessionで終端するため、
    exit-session起動を完了シグナルとする。
    """

    @pytest.mark.parametrize(
        "skill",
        ["agent-toolkit:exit-session", "exit-session"],
    )
    def test_reset_when_exit_session_invoked(self, tmp_path: pathlib.Path, skill: str) -> None:
        """exit-session起動でprocess_feedbacks_skill_invokedが偽になる。"""
        sid = f"exit-{skill.replace(':', '-')}"
        # 事前にフラグを立てる。
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps({"process_feedbacks_skill_invoked": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        _run({"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is False

    def test_reset_idempotent_when_already_false(self, tmp_path: pathlib.Path) -> None:
        """既に偽の状態でexit-sessionが起動されても状態は変わらない。"""
        sid = "exit-idem"
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps({"process_feedbacks_skill_invoked": False}, ensure_ascii=False),
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
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is False


class TestProcessFeedbacksInvokedNonIdempotent:
    """process-feedbacksスキル再起動時のフラグ強制上書き。"""

    def test_reset_and_reinvoke_sets_flag_true(self, tmp_path: pathlib.Path) -> None:
        """exit-session後の再起動でフラグが確実にTrueへ戻る。"""
        sid = "reinvoke"
        # 事前にフラグを立てる。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-feedbacks"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is True
        # exit-session起動でリセット。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:exit-session"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is False
        # 再起動でTrueへ確実に戻ることを確認する。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-feedbacks"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is True


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
                "tool_input": {"command": 'cd "$TARGET" && git commit --amend --no-edit'},
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
                "tool_input": {"command": 'cd "$TARGET" && git push origin master'},
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


class TestWarnCodexRemoteChange:
    """`mcp__plugin_agent-toolkit_codex_app_server__codex_start`/`mcp__plugin_agent-toolkit_codex_app_server__codex_start_reply`呼び出し前後のリモート参照比較による警告。

    codexプロセス内部の実行がPreToolUse/PostToolUseフックを通らずに不可逆操作（`git push`等）を
    行う事象への機械チェック（事後検知）のうち、比較・警告・後始末側（PostToolUse）を検証する。
    """

    @staticmethod
    def _init_repo_with_remote(base: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        remote = base / "remote.git"
        remote.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare"], cwd=remote, check=True)
        repo = base / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "a"], cwd=repo, check=True)
        (repo / "a.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
        return repo, remote

    @staticmethod
    def _write_snapshot_state(tmp_path: pathlib.Path, sid: str, entries: dict) -> None:
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps({"codex_remote_snapshot_by_key": entries}, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _result_payload(sid: str, *, key: str | None = None) -> dict:
        """codex_resultのPostToolUse payloadを作成する。"""
        payload = {
            "session_id": sid,
            "tool_name": "mcp__plugin_agent-toolkit_codex_app_server__codex_result",
            "tool_input": {"session_id": "thread-1"},
            "tool_response": {
                "structuredContent": {
                    "session_id": "thread-1",
                    "turn_id": "turn-1",
                    "status": "completed",
                    "agent_message": "完了",
                    "error": None,
                },
            },
            "isSidechain": True,
        }
        if key is not None:
            payload["transcript_path"] = f"/x/agent-{key}.jsonl"
        return payload

    def test_no_warning_when_no_change(self, tmp_path: pathlib.Path):
        """記録済みスナップショットと現在値が一致する場合は警告しない。"""
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "warn-nochange"
        self._write_snapshot_state(tmp_path, sid, {f"session:{sid}": {"cwd": str(repo), "snapshot": {}}})
        result = _run(self._result_payload(sid), state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" not in result.stdout
        assert _read_state(tmp_path, sid).get("codex_remote_snapshot_by_key") == {}

    def test_remote_check_tracks_result_for_same_session(self, tmp_path: pathlib.Path):
        """開始応答からsessionを記録し、結果回収時に同じsnapshotと比較する。"""
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "same-session"
        initial_pre = _run_pretooluse(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_codex_app_server__codex_start",
                "tool_input": {"prompt": "実装", "cwd": str(repo)},
                "tool_use_id": "start-1",
                "isSidechain": True,
            },
            tmp_path,
        )
        assert initial_pre.returncode == 0
        initial_post = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_codex_app_server__codex_start",
                "tool_input": {"prompt": "実装", "cwd": str(repo)},
                "tool_use_id": "start-1",
                "tool_response": {
                    "structuredContent": {
                        "session_id": "thread-1",
                        "turn_id": "turn-1",
                        "status": "running",
                    }
                },
                "isSidechain": True,
            },
            state_dir=tmp_path,
        )
        assert initial_post.returncode == 0
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
        reply_post = _run(self._result_payload(sid), state_dir=tmp_path)
        assert reply_post.returncode == 0
        assert "remote refs changed" in reply_post.stdout

    def test_reply_failure_blocks_stop_until_result_and_keeps_snapshot(self, tmp_path: pathlib.Path):
        """内部開始失敗は未回収としてStopを遮断し、結果回収時だけsnapshotを比較・解放する。"""
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "reply-failure"
        start_tool = "mcp__plugin_agent-toolkit_codex_app_server__codex_start"
        reply_tool = "mcp__plugin_agent-toolkit_codex_app_server__codex_start_reply"

        assert (
            _run_pretooluse(
                {
                    "session_id": sid,
                    "tool_name": start_tool,
                    "tool_input": {"prompt": "実装", "cwd": str(repo)},
                    "tool_use_id": "start-1",
                    "isSidechain": True,
                },
                tmp_path,
            ).returncode
            == 0
        )
        assert (
            _run(
                {
                    "session_id": sid,
                    "tool_name": start_tool,
                    "tool_input": {"prompt": "実装", "cwd": str(repo)},
                    "tool_use_id": "start-1",
                    "tool_response": {
                        "structuredContent": {
                            "session_id": "thread-1",
                            "turn_id": "turn-1",
                            "status": "running",
                        }
                    },
                    "isSidechain": True,
                },
                state_dir=tmp_path,
            ).returncode
            == 0
        )
        assert _run(self._result_payload(sid), state_dir=tmp_path).returncode == 0

        assert (
            _run_pretooluse(
                {
                    "session_id": sid,
                    "tool_name": reply_tool,
                    "tool_input": {"session_id": "thread-1", "prompt": "続けて実装"},
                    "tool_use_id": "reply-1",
                    "isSidechain": True,
                },
                tmp_path,
            ).returncode
            == 0
        )
        failure = _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": reply_tool,
                "tool_input": {"session_id": "thread-1", "prompt": "続けて実装"},
                "tool_use_id": "reply-1",
                "tool_response": {"error": "turn/start failed"},
                "isSidechain": True,
            },
            state_dir=tmp_path,
        )
        assert failure.returncode == 0
        failed_state = _read_state(tmp_path, sid)
        failed_record = failed_state["codex_app_server_sessions"]["thread-1"]
        assert failed_record["status"] == "failed"
        assert failed_record["turn_id"] == ""
        assert failed_record["result_retrieved"] is False
        assert failed_record["snapshot_key"] == "reply-1"
        assert "reply-1" in failed_state["codex_remote_snapshot_by_key"]

        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:session-review"},
            },
            state_dir=tmp_path,
        )
        stop_before_result = _run_stop({"session_id": sid}, tmp_path)
        assert stop_before_result.returncode == 0
        stop_before_payload = json.loads(stop_before_result.stdout)
        assert stop_before_payload["decision"] == "block"
        assert "codex_result" in stop_before_payload["reason"]

        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
        result_payload = self._result_payload(sid)
        result_payload["tool_use_id"] = "result-1"
        result_payload["tool_response"]["structuredContent"].update({"status": "failed", "error": "turn/start failed"})
        result = _run(result_payload, state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" in result.stdout
        retrieved_state = _read_state(tmp_path, sid)
        retrieved_record = retrieved_state["codex_app_server_sessions"]["thread-1"]
        assert retrieved_record["result_retrieved"] is True
        assert retrieved_state["codex_remote_snapshot_by_key"] == {}

        stop_after_result = _run_stop({"session_id": sid}, tmp_path)
        assert stop_after_result.returncode == 0
        assert "decision" not in json.loads(stop_after_result.stdout)

    def test_initial_start_response_loss_keeps_session_until_completion_and_result(self, tmp_path: pathlib.Path):
        """初回turn/start応答喪失をturn終端まで保持し、Stopとsnapshotを管理する。"""
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "initial-start-failure"
        start_tool = "mcp__plugin_agent-toolkit_codex_app_server__codex_start"

        assert (
            _run_pretooluse(
                {
                    "session_id": sid,
                    "tool_name": start_tool,
                    "tool_input": {"prompt": "実装", "cwd": str(repo)},
                    "tool_use_id": "start-1",
                    "isSidechain": True,
                },
                tmp_path,
            ).returncode
            == 0
        )
        start = _run(
            {
                "session_id": sid,
                "tool_name": start_tool,
                "tool_input": {"prompt": "実装", "cwd": str(repo)},
                "tool_use_id": "start-1",
                "tool_response": {
                    "structuredContent": {
                        "session_id": "thread-1",
                        "turn_id": "",
                        "status": "running",
                        "error": {"message": "turn/start response lost"},
                    }
                },
                "isSidechain": True,
            },
            state_dir=tmp_path,
        )
        assert start.returncode == 0
        ambiguous_state = _read_state(tmp_path, sid)
        ambiguous_record = ambiguous_state["codex_app_server_sessions"]["thread-1"]
        assert ambiguous_record["status"] == "running"
        assert ambiguous_record["result_retrieved"] is False
        assert ambiguous_record["snapshot_key"] == "start-1"
        assert "start-1" in ambiguous_state["codex_remote_snapshot_by_key"]

        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:session-review"},
            },
            state_dir=tmp_path,
        )
        stop_before_result = _run_stop({"session_id": sid}, tmp_path)
        assert json.loads(stop_before_result.stdout)["decision"] == "block"

        completed = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_codex_app_server__codex_wait",
                "tool_input": {"session_id": "thread-1", "timeout": 300},
                "tool_use_id": "wait-1",
                "tool_response": {
                    "structuredContent": {
                        "session_id": "thread-1",
                        "turn_id": "turn-1",
                        "status": "completed",
                        "error": None,
                    }
                },
                "isSidechain": True,
            },
            state_dir=tmp_path,
        )
        assert completed.returncode == 0

        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
        result_payload = self._result_payload(sid)
        result_payload["tool_response"]["structuredContent"].update({"turn_id": "turn-1", "status": "completed", "error": None})
        result = _run(result_payload, state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" in result.stdout
        retrieved_state = _read_state(tmp_path, sid)
        retrieved_record = retrieved_state["codex_app_server_sessions"]["thread-1"]
        assert retrieved_record["result_retrieved"] is True
        assert retrieved_state["codex_remote_snapshot_by_key"] == {}

        stop_after_result = _run_stop({"session_id": sid}, tmp_path)
        assert "decision" not in json.loads(stop_after_result.stdout)

    def test_result_failure_before_completion_keeps_snapshot(self, tmp_path: pathlib.Path):
        """未終端turnのcodex_result失敗では次の結果回収までsnapshotを保持する。"""
        sid = "result-before-completion"
        state = {
            "codex_app_server_sessions": {
                "thread-1": {
                    "session_id": "thread-1",
                    "status": "running",
                    "turn_id": "turn-1",
                    "result_retrieved": False,
                    "snapshot_key": "start-1",
                }
            },
            "codex_remote_snapshot_by_key": {"start-1": {"cwd": str(tmp_path), "snapshot": {}}},
        }
        path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        failure = _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "mcp__plugin_agent-toolkit_codex_app_server__codex_result",
                "tool_input": {"session_id": "thread-1"},
                "tool_use_id": "result-1",
                "tool_response": {"error": "the Codex turn has not completed"},
                "isSidechain": True,
            },
            state_dir=tmp_path,
        )
        assert failure.returncode == 0
        assert _read_state(tmp_path, sid)["codex_remote_snapshot_by_key"] == {"start-1": {"cwd": str(tmp_path), "snapshot": {}}}

    def test_reply_input_failure_preserves_session_state_and_clears_snapshot(self, tmp_path: pathlib.Path):
        """入力検証失敗は新しい未回収ターンに変換せず、対応するsnapshotだけを破棄する。"""
        sid = "reply-input-failure"
        state = {
            "codex_app_server_sessions": {
                "thread-1": {
                    "session_id": "thread-1",
                    "status": "completed",
                    "turn_id": "turn-1",
                    "result_retrieved": True,
                }
            },
            "codex_remote_snapshot_by_key": {"reply-invalid": {"cwd": str(tmp_path), "snapshot": {}}},
        }
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        result = _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "mcp__plugin_agent-toolkit_codex_app_server__codex_start_reply",
                "tool_input": {"session_id": "thread-1", "prompt": ""},
                "tool_use_id": "reply-invalid",
                "tool_response": {"error": "prompt must not be empty"},
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state_after = _read_state(tmp_path, sid)
        assert state_after["codex_app_server_sessions"]["thread-1"]["status"] == "completed"
        assert state_after["codex_app_server_sessions"]["thread-1"]["result_retrieved"] is True
        assert state_after["codex_remote_snapshot_by_key"] == {}

    def test_warns_and_clears_state_when_remote_changed(self, tmp_path: pathlib.Path):
        """codex呼び出し中にリモートへpushされた変化を検知して警告し、記録済みスナップショットを削除する。"""
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "warn-changed"
        self._write_snapshot_state(tmp_path, sid, {f"session:{sid}": {"cwd": str(repo), "snapshot": {}}})
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
        state = _read_state(tmp_path, sid)
        state["codex_app_server_sessions"] = {
            "thread-1": {"session_id": "thread-1", "status": "running", "snapshot_key": f"session:{sid}"}
        }
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(json.dumps(state), encoding="utf-8")
        result = _run(self._result_payload(sid), state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" in result.stdout
        assert "origin" in result.stdout
        assert _read_state(tmp_path, sid).get("codex_remote_snapshot_by_key") == {}

    def test_uses_agent_id_key_when_transcript_path_present(self, tmp_path: pathlib.Path):
        """`transcript_path`からagentIdを抽出できる場合はagentIdをキーとして比較する。"""
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "warn-agent"
        self._write_snapshot_state(tmp_path, sid, {"abc123": {"cwd": str(repo), "snapshot": {}}})
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
        state = _read_state(tmp_path, sid)
        state["codex_app_server_sessions"] = {
            "thread-1": {"session_id": "thread-1", "status": "running", "snapshot_key": "abc123"}
        }
        (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(json.dumps(state), encoding="utf-8")
        result = _run(self._result_payload(sid, key="abc123"), state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" in result.stdout
        assert _read_state(tmp_path, sid).get("codex_remote_snapshot_by_key") == {}

    def test_no_warning_when_no_recorded_entry(self, tmp_path: pathlib.Path):
        """記録済みスナップショットが存在しない場合は比較せず警告しない。"""
        sid = "warn-none"
        result = _run(self._result_payload(sid), state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" not in result.stdout

    def test_no_warning_when_recorded_cwd_invalid(self, tmp_path: pathlib.Path):
        """記録済みエントリの`cwd`が不正な場合は比較せず警告しない。"""
        sid = "warn-badcwd"
        self._write_snapshot_state(tmp_path, sid, {f"session:{sid}": {"cwd": None, "snapshot": {}}})
        result = _run(self._result_payload(sid), state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" not in result.stdout

    def test_codex_reply_also_warns(self, tmp_path: pathlib.Path):
        """`mcp__plugin_agent-toolkit_codex_app_server__codex_start_reply`呼び出し後も同様に比較・警告する。"""
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "warn-reply"
        self._write_snapshot_state(tmp_path, sid, {f"session:{sid}": {"cwd": str(repo), "snapshot": {}}})
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
        result = _run(self._result_payload(sid), state_dir=tmp_path)
        assert result.returncode == 0
        assert "remote refs changed" in result.stdout

    def test_warning_delivered_via_additional_context(self, tmp_path: pathlib.Path):
        """警告は`hookSpecificOutput.additionalContext`経由で出力され、stderrへは出力しない。

        PostToolUseで行動を促す通知はコーディングエージェントへ確実に届く`additionalContext`を
        第一経路とする（`claude-hooks.md`「出力フィールドの使い分け」節）。stderr出力（exit 0時）は
        コーディングエージェントへ届く経路として保証されないため使わない。
        """
        repo, _ = self._init_repo_with_remote(tmp_path)
        sid = "warn-context"
        self._write_snapshot_state(tmp_path, sid, {f"session:{sid}": {"cwd": str(repo), "snapshot": {}}})
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=repo, check=True)
        result = _run(self._result_payload(sid), state_dir=tmp_path)
        assert result.returncode == 0
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert "remote refs changed" in payload["hookSpecificOutput"]["additionalContext"]


class TestDiffRemoteSnapshots:
    """`_diff_remote_snapshots`: 取得失敗（`None`）と新規追加リモートの区別。

    `snapshot_remote_refs`は取得失敗したリモートを`None`でマーキングしつつキー自体は
    保持する。既存リモートが一時的な取得失敗（`None`）を経て次回成功した場合、
    「新規追加されたリモート」と誤認して警告してはならない。
    """

    _diff = staticmethod(_POSTTOOLUSE_MODULE._diff_remote_snapshots)  # pylint: disable=protected-access

    def test_no_diff_when_unchanged(self):
        before = {"origin": {"refs/heads/main": "aaa"}}
        after = {"origin": {"refs/heads/main": "aaa"}}
        assert self._diff(before, after) == set()

    def test_diff_when_ref_updated(self):
        before = {"origin": {"refs/heads/main": "aaa"}}
        after = {"origin": {"refs/heads/main": "bbb"}}
        assert self._diff(before, after) == {"origin"}

    def test_diff_when_new_remote_added_with_refs(self):
        before: dict[str, dict[str, str] | None] = {}
        after = {"origin": {"refs/heads/main": "aaa"}}
        assert self._diff(before, after) == {"origin"}

    def test_no_diff_when_new_remote_added_without_refs(self):
        before: dict[str, dict[str, str] | None] = {}
        after: dict[str, dict[str, str] | None] = {"origin": {}}
        assert self._diff(before, after) == set()

    def test_no_false_positive_when_existing_remote_recovers_from_failure(self):
        """既存リモートが`before`側で取得失敗（`None`）し`after`側で成功した場合、
        新規追加と誤認せず対象から除外する。"""
        before: dict[str, dict[str, str] | None] = {"origin": None}
        after: dict[str, dict[str, str] | None] = {"origin": {"refs/heads/main": "aaa"}}
        assert self._diff(before, after) == set()

    def test_no_false_positive_when_existing_remote_fails_after_success(self):
        """既存リモートが`before`側で成功し`after`側で取得失敗（`None`）した場合も、
        取得失敗を「参照が消えた」という差分と誤認せず対象から除外する。"""
        before: dict[str, dict[str, str] | None] = {"origin": {"refs/heads/main": "aaa"}}
        after: dict[str, dict[str, str] | None] = {"origin": None}
        assert self._diff(before, after) == set()

    def test_no_diff_when_remote_removed_from_after(self):
        """`after`側にリモート自体が存在しない（削除された）場合は比較対象が無いため除外する。"""
        before = {"origin": {"refs/heads/main": "aaa"}}
        after: dict[str, dict[str, str] | None] = {}
        assert self._diff(before, after) == set()


class TestDelegationSkillState:
    """delegation起動記録はメインセッションだけに残す。"""

    def test_sidechain_skill_does_not_set_delegation_state(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "session_id": "side",
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:delegation"},
                "isSidechain": True,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert not (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="side")).exists()

    def test_main_skill_sets_delegation_state(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {"session_id": "main", "tool_name": "Skill", "tool_input": {"skill": "agent-toolkit:delegation"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="main")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["delegation_skill_invoked"] is True
