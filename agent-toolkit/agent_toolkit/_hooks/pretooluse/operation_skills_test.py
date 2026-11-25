"""操作の直前に起動を求めるスキルについて、操作の判定と公開hookの案内を確かめる。

判定の試験は、検索・原因分析の記述・本文の作成と更新・エージェント向け文書の書込・検証の実行の各判定関数と
書込先の解析（`written_paths`）の境界を、既存のシェル・編集ツールの入力で確かめる。
案内の試験は公開PreToolUse・PostToolUse hookを同一プロセスで呼び、主体と文脈ごとにスキル1件につき1回だけ案内すること、
`Skill`で起動したスキル・軽量の委譲先・遮断した呼び出し・一時領域だけへの書込で案内しないこと、
案内済みのスキルの操作を判定しないことを確かめる。表の全項目と委譲先の主体の区分の組は`operation_skills_invariant_test.py`が確かめる。
"""

# 判定関数は表の項目の内側の入口であり、境界の試験で直接呼ぶ。
# pylint: disable=protected-access

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import delegated_session, session_state
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.termination_evidence_state import STATE_KEY as TERMINATION_EVIDENCE_KEY
from agent_toolkit._testing.output_policy_support import assert_output_policy
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _plan_file_state_env,
    _run,
    _run_posttooluse,
    denial_reason,
)

_SEARCH_SKILL = "agent-basics:search"
_BUGFIX_SKILL = "agent-basics:bugfix"
_WRITING_SKILL = "agent-basics:writing-standards"
_CHECK_EXECUTION_SKILL = "agent-basics:check-execution"
_NOTICE = re.compile(r"スキル`([^`]+)`を起動してください")
"""起動を求める案内から、案内したスキルの名前を取り出す。"""
_ROOT_CAUSE_BODY = "# 表題\n\n## 原因分析\n\n- 期待: 対象の全件を処理する。\n- 原因: 範囲の指定の誤り。\n"


def _bash(command: str, session_id: str, **extra: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id, **extra}


def _add_file_patch(path: pathlib.Path, body: str) -> dict:
    """Codexの`apply_patch`でファイルを追加する入力を返す。"""
    lines = "".join(f"+{line}\n" for line in body.splitlines())
    return {"command": f"*** Begin Patch\n*** Add File: {path}\n{lines}*** End Patch\n"}


def _notified(result: subprocess.CompletedProcess[str]) -> set[str]:
    assert result.returncode == 0
    assert not result.stderr
    return set(_NOTICE.findall(_additional_context(result)))


@pytest.fixture(name="temp_root")
def _temp_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "managed-temp"
    monkeypatch.setattr(managed_temp, "temp_root_path", lambda: root)
    return root


def _host_operations(work: pathlib.Path, temp: pathlib.Path) -> dict[str, dict[str, dict]]:
    """表の項目ごとに、Claude CodeとCodexがその操作で送るツール名と入力を返す。

    各入力は表の他の項目に当たらない。原因分析は、AWIの原稿と同じく一時領域へ書いて本文の作成・更新から外す。
    """
    return {
        "search": {
            "claude": {"tool_name": "Grep", "tool_input": {"pattern": "needle", "path": str(work)}},
            "codex": {"tool_name": "Bash", "tool_input": {"command": f"rg -n needle {work}"}},
        },
        "bugfix": {
            "claude": {"tool_name": "Write", "tool_input": {"file_path": str(temp / "awi.md"), "content": _ROOT_CAUSE_BODY}},
            "codex": {"tool_name": "apply_patch", "tool_input": _add_file_patch(temp / "awi.md", _ROOT_CAUSE_BODY)},
        },
        "writing-standards": {
            "claude": {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(work / "notes.md"), "old_string": "旧", "new_string": "新"},
            },
            "codex": {"tool_name": "apply_patch", "tool_input": _add_file_patch(work / "notes.md", "# 見出し\n")},
        },
        "check-execution": {
            "claude": {"tool_name": "Bash", "tool_input": {"command": "uv run --frozen pytest a_test.py"}},
            "codex": {"tool_name": "mcp__pyfltr__run", "tool_input": {"paths": [str(work / "a.py")]}},
        },
    }


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("entry", operation_skills.OPERATION_SKILLS, ids=lambda entry: entry.short_name)
def test_each_operation_notifies_only_its_skill(
    tmp_path: pathlib.Path, temp_root: pathlib.Path, entry: operation_skills.OperationSkill, host: str
) -> None:
    """表の各操作で、対応するスキルだけの起動を案内する。Codexは`turn_id`を持つ入力で確かめる。"""
    operation = _host_operations(tmp_path / "work", temp_root)[entry.short_name][host]
    payload = {**operation, "session_id": f"{entry.short_name}-{host}", "cwd": str(tmp_path)}
    if host == "codex":
        payload["turn_id"] = "turn-1"
    assert _notified(_run(payload, _plan_file_state_env(tmp_path))) == {entry.skill_name}


def test_one_call_with_several_operations_notifies_each_skill(tmp_path: pathlib.Path) -> None:
    payload = _bash("rg x; printf rule > AGENTS.md", "compound", cwd=str(tmp_path))
    assert _notified(_run(payload, _plan_file_state_env(tmp_path))) == {_SEARCH_SKILL, _WRITING_SKILL}


def test_notice_is_given_once_per_subject(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    main = {"tool_name": "Grep", "tool_input": {"pattern": "needle"}, "session_id": "subjects"}
    child = {**main, "agent_id": "child"}
    first = _run(main, env)
    assert _notified(first) == {_SEARCH_SKILL}
    assert_output_policy("search", _additional_context(first))
    repeat = _run(main, env)
    assert not _notified(repeat)
    assert_output_policy("search_repeat", _additional_context(repeat))
    assert _notified(_run(child, env)) == {_SEARCH_SKILL}
    assert not _notified(_run(child, env))


@pytest.mark.parametrize("skill", ["agent-basics:search", "search"])
def test_launched_skill_is_not_notified(tmp_path: pathlib.Path, skill: str) -> None:
    """Claude Codeの`Skill`で起動したスキルは、完全名と短縮名のどちらでも以後案内しない。他のスキルは案内する。"""
    env = _plan_file_state_env(tmp_path)
    launch = {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": skill}, "session_id": "launched"}
    assert _run_posttooluse(launch, env).returncode == 0
    assert not _notified(_run(_bash("rg -n needle .", "launched"), env))
    assert _notified(_run(_bash("uv run --frozen pytest a_test.py", "launched"), env)) == {_CHECK_EXECUTION_SKILL}


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("kind", ["explore", "shell", "write"])
def test_lightweight_delegate_is_not_notified(tmp_path: pathlib.Path, kind: str, host: str) -> None:
    marker = {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"} if host == "claude" else {"AGENT_TOOLKIT_OWNER_SESSION": "owner"}
    env = {**_plan_file_state_env(tmp_path), **marker, delegated_session.LAUNCH_KIND_ENV: kind}
    extra = {"turn_id": "turn-1"} if host == "codex" else {}
    assert not _notified(_run(_bash("rg -n needle .", f"{kind}-{host}", **extra), env))


def test_blocked_call_does_not_record_notice(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    blocked = _run(_bash("rg -n needle . && pkill -f needle", "blocked"), env)
    assert denial_reason(blocked)
    assert not _notified(blocked)
    assert _notified(_run(_bash("rg -n needle .", "blocked"), env)) == {_SEARCH_SKILL}


@pytest.mark.parametrize("tool", ["Write", "Bash", "apply_patch"])
def test_temporary_area_writes_are_not_notified(tmp_path: pathlib.Path, temp_root: pathlib.Path, tool: str) -> None:
    env = _plan_file_state_env(tmp_path)

    def payload(path: pathlib.Path) -> dict:
        tool_input = {
            "Write": {"file_path": str(path), "content": "本文\n"},
            "Bash": {"command": f"cat > {path} <<'EOF'\n本文\nEOF"},
            "apply_patch": _add_file_patch(path, "本文\n"),
        }[tool]
        extra = {"turn_id": "turn-1"} if tool == "apply_patch" else {}
        return {"tool_name": tool, "tool_input": tool_input, "session_id": "temporary", "cwd": str(tmp_path), **extra}

    assert not _notified(_run(payload(temp_root / "session" / "draft.md"), env))
    assert _notified(_run(payload(tmp_path / "work" / "draft.md"), env)) == {_WRITING_SKILL}


def test_mixed_temporary_and_artifact_writes_are_artifact_body_writing(tmp_path: pathlib.Path, temp_root: pathlib.Path) -> None:
    draft = temp_root / "draft.md"
    mixed = {"command": f"tee {draft} {tmp_path / 'notes.md'} <<<'本文'"}
    assert operation_skills._is_artifact_body_writing("Bash", mixed, str(tmp_path))
    assert not operation_skills._is_artifact_body_writing("Bash", {"command": f"tee {draft} <<<'本文'"}, str(tmp_path))


def test_observed_skill_is_not_judged_again(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _plan_file_state_env(tmp_path)
    payload = _bash("rg -n needle .", "judged-once")
    assert _notified(_run(payload, env)) == {_SEARCH_SKILL}
    launch = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": _SEARCH_SKILL},
        "session_id": "judged-once",
    }
    assert _run_posttooluse(launch, env).returncode == 0
    calls: list[tuple[object, ...]] = []

    def failing(*args: object) -> bool:
        calls.append(args)
        raise AssertionError("案内済みのスキルの操作を判定した")

    monkeypatch.setattr(operation_skills, "_is_search_operation", failing)
    repeated = _run(payload, env)
    assert not _notified(repeated)
    assert not calls
    assert "案内済みのスキルの操作を判定した" not in repeated.stdout


@pytest.mark.parametrize(
    "payload",
    [
        _bash("rg -l x .", "s"),
        _bash("git grep -n -F x", "s"),
        _bash("git -C sub grep x", "s"),
        _bash("grep -rn x .", "s"),
        _bash("egrep --recursive x .", "s"),
        _bash("grep -e x -R .", "s"),
        _bash("find . -name x", "s"),
        _bash("cd sub && rg x", "s"),
        _bash("timeout 10 rg x", "s"),
        _bash("bash -c 'rg x | head'", "s"),
        {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "session_id": "s"},
        {"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}, "session_id": "s"},
    ],
    ids=lambda payload: payload["tool_input"].get("command") or payload["tool_name"],
)
def test_search_inputs_are_search_operations(payload: dict) -> None:
    """検索ツールと、リポジトリ・ディレクトリを検索するコマンドを複合文の内側も含めて検索と判定する。"""
    assert operation_skills._is_search_operation(payload["tool_name"], payload["tool_input"])


@pytest.mark.parametrize(
    "command",
    [
        "cat f | rg x",
        "git log --oneline | grep -r x",
        "grep x file",
        "grep -er file",
        "grep -e -r file",
        "grep -- -r file",
        "echo rg x",
        "git log -S rg",
        "ls -la",
    ],
)
def test_non_search_commands_are_not_search_operations(command: str) -> None:
    """標準入力の検索、再帰しない`grep`、他のコマンドの引数を検索と判定しない。"""
    assert not operation_skills._is_search_operation("Bash", {"command": command})


@pytest.mark.parametrize(
    ("arguments", "recursive"),
    [
        ("-R -e -- /dev/null", True),
        ("--regexp -- -R /dev/null", True),
        ("--regexp=-- -R /dev/null", True),
        ("--file -- -R /dev/null", True),
        ("--file=-- -R /dev/null", True),
        ("--regexp -R /dev/null", False),
        ("--regexp=-R /dev/null", False),
        ("--file -R /dev/null", False),
        ("--include -R needle /dev/null", False),
        ("--regexp needle -- -R", False),
        ("-ne-R /dev/null", False),
    ],
)
def test_grep_option_values_do_not_count_as_recursion(arguments: str, recursive: bool) -> None:
    assert operation_skills._is_search_operation("Bash", {"command": "grep " + arguments}) is recursive


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("sed 's/x/y/' -- -i", []),
        ("sed -i 's/x/y/' -- --help", ["--help"]),
        ("sed -e --help -i note.md", ["note.md"]),
        ("sed -i -- 's/x/y/' note.md", ["note.md"]),
        ("cp -- source --help", ["--help"]),
        ("install -- source -d", ["-d"]),
        ("tee -- --help", ["--help"]),
        ("tee -- -", []),
        ("sponge -a -- note.md", ["note.md"]),
        ("sponge --help", []),
        ("gawk -i inplace '{print}' note.md", ["note.md"]),
        ("gawk --include=inplace -v x=1 -f script.awk note.md", ["note.md"]),
        ("gawk -i inplace -- '{print}' --help", ["--help"]),
        ("gawk -i other '{print}' note.md", []),
    ],
)
def test_write_paths_respect_option_roles(command: str, expected: list[str], tmp_path: pathlib.Path) -> None:
    """オプションの値と終端以後のファイル名を、内容を書き込む対象と区別する。"""
    assert operation_skills.written_paths("Bash", {"command": command}, str(tmp_path)) == [
        str(tmp_path / path) for path in expected
    ]


@pytest.mark.parametrize(
    ("payload", "skills"),
    [
        (_bash("rg x", "codex"), ["search"]),
        (_bash("git grep -n -F x", "codex"), ["search"]),
        (_bash("find . -name x", "codex"), ["search"]),
        (_bash("grep -rn x .", "codex"), ["search"]),
        (_bash("cat > /tmp/awi.md <<'EOF'\n# 表題\n\n## 原因分析\nEOF", "codex"), ["bugfix"]),
        (
            {
                "tool_name": "apply_patch",
                "tool_input": {"command": "*** Begin Patch\n*** Add File: awi.md\n+# 表題\n+## 原因分析\n*** End Patch\n"},
                "session_id": "codex",
            },
            ["bugfix", "writing-standards"],
        ),
        (_bash("echo rule > AGENTS.md", "codex"), ["writing-standards"]),
    ],
    ids=["rg", "git-grep", "find", "grep-r", "root-cause-bash", "root-cause-apply_patch", "agent-doc"],
)
def test_codex_inputs_match_required_skill_operations(payload: dict, skills: list[str]) -> None:
    """Codexの`Bash`・`apply_patch`の入力を、表の該当する項目の操作と判定する。"""
    matched = {
        entry.short_name
        for entry in operation_skills.OPERATION_SKILLS
        if entry.matches(payload["tool_name"], payload["tool_input"], "")
    }
    assert set(skills) <= matched


@pytest.mark.parametrize(
    "command",
    [
        "echo rule > AGENTS.md",
        "printf rule 2>>.claude/rules/local.md",
        "printf rule | tee .claude/skills/x/SKILL.md",
        "sed -i 's/old/new/' agent-toolkit/skills/atk-mode/playbooks/commit.md",
        "sed --in-place -e 's/old/new/' CLAUDE.md",
        "python3 -c \"p='AGENTS.md'; open(p,'w').write('rule')\"",
        "uv run --frozen python -c \"from pathlib import Path; Path('.claude/rules/x.md').write_text('rule')\"",
        "python3 - <<'PY'\np='.claude/skills/server-log-review/SKILL.md'\nopen(p,'w').write('rule')\nPY",
        "python3 -c \"from pathlib import Path; p=Path('AGENTS.md'); p.write_bytes(b'rule')\"",
        "for item in one; do printf rule > AGENTS.md; done",
        "if test -f marker; then sed -i 's/old/new/' agent-toolkit/skills/atk-mode/playbooks/commit.md; fi",
        "case value in one) printf rule | tee .claude/skills/x/SKILL.md;; esac",
        "time for item in one; do cp ordinary.md AGENTS.md; done",
        "! if test -f marker; then cp ordinary.md .claude/rules/local.md; fi",
        "coproc WORKER if test -f marker; then cp -t .claude/rules AGENTS.md; fi",
    ],
)
def test_agent_document_bash_writing_is_detected(command: str) -> None:
    """エージェント向け文書へのBashの書込を、複合文の内側も含めて判定する。"""
    assert operation_skills._is_agent_document_writing("Bash", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "cat AGENTS.md",
        "echo 'open(AGENTS.md, w)'",
        "printf rule > README.md",
        "tee script.py",
        "tee --help AGENTS.md",
        "sed --help -i 's/a/b/' AGENTS.md",
        "sed 's/old/new/' AGENTS.md",
        "sed -i 's/AGENTS.md/new/' ordinary.md",
        "python3 -c \"p='AGENTS.md'; open(p,'r').read()\"",
        "python3 -c \"p='AGENTS.md'; p.write_text('rule')\"",
        "python3 -c \"p='AGENTS.md'; open('other.md','w').write(p)\"",
        "python3 -c \"p='AGENTS.md'; print(\\\"open(p,'w')\\\")\"",
        "cat <<'PY'\nopen('AGENTS.md','w').write('rule')\nPY",
        "python3 - <<'PY'\ndef example():\n    open('AGENTS.md','w').write('rule')\nPY",
    ],
)
def test_non_agent_document_writes_and_examples_are_not_detected(command: str) -> None:
    """読取、通常文書への書込、本文中の例をエージェント向け文書の書込と判定しない。"""
    assert not operation_skills._is_agent_document_writing("Bash", {"command": command})


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": "AGENTS.md", "content": "rule"}),
        ("Edit", {"file_path": ".claude/rules/x.md", "old_string": "old", "new_string": "new"}),
        ("MultiEdit", {"file_path": "CLAUDE.md", "edits": [{"old_string": "old", "new_string": "new"}]}),
        (
            "apply_patch",
            {"command": "*** Begin Patch\n*** Update File: AGENTS.md\n*** Move to: ordinary.md\n@@\n-old\n+new\n*** End Patch"},
        ),
        (
            "apply_patch",
            {"command": "*** Begin Patch\n*** Update File: ordinary.md\n*** Move to: AGENTS.md\n@@\n-old\n+new\n*** End Patch"},
        ),
    ],
)
def test_edit_tool_agent_document_writing_is_detected(tool: str, tool_input: dict) -> None:
    """編集ツールの対象と移動元・移動先のどちらかがエージェント向け文書なら判定する。"""
    assert operation_skills._is_agent_document_writing(tool, tool_input)


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Write", {"file_path": "calc_test.py", "content": "def test_sum():\n    assert sum([1, 2]) == 3\n"}),
        ("Edit", {"file_path": "src/lib.rs", "old_string": "old", "new_string": "#[test]\nfn check() {}"}),
        ("MultiEdit", {"file_path": "src/main.ts", "edits": [{"old_string": "old", "new_string": "new"}]}),
        ("Write", {"file_path": "docs/guide.md", "content": "# 使い方\n"}),
        (
            "apply_patch",
            {"command": "*** Begin Patch\n*** Add File: tests/case.spec.ts\n+test('sum', () => {});\n*** End Patch"},
        ),
        ("apply_patch", {"command": "*** Begin Patch\n*** Update File: notes.md\n@@\n-old\n+new\n*** End Patch"}),
        (
            "apply_patch",
            {"command": "*** Begin Patch\n*** Update File: source.cs\n*** Move to: test.cs\n@@\n-old\n+new\n*** End Patch"},
        ),
    ],
)
def test_body_edits_are_body_writing(tool: str, tool_input: dict) -> None:
    """本文を変える編集ツールの入力を、言語と文書種別によらず本文の作成・更新と判定する。"""
    assert operation_skills.is_body_writing(tool, tool_input)


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "calc_test.py"}),
        ("Write", {"file_path": "calc_test.py"}),
        ("Edit", {"file_path": "calc_test.py", "old_string": "old"}),
        ("Edit", {"new_string": "new"}),
        ("MultiEdit", {"file_path": "calc_test.py", "edits": []}),
        ("apply_patch", {"command": "*** Begin Patch\n*** Delete File: calc_test.py\n*** End Patch"}),
        ("apply_patch", {"command": "*** Begin Patch\n*** Update File: source.py\n*** Move to: renamed.py\n*** End Patch"}),
    ],
)
def test_operations_without_body_edit_are_not_body_writing(tool: str, tool_input: dict) -> None:
    """読取、本文を持たない入力、削除、本文を変えない移動を本文の作成・更新と判定しない。"""
    assert not operation_skills.is_body_writing(tool, tool_input)


_WRITE_FORMS = [
    "printf rule > {path}",
    "printf rule >> {path}",
    "printf rule 2>>{path}",
    "tee {path}",
    "tee -a {path}",
    "sed -i 's/a/b/' {path}",
    "sed --in-place -e 's/a/b/' {path}",
    "cp ordinary.md {path}",
    "cp -t {directory} AGENTS.md",
    "mv ordinary.md {path}",
    "install -m 644 ordinary.md {path}",
    "rsync ordinary.md {path}",
    "perl -pi -e 's/a/b/' {path}",
    "dd if=ordinary.md of={path}",
    "python3 -c \"open('{literal}', 'w').write('rule')\"",
    "python3 -c \"from pathlib import Path; Path('{literal}').write_text('rule')\"",
]


@pytest.mark.parametrize("form", _WRITE_FORMS)
@pytest.mark.parametrize("prefix", ["", "/repo/", "~/", "$HOME/", '"$HOME/', "${HOME}/"])
def test_bash_static_document_suffix_is_agent_document_writing(form: str, prefix: str) -> None:
    closing_quote = '"' if prefix.startswith('"') else ""
    path = prefix + ".claude/rules/AGENTS.md" + closing_quote
    directory = prefix + ".claude/rules/" + closing_quote
    literal = prefix.replace('"', "") + ".claude/rules/AGENTS.md"
    command = form.format(path=path, directory=directory, literal=literal)
    assert operation_skills._is_agent_document_writing("Bash", {"command": command})


@pytest.mark.parametrize("form", _WRITE_FORMS)
@pytest.mark.parametrize("prefix", ["", "/repo/", "~/", "$HOME/", '"$HOME/', "${HOME}/"])
def test_bash_static_ordinary_suffix_is_not_agent_document_writing(form: str, prefix: str) -> None:
    closing_quote = '"' if prefix.startswith('"') else ""
    command = form.replace("AGENTS.md", "README.md").format(
        path=prefix + "docs/README.md" + closing_quote,
        directory=prefix + "docs/" + closing_quote,
        literal=prefix.replace('"', "") + "docs/README.md",
    )
    assert not operation_skills._is_agent_document_writing("Bash", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "sed -i -e '1d' ~/.claude/rules/x.md",
        "cat >> ~/.claude/rules/x.md <<'EOF'\nrule\nEOF",
        'echo rule > "$HOME/.claude/rules/x.md"',
        "printf rule | tee -a ${HOME}/AGENTS.md",
        "python3 -c \"open('$HOME/.claude/rules/x.md','a').write('x')\"",
        "sed -i 's/a/b/' \"$REPO\"/.claude/skills/x/SKILL.md",
        "cp a.md /home/shimoyama/.claude/rules/x.md",
        "cp a.md ~/.claude/rules/x.md",
        "cp -t ~/.claude/rules x.md",
        "cp a.md b.md ~/.claude/rules/",
        "mv draft.md .claude/rules/x.md",
        'install -m 644 a.md "$HOME/.claude/rules/x.md"',
        "rsync a.md ~/.claude/rules/x.md",
        "perl -pi -e 's/a/b/' ~/.claude/rules/x.md",
        "dd if=a.md of=$HOME/.claude/rules/x.md",
        "cp -T ordinary.md $HOME/AGENTS.md",
        "cp a.md AGENTS.md $HOME/",
        "mv -S .bak ordinary.md $HOME/AGENTS.md",
        "install -g group -o owner -m 644 ordinary.md $HOME/AGENTS.md",
        "cp --target-directory=$HOME/ AGENTS.md",
        "rsync ordinary.md AGENTS.md $HOME/",
        "perl -i.bak script.pl $HOME/AGENTS.md",
        "perl -i -E 'say 1' $HOME/AGENTS.md",
    ],
)
def test_bash_document_write_option_boundaries(command: str) -> None:
    """値を取るオプションと転送先の指定を読み分け、エージェント向け文書への書込を判定する。"""
    assert operation_skills._is_agent_document_writing("Bash", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "printf rule > $FILE",
        "printf rule > $HOME/AGENTS.$EXT",
        "tee $HOME/*.md",
        "cp AGENTS.md ordinary.md",
        "cp ordinary.md $DEST",
        "cp -S AGENTS.md ordinary.md other.md",
        "cp -aT AGENTS.md ordinary/",
        "cp --help AGENTS.md",
        "cp AGENTS.md /tmp/AGENTS.md.bak",
        "mv AGENTS.md ordinary.md",
        "install -d AGENTS.md",
        "install -vd .claude/rules agent-toolkit/skills/atk-mode/playbooks",
        "install --help AGENTS.md",
        "rsync AGENTS.md ordinary.md",
        "perl -e 'print 1' AGENTS.md",
        "dd if=AGENTS.md of=ordinary.md",
        "echo 'cp ordinary.md AGENTS.md'",
        "touch AGENTS.md",
        "ln ordinary.md AGENTS.md",
    ],
)
def test_bash_document_write_negative_boundaries(command: str) -> None:
    """未知の展開、複製元、ヘルプ表示、本文を書かないコマンドをエージェント向け文書の書込と判定しない。"""
    assert not operation_skills._is_agent_document_writing("Bash", {"command": command})


@pytest.mark.parametrize(
    ("options", "writes"),
    [
        ("-MFile::Basename -ne 'print'", False),
        ("-M File::Basename -ne 'print'", False),
        ("-mFile::Basename -ne 'print'", False),
        ("-Ilib -ne 'print'", False),
        ("-I include -ne 'print'", False),
        ("-Fi -ne 'print'", False),
        ("-Ci -ne 'print'", False),
        ("-xi -ne 'print'", False),
        ("-d:File::Basename -ne 'print'", False),
        ("tool.pl --id", False),
        ("tool.pl -i", False),
        ("-- tool.pl -i", False),
        ("-ne 'print' --id", False),
        ("-cpi -e 's/a/b/'", False),
        ("-pi -e 's/a/b/'", True),
        ("-i.bak tool.pl", True),
        ("-i -E 'say 1'", True),
        ("-MFile::Basename -pi -e 's/a/b/'", True),
        ("-I include -pi -e 's/a/b/'", True),
        ("-lpi -e 's/a/b/'", True),
        ("-l077pi -e 's/a/b/'", True),
        ("-0777pi -e 's/a/b/'", True),
        ("-0x0Api -e 's/a/b/'", True),
        ("-dpi -e 's/a/b/'", True),
    ],
)
def test_perl_document_write_option_and_program_boundaries(options: str, writes: bool) -> None:
    command = f"perl {options} ~/.claude/rules/x.md"
    assert operation_skills._is_agent_document_writing("Bash", {"command": command}) is writes


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/awi.md", "content": _ROOT_CAUSE_BODY}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/awi.md", "old_string": "x", "new_string": _ROOT_CAUSE_BODY}},
        {
            "tool_name": "MultiEdit",
            "tool_input": {"file_path": "/tmp/awi.md", "edits": [{"old_string": "x", "new_string": _ROOT_CAUSE_BODY}]},
        },
        {"tool_name": "Bash", "tool_input": {"command": f"cat > /tmp/awi.md <<'EOF'\n{_ROOT_CAUSE_BODY}EOF"}},
    ],
    ids=["write", "edit", "multiedit", "bash"],
)
def test_root_cause_writing_is_detected(payload: dict) -> None:
    """編集ツールとBashで`## 原因分析`の見出しを書く入力を、原因分析の記述と判定する。"""
    assert operation_skills._is_root_cause_writing(payload["tool_name"], payload["tool_input"])


@pytest.mark.parametrize(
    "content",
    ["## 原因分析の根拠\n", "### 原因分析\n", "`## 原因分析`を置く\n", "本文だけ\n"],
)
def test_writing_without_root_cause_heading_is_not_root_cause_writing(content: str) -> None:
    assert not operation_skills._is_root_cause_writing("Write", {"file_path": "/example/awi.md", "content": content})


_VERIFICATION_PAYLOADS = [
    _bash("uv run --frozen pyfltr run --output-format=jsonl a.py", "verify"),
    _bash("uvx pyfltr fast --commands=pytest", "verify"),
    _bash("python3 -m pyfltr ci", "verify"),
    _bash("uv run --frozen pytest -v -p no:cacheprovider agent-toolkit/agent_toolkit", "verify"),
    _bash("python3 -m pytest tests", "verify"),
    _bash("make test", "verify"),
    _bash("mise run lint", "verify"),
    _bash("pnpm run test:unit", "verify"),
    _bash("npm test", "verify"),
    _bash("pnpm exec vitest run", "verify"),
    _bash("npx playwright test", "verify"),
    _bash("cargo test", "verify"),
    _bash("dotnet test lc.sln", "verify"),
    _bash("cd /w && make test-browser", "verify"),
    {"tool_name": "mcp__plugin_agent-toolkit_pyfltr__run", "tool_input": {"paths": ["/w/a.py"]}, "session_id": "verify"},
    {"tool_name": "mcp__pyfltr__run", "tool_input": {"paths": ["/w/a.py"]}, "session_id": "verify"},
]


@pytest.mark.parametrize(
    "payload",
    _VERIFICATION_PAYLOADS,
    ids=lambda payload: payload["tool_input"].get("command") or payload["tool_name"],
)
def test_verification_operations_are_detected(payload: dict) -> None:
    """検証ツール・タスクランナー・pyfltrのMCPツールの実行を、検証の実行と判定する。"""
    assert operation_skills.is_verification_operation(payload["tool_name"], payload["tool_input"])


@pytest.mark.parametrize(
    "payload",
    [
        _bash("uv run --frozen pyfltr show-run 1 --commands pytest --output", "query"),
        _bash("pyfltr list-runs", "query"),
        _bash("uvx pyfltr command-info pytest --output-format=jsonl", "query"),
        _bash("uv run --frozen pytest --help", "query"),
        _bash("uv run --frozen pytest --collect-only -q", "query"),
        _bash("atk run-script plan-check -- --plan /p.md", "query"),
        _bash("sed -n '1,200p' -- /tmp/pytest.log", "query"),
        _bash("cat /tmp/pytest.log", "query"),
        _bash("make help", "query"),
        _bash("pnpm install", "query"),
        _bash("cargo build", "query"),
        _bash("git status", "query"),
        _bash("echo pytest", "query"),
        {"tool_name": "mcp__plugin_agent-toolkit_pyfltr__show_run", "tool_input": {"run_id": "1"}, "session_id": "query"},
        {"tool_name": "mcp__pyfltr__list_runs", "tool_input": {}, "session_id": "query"},
    ],
    ids=lambda payload: payload["tool_input"].get("command") or payload["tool_name"],
)
def test_verification_queries_are_not_verification_operations(payload: dict) -> None:
    """保存ログの表示、ヘルプ・一覧などの照会、構築と導入を検証の実行と判定しない。"""
    assert not operation_skills.is_verification_operation(payload["tool_name"], payload["tool_input"])


@pytest.mark.parametrize(
    ("tool", "target"),
    [
        (tool, target)
        for tool in ("Write", "Edit", "MultiEdit", "apply_patch")
        for target in ("own", "other", "different-directory", "missing")
    ]
    + [("apply_patch", "mixed")],
)
def test_only_own_handoff_is_exempt(tmp_path: pathlib.Path, tool: str, target: str) -> None:
    env = _plan_file_state_env(tmp_path)
    own = str(tmp_path / "handoff.md")
    other = str(tmp_path / "awi-draft.md")
    if target != "missing":
        env["AGENT_TOOLKIT_HANDOFF_PATH"] = own
    path = own if target in {"own", "missing", "mixed"} else other
    if target == "different-directory":
        path = str(tmp_path / "other-directory/handoff.md")
    inputs: dict[str, dict] = {
        "Write": {"file_path": path, "content": "到達点"},
        "Edit": {"file_path": path, "old_string": "前", "new_string": "後"},
        "MultiEdit": {"file_path": path, "edits": [{"old_string": "前", "new_string": "後"}]},
        "apply_patch": {"command": f"*** Begin Patch\n*** Add File: {path}\n+到達点\n*** End Patch\n"},
    }
    if target == "mixed":
        inputs[tool]["command"] = (
            f"*** Begin Patch\n*** Add File: {own}\n+到達点\n*** Add File: {other}\n+原稿\n*** End Patch\n"
        )
    payload = {"tool_name": tool, "tool_input": inputs[tool], "session_id": "handoff-exempt"}
    assert (_WRITING_SKILL in _notified(_run(payload, env))) is (target != "own")


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("tool", ["Write", "apply_patch", "Bash"])
def test_body_writing_does_not_depend_on_existing_file(tmp_path: pathlib.Path, tool: str, exists: bool) -> None:
    target = tmp_path / "memo.md"
    if exists:
        target.write_text("旧本文\n", encoding="utf-8")
    inputs = {
        "Write": {"file_path": str(target), "content": "新本文\n"},
        "apply_patch": {"command": f"*** Begin Patch\n*** Add File: {target}\n+新本文\n*** End Patch"},
        "Bash": {"command": f"cat > {target} <<'EOF'\n新本文\nEOF"},
    }
    assert operation_skills.is_body_writing(tool, inputs[tool], str(tmp_path))


@pytest.mark.parametrize(
    "form",
    [
        "grep x source.py 2>{path}",
        "tee {path}",
        "sponge {path}",
        "dd if=source.py of={path}",
        "cat > {path} <<'EOF'\n本文\nEOF",
        "tee {path} <<'EOF'\n本文\nEOF",
        "sed -i 's/a/b/' {path}",
        "perl -pi -e 's/a/b/' {path}",
        "gawk -i inplace '{{print}}' {path}",
        "python3 -c \"open('{path}', 'w').write('本文')\"",
        "python3 - <<'PY'\nopen('{path}', 'w').write('本文')\nPY",
    ],
)
def test_discard_output_is_not_body_writing(tmp_path: pathlib.Path, form: str) -> None:
    for path in (os.devnull, str(tmp_path / "memo.md")):
        data = {"command": form.format(path=path)}
        paths = operation_skills.written_paths("Bash", data, str(tmp_path))
        if path == os.devnull:
            assert not paths
            assert not operation_skills.is_body_writing("Bash", data, str(tmp_path))
        else:
            assert paths == [path]


@pytest.mark.skipif(os.name == "nt", reason="POSIXのsymlinkと通常名NULを確認する")
def test_discard_alias_and_mixed_output_keep_ordinary_files(tmp_path: pathlib.Path) -> None:
    alias = tmp_path / "AGENTS.md"
    alias.symlink_to(os.devnull)
    ordinary = tmp_path / "NUL"
    ordinary.write_text("旧本文", encoding="utf-8")
    for command in ("cat > AGENTS.md <<<'本文'", "python3 - <<'PY'\nopen('AGENTS.md', 'w').write('本文')\nPY"):
        assert not operation_skills.written_paths("Bash", {"command": command}, str(tmp_path))
        assert not operation_skills.is_body_writing("Bash", {"command": command}, str(tmp_path))
    data = {"command": "tee AGENTS.md NUL <<<'本文'"}
    assert operation_skills.written_paths("Bash", data, str(tmp_path)) == [str(ordinary)]
    assert operation_skills.is_body_writing("Bash", data, str(tmp_path))


@pytest.mark.skipif(os.name == "nt", reason="POSIXのfd別名を確認する")
@pytest.mark.parametrize("alias", ["/dev/stdout", "/dev/stderr", "/dev/fd/1"])
def test_standard_stream_alias_is_not_discarded_by_name(tmp_path: pathlib.Path, alias: str) -> None:
    data = {"command": f"cat > {alias} <<<'本文'"}
    assert operation_skills.written_paths("Bash", data, str(tmp_path))
    assert operation_skills.is_body_writing("Bash", data, str(tmp_path))


def test_public_hook_discard_output_has_no_edit_guidance(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    payload = _bash(f"cat > {os.devnull} <<<'本文'", "discard-public", cwd=str(tmp_path))
    assert _WRITING_SKILL not in _notified(_run(payload, env))
    result = _run_posttooluse({**payload, "tool_response": "", "hook_event_name": "PostToolUse"}, env)
    assert result.returncode == 0
    state = session_state.read_state("discard-public")
    assert not state.get(TERMINATION_EVIDENCE_KEY, {}).get("works")


@pytest.mark.parametrize("tool", ["Write", "Edit", "apply_patch"])
def test_edit_tools_exclude_discard_destination(tmp_path: pathlib.Path, tool: str) -> None:
    data = {
        "Write": {"file_path": os.devnull, "content": "本文"},
        "Edit": {"file_path": os.devnull, "old_string": "旧", "new_string": "新"},
        "apply_patch": {"command": f"*** Begin Patch\n*** Update File: {os.devnull}\n@@\n-旧\n+新\n*** End Patch"},
    }[tool]
    assert not operation_skills.written_paths(tool, data, str(tmp_path))
    assert not operation_skills.is_body_writing(tool, data, str(tmp_path))


@pytest.mark.parametrize(
    "command",
    ["$COMMAND > {path}", "> {path}", "A=1 > {path}", "sh -c 'printf x > {path}' name \"$ARG\""],
    ids=["unknown-command", "redirect-only", "assignment-only", "static-shell-body"],
)
def test_static_output_of_unknown_execution_is_a_write(tmp_path: pathlib.Path, command: str) -> None:
    """未知の実行でも、静的な絶対出力先は通常の書込先として返す。"""
    path = tmp_path / "out.md"
    assert operation_skills.written_paths("Bash", {"command": command.format(path=path)}, str(tmp_path)) == [str(path)]


@pytest.mark.parametrize("command", ['$COMMAND > "$OUT"', 'printf x > "$ROOT/path.md"', "cat > $ROOT/path.md <<<x"])
def test_unknown_output_path_is_not_converted_to_a_real_path(tmp_path: pathlib.Path, command: str) -> None:
    """未知の出力先と未知の接頭部を持つ出力先は、推測した実パスへ変えない。"""
    assert not operation_skills.written_paths("Bash", {"command": command}, str(tmp_path))


def test_partial_suffix_identifies_document_kind_without_a_real_path(tmp_path: pathlib.Path) -> None:
    data = {"command": 'cat > "$ROOT/.claude/skills/x/SKILL.md" <<<x'}
    assert not operation_skills.written_paths("Bash", data, str(tmp_path))
    assert operation_skills._is_agent_document_writing("Bash", data, str(tmp_path))


def test_notified_operation_is_never_denied(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    payload = _bash("rg -n needle .", "observed-next")
    assert _notified(_run(payload, env)) == {_SEARCH_SKILL}
    again = _run(payload, env)
    assert again.returncode == 0 and (not again.stdout or not denial_reason(again))
    assert not _notified(again)
