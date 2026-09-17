"""大量全文読取のPreToolUse検査を検証する。"""

import json
import pathlib

from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read, check_large_read


def _large_file(tmp_path: pathlib.Path, name: str = "large.txt", lines: int = 351) -> pathlib.Path:
    path = tmp_path / name
    path.write_text("line\n" * lines, encoding="utf-8")
    return path


def test_read_corrects_large_file_without_range(tmp_path: pathlib.Path) -> None:
    """範囲指定のない全文取得を、先頭の閾値行へ補正して通す。"""
    path = _large_file(tmp_path)

    corrected = check_large_read({"file_path": str(path)}, str(tmp_path))

    assert corrected is not None
    tool_input, _notice = corrected
    assert tool_input["offset"] == 1
    assert tool_input["limit"] == 350
    assert tool_input["file_path"] == str(path)


def test_notice_shows_offset_and_limit_pairs_covering_the_file(tmp_path: pathlib.Path) -> None:
    """通知本文が、実測行数と閾値から確定する`offset`と`limit`の組を全行分示す。"""
    path = _large_file(tmp_path, lines=622)

    corrected = check_large_read({"file_path": str(path)}, str(tmp_path))

    assert corrected is not None
    _tool_input, notice = corrected
    assert "offset=1, limit=350" in notice
    assert "offset=351, limit=272" in notice


def test_read_allows_explicit_range_and_mandatory_document(tmp_path: pathlib.Path) -> None:
    path = _large_file(tmp_path)
    instructions = _large_file(tmp_path, "AGENTS.md")

    assert check_large_read({"file_path": str(path), "limit": 100}, str(tmp_path)) is None
    assert check_large_read({"file_path": str(path), "offset": 2}, str(tmp_path)) is None
    assert check_large_read({"file_path": str(instructions)}, str(tmp_path)) is None


def test_read_allows_non_line_oriented_formats(tmp_path: pathlib.Path) -> None:
    image = _large_file(tmp_path, "capture.PNG")
    document = _large_file(tmp_path, "manual.pdf")
    text = _large_file(tmp_path, "notes.txt")

    assert check_large_read({"file_path": str(image)}, str(tmp_path)) is None
    assert check_large_read({"file_path": str(document)}, str(tmp_path)) is None
    assert check_large_read({"file_path": str(text)}, str(tmp_path)) is not None


def test_bash_blocks_non_line_oriented_full_reads(tmp_path: pathlib.Path) -> None:
    image = _large_file(tmp_path, "capture.png")

    assert check_large_bash_read(f"cat {image}", str(tmp_path)) is not None


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


def test_dispatch_corrects_large_read(tmp_path: pathlib.Path, capsys) -> None:
    """`Read`の全文取得を遮断せず、範囲を補正した入力で通す。"""
    path = _large_file(tmp_path)
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "session_id": "large-read",
        "cwd": str(tmp_path),
    }

    assert pretooluse.main(json.dumps(payload)) == 0
    captured = json.loads(capsys.readouterr().out)
    output = captured["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"]["offset"] == 1
    assert output["updatedInput"]["limit"] == 350
    assert str(path) in output["additionalContext"]


def test_dispatch_still_blocks_bash_full_read(tmp_path: pathlib.Path, capsys) -> None:
    """`Bash`の全文取得は遮断のまま保つ。"""
    path = _large_file(tmp_path)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"cat {path}"},
        "session_id": "large-bash-read",
        "cwd": str(tmp_path),
    }

    assert pretooluse.main(json.dumps(payload)) == 2
    assert str(path) in capsys.readouterr().err
