"""大量全文読取のPreToolUse検査を検証する。"""

import json
import pathlib

from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read, check_large_read


def _large_file(tmp_path: pathlib.Path, name: str = "large.txt", lines: int = 351) -> pathlib.Path:
    path = tmp_path / name
    path.write_text("line\n" * lines, encoding="utf-8")
    return path


def test_read_blocks_large_file_without_range(tmp_path: pathlib.Path) -> None:
    path = _large_file(tmp_path)

    assert check_large_read({"file_path": str(path)}, str(tmp_path)) is not None


def test_read_allows_explicit_range_and_mandatory_document(tmp_path: pathlib.Path) -> None:
    path = _large_file(tmp_path)
    instructions = _large_file(tmp_path, "AGENTS.md")

    assert check_large_read({"file_path": str(path), "limit": 100}, str(tmp_path)) is None
    assert check_large_read({"file_path": str(path), "offset": 2}, str(tmp_path)) is None
    assert check_large_read({"file_path": str(instructions)}, str(tmp_path)) is None


def test_bash_blocks_only_direct_static_full_reads(tmp_path: pathlib.Path) -> None:
    path = _large_file(tmp_path)

    assert check_large_bash_read(f"cat {path}", str(tmp_path)) is not None
    assert check_large_bash_read(f"sed -n p {path}", str(tmp_path)) is not None
    assert check_large_bash_read(f"awk '{{print}}' {path}", str(tmp_path)) is not None
    assert check_large_bash_read(f"cat {path} | rg value", str(tmp_path)) is None
    assert check_large_bash_read(f"cat {path} > output.txt", str(tmp_path)) is None


def test_threshold_environment_override(tmp_path: pathlib.Path, monkeypatch) -> None:
    path = _large_file(tmp_path, lines=3)
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_LINES", "2")

    assert check_large_read({"file_path": str(path)}, str(tmp_path)) is not None


def test_dispatch_blocks_large_read(tmp_path: pathlib.Path, capsys) -> None:
    path = _large_file(tmp_path)
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "session_id": "large-read",
        "cwd": str(tmp_path),
    }

    assert pretooluse.main(json.dumps(payload)) == 2
    assert str(path) in capsys.readouterr().err
