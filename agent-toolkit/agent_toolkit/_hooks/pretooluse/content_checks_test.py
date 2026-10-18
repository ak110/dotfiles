# pylint: disable=protected-access
"""agent-toolkit/agent_toolkit/_hooks/pretooluse/content_checks.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import pathlib

import pytest

from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _plan_file_state_env,
    _read_session_state,
    _run,
    _write_session_state,
)


def _writing_ready_env(tmp_path: pathlib.Path, session_id: str) -> dict[str, str]:
    """執筆基準を取得済みの主体で、編集内容の判定を確かめる。"""
    _write_session_state(
        tmp_path, session_id, {"operation_skill_delivered_agents": {"agent-toolkit:writing-standards": ["main"]}}
    )
    return _plan_file_state_env(tmp_path)


@pytest.mark.parametrize("suffix", [".py", ".md"])
def test_trailing_tool_boundary_tags_warn(
    suffix: str,
    tmp_path: pathlib.Path,
) -> None:
    """Pythonと計画Markdownの末尾へ混入したツール境界タグを警告する。"""
    if suffix == ".md":
        target = tmp_path / ".claude/plans/example.md"
        target.parent.mkdir(parents=True)
    else:
        target = tmp_path / "example.py"
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "本文\n</content>\n</invoke>\n"},
            "session_id": f"boundary-{suffix}",
        },
        env_overrides={**_writing_ready_env(tmp_path, f"boundary-{suffix}"), "HOME": str(tmp_path)},
    )
    assert result.returncode == 0
    assert "ツール境界タグ" in result.stdout


def test_trailing_tool_boundary_tags_in_regular_markdown_are_allowed(tmp_path: pathlib.Path) -> None:
    """計画以外のMarkdownではツール境界タグを判定しない。"""
    result = _run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "note.md"), "content": "本文\n</content>\n</invoke>\n"},
            "session_id": "boundary-regular-markdown",
        },
        env_overrides=_writing_ready_env(tmp_path, "boundary-regular-markdown"),
    )
    assert result.returncode == 0


class TestLanguageRewrite:
    """質問の直接入力を毎回判定し、旧transcript判定の状態で通過させない。"""

    @pytest.mark.parametrize("text,expected", [("Read the evidence.", 2), ("根拠を確認します。", 0), ("了解した。", 0)])
    def test_current_text_controls_decision_with_legacy_state(self, tmp_path: pathlib.Path, text: str, expected: int) -> None:
        sid = "language-current-text"
        legacy = {"english_warning_count": 2, "english_warning_msg_id": "same", "unrelated": True}
        _write_session_state(tmp_path, sid, legacy)
        payload = {
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": text, "header": "確認", "options": []}]},
            "session_id": sid,
        }
        for _ in range(2):
            result = _run(payload, env_overrides=_plan_file_state_env(tmp_path))
            assert result.returncode == expected
            saved = _read_session_state(tmp_path, sid)
            assert all(saved[key] == value for key, value in legacy.items())
            if expected == 2:
                assert "日本語で書き直す" in result.stderr
                assert "識別子と名前" in result.stderr
                assert result.stderr.rstrip().endswith("</atk-auto>")

    def test_control_characters_in_input_do_not_leak_into_notice(self, tmp_path: pathlib.Path) -> None:
        result = _run(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [{"question": "The process \x1b[2J \x00 finished.", "header": "確認", "options": []}]
                },
            },
            env_overrides=_plan_file_state_env(tmp_path),
        )
        assert result.returncode == 2
        assert "\x1b" not in result.stderr
        assert "\x00" not in result.stderr


class TestGeneralBehavior:
    """統合スクリプト共通の振る舞い。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # Write/Edit/MultiEdit以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo \ufffd"}},
            # tool_inputが欠落していても通す
            {"tool_name": "Write"},
            # 正常な日本語は通す
            {"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "こんにちは世界"}},
        ],
    )
    def test_allowed(self, payload: dict):
        result = _run(payload)
        assert result.returncode == 0

    def test_invalid_json(self):
        """不正JSONはフックを無効化（安全側）。"""
        result = _run("this is not json")
        assert result.returncode == 0


@pytest.mark.parametrize(
    ("name", "body", "required"),
    [
        ("new.ps1", "本文\n", "BOMと改行"),
        ("uv.lock", "version = 1\n", "依存変更または再生成"),
        ("pyproject.toml", "dependencies = []\n", "依存変更と`lockfile`の同期"),
    ],
)
def test_edit_warning_supports_command_restricted_agent(tmp_path, name, body, required):
    """編集だけを持つ担当も、対象と必要な処置を返して担当範囲内で引き渡せる。"""
    target = tmp_path / name
    result = _run(
        {"tool_name": "Write", "tool_input": {"file_path": str(target), "content": body}, "session_id": "restricted-" + name},
        env_overrides={**_writing_ready_env(tmp_path, "restricted-" + name), "HOME": str(tmp_path)},
    )
    assert result.returncode == 0
    notice = _additional_context(result)
    assert "委譲元へ返す" in notice and "対象パス" in notice
    assert required in notice and "許可される担当" in notice
