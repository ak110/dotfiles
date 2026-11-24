# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_languageの契約テスト。"""

import json
import pathlib
import sys

import pytest

from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, _write_session_state
from agent_toolkit._testing.pretooluse_support import run_without_skill_notices as _run


class TestResponseLanguageCheck:
    @pytest.mark.parametrize("delegated", ["0", "1"])
    def test_transcript_language_does_not_block_current_operation(self, tmp_path: pathlib.Path, delegated: str) -> None:
        transcript = tmp_path / "transcript.jsonl"
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "file")}, "transcript_path": str(transcript)}
        env = {**_plan_file_state_env(tmp_path), "AGENT_TOOLKIT_DELEGATED_SESSION": delegated}

        def write(text: str) -> None:
            transcript.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": text}]},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

        write("Read the draft and evidence.")
        first = _run(payload, env_overrides=env)
        repeated = _run(payload, env_overrides=env)
        assert first.returncode == repeated.returncode == 0
        write("原稿と根拠を読みます。")
        assert _run(payload, env_overrides=env).returncode == 0

    def test_subagent_pretooluse_leaves_parent_transcript(self, tmp_path: pathlib.Path) -> None:
        transcript = tmp_path / "parent.jsonl"
        transcript.write_text(
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Read the draft."}]}}),
            encoding="utf-8",
        )
        result = _run({"agent_id": "child", "tool_name": "Read", "tool_input": {}, "transcript_path": str(transcript)})
        assert result.returncode == 0

    @pytest.mark.parametrize(
        "tool_name,tool_input",
        [
            (
                "AskUserQuestion",
                {"questions": [{"question": "Which option should we choose?", "header": "確認", "options": []}]},
            ),
            ("ExitPlanMode", {"plan": "Implement the planned changes."}),
        ],
    )
    def test_blocks_english_user_facing_input(self, tool_name: str, tool_input: dict) -> None:
        result = _run({"tool_name": tool_name, "tool_input": tool_input})
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "日本語で書き直してください" in reason
        assert "原表記を保ち" in reason
        assert reason.count("判定できない") == 1
        assert "`Now`" not in reason and "`Next`" not in reason
        assert not result.stderr


class TestRemovedChecksAreSilent:
    @pytest.mark.parametrize(
        "command",
        [
            "python -c 'x = 1; print(x)'",
            "sh -c 'echo ok'",
            "cat /nonexistent-directory/missing.txt",
            "git commit -m 'message without attribution'",
            "git commit --amend --no-edit",
            "git log -3",
            "git add -A",
            "sleep 500; ls",
            "pytest | tail -5",
            "grep -r keyword .",
            "rg keyword ~/.local",
            "cat .env",
            "echo a(b)",
            "atk wi list --unknown-option",
            "codex exec 'task'",
            "uv run python script.py",
        ],
    )
    def test_bash_input_passes_without_output(self, tmp_path: pathlib.Path, command: str) -> None:
        _write_session_state(tmp_path, "removed-bash", {})
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "removed-bash", "cwd": str(tmp_path)},
            env_overrides=_plan_file_state_env(tmp_path),
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    @pytest.mark.parametrize("deny_substring", ["必ず", "絶対に"])
    def test_edit_and_non_edit_tools_pass_without_output(
        self, tmp_path: pathlib.Path, deny_substring: str, monkeypatch
    ) -> None:
        monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])
        home = tmp_path / "home"
        (home / ".claude" / "plans").mkdir(parents=True)
        payloads = [
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / "doc.md"), "content": f"概要は{deny_substring}該当する。\n"},
            },
            {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "doc.py"), "content": f"PATH = '{home}/a'\n"}},
            {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "id_rsa"), "content": "key\n"}},
            {
                "tool_name": "Write",
                "tool_input": {"file_path": str(home / ".claude" / "plans" / "plan.md"), "content": "# 計画\n"},
            },
            {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / ".env")}},
            {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com", "prompt": "全文を返す"}},
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "project-reviewer", "prompt": "調べる", "description": "調査"},
            },
            {"tool_name": "SendMessage", "tool_input": {"to": "general-purpose", "message": "続ける"}},
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [{"question": f"概要は{deny_substring}該当する。", "header": "確認", "options": []}]
                },
            },
        ]
        for index, payload in enumerate(payloads):
            _write_session_state(
                tmp_path,
                f"removed-tool-{index}",
                {},
            )
            result = _run(
                {**payload, "session_id": f"removed-tool-{index}", "cwd": str(tmp_path)},
                env_overrides=_plan_file_state_env(tmp_path, home_dir=home),
            )

            assert result.returncode == 0, (payload, result.stderr, result.stdout)
            assert result.stdout == "", payload
            assert result.stderr == "", payload


@pytest.mark.cpu_limit(
    3.0,
    reason=(
        "旧11回周期の再注入を検出するため公開Preを最小12回反復する。対象外のスキルの案内は分離済み（Linux限定再確認のCP"
        "U合計最大1.373秒）。"
    ),
)
def test_repeated_pretooluse_does_not_reinject_language(tmp_path: pathlib.Path) -> None:
    for _ in range(12):
        result = _run(
            {"session_id": "no-language-reinjection", "tool_name": "Read", "tool_input": {}},
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 0
        assert not result.stderr
        assert "ツール呼び出し前の短いステータス行も含めて日本語" not in result.stdout


@pytest.mark.parametrize(
    ("extra_payload", "expected_warned"),
    [({}, True), ({"turn_id": "codex-turn"}, False)],
    ids=["claude-code", "codex"],
)
def test_bash_drive_letter_path_warning_reaches_additional_context(
    monkeypatch: pytest.MonkeyPatch,
    extra_payload: dict[str, str],
    expected_warned: bool,
) -> None:
    """Windows上のClaude CodeのBashでは、PATHのドライブ文字形式の警告が`additionalContext`へ届く。

    Codexのpayload（`turn_id`を持つ）はPowerShellでシェルを実行するため警告しない。
    """
    monkeypatch.setattr(sys, "platform", "win32")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": 'T="C:/x"; export PATH="$T/bin:$PATH"; command -v jq'},
        "session_id": "dispatch-drive-letter-path",
        **extra_payload,
    }
    result = _run(payload)
    assert result.returncode == 0
    out = result.stdout
    assert ("`PATH`へドライブ文字形式の要素" in out) is expected_warned
