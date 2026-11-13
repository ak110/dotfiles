"""読取要求と実配送から主体別の受信証拠を記録する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import json
import os
import pathlib
from typing import TYPE_CHECKING

from agent_toolkit._common import delegated_session, read_triggers, shell_cwd
from agent_toolkit._common.shell_tokens import join_words
from agent_toolkit._hooks import (
    agent_id,
    codex_read_observation,
    document_reads,
    rules_context,
)
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.large_reads import (
    is_dynamic_read_operand,
    sed_read_range,
)
from agent_toolkit._hooks.pretooluse.read_delivery_paths import _read_paths
from agent_toolkit._hooks.pretooluse.read_delivery_selection import _select_result_documents

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def _named_section_targets(rows: list[read_triggers.ReadTrigger], paths: list[pathlib.Path]) -> list[read_triggers.ReadTarget]:
    """読取対象の資料について、宣言が節を名指しする範囲を返す。

    節の範囲は資料の途中から読むことが多く、条件の成立も読取の時点では確かめられないため、配送操作との一致では判定しない。
    節を名指ししない大多数の行は、資料列の文字列と読取対象の名前で資料を解決せずに外す。
    """
    if not paths:
        return []
    names = {path.name for path in paths}
    found: list[read_triggers.ReadTarget] = []
    for row in rows:
        if not row.headings and not ("「" in row.material and any(name in row.material for name in names)):
            continue
        try:
            found.extend(target for target in row.targets if target.heading and target.path in paths)
        except OSError, UnicodeError, ValueError:
            # 解決できない宣言の診断は_deliver_documentsが返す。
            continue
    return found


def _receipt_targets(
    payload: dict,
    tool_name: str,
    tool_input: dict,
    session_id: str,
    *,
    active_only: bool = False,
    include_selected: bool = True,
) -> list[read_triggers.ReadTarget]:
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    cwd = str(payload.get("cwd", ""))
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    try:
        rows = read_delivery.declarations(cwd, host="codex" if is_codex_payload(payload) else "claude", session_id=session_id)
    except OSError, UnicodeError, ValueError:
        # 宣言の診断と復旧操作の許可は_deliver_documentsが返す。
        rows = []
    targets = [
        target
        for row in rows
        if not active_only
        or read_delivery.condition_confirmed(row, tool_name, tool_input, cwd)
        and read_delivery.row_matches(row, tool_name, tool_input, cwd)
        for target in row.targets
    ]
    if active_only:
        targets.extend(_named_section_targets(rows, _read_paths(tool_name, tool_input, cwd)))
    if include_selected:
        targets.extend(read_delivery.selected_targets(records, agent))
    targets.extend(read_triggers.ReadTarget(path) for path in _read_paths(tool_name, tool_input, cwd))
    targets.extend(
        read_triggers.ReadTarget(pathlib.Path(json.loads(key.removeprefix("startup-input-target:"))["path"]))
        for key, actors in records.items()
        if key.startswith("startup-input-target:") and agent in actors
    )
    return list(dict.fromkeys(targets))


def _record_receipts(
    session_id: str,
    agent: str,
    candidates: list[dict],
    texts: list[str],
    *,
    fragments=None,
    requests=None,
    coverage=None,
    host="claude",
    project=None,
) -> None:
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
    from agent_toolkit._hooks.pretooluse.read_delivery import _mark  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    received = document_reads.received_candidates(candidates, texts)
    if fragments is None:
        fragments = [
            (packet, packet["blocks"]) for packet in document_reads.fragment_candidates(candidates, texts, requests or {})
        ]
    coverage = coverage if coverage is not None else document_reads.fragment_coverage([packet for packet, _blocks in fragments])
    received = document_reads.restrict_receipts(received, coverage)
    if not received and not fragments:
        return
    full = []

    def mark(value: dict) -> dict:
        records = value.setdefault(rules_context.OPERATION_SKILL_READY_KEY, {})
        completed = list(received)
        for packet, blocks in fragments:
            completed.extend(document_reads.accumulate_fragments(records, packet, blocks, agent))
        known = {tuple(part) for item in completed for part in item["parts"]}
        completed.extend(
            document_reads.restrict_receipts(
                [item for item in candidates if all(tuple(part) in known for part in item["parts"])], coverage
            )
        )
        observed = set()
        for candidate in completed:
            metadata = document_reads.receipt_metadata(candidate)
            scope = (metadata["logical"], metadata["heading"], json.dumps(metadata["parts"]))
            if scope in observed:
                continue
            observed.add(scope)
            operation_skills.mark_ready(document_reads.receipt_key(metadata), agent)(value)
            if not metadata["heading"]:
                full.append(metadata)
        return value

    read_delivery.update_state(session_id, mark)
    for metadata in full:
        path = pathlib.Path(metadata["path"])
        if path.is_file() and document_reads.content_fingerprint(path) == metadata["version"]:
            _mark(session_id, path, agent, metadata["version"], record_scopes=False, host=host, project=project)
        else:
            read_delivery.update_state(
                session_id, operation_skills.mark_ready(read_delivery.document_key(path, metadata["version"]), agent)
            )


def _receipt_candidates(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> list[dict]:
    # Codexは次の操作で外側配送を観測するため、静的読取でない応答の候補もその時点まで保持する。
    if tool_name not in {"Read", "Bash"} and not is_codex_payload(payload):
        return []
    requested = {pathlib.Path(path) for path in _fragment_requests(payload, tool_name, tool_input)}
    targets = _receipt_targets(payload, tool_name, tool_input, session_id, active_only=True)
    active = {target.path for target in targets}
    candidates = document_reads.receipt_candidates(
        [
            *targets,
            *(read_triggers.ReadTarget(path) for path in requested),
        ],
        fragment_paths=active | requested,
    )
    request = _read_request(tool_name, tool_input)
    for item in candidates:
        if "lines" in item:
            item["request"] = request
    return candidates


def _read_request(tool_name: str, tool_input: dict) -> str:
    return document_reads.bytes_fingerprint(json.dumps([tool_name, tool_input], sort_keys=True).encode("utf-8"))


def _fragment_requests(payload: dict, tool_name: str, tool_input: dict) -> dict[str, list[tuple[int, int | None, int]]]:
    """ホストの読取引数から原本上の行位置を取得し、未知の位置は集約しない。"""
    cwd = str(payload.get("cwd", ""))
    result: dict[str, list[tuple[int, int | None, int]]] = {}
    if tool_name == "Read":
        start = tool_input.get("offset", 1)
        limit = tool_input.get("limit")
        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            return result
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
            return result
        end = start + limit - 1 if limit is not None else None
        response = payload.get("tool_response")
        file = response.get("file") if isinstance(response, dict) else None
        if isinstance(file, dict):
            if file.get("startLine", start) != start:
                return result
            if file.get("truncatedByTokenCap") is True:
                content = file.get("content")
                fields = document_reads.returned_texts(content)
                count = (
                    max((len(text.splitlines()) for text in fields), default=0)
                    if isinstance(content, str)
                    else sum(len(text.splitlines()) for text in fields)
                )
                end = min(end, start + count - 1) if end is not None else start + count - 1
        return {str(path): [(start, end, order)] for order, path in enumerate(_read_paths(tool_name, tool_input, cwd))}
    command = tool_input.get("command")
    if tool_name != "Bash" or not isinstance(command, str):
        return result
    order = 0
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        if not invocation.execution_known or not invocation.arguments_known or invocation.captured or invocation.output_pipe:
            continue
        tokens = invocation.segment.tokens
        selected = sed_read_range(tokens)
        if selected is not None:
            operand, start, end = selected
            operands = [operand]
        else:
            plans = codex_read_observation.read_plan(join_words(tokens))
            if len(plans) != 1 or plans[0].argv[0] not in {"cat", "head", "tail"}:
                continue
            plan = plans[0]
            operands = [tokens[index] for index in plan.operands]
            start, end = 1, None
            if plan.argv[0] in {"head", "tail"}:
                options = plan.argv[1 : plan.operands[0]]
                count = (
                    int(options[1])
                    if options[:1] == ("-n",)
                    else int(options[0][2:])
                    if options and options[0].startswith("-n")
                    else 10
                )
                if count < 1:
                    continue
                if plan.argv[0] == "head":
                    end = count
                else:
                    start = -count
        for operand in operands:
            if is_dynamic_read_operand(operand):
                continue
            path = pathlib.Path(operand).expanduser()
            if not path.is_absolute():
                if not current.resolved:
                    continue
                path = pathlib.Path(current.path) / path
            if not path.is_file():
                # 前段の生成は実行前には確定できない。PostToolUseで実在と実配送を観測する。
                continue
            result.setdefault(str(path.resolve()), []).append((start, end, order))
            order += 1
    return result


def _record_codex_delivery(
    session_id: str, agent: str, candidates: list[dict], texts: list[str], *, project: str | None
) -> None:
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    pending = read_delivery.read_state(session_id).get("document_read_pending", {}).get(agent, [])
    frames = {packet["observation"]: packet["produced"] for packet in pending if packet.get("produced")}
    json_fields = {packet["observation"]: packet.get("json_fields", []) for packet in pending if packet.get("produced")}
    tail_fields = {packet["observation"]: packet.get("json_tail_fields", []) for packet in pending if packet.get("produced")}
    embedded = len({packet["delivery"] for packet in pending}) == 1
    choices: dict[str, list[tuple[int, dict, list[dict]]]] = {}
    for packet in pending:
        # 異なる応答の包含関係がある場合は、他の応答の部分を流用しない。
        count, blocks = document_reads.delivered_fragments(
            packet, texts, produced=frames.get(packet["observation"]) if embedded else None
        )
        if count:
            choices.setdefault(packet["delivery"], []).append((count, packet, blocks))
    selected: list[tuple[dict, list[dict]]] = []
    for possibilities in choices.values():
        observations: dict[str, list[tuple]] = {}
        for _count, packet, blocks in possibilities:
            observations.setdefault(packet["observation"], []).append(
                (packet["source"]["logical"], packet["source"]["version"], json.dumps(blocks, sort_keys=True))
            )
        identities = {tuple(sorted(items)) for items in observations.values()}
        # 同文の異なる要求位置には、それぞれに対応する実配送が必要。
        if possibilities[0][0] < len(identities):
            continue
        selected.extend((packet, blocks) for _count, packet, blocks in possibilities)
    for packet in pending:
        if any(packet is item[0] for item in selected):
            continue
        blocks = document_reads.unambiguous_fragments(packet, pending, texts)
        if blocks:
            selected.append((packet, blocks))
    if not all(any(packet is item and blocks == packet["blocks"] for item, blocks in selected) for packet in pending):
        selected.extend(document_reads.partial_fragment_deliveries(pending, texts))
    coverage = document_reads.fragment_coverage(pending)
    _record_receipts(session_id, agent, candidates, texts, fragments=selected, coverage=coverage, host="codex", project=project)
    if selected:

        def advance(value: dict) -> dict:
            queue = value.get("document_read_pending", {}).get(agent, [])
            for packet, _blocks in selected:
                if packet in queue:
                    queue.remove(packet)
            # 同じ生産観測の未配送資料が残る間は、共有する出力索引を1件だけ保つ。
            for observation, produced in frames.items():
                remaining = [item for item in queue if item["observation"] == observation]
                if remaining and not any(item.get("produced") for item in remaining):
                    remaining[0]["produced"] = produced
                    remaining[0]["json_fields"] = json_fields[observation]
                    remaining[0]["json_tail_fields"] = tail_fields[observation]
            return value

        read_delivery.update_state(session_id, advance)


def _refresh_receipts(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    """Preで既知範囲のdigestを準備し、Codexの外側配送後の出力だけを差分で観測する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return
    agent = agent_id.resolve_hook_agent_id(payload)
    state = read_delivery.read_state(session_id)
    candidates = state.get("document_read_candidates", {}).get(agent, [])
    current = _receipt_candidates(payload, tool_name, tool_input, session_id)
    if is_codex_payload(payload):
        raw_path = payload.get("agent_transcript_path") if agent != "main" else payload.get("transcript_path")
        if isinstance(raw_path, str) and raw_path:
            path = pathlib.Path(raw_path)
            cursor_key = json.dumps([agent, str(path)])
            offset = state.get("document_read_offsets", {}).get(cursor_key, 0)
            rotated = False
            texts = []
            try:
                with path.open("rb") as stream:
                    if cursor_key not in state.get("document_read_offsets", {}) and agent in state.get(
                        rules_context.OPERATION_SKILL_READY_KEY, {}
                    ).get(rules_context.READ_CONTEXT_RESET_KEY, []):
                        offset = path.stat().st_size
                    if offset > path.stat().st_size:
                        # 切り替わった記録の旧履歴を再受信として扱わない。
                        offset = path.stat().st_size
                        rotated = True
                    stream.seek(offset)
                    for raw in stream:
                        if not raw.endswith(b"\n"):
                            break
                        offset += len(raw)
                        try:
                            entry = json.loads(raw)
                        except ValueError:
                            continue
                        item = entry.get("payload") if isinstance(entry, dict) else None
                        if (
                            isinstance(entry, dict)
                            and entry.get("type") == "response_item"
                            and isinstance(item, dict)
                            and item.get("type") in {"custom_tool_call_output", "function_call_output"}
                        ):
                            output = item.get("output")
                            if isinstance(output, str):
                                texts.append([output])
                            elif isinstance(output, list):
                                texts.append(
                                    [
                                        block["text"]
                                        for block in output
                                        if isinstance(block, dict)
                                        and block.get("type") in {"text", "input_text", "output_text"}
                                        and isinstance(block.get("text"), str)
                                    ]
                                )
            except OSError:
                texts = []
            else:
                for text in texts:
                    _record_codex_delivery(session_id, agent, [*candidates, *current], text, project=payload.get("cwd"))
                    from agent_toolkit._hooks import result_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

                    _select_result_documents(
                        session_id,
                        agent,
                        result_reads.observe_outer(session_id, agent, text),
                        host="codex",
                        project=payload.get("cwd"),
                    )

                def advance(value: dict) -> dict:
                    positions = value.setdefault("document_read_offsets", {})
                    positions[cursor_key] = offset if rotated else max(positions.get(cursor_key, 0), offset)
                    if texts:
                        # 観測済みの外側出力へ結び付けられなかった候補を、次の読取へ流用しない。
                        queue = value.get("document_read_pending", {}).get(agent, [])
                        for packet in state.get("document_read_pending", {}).get(agent, []):
                            if packet in queue:
                                queue.remove(packet)
                    return value

                read_delivery.update_state(session_id, advance)
    if current and current != candidates:

        def prepare(value: dict) -> dict:
            value.setdefault("document_read_candidates", {})[agent] = current
            return value

        read_delivery.update_state(session_id, prepare)
