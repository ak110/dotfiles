"""大量の全文読取をPreToolUseで遮断する条件を検証する。

遮断はCodexのBash実行だけへ適用し、Claude Codeの`Read`と`Bash`は補正も遮断もしない。
"""

import json
import pathlib
import re
import shlex

import pytest

from agent_toolkit import atk
from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths, check_large_bash_read

_THRESHOLD = 48 * 1024


@pytest.mark.parametrize(
    "command",
    [
        "cat -- large.txt",
        "sed -n '2,3p' large.txt",
        "atk read-file -- large.txt",
        "atk read-file --start 0 --max-bytes 12000 -- large.txt",
        "atk read-file --max-bytes=12000 --start=0 -- large.txt",
    ],
)
def test_partial_delivery_keeps_large_read_trigger_unchanged(tmp_path: pathlib.Path, command: str) -> None:
    """配送の追加読取形は同じパスを得るが、既存の大量読取遮断を増やさない。"""
    target = _sized_file(tmp_path / "nested", _THRESHOLD + 1)
    command = f"cd {target.parent} && {command}"
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(target,)]
    assert check_large_bash_read(command, str(tmp_path), is_codex=True) is None


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


def test_codex_byte_threshold_boundary(tmp_path: pathlib.Path) -> None:
    """48KiB以下の全文取得は通し、1バイトでも超えると遮断する。"""
    within = _sized_file(tmp_path, _THRESHOLD, "within.md")
    over = _sized_file(tmp_path, _THRESHOLD + 1, "over.md")

    assert _codex_read(f"cat {within}", tmp_path) is None
    assert _codex_read(f"cat {over}", tmp_path) is not None


@pytest.mark.parametrize("single_line", [False, True])
def test_block_notice_leads_to_read_file(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], single_line: bool) -> None:
    """通知から示した公開コマンドだけで、長い単一行を含む全文を復元する。"""
    path = _sized_file(tmp_path / "with space", _THRESHOLD + 300, line_bytes=100000 if single_line else 100)
    notice = _codex_read(f"cat {shlex.quote(str(path))}", tmp_path)
    assert notice is not None
    command = re.search(r"`(atk read-file -- [^`]+)`", notice)
    assert command is not None
    argv = shlex.split(command.group(1))
    parts: list[str] = []
    while True:
        assert _codex_read(shlex.join(argv), tmp_path) is None
        with pytest.raises(SystemExit, match="0"):
            atk.main(argv[1:])
        captured = capsys.readouterr()
        assert not captured.err
        assert len(captured.out.encode("utf-8")) <= 4096
        result = json.loads(captured.out)
        parts.append(result["text"])
        if result["eof"]:
            break
        argv = ["atk", "read-file", "--start", str(result["next"]), "--", str(path)]
    assert "".join(parts).encode("utf-8") == path.read_bytes()


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
    assert "atk read-file -- " in err


@pytest.mark.parametrize("multiple", [False, True])
def test_large_original_record_is_guided_to_evidence_query(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    multiple: bool,
) -> None:
    """原記録の大容量遮断では専用照会を案内し、通常本文のページ分割へ誘導しない。"""
    codex = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex))
    record = codex / "sessions/2026/10/10/rollout-session.jsonl"
    record.parent.mkdir(parents=True)
    entry = {
        "type": "assistant",
        "message": {"id": "reply", "role": "assistant", "content": [{"type": "text", "text": "本文" * 10000}]},
    }
    record.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    extra = _sized_file(tmp_path, 100, "ordinary.txt")
    command = "cat " + shlex.quote(str(record)) + (" " + shlex.quote(str(extra)) if multiple else "")
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path), "turn_id": "codex-turn"}
    assert pretooluse.main(json.dumps(payload)) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "session-review-evidence" in captured.err
    assert "--transcript" in captured.err
    assert "atk read-file" not in captured.err
