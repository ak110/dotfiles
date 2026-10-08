"""`_agents_server/launch_prompts.py`の振る舞いを検証する。"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from agent_toolkit._agents_server import launch_prompts, state


@pytest.mark.skipif(os.name == "nt", reason="POSIX shellによる実行の受入例")
def test_python_runtime_information_runs_json_without_python_on_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配送された起動部分から空白・引用符を含むパスのPythonでJSONを照会する。"""
    executable = tmp_path / "Python's runtime with spaces"
    executable.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(executable))
    instructions = launch_prompts.python_runtime_instructions()
    prefix = "POSIX shellでの起動部分: "
    command = next(line.removeprefix(prefix) for line in instructions.splitlines() if line.startswith(prefix))
    code = 'import json; print(json.loads("[3, 7]")[1])'
    result = subprocess.run(
        ["/bin/sh", "-c", command + " -c " + shlex.quote(code)],
        env={"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "7\n"
    assert not result.stderr


_LAUNCH_DOCUMENTS: dict[state.LaunchKind, str] = {
    "delegate": "agents-server-delegate.md",
    "explore": "agents-server-explore.md",
    "shell": "agents-server-shell.md",
    "write": "agents-server-write.md",
}


def _shared_document_body(name: str) -> str:
    """共有文書から先頭のH1見出し行と直後の空行を除いた本文を返す。

    除去する行数は共有文書の書式（1行目がH1見出し、2行目が空行）から導く。
    書式が不正な場合は比較の前に失敗させ、実装側の分割結果と偶然一致する事態を防ぐ。
    """
    lines = (launch_prompts.SHARE_DIR / name).read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert lines[0].startswith("# "), f"{name}の1行目がH1見出しではない"
    assert lines[1] == "", f"{name}の2行目が空行ではない"
    return "\n".join(lines[2:])


def test_launch_prompts_load_shared_documents() -> None:
    """起動区分ごとに対応する共有文書を読み、委譲先通知と組み合わせて実行時のシステム指示を組み立てる。

    共有文書の文面そのものは`state`モジュールの契約ではなくその文書側の内容であるため、判定対象にしない。
    文面をテストへ書き写すと、実装の契約が変わらない改訂でもこのテストが失敗する。
    """
    notice = _shared_document_body("agents-server-delegate-notice.md")

    assert notice == launch_prompts.DELEGATE_NOTICE
    for kind, document in _LAUNCH_DOCUMENTS.items():
        prompt = launch_prompts.LAUNCH_SYSTEM_PROMPTS[kind]
        assert f"{notice}\n{_shared_document_body(document)}" in prompt, kind
        assert (launch_prompts.SUBAGENT_RULES in prompt) is (kind == "delegate"), kind
    assert _shared_document_body("agents-server-auto-resume.md") in launch_prompts.AUTO_RESUME_NOTICE


def test_launch_prompts_carry_normative_boundaries() -> None:
    """system指示の各区分が、生成主体と種別を示す境界を持つこと。"""
    for kind, prompt in launch_prompts.LAUNCH_SYSTEM_PROMPTS.items():
        assert f'<{launch_prompts.NORMATIVE_ELEMENT} source="{launch_prompts.NORMATIVE_SOURCE}" kind="{kind}"' in prompt, kind
        assert prompt.endswith(f"</{launch_prompts.NORMATIVE_ELEMENT}>"), kind
    assert launch_prompts.AUTO_RESUME_NOTICE.startswith(f"<{launch_prompts.NORMATIVE_ELEMENT} ")
    assert 'kind="rules-subagent"' in launch_prompts.DELEGATE_SYSTEM_PROMPT


def test_all_launch_system_prompts_include_language_condition() -> None:
    """全modeのシステム指示が完了報告の言語を定め、通常委譲は英語の挿入指示を引き継がない条件も持つ。

    委譲プロンプトから言語の指定を外しても委譲先が日本語で返すことを、委譲元の記述に依存せず保証する。
    通常委譲の`agents-server-delegate.md`が条件を欠くと、`01-agent.md`「使用言語」が届かない委譲先
    （`~/.codex/AGENTS.md`を配置していないCodexと、Antigravity）では言語の条件が無くなり、
    実行環境が英語で挿入した指示に引きずられて英語で書いた応答は、応答言語を判定するPreToolUse hookに遮断される。
    """
    for kind, prompt in launch_prompts.LAUNCH_SYSTEM_PROMPTS.items():
        assert "日本語" in prompt, kind
    for prompt in (launch_prompts.DELEGATE_SYSTEM_PROMPT, launch_prompts.CLAUDE_DELEGATE_SYSTEM_PROMPT):
        assert "英語で挿入した指示" in prompt
        assert "応答言語として引き継がない" in prompt
