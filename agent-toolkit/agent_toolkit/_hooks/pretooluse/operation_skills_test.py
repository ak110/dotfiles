"""agent-toolkit/agent_toolkit/_hooks/pretooluse/operation_skills.py のテスト。

PreToolUseとPostToolUseの統合フックをsubprocessで起動し、セッション状態を経由した遮断の有無を検証する。
遮断はホスト本来の取得（Skill起動か資料の読取）をPostToolUseが観測するまで続く。
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from agent_toolkit._hooks import plugin_resources, rules_context
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _plan_file_state_env,
    _read_session_state,
    _run,
    _run_posttooluse,
)

_SEARCH_SKILL = "agent-toolkit:search"
_WARN_OPENING = '<atk-auto source="pretooluse" kind="block">'

_DELEGATION_TOOLS = (
    "Agent",
    "Task",
    "mcp__plugin_agent-toolkit_agents_server__start",
    "mcp__plugin_agent-toolkit_agents_server__send_message",
    "mcp__plugin_agent-toolkit_agents_server__kill",
    "mcp__plugin_agent-toolkit_agents_server__list",
)


@pytest.mark.parametrize("tool", _DELEGATION_TOOLS)
def test_delegation_warning_preserves_permission_and_context(tmp_path: pathlib.Path, tool: str) -> None:
    """特殊分岐を通る委譲操作でも、未起動通知は主体ごとに1回で、既存の通過判定を保つ。"""
    env = _plan_file_state_env(tmp_path)
    # 継続入力の欠落は既存の警告判定を通し、未起動通知との結合も確かめる。
    payload = {"tool_name": tool, "tool_input": {"prompt": "調査する"}, "session_id": "delegation"}
    first = _run(payload, env)
    assert first.returncode == 2
    assert "agent-toolkit:delegation" in first.stderr
    assert "agent-toolkit:delegation" in _run(payload, env).stderr
    _record_skill(env, "delegation", "agent-toolkit:delegation")
    assert "agent-toolkit:delegation" not in _additional_context(_run(payload, env))
    assert "agent-toolkit:delegation" in _run({**payload, "agent_id": "child"}, env).stderr


@pytest.mark.parametrize("tool", _DELEGATION_TOOLS)
def test_delegation_ready_and_codex_acquisition(tmp_path: pathlib.Path, tool: str) -> None:
    """Skill起動の後は通り、Codexは`SKILL.md`の読取を観測するまで記録せずに遮断を続ける。"""
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "ready", "delegation")
    payload = {"tool_name": tool, "tool_input": {"prompt": "調査する"}, "session_id": "ready"}
    assert "agent-toolkit:delegation" not in _additional_context(_run(payload, env))
    codex = {**payload, "session_id": "codex-delegation", "turn_id": "turn-1"}
    assert _run(codex, env).returncode == 2
    state_file = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="codex-delegation")
    state = _read_session_state(tmp_path, "codex-delegation") if state_file.exists() else {}
    assert rules_context.OPERATION_SKILL_READY_KEY not in state
    _acquire(env, codex, "delegation")
    state = _read_session_state(tmp_path, "codex-delegation")
    assert state[rules_context.OPERATION_SKILL_READY_KEY]["agent-toolkit:delegation"] == ["main"]
    assert "agent-toolkit:delegation" not in _additional_context(_run(codex, env))


def test_blocked_delegation_does_not_consume_warning(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    blocked = {"tool_name": _DELEGATION_TOOLS[4], "tool_input": {"session_id": "unknown"}, "session_id": "blocked"}
    result = _run(blocked, env)
    assert result.returncode == 2
    assert "agent-toolkit:delegation" not in result.stderr
    state_file = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="blocked")
    state = _read_session_state(tmp_path, "blocked") if state_file.exists() else {}
    assert rules_context.OPERATION_SKILL_READY_KEY not in state
    valid = {**blocked, "tool_name": _DELEGATION_TOOLS[5], "tool_input": {}}
    assert "agent-toolkit:delegation" in _run(valid, env).stderr


def _bash(command: str, session_id: str, **extra: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id, **extra}


def _record_skill(env: dict[str, str], session_id: str, skill: str, **extra: object) -> None:
    result = _run_posttooluse(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": skill},
            "session_id": session_id,
            **extra,
        },
        env,
    )
    assert result.returncode == 0


def _acquire(env: dict[str, str], payload: dict, skill: str) -> None:
    """主体の区分に応じたホスト本来の手段で`SKILL.md`を取得し、PostToolUseへ観測させる。

    Claude CodeはSkill起動、Codexは`SKILL.md`の全文を返したBashの読取を観測の入力とする。
    """
    extra = {key: payload[key] for key in ("agent_id", "turn_id") if key in payload}
    if "turn_id" not in payload:
        _record_skill(env, payload["session_id"], f"agent-toolkit:{skill}", **extra)
        return
    path = plugin_resources.skill_document_path(skill, "SKILL.md")
    result = _run_posttooluse(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"cat {path}"},
            "tool_response": path.read_text(encoding="utf-8"),
            "session_id": payload["session_id"],
            **extra,
        },
        env,
    )
    assert result.returncode == 0


def _blocked_until_acquired(env: dict[str, str], payload: dict, skill: str) -> None:
    """取得の観測までは同じ操作の再発行も遮断し、観測の後は同じ主体の操作を通す。"""
    full = f"agent-toolkit:{skill}"
    for _ in range(2):
        warning = _search_warning(payload, env)
        assert warning.startswith(_WARN_OPENING)
        assert f"`{full}`" in warning
    _acquire(env, payload, skill)
    assert f"`{full}`" not in _search_warning(payload, env)


def _search_warning(payload: dict, env: dict[str, str]) -> str:
    result = _run(payload, env)
    assert result.returncode in {0, 2}
    return result.stderr if result.returncode == 2 else _additional_context(result)


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
def test_search_without_skill_blocks_until_acquired(tmp_path: pathlib.Path, payload: dict) -> None:
    env = _plan_file_state_env(tmp_path)
    _blocked_until_acquired(env, payload, "search")
    assert _search_warning(payload, env) == ""


@pytest.mark.parametrize("skill", [_SEARCH_SKILL, "search"])
def test_search_after_skill_use_does_not_warn(tmp_path: pathlib.Path, skill: str) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "s", skill)
    assert _search_warning(_bash("rg x", "s"), env) == ""


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
def test_non_search_commands_do_not_warn(tmp_path: pathlib.Path, command: str) -> None:
    assert _search_warning(_bash(command, "s"), _plan_file_state_env(tmp_path)) == ""


def test_subagent_context_is_separate_from_main(tmp_path: pathlib.Path) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "s", _SEARCH_SKILL)
    assert _search_warning(_bash("rg x", "s", agent_id="sub-1"), env).startswith(_WARN_OPENING)
    assert _search_warning(_bash("rg x", "s"), env) == ""

    _record_skill(env, "t", _SEARCH_SKILL, agent_id="sub-2")
    assert _search_warning(_bash("rg x", "t", agent_id="sub-2"), env) == ""
    assert _search_warning(_bash("rg x", "t"), env).startswith(_WARN_OPENING)


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
        (_bash("atk managed-temp create --prefix x", "codex"), ["managed-temp"]),
        (_bash("echo rule > AGENTS.md", "codex"), ["writing-standards"]),
    ],
    ids=["rg", "git-grep", "find", "grep-r", "root-cause-bash", "root-cause-apply_patch", "managed-temp", "agent-doc"],
)
def test_codex_operations_block_until_skill_md_read(tmp_path: pathlib.Path, payload: dict, skills: list[str]) -> None:
    """Codexは`SKILL.md`の絶対パスの読取を案内し、本文を返した読取の観測までは再発行も遮断する。"""
    env = _plan_file_state_env(tmp_path)
    payload = {**payload, "turn_id": "turn-1"}
    for _ in range(2):
        result = _run(payload, env)
        assert result.returncode == 2
        assert "同じ操作を再発行" in result.stderr
        for skill in skills:
            assert str(plugin_resources.skill_document_path(skill, "SKILL.md")) in result.stderr
    for skill in skills:
        _acquire(env, payload, skill)
    assert _run(payload, env).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "echo rule > AGENTS.md",
        "printf rule 2>>.claude/rules/local.md",
        "printf rule | tee .claude/skills/x/SKILL.md",
        "sed -i 's/old/new/' agent-toolkit/rules/01-agent.md",
        "sed --in-place -e 's/old/new/' CLAUDE.md",
        "python3 -c \"p='AGENTS.md'; open(p,'w').write('rule')\"",
        "uv run --frozen python -c \"from pathlib import Path; Path('.claude/rules/x.md').write_text('rule')\"",
        "python3 - <<'PY'\np='.claude/skills/server-log-review/SKILL.md'\nopen(p,'w').write('rule')\nPY",
        "python3 -c \"from pathlib import Path; p=Path('AGENTS.md'); p.write_bytes(b'rule')\"",
        "for item in one; do printf rule > AGENTS.md; done",
        "if test -f marker; then sed -i 's/old/new/' agent-toolkit/rules/01-agent.md; fi",
        "case value in one) printf rule | tee .claude/skills/x/SKILL.md;; esac",
        "time for item in one; do cp ordinary.md AGENTS.md; done",
        "! if test -f marker; then cp ordinary.md .claude/rules/local.md; fi",
        "coproc WORKER if test -f marker; then cp -t .claude/rules AGENTS.md; fi",
    ],
)
def test_agent_document_bash_writing_blocks_until_acquired(tmp_path: pathlib.Path, command: str) -> None:
    _blocked_until_acquired(_plan_file_state_env(tmp_path), _bash(command, "writing"), "writing-standards")


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
def test_non_agent_document_writes_and_examples_do_not_warn(tmp_path: pathlib.Path, command: str) -> None:
    assert "agent-toolkit:writing-standards" not in _search_warning(_bash(command, "writing"), _plan_file_state_env(tmp_path))


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
def test_edit_tool_agent_document_writing_and_skill_context(tmp_path: pathlib.Path, tool: str, tool_input: dict) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "writing", "agent-toolkit:writing-standards")
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": "writing"}
    assert "agent-toolkit:writing-standards" not in _search_warning(payload, env)
    payload["agent_id"] = "child"
    _blocked_until_acquired(env, payload, "writing-standards")


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
def test_body_edit_delivers_writing_standards_per_actor(tmp_path, tool, tool_input):
    """本文を持つ編集は命名と言語によらず同じ遮断と主体別の取得記録を使う。"""
    env = _plan_file_state_env(tmp_path)
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": "body-writing"}
    _blocked_until_acquired(env, payload, "writing-standards")
    assert _run(payload, env).returncode == 0
    assert _run({**payload, "agent_id": "child"}, env).returncode == 2
    _record_skill(env, "ready-body", "writing-standards")
    assert _run({**payload, "session_id": "ready-body"}, env).returncode == 0


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
def test_operations_without_body_edit_do_not_deliver(tmp_path, tool, tool_input):
    """読取・本文欠落・削除・純粋な移動を本文執筆の契機から除く。"""
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": "no-body"}
    assert "agent-toolkit:writing-standards" not in _search_warning(payload, _plan_file_state_env(tmp_path))


def test_operation_skills_for_one_operation_share_one_notice(tmp_path: pathlib.Path) -> None:
    """同じ操作に該当する複数のスキルの取得は、1回の遮断の案内へまとめる。"""
    result = _run(_bash("rg x; printf rule > AGENTS.md", "s"), _plan_file_state_env(tmp_path))
    assert result.returncode == 2
    context = result.stderr
    assert context.count(_WARN_OPENING) == 1
    assert f"`{_SEARCH_SKILL}`" in context
    assert "agent-toolkit:writing-standards" in context


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
def test_bash_static_document_suffix_blocks_until_acquired(tmp_path: pathlib.Path, form: str, prefix: str) -> None:
    """展開される先頭と操作形式を組み合わせ、公開hookが取得の観測まで遮断し、観測の後は通すことを検証する。"""
    closing_quote = '"' if prefix.startswith('"') else ""
    path = prefix + ".claude/rules/AGENTS.md" + closing_quote
    directory = prefix + ".claude/rules/" + closing_quote
    literal = prefix.replace('"', "") + ".claude/rules/AGENTS.md"
    command = form.format(path=path, directory=directory, literal=literal)
    _blocked_until_acquired(_plan_file_state_env(tmp_path), _bash(command, "static-writing"), "writing-standards")


@pytest.mark.parametrize("form", _WRITE_FORMS)
@pytest.mark.parametrize("prefix", ["", "/repo/", "~/", "$HOME/", '"$HOME/', "${HOME}/"])
def test_bash_static_ordinary_suffix_does_not_warn(tmp_path: pathlib.Path, form: str, prefix: str) -> None:
    """同じ操作と各展開表記でも通常文書の書込は警告しない。"""
    closing_quote = '"' if prefix.startswith('"') else ""
    command = form.replace("AGENTS.md", "README.md").format(
        path=prefix + "docs/README.md" + closing_quote,
        directory=prefix + "docs/" + closing_quote,
        literal=prefix.replace('"', "") + "docs/README.md",
    )
    assert "agent-toolkit:writing-standards" not in _search_warning(_bash(command, "ordinary"), _plan_file_state_env(tmp_path))


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
def test_bash_document_write_option_boundaries(tmp_path: pathlib.Path, command: str) -> None:
    assert "agent-toolkit:writing-standards" in _search_warning(_bash(command, "options"), _plan_file_state_env(tmp_path))


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
        "install -vd .claude/rules agent-toolkit/rules",
        "install --help AGENTS.md",
        "rsync AGENTS.md ordinary.md",
        "perl -e 'print 1' AGENTS.md",
        "dd if=AGENTS.md of=ordinary.md",
        "echo 'cp ordinary.md AGENTS.md'",
        "touch AGENTS.md",
        "ln ordinary.md AGENTS.md",
    ],
)
def test_bash_document_write_negative_boundaries(tmp_path: pathlib.Path, command: str) -> None:
    assert "agent-toolkit:writing-standards" not in _search_warning(_bash(command, "negative"), _plan_file_state_env(tmp_path))


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
def test_perl_document_write_option_and_program_boundaries(tmp_path: pathlib.Path, options: str, writes: bool) -> None:
    """公開hookは、オプションの値に含まれる`i`とprogramfile以降のオプションを除き、有効なin-place編集だけを警告する。"""
    payload = _bash(f"perl {options} ~/.claude/rules/x.md", "perl-options")
    env = _plan_file_state_env(tmp_path)
    if writes:
        _blocked_until_acquired(env, payload, "writing-standards")
    else:
        assert "agent-toolkit:writing-standards" not in _search_warning(payload, env)


_BUGFIX_SKILL = "agent-toolkit:bugfix"
_ROOT_CAUSE_BODY = "# 表題\n\n## 原因分析\n\n| 要因系統 | L1 現象 |\n"


def _bugfix_warnings(context: str) -> int:
    return context.count(f"原因分析の記述の実行前に`{_BUGFIX_SKILL}`の基準を取得する")


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
def test_root_cause_writing_without_bugfix_blocks_until_acquired(tmp_path: pathlib.Path, payload: dict) -> None:
    """`agent-toolkit:bugfix`を起動しないまま`## 原因分析`の見出し行を書くと、起動を観測するまで遮断する。

    起草で原因分析を書く時点に未起動を示す手掛かりが無いと、起動を求める条文が文脈にあっても起動されず、
    外部の挙動を一次資料で確かめる初動の基準が適用されないまま原因が確定する。
    """
    env = _plan_file_state_env(tmp_path)
    payload = {**payload, "session_id": "root-cause"}
    _record_skill(env, "root-cause", "writing-standards")
    for _ in range(2):
        result = _run(payload, env)
        assert result.returncode == 2
        assert _bugfix_warnings(result.stderr) == 1
    _record_skill(env, "root-cause", _BUGFIX_SKILL)
    second = _run(payload, env)
    assert second.returncode == 0
    assert _bugfix_warnings(_additional_context(second)) == 0


@pytest.mark.parametrize("skill", [_BUGFIX_SKILL, "bugfix"])
def test_root_cause_writing_after_bugfix_does_not_warn(tmp_path: pathlib.Path, skill: str) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "root-cause", "writing-standards")
    _record_skill(env, "root-cause", skill)
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/awi.md", "content": _ROOT_CAUSE_BODY}}
    result = _run({**payload, "session_id": "root-cause"}, env)
    assert result.returncode == 0
    assert _bugfix_warnings(_additional_context(result)) == 0


@pytest.mark.parametrize(
    "content",
    ["## 原因分析の根拠\n", "### 原因分析\n", "`## 原因分析`を置く\n", "本文だけ\n"],
)
def test_writing_without_root_cause_heading_does_not_warn(tmp_path: pathlib.Path, content: str) -> None:
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/tmp/awi.md", "content": content}, "session_id": "s"}
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "s", "writing-standards")
    result = _run(payload, env)
    assert result.returncode == 0
    assert _bugfix_warnings(_additional_context(result)) == 0


_MANAGED_TEMP_SKILL = "agent-toolkit:managed-temp"


def _managed_temp_warnings(context: str) -> int:
    return context.count(f"個別のmanaged-temp領域の作成の実行前に`{_MANAGED_TEMP_SKILL}`の基準を取得する")


@pytest.mark.parametrize(
    "command",
    [
        "atk managed-temp create --prefix lane-02-grp-a",
        "C:/tools/atk.cmd managed-temp create --prefix x",
        r"'C:\tools\atk.cmd' managed-temp create --prefix x",
        "atk.py managed-temp create --prefix x",
        "/home/u/dotfiles/agent-toolkit/bin/atk managed-temp create --prefix x",
        "cd /repo && atk managed-temp create --prefix x",
        "for l in a b; do atk managed-temp create --prefix $l; done",
        "cd /repo && d=$(atk managed-temp create --prefix y)",
    ],
)
def test_managed_temp_create_without_skill_blocks_until_acquired(tmp_path: pathlib.Path, command: str) -> None:
    """`agent-toolkit:managed-temp`を起動しないまま個別の領域を作成すると、起動を観測するまで遮断する。

    作成の時点に未起動を示す手掛かりが無いと、置き場所の規定を読まずに参照される保存物を個別の領域へ置き、
    領域の回収で保存物が失われる。
    """
    env = _plan_file_state_env(tmp_path)
    for _ in range(2):
        result = _run(_bash(command, "managed-temp"), env)
        assert result.returncode == 2
        assert _managed_temp_warnings(result.stderr) == 1
    _record_skill(env, "managed-temp", _MANAGED_TEMP_SKILL)
    second = _run(_bash(command, "managed-temp"), env)
    assert _managed_temp_warnings(second.stderr + _additional_context(second)) == 0


@pytest.mark.parametrize("skill", [_MANAGED_TEMP_SKILL, "managed-temp"])
def test_managed_temp_create_after_skill_does_not_warn(tmp_path: pathlib.Path, skill: str) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "managed-temp", skill)
    result = _run(_bash("atk managed-temp create --prefix x", "managed-temp"), env)
    assert _managed_temp_warnings(_additional_context(result)) == 0


@pytest.mark.parametrize(
    "command",
    [
        "atk managed-temp cleanup --path /tmp/x",
        "atk managed-temp list",
        "git grep -F 'atk managed-temp create'",
        "printf '%s' 'atk managed-temp create'",
        "printf '%s' C:/tools/atk.cmd managed-temp create",
        "atk wi list",
    ],
)
def test_other_commands_do_not_warn_managed_temp(tmp_path: pathlib.Path, command: str) -> None:
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "s", "search")
    result = _run(_bash(command, "s"), env)
    assert result.returncode == 0
    assert _managed_temp_warnings(_additional_context(result)) == 0


@pytest.mark.parametrize("source", ["compact", "clear"])
def test_legacy_warning_and_context_reset_require_acquisition(
    tmp_path: pathlib.Path,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """旧版の警告済み状態は取得の証拠にせず、文脈破棄の後は取得を再び求める。"""
    env = _plan_file_state_env(tmp_path)
    state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="legacy")
    state_path.write_text(json.dumps({"operation_skill_ready_agents": {_SEARCH_SKILL: ["main"]}}), encoding="utf-8")
    payload = {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "session_id": "legacy"}
    assert _run(payload, env).returncode == 2
    assert _run(payload, env).returncode == 2
    _record_skill(env, "legacy", _SEARCH_SKILL)
    assert _run(payload, env).returncode == 0
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "session_id": "legacy", "source": source})) == 0
    capsys.readouterr()
    assert _run(payload, env).returncode == 2


_CHECK_EXECUTION_SKILL = "agent-toolkit:check-execution"
_SCOPE_REFERENCE = plugin_resources.skill_document_path("check-execution", "references/verification-scope.md")


def _record_read(env: dict[str, str], session_id: str, path: pathlib.Path, **extra: object) -> None:
    """Claude CodeのReadが資料の全文を返したPostToolUseを観測させる。

    `tool_response`の形はClaude Code 2.1.303の本体のReadの結果（`type`・`file`）から写した。
    """
    text = path.read_text(encoding="utf-8")
    lines = len(text.splitlines())
    file = {"filePath": str(path), "content": text, "numLines": lines, "startLine": 1, "totalLines": lines}
    result = _run_posttooluse(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
            "tool_response": {"type": "text", "file": file},
            "session_id": session_id,
            **extra,
        },
        env,
    )
    assert result.returncode == 0


_VERIFICATION_PAYLOADS = [
    _bash("atk run-script plan-verify -- --plan /p.md --worktree /w --timeout 60 --summary-only", "verify"),
    _bash("atk run-script plan-verify -- --plan /p.md --worktree /w --list", "verify"),
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
    _bash("atk run-command --cwd /w --timeout 60 -- uv run --frozen pytest a_test.py", "verify"),
    _bash("cd /w && make test-browser", "verify"),
    {"tool_name": "mcp__plugin_agent-toolkit_pyfltr__run", "tool_input": {"paths": ["/w/a.py"]}, "session_id": "verify"},
    {"tool_name": "mcp__pyfltr__run", "tool_input": {"paths": ["/w/a.py"]}, "session_id": "verify"},
]


@pytest.mark.parametrize(
    "payload",
    _VERIFICATION_PAYLOADS,
    ids=lambda payload: payload["tool_input"].get("command") or payload["tool_name"],
)
def test_verification_operations_require_check_execution_and_scope_reference(tmp_path: pathlib.Path, payload: dict) -> None:
    """検証の実行は`SKILL.md`のSkill起動と`references/verification-scope.md`の全文の読取を観測するまで遮断する。

    対象選定の基準は参照資料にあり、スキル本体の起動だけでは実行直前に基準へ到達しない。
    """
    env = _plan_file_state_env(tmp_path)
    first = _run(payload, env)
    assert first.returncode == 2
    assert f"`Skill`で`{_CHECK_EXECUTION_SKILL}`を起動する" in first.stderr
    assert str(_SCOPE_REFERENCE) in first.stderr
    _record_skill(env, "verify", _CHECK_EXECUTION_SKILL)
    after_skill = _run(payload, env)
    assert after_skill.returncode == 2
    assert str(_SCOPE_REFERENCE) in after_skill.stderr
    assert f"`Skill`で`{_CHECK_EXECUTION_SKILL}`を起動する" not in after_skill.stderr
    _record_read(env, "verify", _SCOPE_REFERENCE)
    assert _run(payload, env).returncode == 0


def test_verification_acquisition_is_requested_again_after_compact(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話圧縮の後はメインの取得の記録を除き、次の検証の実行で再び取得を求める。"""
    env = _plan_file_state_env(tmp_path)
    payload = _bash("uv run --frozen pytest a_test.py", "verify-compact")
    _record_skill(env, "verify-compact", _CHECK_EXECUTION_SKILL)
    _record_read(env, "verify-compact", _SCOPE_REFERENCE)
    assert _run(payload, env).returncode == 0
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    event = {"hook_event_name": "SessionStart", "session_id": "verify-compact", "source": "compact"}
    assert rules_context.main(json.dumps(event)) == 0
    capsys.readouterr()
    result = _run(payload, env)
    assert result.returncode == 2
    assert str(_SCOPE_REFERENCE) in result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        _bash("uv run --frozen pyfltr show-run 1 --commands pytest --output", "query"),
        _bash("pyfltr list-runs", "query"),
        _bash("uvx pyfltr command-info pytest --output-format=jsonl", "query"),
        _bash("uv run --frozen pytest --help", "query"),
        _bash("uv run --frozen pytest --collect-only -q", "query"),
        _bash("atk run-script plan-verify -- --help", "query"),
        _bash("atk run-command --records-file /tmp/results.json", "query"),
        _bash("atk read-file -- /tmp/pytest.log", "query"),
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
def test_verification_queries_do_not_require_check_execution(tmp_path: pathlib.Path, payload: dict) -> None:
    """ヘルプ・保存ログ・一覧・状態の照会は検証の実行として遮断しない。"""
    result = _run(payload, _plan_file_state_env(tmp_path))
    assert _CHECK_EXECUTION_SKILL not in result.stderr + _additional_context(result)
