"""agent-toolkit/scripts/posttooluse.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
plan file形式検査・SSOT検査・codex-review.md読み込み追跡は
`posttooluse_plan_format_test.py`へ分割している。
"""

import functools
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import types

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"


@functools.cache
def _load_posttooluse_module() -> types.ModuleType:
    """`scripts/posttooluse.py`を`importlib`で動的にインポートする。

    `TestPlanFormatSsot`で本体スクリプトの定数（`_PLAN_REQUIRED_H2`等）と
    外部ドキュメントの整合性を検査するために使う。
    引数注入では到達不能なモジュール内部状態の検査のため、importlibによる直接参照を例外的に許容する。
    """
    spec = importlib.util.spec_from_file_location("posttooluse", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# モジュールレベルでキャッシュ済みモジュールを参照し、_build_valid_plan で使う必須セクション順を取得する。
# 引数注入では到達不能なモジュール内部状態の参照のため直接アクセスする。
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
            (state_dir / f"claude-agent-toolkit-{sid}.json").write_text(
                json.dumps({"plan_mode_skill_invoked": True}, ensure_ascii=False),
                encoding="utf-8",
            )
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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


class TestGitStatusCheck:
    """Git 状態確認検出。"""

    @pytest.mark.parametrize("command", ["git status", "git log --decorate --oneline -5", "git diff"])
    def test_git_commands_detected(self, tmp_path: pathlib.Path, command: str):
        sid = "test-git-status"
        _run({"session_id": sid, "tool_input": {"command": command}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("git_status_checked") is True


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
        (tmp_path / f"claude-agent-toolkit-{sid}.json").write_text(
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
        assert not (tmp_path / f"claude-agent-toolkit-{sid}.json").exists()

    def test_idempotent_no_rewrite_when_already_true(self, tmp_path: pathlib.Path):
        """既に対象キーが真の場合、状態ファイルへの再書き込みが発生しない（冪等性）。"""
        sid = "review-flag-idem"
        path = tmp_path / f"claude-agent-toolkit-{sid}.json"
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


class TestAgentInvocationFlags:
    """AgentとTask起動のsubagent_type別セッション状態フラグ記録と、codex-review起動検出・codex-impl起動検出。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize(
        ("subagent_type", "flag_key"),
        [
            ("plan-reviewer", "plan_reviewer_invoked"),
            ("agent-toolkit:plan-reviewer", "plan_reviewer_invoked"),
            ("plan-impl-reviewer", "plan_impl_reviewer_invoked"),
            ("agent-toolkit:plan-impl-reviewer", "plan_impl_reviewer_invoked"),
            ("agent-doc-validator", "agent_doc_validator_invoked"),
            ("agent-toolkit:agent-doc-validator", "agent_doc_validator_invoked"),
            ("plan-codex-reviewer", "codex_review_invoked"),
            ("agent-toolkit:plan-codex-reviewer", "codex_review_invoked"),
        ],
    )
    def test_subagent_type_flag(self, tmp_path: pathlib.Path, tool_name: str, subagent_type: str, flag_key: str):
        sid = f"{tool_name.lower()}-{subagent_type.replace(':', '-')}"
        _run({"session_id": sid, "tool_name": tool_name, "tool_input": {"subagent_type": subagent_type}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get(flag_key) is True

    def test_codex_review_flag_via_mcp(self, tmp_path: pathlib.Path):
        sid = "codex-review-via-mcp"
        _run({"session_id": sid, "tool_name": "mcp__codex__codex", "tool_input": {}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("codex_review_invoked") is True

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:codex-impl", "codex-impl"])
    def test_codex_impl_flag_via_skill(self, tmp_path: pathlib.Path, skill_name: str):
        """`codex-impl`スキル完了で`codex_impl_invoked`が記録される（フルネーム・短縮名の両方）。"""
        sid = f"codex-impl-via-skill-{skill_name.replace(':', '-')}"
        _run({"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill_name}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("codex_impl_invoked") is True

    def test_codex_review_not_recorded_via_mcp_when_codex_impl_invoked(self, tmp_path: pathlib.Path):
        """`codex_impl_invoked`が真の場合、`mcp__codex__codex`完了で`codex_review_invoked`を記録しない。

        `codex_impl_invoked`未設定時に記録される挙動（従来どおり）は`test_codex_review_flag_via_mcp`で検証済み。
        """
        sid = "codex-impl-mcp-no-review-flag"
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / f"claude-agent-toolkit-{sid}.json").write_text(
            json.dumps({"codex_impl_invoked": True}, ensure_ascii=False), encoding="utf-8"
        )
        _run({"session_id": sid, "tool_name": "mcp__codex__codex", "tool_input": {}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("codex_review_invoked") is not True

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_other_subagent_type_no_flag(self, tmp_path: pathlib.Path, tool_name: str):
        sid = f"{tool_name.lower()}-other-subagent"
        _run({"session_id": sid, "tool_name": tool_name, "tool_input": {"subagent_type": "claude"}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("plan_reviewer_invoked") is not True
        assert state.get("plan_impl_reviewer_invoked") is not True
        assert state.get("agent_doc_validator_invoked") is not True
        assert state.get("codex_review_invoked") is not True


class TestSubagentEndProcessLoopLog:
    """`_TRACKED_SUBAGENT_TYPES`対象種別終了時の`_process_loop_log`記録（fb-1、`enable_env`偽は空文字列で継承無効化）。"""

    @pytest.mark.parametrize(
        ("subagent_type", "enable_env", "expect_logged"),
        [("plan-implementer", True, True), ("plan-implementer", False, False), ("claude", True, False)],
    )
    def test_subagent_end_logging(self, tmp_path: pathlib.Path, subagent_type: str, enable_env: bool, expect_logged: bool):
        xdg_state_home = tmp_path / "xdg-state"
        extra_env = {"XDG_STATE_HOME": str(xdg_state_home), "DOTFILES_AUTONOMOUS_EXIT_REQUIRED": "1" if enable_env else ""}
        payload = {"session_id": "sid", "tool_name": "Agent", "tool_input": {"subagent_type": subagent_type}}
        _run(payload, state_dir=tmp_path, extra_env=extra_env)
        log_path = xdg_state_home / "agent-toolkit" / "process-feedbacks.log"
        assert log_path.exists() == expect_logged
        if expect_logged:
            assert "event=subagent_end" in (text := log_path.read_text(encoding="utf-8")) and f"type={subagent_type}" in text


class TestCurrentPlanFilePathTracking:
    """plan file編集時の`current_plan_file_path`記録。

    pretooluse.py側の`agent_doc_validator_invoked`条件付き必須化判定
    （`_should_require_agent_doc_validator`）が計画ファイル本文を再読み込みする際に使う。
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
    cwd空文字列環境では旧形式の単一bool値で記録し後方互換を保つ。
    Write / Edit / MultiEditは編集の事実が裏で他コミットを動かしている可能性に備え、
    辞書全体をクリアする（cwd別の細粒度リセットは行わない）。
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

    def test_git_log_sets_legacy_bool_when_cwd_absent(self, tmp_path: pathlib.Path):
        """cwd未指定では旧形式の単一bool値で記録する（後方互換）。"""
        sid = "log-check-nocwd"
        _run({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline -5"}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("git_log_checked") is True

    @pytest.mark.parametrize(
        ("label", "reset_command", "reset_cwd"),
        [
            ("commit", "git commit -m 'x'", "/repo/a"),
            ("rebase", "GIT_SEQUENCE_EDITOR=: git rebase -i HEAD~2", "/repo/a"),
            ("push", "git push origin master", "/repo/a"),
        ],
    )
    def test_same_cwd_reset_removes_only_target_entry(
        self, tmp_path: pathlib.Path, label: str, reset_command: str, reset_cwd: str
    ):
        """同cwdでのcommit/rebase/pushは該当cwdのみリセットする。"""
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

    def test_legacy_bool_reset_back_to_false(self, tmp_path: pathlib.Path):
        """旧形式bool値はcommit時に`False`へ戻す（従来挙動）。"""
        sid = "log-legacy-reset"
        _run({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("git_log_checked") is True
        _run({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

    @pytest.mark.parametrize(
        ("edit_payload"),
        [
            {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt", "content": "x"}},
            {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.txt"}},
        ],
    )
    def test_edit_resets_dict(self, tmp_path: pathlib.Path, edit_payload: dict):
        """Write/Editは辞書全体をクリアする。"""
        sid = "log-reset-edit"
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
        assert _read_state(tmp_path, sid).get("git_log_checked") == {}

    def test_edit_resets_legacy_bool(self, tmp_path: pathlib.Path):
        """旧形式bool値の場合もWrite/Editでリセットする（後方互換）。"""
        sid = "log-reset-edit-legacy"
        _run({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("git_log_checked") is True
        _run(
            {"session_id": sid, "tool_name": "Write", "tool_input": {"file_path": "/tmp/x.txt", "content": "x"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is False

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


class TestReadHandler:
    """Read系判定（codex-review.md / textlint-violations.md）。

    POSIX区切り・Windows区切り双方のfile_pathに対して同等にセッション状態フラグを立てることを検証する。
    Windows環境ではClaude Codeがバックスラッシュ区切りのパスをfile_path引数に渡すため、
    posttooluse.py側で`replace("\\", "/")`による正規化を経てから判定する。
    """

    @pytest.mark.parametrize(
        ("file_path", "expected_flag"),
        [
            ("/home/user/dotfiles/agent-toolkit/skills/plan-mode/references/codex-review.md", "codex_review_read"),
            (
                r"C:\Users\user\dotfiles\agent-toolkit\skills\plan-mode\references\codex-review.md",
                "codex_review_read",
            ),
            (
                "/home/user/dotfiles/agent-toolkit/skills/writing-standards/references/textlint-violations.md",
                "textlint_violations_read",
            ),
            (
                r"C:\Users\user\dotfiles\agent-toolkit\skills\writing-standards\references\textlint-violations.md",
                "textlint_violations_read",
            ),
            (
                "/home/user/dotfiles/agent-toolkit/skills/plan-mode/references/plan-file-guidelines.md",
                "plan_file_guidelines_read",
            ),
            (
                r"C:\Users\user\dotfiles\agent-toolkit\skills\plan-mode\references\plan-file-guidelines.md",
                "plan_file_guidelines_read",
            ),
        ],
    )
    def test_read_sets_flag_for_both_posix_and_windows_paths(
        self,
        tmp_path: pathlib.Path,
        file_path: str,
        expected_flag: str,
    ) -> None:
        """POSIX区切り・Windows区切りいずれの`file_path`でも対応するフラグが立つ。"""
        sid = f"read-{expected_flag}-{len(file_path)}"
        _run(
            {
                "session_id": sid,
                "tool_name": "Read",
                "tool_input": {"file_path": file_path},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get(expected_flag) is True

    def test_read_unrelated_path_does_not_set_flags(self, tmp_path: pathlib.Path) -> None:
        """無関係ファイルのReadではどのフラグも立たない。"""
        sid = "read-unrelated"
        _run(
            {
                "session_id": sid,
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/random.txt"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_review_read") is not True
        assert state.get("textlint_violations_read") is not True
        assert state.get("plan_file_guidelines_read") is not True


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


class TestProcessFeedbacksFinishResetsFlag:
    """process-feedbacks-finishスキル起動検知時のフラグリセット。"""

    @pytest.mark.parametrize(
        "skill",
        ["agent-toolkit:process-feedbacks-finish", "process-feedbacks-finish"],
    )
    def test_reset_when_finish_skill_invoked(self, tmp_path: pathlib.Path, skill: str) -> None:
        """process-feedbacks-finish起動でprocess_feedbacks_skill_invokedが偽になる。"""
        sid = f"finish-{skill.replace(':', '-')}"
        # 事前にフラグを立てる。
        (tmp_path / f"claude-agent-toolkit-{sid}.json").write_text(
            json.dumps({"process_feedbacks_skill_invoked": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        _run({"session_id": sid, "tool_name": "Skill", "tool_input": {"skill": skill}}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is False

    def test_reset_idempotent_when_already_false(self, tmp_path: pathlib.Path) -> None:
        """既に偽の状態でfinishスキルが起動されても状態は変わらない。"""
        sid = "finish-idem"
        (tmp_path / f"claude-agent-toolkit-{sid}.json").write_text(
            json.dumps({"process_feedbacks_skill_invoked": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-feedbacks-finish"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is False


class TestProcessFeedbacksInvokedNonIdempotent:
    """process-feedbacksスキル再起動時のフラグ強制上書き。"""

    def test_reset_and_reinvoke_sets_flag_true(self, tmp_path: pathlib.Path) -> None:
        """finish後の再起動でフラグが確実にTrueへ戻る。"""
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
        # finish起動でリセット。
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:process-feedbacks-finish"},
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
