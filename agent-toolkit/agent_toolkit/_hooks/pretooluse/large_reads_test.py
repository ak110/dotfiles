"""大量の全文読取をPreToolUseで遮断する条件を検証する。

遮断はCodexのBash実行だけへ適用し、Claude Codeの`Read`と`Bash`は補正も遮断もしない。
"""

import json
import pathlib
import re
import shlex
import stat
import types

import pytest

from agent_toolkit import atk
from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.pretooluse import dispatch, large_reads, operation_skills
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths, check_large_bash_read

_THRESHOLD = 48 * 1024


@pytest.mark.parametrize("launcher", ["atk", "C:/tools/atk.cmd", r"'C:\tools\atk.cmd'", "atk.py"])
def test_atk_launcher_read_paths_share_operand_contract(tmp_path, launcher):
    """全ランチャーのread-fileが同じ読取対象を返す。"""
    target = tmp_path / "note.md"
    command = f"{launcher} read-file -- {shlex.quote(str(target))}"
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(target,)]


@pytest.mark.parametrize(
    ("prefix", "moved"),
    [
        ("cd {next} &&", True),
        ("{{ cd {next}; }};", True),
        ("if true; then cd {next}; fi;", True),
        ("(cd {next});", False),
        ("cd {next} &", False),
        ('echo "$(cd {next})";', False),
    ],
)
def test_read_and_write_consumers_share_cwd(tmp_path, prefix, moved):
    """同じ作用範囲の移動を読取・書込の両方へ渡す。"""
    target = tmp_path / "next"
    prefix = prefix.format(next=shlex.quote(str(target)))
    expected = target if moved else tmp_path
    assert list(bash_read_paths(prefix + " cat file", str(tmp_path))) == [(expected / "file",)]
    assert operation_skills.written_paths("Bash", {"command": prefix + " echo x > file"}, str(tmp_path)) == [
        str(expected / "file")
    ]


def test_unknown_session_record_cwd_is_not_replaced():
    """未知の移動先を元のcwdの原記録へ帰属させない。"""
    root = pathlib.Path.home() / ".claude/projects/project"
    root.mkdir(parents=True)
    (root / "record.jsonl").write_text("{}\n", encoding="utf-8")
    assert large_reads.session_record_reference_warning("Bash", {"command": "cat record.jsonl"}, str(root))
    assert large_reads.session_record_reference_warning("Bash", {"command": 'cd "$D" && cat record.jsonl'}, str(root)) is None


def test_python_heredoc_write_uses_invocation_cwd(tmp_path):
    """同じ行で移動した後のPython本文の書込先も共通の実行位置から得る。"""
    target = tmp_path / "next"
    command = f"cd {shlex.quote(str(target))} && python3 - <<'PY'\nfrom pathlib import Path\nPath('file').write_text('x')\nPY\n"
    assert operation_skills.written_paths("Bash", {"command": command}, str(tmp_path)) == [str(target / "file")]


@pytest.mark.parametrize(
    "command",
    [
        "sed -n '2,3p' large.txt",
        "atk read-file -- large.txt",
        "atk read-file --start 0 --max-bytes 12000 -- large.txt",
        "atk read-file --max-bytes=12000 --start=0 -- large.txt",
        "atk read-file -- large.txt --start 0 --max-bytes 12000",
        "atk read-file -- large.txt --max-bytes=12000 --start=0",
    ],
)
def test_partial_delivery_keeps_large_read_trigger_unchanged(tmp_path: pathlib.Path, command: str) -> None:
    """帰属行の配送のために範囲取得も含めた解析は同じパスを返すが、大量読取の遮断を新たに発生させない。"""
    target = _sized_file(tmp_path / "nested", _THRESHOLD + 1)
    command = f"cd {target.parent} && {command}"
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(target,)]
    assert check_large_bash_read(command, str(tmp_path), is_codex=True) is None


@pytest.mark.parametrize("command", ["cat", "cat -n", "less -N", "more -d", "sed -n p", "awk '{print}'"])
@pytest.mark.parametrize("multiple", [False, True])
def test_allowed_reads_do_not_open_regular_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, command: str, multiple: bool
) -> None:
    """許可に必要な合計容量はメタデータから求め、通常ファイルの本文は開かない。"""
    first = _sized_file(tmp_path, 10, "first.txt")
    second = _sized_file(tmp_path, 20, "second.txt")
    # sed・awkは既存の単体取得の契約であり、複数ファイルは本文表示コマンドで検証する。
    operands = [first, second] if multiple and not command.startswith(("sed", "awk")) else [first]

    def reject_open(self: pathlib.Path, *args: object, **kwargs: object) -> None:
        raise AssertionError(f"許可の判定が本文を開いた: {self}")

    monkeypatch.setattr(pathlib.Path, "open", reject_open)
    assert _codex_read(shlex.join(shlex.split(command) + [str(path) for path in operands]), tmp_path) is None


@pytest.mark.parametrize("size", [100, _THRESHOLD + 1])
def test_zero_metadata_size_uses_content(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    """サイズ0の仮想ファイルも、閾値前後の内容量で許可と遮断を決める。"""
    target = _sized_file(tmp_path, size)
    original = pathlib.Path.stat

    def zero_size(self: pathlib.Path, *args, **kwargs):
        if self == target:
            return types.SimpleNamespace(st_mode=stat.S_IFREG, st_size=0)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", zero_size)
    notice = _codex_read(f"cat {target}", tmp_path)
    assert (notice is not None) == (size > _THRESHOLD)
    if notice:
        assert f"{size}バイト" in notice


@pytest.mark.parametrize(
    "command",
    [
        "cat --",
        "cat -n",
        "cat -nE",
        "cat --number --show-ends",
        "less -N",
        "less -x 4 --",
        "less -J",
        "more -d --",
        "more --lines=20 --",
        "more -e",
        "more --exit-on-eof",
        "more -20",
    ],
)
def test_full_read_options_and_delivery(tmp_path: pathlib.Path, command: str) -> None:
    """通常の表示指定でも、配送と遮断の双方が終端・cwd・合計容量を解釈する。"""
    first = _sized_file(tmp_path / "nested", 30 * 1024, "-first.txt")
    second = _sized_file(first.parent, 20 * 1024, "second.txt")
    # ハイフン始まりの名前には、指定済みならその終端を使う。
    suffix = "" if command.endswith("--") else " --"
    value = f"cd {first.parent} && {command}{suffix} {first.name} {second.name}"
    assert list(bash_read_paths(value, str(tmp_path), include_partial=True)) == [(first, second)]
    notice = _codex_read(value, tmp_path)
    assert notice is not None
    assert "合計: 51200バイト" in notice
    assert "atk read-file" in notice


@pytest.mark.parametrize(
    "command", ["cat --help", "cat --version", "cat --unknown", "less --help", "less -Z", "more --version"]
)
def test_non_read_options_are_not_inferred_as_full_reads(tmp_path: pathlib.Path, command: str) -> None:
    target = _sized_file(tmp_path, _THRESHOLD + 1)
    assert _codex_read(f"{command} {target}", tmp_path) is None
    assert not list(bash_read_paths(f"{command} {target}", str(tmp_path), include_partial=True))


@pytest.mark.parametrize("command", ["cat -n --", "cat --number --", "less -x 4 --", "more -d --", "sed -n '1,2p'"])
def test_commit_rule_delivery_uses_shared_option_parsing(tmp_path: pathlib.Path, command: str) -> None:
    """通常のオプションや範囲指定を伴う読取でも、実在する帰属資料を読む呼び出しを配送の判定が検出する。"""
    directory = tmp_path / "plugin/skills/commit/references"
    directory.mkdir(parents=True)
    target = directory / "message.md"
    target.write_text("commitの記述規則", encoding="utf-8")
    invocation = f"cd {directory} && {command} message.md"
    assert dispatch._reads_commit_message_rules(invocation, str(tmp_path))  # pylint: disable=protected-access
    assert not dispatch._reads_commit_message_rules(  # pylint: disable=protected-access
        f"{command} other.md", str(directory)
    )


@pytest.mark.parametrize("operation", ["stat", "open"])
def test_unavailable_content_does_not_create_a_block(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """メタデータまたは本文を取得できない対象は判定不能として扱い、遮断しない。"""
    target = _sized_file(tmp_path, _THRESHOLD + 1)
    original = getattr(pathlib.Path, operation)

    def unavailable(path: pathlib.Path, *args, **kwargs):
        if path == target:
            raise OSError("読取不能")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, operation, unavailable)
    assert _codex_read(f"cat -- {target}", tmp_path) is None
    assert _codex_read(f"cat -- {tmp_path / 'missing.txt'}", tmp_path) is None
    assert _codex_read(f"cat -- {tmp_path}", tmp_path) is None


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


def _read_file_response(output: str) -> tuple[dict, str]:
    """read-fileのメタデータと無加工本文を分ける。"""
    metadata, separator, body = output.partition("\n")
    assert separator
    return json.loads(metadata), body


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
        assert len(captured.out.encode("utf-8")) <= 16384
        result, body = _read_file_response(captured.out)
        parts.append(body)
        if result["eof"]:
            break
        argv = ["atk", "read-file", "--start", str(result["next"]), "--", str(path)]
    assert "".join(parts).encode("utf-8") == path.read_bytes()


@pytest.mark.parametrize("multiple", [False, True])
def test_notice_continuation_commands_roundtrip_utf8(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], multiple: bool
) -> None:
    """共通テンプレートにも引用した実パスを入れ、日本語とCRLFを終端まで復元する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")
    paths = [tmp_path / "file's name.txt", *([tmp_path / "second file.txt"] if multiple else [])]
    for index, path in enumerate(paths):
        path.write_bytes((f"本文{index}\r\n" * 3000).encode("utf-8"))
    notice = _codex_read(shlex.join(["cat", *map(str, paths)]), tmp_path)
    assert notice is not None
    initial = re.findall(r"`(atk read-file -- [^`]+)`", notice)
    continuations = re.findall(r"`(atk read-file --start <next> -- [^`]+)`", notice)
    if multiple:
        assert len(initial) == len(continuations) == 1
        initial = [initial[0].replace("<必要なファイルの絶対パス>", shlex.quote(str(path))) for path in paths]
        continuations = [continuations[0].replace("<同じファイルの絶対パス>", shlex.quote(str(path))) for path in paths]
    assert len(initial) == len(continuations) == len(paths)
    for path, first, continuation in zip(paths, initial, continuations, strict=True):
        assert shlex.split(continuation)[-1] == str(path)
        argv = shlex.split(first)
        parts: list[str] = []
        while True:
            with pytest.raises(SystemExit, match="0"):
                atk.main(argv[1:])
            result = capsys.readouterr()
            assert not result.err
            response, body = _read_file_response(result.out)
            parts.append(body)
            if response["eof"]:
                break
            argv = shlex.split(continuation.replace("<next>", str(response["next"])))
        assert len(parts) > 1
        assert "".join(parts).encode("utf-8") == path.read_bytes()


@pytest.mark.parametrize("change", ["cd", "pushd"])
@pytest.mark.parametrize("separator", ["&&", ";"])
def test_codex_resolves_relative_path_after_cwd_change(tmp_path: pathlib.Path, change: str, separator: str) -> None:
    target = _sized_file(tmp_path / "nested", _THRESHOLD + 1)

    notice = _codex_read(f"{change} {target.parent} {separator} cat {target.name}", tmp_path)

    assert notice is not None
    assert str(target) in notice


def test_codex_unresolved_cwd_change_keeps_read_target_unknown(tmp_path: pathlib.Path) -> None:
    """元のcwdの同名ファイルの容量で、解決不能な移動先の読取を遮断しない。"""
    _sized_file(tmp_path, _THRESHOLD + 1)

    notice = _codex_read('cd "$DIR" && cat large.txt', tmp_path)

    assert notice is None


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


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("original", [False, True])
def test_large_read_notice_keeps_recovery_without_generic_fallback(tmp_path, monkeypatch, multiple, original):
    """原記録・通常資料の単体・複数遮断が具体的な取得方法を案内する。"""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    target = _sized_file(home / "sessions" if original else tmp_path, _THRESHOLD + 1, "record.jsonl")
    extra = _sized_file(tmp_path, 10, "extra.txt")
    notice = _codex_read(shlex.join(["cat", str(target), *([str(extra)] if multiple else [])]), tmp_path)
    assert notice is not None
    assert "次の操作:" in notice
    assert "実行が許可される担当" not in notice
    assert "実行が制限される担当" not in notice
    assert "委譲元へ返し" not in notice
    if original:
        assert "session-review-evidence" in notice
        assert "--transcript" in notice
    else:
        assert "atk read-file" in notice and "eof" in notice
