"""agent-toolkit/scripts/posttooluse.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
plan file形式検査・SSOT検査・codex-review.md読み込み追跡は
`posttooluse_plan_format_test.py`へ分割している。
"""

import functools
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import types

import _plan_file
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
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
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
        result = _run(
            {"session_id": sid, "tool_input": {"command": command}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is True, f"command={command!r} not detected"

    def test_unrelated_command_no_change(self, tmp_path: pathlib.Path):
        sid = "test-unrelated"
        _run(
            {"session_id": sid, "tool_input": {"command": "echo hello"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is not True


class TestGitStatusCheck:
    """Git 状態確認検出。"""

    @pytest.mark.parametrize("command", ["git status", "git log --decorate --oneline -5", "git diff"])
    def test_git_commands_detected(self, tmp_path: pathlib.Path, command: str):
        sid = "test-git-status"
        _run(
            {"session_id": sid, "tool_input": {"command": command}},
            state_dir=tmp_path,
        )
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
        result = _run(
            {"session_id": "silent", "tool_input": {"command": "pytest"}},
            state_dir=tmp_path,
        )
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
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline -5"}},
            state_dir=tmp_path,
        )
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
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("git_log_checked") is True
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git commit -m 'x'"}},
            state_dir=tmp_path,
        )
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
        _run(
            {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
            state_dir=tmp_path,
        )
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


class TestPrelintSuccessRecord:
    """Bash経由のpyfltr事前lint検査成功記録。

    対象パターンBashが`{"kind":"summary","exit":0,...}`を含む出力で成功した場合、
    対象scratchpadファイルの全文SHA256と`## 背景`配下`text`コードブロック除去後SHA256を
    `plan_prelint_passed`リストへ追加する。
    """

    _SUCCESS_OUTPUT = '{"kind":"summary","exit":0,"foo":1}'

    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _strip_bg_text(content: str) -> str:
        return _plan_file.strip_background_text_blocks(content)

    _LINT_COMMAND = "pyfltr run-for-agent --commands=textlint,markdownlint,typos,colloquial-check --enable=colloquial-check"

    def _make_lint_file(self, tmp_path: pathlib.Path, content: str = "# plan\n") -> pathlib.Path:
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        target = scratch / "plan-prelint.md"
        target.write_text(content, encoding="utf-8")
        return target

    def test_success_records_two_hashes(self, tmp_path: pathlib.Path):
        target = self._make_lint_file(tmp_path)
        sid = "prelint-success"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {target}",
                },
                "tool_response": {"output": self._SUCCESS_OUTPUT},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        recorded = state.get("plan_prelint_passed", [])
        assert isinstance(recorded, list)
        content = target.read_text(encoding="utf-8")
        assert self._sha(content) in recorded
        assert self._sha(self._strip_bg_text(content)) in recorded

    def test_uvx_prefix_also_matched(self, tmp_path: pathlib.Path):
        target = self._make_lint_file(tmp_path)
        sid = "prelint-uvx"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"uvx {self._LINT_COMMAND} {target}",
                },
                "tool_response": {"output": self._SUCCESS_OUTPUT},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        recorded = state.get("plan_prelint_passed", [])
        assert recorded  # 非空

    def test_no_success_marker_no_record(self, tmp_path: pathlib.Path):
        target = self._make_lint_file(tmp_path)
        sid = "prelint-no-marker"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {target}",
                },
                "tool_response": {"output": "no summary line here"},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state

    def test_interrupted_no_record(self, tmp_path: pathlib.Path):
        target = self._make_lint_file(tmp_path)
        sid = "prelint-interrupted"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {target}",
                },
                "tool_response": {"output": self._SUCCESS_OUTPUT, "interrupted": True},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state

    def test_non_zero_exit_summary_no_record(self, tmp_path: pathlib.Path):
        target = self._make_lint_file(tmp_path)
        sid = "prelint-exit1"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {target}",
                },
                "tool_response": {"output": '{"kind":"summary","exit":1}'},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state

    def test_missing_file_no_record(self, tmp_path: pathlib.Path):
        sid = "prelint-missing"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {tmp_path}/missing.md",
                },
                "tool_response": {"output": self._SUCCESS_OUTPUT},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state

    def test_unrelated_bash_no_record(self, tmp_path: pathlib.Path):
        sid = "prelint-unrelated"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
                "tool_response": {"output": self._SUCCESS_OUTPUT},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state

    @pytest.mark.parametrize(
        "command_suffix",
        [
            "&& echo ok",
            "|| echo fail",
            "; echo trail",
            "| cat",
            "& sleep 1",
        ],
    )
    def test_shell_operator_compound_no_record(self, tmp_path: pathlib.Path, command_suffix: str):
        target = self._make_lint_file(tmp_path)
        sid = f"prelint-compound-{command_suffix[:2]}"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {target} {command_suffix}",
                },
                "tool_response": {"output": self._SUCCESS_OUTPUT},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state

    def test_newline_separated_no_record(self, tmp_path: pathlib.Path):
        target = self._make_lint_file(tmp_path)
        sid = "prelint-newline"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {target}\necho done",
                },
                "tool_response": {"output": self._SUCCESS_OUTPUT},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state

    def test_extra_suffix_no_record(self, tmp_path: pathlib.Path):
        target = self._make_lint_file(tmp_path)
        sid = "prelint-suffix"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"{self._LINT_COMMAND} {target} --extra-flag",
                },
                "tool_response": {"output": self._SUCCESS_OUTPUT},
                "session_id": sid,
                "cwd": str(tmp_path),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert "plan_prelint_passed" not in state


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
