"""大量の全文読取をPreToolUseで遮断する条件を検証する。

遮断はCodexのBash実行だけへ適用し、Claude Codeの`Read`と`Bash`は補正も遮断もしない。
"""

import json
import pathlib
import re
import subprocess

import pytest

from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read

_THRESHOLD = 48 * 1024
_RANGE_RE = re.compile(r"`sed -n '(\d+),(\d+)p' ([^`\s]+)`")


def _sized_file(tmp_path: pathlib.Path, size: int, name: str = "large.txt", line_bytes: int = 100) -> pathlib.Path:
    """1行`line_bytes`バイト（改行込み）で合計`size`バイトのファイルを作成する。"""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    full, rest = divmod(size, line_bytes)
    body = ("a" * (line_bytes - 1) + "\n") * full + "b" * rest
    path.write_bytes(body.encode("ascii"))
    return path


def _codex_read(command: str, cwd: pathlib.Path) -> str | None:
    return check_large_bash_read(command, str(cwd), is_codex=True)


def _ranges(notice: str) -> list[tuple[int, int, str]]:
    return [(int(first), int(last), path) for first, last, path in _RANGE_RE.findall(notice)]


def test_codex_byte_threshold_boundary(tmp_path: pathlib.Path) -> None:
    """48KiB以下の全文取得は通し、1バイトでも超えると遮断する。"""
    within = _sized_file(tmp_path, _THRESHOLD, "within.md")
    over = _sized_file(tmp_path, _THRESHOLD + 1, "over.md")

    assert _codex_read(f"cat {within}", tmp_path) is None
    assert _codex_read(f"cat {over}", tmp_path) is not None


def test_codex_notice_ranges_cover_all_lines_within_threshold(tmp_path: pathlib.Path) -> None:
    """通知の範囲案は`sed -n`の形で全行を重複も欠落もなく覆い、各範囲が閾値以下になる。

    範囲案どおりの取得が再び遮断されると、通知が正しい操作のたびに反復する。
    """
    path = _sized_file(tmp_path, _THRESHOLD * 2 + 5000, "agent-toolkit/rules/01-agent.md")
    lines = path.read_bytes().splitlines(keepends=True)

    notice = _codex_read(f"cat {path}", tmp_path)

    assert notice is not None
    ranges = _ranges(notice)
    assert len(ranges) >= 3
    expected_first = 1
    for first, last, quoted in ranges:
        assert first == expected_first
        assert quoted == str(path)
        assert sum(len(line) for line in lines[first - 1 : last]) <= _THRESHOLD
        assert _codex_read(f"sed -n '{first},{last}p' {path}", tmp_path) is None
        expected_first = last + 1
    assert expected_first == len(lines) + 1


def test_codex_notice_range_command_reproduces_file(tmp_path: pathlib.Path) -> None:
    """範囲案のコマンドを順に実行した出力の連結が元のファイルと一致する。"""
    path = _sized_file(tmp_path / "with space", _THRESHOLD + 300, "large.md")

    notice = _codex_read(f"cat '{path}'", tmp_path)

    assert notice is not None
    commands = re.findall(r"`(sed -n '\d+,\d+p' [^`]+)`", notice)
    assert commands
    output = b"".join(subprocess.run(["bash", "-c", command], capture_output=True, check=True).stdout for command in commands)
    assert output == path.read_bytes()


def test_codex_notice_reports_oversized_single_line(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "minified.js"
    path.write_bytes(b"x" * (_THRESHOLD + 10) + b"\nshort\n")

    notice = _codex_read(f"cat {path}", tmp_path)

    assert notice is not None
    assert f"1行目（{_THRESHOLD + 11}バイト）" in notice
    assert _ranges(notice) == [(2, 2, str(path))]


@pytest.mark.parametrize("change", ["cd", "pushd"])
@pytest.mark.parametrize("separator", ["&&", ";"])
def test_codex_resolves_relative_path_after_cwd_change(tmp_path: pathlib.Path, change: str, separator: str) -> None:
    target = _sized_file(tmp_path / "nested", _THRESHOLD + 1)

    notice = _codex_read(f"{change} {target.parent} {separator} cat {target.name}", tmp_path)

    assert notice is not None
    assert str(target) in notice


def test_codex_unresolved_cwd_change_uses_payload_cwd(tmp_path: pathlib.Path) -> None:
    target = _sized_file(tmp_path, _THRESHOLD + 1)

    notice = _codex_read('cd "$DIR" && cat large.txt', tmp_path)

    assert notice is not None
    assert str(target) in notice


def test_codex_blocks_only_direct_static_full_reads(tmp_path: pathlib.Path) -> None:
    path = _sized_file(tmp_path, _THRESHOLD + 1)
    image = _sized_file(tmp_path, _THRESHOLD + 1, "capture.png")

    assert _codex_read(f"cat {path}", tmp_path) is not None
    assert _codex_read(f"sed -n p {path}", tmp_path) is not None
    assert _codex_read(f"awk '{{print}}' {path}", tmp_path) is not None
    assert _codex_read(f"cat {image}", tmp_path) is not None
    assert _codex_read(f"cat {path} | rg value", tmp_path) is None
    assert _codex_read(f"cat {path} > output.txt", tmp_path) is None


@pytest.mark.parametrize("command", ["cat", "less", "more"])
def test_codex_blocks_multiple_files_over_total_threshold(tmp_path: pathlib.Path, command: str) -> None:
    """個別には閾値以内でも、合計が閾値を超える全文取得を遮断する。"""
    first = _sized_file(tmp_path, 30 * 1024, "first.txt")
    second = _sized_file(tmp_path, 20 * 1024, "second.txt")
    within_first = _sized_file(tmp_path, 24 * 1024, "within-first.txt")
    within_second = _sized_file(tmp_path, 24 * 1024, "within-second.txt")

    notice = _codex_read(f"{command} {first} {second}", tmp_path)

    assert notice is not None
    assert f"`{first}`: " in notice
    assert f"`{second}`: " in notice
    assert f"合計: {50 * 1024}バイト" in notice
    assert _codex_read(f"{command} {within_first} {within_second}", tmp_path) is None


def test_codex_applies_environment_override(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("a" * 60, encoding="utf-8")
    second.write_text("b" * 41, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")

    notice = _codex_read(f"cat {first} {second}", tmp_path)

    assert notice is not None
    assert "101バイト" in notice


@pytest.mark.parametrize("tool_name", ["Read", "Bash"])
def test_dispatch_claude_large_read_passes_without_correction(tmp_path: pathlib.Path, capsys, tool_name: str) -> None:
    """Claude Codeでは`Read`と`cat`の大量読取を補正も遮断もしない。

    ホストが上限超過を`PARTIAL view`または退避ファイルとして返し、残りを続けて取得できるためである。
    """
    path = _sized_file(tmp_path, _THRESHOLD + 1)
    tool_input = {"file_path": str(path)} if tool_name == "Read" else {"command": f"cat {path}"}
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "claude-large-read",
        "cwd": str(tmp_path),
    }

    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_dispatch_codex_blocks_bash_full_read(tmp_path: pathlib.Path, capsys) -> None:
    """`turn_id`を持つCodex payloadでは、相対パスの全文取得を遮断し、範囲案を示す。"""
    target = _sized_file(tmp_path / "nested", _THRESHOLD + 1)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"cd {target.parent} && cat {target.name}"},
        "session_id": "codex-large-read",
        "cwd": str(tmp_path),
        "turn_id": "codex-turn",
    }

    assert pretooluse.main(json.dumps(payload)) == 2
    err = capsys.readouterr().err
    assert str(target) in err
    assert "sed -n '1," in err
