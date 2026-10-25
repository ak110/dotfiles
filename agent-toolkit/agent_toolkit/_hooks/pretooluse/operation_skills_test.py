"""既存のシェル・編集・委譲・検証の各操作に対する検出条件を確かめる。

配送、取得、主体、圧縮および両ホストの応答契約はread_delivery_test.pyが検証する。
本ファイルは既存のテストコードの入力を使って検出条件を確かめ、同じ状態遷移に対する子プロセス起動の反復を省く。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import plugin_resources, rules_context
from agent_toolkit._hooks.pretooluse import operation_skills
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
def test_delegation_warning_preserves_permission_and_context(tool: str) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("delegation", tool, {"prompt": "調査する"})


def _bash(command: str, session_id: str, **extra: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id, **extra}


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
def test_search_without_skill_blocks_until_acquired(payload: dict) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("search", payload["tool_name"], payload["tool_input"])


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
def test_non_search_commands_do_not_warn(command: str) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert not operation_skills.operation_matches("search", "Bash", {"command": command})


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
        (_bash("mktemp -d /cache/session/x-XXXXXXXX", "codex"), ["managed-temp"]),
        (_bash("echo rule > AGENTS.md", "codex"), ["writing-standards"]),
    ],
    ids=["rg", "git-grep", "find", "grep-r", "root-cause-bash", "root-cause-apply_patch", "managed-temp", "agent-doc"],
)
def test_codex_operations_block_until_skill_md_read(payload: dict, skills: list[str]) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    matched = {
        entry.short_name
        for entry in operation_skills.OPERATION_SKILLS
        if entry.matches(payload["tool_name"], payload["tool_input"])
    }
    assert set(skills) <= matched


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
def test_agent_document_bash_writing_blocks_until_acquired(command: str) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("agent-document-writing", "Bash", {"command": command})


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
def test_non_agent_document_writes_and_examples_do_not_warn(command: str) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert not operation_skills.operation_matches("agent-document-writing", "Bash", {"command": command})


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
def test_edit_tool_agent_document_writing_and_skill_context(tool: str, tool_input: dict) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("agent-document-writing", tool, tool_input)


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
def test_body_edit_delivers_writing_standards_per_actor(tool, tool_input):
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("body-writing", tool, tool_input)


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
def test_operations_without_body_edit_do_not_deliver(tool, tool_input):
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert not operation_skills.operation_matches("body-writing", tool, tool_input)


def test_operation_skills_for_one_operation_share_one_notice() -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    matched = {
        entry.short_name
        for entry in operation_skills.OPERATION_SKILLS
        if entry.matches("Bash", {"command": "rg x; printf rule > AGENTS.md"})
    }
    assert {"search", "writing-standards"} <= matched


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
def test_bash_static_document_suffix_blocks_until_acquired(form: str, prefix: str) -> None:
    """展開される先頭と操作形式を組み合わせ、公開hookが取得の観測まで遮断し、観測の後は通すことを検証する。"""
    closing_quote = '"' if prefix.startswith('"') else ""
    path = prefix + ".claude/rules/AGENTS.md" + closing_quote
    directory = prefix + ".claude/rules/" + closing_quote
    literal = prefix.replace('"', "") + ".claude/rules/AGENTS.md"
    command = form.format(path=path, directory=directory, literal=literal)
    assert operation_skills.operation_matches("agent-document-writing", "Bash", {"command": command})


@pytest.mark.parametrize("form", _WRITE_FORMS)
@pytest.mark.parametrize("prefix", ["", "/repo/", "~/", "$HOME/", '"$HOME/', "${HOME}/"])
def test_bash_static_ordinary_suffix_does_not_warn(form: str, prefix: str) -> None:
    """同じ操作と各展開表記でも通常文書の書込は警告しない。"""
    closing_quote = '"' if prefix.startswith('"') else ""
    command = form.replace("AGENTS.md", "README.md").format(
        path=prefix + "docs/README.md" + closing_quote,
        directory=prefix + "docs/" + closing_quote,
        literal=prefix.replace('"', "") + "docs/README.md",
    )
    assert not operation_skills.operation_matches("agent-document-writing", "Bash", {"command": command})


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
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("agent-document-writing", "Bash", {"command": command})


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
def test_bash_document_write_negative_boundaries(command: str) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert not operation_skills.operation_matches("agent-document-writing", "Bash", {"command": command})


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
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert (
        operation_skills.operation_matches(
            "agent-document-writing", "Bash", {"command": f"perl {options} ~/.claude/rules/x.md"}
        )
        is writes
    )


_BUGFIX_SKILL = "agent-toolkit:bugfix"
_ROOT_CAUSE_BODY = "# 表題\n\n## 原因分析\n\n| 要因系統 | L1 現象 |\n"


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
def test_root_cause_writing_without_bugfix_blocks_until_acquired(payload: dict) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("root-cause-writing", payload["tool_name"], payload["tool_input"])


@pytest.mark.parametrize(
    "content",
    ["## 原因分析の根拠\n", "### 原因分析\n", "`## 原因分析`を置く\n", "本文だけ\n"],
)
def test_writing_without_root_cause_heading_does_not_warn(content: str) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert not operation_skills.operation_matches(
        "root-cause-writing", "Write", {"file_path": "/example/awi.md", "content": content}
    )


_MANAGED_TEMP_SKILL = "agent-toolkit:managed-temp"


@pytest.mark.parametrize(
    "command",
    [
        "mktemp -d '/cache/session/lane-02-XXXXXXXX'",
        "mktemp --directory -p /cache/session",
        "mktemp -dp /cache/session work-XXXXXXXX",
        "/usr/bin/mktemp -p /cache/session -d",
        "cd /repo && mktemp -d /cache/session/x-XXXXXXXX",
        'for l in a b; do mktemp -d "/cache/session/$l-XXXXXXXX"; done',
        "cd /repo && d=$(mktemp -d /cache/session/y-XXXXXXXX)",
    ],
)
def test_temp_directory_create_triggers_managed_temp(command: str) -> None:
    """個別の一時ディレクトリを作成する静的な呼出しを、置換と複合文の内側も含めて検出する。"""
    assert operation_skills.operation_matches("managed-temp-create", "Bash", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "mktemp /cache/session/file-XXXXXXXX",
        "mktemp -p /cache/session",
        "mktemp -- -d",
        "rm -rf -- /cache/session/x-12345678",
        "git grep -F 'mktemp -d'",
        "printf '%s' 'mktemp -d'",
        "atk wi list",
    ],
)
def test_other_commands_do_not_warn_managed_temp(command: str) -> None:
    """一時ファイルの作成、回収、他のコマンドの引数を一時ディレクトリの作成として扱わない。"""
    assert not operation_skills.operation_matches("managed-temp-create", "Bash", {"command": command})


_CHECK_EXECUTION_SKILL = "agent-toolkit:check-execution"
_SCOPE_REFERENCE = plugin_resources.skill_document_path("check-execution", "references/verification-scope.md")


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
def test_verification_operations_require_check_execution_and_scope_reference(payload: dict) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert operation_skills.operation_matches("verification", payload["tool_name"], payload["tool_input"])


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
def test_verification_queries_do_not_require_check_execution(payload: dict) -> None:
    """既存の操作に対する検出条件を静的解析で確かめる。"""
    assert not operation_skills.operation_matches("verification", payload["tool_name"], payload["tool_input"])


@pytest.mark.parametrize("tool", _DELEGATION_TOOLS)
def test_delegation_ready_and_codex_acquisition(tmp_path: pathlib.Path, tool: str) -> None:
    """資料不足を警告し、取得を観測するまで準備済みとして記録しない。"""
    env = _plan_file_state_env(tmp_path)
    _record_skill(env, "ready", "delegation")
    payload = {"tool_name": tool, "tool_input": {"prompt": "調査する"}, "session_id": "ready"}
    assert "agent-toolkit:delegation" not in _additional_context(_run(payload, env))
    codex = {**payload, "session_id": "codex-delegation", "turn_id": "turn-1"}
    result = _run(codex, env)
    assert result.returncode == 0
    assert "/skills/delegation/SKILL.md" in _additional_context(result)
    assert result.stderr == ""
    state_file = session_state.state_path("codex-delegation")
    state = _read_session_state(tmp_path, "codex-delegation") if state_file.exists() else {}
    ready = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert "agent-toolkit:delegation" not in ready
    assert not any(key.startswith("read-trigger:") for key in ready)
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
    state_file = session_state.state_path("blocked")
    state = _read_session_state(tmp_path, "blocked") if state_file.exists() else {}
    assert rules_context.OPERATION_SKILL_READY_KEY not in state
    valid = {**blocked, "tool_name": _DELEGATION_TOOLS[5], "tool_input": {}}
    delivered = _run(valid, env)
    assert delivered.returncode == 0
    assert "/skills/delegation/SKILL.md" in _additional_context(delivered)
    assert delivered.stderr == ""


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
    """記録先1件だけへの編集を許し、混在と原稿への編集では基準取得を求める。"""
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
    result = _run(payload, env)
    assert ("/skills/writing-standards/SKILL.md" in _additional_context(result)) is (target != "own")
    assert result.returncode == 0
    assert result.stderr == ""
