"""原記録・見出し抽出の通知と、受信枠不明の通常読取の非通知を検証する。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit._hooks import posttooluse, pretooluse, work_contract
from agent_toolkit._hooks.pretooluse import commit_identity, large_reads, operation_skills
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
        assert "session-records.md`「本文の検索」" in context


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


def _reads_commit_message_rules(command: str, cwd: str) -> bool:
    paths = (path for group in bash_read_paths(command, cwd, include_partial=True) for path in group)
    return commit_identity.reads_commit_message_rules(paths)


@pytest.mark.parametrize("separator", [[], ["--"]])
def test_same_range_reads_reach_consumers(tmp_path: pathlib.Path, separator: list[str]) -> None:
    root = pathlib.Path(large_reads.__file__).resolve().parents[3]
    targets = [
        root / "skills/atk-mode/playbooks/confirmation.md",
        root / "skills/atk-mode/references/choice-construction.md",
        root / "skills/atk-mode/references/commit-message.md",
    ]
    command = "; ".join(shlex.join(["sed", "-n", "1,400p", *separator, str(path)]) for path in targets)
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(path,) for path in targets]

    assert _reads_commit_message_rules(command, str(tmp_path))


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
    target = tmp_path / "next"
    prefix = prefix.format(next=shlex.quote(str(target)))
    expected = target if moved else tmp_path
    assert list(bash_read_paths(prefix + " cat file", str(tmp_path))) == [(expected / "file",)]
    assert operation_skills.written_paths("Bash", {"command": prefix + " echo x > file"}, str(tmp_path)) == [
        str(expected / "file")
    ]


def test_unknown_session_record_cwd_is_not_replaced():
    root = pathlib.Path.home() / ".claude/projects/project"
    root.mkdir(parents=True)
    (root / "record.jsonl").write_text("{}\n", encoding="utf-8")
    notice = large_reads.session_record_reference_warning("Bash", {"command": "cat record.jsonl"}, str(root))
    assert notice is not None and "`agent-toolkit:atk-mode/references/session-records.md`" in notice
    source = pathlib.Path(__file__).resolve().parents[3] / "skills/atk-mode/references/session-records.md"
    assert source.is_file() and "references/session-records.md" in notice
    assert str(source) not in notice
    assert large_reads.session_record_reference_warning("Bash", {"command": 'cd "$D" && cat record.jsonl'}, str(root)) is None


def test_python_heredoc_write_uses_invocation_cwd(tmp_path):
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
    assert check_large_bash_read(command, str(tmp_path)) is None


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
    directory = tmp_path / "plugin/skills/atk-mode/references"
    directory.mkdir(parents=True)
    target = directory / "commit-message.md"
    target.write_text("commitの記述規則", encoding="utf-8")
    invocation = f"cd {directory} && {command} commit-message.md"
    assert _reads_commit_message_rules(invocation, str(tmp_path))
    assert not _reads_commit_message_rules(f"{command} other.md", str(directory))


@pytest.mark.parametrize("operation", ["stat", "open"])
def test_unavailable_content_does_not_create_a_block(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
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
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    full, rest = divmod(size, line_bytes)
    body = ("a" * (line_bytes - 1) + "\n") * full + "b" * rest
    path.write_bytes(body.encode("ascii"))
    return path


def _codex_read(command: str, cwd: pathlib.Path) -> str | None:
    return check_large_bash_read(command, str(cwd))


def test_codex_unresolved_cwd_change_keeps_read_target_unknown(tmp_path: pathlib.Path) -> None:
    _sized_file(tmp_path, _THRESHOLD + 1)

    notice = _codex_read('cd "$DIR" && cat large.txt', tmp_path)

    assert notice is None


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


@pytest.mark.parametrize("multiple", [False, True])
def test_large_original_record_is_guided_to_evidence_query(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    multiple: bool,
) -> None:
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
    assert "`agent-toolkit:atk-mode/references/session-records.md`" in captured.out
    assert "--transcript" in captured.out
    assert "sed -n '" not in captured.out


def test_known_read_operand_is_kept_as_a_candidate_with_unknown_arguments(tmp_path):
    target = tmp_path / "input.md"
    target.write_text("本文\n", encoding="utf-8")
    command = f'cat -- {shlex.quote(str(target))} "$OTHER"'
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(target,)]
    assert not list(bash_read_paths(command, str(tmp_path)))


def test_static_session_record_with_unknown_search_term_keeps_the_reference_guidance(tmp_path):
    root = pathlib.Path.home() / ".claude/projects/project"
    root.mkdir(parents=True)
    record = root / "record.jsonl"
    record.write_text("{}\n", encoding="utf-8")
    notice = large_reads.session_record_reference_warning(
        "Bash", {"command": f'rg -e "$PATTERN" -- {shlex.quote(str(record))}'}, str(tmp_path)
    )
    assert notice is not None and str(record) in notice

    assert large_reads.session_record_reference_warning("Bash", {"command": 'rg needle "$DIR"'}, str(root)) is None


@pytest.mark.parametrize("route", ["functions.exec", "functions.exec_command"])
@pytest.mark.parametrize("kind", ["record", "ordinary", "metadata"])
def test_codex_shell_results_guide_only_static_session_content_searches(tmp_path, capsys, route, kind):
    codex_home = pathlib.Path.home() / ".codex/sessions/2026/11/19"
    codex_home.mkdir(parents=True)
    record = codex_home / "rollout-2026-11-19T03-33-00-01a1f1dd-db72-7143-bae6-845204964526.jsonl"
    ordinary = tmp_path / "test.jsonl"
    for target in (record, ordinary):
        target.write_text("{}\n", encoding="utf-8")
    command = {
        "record": f"rg needle {shlex.quote(str(record))}",
        "ordinary": f"rg needle {shlex.quote(str(ordinary))}",
        "metadata": f"rg --files {shlex.quote(str(record.parent))}",
    }[kind]
    tool_input = (
        f"text(await tools.exec_command({{cmd: {json.dumps(command)}}}));" if route == "functions.exec" else {"cmd": command}
    )
    payload = {
        "session_id": "codex-record-search",
        "turn_id": "turn",
        "tool_name": route,
        "tool_input": tool_input,
        "cwd": str(tmp_path),
        "tool_response": "検索の結果",
        "hook_event_name": "PostToolUse",
    }
    assert posttooluse.main(json.dumps(payload)) == 0
    result = capsys.readouterr().out
    notice = json.loads(result)["hookSpecificOutput"].get("additionalContext", "") if result else ""
    assert ("session-review-evidence" in notice) is (kind == "record")


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
        assert str(path) in context and "全文" in context
        assert "cat --" not in context and "functions.exec" not in context


@pytest.mark.parametrize("multiple", [False, True])
def test_capacity_without_known_receiver_budget_does_not_warn(tmp_path, capsys, multiple):
    first = _sized_file(tmp_path, 85_000)
    second = _sized_file(tmp_path, 85_000, "second.txt")
    command = shlex.join(["cat", "--", str(first), *([str(second)] if multiple else [])])
    assert (
        pretooluse.main(
            json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path), "turn_id": "turn"})
        )
        == 0
    )
    captured = capsys.readouterr()
    assert not captured.err
    assert ("分けて取得" in captured.out) is multiple


def _bundle(tmp_path, sizes):
    paths = []
    for index, size in enumerate(sizes):
        directory = tmp_path / f"doc{index}"
        directory.mkdir()
        path = directory / "AGENTS.md"
        path.write_text(f"{index}" + "あ" * size, encoding="utf-8")
        paths.append(path)
    return paths, "cat -- " + " ".join(str(path) for path in paths)


@pytest.mark.parametrize(("limit", "warned"), [("59", True), ("60", False)])
def test_bundled_read_over_claude_budget_is_split(tmp_path, limit, warned):
    _paths, command = _bundle(tmp_path, (29, 29))
    warning = large_reads.check_bundled_read(command, str(tmp_path), codex=False, environment={"BASH_MAX_OUTPUT_LENGTH": limit})
    assert (warning is not None and "分けて取得" in warning) is warned


def test_bundled_partial_reads_are_not_counted_as_full_text(tmp_path):
    paths, _command = _bundle(tmp_path, (99, 99))
    command = "sed -n '1p' -- " + " ".join(str(path) for path in paths)
    warning = large_reads.check_bundled_read(command, str(tmp_path), codex=False, environment={"BASH_MAX_OUTPUT_LENGTH": "100"})
    assert warning is None


def test_bundled_read_codex_budget_includes_envelope(tmp_path):
    _paths, command = _bundle(tmp_path, (99, 99))  # UTF-8で各298バイト、見積り各100トークン
    budget = 200 + large_reads._CODEX_ENVELOPE_TOKENS  # pylint: disable=protected-access
    within = large_reads.check_bundled_read(command, str(tmp_path), codex=True, environment={}, max_output_tokens=budget)
    over = large_reads.check_bundled_read(command, str(tmp_path), codex=True, environment={}, max_output_tokens=budget - 1)
    assert within is None and over is not None


@pytest.mark.parametrize(
    ("command", "names"),
    [
        ('R=docs; cat ${R}/first.md; echo done; cat "$R/second.md"', ["docs/first.md", "docs/second.md"]),
        ("R=docs; sed -n '1,$p' -- $R/first.md", ["docs/first.md"]),
        ('cat "$R/first.md"', []),
        ('R=docs; unset R; cat "$R/first.md"', []),
        ("R=docs; bash -c 'cat $R/first.md'", []),
        ("R='two dirs'; cat $R/first.md", []),
        ("""R='two dirs'; cat "${R}"/first.md""", ["two dirs/first.md"]),
        ('R=$UNSET; cat "$R/first.md"', []),
        ('R=$(pwd); cat "$R/first.md"', []),
        ('R=docs; true || R=other; cat "$R/first.md"', []),
        ('(R=docs); cat "$R/first.md"', []),
        ('R=docs; cat "$R/first.md" | head -1', []),
        ('R=docs; cat "$R/first.md" > copy.md', []),
    ],
)
def test_full_read_paths_from_same_shell_assignments(tmp_path: pathlib.Path, command: str, names: list[str]) -> None:
    paths = [path for group in bash_read_paths(command, str(tmp_path)) for path in group]
    assert paths == [tmp_path / name for name in names]
