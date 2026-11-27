"""退避出力と書込先の解析が共有する、静的な読取引数の境界を確かめる。  pyfltr: allow-comment"""

import pathlib
import shlex

import pytest

from agent_toolkit._hooks.pretooluse import commit_identity, large_reads, operation_skills
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths, sed_read_range


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


def test_python_heredoc_write_uses_invocation_cwd(tmp_path):
    target = tmp_path / "next"
    command = f"cd {shlex.quote(str(target))} && python3 - <<'PY'\nfrom pathlib import Path\nPath('file').write_text('x')\nPY\n"
    assert operation_skills.written_paths("Bash", {"command": command}, str(tmp_path)) == [str(target / "file")]


@pytest.mark.parametrize(
    "command", ["cat --help", "cat --version", "cat --unknown", "less --help", "less -Z", "more --version"]
)
def test_non_read_options_are_not_inferred_as_full_reads(tmp_path: pathlib.Path, command: str) -> None:
    target = tmp_path / "note.md"
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


def test_known_read_operand_is_kept_as_a_candidate_with_unknown_arguments(tmp_path):
    target = tmp_path / "input.md"
    target.write_text("本文\n", encoding="utf-8")
    command = f'cat -- {shlex.quote(str(target))} "$OTHER"'
    assert list(bash_read_paths(command, str(tmp_path), include_partial=True)) == [(target,)]
    assert not list(bash_read_paths(command, str(tmp_path)))


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
