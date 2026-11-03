"""スキル配下の資料を読む操作が、その資料の本文を返したかを判定する。

report-completionの起動証拠（`termination_evidence.py`）と、Antigravityの`read_file`をSkill観測へ渡す処理
（`antigravity.py`）が使う。パスの文字列だけを根拠にせず、読取の結果が資料の本文を返したことを確かめる。
Codexの起動証拠と読込案内は、同じ主体へ配送された本文・必要範囲・受信版から判定する。
分割受信は同じ版の行範囲として集約し、Antigravityの識別行による起動観測とは区別する。
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import hashlib
import json
import pathlib
import re
from collections.abc import Callable, Sequence

lazy from agent_toolkit._common import read_triggers
lazy from agent_toolkit._common.plugin_roots import (
    PluginRootError,
    codex_plugin_source_root,
    resolve_plugin_root,
    sibling_plugin_root,
)

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._git import command as git_command
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
        return any((directory / name / "SKILL.md").resolve() == path.resolve() for directory in directories)
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


READ_TRIGGER_PREFIX = "read-trigger:"
"""読込案内の取得記録のキーの接頭辞。キーは`read-trigger:<絶対パス>#sha256:<内容ハッシュ>`の形をとる。"""

RECEIPT_PREFIX = "received-document:"
"""本文を保存せず、論理資料・範囲・受信版を主体別に保持する。"""


@functools.cache
def _repository_identity(root: pathlib.Path) -> str:
    result = git_command.run(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return str(pathlib.Path(result.stdout.strip()).resolve()) if result.returncode == 0 else str(root)


def logical_document(path: pathlib.Path) -> str:
    """実在manifestとGit共通ディレクトリで裏付けられる配布元だけを統合する。"""
    path = path.resolve()
    owner = document_owner(path)
    repository = next((parent for parent in path.parents if (parent / ".git").exists()), None)
    if repository is None and owner is not None:
        source = _distribution_source(owner, document_plugin(str(path)))
        if source is not None:
            repository = next((parent for parent in (source, *source.parents) if (parent / ".git").exists()), source)
    if repository is not None:
        origin = _repository_identity(repository)
        relative = path.relative_to(owner if owner else repository).as_posix()
        return json.dumps([origin, document_plugin(str(path)) if owner else "project", relative], ensure_ascii=False)
    # 出所を確認できない配布cacheや同名ファイルを、名前だけで統合しない。
    return str(path)


@functools.cache
def _distribution_source(owner: pathlib.Path, name: str) -> pathlib.Path | None:
    try:
        return codex_plugin_source_root(owner, name)
    except PluginRootError:
        # 出所を取得できない実体は別資料として扱う。本文そのものの一致による再利用は保つ。
        return None


def receipt_key(metadata: dict) -> str:
    """受信した範囲の識別子を既存の主体別マッピングへ格納する。"""
    return RECEIPT_PREFIX + json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def receipt_candidates(
    targets: Sequence[read_triggers.ReadTarget],
    *,
    include_headings: bool = True,
    bodies: dict[pathlib.Path, str] | None = None,
    fragment_paths: set[pathlib.Path] | None = None,
) -> list[dict]:
    """既知資料の現在の範囲digestを作る。未知ファイル全体や本文の写しは保存しない。"""
    candidates = []
    for path in dict.fromkeys(target.path for target in targets):
        if not path.is_file():
            continue
        try:
            text = bodies[path] if bodies is not None and path in bodies else path.read_bytes().decode("utf-8")
        except OSError, UnicodeError:
            # 明示されたReadには画像なども含まれる。本文候補は読めたUTF-8資料に限る。
            continue
        lines = text.splitlines(keepends=True)
        logical = logical_document(path)
        version = bytes_fingerprint(text.encode("utf-8"))
        headings = [target.heading for target in targets if target.path == path]
        # 選択前の全文取得も、後で必要になる実在節へ再利用できるようにする。
        headings.extend(
            match[1]
            for _number, line, visible in read_triggers.document_lines(text)
            if include_headings and visible and (match := re.fullmatch(r"#{1,6} (.+?)[ \t]*", line))
        )
        for heading in dict.fromkeys(["", *headings]):
            target = read_triggers.ReadTarget(path, heading)
            try:
                ranges = target.line_ranges(text) if heading else ((1, len(lines)),)
                if path.name == "Makefile" and heading.startswith("make:"):
                    parts = [target.body(text).rstrip("\n")]
                else:
                    parts = ["".join(lines[start - 1 : end]).rstrip("\n") for start, end in ranges]
            except ValueError:
                continue
            if not parts or any(not part for part in parts):
                continue
            metadata = {
                "path": str(path),
                "logical": logical,
                "heading": heading,
                "version": version,
                "parts": [[bytes_fingerprint(part.encode("utf-8")), len(part), len(part.splitlines())] for part in parts],
                "ranges": [
                    [start, start + len(piece.splitlines()) - 1]
                    for start, end in ranges
                    if (piece := "".join(lines[start - 1 : end]).rstrip("\n"))
                ],
            }
            if not heading and fragment_paths is not None and path in fragment_paths:
                metadata["lines"] = _line_hashes(text)
            candidates.append(metadata)
    return candidates


def returned_texts(response: object) -> list[str]:
    """モデル向け出力の本文を取り出し、別ブロック間を連結して欠落を埋めない。"""
    if isinstance(response, list):
        return [text for part in response for text in returned_texts(part)]
    if isinstance(response, dict):
        if "type" in response and response["type"] not in {"text", "input_text", "output_text"}:
            return []
        if isinstance(response.get("file"), dict):
            return returned_texts(response["file"].get("content"))
        if "stdout" in response or "stderr" in response:
            return [text for key in ("stdout", "stderr") for text in returned_texts(response.get(key))]
        for key in ("stdout", "output", "text"):
            if key in response:
                return returned_texts(response[key])
        return []
    if not isinstance(response, str) or not response:
        return []
    try:
        decoded = json.loads(response)
    except ValueError:
        return [response]
    return [response, *returned_texts(decoded)] if isinstance(decoded, (dict, list)) else [response]


def _text_variants(texts: Sequence[str]) -> list[str]:
    decoded = list(texts)
    for text in texts:
        if "\\n" in text:
            decoded.append(re.sub(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})', lambda match: json.loads('"' + match[0] + '"'), text))
        # 外側で中間を省略されたJSONでも、残った各文字列断片の境界を保つ。
        for fragment in re.findall(r'"(?:[^"\\]|\\.)*"', text):
            with contextlib.suppress(ValueError):
                decoded.append(json.loads(fragment))
    valid = []
    for text in decoded:
        try:
            text.encode("utf-8")
        except UnicodeError:
            continue
        if text not in valid:
            valid.append(text)
    return valid


def _line_hashes(text: str) -> list[str]:
    return [bytes_fingerprint(line.rstrip("\n").encode("utf-8")) for line in text.splitlines(keepends=True)]


def receipt_metadata(candidate: dict) -> dict:
    """受信記録は範囲・版・digestに限定し、一致判定用の全行索引を取り除く。"""
    return {key: value for key, value in candidate.items() if key not in {"lines", "request"}}


def received_candidates(candidates: Sequence[dict], texts: Sequence[str]) -> list[dict]:
    """単一の実出力に完全に含まれる範囲を返す。分割分は同じ版の行範囲として別に集約する。"""
    windows: dict[tuple[int, int], set[str]] = {}
    indexed = []
    for text in _text_variants(texts):
        lines = text.splitlines(keepends=True)
        lengths = [0]
        trailing = [0]
        for line in lines:
            lengths.append(lengths[-1] + len(line))
            trailing.append(trailing[-1] + len(line) if not line.rstrip("\n") else len(line) - len(line.rstrip("\n")))
        indexed.append((lines, lengths, trailing))
    result = []
    complete = set()
    for candidate in candidates:
        source = (candidate["path"], candidate["version"])
        if source in complete:
            result.append(candidate)
            continue
        matches = True
        for digest, size, count in candidate["parts"]:
            key = (size, count)
            if key not in windows:
                values = set()
                for lines, lengths, trailing in indexed:
                    for start in range(len(lines) - count + 1):
                        end = start + count
                        length = lengths[end] - lengths[start]
                        if length - min(length, trailing[end]) == size:
                            value = "".join(lines[start:end]).rstrip("\n")
                            values.add(bytes_fingerprint(value.encode("utf-8")))
                windows[key] = values
            if digest not in windows[key]:
                matches = False
                break
        if matches:
            result.append(candidate)
            # 同じ版の全文が返れば、非連続なMakefile targetを含む既知範囲も全て含まれる。
            if not candidate["heading"]:
                complete.add(source)
    return result


def _find_lines(lines: list[str], expected: list[str], cursor: int) -> int:
    return next(
        (index for index in range(cursor, len(lines) - len(expected) + 1) if lines[index : index + len(expected)] == expected),
        -1,
    )


def fragment_candidates(
    candidates: Sequence[dict], texts: Sequence[str], requests: dict[str, list[tuple[int, int | None]]]
) -> list[dict]:
    """読取要求の位置に対応する本文が実出力へ返った範囲を保持する。位置は本文から推定しない。"""
    variants = [_line_hashes(text) for text in _text_variants(texts)]
    choices: dict[str, tuple[int, dict]] = {}
    for source in candidates:
        if not source.get("lines") or source["path"] not in requests:
            continue
        requested: list[dict] = []
        for start, end in requests[source["path"]]:
            if start < 0:
                start = max(1, len(source["lines"]) + start + 1)
            end = min(end if end is not None else len(source["lines"]), len(source["lines"]))
            if start <= end:
                content = source["lines"][start - 1 : end]
                if end == len(source["lines"]):
                    while content and content[-1] == bytes_fingerprint(b""):
                        content.pop()
                if content:
                    requested.append({"start": start, "lines": content})
        if not requested:
            continue
        best: list[dict] = []
        delivery = ""
        for lines in variants:
            cursor = 0
            for block in requested:
                index = _find_lines(lines, block["lines"], cursor)
                if index < 0:
                    break
                cursor = index + len(block["lines"])
            else:
                best = requested
                delivery = bytes_fingerprint("".join(lines).encode("ascii"))
                break
        score = sum(len(block["lines"]) for block in best)
        if not score:
            continue
        scopes = [
            receipt_metadata(item)
            for item in candidates
            if item["path"] == source["path"] and item["version"] == source["version"]
        ]
        packet = {"source": source, "scopes": scopes, "blocks": best, "delivery": delivery}
        # Preの版を先に渡す。Postまでに実体が変わった場合も、同点なら受信前の版を保つ。
        if source["path"] not in choices or score > choices[source["path"]][0]:
            choices[source["path"]] = (score, packet)
    return [packet for _score, packet in choices.values()]


def delivered_fragments(packet: dict, texts: Sequence[str]) -> tuple[int, list[dict]]:
    """生産した応答全体の配送回数を数え、別の応答の一部分を流用しない。"""
    blocks = packet["blocks"]
    if any(not isinstance(block, dict) for block in blocks):
        return 0, []

    def matches_delivery(text: str) -> bool:
        try:
            return bytes_fingerprint("".join(_line_hashes(text)).encode("ascii")) == packet.get("delivery")
        except UnicodeError:
            return False

    count = 0
    for text in texts:
        fields = []
        with contextlib.suppress(ValueError):
            decoded = json.loads(text)
            if isinstance(decoded, str):
                fields.append(decoded)
        # 外側のtext(result)に含まれる本文フィールドを、同文の重複も保って数える。
        for match in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:\s*("(?:[^"\\]|\\.)*")', text):
            with contextlib.suppress(ValueError):
                if json.loads('"' + match[1] + '"') in {"stdout", "stderr", "output", "text", "content"}:
                    fields.append(json.loads(match[2]))
        count += max(int(matches_delivery(text)), sum(matches_delivery(field) for field in fields))
    return count, blocks if count else []


def fragment_coverage(packets: Sequence[dict]) -> dict[str, list[list[int]]]:
    """本文が一致した読取要求の位置を、同じ全文内容の写しにも適用する。"""
    result: dict[str, list[list[int]]] = {}
    for packet in packets:
        ranges = result.setdefault(packet["source"]["version"], [])
        ranges.extend([block["start"], block["start"] + len(block["lines"]) - 1] for block in packet["blocks"])
    return {version: _merge_ranges(ranges) for version, ranges in result.items()}


def unambiguous_fragments(packet: dict, pending: Sequence[dict], texts: Sequence[str]) -> list[dict]:
    """省略後も完全な要求範囲があり、他の生産位置と競合しない断片を返す。"""
    visible = [_line_hashes(text) for text in _text_variants(texts)]
    result = []
    for block in packet["blocks"]:
        positions = set()
        for other in pending:
            for produced in other["blocks"]:
                if not isinstance(produced, dict):
                    continue
                cursor = 0
                while (index := _find_lines(produced["lines"], block["lines"], cursor)) >= 0:
                    positions.add((other["source"]["logical"], other["source"]["version"], produced["start"] + index))
                    cursor = index + 1
        expected = (packet["source"]["logical"], packet["source"]["version"], block["start"])
        if positions == {expected} and any(_find_lines(lines, block["lines"], 0) >= 0 for lines in visible):
            result.append(block)
    return result


def restrict_receipts(candidates: Sequence[dict], coverage: dict[str, list[list[int]]]) -> list[dict]:
    """同文の部分反復が全文に一致しても、既知の要求位置より広い取得へ昇格させない。"""
    return [
        item for item in candidates if item["version"] not in coverage or _covers(coverage[item["version"]], item["ranges"])
    ]


def _merge_ranges(ranges: Sequence[Sequence[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges, key=lambda span: (span[0], span[1])):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _covers(coverage: Sequence[Sequence[int]], required: Sequence[Sequence[int]]) -> bool:
    return all(any(low <= start and end <= high for low, high in coverage) for start, end in required)


def accumulate_fragments(records: dict, packet: dict, blocks: Sequence[dict], agent: str) -> list[dict]:
    """同じ主体・論理資料・版の実受信範囲だけを集約し、完成した既知範囲を返す。"""
    source = packet["source"]
    coverage = []
    previous = []
    for key, actors in list(records.items()):
        if not key.startswith(RECEIPT_PREFIX) or agent not in actors:
            continue
        item = json.loads(key.removeprefix(RECEIPT_PREFIX))
        if item.get("partial") and item["logical"] == source["logical"] and item["version"] == source["version"]:
            coverage.extend(item["coverage"])
            previous.append(key)
    coverage = _merge_ranges(coverage)
    for block in blocks:
        start = block["start"]
        end = start + len(block["lines"]) - 1
        if start >= 1 and block["lines"] == source["lines"][start - 1 : end]:
            coverage = _merge_ranges([*coverage, [start, end]])
    for key in previous:
        records[key] = [actor for actor in records[key] if actor != agent]
        if not records[key]:
            del records[key]
    metadata = receipt_metadata(source)
    metadata.update(partial=True, coverage=coverage)
    if not _covers(coverage, source["ranges"]):
        records.setdefault(receipt_key(metadata), []).append(agent)
    return [metadata for metadata in packet["scopes"] if _covers(coverage, metadata.get("ranges", [[1, len(source["lines"])]]))]


def acquisition_state(records: dict, target: read_triggers.ReadTarget, agent: str) -> str:
    """現在範囲の取得・旧版取得後の更新・未取得を共通の意味で返す。"""
    raw = target.path.read_bytes()
    if target.heading:
        try:
            target.body(raw.decode("utf-8"))
        except ValueError:
            return "missing"
    if content_ready(records, bytes_fingerprint(raw), agent):
        return "current"
    current = next(
        (item for item in receipt_candidates([target], include_headings=False) if item["heading"] == target.heading), None
    )
    if current is None:
        return "missing"
    receipts = [
        json.loads(key.removeprefix(RECEIPT_PREFIX))
        for key, actors in records.items()
        if key.startswith(RECEIPT_PREFIX) and isinstance(actors, list) and agent in actors
    ]
    receipts = [item for item in receipts if not item.get("partial")]
    known = {tuple(part) for item in receipts for part in item["parts"]}
    if all(tuple(part) in known for part in current["parts"]):
        return "current"
    if any(item["logical"] == current["logical"] and item["heading"] == current["heading"] for item in receipts):
        return "updated"
    # 旧形式の証拠は保持する。新版の受信記録へ変換せず、全文の更新として区別する。
    if not target.heading and any(
        key.startswith(READ_TRIGGER_PREFIX + str(target.path) + "#sha256:") and agent in actors
        for key, actors in records.items()
        if isinstance(actors, list)
    ):
        return "updated"
    return "missing"


def content_fingerprint(path: pathlib.Path) -> str:
    """資料の現在版を表す内容ハッシュを返す。"""
    return bytes_fingerprint(path.read_bytes())


def bytes_fingerprint(raw: bytes) -> str:
    """読み込み済みの本文から、`content_fingerprint`と同じ内容ハッシュを返す。"""
    return hashlib.sha256(raw).hexdigest()


def read_trigger_key(path: pathlib.Path, fingerprint: str) -> str:
    """取得を観測した資料の実体パスと内容識別子から記録キーを返す。"""
    return READ_TRIGGER_PREFIX + str(path.resolve()) + "#sha256:" + fingerprint


def skill_invocation_key(path: pathlib.Path, fingerprint: str) -> str:
    """資料参照の成功と区別して、同じ主体・本文のSkill起動を識別する。"""
    return "read-trigger-skill:" + str(path.resolve()) + "#sha256:" + fingerprint


def reference_only(operations: Sequence[Sequence[str]]) -> bool:
    """選択元がすべて名指し参照である場合だけ、Readによる取得を案内する。"""
    return bool(operations) and all(any(value.startswith(("read:", "section:")) for value in group) for group in operations)


def content_ready(records: dict, fingerprint: str, agent: str) -> bool:
    """同じ主体の全文受信版を、記録時のパスを問わずに照会する。旧形式のキーも保持する。"""
    suffix = "#sha256:" + fingerprint
    return any(
        key.startswith(READ_TRIGGER_PREFIX) and key.endswith(suffix) and isinstance(actors, list) and agent in actors
        for key, actors in records.items()
    )


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
