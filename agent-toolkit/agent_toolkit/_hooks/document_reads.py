"""スキル配下の資料を読む操作が、その資料の本文を返したかを判定する。

completion-reportの起動証拠（`termination_evidence.py`）と、操作を起動の契機とするスキルの取得の記録
（`posttooluse.py`）が同じ判定を使う。パスの文字列だけを根拠にせず、読取の結果が資料の本文を返したことを確かめる。
先頭や範囲だけを返し得る読取は`partial`として区別し、受理するかは呼び出し側が決める。
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

lazy from agent_toolkit._common.plugin_roots import resolve_plugin_root

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._hooks import plugin_resources

_READ_PROGRAMS = frozenset({"cat", "sed", "head", "nl", "less", "more", "bat", "type", "get-content", "gc"})
"""Codexが資料を表示する読取コマンド。検索・抽出のコマンドは本文全体を返さないため含めない。

`pretooluse/large_reads.py`の`bash_read_paths`は全文取得の警告用で、`head`・`nl`・`bat`・
PowerShellの`Get-Content`を扱わない。completion-reportの起動証拠はこれらの読取も受理するため、別に列挙する。
"""

_SED_FULL_SCRIPTS = frozenset({"p", "1,$p"})
"""`sed -n`のうち全行を表示するスクリプト。他のスクリプトは範囲だけを返し得る。"""


@dataclasses.dataclass(frozen=True)
class DocumentRead:
    """スキル配下の資料を対象とする1件の読取。"""

    skill: str
    """`agent-toolkit:`の修飾を除いたスキルの短縮名。"""
    relative_path: str
    """スキルのディレクトリからの相対パス（`SKILL.md`か`references/<名前>.md`）。"""
    partial: bool
    """先頭や範囲だけを返し得る読取であるか。"""
    plugin: str = "agent-toolkit"
    """同名スキルを持つプラグインを区別する。"""
    path: str = dataclasses.field(default="", compare=False)
    """観測した実体。別ホストの導入情報で識別行を推定しないために保持する。"""


def document_plugin(argument: str) -> str:
    """実在するmanifestから資料の所有プラグインを識別する。"""
    for parent in pathlib.Path(argument).expanduser().resolve().parents:
        manifest = parent / ".claude-plugin/plugin.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return data.get("name", "agent-toolkit")
    return "agent-toolkit"


def skill_document(argument: str) -> tuple[str, str] | None:
    """パスの引数がスキルの`SKILL.md`か`references/`直下の資料を指す場合に、短縮名と相対パスを返す。"""
    parts = pathlib.PurePosixPath(argument.replace("\\", "/")).parts
    if (
        len(parts) >= 4
        and parts[-1] == "antigravity-full-instructions.md"
        and parts[-2] == "references"
        and any((parent / "plugin.json").is_file() for parent in pathlib.Path(argument).parents)
    ):
        return parts[-3], "SKILL.md"
    if len(parts) >= 3 and parts[-1] == "SKILL.md" and parts[-3] == "skills":
        return parts[-2], "SKILL.md"
    if len(parts) >= 4 and parts[-2] == "references" and parts[-4] == "skills" and parts[-1].endswith(".md"):
        return parts[-3], f"references/{parts[-1]}"
    return None


def bash_document_reads(command: str) -> list[DocumentRead]:
    """Bashのコマンドが実行する、スキル配下の資料の読取を返す。ヘルプ表示と検索・抽出のコマンドは含めない。"""
    reads: list[DocumentRead] = []
    for segment in extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        tokens = list(segment.tokens)
        if "--help" in tokens or "-h" in tokens:
            continue
        program = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name.lower().removesuffix(".exe")
        if program not in _READ_PROGRAMS:
            continue
        arguments = tokens[1:]
        partial = program == "head" or program == "sed" and not _SED_FULL_SCRIPTS.intersection(arguments)
        for argument in arguments:
            document = skill_document(argument)
            if document is not None:
                reads.append(DocumentRead(*document, partial=partial, plugin=document_plugin(argument), path=argument))
    return reads


# Claude CodeのReadの`tool_response`は`{"type": "text", "file": {"filePath", "content", "numLines", "startLine",
# "totalLines", "truncatedByTokenCap"?}}`、本文を文脈に保持したまま未変更の再読込では`{"type": "file_unchanged"}`を返す。
# Claude Code 2.1.303の本体（`~/.local/share/claude/versions/2.1.303`）を`rg -a 'type:"text",file:\{filePath:'`と
# `rg -a 'type:"file_unchanged"'`で検索して確かめた。
def read_tool_document(tool_input: dict, tool_response: object) -> DocumentRead | None:
    """Claude CodeのReadの結果が、スキル配下の資料の本文を返した場合にその読取を返す。"""
    path = tool_input.get("file_path")
    if not isinstance(path, str):
        return None
    document = skill_document(path)
    if document is None or not isinstance(tool_response, dict):
        return None
    ranged = tool_input.get("offset") is not None or tool_input.get("limit") is not None
    if tool_response.get("type") == "file_unchanged":
        return DocumentRead(*document, partial=ranged, plugin=document_plugin(path), path=path)
    file = tool_response.get("file")
    if tool_response.get("type") != "text" or not isinstance(file, dict):
        return None
    content = file.get("content")
    plugin = document_plugin(path)
    if not isinstance(content, str) or not body_returned(*document, content, plugin=plugin, path=path):
        return None
    partial = (
        file.get("startLine") != 1 or file.get("numLines") != file.get("totalLines") or bool(file.get("truncatedByTokenCap"))
    )
    return DocumentRead(*document, partial=partial, plugin=plugin, path=path)


def _identifying_line(skill: str, relative_path: str, plugin: str = "agent-toolkit", path: str = "") -> str | None:
    """資料の本文を返したことの判定に使う行。`SKILL.md`はfrontmatterの`name:`行、他は稼働版の先頭のH1の行とする。"""
    if relative_path == "SKILL.md" and not (path and pathlib.Path(path).name == "antigravity-full-instructions.md"):
        return f"name: {skill}"
    try:
        target = (
            pathlib.Path(path)
            if path
            else resolve_plugin_root(plugin, plugin_resources.plugin_root()) / "skills" / skill / relative_path
        )
        text = target.read_text(encoding="utf-8")
    except OSError, UnicodeError, ValueError:
        return None
    return next((line for line in text.splitlines() if line.startswith("# ")), None)


def body_returned(skill: str, relative_path: str, text: str | None, *, plugin: str = "agent-toolkit", path: str = "") -> bool:
    """読取の結果が資料の本文を返したかを返す。パスの文字列だけを根拠にしない。"""
    line = _identifying_line(skill, relative_path, plugin, path)
    return text is not None and line is not None and line in text


# 両ホストのPostToolUseの`tool_response`の形（CodexのBashでは終了コードを含まない出力文字列）と、
# Stopの出力契約を公式Hooks仕様で確かめた監査記録は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_hooks/termination_evidence.py：終了工程の証拠のStop判定：2026年10月3日」にある。
def response_text(response: object) -> str | None:
    """PostToolUseの`tool_response`から読取結果の本文を取り出す。"""
    if isinstance(response, str):
        try:
            decoded = json.loads(response)
        except json.JSONDecodeError, ValueError:
            return response
        if isinstance(decoded, dict):
            return response_text(decoded) or response
        return response
    if isinstance(response, dict):
        for key in ("stdout", "output", "text"):
            value = response.get(key)
            if isinstance(value, str):
                return value
    return None
