"""振り返り手順の実行例が登録済みコマンドに到達することを確かめる。"""

import pathlib
import shlex
from typing import Any

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._testing import cli_examples


def test_session_review_documents_use_public_script_entries() -> None:
    """振り返りの手順書の実行例が登録済みのコマンドを使い、実装ファイルを直接起動しない。"""
    documents = (run_script.PLUGIN_ROOT / "skills/review-session/references/current-session.md",)
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
        assert "--work-dir" not in command
        assert run_script.registered_script_path(command[2]).is_file()
        cli_examples.validate_example(shlex.join(command), source=str(documents[0]))


def test_document_option_mutation_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """実物の案内だけの未登録名への変更が、所在付きで失敗する。"""
    document = run_script.PLUGIN_ROOT / "skills/review-session/references/current-session.md"
    original = pathlib.Path.read_text

    def mutated(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        text = original(path, *args, **kwargs)
        return text.replace("--target-repo", "--unsupported-r2") if path == document else text

    monkeypatch.setattr(pathlib.Path, "read_text", mutated)
    with pytest.raises(AssertionError, match="unsupported-r2"):
        test_session_review_documents_use_public_script_entries()


def test_receiver_required_input_mutation_is_detected() -> None:
    """受理側だけが必須入力を増やす変更も、文書の実物との不一致として検出する。"""
    parser = cli_examples.registered_parser("session-review-prepare")
    parser.add_argument("--required-contract-input", required=True)
    document = run_script.PLUGIN_ROOT / "skills/review-session/references/current-session.md"
    with pytest.raises(AssertionError, match="required-contract-input"):
        cli_examples.validate_document(document, parsers={"session-review-prepare": parser})
