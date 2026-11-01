"""スキル配下の資料を読む操作が、その資料の本文を返したかを判定する。

report-completionの起動証拠（`termination_evidence.py`）と、Antigravityの`read_file`をSkill観測へ渡す処理
（`antigravity.py`）が使う。パスの文字列だけを根拠にせず、読取の結果が資料の本文を返したことを確かめる。
起動証拠は範囲指定の読取も受理するため、本モジュールは全文か部分かを区別しない。
読込案内の取得状態は`pretooluse/read_delivery.py`が読取操作の成功から記録する。
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import pathlib
from collections.abc import Callable

lazy from agent_toolkit._common import read_triggers
lazy from agent_toolkit._common.plugin_roots import resolve_plugin_root, sibling_plugin_root

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._hooks import plugin_resources

_READ_PROGRAMS = frozenset({"cat", "sed", "head", "nl", "less", "more", "bat", "type", "get-content", "gc"})
"""Codexが資料を表示する読取コマンド。検索・抽出のコマンドは本文全体を返さないため含めない。

`pretooluse/large_reads.py`の`bash_read_paths`は全文取得の警告用で、`head`・`nl`・`bat`・
PowerShellの`Get-Content`を扱わない。report-completionの起動証拠はこれらの読取も受理するため、別に列挙する。
"""


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


def document_owner(path: pathlib.Path) -> pathlib.Path | None:
    """選択された実体のmanifestを持つ所有rootを返す。別版の探索はしない。"""
    return next((parent for parent in path.resolve().parents if (parent / ".claude-plugin/plugin.json").is_file()), None)


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


def skill_loadable(path: pathlib.Path, *, owner: Callable[[pathlib.Path], pathlib.Path | None] = document_owner) -> bool:
    """`Skill`起動がこの実体を読み込むかを返す。

    同じ名前の配布スキルでも、作業ツリー内の写しは起動中の配布物とは別の実体であり、`Skill`起動では読み込まれない。
    写しにまで`Skill`起動を求めると取得済みに到達できないため、写しはReadの全文取得で扱う。
    起動中の配布物を解決できない場合は、従来どおり`Skill`起動で読み込む実体として扱う。
    読込案内、取得観測、圧縮後と取得失敗の案内が同じ判定を使うよう、どのhookからも依存できるこの層に置く。
    """
    name = skill_name(path, owner=owner)
    if name is None or ":" not in name:
        return True
    active = active_skill_root(name.split(":", 1)[0])
    root = owner(path)
    return active is None or root is None or root == active


def requires_skill_invocation(
    path: pathlib.Path,
    *,
    host: str,
    subject: str,
    owner: Callable[[pathlib.Path], pathlib.Path | None] = document_owner,
) -> bool:
    """`Skill`起動だけを全文取得として認める資料かを返す。"""
    return skill_loadable(path, owner=owner) and read_triggers.skill_invocation_required(path, host=host, subject=subject)


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


READ_TRIGGER_PREFIX = "read-trigger:"
"""読込案内の取得記録のキーの接頭辞。キーは`read-trigger:<絶対パス>#sha256:<内容ハッシュ>`の形をとる。"""

# 読込状態は主体と本文の変更を区別する。区間の被覆や応答本文の一致は判定しない。


def content_fingerprint(path: pathlib.Path) -> str:
    """資料の同一性を表す内容ハッシュを返す。"""
    return bytes_fingerprint(path.read_bytes())


def bytes_fingerprint(raw: bytes) -> str:
    """読み込み済みの本文から、`content_fingerprint`と同じ内容ハッシュを返す。"""
    return hashlib.sha256(raw).hexdigest()


def read_trigger_key(path: pathlib.Path, fingerprint: str) -> str:
    """取得を観測した資料の実体パスと内容識別子から記録キーを返す。"""
    return READ_TRIGGER_PREFIX + str(path.resolve()) + "#sha256:" + fingerprint


def content_ready(records: dict, fingerprint: str, agent: str) -> bool:
    """同じ主体が同じ内容ハッシュの資料への読取が成功した記録があるかを、記録時のパスを問わずに返す。"""
    suffix = "#sha256:" + fingerprint
    return any(
        key.startswith(READ_TRIGGER_PREFIX) and key.endswith(suffix) and isinstance(actors, list) and agent in actors
        for key, actors in records.items()
    )
