"""委譲先へ継続の入力を送る必要が生じた委譲を、事象が帰属するturnのモデル・effortと役割別に数える。  pyfltr: allow-comment

週次のメタ分析で工程別の既定のモデル・effortを選び直す材料にするための読み取り専用の集計であり、
閾値の判定や警告は持たない。
会話の本文と識別子は出力せず、件数・母数・欠測だけを返す。
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import json
import pathlib
import re
import socket
from typing import Any

from agent_toolkit._agents_server.codex_providers import CONTINUE_PROMPT as _CODEX_PROVIDER_CONTINUE
from agent_toolkit._agents_server.result_contract import _VALID_CLASSIFICATIONS, _visible_result_lines, is_wait_statement
from agent_toolkit._common import session_record_paths, session_registry, task_documents
from agent_toolkit._common.delegated_session import FINAL_RESPONSE_NOTICE
from agent_toolkit._common.message_format import delegation_delivery_body
from agent_toolkit._common.runtime_identity import observed_model
from agent_toolkit._common.session_family import _codex_text_blocks, _text_blocks
from session_evidence_stats import _parse_cli_timestamp

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 自動の回復の文面は`manager_resume.py`が送る本文の先頭で、試験が原本との一致を確かめる。  # pyfltr: allow-comment
# 作業を残した返却への回復は区分を持たずどちらかの文面で届くため、別に数えられない。  # pyfltr: allow-comment
AUTO_FORMAT_PREFIX = "直前の返却は返却形式の契約に適合しない。"
AUTO_RECHECK_PREFIX = "直前の不足申告の前提を一度再確認する。"
AUTO_RECOVERY_MARKER = "担当内で補える作業を完遂した上で所定の返却形式を返せ。"
# 障害や待機の解除による自動の再開は、委譲先が途中で返した事象ではないため継続に数えない。  # pyfltr: allow-comment
CHILD_RESUME_PREFIX = "あなたが起動した子は終了した。"
INFRA_RESUME_PATTERN = re.compile(
    r"\A(?:直前のturnはモデルの過負荷|直前のturnは\S+の利用上限|"
    r"直前のturnはClaudeの接続の失敗|" + re.escape(_CODEX_PROVIDER_CONTINUE) + ")"
)
# 完了を示した返却の後の追送は、継続を求める語を含む時だけ新しい作業と区別できない。  # pyfltr: allow-comment
CONTINUATION_CUE = re.compile(r"続け|続行|完遂|再開|最後まで|残り|残工程|未完了|途中")
_CUE_NEGATION = re.compile(r"(?:続け|続行|再開)(?:せず|ず|ないで|しないで|不要)|だけ教えて|状況だけ|進捗だけ")
# 作業契約の更新を伴う追送は、記録した原文の後に更新の通知が付いて届く。  # pyfltr: allow-comment
_CONTRACT_UPDATE_OPENING = '\n\n<atk-auto source="agents-server" kind="work-contract-update">'
_SUCCESS_LINE = "状態: completed"
_CLASSIFICATION_PREFIX = "続行できない区分:"
_REASON_PREFIX = "続行できない理由:"
# 判断・入力・認可か外部の待ちの解除を求めた返却への追送は、途中で止まった事象ではない。  # pyfltr: allow-comment
_ANSWER_CLASSIFICATIONS = frozenset({"user_decision", "owner_input", "authorization", "external_wait"})

CONTINUATION_REASONS = ("auto_format", "auto_insufficiency_recheck", "auto_unknown", "owner_continue")
UNCOUNTED_KINDS = (
    "after_wait",
    "declared_stage",
    "answer",
    "new_work_or_inquiry",
    "infra_resume",
    "duplicate_for_same_return",
)


@dataclasses.dataclass
class Turn:
    delivery: str | None
    model: str | None
    effort: str | None
    final: str | None = None
    seconds: float | None = None


@dataclasses.dataclass(frozen=True)
class Role:
    name: str
    task: bool = False
    instructions: frozenset[str] = frozenset()
    result_heads: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Delegation:
    engine: str
    model_type: str | None
    role: Role
    followups: tuple[str, ...]
    turns: tuple[Turn, ...]


def _role(info: session_registry.ResumeInfo) -> Role:
    document = task_documents.launched_task_document(info.launch_info.prompt)
    if document is None:
        return Role(info.launch_kind)
    name = document.name.removesuffix(task_documents.TASK_DOCUMENT_SUFFIX)
    # 起動時の配布物が後始末で消えた委譲は、同じ役割名の現行の文書で照合する。  # pyfltr: allow-comment
    if not document.is_file():
        document = task_documents.find_role_document(_PLUGIN_ROOT, document.name) or document
    try:
        declaration = task_documents.read_declaration(document)
    except OSError, UnicodeError, ValueError:
        return Role(name, task=True)
    if isinstance(declaration, str):
        return Role(name, task=True)
    return Role(
        name,
        task=True,
        instructions=frozenset(item.instruction for item in declaration.continuations),
        result_heads=declaration.result_heads,
    )


def return_state(text: str | None, role: Role) -> str:
    if not text or not text.strip():
        return "missing"
    if is_wait_statement(text):
        return "wait"
    visible = _visible_result_lines(text)
    lines = [line.strip() for line in visible if line.strip()]
    if not lines:
        return "unknown"
    classes = [
        line.strip().removeprefix(_CLASSIFICATION_PREFIX).strip().strip("`")
        for line, following in zip(visible, visible[1:], strict=False)
        if _paired_reason(line, following)
    ]
    if classes:
        # 区分の値は返却本文の一部のため、出力へは定義済みの値だけを通す。  # pyfltr: allow-comment
        return "cannot_continue:" + (classes[0] if classes[0] in _VALID_CLASSIFICATIONS else "invalid")
    if _SUCCESS_LINE in (lines[0], lines[-1]) or any(lines[0].startswith(head) for head in role.result_heads):
        return "completed"
    # 役割の結果の固定行は版ごとに変わるため、照合できなくても未完了と断定しない。  # pyfltr: allow-comment
    return "unknown" if role.task or role.name == "shell" else "no_success_line"


def _paired_reason(line: str, following: str) -> bool:
    indent = line[: len(line) - len(line.lstrip())]
    return (
        line.startswith(indent + _CLASSIFICATION_PREFIX)
        and following.startswith(indent + _REASON_PREFIX)
        and bool(following.removeprefix(indent + _REASON_PREFIX).strip())
    )


def delivered_text(body: str) -> str:
    head, opening, notice = body.rpartition(_CONTRACT_UPDATE_OPENING)
    if opening and notice.rstrip().endswith("</atk-auto>") and "<atk-auto" not in notice:
        return head
    return body


def _asks_to_continue(body: str) -> bool:
    return CONTINUATION_CUE.search(body) is not None and _CUE_NEGATION.search(body) is None


def classify_input(body: str, previous: str | None, delegation: Delegation, *, occurrences: int = 1) -> str:
    """`occurrences`は同じ本文で始まるturnの数で、追送の記録より多ければ出所を決められない。  pyfltr: allow-comment"""
    state = return_state(previous, delegation.role)
    if state == "wait" or body.startswith(CHILD_RESUME_PREFIX):
        return "after_wait"
    body = delivered_text(body)
    recorded = delegation.followups.count(body)
    if 0 < recorded < occurrences:
        return "unidentified"
    if recorded:
        first = body.partition("\n")[0].strip().removesuffix("。")
        if first in delegation.role.instructions:
            return "declared_stage"
        if state == "completed":
            return "unidentified" if CONTINUATION_CUE.search(body) else "new_work_or_inquiry"
        if state.removeprefix("cannot_continue:") in _ANSWER_CLASSIFICATIONS:
            return "answer"
        # 未完了の返却の後でも照会などがあるため、継続を求める語がある時だけ継続指示とする。  # pyfltr: allow-comment
        if state.startswith("cannot_continue:") or state == "no_success_line":
            return "owner_continue" if _asks_to_continue(body) else "unidentified"
        return "unidentified"
    if body.startswith(AUTO_FORMAT_PREFIX):
        return "auto_format"
    if body.startswith(AUTO_RECHECK_PREFIX):
        return "auto_insufficiency_recheck"
    if INFRA_RESUME_PATTERN.match(body):
        return "infra_resume"
    if AUTO_RECOVERY_MARKER in body and body.rstrip().endswith(FINAL_RESPONSE_NOTICE):
        return "auto_unknown"
    return "unidentified"


def _codex_turns(path: pathlib.Path) -> list[Turn]:
    turns: list[Turn] = []
    with path.open("rb") as stream:
        for raw in stream:
            if not any(key in raw for key in (b"task_started", b"turn_context", b"task_complete", b'"user"')):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            payload = entry.get("payload") if isinstance(entry, dict) else None
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if entry.get("type") == "event_msg" and kind == "task_started":
                if payload.get("root_turn_id", payload.get("turn_id")) == payload.get("turn_id"):
                    turns.append(Turn(None, None, None))
            elif not turns:
                continue
            elif entry.get("type") == "turn_context":
                turns[-1].model = observed_model(entry, "codex")
                turns[-1].effort = payload.get("effort") if isinstance(payload.get("effort"), str) else None
            elif entry.get("type") == "response_item" and kind == "message" and payload.get("role") == "user":
                body = delegation_delivery_body("\n".join(_codex_text_blocks(payload.get("content"))))
                if body is not None and turns[-1].delivery is None:
                    turns[-1].delivery = body
            elif entry.get("type") == "event_msg" and kind == "task_complete":
                message = payload.get("last_agent_message")
                turns[-1].final = message if isinstance(message, str) else None
                duration = payload.get("duration_ms")
                if isinstance(duration, (int, float, str)) and str(duration).isdigit():
                    turns[-1].seconds = int(duration) / 1000
    return turns


def _claude_turns(path: pathlib.Path, turn_seq: int) -> list[Turn] | None:
    """配送ごとの区間を、前の区間の終わりの観測で新しいturnか実行中の追加指示へ分ける。  pyfltr: allow-comment

    終わりの観測は直前のassistant行の`end_turn`の停止理由かAPIエラーの行とする。
    道具の結果を待つ間とその直後の配送は追加指示とし、どちらの観測も無い配送はturn数が合う側へ寄せる。
    どちらに寄せてもturn数が合わなければ境界を確定できないとしてNoneを返す。
    道具を待つ間に終わったturnの後の配送は追加指示と区別できないため、turn数が合わない限り除外される。
    """
    segments: list[tuple[Turn, str]] = []
    pending_tools: set[str] = set()
    after_tool_result = False
    ended = False
    with path.open("rb") as stream:
        for raw in stream:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            message = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(message, dict) or entry.get("isSidechain") is True:
                continue
            content = message.get("content")
            blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
            if entry.get("type") == "user":
                results = {str(block.get("tool_use_id")) for block in blocks if block.get("type") == "tool_result"}
                if results:
                    pending_tools -= results
                    after_tool_result = True
                    continue
                body = delegation_delivery_body("\n".join(_text_blocks(content)))
                if body is None:
                    continue
                if not segments or ended:
                    kind = "start"
                elif pending_tools or after_tool_result:
                    kind = "steer"
                else:
                    kind = "unknown"
                segments.append((Turn(body, None, None), kind))
                if kind != "steer":
                    ended, pending_tools, after_tool_result = False, set(), False
            elif entry.get("type") == "assistant" and segments:
                after_tool_result = False
                pending_tools |= {str(block.get("id")) for block in blocks if block.get("type") == "tool_use"}
                model = observed_model(entry, "claude")
                if entry.get("isApiErrorMessage") is True:
                    ended = True
                elif model is not None:
                    # 停止理由を持たない合成の通知行は、観測済みの終わりを取り消さない。  # pyfltr: allow-comment
                    ended = message.get("stop_reason") == "end_turn"
                if model is not None:
                    effort = entry.get("effort")
                    segments[-1][0].model = model
                    segments[-1][0].effort = effort if isinstance(effort, str) and effort else None
    starts = sum(kind == "start" for _turn, kind in segments)
    unknown = sum(kind == "unknown" for _turn, kind in segments)
    if starts == turn_seq:
        new_turn = {"start"}
    elif starts + unknown == turn_seq:
        new_turn = {"start", "unknown"}
    else:
        return None
    turns: list[Turn] = []
    for turn, kind in segments:
        if kind in new_turn:
            turns.append(turn)
        elif turn.model is not None:
            turns[-1].model, turns[-1].effort = turn.model, turn.effort
    return turns


def launched_at(session_id: str) -> tuple[session_registry.ResumeInfo | None, datetime.datetime | None]:
    try:
        resolution = session_registry.resolve(session_id)
    except OSError, ValueError:
        return None, None
    info = resolution.resume_info or resolution.handoff_info
    if info is None or info.launch_info.created_at is None:
        return info, None
    try:
        return info, _parse_cli_timestamp(info.launch_info.created_at)
    except ValueError:
        return info, None


def load_delegation(
    session_id: str,
    info: session_registry.ResumeInfo,
    *,
    codex_home: pathlib.Path | None = None,
    claude_home: pathlib.Path | None = None,
) -> tuple[Delegation | None, str | None]:
    if info.engine not in {"codex", "claude"}:
        return None, f"unsupported_engine:{info.engine}"
    record = session_record_paths.find_session_record(session_id, codex_home=codex_home, claude_home=claude_home)
    if record is None or record.engine != info.engine or len(record.paths) != 1:
        return None, "record_unresolved"
    try:
        turns = _codex_turns(record.paths[0]) if info.engine == "codex" else _claude_turns(record.paths[0], info.turn_seq) or []
    except OSError:
        return None, "record_unreadable"
    # 実行中の追加指示や記録の欠けでturn境界を確定できない委譲は、入力を取り違えないよう母数から外す。  # pyfltr: allow-comment
    if len(turns) != info.turn_seq or not turns or turns[0].delivery is None:
        return None, "turn_boundary_unresolved"
    finals = dict(info.launch_info.final_responses)
    for index, turn in enumerate(turns, 1):
        turn.final = finals.get(index, turn.final)
    delegation = Delegation(info.engine, info.model_type, _role(info), info.launch_info.followup_prompts, tuple(turns))
    return delegation, None


def _group_key(delegation: Delegation, turn: Turn) -> tuple[str, str | None, str | None, str | None, str]:
    return delegation.engine, turn.model, turn.effort, delegation.model_type, delegation.role.name


def _empty_group() -> dict[str, Any]:
    return {
        "delegations": 0,
        "returns": 0,
        "final_returns": 0,
        "delegations_with_continuation": 0,
        "continuations": dict.fromkeys(CONTINUATION_REASONS, 0),
        "unidentified": 0,
        "uncounted": dict.fromkeys(UNCOUNTED_KINDS, 0),
        "final_without_continuation": collections.Counter(),
        "final_incomplete_with_unidentified_input": 0,
        "final_state_unknown": 0,
        "continuation_turn_seconds": 0.0,
        "continuation_turn_seconds_missing": 0,
    }


def tally(delegations: list[Delegation]) -> tuple[dict[tuple, dict[str, Any]], dict[str, int]]:
    """継続は直前の返却、継続なしの未完了は最終の返却のturnの値で数える。  pyfltr: allow-comment

    組の値に加え、モデルやeffortが途中で変わった委譲を重ねて数えない委譲単位の件数を返す。
    継続の有無を判別できない入力を持つ委譲は、継続なしと断定せず別に数える。
    """
    groups: dict[tuple, dict[str, Any]] = collections.defaultdict(_empty_group)
    summary = dict.fromkeys(
        (
            "delegations_with_continuation",
            "delegations_incomplete_without_continuation",
            "delegations_incomplete_with_unidentified_input",
            "delegations_without_return",
            "delegations_final_state_unknown",
        ),
        0,
    )
    for delegation in delegations:
        continued: set[tuple] = set()
        touched: set[tuple] = set()
        unidentified = False
        consumed_returns: set[int] = set()
        last_return: int | None = None
        starts = collections.Counter(delivered_text(turn.delivery) for turn in delegation.turns[1:] if turn.delivery)
        for index, turn in enumerate(delegation.turns):
            if index and turn.delivery is not None and last_return is not None:
                previous = delegation.turns[last_return]
                key = _group_key(delegation, previous)
                kind = classify_input(
                    turn.delivery, previous.final, delegation, occurrences=starts[delivered_text(turn.delivery)]
                )
                if kind in CONTINUATION_REASONS and last_return in consumed_returns:
                    kind = "duplicate_for_same_return"
                group = groups[key]
                if kind in CONTINUATION_REASONS:
                    consumed_returns.add(last_return)
                    group["continuations"][kind] += 1
                    continued.add(key)
                    if turn.seconds is None:
                        group["continuation_turn_seconds_missing"] += 1
                    else:
                        group["continuation_turn_seconds"] += turn.seconds
                elif kind == "unidentified":
                    group["unidentified"] += 1
                    unidentified = True
                else:
                    group["uncounted"][kind] += 1
            if turn.final is not None and turn.final.strip():
                groups[_group_key(delegation, turn)]["returns"] += 1
                touched.add(_group_key(delegation, turn))
                last_return = index
        for key in continued:
            groups[key]["delegations_with_continuation"] += 1
        for key in touched:
            groups[key]["delegations"] += 1
        summary["delegations_with_continuation"] += bool(continued)
        if last_return is None:
            summary["delegations_without_return"] += 1
            continue
        final_turn = delegation.turns[last_return]
        final_group = groups[_group_key(delegation, final_turn)]
        final_group["final_returns"] += 1
        state = return_state(final_turn.final, delegation.role)
        if continued or state in {"completed", "wait"}:
            continue
        if state in {"unknown", "missing"}:
            summary["delegations_final_state_unknown"] += 1
            final_group["final_state_unknown"] += 1
        elif unidentified:
            summary["delegations_incomplete_with_unidentified_input"] += 1
            final_group["final_incomplete_with_unidentified_input"] += 1
        else:
            final_group["final_without_continuation"][state] += 1
            summary["delegations_incomplete_without_continuation"] += 1
    return groups, summary


def _period(value: str, *, end: bool) -> datetime.datetime:
    moment = _parse_cli_timestamp(value)
    if end and len(value) == 10:
        moment = _parse_cli_timestamp((datetime.date.fromisoformat(value) + datetime.timedelta(days=1)).isoformat())
    return moment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "agents_serverの登録簿から解決できた委譲先の記録で、継続の入力を送った委譲と継続なしの未完了を、"
            "帰属するturnのengine・model・effort・model_type・役割別に数える。本文と識別子は出力しない。"
        ),
        epilog=(
            "continuations: auto_format=返却形式の不適合への自動の回復、auto_insufficiency_recheck=不足の申告の再確認、"
            "auto_unknown=理由を文面から判別できない自動の回復、owner_continue=未完了の返却への委譲元の継続指示。"
            "unidentifiedは出所か目的を判別できない入力。uncountedは継続に数えない入力で、after_wait=待機の後、"
            "declared_stage=役割の定型の工程継続、answer=判断・入力・認可・外部の待ちを求めた返却への回答、"
            "new_work_or_inquiry=完了の後の新しい依頼や照会、infra_resume=障害の後の自動の再開、"
            "duplicate_for_same_return=同じ返却への2回目以降の継続。継続は直前の返却、継続なしの未完了は最終の返却のturnに帰属させる。"
            "modelとeffortは記録の観測値（Codexはturn_context、Claudeはassistant行）だけを使い、無ければnullの欠測とする。"
        ),
    )
    parser.add_argument("--since", required=True, help="起動日時の開始。YYYY-MM-DDかISO 8601日時。開始を含む。")
    parser.add_argument("--until", required=True, help="起動日時の終了日（その日を含む）またはISO 8601日時（終了を含まない）。")
    args = parser.parse_args(argv)
    try:
        since, until = _period(args.since, end=False), _period(args.until, end=True)
        if since >= until:
            raise ValueError("開始は終了より前を指定する")
    except ValueError, OverflowError:
        parser.error("範囲をYYYY-MM-DDかISO 8601日時で指定する。開始は終了より前を指定する。")
    excluded: collections.Counter[str] = collections.Counter()
    delegations: list[Delegation] = []
    registered = undated = 0
    for path in sorted(session_registry.registry_directory().glob("*.json")):
        info, launched = launched_at(path.stem)
        if launched is None:
            undated += 1
            continue
        if not since <= launched < until:
            continue
        registered += 1
        assert info is not None
        delegation, reason = load_delegation(path.stem, info)
        if delegation is None:
            excluded[reason or "unknown"] += 1
        else:
            delegations.append(delegation)
    groups, summary = tally(delegations)
    rows = [
        {
            "engine": key[0],
            "model": key[1],
            "effort": key[2],
            "model_type": key[3],
            "role": key[4],
            **{name: dict(value) if isinstance(value, collections.Counter) else value for name, value in group.items()},
        }
        for key, group in sorted(groups.items(), key=lambda item: tuple(str(part) for part in item[0]))
    ]
    print(
        json.dumps(
            {
                "host": socket.gethostname(),
                "since": since.isoformat(),
                "until_exclusive": until.isoformat(),
                "registered_in_period": registered,
                "undated_registrations": undated,
                "analyzed_delegations": len(delegations),
                "excluded": dict(sorted(excluded.items())),
                **summary,
                "unattributed_values": "modelとeffortのnullは該当turnの記録に観測値が無い欠測",
                "acceptance": "欠測（登録簿と委譲先の記録に委譲元の受け入れが残らない）",
                "tokens": "全件欠測（turnごとに集計していない）",
                "groups": rows,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
