"""宣言された操作の前に、主体の文脈に未取得の資料を届ける。

確認は関係する資料のパスを警告にまとめ、通常の操作を継続する。
配送できた資料だけを取得へ記録する。
Codexの配送本文の到達上限は未確認のため、資料から算出した行範囲の`sed -n`による取得を案内する。
取得後は出力と資料の該当行の一致を確かめ、分割した取得を区間の被覆として記録する。
軽量委譲は起動契約がスキルを使わないため対象外とする。
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shlex
from collections.abc import Iterator
lazy import subprocess

from agent_toolkit._common import delegated_session, read_triggers, shell_cwd, transcript
from agent_toolkit._common.plugin_roots import PluginRootError, sibling_plugin_root
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import is_atk_command
from agent_toolkit._hooks import agent_id, document_reads, plugin_resources, rules_context
from agent_toolkit._hooks import tool_input as tool_input_fields
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths, sed_read_range
from agent_toolkit._hooks.pretooluse.notices import _llm_notice
lazy from agent_toolkit._common import process_tree, task_documents
lazy from agent_toolkit._hooks.pretooluse import shell_checks

# WI原本のClaude Code 2.1.303の観測44,346文字より小さくし、標識とパス一覧の余地を取る。
# 統合時に現在ホストで本文の到達を確認する。Codexには取得先のパスを案内する。
CLAUDE_BODY_BUDGET = 40_000
_PATH_PREFIX = "read-trigger:"


def declarations(cwd: str, *, host: str = "claude") -> list[read_triggers.ReadTrigger]:
    """稼働版と対象プロジェクト・ユーザースキルの宣言を読む。"""
    root = plugin_resources.plugin_root()
    sources = read_triggers.document_sources(root, cwd)
    # 外部分離された品質スキルは、導入された実体がある場合に同じ表形式で取り込む。
    sibling = sibling_plugin_root("agent-basics", root)
    if sibling is not None:
        sources.extend(sorted((sibling / "skills").rglob("*.md")))
    entries = [(source.resolve(), read_triggers.parse_tables(source, root, host=host)) for source in dict.fromkeys(sources)]
    # 参照先の所有plugin全体は有効化しない。選択した実体の宣言は取得時に追加する。
    included = {row.source for source, rows in entries for row in rows if row.source != source}
    return list(dict.fromkeys(row for source, rows in entries if source not in included for row in rows))


def _skill(source: pathlib.Path) -> str | None:
    for parent in [source.parent, *source.parents]:
        if parent.parent.name == "skills":
            return parent.name
    return None


def _read_paths(tool_name: str, tool_input: dict, cwd: str, *, resolve: bool = True) -> list[pathlib.Path]:
    if tool_name == "Read" and isinstance(tool_input.get("file_path"), str):
        path = pathlib.Path(tool_input["file_path"]).expanduser()
        path = path if path.is_absolute() else pathlib.Path(cwd) / path
        return [path.resolve() if resolve else path]
    command = tool_input.get("command")
    if tool_name == "Bash" and isinstance(command, str):
        return [
            path.resolve() if resolve else path
            for group in bash_read_paths(command, cwd, include_partial=True)
            for path in group
        ]
    return []


def _read_path_variants(tool_name: str, tool_input: dict, cwd: str) -> list[pathlib.Path]:
    """資料の取得を始める読取のパスを、リンク経由の名前でも宣言へ一致させるため解決前後の両方で返す。

    行範囲の読取は1行目から始まるものだけを含める。途中の節や続きの行範囲は、section宣言と取得後の被覆記録が扱う。
    """
    command = tool_input.get("command")
    if tool_name == "Bash" and isinstance(command, str):
        groups = list(bash_read_paths(command, cwd, include_partial=True, first_line_only=True))
        return [path.resolve() if resolve else path for resolve in (True, False) for group in groups for path in group]
    return [path for resolve in (True, False) for path in _read_paths(tool_name, tool_input, cwd, resolve=resolve)]


def _sed_reads(tool_name: str, tool_input: dict, cwd: str) -> tuple[list[tuple[pathlib.Path, int, int | None]], bool]:
    """Bashの静的な`sed -n '<開始行>,<終了行>p'`を出力順に返す。

    2番目の値は、移動以外の全ての呼出しが行範囲の読取であり、出力全体を範囲の連結と比べられるかを示す。
    """
    command = tool_input.get("command")
    if tool_name != "Bash" or not isinstance(command, str):
        return [], False
    reads: list[tuple[pathlib.Path, int, int | None]] = []
    only_reads = True
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        tokens = invocation.segment.tokens
        if invocation.arguments_known and tokens and pathlib.PurePath(tokens[0]).name == "cd":
            continue
        sed = sed_read_range(tokens) if invocation.arguments_known else None
        if (
            sed is None
            or any(marker in sed[0] for marker in ("$", "`", "<", ">", "*", "?"))
            or invocation.output_pipe
            or invocation.output_redirected
            or invocation.captured
            or not (pathlib.Path(sed[0]).expanduser().is_absolute() or current.resolved)
        ):
            only_reads = False
            continue
        operand, start, end = sed
        path = pathlib.Path(operand).expanduser()
        reads.append(((path if path.is_absolute() else pathlib.Path(current.path) / path).resolve(), start, end))
    return reads, only_reads and bool(reads)


def _line_spans(target: read_triggers.ReadTarget, source: str) -> list[tuple[int, int]]:
    """指定範囲が占める資料の行を、1始まりで両端を含む連続区間の列として返す。

    指定範囲は資料の行を順に選んだ部分列であるため、先頭から順に行を比べて位置を求める。
    """
    lines = source.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    cursor = 0
    for line in target.body(source).splitlines(keepends=True):
        while cursor < len(lines) and lines[cursor] != line:
            cursor += 1
        if cursor == len(lines):
            raise ValueError(f"{target}: 指定範囲の行を資料内に特定できない")
        cursor += 1
        if spans and spans[-1][1] == cursor - 1:
            spans[-1] = (spans[-1][0], cursor)
        else:
            spans.append((cursor, cursor))
    return spans


def _source_text(path: pathlib.Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _range_steps(target: read_triggers.ReadTarget) -> str:
    """未取得の資料の取得に使う行範囲を、資料の現在の本文から算出して示す。"""
    source = _source_text(target.path)
    spans = _line_spans(target, source) if target.heading else [(1, len(source.splitlines()))]
    quoted = shlex.quote(str(target.path))
    return "と".join(f"`sed -n '{start},{end}p' -- {quoted}`" for start, end in spans)


def matches(operation: str, tool_name: str, tool_input: dict, cwd: str) -> bool:
    """共通語彙を既存の静的な操作判定へ結びつける。"""
    matched = operation_skills.operation_matches(operation, tool_name, tool_input)
    if matched is not None:
        return matched
    prefix, value = operation.split(":", 1)
    if prefix == "tool":
        return tool_name == value
    if prefix == "write":
        return any(
            fnmatch.fnmatch(path.replace("\\", "/"), value)
            for path in operation_skills.written_paths(tool_name, tool_input, cwd)
        )
    if prefix == "read":
        return any(_matches_document_path(path, value) for path in _read_path_variants(tool_name, tool_input, cwd))
    if prefix == "path":
        paths = operation_skills.written_paths(tool_name, tool_input, cwd)
        return any(fnmatch.fnmatch(path.replace("\\", "/"), value) for path in paths)
    if prefix == "section":
        # 行範囲の読取が宣言した節の行を1行でも含めば、その節の取得とする。分割取得の被覆は取得後に記録する。
        pattern, separator, heading = value.partition("#")
        if not separator:
            return False
        for path, start, end in _sed_reads(tool_name, tool_input, cwd)[0]:
            if not _matches_document_path(path, pattern) or not path.is_file():
                continue
            spans = _line_spans(read_triggers.ReadTarget(path, heading), _source_text(path))
            if any(left <= (end or right) and start <= right for left, right in spans):
                return True
        return False
    if prefix == "dependency":
        fields = tool_input_fields.new_content_fields(tool_name, tool_input)
        contents = [content for _name, content in fields or []]
        for path in operation_skills.written_paths(tool_name, tool_input, cwd):
            candidate = pathlib.Path(path)
            if candidate.is_file():
                contents.append(candidate.read_text(encoding="utf-8"))
        return any(value in content for content in contents)
    if prefix == "role":
        # 役割資料は受領した会話へstartup_contextが届ける。起動する親へは配送しない。
        return False
    if prefix == "skill":
        plugin, separator, short = value.partition(":")
        if not separator:
            plugin, short = "agent-toolkit", value
        requested = str(tool_input.get("skill", ""))
        requested = requested if ":" in requested else "agent-toolkit:" + requested
        return (tool_name == "Skill" and requested == plugin + ":" + short) or any(
            document_reads.skill_document(str(path)) == (short, "SKILL.md")
            and document_reads.document_plugin(str(path)) == plugin
            for path in _read_path_variants(tool_name, tool_input, cwd)
        )
    if prefix == "bash" and tool_name == "Bash" and isinstance(tool_input.get("command"), str):
        expected = shlex.split(value)
        for segment in extract_execution_segments(tool_input["command"]):
            if not segment.resolved or not segment.tokens:
                continue
            tokens = [pathlib.Path(segment.tokens[0]).name, *segment.tokens[1:]]
            if expected[0] == "git":
                subcommand = shell_checks.git_subcommand_tokens(segment)
                if subcommand is not None:
                    tokens = ["git", *subcommand]
            if expected[0] == "atk" and is_atk_command(segment.tokens):
                tokens[0] = "atk"
            if tokens[: len(expected)] == expected:
                return True
        return False
    return False


def _matches_document_path(path: pathlib.Path, pattern: str) -> bool:
    """同名skillを持つpluginを、明示した資料の取得操作でも区別する。"""
    candidates = [path]
    if path.name == "antigravity-full-instructions.md" and document_reads.skill_document(str(path)) is not None:
        candidates.append(path.parent.parent / "SKILL.md")
    for plugin in ("agent-toolkit", "agent-basics"):
        if pattern.startswith(plugin + ":"):
            return document_reads.document_plugin(str(path)) == plugin and any(
                fnmatch.fnmatch(candidate.as_posix(), pattern.removeprefix(plugin + ":")) for candidate in candidates
            )
    return any(fnmatch.fnmatch(candidate.as_posix(), pattern) for candidate in candidates)


def _document_owner(path: pathlib.Path) -> pathlib.Path | None:
    """選択された実体のmanifestを持つ所有rootを返す。別版の探索はしない。"""
    return next((parent for parent in path.resolve().parents if (parent / ".claude-plugin/plugin.json").is_file()), None)


def _bound_operation_matches(
    row: read_triggers.ReadTrigger, operation: str, tool_name: str, tool_input: dict, cwd: str
) -> bool:
    """同名の別rootを読む操作と、宣言が所属する実体を区別する。"""
    if not matches(operation, tool_name, tool_input, cwd):
        return False
    owner = _document_owner(row.source)
    plugin = document_reads.document_plugin(str(row.source))
    if owner is not None and operation.startswith((f"read:{plugin}:", f"section:{plugin}:", f"skill:{plugin}:")):
        requests = [tool_input]
        if tool_name == "Bash" and isinstance(tool_input.get("command"), str):
            requests = [
                {"command": shlex.join(segment.tokens)}
                for segment in extract_execution_segments(tool_input["command"])
                if segment.resolved
            ]
        if any(_read_paths(tool_name, request, cwd) for request in requests):
            prefix, value = operation.split(":", 1)
            pattern = value.partition("#")[0]
            return any(
                matches(operation, tool_name, request, cwd)
                and any(
                    _document_owner(path) == owner
                    and (
                        document_reads.skill_document(str(path)) == (value.rsplit(":", 1)[-1], "SKILL.md")
                        if prefix == "skill"
                        else _matches_document_path(path, pattern)
                    )
                    for path in _read_paths(tool_name, request, cwd)
                )
                for request in requests
            )
        if tool_name == "Skill":
            active = plugin_resources.plugin_root()
            if plugin != "agent-toolkit":
                active = sibling_plugin_root(plugin, active)
            if active is None or active.resolve() != owner:
                return False
    return True


def row_matches(row: read_triggers.ReadTrigger, tool_name: str, tool_input: dict, cwd: str) -> bool:
    plugin = document_reads.document_plugin(str(row.source))
    operations = tuple(
        "skill:" + plugin + ":" + operation.removeprefix("skill:")
        if operation.startswith("skill:") and ":" not in operation.removeprefix("skill:")
        else operation
        for operation in row.operations
    )
    if any(_bound_operation_matches(row, operation, tool_name, tool_input, cwd) for operation in operations):
        return True
    if row.kind == "起動時":
        skill = _skill(row.source)
        if skill:
            return _bound_operation_matches(row, f"skill:{plugin}:{skill}", tool_name, tool_input, cwd)
        if row.source.name.endswith(".subagent.md"):
            return False
        return row.source.resolve() in _read_paths(tool_name, tool_input, cwd)
    return False


def _user_texts(entry: dict):
    """ホストのユーザー本文だけを取り出す。取得した資料・ツール出力の見出しを役割にしない。"""
    message = entry.get("message") if entry.get("type") == "user" else None
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict) and payload.get("role") == "user":
        message = payload
    if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "user_message":
        if isinstance(payload.get("message"), str):
            yield payload["message"]
        return
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"} and isinstance(block.get("text"), str)
        ]
        if texts:
            yield "\n".join(texts)


def active_roles(payload: dict, session_id: str, records: dict, agent: str) -> set[pathlib.Path]:
    """正式な起動入力と役割を解決し、圧縮をまたぐ実体指定を保持する。"""
    prefix = "read-trigger-role:"
    if agent != "main":
        return set()
    roles = {
        pathlib.Path(key.removeprefix(prefix)).resolve()
        for key, agents in records.items()
        if key.startswith(prefix) and agent in agents
    }
    if agent in records.get("startup-context-observed", []):
        return roles
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return roles
    lines = transcript.read_transcript_lines(path)
    if lines is None:
        return roles
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for text in _user_texts(entry):
            context_seen = False
            startup_methods = ()
            for line in text.splitlines()[:1]:
                # 正式な役割見出しの候補だけでfrontmatterの処理を読み込む。
                if ".subagent.md" not in line:
                    continue
                role = task_documents.launched_task_document(line)
                role = role.resolve() if role is not None else None
                if role is not None and role.is_file() and role.parent == plugin_resources.plugin_root().resolve() / "share":
                    roles.add(role)
                    update_state(session_id, operation_skills.mark_ready(prefix + str(role), agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        startup_methods = read_triggers.reference_method_targets(role, plugin_resources.plugin_root())
                    context_seen = True
            for match in re.finditer(r'<atk-auto source="read-delivery" kind="startup-document">\n資料JSON: ([^\n]+)', text):
                metadata = json.loads(match[1])
                selectors = metadata.get("selectors", [metadata.get("selector", "")])
                input_target = any(isinstance(selector, str) and selector.startswith("input:") for selector in selectors)
                if not input_target and not startup_methods:
                    continue
                target = read_triggers.ReadTarget(pathlib.Path(metadata["path"]), metadata["heading"])
                if input_target or target in startup_methods:
                    context_seen = True
                    if input_target:
                        key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                        update_state(session_id, operation_skills.mark_ready(key, agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        rendered = read_triggers.startup_documents(
                            [target], plugin_resources.plugin_root(), {target: set(selectors)}
                        )
                        if rendered in text and agent not in records.get(rules_context.READ_CONTEXT_RESET_KEY, []):
                            _mark(session_id, target.path, agent, target.heading, metadata["sha256"])
            # 保存済みsessionの正式な参照だけの配送も、同じ実体指定として受理する。
            legacy = re.search(
                r'<atk-auto source="agents-server" kind="work-contract-reference">\n全体作業契約: ([^\n]+)\n', text
            )
            if legacy:
                contract = pathlib.Path(legacy[1])
                if contract.is_absolute():
                    context_seen = True
                    metadata = {"path": str(contract.resolve()), "heading": "", "selectors": ["input:作業契約"]}
                    key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                    update_state(session_id, operation_skills.mark_ready(key, agent))
            if context_seen:
                update_state(session_id, operation_skills.mark_ready("startup-context-observed", agent))
                return roles
    return roles


def _project_skill_rows(
    records: dict,
    agent: str,
    paths: tuple[pathlib.Path, ...] = (),
    *,
    known_sources: set[pathlib.Path] | None = None,
    host: str = "claude",
) -> list[read_triggers.ReadTrigger]:
    """選択した資料と、同じ主体が全文取得したplugin資料の宣言を使う。"""
    rows = []
    selected = set(paths)
    for key, owners in records.items():
        if agent not in owners:
            continue
        if key.startswith("resolved-project-skill:"):
            selected.add(pathlib.Path(key.removeprefix("resolved-project-skill:")))
        elif key.startswith(_PATH_PREFIX):
            value, separator, fingerprint = key.removeprefix(_PATH_PREFIX).rpartition("#sha256:")
            if separator and len(fingerprint) == 64:
                path = pathlib.Path(value)
                if (
                    path.is_file()
                    and _document_owner(path) is not None
                    and hashlib.sha256(path.read_bytes()).hexdigest() == fingerprint
                ):
                    selected.add(path)
    for path in sorted(selected):
        path = path.resolve()
        if path.suffix != ".md" or not path.is_file() or path in (known_sources or set()):
            continue
        explicit = "resolved-project-skill:" + str(path)
        if _document_owner(path) is not None or agent in records.get(explicit, []):
            rows.extend(read_triggers.parse_tables(path, plugin_resources.plugin_root(), host=host))
    return rows


def document_key(path: pathlib.Path, fingerprint: str = "") -> str:
    fingerprint = fingerprint or hashlib.sha256(path.read_bytes()).hexdigest()
    return _PATH_PREFIX + str(path.resolve()) + "#sha256:" + fingerprint


def _redundant_selection(target, declared, records: dict, agent: str) -> bool:
    """選択待ちと現行宣言の資料で、plugin名・相対パス・版・本文・取得主体の一致を調べる。"""
    path = target.path.resolve()
    for prefix in ("resolved-project-skill:", "modified-agent-document:", "read-trigger-role:"):
        if agent in records.get(prefix + str(path), []):
            return False
    for key, actors in records.items():
        if key.startswith("startup-input-target:") and agent in actors:
            metadata = json.loads(key.removeprefix("startup-input-target:"))
            if pathlib.Path(metadata["path"]).resolve() == path:
                return False
    owner = _document_owner(path)
    if owner is None:
        return False
    identity = json.loads((owner / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    if not identity.get("name") or not identity.get("version"):
        return False
    for candidate in declared:
        other = _document_owner(candidate.path)
        if other is None or other == owner or candidate.path.relative_to(other) != path.relative_to(owner):
            continue
        peer = json.loads((other / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        if (
            all(peer.get(key) == identity[key] for key in ("name", "version"))
            and candidate.path.read_bytes() == path.read_bytes()
            and target_ready(records, read_triggers.ReadTarget(candidate.path, target.heading), agent)
        ):
            return True
    return False


def dynamic_targets(
    row: read_triggers.ReadTrigger,
    payload: dict,
    tool_name: str,
    tool_input: dict,
    records: dict,
    agent: str,
    session_id: str = "",
) -> list[read_triggers.ReadTarget]:
    """宣言された動的資料を、実行時の実在入力だけから解決する。"""
    if not row.selector:
        return []
    if row.selector == "project-skill":
        targets: list[read_triggers.ReadTarget] = []
        for path in _read_paths(tool_name, tool_input, str(payload.get("cwd", ""))):
            if path.name != "SKILL.md" or not path.is_file():
                continue
            source = read_triggers.resolve_read_source(path, plugin_resources.plugin_root())
            declared = read_triggers.parse_tables(source, plugin_resources.plugin_root())
            if session_id:
                update_state(session_id, operation_skills.mark_ready("resolved-project-skill:" + str(source), agent))
            targets.extend(target for item in declared if item.kind == "起動時" for target in item.targets)
            targets.append(read_triggers.ReadTarget(source))
        return targets
    if row.selector.startswith("input:"):
        targets = []
        for key, agents in records.items():
            if key.startswith("startup-input-target:") and agent in agents:
                item = json.loads(key.removeprefix("startup-input-target:"))
                if row.selector in item.get("selectors", [item.get("selector", "")]):
                    targets.append(read_triggers.ReadTarget(pathlib.Path(item["path"]), item["heading"]))
        if not targets:
            raise ValueError(f"{row.source}:{row.line}: {row.selector}の起動時配送入力が無い")
        return targets
    if row.selector == "same-document":
        return [read_triggers.ReadTarget(row.source)]
    if row.selector == "modified-agent-documents":
        paths = [
            key.removeprefix("modified-agent-document:")
            for key, agents in records.items()
            if key.startswith("modified-agent-document:") and agent in agents
        ]
        return [read_triggers.ReadTarget(pathlib.Path(path)) for path in paths if pathlib.Path(path).is_file()]
    if row.selector == "role-contract":
        role = tool_input.get("subagent_md_path")
        if not isinstance(role, str):
            return []
        return list(read_triggers.parent_contract_targets(role, plugin_resources.plugin_root()))
    if row.selector == "review-reference":
        outputs = [
            pathlib.Path(key.removeprefix("review-output:"))
            for key, agents in records.items()
            if key.startswith("review-output:") and agent in agents
        ]
        unread = [path for path in outputs if agent not in records.get("review-output-parsed:" + document_key(path), [])]
        if unread:
            if any(path in _read_paths(tool_name, tool_input, str(payload.get("cwd", ""))) for path in unread):
                return []
            raise ValueError(f"prepareの保存JSONを末尾まで取得する: {unread}")
        paths = [
            key.removeprefix("review-reference:")
            for key, agents in records.items()
            if key.startswith("review-reference:") and agent in agents
        ]
        return [read_triggers.ReadTarget(pathlib.Path(path)) for path in paths if pathlib.Path(path).is_file()]
    if row.selector == "artifact-standards":
        cwd = payload.get("cwd", "")
        targets = []
        host = "codex" if is_codex_payload(payload) else "claude"
        provider = document_reads.document_plugin(str(row.source))
        selection = read_triggers.resolve_document(
            f"{provider}:writing-standards", row.source, plugin_resources.plugin_root(), host
        )
        if selection is not None and selection.is_file():
            targets.append(read_triggers.ReadTarget(selection))
        for candidate in declarations(cwd, host=host):
            if (
                _skill(candidate.source) == "writing-standards"
                and candidate.kind == "操作"
                and any(
                    operation.startswith(("path:", "write:", "dependency:")) and matches(operation, tool_name, tool_input, cwd)
                    for operation in candidate.operations
                )
            ):
                targets.extend(candidate.targets)
        return list(dict.fromkeys(targets))
    if row.selector == "external-pytilpack-index":
        index_ready = agent in records.get("external-document:https://ak110.github.io/pytilpack/llms.txt", [])
        pages = [
            key
            for key, agents in records.items()
            if key.startswith("external-document:https://ak110.github.io/pytilpack/")
            and not key.endswith(("/llms.txt", "/llms-full.txt"))
            and agent in agents
        ]
        if index_ready and pages:
            return []
        raise ValueError(f"{row.source}:{row.line}: 外部資料の索引と対象モジュールをWebFetchで取得する。ローカル配送の対象外")
    raise ValueError(f"未登録の資料選択子: {row.selector}")


def _mark(session_id: str, path: pathlib.Path, agent: str, heading: str = "", fingerprint: str = "") -> None:
    identifier = document_key(path, fingerprint) + ("#heading:" + heading if heading else "")
    update_state(session_id, operation_skills.mark_ready(identifier, agent))
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    if not heading and agent in records.get("review-output:" + str(path), []):
        raw = path.read_bytes()
        if fingerprint and hashlib.sha256(raw).hexdigest() != fingerprint:
            return
        _review_reference(session_id, json.loads(raw), agent)
        update_state(session_id, operation_skills.mark_ready("review-output-parsed:" + document_key(path, fingerprint), agent))

        def parsed(state: dict) -> dict:
            rows = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
            key = "review-output:" + str(path)
            if key in rows:
                rows[key] = [owner for owner in rows[key] if owner != agent]
                if not rows[key]:
                    del rows[key]
            return state

        update_state(session_id, parsed)


def _review_reference(session_id: str, prepared: object, agent: str) -> None:
    """prepareの成功JSONが示す実在する資料だけを登録する。"""
    if not isinstance(prepared, dict):
        raise ValueError("session-review-prepareの結果がJSON objectでない")
    value = prepared.get("reference_document")
    if value is None or value == "":
        return
    path = pathlib.Path(value) if isinstance(value, str) else None
    if path is None or not path.is_absolute() or not path.is_file():
        raise ValueError(f"reference_documentの実体を解決できない: {value}")
    update_state(session_id, operation_skills.mark_ready("review-reference:" + str(path), agent))


def target_ready(records: dict, target: read_triggers.ReadTarget, agent: str) -> bool:
    raw = target.path.read_bytes()
    key = document_key(target.path, hashlib.sha256(raw).hexdigest())
    if target.heading:
        try:
            target.body(raw.decode("utf-8"))
        except ValueError:
            return False
    return (
        agent in records.get(key, []) or bool(target.heading) and agent in records.get(key + "#heading:" + target.heading, [])
    )


def selected_targets(records: dict, agent: str) -> list[read_triggers.ReadTarget]:
    targets = []
    for key, agents in records.items():
        if key.startswith("selected-read-target:") and agent in agents:
            raw = json.loads(key.removeprefix("selected-read-target:"))
            path = pathlib.Path(raw["path"])
            if path.is_file():
                targets.append(read_triggers.ReadTarget(path, raw["heading"]))
    return targets


def _select_targets(session_id: str, targets: list[read_triggers.ReadTarget], agent: str) -> None:
    for target in targets:
        value = json.dumps({"path": str(target.path), "heading": target.heading}, ensure_ascii=False, separators=(",", ":"))
        update_state(session_id, operation_skills.mark_ready("selected-read-target:" + value, agent))


def _restore_history_reads(payload: dict, session_id: str, agent: str, targets: list[read_triggers.ReadTarget]) -> None:
    """現在の文脈で成功した履歴を、呼出ID・本文・actorの一致で復元する。"""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path or not targets:
        return
    lines = transcript.read_transcript_lines(path)
    if lines is None:
        return
    pending: dict[str, tuple[str, dict, str]] = {}
    completed: dict[read_triggers.ReadTarget, str] = {}
    ranges: dict[tuple[read_triggers.ReadTarget, int, str], list[tuple[int, int]]] = {}
    paths = {target.path for target in targets}
    sections = {target for target in targets if target.heading}
    boundary_seen = False
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    reset = isinstance(records, dict) and agent in records.get(rules_context.READ_CONTEXT_RESET_KEY, [])
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        actor = entry.get("agent_id", entry.get("agentId"))
        if agent == "main" and entry.get("isSidechain") is True:
            continue
        if actor is not None and not isinstance(actor, str):
            continue
        if isinstance(actor, str) and actor:
            if actor != agent:
                continue
        elif agent != "main" or entry.get("isSidechain") is True:
            continue
        if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
            pending.clear()
            completed.clear()
            ranges.clear()
            boundary_seen = True
            continue
        message = entry.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                continue
            if entry.get("type") == "assistant" and block.get("type") == "tool_use":
                tool_id, name, inputs = block.get("id"), block.get("name"), block.get("input")
                if isinstance(tool_id, str) and isinstance(name, str) and name in {"Read", "Bash"} and isinstance(inputs, dict):
                    cwd = entry.get("cwd", payload.get("cwd", ""))
                    pending[tool_id] = (name, inputs, cwd if isinstance(cwd, str) else "")
            elif entry.get("type") == "user" and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if not isinstance(tool_id, str):
                    continue
                request = pending.pop(tool_id, None)
                if request is None or block.get("is_error") is True:
                    continue
                name, inputs, cwd = request
                response = block.get("content")
                structured = entry.get("toolUseResult")
                if isinstance(structured, dict):
                    response = structured
                elif isinstance(response, list):
                    response = "\n".join(
                        part["text"] for part in response if isinstance(part, dict) and isinstance(part.get("text"), str)
                    )
                with contextlib.suppress(OSError, UnicodeError, ValueError, RuntimeError):
                    read_paths = [candidate for candidate in _read_paths(name, inputs, cwd) if candidate in paths]
                    for target, start, end, length, fingerprint in _read_body_proofs(
                        name, inputs, response, read_paths, paths, sections, cwd
                    ):
                        key = (target, length, fingerprint)
                        ranges.setdefault(key, []).append((start, end))
                        reached = 0
                        for left, right in sorted(ranges[key]):
                            if left > reached:
                                break
                            reached = max(reached, right)
                        if reached >= length:
                            completed[target] = fingerprint
    # clear/compact直後に境界の位置をまだ確かめられない履歴は再計上しない。
    if reset and not boundary_seen:
        return
    for target, fingerprint in completed.items():
        _mark(session_id, target.path, agent, target.heading, fingerprint)
        if target.heading:
            for selected in sections:
                if selected.path == target.path and selected.body().strip() in target.body():
                    _mark(session_id, selected.path, agent, selected.heading, fingerprint)


def _codex_dependency_repair(tokens: tuple[str, ...], error: Exception) -> bool:
    """未導入のBasicsだけを、導入済みToolkitと同じmarketplaceから追加できる。"""
    if str(error).casefold() != "codexのagent-basics導入先が一意でない: 0件":
        return False
    if tokens[1:3] != ("plugin", "add") or len(tokens) != 5 or tokens[4] != "--json":
        return False
    if not tokens[3].startswith("agent-basics@"):
        return False
    try:
        result = process_tree.run_with_timeout(
            [tokens[0], "plugin", "list", "--json"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        data = json.loads(result.stdout) if result.returncode == 0 else None
    except OSError, ValueError, subprocess.TimeoutExpired:
        return False
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, list):
        return False
    marketplaces = {
        item["marketplaceName"]
        for item in installed
        if isinstance(item, dict)
        and item.get("name") == "agent-toolkit"
        and item.get("installed") is True
        and item.get("enabled") is True
        and isinstance(item.get("marketplaceName"), str)
        and item["marketplaceName"]
    }
    return len(marketplaces) == 1 and tokens[3] == "agent-basics@" + next(iter(marketplaces))


def _declaration_recovery_operation(tool_name: str, tool_input: dict, error: Exception) -> bool:
    """読取・公開診断と、原因に対応する正規の依存追加を宣言取得から独立させる。"""
    if not isinstance(error, PluginRootError):
        return False
    if tool_name in {"Read", "Glob", "Grep", "mcp__agents_server__list"}:
        return True
    command = tool_input.get("command")
    if tool_name != "Bash" or not isinstance(command, str):
        return False
    # 回復用の例外では、単一の静的な呼出しだけを受理する。
    if any(char in command for char in ("$", "`", "\n", "\r")):
        return False
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    try:
        tokens = tuple(lexer)
    except ValueError:
        return False
    if not tokens or any(token and all(char in ";&|<>()" for char in token) for token in tokens):
        return False
    name = pathlib.Path(tokens[0]).name
    if name in {"cat", "cat.exe"}:
        return len(tokens) > 1
    if sed_read_range(tokens) is not None:
        return True
    if name in {"codex", "codex.exe"}:
        return (
            tokens[1:3] == ("plugin", "list")
            and all(token in {"--json", "--help"} for token in tokens[3:])
            or tokens[1:] == ("plugin", "add", "--help")
            or _codex_dependency_repair(tokens, error)
        )
    if is_atk_command(tokens):
        return tokens[1:2] in {("info",), ("--help",)} or tokens[1:3] in {
            ("agents", "logs"),
            ("agents", "list"),
            ("agents", "status"),
        }
    return False


def deliver(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    """現在の操作に関係する宣言資料を配送または案内し、通常操作の進行を保つ。"""
    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return Decision()
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    handoff = delegated_session.handoff_path(os.environ)
    written = operation_skills.written_paths(tool_name, tool_input, cwd)
    if handoff and written and all(pathlib.Path(path).resolve() == pathlib.Path(handoff).resolve() for path in written):
        return Decision()
    warning_only = tool_name in {"AskUserQuestion", "ExitPlanMode", "request_user_input", "request_user_input_async"}
    try:
        rows = declarations(cwd, host="codex" if is_codex_payload(payload) else "claude")
    except (OSError, UnicodeError, ValueError) as error:
        if warning_only:
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込宣言を取得できない: {error}",
                        tag=_WARN_TAG,
                        fix="回答に依存する次の操作の前に宣言を復旧し、質問の前提を確認する。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        if _declaration_recovery_operation(tool_name, tool_input, error):
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込宣言を取得できない: {error}",
                        tag=_WARN_TAG,
                        fix="読取・導入診断と正規の依存追加で宣言を復旧し、取得結果を確かめてから判断・その他の変更へ進む。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        recovery_paths = operation_skills.written_paths(tool_name, tool_input, cwd) + [
            str(path) for path in _read_paths(tool_name, tool_input, cwd)
        ]
        if any(str(error).startswith(path + ":") for path in recovery_paths):
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込宣言の復旧対象を操作する: {error}",
                        tag=_WARN_TAG,
                        fix="宣言を復旧した後に元の操作を再発行する。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        return Decision(
            notices=(
                _llm_notice(
                    f"読込宣言を取得できない: {error}",
                    tag=_WARN_TAG,
                    fix="宣言と資料の所在を診断し、復旧後に必要な資料を読む。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    roles = active_roles(payload, session_id, records, agent)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    try:
        rows = list(
            dict.fromkeys(
                [
                    *rows,
                    *_project_skill_rows(
                        records,
                        agent,
                        tuple(_read_paths(tool_name, tool_input, cwd)),
                        known_sources={row.source for row in rows},
                        host="codex" if is_codex_payload(payload) else "claude",
                    ),
                ]
            )
        )
        pending = list(
            dict.fromkeys(
                target
                for row in rows
                if (
                    row_matches(row, tool_name, tool_input, cwd)
                    or row.selector == "artifact-standards"
                    and target_ready(records, read_triggers.ReadTarget(row.source), agent)
                    and bool(operation_skills.written_paths(tool_name, tool_input, cwd))
                    or row.source in roles
                    and (row.kind == "起動時" or any(operation.startswith("role:") for operation in row.operations))
                )
                for target in (*row.targets, *dynamic_targets(row, payload, tool_name, tool_input, records, agent, session_id))
                if not target_ready(records, target, agent)
            )
        )
        host = "codex" if is_codex_payload(payload) else "claude"
        method_sources = dict.fromkeys([*_read_paths(tool_name, tool_input, cwd), *sorted(roles)])
        for path in method_sources:
            if path.suffix == ".md" and path.is_file():
                pending.extend(
                    target
                    for target in read_triggers.reference_method_targets(path, plugin_resources.plugin_root(), host)
                    if target not in pending and not target_ready(records, target, agent)
                )
        # スキル本文とともに届く共有の初動資料を、同じ受領単位へ含める。
        cursor = 0
        while cursor < len(pending):
            target = pending[cursor]
            cursor += 1
            pending.extend(
                method
                for method in read_triggers.reference_method_targets(target.path, plugin_resources.plugin_root(), host)
                if method not in pending and not target_ready(records, method, agent)
            )
            if not target.heading:
                rows = list(
                    dict.fromkeys(
                        [
                            *rows,
                            *_project_skill_rows(
                                records,
                                agent,
                                (target.path,),
                                known_sources={row.source for row in rows},
                                host="codex" if is_codex_payload(payload) else "claude",
                            ),
                        ]
                    )
                )
            target_identity = document_reads.skill_document(str(target.path))
            for row in rows:
                if (
                    row.kind == "起動時"
                    and row.source == target.path
                    and target_identity is not None
                    and target_identity[1] == "SKILL.md"
                    and not target.heading
                ):
                    pending.extend(
                        item for item in row.targets if item not in pending and not target_ready(records, item, agent)
                    )
    except (OSError, ValueError) as error:
        if warning_only:
            return Decision(
                notices=(
                    _llm_notice(
                        f"動的資料を解決できない: {error}",
                        tag=_WARN_TAG,
                        fix="回答に依存する次の操作の前に入力と資料を確定し、質問の前提を確認する。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        return Decision(
            notices=(
                _llm_notice(
                    f"動的資料を解決できない: {error}",
                    tag=_WARN_TAG,
                    fix="実在する入力と取得対象を確定し、該当する資料を読む。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    if pending:
        _restore_history_reads(payload, session_id, agent, pending)
        restored = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
        records = restored if isinstance(restored, dict) else {}
        pending = [target for target in pending if not target_ready(records, target, agent)]
    if not pending:
        return Decision()
    acquisition_paths = _read_paths(tool_name, tool_input, cwd)
    if any(row.selector == "project-skill" and row_matches(row, tool_name, tool_input, cwd) for row in rows):
        # SKILL.mdを返す取得より先に、その初動資料を届ける。確定したSKILL自体の取得はその再発行で行う。
        _select_targets(session_id, pending, agent)
        pending = [target for target in pending if target.path not in acquisition_paths]
        if not pending:
            return Decision()
    if any(target.path in acquisition_paths for target in pending):
        _select_targets(session_id, pending, agent)
        pending = [target for target in pending if target.path not in acquisition_paths]
        if not pending:
            # 取得操作を許可し、PostToolUseで返却本文と資料の指定範囲が一致するか確かめる。
            # 次の依存操作では取得完了を確かめ、未取得なら関連資料を案内する。
            return Decision()
    shown = "、".join(f"`{path}`" for path in pending)
    if warning_only:
        if tool_name != "ExitPlanMode":
            if not agent_id.is_main_agent_context(payload):
                return Decision()
            history_path = payload.get("transcript_path")
            if isinstance(history_path, str) and history_path and transcript.read_transcript_lines(history_path) is None:
                return Decision()
        return Decision(
            notices=(
                _llm_notice(
                    f"操作前に全文読む資料が未取得: {shown}。",
                    tag=_WARN_TAG,
                    fix="まず質問の必要性と資料の読込条件を見直す。不要な質問は取り下げる。"
                    "質問を続ける場合は、条件に該当する資料を読み、質問の前提と選択肢を確かめ直す。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    _select_targets(session_id, pending, agent)
    if is_codex_payload(payload):
        try:
            steps = [f"`{target}`は{_range_steps(target)}" for target in pending]
        except (OSError, UnicodeError, ValueError) as error:
            steps = [f"行範囲を算出できない（{error}）。各資料を`cat -- <絶対パス>`で全文取得する"]
        return Decision(
            notices=(
                _llm_notice(
                    f"操作前に全文読む資料: {shown}。",
                    tag=_WARN_TAG,
                    removable_cause=True,
                    escalate_on_repeat=False,
                    fix="まず操作の必要性と資料の読込条件を見直す。不要な操作は中止する。"
                    "続行する場合は、条件に該当する資料の指定範囲を取得する。"
                    + "。".join(steps)
                    + "。予算を超える範囲や出力が省略された範囲は、開始行と終了行を狭めて同じ形で分割取得する。"
                    "全文必須の資料は検索だけで読了としない。",
                ),
            )
        )
    bodies: list[str] = []
    fingerprints: list[str] = []
    used = 0
    read_failure = ""
    for target in pending:
        owner = next(
            (parent.parent for parent in target.path.parents if parent.name == "skills"), plugin_resources.plugin_root()
        )
        try:
            raw = target.path.read_bytes()
            source_text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            text = target.body(source_text).replace("${CLAUDE_PLUGIN_ROOT}", str(owner))
        except (OSError, UnicodeError, ValueError) as error:
            repairs = {row.source.resolve() for row in rows if target in row.targets} | {target.path}
            writes = operation_skills.written_paths(tool_name, tool_input, cwd)
            if writes and all(pathlib.Path(path).resolve() in repairs for path in writes):
                return Decision(
                    notices=(
                        _llm_notice(
                            f"読込宣言の範囲を復旧する操作を通す: {error}",
                            tag=_WARN_TAG,
                            fix="宣言と実見出しを整合させた後に元の操作を再発行する。",
                            removable_cause=True,
                            escalate_on_repeat=False,
                        ),
                    )
                )
            read_failure = f"\n資料本文を取得できない: {error}"
            break
        body = (
            '\n<atk-auto source="read-delivery" kind="document">\n'
            f"資料: {target}\n相対参照の基点: {target.path.parent}\n{text}\n</atk-auto>\n"
        )
        if used + len(body) > CLAUDE_BODY_BUDGET:
            break
        bodies.append(body)
        fingerprints.append(hashlib.sha256(raw).hexdigest())
        used += len(body)
    # 読込失敗や上限を超える資料を配送済みにしない。再発行時は同じ順から再開する。
    for target, fingerprint in zip(pending[: len(bodies)], fingerprints, strict=True):
        try:
            _mark(session_id, target.path, agent, target.heading, fingerprint)
        except (OSError, UnicodeError, ValueError) as error:
            read_failure += f"\n資料の取得結果を登録できない: {error}"
    remaining = pending[len(bodies) :]
    suffix = ""
    if remaining:
        try:
            suffix = "\n未取得: " + "、".join(f"`{target}`（{_range_steps(target)}）" for target in remaining)
        except OSError, UnicodeError, ValueError:
            suffix = "\n未取得: " + "、".join(f"`{target}`" for target in remaining)
        suffix += "\n行範囲はReadの`offset`（開始行）と`limit`（行数）でも同じ範囲を取得できる。"
    suffix += read_failure
    return Decision(
        notices=(
            _llm_notice(
                "操作に関係する資料を配送する。" + "".join(bodies) + suffix,
                tag="notice",
                fix="配送本文の基準を適用する。本文が収まらない資料は示した絶対パスから必要な範囲を読む。",
            ),
        )
    )


def _read_body_proofs(
    tool_name: str,
    tool_input: dict,
    response: object,
    paths: list[pathlib.Path],
    targets: set[pathlib.Path],
    section_targets: set[read_triggers.ReadTarget],
    cwd: str,
) -> Iterator[tuple[read_triggers.ReadTarget, int, int, int, str]]:
    """本文と位置が実ファイルに一致する取得範囲を返す。履歴とPostToolUseで同じ本文一致の判定を使う。"""
    if isinstance(response, str):
        with contextlib.suppress(ValueError):
            decoded = json.loads(response)
            if isinstance(decoded, dict) and any(key in decoded for key in ("exit_code", "interrupted", "is_error", "file")):
                response = decoded
    if isinstance(response, dict) and (
        response.get("exit_code", 0) != 0
        or response.get("interrupted")
        or response.get("is_error")
        or isinstance(response.get("file"), dict)
        and response["file"].get("truncatedByTokenCap")
    ):
        return
    text = document_reads.response_text(response)
    if not text and isinstance(response, dict) and isinstance(response.get("file"), dict):
        text = response["file"].get("content")
    if not isinstance(text, str) or any(
        marker in text for marker in ("truncated output", "Output too large", "truncatedByTokenCap")
    ):
        return
    sed_reads, only_reads = _sed_reads(tool_name, tool_input, cwd)
    if sed_reads:
        if only_reads:
            yield from _sed_range_proofs(sed_reads, text, targets, section_targets)
        return
    for path in paths:
        if path not in targets:
            continue
        raw = path.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        source_body = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        body = source_body
        # 本文全体が結果に含まれる取得だけを全取得とする。保存先の本文を読む行為は別の取得である。
        normalized = re.sub(r"(?m)^\s*\d+[→\t]", "", text).replace("\r\n", "\n").replace("\r", "\n")
        if tool_name == "Read" and isinstance(response, dict) and isinstance(response.get("file"), dict):
            file = response["file"]
            start, count, total = file.get("startLine"), file.get("numLines"), file.get("totalLines")
            if all((isinstance(value, int) and not isinstance(value, bool)) for value in (start, count, total)):
                lines = body.splitlines(keepends=True)
                if not (total == len(lines) and 1 <= start <= total and count > 0):
                    continue
                expected = "".join(lines[start - 1 : start - 1 + count])
                if expected.rstrip("\n") != normalized.rstrip("\n"):
                    continue
        if body.strip() and body.strip() in normalized:
            yield read_triggers.ReadTarget(path), 0, len(body), len(body), fingerprint
            continue
        for target in section_targets:
            if target.path == path and target.body(source_body).strip() in normalized:
                yield target, 0, len(target.body(source_body)), len(target.body(source_body)), fingerprint
        file = response.get("file") if isinstance(response, dict) else None
        if tool_name == "Read" and isinstance(file, dict) and not file.get("truncatedByTokenCap"):
            start, count, total = file.get("startLine"), file.get("numLines"), file.get("totalLines")
            lines = body.splitlines(keepends=True)
            if (
                (isinstance(start, int) and not isinstance(start, bool))
                and (isinstance(count, int) and not isinstance(count, bool))
                and (isinstance(total, int) and not isinstance(total, bool))
                and total == len(lines)
                and 1 <= start <= total
                and count > 0
            ):
                expected = "".join(lines[start - 1 : start - 1 + count])
                if expected.rstrip("\n") == normalized.rstrip("\n"):
                    left = len("".join(lines[: start - 1]))
                    yield read_triggers.ReadTarget(path), left, left + len(expected), len(body), fingerprint


def _sed_range_proofs(
    reads: list[tuple[pathlib.Path, int, int | None]],
    text: str,
    targets: set[pathlib.Path],
    section_targets: set[read_triggers.ReadTarget],
) -> Iterator[tuple[read_triggers.ReadTarget, int, int, int, str]]:
    """行範囲の連結が出力全体と一致した場合だけ、各範囲を資料と節の文字位置へ変換して返す。

    範囲が資料の行数を外れる場合と、出力の欠落・追加・相違がある場合は、どの範囲も取得済みにしない。
    """
    pieces = []
    for path, start, end in reads:
        source = _source_text(path)
        lines = source.splitlines(keepends=True)
        last = len(lines) if end is None else min(end, len(lines))
        if start > last:
            return
        pieces.append((path, source, lines, start, last))
    expected = "".join("".join(lines[start - 1 : last]) for _path, _source, lines, start, last in pieces)
    if expected.rstrip("\n") != text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n"):
        return
    for path, source, lines, start, last in pieces:
        if path not in targets:
            continue
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        left = len("".join(lines[: start - 1]))
        yield read_triggers.ReadTarget(path), left, left + len("".join(lines[start - 1 : last])), len(source), fingerprint
        for target in section_targets:
            if target.path != path:
                continue
            # 節の本文は選ばれた行の連結であり、行範囲と重なる行は節の本文の中で連続する。
            numbers = [
                number for left_line, right_line in _line_spans(target, source) for number in range(left_line, right_line + 1)
            ]
            inside = [index for index, number in enumerate(numbers) if start <= number <= last]
            if not inside:
                continue
            offset = len("".join(lines[number - 1] for number in numbers[: inside[0]]))
            size = len("".join(lines[numbers[index] - 1] for index in inside))
            length = len("".join(lines[number - 1] for number in numbers))
            yield target, offset, offset + size, length, fingerprint


def observe(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    """成功した全文・分割読取を資料単位・主体単位に記録する。"""
    if payload.get("hook_event_name") in {"PostToolUseFailure", "PermissionDenied"}:
        return
    response = payload.get("tool_response")
    if isinstance(response, dict) and (
        response.get("exit_code", 0) != 0
        or response.get("interrupted")
        or response.get("is_error")
        or isinstance(response.get("file"), dict)
        and response["file"].get("truncatedByTokenCap")
    ):
        return
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    agent = agent_id.resolve_hook_agent_id(payload)
    if (
        tool_name == "WebFetch"
        and isinstance(tool_input.get("url"), str)
        and response
        and not any(marker in str(response) for marker in ("truncated output", "Output too large", "truncatedByTokenCap"))
    ):
        update_state(session_id, operation_skills.mark_ready("external-document:" + tool_input["url"], agent))
    for path in operation_skills.written_paths(tool_name, tool_input, cwd):
        if operation_skills.is_agent_doc_target_file(path):
            update_state(
                session_id, operation_skills.mark_ready("modified-agent-document:" + str(pathlib.Path(path).resolve()), agent)
            )
    text = document_reads.response_text(response)
    if (
        tool_name == "Bash"
        and isinstance(tool_input.get("command"), str)
        and any(
            segment.resolved
            and is_atk_command(segment.tokens)
            and segment.tokens[1:3] == ("run-script", "session-review-prepare")
            for segment in extract_execution_segments(tool_input["command"])
        )
        and text
    ):
        try:
            prepared = json.loads(text)
        except ValueError:
            prepared = None
        if prepared is not None:
            _review_reference(session_id, prepared, agent)
        else:
            saved = re.search(r"(?m)^保存先: (.+)$", text)
            if saved:
                path = pathlib.Path(saved[1])
                if not path.is_absolute() or not path.is_file():
                    raise ValueError(f"prepareの保存結果が実在しない: {path}")
                update_state(session_id, operation_skills.mark_ready("review-output:" + str(path), agent))
                _select_targets(session_id, [read_triggers.ReadTarget(path)], agent)
    paths = _read_paths(tool_name, tool_input, cwd)
    if tool_name != "Skill" and not paths:
        return
    rows = declarations(cwd, host="codex" if is_codex_payload(payload) else "claude")
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    rows = list(
        dict.fromkeys(
            [
                *rows,
                *_project_skill_rows(
                    records if isinstance(records, dict) else {},
                    agent,
                    tuple(paths),
                    known_sources={row.source for row in rows},
                    host="codex" if is_codex_payload(payload) else "claude",
                ),
            ]
        )
    )
    chosen_targets = selected_targets(records if isinstance(records, dict) else {}, agent)
    targets = {path for row in rows for path in row.documents} | {target.path for target in chosen_targets}
    targets.update(path for path in paths if path.suffix == ".md" and _document_owner(path) is not None)
    section_targets = {target for row in rows for target in row.targets if target.heading} | {
        target for target in chosen_targets if target.heading
    }
    if tool_name == "Skill":
        qualified = str(tool_input.get("skill", ""))
        name = qualified.rsplit(":", 1)[-1]
        plugin = qualified.split(":", 1)[0] if ":" in qualified else "agent-toolkit"
        for path in targets:
            if path.name == "SKILL.md" and path.parent.name == name and document_reads.document_plugin(str(path)) == plugin:
                _mark(session_id, path, agent)
        return
    for target, start, end, length, fingerprint in _read_body_proofs(
        tool_name, tool_input, response, paths, targets, section_targets, cwd
    ):
        if start == 0 and end == length:
            _mark(session_id, target.path, agent, target.heading, fingerprint)
            complete = True
        else:
            complete = _record_range(session_id, target.path, agent, start, end, length, target.heading, fingerprint)
        if complete and target.heading:
            body = target.body()
            for selected in section_targets:
                if selected.path == target.path and selected.body().strip() in body:
                    _mark(session_id, selected.path, agent, selected.heading, fingerprint)


def _record_range(
    session_id: str, path: pathlib.Path, agent: str, start: int, end: int, length: int, section: str = "", fingerprint: str = ""
) -> bool:
    """連続被覆が本文末尾に達したときだけ取得済みにする。圧縮では既存の主体別リセットが全区間を除く。"""
    prefix = document_key(path, fingerprint) + ("#heading:" + section if section else "") + "#range:"
    update_state(session_id, operation_skills.mark_ready(f"{prefix}{start}:{end}", agent))
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    intervals = sorted(
        tuple(map(int, key[len(prefix) :].split(":")))
        for key, agents in records.items()
        if key.startswith(prefix) and agent in agents
    )
    reached = 0
    for left, right in intervals:
        if left > reached:
            break
        reached = max(reached, right)
    if reached >= length:
        _mark(session_id, path, agent, section, fingerprint)
        return True
    return False
