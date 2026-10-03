"""振り返り手順の実行例が登録済みコマンドに到達することを確かめる。"""

import shlex

import pytest

from agent_toolkit._atk import run_script

pytestmark = pytest.mark.repo_invariant


def test_session_review_documents_use_public_script_entries() -> None:
    """振り返りの手順書の実行例が登録済みのコマンドを使い、実装ファイルを直接起動しない。"""
    documents = (run_script.PLUGIN_ROOT / "skills" / "session-review" / "SKILL.md",)
    commands: list[list[str]] = []
    for document in documents:
        in_block = False
        for line in document.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "```text":
                in_block = True
            elif stripped == "```":
                in_block = False
            elif in_block and stripped.startswith("atk run-script session-review-"):
                commands.append(shlex.split(stripped))

    assert {command[2] for command in commands} == {"session-review-prepare"}
    for command in commands:
        assert command[:2] == ["atk", "run-script"]
        assert command[3] == "--"
        assert run_script.registered_script_path(command[2]).is_file()
