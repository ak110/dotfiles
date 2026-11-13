"""スキル配下の資料を読む操作が、その資料の本文を返したかを判定する。

report-completionの起動証拠（`termination_evidence.py`）と、Antigravityの`read_file`をSkill観測へ渡す処理
（`antigravity.py`）が使う。パスの文字列だけを根拠にせず、読取の結果が資料の本文を返したことを確かめる。
Codexの起動証拠と読込案内は、同じ主体へ配送された本文・必要範囲・受信版から判定する。
分割受信は同じ版の行範囲として集約し、Antigravityの識別行による起動観測とは区別する。
"""

from __future__ import annotations

import dataclasses
import functools
import json
import pathlib
from collections.abc import Callable, Sequence

from agent_toolkit._git.document_acquisition import (
    READ_TRIGGER_PREFIX,
    RECEIPT_PREFIX,
    acquisition_state,
    bytes_fingerprint,
    content_fingerprint,
    content_ready,
    document_owner,
    document_plugin,
    logical_document,
    read_trigger_key,
    receipt_candidates,
    receipt_key,
)
from agent_toolkit._hooks.document_reads_fragments import (
    delivered_fragments,
    fragment_candidates,
    fragment_coverage,
    partial_fragment_deliveries,
    unambiguous_fragments,
)
from agent_toolkit._hooks.document_reads_receipts import (
    accumulate_fragments,
    receipt_metadata,
    received_candidates,
    restrict_receipts,
)
from agent_toolkit._hooks.document_reads_text import (
    returned_texts,
)
lazy from agent_toolkit._common import read_triggers
lazy from agent_toolkit._common.plugin_roots import (
    resolve_plugin_root,
    sibling_plugin_root,
)

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._hooks import plugin_resources

__all__ = [
    "READ_TRIGGER_PREFIX",
    "RECEIPT_PREFIX",
    "accumulate_fragments",
    "acquisition_state",
    "bytes_fingerprint",
    "content_fingerprint",
    "content_ready",
    "document_owner",
    "document_plugin",
    "logical_document",
    "read_trigger_key",
    "receipt_candidates",
    "receipt_key",
    "delivered_fragments",
    "fragment_candidates",
    "fragment_coverage",
    "partial_fragment_deliveries",
    "receipt_metadata",
    "received_candidates",
    "restrict_receipts",
    "returned_texts",
    "unambiguous_fragments",
]

_READ_PROGRAMS = frozenset({"cat", "sed", "head", "nl", "less", "more", "bat", "type", "get-content", "gc"})

"""Codexが資料を表示する読取コマンド。検索・抽出のコマンドは本文全体を返さないため含めない。

`pretooluse/large_reads.py`の`bash_read_paths`は全文取得の警告用で、`head`・`nl`・`bat`・
PowerShellの`Get-Content`を扱わない。report-completionの起動証拠はこれらの読取も受理するため、別に列挙する。
"""


def selected_target_key(target: read_triggers.ReadTarget, *, declarations: Sequence[dict] = ()) -> str:
    """復元と通常操作で同じ資料選択の識別子を使う。"""
    selected: dict[str, object] = {"path": str(target.path), "heading": target.heading}
    if declarations:
        selected["declarations"] = list(declarations)
    return "selected-read-target:" + json.dumps(selected, ensure_ascii=False, separators=(",", ":"))


@dataclasses.dataclass(frozen=True)
class DocumentRead:
    """スキル配下の資料を対象とする1件の読取。"""

    skill: str
    """`agent-toolkit:`の修飾を除いたスキルの短縮名。"""
    relative_path: str
    """スキルのディレクトリからの相対パス（`SKILL.md`か`references/<名前>.md`）。"""
    plugin: str = "agent-toolkit"
    """同名スキルを持つプラグインを区別する。"""
    path: str = dataclasses.field(default="", compare=False)
    """観測した実体。別ホストの導入情報で識別行を推定しないために保持する。"""


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


def skill_name(path: pathlib.Path, *, owner: Callable[[pathlib.Path], pathlib.Path | None] = document_owner) -> str | None:
    """実体のmanifestを使い、配布スキルとローカルスキルを区別する。

    `owner`には、呼出側が同じ実体の判定を共有する所有rootの解決関数を渡せる。
    """
    document = skill_document(str(path))
    if document is None or document[1] != "SKILL.md":
        return None
    if owner(path) is None:
        return document[0]
    return f"{document_plugin(str(path))}:{document[0]}"


def active_skill_root(plugin: str) -> pathlib.Path | None:
    """`Skill`起動でホストが読み込む配布物のrootを返す。解決できなければNoneを返す。"""
    return _sibling_skill_root(plugin, plugin_resources.plugin_root().resolve())


@functools.cache
def _sibling_skill_root(plugin: str, toolkit: pathlib.Path) -> pathlib.Path | None:
    """読込案内は資料ごとに同じrootを繰り返し解決するため、toolkitのrootと配布物名の組で結果を保持する。"""
    if plugin == "agent-toolkit":
        return toolkit
    sibling = sibling_plugin_root(plugin, toolkit)
    return sibling.resolve() if sibling is not None else None


def skill_loadable(
    path: pathlib.Path,
    *,
    cwd: str | pathlib.Path | None = None,
    owner: Callable[[pathlib.Path], pathlib.Path | None] = document_owner,
) -> bool:
    """`Skill`起動がこの実体を読み込むかを返す。

    同じ名前の配布スキルでも、作業ツリー内の写しは起動中の配布物とは別の実体であり、`Skill`起動では読み込まれない。
    写しにまで`Skill`起動を求めると取得済みに到達できないため、写しはReadの全文取得で扱う。
    起動中の配布物を解決できない場合は、従来どおり`Skill`起動で読み込む実体として扱う。
    読込案内、取得観測、圧縮後と取得失敗の案内が同じ判定を使うよう、どのhookからも依存できるこの層に置く。
    """
    name = skill_name(path, owner=owner)
    if name is None:
        return True
    if ":" not in name:
        # ローカル資料の実在だけでは、会話からSkillで起動できる根拠にならない。
        directories = [pathlib.Path.home() / ".claude/skills", *read_triggers.project_skill_dirs(pathlib.Path(cwd or "."))]
        selected = next(
            (directory / name / "SKILL.md" for directory in directories if (directory / name / "SKILL.md").is_file()), None
        )
        return selected is not None and selected.resolve() == path.resolve()
    active = active_skill_root(name.split(":", 1)[0])
    root = owner(path)
    return active is None or root is None or root == active


def requires_skill_invocation(
    path: pathlib.Path,
    *,
    host: str,
    subject: str,
    cwd: str | pathlib.Path | None = None,
    owner: Callable[[pathlib.Path], pathlib.Path | None] = document_owner,
) -> bool:
    """本文参照と別に、`Skill`起動の証拠を要求する資料かを返す。"""
    return skill_loadable(path, cwd=cwd, owner=owner) and read_triggers.skill_invocation_required(
        path, host=host, subject=subject
    )


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
        for argument in arguments:
            document = skill_document(argument)
            if document is not None:
                reads.append(DocumentRead(*document, plugin=document_plugin(argument), path=argument))
    return reads


def _identifying_line(
    skill: str,
    relative_path: str,
    plugin: str = "agent-toolkit",
    path: str = "",
    *,
    host: str = "claude",
    project: pathlib.Path | str | None = None,
) -> str | None:
    """資料の本文を返したことの判定に使う行。`SKILL.md`はfrontmatterの`name:`行、他は稼働版の先頭のH1の行とする。"""
    if relative_path == "SKILL.md" and not (path and pathlib.Path(path).name == "antigravity-full-instructions.md"):
        return f"name: {skill}"
    try:
        target = (
            pathlib.Path(path)
            if path
            else resolve_plugin_root(plugin, plugin_resources.plugin_root(), host, project) / "skills" / skill / relative_path
        )
        text = target.read_text(encoding="utf-8")
    except OSError, UnicodeError, ValueError:
        return None
    return next((line for line in text.splitlines() if line.startswith("# ")), None)


def body_returned(
    skill: str,
    relative_path: str,
    text: str | None,
    *,
    plugin: str = "agent-toolkit",
    path: str = "",
    host: str = "claude",
    project: pathlib.Path | str | None = None,
) -> bool:
    """読取の結果が資料の本文を返したかを返す。パスの文字列だけを根拠にしない。"""
    line = _identifying_line(skill, relative_path, plugin, path, host=host, project=project)
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


def skill_invocation_key(path: pathlib.Path, fingerprint: str) -> str:
    """資料参照の成功と区別して、同じ主体・本文のSkill起動を識別する。"""
    return "read-trigger-skill:" + str(path.resolve()) + "#sha256:" + fingerprint


def reference_only(operations: Sequence[Sequence[str]]) -> bool:
    """選択元がすべて名指し参照である場合だけ、Readによる取得を案内する。"""
    return bool(operations) and all(any(value.startswith("read:") for value in group) for group in operations)


def edited_document(tool_name: str, tool_input: dict, response: object, cwd: str) -> tuple[pathlib.Path, str] | None:
    """編集応答の前本文と入力の変更が、現在の対象本文に一致する場合だけ前hashを返す。

    Claude Code 2.1.316のEdit/Write出力型のoriginalFileは編集前全文かnullである。
    staged（未適用）とuserModified（ユーザー変更）は、自分の入力どおりの成功に含めない。
    """
    if tool_name not in {"Edit", "MultiEdit", "Write"} or not isinstance(response, dict):
        return None
    before = response.get("originalFile")
    requested = tool_input.get("file_path")
    reported = response.get("filePath")
    if (
        not isinstance(before, str)
        or not isinstance(requested, str)
        or not isinstance(reported, str)
        or response.get("staged")
        or response.get("userModified")
    ):
        return None
    path = pathlib.Path(requested).expanduser()
    path = (path if path.is_absolute() else pathlib.Path(cwd) / path).resolve()
    report = pathlib.Path(reported).expanduser()
    if (report if report.is_absolute() else pathlib.Path(cwd) / report).resolve() != path:
        return None
    if tool_name == "Write":
        after = tool_input.get("content")
        if not isinstance(after, str):
            return None
    else:
        edits = [tool_input] if tool_name == "Edit" else tool_input.get("edits")
        if not isinstance(edits, list) or not edits:
            return None
        after = before
        for edit in edits:
            if not isinstance(edit, dict):
                return None
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not old or not isinstance(new, str):
                return None
            if old not in after or not edit.get("replace_all") and after.count(old) != 1:
                return None
            after = after.replace(old, new, -1 if edit.get("replace_all") else 1)
    # 応答の後に外部変更があった場合も、その未知の本文へ継承しない。
    if path.read_bytes() != after.encode("utf-8"):
        return None
    return path, bytes_fingerprint(before.encode("utf-8"))
