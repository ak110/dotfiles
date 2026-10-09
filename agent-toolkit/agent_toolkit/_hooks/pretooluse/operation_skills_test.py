"""agent-toolkit/agent_toolkit/_hooks/pretooluse/operation_skills.py のテスト。

PreToolUseとPostToolUseの統合フックをsubprocessで起動し、セッション状態を経由した警告の有無を検証する。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import claude_settings
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE
from agent_toolkit._testing.pretooluse_support import (
    _additional_context,
    _plan_file_state_env,
    _read_session_state,
    _run,
    _run_posttooluse,
)

_SEARCH_SKILL = operation_skills.OPERATION_SKILLS[0].skill_name
_SEARCH_SKILL_MD = pathlib.Path(__file__).resolve().parents[3] / "skills" / "search" / "SKILL.md"
_WARN_OPENING = '<atk-auto source="pretooluse" kind="warn">'


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


def _search_warning(payload: dict, env: dict[str, str]) -> str:
    result = _run(payload, env)
    assert result.returncode == 0
    return _additional_context(result)


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
def test_search_without_skill_warns_once_per_context(tmp_path: pathlib.Path, payload: dict) -> None:
    env = _plan_file_state_env(tmp_path)
    first = _search_warning(payload, env)
    assert first.startswith(_WARN_OPENING)
    assert f"`{_SEARCH_SKILL}`" in first
    assert _search_warning(payload, env) == ""


@pytest.mark.parametrize("skill", [_SEARCH_SKILL, operation_skills.OPERATION_SKILLS[0].short_name])
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


# 軽量起動の各modeで発火し得る検索の入力。許可ツールに含まれるツールだけを使う。
_LIGHTWEIGHT_SEARCH_PAYLOADS = [
    ("explore", {"tool_name": "Grep", "tool_input": {"pattern": "x"}}),
    ("explore", {"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}}),
    ("shell", {"tool_name": "Bash", "tool_input": {"command": "git grep -n -F x"}}),
    ("write", {"tool_name": "Grep", "tool_input": {"pattern": "x"}}),
    ("write", {"tool_name": "Glob", "tool_input": {"pattern": "*.md"}}),
]


@pytest.mark.parametrize(
    ("launch_kind", "payload"),
    _LIGHTWEIGHT_SEARCH_PAYLOADS,
    ids=[f"{kind}-{payload['tool_name']}" for kind, payload in _LIGHTWEIGHT_SEARCH_PAYLOADS],
)
def test_claude_warning_offers_skill_md_read_for_sessions_without_skill(
    tmp_path: pathlib.Path, launch_kind: claude_backend.LaunchKind, payload: dict
) -> None:
    """Claudeの警告は`Skill`の起動と、`Skill`を使えない主体が実在する`SKILL.md`を`Read`で読む操作を併記する。

    `agents_server`の軽量起動の委譲先は`Skill`を使えずスキルの一覧も届かないため、`Skill`だけを示す警告では
    起動の失敗を報告するだけで検索の基準へ到達できない。案内する`Read`が各modeの許可ツールにあることも確かめる。
    """
    allowed_tools = claude_backend._LAUNCH_ALLOWED_TOOLS[launch_kind]  # pylint: disable=protected-access
    assert "Read" in allowed_tools
    options = claude_backend._build_options(str(tmp_path), None, None, launch_kind=launch_kind)  # pylint: disable=protected-access
    assert "Skill" in options.disallowed_tools
    claude_settings.cleanup_settings(options)
    assert payload["tool_name"] in allowed_tools
    env = {**_plan_file_state_env(tmp_path), "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}

    warning = _search_warning({**payload, "session_id": f"light-{launch_kind}"}, env)

    assert warning.startswith(_WARN_OPENING)
    assert f"ツール`Skill`で`{_SEARCH_SKILL}`を起動し" in warning
    assert "ツール一覧に`Skill`が無い主体" in warning
    assert f"`{_SEARCH_SKILL_MD}`）を`Read`で全文読んで" in warning
    assert _SEARCH_SKILL_MD.is_file()


@pytest.mark.parametrize(
    "payload",
    [
        _bash("rg x", "codex"),
        _bash("git grep -n -F x", "codex"),
        _bash("find . -name x", "codex"),
        _bash("grep -rn x .", "codex"),
        _bash("cat > /tmp/awi.md <<'EOF'\n# 表題\n\n## 原因分析\nEOF", "codex"),
        {
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Add File: awi.md\n+# 表題\n+## 原因分析\n*** End Patch\n"},
            "session_id": "codex",
        },
        _bash("atk managed-temp create --prefix x", "codex"),
        _bash("echo rule > AGENTS.md", "codex"),
    ],
    ids=["rg", "git-grep", "find", "grep-r", "root-cause-bash", "root-cause-apply_patch", "managed-temp", "agent-doc"],
)
def test_codex_operations_do_not_warn_or_record(tmp_path: pathlib.Path, payload: dict) -> None:
    """Codexでは表の全操作で未起動の警告も起動済みの記録も生じない。

    Codexは`SKILL.md`の読取でスキルを適用し、hookはその読取を起動として観測できない。
    警告すると、全文を読んだ後の正当な操作にも再読と結果の見直しを求める。
    """
    env = _plan_file_state_env(tmp_path)
    codex_payload = {**payload, "turn_id": "turn-1"}

    for _ in range(2):
        result = _run(codex_payload, env)
        assert result.returncode == 0
        assert "を起動しないまま" not in _additional_context(result)

    state_file = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=payload["session_id"])
    state = _read_session_state(tmp_path, payload["session_id"]) if state_file.exists() else {}
    assert rules_context.OPERATION_SKILL_READY_KEY not in state


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
    ],
)
def test_agent_document_bash_writing_warns_once(tmp_path: pathlib.Path, command: str) -> None:
    env = _plan_file_state_env(tmp_path)
    payload = _bash(command, "writing")
    assert "agent-toolkit:writing-standards" in _search_warning(payload, env)
    assert "agent-toolkit:writing-standards" not in _search_warning(payload, env)


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
    assert "agent-toolkit:writing-standards" in _search_warning(payload, env)
    assert "agent-toolkit:writing-standards" not in _search_warning(payload, env)


def test_search_warning_joins_other_warnings(tmp_path: pathlib.Path) -> None:
    result = _run(_bash("rg x; printf rule > AGENTS.md", "s"), _plan_file_state_env(tmp_path))
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context.count(_WARN_OPENING) == 2
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
def test_bash_static_document_suffix_warns_once(tmp_path: pathlib.Path, form: str, prefix: str) -> None:
    """展開される先頭と操作形式を組み合わせ、公開hookの初回警告と反復0回を検証する。"""
    closing_quote = '"' if prefix.startswith('"') else ""
    path = prefix + ".claude/rules/AGENTS.md" + closing_quote
    directory = prefix + ".claude/rules/" + closing_quote
    literal = prefix.replace('"', "") + ".claude/rules/AGENTS.md"
    command = form.format(path=path, directory=directory, literal=literal)
    env = _plan_file_state_env(tmp_path)
    payload = _bash(command, "static-writing")
    assert "agent-toolkit:writing-standards" in _search_warning(payload, env)
    assert "agent-toolkit:writing-standards" not in _search_warning(payload, env)


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
    """公開hookで値中のiとprogramfile後の旗を除き、有効なin-place編集だけを警告する。"""
    payload = _bash(f"perl {options} ~/.claude/rules/x.md", "perl-options")
    env = _plan_file_state_env(tmp_path)
    warning = _search_warning(payload, env)
    assert ("agent-toolkit:writing-standards" in warning) is writes
    assert "agent-toolkit:writing-standards" not in _search_warning(payload, env)


_BUGFIX_SKILL = "agent-toolkit:bugfix"
_ROOT_CAUSE_BODY = "# 表題\n\n## 原因分析\n\n| 要因系統 | L1 現象 |\n"


def _bugfix_warnings(context: str) -> int:
    return context.count(f"`{_BUGFIX_SKILL}`を起動しないまま原因分析の記述を実行した")


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
def test_root_cause_writing_without_bugfix_warns_once_per_context(tmp_path: pathlib.Path, payload: dict) -> None:
    """`agent-toolkit:bugfix`を起動しないまま`## 原因分析`の見出し行を書くと、文脈ごとに1回だけ警告する。

    起草で原因分析を書く時点に未起動を示す手掛かりが無いと、起動を求める条文が文脈にあっても起動されず、
    外部の挙動を一次資料で確かめる初動の基準が適用されないまま原因が確定する。
    """
    env = _plan_file_state_env(tmp_path)
    payload = {**payload, "session_id": "root-cause"}
    first = _run(payload, env)
    assert first.returncode == 0
    assert _bugfix_warnings(_additional_context(first)) == 1
    second = _run(payload, env)
    assert second.returncode == 0
    assert _bugfix_warnings(_additional_context(second)) == 0


@pytest.mark.parametrize("skill", [_BUGFIX_SKILL, "bugfix"])
def test_root_cause_writing_after_bugfix_does_not_warn(tmp_path: pathlib.Path, skill: str) -> None:
    env = _plan_file_state_env(tmp_path)
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
    result = _run(payload, _plan_file_state_env(tmp_path))
    assert result.returncode == 0
    assert _bugfix_warnings(_additional_context(result)) == 0


_MANAGED_TEMP_SKILL = "agent-toolkit:managed-temp"


def _managed_temp_warnings(context: str) -> int:
    return context.count(f"`{_MANAGED_TEMP_SKILL}`を起動しないまま")


@pytest.mark.parametrize(
    "command",
    [
        "atk managed-temp create --prefix lane-02-grp-a",
        "/home/u/dotfiles/agent-toolkit/bin/atk managed-temp create --prefix x",
        "cd /repo && atk managed-temp create --prefix x",
        "for l in a b; do atk managed-temp create --prefix $l; done",
        "cd /repo && d=$(atk managed-temp create --prefix y)",
    ],
)
def test_managed_temp_create_without_skill_warns_once_per_context(tmp_path: pathlib.Path, command: str) -> None:
    """`agent-toolkit:managed-temp`を起動しないまま個別の領域を作成すると、文脈ごとに1回だけ警告する。

    作成の時点に未起動を示す手掛かりが無いと、置き場所の規定を読まずに参照される保存物を個別の領域へ置き、
    領域の回収で保存物が失われる。
    """
    env = _plan_file_state_env(tmp_path)
    first = _run(_bash(command, "managed-temp"), env)
    assert first.returncode == 0
    assert _managed_temp_warnings(_additional_context(first)) == 1
    second = _run(_bash(command, "managed-temp"), env)
    assert _managed_temp_warnings(_additional_context(second)) == 0


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
        "atk wi list",
    ],
)
def test_other_commands_do_not_warn_managed_temp(tmp_path: pathlib.Path, command: str) -> None:
    result = _run(_bash(command, "s"), _plan_file_state_env(tmp_path))
    assert result.returncode == 0
    assert _managed_temp_warnings(_additional_context(result)) == 0
