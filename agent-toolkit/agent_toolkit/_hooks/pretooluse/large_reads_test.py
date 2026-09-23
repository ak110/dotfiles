"""大量全文読取のPreToolUse検査を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read, check_large_read


def _large_file(tmp_path: pathlib.Path, name: str = "large.txt", lines: int = 351) -> pathlib.Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("line\n" * lines, encoding="utf-8")
    return path


def test_read_corrects_large_file_without_range(tmp_path: pathlib.Path) -> None:
    """範囲指定のない全文取得を、先頭の閾値行へ補正して通す。"""
    path = _large_file(tmp_path)

    corrected = check_large_read({"file_path": str(path)}, str(tmp_path))

    assert corrected is not None
    tool_input = corrected.updated_input
    assert tool_input is not None
    assert tool_input["offset"] == 1
    assert tool_input["limit"] == 350
    assert tool_input["file_path"] == str(path)


def test_notice_shows_offset_and_limit_pairs_covering_the_file(tmp_path: pathlib.Path) -> None:
    """通知本文が、実測行数と閾値から確定する`offset`と`limit`の組を全行分示す。"""
    path = _large_file(tmp_path, lines=622)

    corrected = check_large_read({"file_path": str(path)}, str(tmp_path))

    assert corrected is not None
    notice = corrected.notice
    assert "offset=1, limit=350" in notice
    assert "offset=351, limit=272" in notice


def test_read_allows_explicit_range(tmp_path: pathlib.Path) -> None:
    path = _large_file(tmp_path)

    assert check_large_read({"file_path": str(path), "limit": 100}, str(tmp_path)) is None
    assert check_large_read({"file_path": str(path), "offset": 2}, str(tmp_path)) is None


def test_read_and_bash_apply_threshold_to_mandatory_documents(tmp_path: pathlib.Path) -> None:
    """必須文書も通常ファイルと同じ閾値で分割する。"""
    paths = [
        _large_file(tmp_path, "AGENTS.md"),
        _large_file(tmp_path, "CLAUDE.md"),
        _large_file(tmp_path, "SKILL.md"),
        _large_file(tmp_path / "agent-toolkit" / "rules", "01-agent.md"),
        _large_file(tmp_path / "agent-toolkit" / "skills", "workflow.md"),
    ]

    for path in paths:
        assert check_large_read({"file_path": str(path)}, str(tmp_path)) is not None
        notice = check_large_bash_read(f"cat {path}", str(tmp_path))
        assert notice is not None
        assert "offset=1, limit=350" in notice


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


@pytest.mark.parametrize("command", ["cat", "less", "more"])
def test_bash_blocks_multiple_files_over_total_threshold(tmp_path: pathlib.Path, command: str) -> None:
    """個別には閾値以内でも、合計が閾値を超える全文取得を遮断する。"""
    first = _large_file(tmp_path, "first.txt", lines=180)
    second = _large_file(tmp_path, "second.txt", lines=171)

    notice = check_large_bash_read(f"{command} {first} {second}", str(tmp_path))

    assert notice is not None
    assert f"`{first}`: 180行" in notice
    assert f"`{second}`: 171行" in notice
    assert "合計: 351行" in notice
    assert "ファイルごと" in notice
    assert "連続した行範囲" in notice


def test_bash_blocks_multiple_files_when_one_exceeds_threshold(tmp_path: pathlib.Path) -> None:
    large = _large_file(tmp_path, "large.txt")
    small = _large_file(tmp_path, "small.txt", lines=1)

    assert check_large_bash_read(f"cat {large} {small}", str(tmp_path)) is not None


def test_bash_allows_multiple_files_within_total_threshold(tmp_path: pathlib.Path) -> None:
    first = _large_file(tmp_path, "first.txt", lines=175)
    second = _large_file(tmp_path, "second.txt", lines=175)

    assert check_large_bash_read(f"cat {first} {second}", str(tmp_path)) is None


def test_threshold_environment_override(tmp_path: pathlib.Path, monkeypatch) -> None:
    path = _large_file(tmp_path, lines=3)
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_LINES", "2")

    assert check_large_read({"file_path": str(path)}, str(tmp_path)) is not None


def test_dispatch_blocks_read_over_byte_threshold_within_line_threshold(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """行数閾値以内でもバイト閾値を超えるReadを、分割手段の案内とともに遮断する。"""
    path = tmp_path / "long-line.md"
    path.write_text("x" * 101, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "session_id": "large-byte-read",
        "cwd": str(tmp_path),
    }

    assert pretooluse.main(json.dumps(payload)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(path) in captured.err
    assert "101バイト" in captured.err
    assert "Bash" in captured.err
    assert "バイト単位" in captured.err
    assert "start_explore" in captured.err


def test_dispatch_blocks_read_when_line_and_corrected_range_exceed_thresholds(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """両閾値を超える場合も、両方に収まる先頭範囲へ補正する。"""
    path = _large_file(tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "session_id": "large-line-and-byte-read",
        "cwd": str(tmp_path),
    }

    assert pretooluse.main(json.dumps(payload)) == 0
    captured = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert captured["updatedInput"]["offset"] == 1
    assert captured["updatedInput"]["limit"] == 20
    assert "offset=21, limit=20" in captured["additionalContext"]


def test_byte_only_limit_builds_contiguous_ranges(tmp_path: pathlib.Path, monkeypatch) -> None:
    """行数が少ない場合も、バイト上限に合わせて全行を分割する。"""
    path = _large_file(tmp_path, lines=5)
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "10")

    result = check_large_read({"file_path": str(path)}, str(tmp_path))

    assert result is not None
    assert result.updated_input == {"file_path": str(path), "offset": 1, "limit": 2}
    assert "offset=3, limit=2" in result.notice
    assert "offset=5, limit=1" in result.notice


def test_oversized_later_line_has_separate_extraction_guidance(tmp_path: pathlib.Path, monkeypatch) -> None:
    """単一の長行をReadの範囲へ含めず、位置と抽出方法を示す。"""
    path = tmp_path / "mixed.txt"
    path.write_text("a\n" + "x" * 101 + "\nb\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")

    result = check_large_read({"file_path": str(path)}, str(tmp_path))

    assert result is not None
    assert result.updated_input == {"file_path": str(path), "offset": 1, "limit": 1}
    assert "2行目（102バイト）は単一行" in result.notice
    assert "offset=3, limit=1" in result.notice


def test_bash_applies_total_byte_threshold(tmp_path: pathlib.Path, monkeypatch) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("a" * 60, encoding="utf-8")
    second.write_text("b" * 41, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")

    notice = check_large_bash_read(f"cat {first} {second}", str(tmp_path))

    assert notice is not None
    assert "101バイト" in notice


def test_byte_threshold_preserves_non_line_oriented_read(tmp_path: pathlib.Path, monkeypatch) -> None:
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * 101)
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")

    assert check_large_read({"file_path": str(image)}, str(tmp_path)) is None


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
