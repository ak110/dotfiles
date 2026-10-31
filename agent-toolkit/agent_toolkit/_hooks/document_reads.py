"""スキル配下の資料を読む操作が、その資料の本文を返したかを判定する。

completion-reportの起動証拠（`termination_evidence.py`）と、Antigravityの`read_file`をSkill観測へ渡す処理
（`antigravity.py`）が使う。パスの文字列だけを根拠にせず、読取の結果が資料の本文を返したことを確かめる。
起動証拠は範囲指定の読取も受理するため、本モジュールは全文か部分かを区別しない。
資料の全文取得の判定は`pretooluse/read_delivery.py`が内容ハッシュと本文一致で行う。
"""

from __future__ import annotations

import dataclasses
import hashlib
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

# 読込案内の取得状態は主体と資料の内容ハッシュの組で管理し、記録時のパスを条件に含めない
# （`share/read-trigger-contract.md`「取得とチェック」）。主checkoutと配下のworktreeのように同じ内容の写しが
# 別の場所にあっても、どちらを読んだかで判定を変えない。資料の同一性（内容ハッシュ）、全文取得の照会、
# 分割取得の区間の合算と被覆の判定は以下の関数だけが持ち、案内要否、分割読取の集計、履歴の復元、
# 会話圧縮時の候補抽出の4箇所はこれらを使う。


def content_fingerprint(path: pathlib.Path) -> str:
    """資料の同一性を表す内容ハッシュを返す。"""
    return bytes_fingerprint(path.read_bytes())


def bytes_fingerprint(raw: bytes) -> str:
    """読み込み済みの本文から、`content_fingerprint`と同じ内容ハッシュを返す。"""
    return hashlib.sha256(raw).hexdigest()


def read_trigger_key(path: pathlib.Path, fingerprint: str) -> str:
    """取得を観測した資料の記録キーを返す。パスは圧縮後の候補案内で取得手段を示すために残す。"""
    return READ_TRIGGER_PREFIX + str(path.resolve()) + "#sha256:" + fingerprint


def read_trigger_range_key(path: pathlib.Path, fingerprint: str, start: int, end: int) -> str:
    """分割取得した区間の記録キーを返す。区間は本文全体の文字位置で表す。"""
    return f"{read_trigger_key(path, fingerprint)}#range:{start}:{end}"


def content_ready(records: dict, fingerprint: str, agent: str) -> bool:
    """同じ主体が同じ内容ハッシュの資料を全文取得した記録があるかを、記録時のパスを問わずに返す。"""
    suffix = "#sha256:" + fingerprint
    return any(
        key.startswith(READ_TRIGGER_PREFIX) and key.endswith(suffix) and isinstance(actors, list) and agent in actors
        for key, actors in records.items()
    )


def recorded_ranges(records: dict, fingerprint: str, agent: str) -> list[tuple[int, int]]:
    """同じ主体が同じ内容ハッシュの資料を分割取得した区間を、記録時のパスを問わずに返す。"""
    marker = f"#sha256:{fingerprint}#range:"
    ranges = []
    for key, actors in records.items():
        if key.startswith(READ_TRIGGER_PREFIX) and marker in key and isinstance(actors, list) and agent in actors:
            left, _separator, right = key.rpartition(marker)[2].partition(":")
            ranges.append((int(left), int(right)))
    return ranges


def covers_whole(intervals, length: int) -> bool:
    """区間の和が本文の先頭から末尾まで途切れずに覆うかを返す。"""
    reached = 0
    for left, right in sorted(intervals):
        if left > reached:
            break
        reached = max(reached, right)
    return reached >= length
