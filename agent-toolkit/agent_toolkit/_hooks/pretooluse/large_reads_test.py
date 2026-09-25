"""大量全文読取のPreToolUse検査を検証する。

遮断はCodexのBash経路だけへ適用し、Claude Codeの`Read`と`Bash`は補正も遮断もしない。
"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read


def _large_file(tmp_path: pathlib.Path, name: str = "large.txt", lines: int = 351) -> pathlib.Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("line\n" * lines, encoding="utf-8")
    return path


def _codex_read(command: str, cwd: pathlib.Path) -> str | None:
    return check_large_bash_read(command, str(cwd), is_codex=True)


def test_codex_blocks_agent_documents_with_ranges(tmp_path: pathlib.Path) -> None:
    """Codexではコーディングエージェント向け文書も分割へ誘導し、全行分の範囲を示す。"""
    paths = [
        _large_file(tmp_path, "AGENTS.md"),
        _large_file(tmp_path / "agent-toolkit" / "rules", "01-agent.md"),
    ]

    for path in paths:
        notice = _codex_read(f"cat {path}", tmp_path)
        assert notice is not None
        assert "offset=1, limit=350" in notice
        assert "offset=351, limit=1" in notice


@pytest.mark.parametrize("change", ["cd", "pushd"])
@pytest.mark.parametrize("separator", ["&&", ";"])
def test_codex_resolves_relative_path_after_cwd_change(tmp_path: pathlib.Path, change: str, separator: str) -> None:
    target = _large_file(tmp_path / "nested", "large.txt")

    notice = _codex_read(f"{change} {target.parent} {separator} cat {target.name}", tmp_path)

    assert notice is not None
    assert str(target) in notice


def test_codex_unresolved_cwd_change_uses_payload_cwd(tmp_path: pathlib.Path) -> None:
    target = _large_file(tmp_path)

    notice = _codex_read('cd "$DIR" && cat large.txt', tmp_path)

    assert notice is not None
    assert str(target) in notice


def test_codex_blocks_only_direct_static_full_reads(tmp_path: pathlib.Path) -> None:
    path = _large_file(tmp_path)
    image = _large_file(tmp_path, "capture.png")

    assert _codex_read(f"cat {path}", tmp_path) is not None
    assert _codex_read(f"sed -n p {path}", tmp_path) is not None
    assert _codex_read(f"awk '{{print}}' {path}", tmp_path) is not None
    assert _codex_read(f"cat {image}", tmp_path) is not None
    assert _codex_read(f"cat {path} | rg value", tmp_path) is None
    assert _codex_read(f"cat {path} > output.txt", tmp_path) is None


@pytest.mark.parametrize("command", ["cat", "less", "more"])
def test_codex_blocks_multiple_files_over_total_threshold(tmp_path: pathlib.Path, command: str) -> None:
    """個別には閾値以内でも、合計が閾値を超える全文取得を遮断する。"""
    first = _large_file(tmp_path, "first.txt", lines=180)
    second = _large_file(tmp_path, "second.txt", lines=171)
    within_first = _large_file(tmp_path, "within-first.txt", lines=175)
    within_second = _large_file(tmp_path, "within-second.txt", lines=175)

    notice = _codex_read(f"{command} {first} {second}", tmp_path)

    assert notice is not None
    assert f"`{first}`: 180行" in notice
    assert f"`{second}`: 171行" in notice
    assert "合計: 351行" in notice
    assert _codex_read(f"{command} {within_first} {within_second}", tmp_path) is None


def test_codex_applies_byte_threshold_and_environment_override(tmp_path: pathlib.Path, monkeypatch) -> None:
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

    ホストが上限超過を`PARTIAL view`又は退避ファイルとして返し、残りを続けて取得できるためである。
    """
    path = _large_file(tmp_path)
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
    """`turn_id`を持つCodex payloadでは、相対パスの全文取得を遮断する。"""
    target = _large_file(tmp_path / "nested", "large.txt")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"cd {target.parent} && cat {target.name}"},
        "session_id": "codex-large-read",
        "cwd": str(tmp_path),
        "turn_id": "codex-turn",
    }

    assert pretooluse.main(json.dumps(payload)) == 2
    assert str(target) in capsys.readouterr().err
