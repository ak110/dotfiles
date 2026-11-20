"""ツール呼び出しが、ファイルの現在の全文をモデルへ返したかを判定する。

Claude Codeの`Read`は、応答の`file.content`が現在の全文と一致したものを数える。`offset`・`limit`の値は信じず、
範囲を限った読取、トークン上限で本文を省略した読取（`truncatedByTokenCap`）、旧版の読取を数えない。
シェルの読取は、出力をリダイレクトしない本文表示と、1行目から始まる`sed -n`の行範囲の表示を候補にし、
結果が現在の全文を含むものを数える。Codexの結果は終了コードを含まないため、全文を含むことを読取の成功とする。
途中で終わる範囲の読取、省略された結果、失敗した読取は全文を含まないため数えない。
"""

from __future__ import annotations

import json
import pathlib
lazy from collections.abc import Callable, Iterator, Mapping

lazy from agent_toolkit._common import shell_cwd
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS
lazy from agent_toolkit._hooks.pretooluse.large_reads import invocation_read_paths, sed_read_range


def response_text(response: object) -> str:
    """シェルの`tool_response`から、ホストがモデルへ返した本文を取り出す。"""
    if isinstance(response, dict):
        response = next((response[key] for key in ("stdout", "output", "text") if isinstance(response.get(key), str)), "")
    return response if isinstance(response, str) else ""


def _current_text(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return None


def read_tool_full_read(
    tool_input: Mapping[str, object], response: object, wanted: Callable[[pathlib.Path], bool]
) -> pathlib.Path | None:
    """Claude Codeの`Read`が現在の全文を返した、`wanted`が真のファイルを返す。"""
    value = tool_input.get("file_path")
    file = response.get("file") if isinstance(response, dict) else None
    if not isinstance(value, str) or not value or not isinstance(file, dict):
        return None
    path = pathlib.Path(value)
    content = file.get("content")
    if not wanted(path) or not isinstance(content, str) or file.get("truncatedByTokenCap"):
        return None
    current = _current_text(path)
    if current is None or not current.strip() or content.rstrip("\n") != current.rstrip("\n"):
        return None
    return path


def shell_full_reads(command: str, cwd: str, text: str, wanted: Callable[[pathlib.Path], bool]) -> Iterator[pathlib.Path]:
    """シェルのコマンドが全文を表示し、結果`text`が現在の全文を含む、`wanted`が真のファイルを返す。"""
    if not text:
        return
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd, effects=True):
        yield from invocation_full_reads(invocation, current, text, wanted)


def invocation_full_reads(
    invocation: shell_cwd.BashInvocation, cwd: shell_cwd.CwdResolution, text: str, wanted: Callable[[pathlib.Path], bool]
) -> Iterator[pathlib.Path]:
    """現在の全文を出力した読取を、その呼出しの行範囲と出力先を保って判定する。"""
    for path in invocation_read_paths(invocation, cwd, include_partial=True, first_line_only=True):
        if not wanted(path):
            continue
        body = _current_text(path) or ""
        if not (content := body.strip()) or content not in text:
            continue
        line_range = sed_read_range(invocation.variable_tokens)
        if line_range and line_range[2] is not None and len(body.removesuffix("\n").split("\n")) > line_range[2]:
            continue
        yield path


def full_reads(
    tool_name: str, tool_input: Mapping[str, object], response: object, cwd: str, wanted: Callable[[pathlib.Path], bool]
) -> list[pathlib.Path]:
    """`Read`とシェルの呼び出しが現在の全文を返した、`wanted`が真のファイルを返す。"""
    if tool_name == "Read":
        path = read_tool_full_read(tool_input, response, wanted)
        return [path] if path is not None else []
    command = tool_input.get("command")
    if tool_name not in ATK_SHELL_TOOLS or not isinstance(command, str):
        return []
    return list(shell_full_reads(command, cwd, response_text(response), wanted))


def is_skill_entry_name(path: pathlib.Path) -> bool:
    """ファイル名がスキルの入口の名前か（所有pluginを確かめる前の判定）を返す。"""
    return path.name in {"SKILL.md", "antigravity-full-instructions.md"}


def skill_entry(path: pathlib.Path) -> tuple[str, str] | None:
    """ファイルがスキルの入口なら、所有pluginとスキルの名前を返す。

    入口は`SKILL.md`とAntigravity向けの全文`references/antigravity-full-instructions.md`とする。
    所有pluginは実在するmanifestの`name`から求める。
    """
    parts = path.parts
    if len(parts) >= 3 and parts[-1] == "SKILL.md" and parts[-3] == "skills":
        skill, root = parts[-2], path.parents[2]
    elif len(parts) >= 4 and parts[-2:] == ("references", "antigravity-full-instructions.md") and parts[-4] == "skills":
        skill, root = parts[-3], path.parents[3]
    else:
        return None
    for manifest in (root / ".claude-plugin/plugin.json", root / ".codex-plugin/plugin.json", root / "plugin.json"):
        try:
            name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
        except OSError, ValueError, AttributeError:
            continue
        if isinstance(name, str) and name:
            return name, skill
    return None
