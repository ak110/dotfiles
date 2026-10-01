# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/agent_toolkit/_hooks/pretooluse/task_document_launch.py のテスト。

PreToolUseフックをsubprocessで起動し、配布物のタスク文書を指す起動文の終了コードと通知を検証する。
"""

import pathlib
import subprocess

import pytest

from agent_toolkit._hooks.pretooluse.test_support_test import *  # noqa: F403

_EXEC_DOCUMENT = _SHARE_DIR / "exec.subagent.md"


def _invoke(tool_name: str, prompt: str, tmp_path: pathlib.Path, mode: str | None = None) -> subprocess.CompletedProcess[str]:
    tool_input = {"prompt": prompt, "cwd": str(tmp_path), "model_type": "high_tier"}
    if mode is not None:
        tool_input["mode"] = mode
    return _run(
        {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "session_id": f"task-document-launch-{tool_name.rsplit('__', 1)[-1].lower()}-{mode}",
            "cwd": str(tmp_path),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )


@pytest.mark.parametrize(
    ("tool_name", "mode"),
    [
        ("mcp__plugin_agent-toolkit_agents_server__start", "delegate"),
        ("mcp__plugin_agent-toolkit_agents_server__start", "explore"),
        ("mcp__agents_server__start", "write"),
    ],
)
def test_free_text_start_pointing_task_document_is_blocked(tool_name: str, mode: str, tmp_path: pathlib.Path) -> None:
    """自由本文のmodeでタスク文書を指すと遮断し、taskの`start`と`subagent_md_path`での起動を案内する。

    通すと、タスク文書の宣言を経ない起動文が委譲先のコンテキストへ取り込まれる。
    """
    result = _invoke(tool_name, f"{_EXEC_DOCUMENT}の手順を実行せよ。\n担当種別: レーン担当\n", tmp_path, mode)

    assert result.returncode == 2
    assert f"subagent_md_path={_EXEC_DOCUMENT}" in result.stderr
    assert f"`start`の`{mode}`" in result.stderr


@pytest.mark.parametrize("mode", [None, "shell"])
def test_task_and_shell_modes_are_not_free_text_starts(mode: str | None, tmp_path: pathlib.Path) -> None:
    """taskとshellは`prompt`を受理しないmodeであり、本文の遮断対象にしない。入力の拒否はサーバーが行う。"""
    result = _invoke("mcp__plugin_agent-toolkit_agents_server__start", f"{_EXEC_DOCUMENT}の手順を実行せよ。\n", tmp_path, mode)

    assert result.returncode == 0


def test_free_text_start_without_task_document_passes(tmp_path: pathlib.Path) -> None:
    """タスク文書を指さない自由本文の起動は遮断しない。"""
    result = _invoke("mcp__plugin_agent-toolkit_agents_server__start", "対象の所在を調べて返す。", tmp_path, "explore")

    assert result.returncode == 0


def test_agent_task_document_prompt_allows_only_declared_lines(tmp_path: pathlib.Path) -> None:
    """`Agent`の本文は1行目の命令と宣言済み入力の行だけで通り、役割宣言などの行を含むと遮断する。

    宣言済みの入力には、字下げした続きの行を持つ値を含める。
    """
    minimal = "\n".join(
        [
            f"{_EXEC_DOCUMENT}の手順を実行せよ。",
            "担当種別: レーン担当",
            "引き継ぎ記録先:",
            f"  {tmp_path / 'handoff.md'}（新規）",
            "  ",
            "レーン識別子: lane-01",
        ]
    )
    passed = _invoke("Agent", minimal, tmp_path)
    blocked = _invoke("Agent", minimal + "\nあなたはレーン担当である。権限はcommitまでとする。", tmp_path)

    assert passed.returncode == 0
    assert blocked.returncode == 2
    assert "あなたはレーン担当である。" in blocked.stderr
    assert "レーン識別子" in blocked.stderr


def test_reader_fit_review_inputs_pass_task_document_hook(tmp_path: pathlib.Path) -> None:
    """初回3入力と再レビュー入力を通し、宣言外補足の拒否を維持する。"""
    task = _SHARE_DIR / "reader-fit-review.subagent.md"
    initial = f"{task}の手順を実行せよ。\n成果物: {tmp_path / 'guide.md'}\n種別: 利用者向け文書\n読者像: 初めて導入する利用者\n"
    rereview = initial + (
        f"レビュー種別: 再レビュー\n修正範囲: {tmp_path / 'before.md'}と成果物の保存節の差分\n未解決事項: なし\n"
    )
    assert _invoke("Agent", initial, tmp_path).returncode == 0
    assert _invoke("Agent", rereview, tmp_path).returncode == 0
    rejected = _invoke("Agent", rereview + "追加説明: 全文から指摘して\n", tmp_path)
    assert rejected.returncode == 2
    assert "追加説明" in rejected.stderr


def test_exec_review_previous_revision_passes_task_document_hook(tmp_path: pathlib.Path) -> None:
    """引き継ぎで前回確認版を渡しても宣言外入力として遮断しない。"""
    task = _SHARE_DIR / "exec-review.subagent.md"
    prompt = (
        f"{task}の手順を実行せよ。\nレビュー基準: 計画\n計画: {tmp_path / 'plan.md'}\n"
        f"引き継ぎ記録先: {tmp_path / 'handoff.md'}\n完成条件証拠: なし\n"
        f"レビュー種別: 引き継ぎ再レビュー\nround: 2\n前回確認版: {tmp_path / 'previous.md'}\n"
    )
    assert _invoke("Agent", prompt, tmp_path).returncode == 0


def test_agent_prompt_with_role_preface_before_command_is_blocked(tmp_path: pathlib.Path) -> None:
    """1行目が命令でない本文は、命令の前に置いた前置きを宣言外の行として遮断する。"""
    prompt = f"あなたは実装担当である。\n{_EXEC_DOCUMENT}の手順を実行せよ。\n担当種別: レーン担当\n"

    result = _run(
        {"tool_name": "Task", "tool_input": {"prompt": prompt}, "session_id": "task-document-launch-task"},
        env_overrides=_plan_file_state_env(tmp_path),
    )

    assert result.returncode == 2
    assert "あなたは実装担当である。" in result.stderr
