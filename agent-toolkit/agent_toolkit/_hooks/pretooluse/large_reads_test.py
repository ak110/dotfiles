"""自動配送の適用外で、大量の全文読取をPreToolUseが警告する条件を検証する。

警告はCodexのBash実行だけへ適用し、Claude Codeの`Read`と`Bash`は補正も警告もしない。
"""

import json
import pathlib
import re
import shlex
import stat
import subprocess
import types

import pytest

from agent_toolkit._hooks import pretooluse, work_contract
from agent_toolkit._hooks.pretooluse import dispatch, large_reads, operation_skills
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths, check_large_bash_read, sed_read_range

_THRESHOLD = 48 * 1024


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """既存の手動フォールバックを検証し、自動配送の公開経路は別試験で扱う。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: 読取通知の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)
    monkeypatch.setattr(large_reads.codex_read_delivery, "can_deliver", lambda *_args: False)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("sed -n p note.md", ("note.md", 1, None)),
        ("sed -n '1,$p' -- note.md", ("note.md", 1, None)),
        ("sed -n '3p' note.md", ("note.md", 3, 3)),
        ("sed -n '2,40p' -- note.md", ("note.md", 2, 40)),
        ("/usr/bin/sed -n '5,$p' note.md", ("note.md", 5, None)),
        ("sed -n '0,4p' note.md", None),
        ("sed -n '1,4p' -x note.md", None),
        ("sed -n '1,4p' -- a.md b.md", None),
        ("sed -n '1,4w out' note.md", None),
        ("sed -n '/x/p' note.md", None),
        ("sed '1,4p' note.md", None),
        ("sed -n '1,4p' -note.md", None),
    ],
)
def test_sed_read_range_accepts_only_static_line_ranges(command, expected):
    """終了の有無によらず静的な行範囲だけを読取として解析し、書込や複数ファイルを含む形を除く。"""
    assert sed_read_range(shlex.split(command)) == expected


@pytest.mark.parametrize("separator", ["", "-- "])
def test_sed_range_read_paths_share_operand_contract(tmp_path, separator):
    """終了の有無によらず、行範囲の読取が同じ読取対象を返す。"""
    target = tmp_path / "note.md"
    command = f"sed -n '2,4p' {separator}{shlex.quote(str(target))}"
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(target,)]
    assert not list(bash_read_paths(command, str(tmp_path)))


@pytest.mark.parametrize("separator", [[], ["--"]])
def test_same_range_reads_reach_consumers(tmp_path: pathlib.Path, separator: list[str]) -> None:
    """同じ行範囲の読取入力を大量読取とcommit資料の消費側が認識する。"""
    root = pathlib.Path(large_reads.__file__).resolve().parents[3]
    targets = [
        root / "skills/user-confirmation-and-report/references/approval-scope.md",
        root / "skills/user-confirmation-and-report/references/choice-construction.md",
        root / "skills/commit/references/message.md",
    ]
    command = "; ".join(shlex.join(["sed", "-n", "1,400p", *separator, str(path)]) for path in targets)
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(path,) for path in targets]
    # この識別処理自体が指定された消費側であり、実在するcommit資料を渡して判定する。
    assert dispatch._reads_commit_message_rules(command, str(tmp_path))  # pylint: disable=protected-access


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
        "sed -n '2,3p' -- large.txt",
        "sed -n '5p' large.txt",
        "sed -n '2,$p' -- large.txt",
    ],
)
def test_partial_delivery_keeps_large_read_trigger_unchanged(tmp_path: pathlib.Path, command: str) -> None:
    """帰属行の配送のために範囲取得も含めた解析は同じパスを返すが、大量読取の警告を新たに発生させない。"""
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
    """サイズ0の仮想ファイルも、閾値前後の内容量で許可と警告を決める。"""
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
    """通常の表示指定でも、配送と警告の双方が終了・cwd・合計容量を解釈する。"""
    first = _sized_file(tmp_path / "nested", 30 * 1024, "-first.txt")
    second = _sized_file(first.parent, 20 * 1024, "second.txt")
    # ハイフン始まりの名前には、指定済みならその終了を使う。
    suffix = "" if command.endswith("--") else " --"
    value = f"cd {first.parent} && {command}{suffix} {first.name} {second.name}"
    assert list(bash_read_paths(value, str(tmp_path), include_partial=True)) == [(first, second)]
    notice = _codex_read(value, tmp_path)
    assert notice is not None
    assert "合計: 51200バイト" in notice
    assert "sed -n '<開始行>,<終了行>p' -- " in notice


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
    """メタデータまたは本文を取得できない対象は判定不能として扱い、警告しない。"""
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


def _sed_chunks(path: pathlib.Path, chunk: int, cwd: pathlib.Path) -> bytes:
    """通知の行数単位で`sed -n`を末尾の行まで進め、取得した出力を連結する。"""
    total = len(path.read_bytes().splitlines(keepends=True))
    parts: list[bytes] = []
    for start in range(1, total + 1, chunk):
        command = shlex.join(["sed", "-n", f"{start},{start + chunk - 1}p", "--", str(path)])
        assert _codex_read(command, cwd) is None
        parts.append(subprocess.run(shlex.split(command), check=True, capture_output=True).stdout)
    assert len(parts) == -(-total // chunk)
    return b"".join(parts)


def test_codex_byte_threshold_boundary(tmp_path: pathlib.Path) -> None:
    """48KiB以下の全文取得は通し、1バイトでも超えると警告する。"""
    within = _sized_file(tmp_path, _THRESHOLD, "within.md")
    over = _sized_file(tmp_path, _THRESHOLD + 1, "over.md")

    assert _codex_read(f"cat {within}", tmp_path) is None
    assert _codex_read(f"cat {over}", tmp_path) is not None


@pytest.mark.parametrize("single_line", [False, True])
def test_block_notice_leads_to_sed_ranges(tmp_path: pathlib.Path, single_line: bool) -> None:
    """通知が示した行範囲の取得だけで、長い単一行を含む全文を復元する。"""
    path = _sized_file(tmp_path / "with space", _THRESHOLD + 300, line_bytes=100000 if single_line else 100)
    notice = _codex_read(f"cat {shlex.quote(str(path))}", tmp_path)
    assert notice is not None
    first = re.search(r"`(sed -n '1,(\d+)p' -- [^`]+)`", notice)
    assert first is not None
    assert shlex.split(first.group(1))[-1] == str(path)
    chunk = int(first.group(2))
    assert f"`sed -n '{chunk + 1},{2 * chunk}p' -- {shlex.quote(str(path))}`" in notice
    assert _sed_chunks(path, chunk, tmp_path) == path.read_bytes()


@pytest.mark.parametrize("multiple", [False, True])
def test_notice_range_commands_roundtrip_utf8(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, multiple: bool) -> None:
    """引用した実パスとテンプレートの行範囲取得で、日本語とCRLFを末尾の行まで復元する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")
    paths = [tmp_path / "file's name.txt", *([tmp_path / "second file.txt"] if multiple else [])]
    for index, path in enumerate(paths):
        path.write_bytes((f"本文{index}\r\n" * 3000).encode("utf-8"))
    notice = _codex_read(shlex.join(["cat", *map(str, paths)]), tmp_path)
    assert notice is not None
    if multiple:
        template = re.findall(r"`(sed -n '<開始行>,<終了行>p' -- <必要なファイルの絶対パス>)`", notice)
        assert len(template) == 1
        chunk = 1000
    else:
        first = re.search(r"`sed -n '1,(\d+)p' -- ([^`]+)`", notice)
        assert first is not None
        assert shlex.split(first.group(2)) == [str(paths[0])]
        chunk = int(first.group(1))
    for path in paths:
        assert _sed_chunks(path, chunk, tmp_path) == path.read_bytes()


@pytest.mark.parametrize("change", ["cd", "pushd"])
@pytest.mark.parametrize("separator", ["&&", ";"])
def test_codex_resolves_relative_path_after_cwd_change(tmp_path: pathlib.Path, change: str, separator: str) -> None:
    target = _sized_file(tmp_path / "nested", _THRESHOLD + 1)

    notice = _codex_read(f"{change} {target.parent} {separator} cat {target.name}", tmp_path)

    assert notice is not None
    assert str(target) in notice


def test_codex_unresolved_cwd_change_keeps_read_target_unknown(tmp_path: pathlib.Path) -> None:
    """元のcwdの同名ファイルの容量で、解決不能な移動先の読取を警告しない。"""
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
    """個別には閾値以内でも、合計が閾値を超える全文取得を警告する。"""
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
    """Claude Codeでは`Read`と`cat`の大量読取を補正も警告もしない。

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


def test_dispatch_codex_warns_and_allows_bash_full_read(tmp_path: pathlib.Path, capsys) -> None:
    """Codexの全文取得を継続し、出力予算と継続取得の案内を渡す。"""
    target = _sized_file(tmp_path / "nested", _THRESHOLD + 1)
    # 元turnのturn_contextを持たないPre入力では容量警告を維持する。
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"cd {target.parent} && cat {target.name}"},
        "session_id": "codex-large-read",
        "cwd": str(tmp_path),
        "turn_id": "codex-turn",
    }

    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    message = json.loads(captured.out)["hookSpecificOutput"]["additionalContext"]
    assert not captured.err
    assert str(target) in message
    assert "sed -n '1," in message
    assert 'kind="warn"' in message
    assert "内側コマンドと外側セル" in message


@pytest.mark.parametrize("multiple", [False, True])
def test_large_original_record_is_guided_to_evidence_query(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    multiple: bool,
) -> None:
    """原記録の大容量警告では専用照会を案内し、通常本文のページ分割へ誘導しない。"""
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
    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert "session-review-evidence" in captured.out
    assert "--transcript" in captured.out
    assert "sed -n '" not in captured.out


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("original", [False, True])
def test_large_read_notice_keeps_recovery_without_generic_fallback(tmp_path, monkeypatch, multiple, original):
    """原記録・通常資料の単体・複数警告が具体的な取得方法を案内する。"""
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
        assert "sed -n '" in notice and "末尾の" in notice


@pytest.mark.parametrize("multiple", [False, True])
def test_large_read_budget_guidance(tmp_path, multiple):
    """日本語の実バイト合計に対応する内外予算と再取得手順を案内する。"""
    paths = [tmp_path / "first.md", *([tmp_path / "second.md"] if multiple else [])]
    for path in paths:
        path.write_text("本文\n" * 8000, encoding="utf-8")
    budget = (sum(path.stat().st_size for path in paths) + 2) // 3
    notice = _codex_read(shlex.join(["cat", "--", *map(str, paths)]), tmp_path)
    assert notice is not None
    assert f"少なくとも{budget}" in notice
    assert "外側セルのmax_output_tokensは内側コマンドの予算の合計以上" in notice
    assert "original token count" in notice and "その数以上で1回再取得" in notice
