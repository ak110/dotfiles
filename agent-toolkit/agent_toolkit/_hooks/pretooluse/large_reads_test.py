"""大量の全文読取をPreToolUseで警告する条件を検証する。

容量警告はCodexのBash実行だけへ適用し、見出し抽出の警告は両ホストへ適用する。
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


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    ("search", "warned"),
    [
        ("rg -l -F -- needle", False),
        ("rg -lF needle", False),
        ("grep -rl needle", False),
        ("grep -L needle", False),
        ("rg -c needle", False),
        ("rg --count needle", False),
        ("rg --count-matches needle", False),
        ("rg --files-with-matches needle", False),
        ("rg --files-without-match needle", False),
        ("rg --files", False),
        ("rg -F -- needle", True),
        ("rg -n needle", True),
        ("rg -L needle", True),
        ("rg -e '-lc'", True),
        ("rg -Fe-lc", True),
        ("rg --regexp --count", True),
        ("rg --glob --count needle", True),
        ("rg -g-l needle", True),
        ("rg -r-l needle", True),
        ("rg -- '-lc'", True),
        ("grep -e '-l'", True),
        ("grep --include --count needle", True),
        ("grep -Hlc needle", False),
    ],
)
def test_original_record_search_output_mode(tmp_path, monkeypatch, capsys, codex, search, warned):
    """公開hookで所在・件数検索を通し、本文表示とフラグに似た値には照会を案内する。"""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    directory = home / "sessions"
    directory.mkdir(parents=True)
    (directory / "record.jsonl").write_text("{}\n", encoding="utf-8")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"{search} {shlex.quote(str(directory))}"},
        "cwd": str(tmp_path),
        **({"turn_id": "t"} if codex else {}),
    }
    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert ("session-review-evidence" in captured.out) is warned
    assert "permissionDecision" not in captured.out
    if warned:
        context = json.loads(captured.out)["hookSpecificOutput"]["additionalContext"]
        assert "session-records-query.md" in context


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """読取固有の通知を、作業契約を作成済みの状態で検証する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: 読取通知の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


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


@pytest.mark.parametrize(
    "template",
    [
        "sed -n '/開始/,/終了/p' {path}",
        "sed -n '/条件/p' -- {path}",
        "awk '/条件/' {path}",
        "awk '/^## 入力/{f=1} f&&/^## /&&!/^## 入力/{exit} f' {path} | head -60",
        "cd {parent} && sed -n '/^## 入力/,/^## /p' {name} | head -60",
    ],
)
def test_conditional_display_candidates_do_not_prove_acquisition(tmp_path, template):
    """表示案内の候補は条件付き抽出を含み、取得の被覆判定には流用しない。"""
    target = tmp_path / "note.md"
    target.write_text("# 表示対象\n## 入力\n本文\n", encoding="utf-8")
    command = (
        template.replace("{path}", shlex.quote(str(target)))
        .replace("{parent}", shlex.quote(str(target.parent)))
        .replace("{name}", shlex.quote(target.name))
    )
    assert large_reads.display_read_paths(command, str(tmp_path)) == [target]
    assert not list(bash_read_paths(command, str(tmp_path), include_partial=True, include_piped=True))


@pytest.mark.parametrize(
    "script",
    [
        "sed -i -n '/条件/p' {path}",
        "sed -n '/条件/w out' {path}",
        "sed -n '/条件/e' {path}",
        "sed -n -f {path}",
        "sed -n -e {path}",
        "awk -f {path}",
        "awk -v value={path} '{{print}}'",
        "awk '{{print > \"out\"}}' {path}",
        "awk '{{print >> \"out\"}}' {path}",
        "awk '{{print | \"cat\"}}' {path}",
        "awk '{{system(\"cat\")}}' {path}",
        "awk '{{getline}}' {path}",
        "awk '/条件/' {path} > out",
        "awk '/条件/' {path} | head -1 > out",
        "sed -n '/条件/p' {path} | cat > out",
    ],
)
def test_script_display_excludes_program_options_writes_and_execution(tmp_path, script):
    """存在する式・オプション値を入力と誤認せず、書込と外部実行を除く。"""
    target = tmp_path / "note.md"
    target.write_text("本文\n", encoding="utf-8")
    command = script.format(path=shlex.quote(str(target)))
    assert not large_reads.display_read_paths(command, str(tmp_path))


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    "template",
    ["sed -n '/条件/p' {path}", "awk '/条件/{{print}}' {path}"],
)
def test_conditional_session_record_display_keeps_reference_guidance(tmp_path, monkeypatch, capsys, codex, template):
    """同じ表示候補を使う原記録参照の案内も、両ホストの公開hookへ通す。"""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    target = home / "sessions/record.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": template.format(path=shlex.quote(str(target)))},
        "cwd": str(tmp_path),
        **({"turn_id": "t"} if codex else {}),
    }
    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert not captured.err
    context = json.loads(captured.out)["hookSpecificOutput"]["additionalContext"]
    assert "session-review-evidence" in context and str(target) in context
    assert "permissionDecision" not in captured.out


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
    notice = large_reads.session_record_reference_warning("Bash", {"command": "cat record.jsonl"}, str(root))
    assert notice is not None and "`agent-toolkit:review-session`" in notice
    source = pathlib.Path(__file__).resolve().parents[3] / "skills/review-session/references/session-records.md"
    assert source.is_file() and str(source) in notice
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
    assert notice.count("const result = await tools.exec_command(") == 2


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


def _execute_guidance(notice: str) -> dict[str, bytes]:
    """生成されたセルの全引数・予算を検査し、案内されたコマンドの本文を結合する。"""
    blocks = re.findall(
        r"```javascript\n// @exec: ([^\n]+)\nconst result = await tools.exec_command\(([^\n]+)\);\ntext\(result\);\n```",
        notice,
    )
    assert blocks
    received: dict[str, bytes] = {}
    for pragma, arguments in blocks:
        outer = json.loads(pragma)["max_output_tokens"]
        request = json.loads(arguments)
        assert 0 < request["max_output_tokens"] <= outer <= 20_000
        argv = shlex.split(request["cmd"])
        result = subprocess.run(argv, capture_output=True, timeout=10, check=False)
        assert result.returncode == 0 and not result.stderr
        displayed = json.dumps(
            {"output": result.stdout.decode("utf-8"), "exit_code": result.returncode}, ensure_ascii=False
        ).encode("utf-8")
        assert outer >= (len(displayed) + 2) // 3
        received[argv[-1]] = received.get(argv[-1], b"") + result.stdout
    return received


def test_codex_byte_threshold_boundary(tmp_path: pathlib.Path) -> None:
    """48KiB以下の全文取得は通し、1バイトでも超えると警告する。"""
    within = _sized_file(tmp_path, _THRESHOLD, "within.md")
    over = _sized_file(tmp_path, _THRESHOLD + 1, "over.md")

    assert _codex_read(f"cat {within}", tmp_path) is None
    assert _codex_read(f"cat {over}", tmp_path) is not None


@pytest.mark.parametrize("single_line", [False, True])
def test_block_notice_leads_to_sed_ranges(tmp_path: pathlib.Path, single_line: bool) -> None:
    """全量が枠を超える通常行を分割し、1行だけの超過は制約として案内する。"""
    path = _sized_file(tmp_path / "with space", 85_000, line_bytes=100000 if single_line else 100)
    notice = _codex_read(f"cat {shlex.quote(str(path))}", tmp_path)
    assert notice is not None
    if single_line:
        assert "行範囲では収まらない" in notice and "必要な項目を抽出" in notice
        assert "tools.exec_command(" not in notice
    else:
        assert ",$p' --" in notice
        assert _execute_guidance(notice) == {str(path): path.read_bytes()}


@pytest.mark.parametrize("multiple", [False, True])
def test_notice_range_commands_roundtrip_utf8(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, multiple: bool) -> None:
    """引用した実パスの計算済み取得例で、日本語とCRLFを末尾の行まで復元する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_LARGE_READ_BYTES", "100")
    paths = [tmp_path / "file's name.txt", *([tmp_path / "second file.txt"] if multiple else [])]
    for index, path in enumerate(paths):
        path.write_bytes((f"本文{index}\r\n" * 3000).encode("utf-8"))
    notice = _codex_read(shlex.join(["cat", *map(str, paths)]), tmp_path)
    assert notice is not None
    assert _execute_guidance(notice) == {str(path): path.read_bytes() for path in paths}


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
    assert "tools.exec_command(" in message and "text(result)" in message
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
    assert "`agent-toolkit:review-session`" in captured.out
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
        assert "`agent-toolkit:review-session`" in notice
        assert "--transcript" in notice
    else:
        assert "tools.exec_command(" in notice and "text(result)" in notice


@pytest.mark.parametrize("multiple", [False, True])
def test_large_read_budget_guidance(tmp_path, multiple):
    """日本語85KB程度の資料を、両層の予算と全範囲が対応する取得例で復元する。"""
    paths = [tmp_path / "first.md", *([tmp_path / "second.md"] if multiple else [])]
    for path in paths:
        path.write_text("本文\n" * 12000, encoding="utf-8")
    notice = _codex_read(shlex.join(["cat", "--", *map(str, paths)]), tmp_path)
    assert notice is not None
    assert "original token count" in notice and "write_stdin" in notice
    assert _execute_guidance(notice) == {str(path): path.read_bytes() for path in paths}


def test_known_read_operand_is_kept_as_a_candidate_with_unknown_arguments(tmp_path):
    """無関係な引数が未知でも、既知のファイル引数を読取候補に残し、全文取得の証明には使わない。"""
    target = tmp_path / "input.md"
    target.write_text("本文\n", encoding="utf-8")
    command = f'cat -- {shlex.quote(str(target))} "$OTHER"'
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(target,)]
    assert not list(bash_read_paths(command, str(tmp_path)))


def test_static_session_record_with_unknown_search_term_keeps_the_reference_guidance(tmp_path):
    """検索語が未知でも、静的な原記録の入力先には参照案内を届ける。"""
    root = pathlib.Path.home() / ".claude/projects/project"
    root.mkdir(parents=True)
    record = root / "record.jsonl"
    record.write_text("{}\n", encoding="utf-8")
    notice = large_reads.session_record_reference_warning(
        "Bash", {"command": f'rg -e "$PATTERN" -- {shlex.quote(str(record))}'}, str(tmp_path)
    )
    assert notice is not None and str(record) in notice
    # 入力先そのものが未知なら、cwdを入力先と推定しない。
    assert large_reads.session_record_reference_warning("Bash", {"command": 'rg needle "$DIR"'}, str(root)) is None


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("normative", [False, True])
@pytest.mark.parametrize(
    "command,warned",
    [
        ("sed -n '/^## 対象/,/^## /p' -- {path}", True),
        ("sed -n -e '/^## 対象/,/^## /p' -- {path}", True),
        ("awk '/^## 対象/,/^## /' {path}", True),
        ("awk '/^## 対象/ {{found=1}} found {{print}} /^## 後段/ {{exit}}' {path}", True),
        ("sed -n '2,4p' -- {path}", False),
        ("sed -n '/本文/p' -- {path}", False),
        ("printf '%s' 'sed -n /###/p' {path}", False),
    ],
)
def test_heading_extraction_warns_only_for_normative_files(tmp_path, capsys, codex, normative, command, warned):
    """公開PreToolUseは両hostで見出し抽出だけを警告し、操作は許可する。"""
    path = tmp_path / ("AGENTS.md" if normative else "record.md")
    path.write_text("# 資料\n## 対象\n本文\n## 後段\n例外\n", encoding="utf-8")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command.format(path=shlex.quote(str(path)))},
        "cwd": str(tmp_path),
        **({"turn_id": "turn"} if codex else {}),
    }
    assert pretooluse.main(json.dumps(payload)) == 0
    output = capsys.readouterr()
    assert not output.err
    specific = json.loads(output.out).get("hookSpecificOutput", {}) if output.out else {}
    assert specific.get("permissionDecision") != "deny"
    context = specific.get("additionalContext", "")
    assert ("見出しの正規表現" in context) is (normative and warned)
    if normative and warned:
        assert str(path) in context and "全文" in context and "cat --" in context
