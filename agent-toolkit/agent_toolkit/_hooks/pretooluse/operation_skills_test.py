"""共有する書込先の解析と検証操作の境界を確かめる。  pyfltr: allow-comment"""

from __future__ import annotations

import os
import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import operation_skills


def _bash(command: str, session_id: str, **extra: object) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session_id, **extra}


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
def test_discard_output_is_not_a_write(tmp_path: pathlib.Path, form: str) -> None:
    for path in (os.devnull, str(tmp_path / "memo.md")):
        data = {"command": form.format(path=path)}
        paths = operation_skills.written_paths("Bash", data, str(tmp_path))
        if path == os.devnull:
            assert not paths
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
    data = {"command": "tee AGENTS.md NUL <<<'本文'"}
    assert operation_skills.written_paths("Bash", data, str(tmp_path)) == [str(ordinary)]


@pytest.mark.skipif(os.name == "nt", reason="POSIXのfd別名を確認する")
@pytest.mark.parametrize("alias", ["/dev/stdout", "/dev/stderr", "/dev/fd/1"])
def test_standard_stream_alias_is_not_discarded_by_name(tmp_path: pathlib.Path, alias: str) -> None:
    data = {"command": f"cat > {alias} <<<'本文'"}
    assert operation_skills.written_paths("Bash", data, str(tmp_path))


@pytest.mark.parametrize("tool", ["Write", "Edit", "apply_patch"])
def test_edit_tools_exclude_discard_destination(tmp_path: pathlib.Path, tool: str) -> None:
    data = {
        "Write": {"file_path": os.devnull, "content": "本文"},
        "Edit": {"file_path": os.devnull, "old_string": "旧", "new_string": "新"},
        "apply_patch": {"command": f"*** Begin Patch\n*** Update File: {os.devnull}\n@@\n-旧\n+新\n*** End Patch"},
    }[tool]
    assert not operation_skills.written_paths(tool, data, str(tmp_path))


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
def test_perl_write_option_and_program_boundaries(tmp_path: pathlib.Path, options: str, writes: bool) -> None:
    command = f"perl {options} app.py"
    expected = [str(tmp_path / "app.py")] if writes else []
    assert operation_skills.written_paths("Bash", {"command": command}, str(tmp_path)) == expected
