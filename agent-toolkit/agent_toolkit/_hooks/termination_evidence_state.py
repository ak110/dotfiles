"""終了工程の作業状態と入力の保持。"""

from __future__ import annotations

import json
import pathlib
lazy from typing import Any

lazy from agent_toolkit._atk.wi import uwi_scan
lazy from agent_toolkit._common import automated_prompt, runtime_inserted, session_state
lazy from agent_toolkit._hooks import agent_id, agents_server_observations
lazy from agent_toolkit._hooks.stop_session import append_stop_log

STATE_KEY = "termination_evidence"


COMPLETION_REPORT = "report-completion"


"""起動証拠を確かめるスキルの短縮名。"""


TARGET_SKILLS = ("process-wi", "add-awi-by-user")


"""開始した時点で成果作業となるスキルの短縮名。適用条件は`report-completion/SKILL.md`「工程」の工程9に従う。"""


_TERMINATION_EVIDENCE_REFERENCE = "`agent-toolkit:report-completion`の`references/termination-evidence.md`"


_UNRESOLVED_REPOSITORY_FIX = (
    "この作業では省略を記録できない。`agent-toolkit:report-completion`を起動して終了工程を進めよ。"
    f"規範がその起動を禁じる場合は、{_TERMINATION_EVIDENCE_REFERENCE}「終了方法の分岐」に従い、その旨をユーザーへ報告せよ。"
)


class _UnresolvedRepositoryError(ValueError):
    """所属不明の作業には、省略の再申請と異なる終了操作を案内する。"""


class _FailureEvidenceError(ValueError):
    """実際の失敗呼び出しを欠く技術的不成立を表す。"""


class _InactiveWaitError(ValueError):
    """所有する委譲先への有効な待機が終わっていることを表す。"""


_HOST_STOP_FIX = f"技術的不成立の判定と確認手段は{_TERMINATION_EVIDENCE_REFERENCE}「技術的不成立の判定」に従え。"


_GIT_PUSH_VALUE_OPTIONS = frozenset(
    {
        "--repo",
        "--receive-pack",
        "--exec",
        "-o",
        "--push-option",
    }
)


"""`git push`の値を伴うオプション。値のトークンを実行抑止の判定から除く。"""


def _parse(payload_text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        return None
    return payload if agent_id.is_main_agent_context(payload) else None


def _data(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    current = state.get(STATE_KEY)
    if (
        not isinstance(current, dict)
        or current.get("session_id") != session_id
        or current.get("version") != 1
        or not all(isinstance(current.get(key), dict) for key in ("works", "calls", "inputs"))
        or not isinstance(current.get("sequence"), int)
    ):
        if current is not None:
            append_stop_log(session_id, "termination_evidence_unavailable", {"reason": "状態の版または形式が異なる"})
        current = {"version": 1, "session_id": session_id, "sequence": 0, "works": {}, "calls": {}, "inputs": {}}
        state[STATE_KEY] = current
    return current


def _compact(data: dict[str, Any]) -> None:
    """現在の証拠と遅延応答に必要な参照だけを保持する。"""
    referenced: set[str] = {data.get("last_input", ""), data.get("last_human_input", "")}
    for work in data["works"].values():
        if work.get("publish_scope") or work.get("completion_report"):
            work.pop("edits", None)
        referenced.add(work.get("publish_scope", {}).get("input_id", ""))
        decisions = work.get("decisions", [])
        if decisions:
            work["decisions"] = decisions[-1:]
            referenced.add(decisions[-1].get("input_id", ""))
        referenced.add(work["reference"])
        retained = set(work.get("async_targets", {}).values())
        if decisions:
            retained.add(decisions[-1].get("call_id", ""))
        retained.add(work.get("failure_call", ""))
        for call_id in list(work["calls"]):
            records = data["calls"].get(call_id, [])
            # 所属不明の操作の種類を省略拒否へ示すため、起動証拠を得るまで既存の呼出し記録を保つ。
            unresolved = any(record.get("kind") == "deliverable" and record.get("repository") is None for record in records)
            if unresolved and not work.get("completion_report"):
                continue
            if call_id not in retained and all(record.get("observed") for record in records):
                data["calls"].pop(call_id, None)
                work["calls"].remove(call_id)
    data["inputs"] = {key: value for key, value in data["inputs"].items() if key in referenced}


def _new_work(data: dict[str, Any], reference: str) -> tuple[str, dict[str, Any]]:
    data["sequence"] += 1
    work_id = f"work-{data['sequence']}"
    work = {
        "reference": reference,
        "calls": [],
        "decisions": [],
        "deliverables": [],
        "async_targets": {},
        "offset": data["inputs"].get(reference, {}).get("offset", 0),
        "input_at_last_call": data.get("last_human_input", data.get("last_input")),
    }
    data["works"][work_id] = work
    data["current_work"] = work_id
    return work_id, work


def _current_work(data: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    work_id = data.get("current_work")
    work = data["works"].get(work_id)
    return (work_id, work) if isinstance(work_id, str) and isinstance(work, dict) else None


def _last_decision(work: dict[str, Any]) -> str:
    decisions = work.get("decisions", [])
    return decisions[-1]["action"] if decisions else ""


def _finished(data: dict[str, Any], work: dict[str, Any]) -> bool:
    """起動・省略の証拠を得た作業の後に新しい入力が届いたかを返す。"""
    return bool(work.get("completion_report") or work.get("omit_completion")) and data.get(
        "last_human_input", data.get("last_input")
    ) != work.get("input_at_last_call")


def _position(payload: dict[str, Any]) -> int:
    path = payload.get("transcript_path")
    try:
        return pathlib.Path(path).stat().st_size if isinstance(path, str) else 0
    except OSError:
        return 0


def _store_input(data: dict[str, Any], reference: str, text: str, *, human: bool, offset: int) -> None:
    """入力を1件の事象として保存し、直近の入力と人間入力の参照を進める。

    Claude Codeの`prompt_id`は人間の入力と同じターンに届くtask-notificationなどにも付くため、事象の一意キーにならない。
    同じキーに本文か由来の異なる入力が既にある場合は別のキーで保存し、人間入力の本文と由来を書き換えない。
    同じ入力を重ねて観測した場合は同じキーを使う。
    """
    existing = data["inputs"].get(reference)
    if isinstance(existing, dict) and (existing.get("text") != text or existing.get("human") is not human):
        data["sequence"] += 1
        reference = f"{reference}#{data['sequence']}"
    data["inputs"][reference] = {"text": text, "human": human, "offset": offset}
    data["last_input"] = reference
    if human:
        data["last_human_input"] = reference


def _sync_queued_inputs(data: dict[str, Any], transcript_path: object) -> None:
    """Claude Codeのセッション記録に`queued_command`として届いた入力を、前回の読取位置から取り込む。

    ターンの途中で人間が送った指示はUserPromptSubmitを経ずに`attachment.type=queued_command`として記録される。
    人間の入力は記録の`origin.kind=human`で判定し、通知や他エージェントの配送は非人間入力として保存する。
    """
    if isinstance(transcript_path, str) and transcript_path and transcript_path != data.get("transcript_path"):
        # 読取位置は記録ファイルごとの値のため、別の記録へ移ったら先頭から読む。
        data["transcript_path"] = transcript_path
        data["queued_offset"] = 0
    path = data.get("transcript_path")
    if not isinstance(path, str) or not path:
        return
    start = data.get("queued_offset", 0)
    start = start if isinstance(start, int) and not isinstance(start, bool) and start >= 0 else 0
    queued: list[tuple[int, bytes]] = []
    position = start
    try:
        with pathlib.Path(path).open("rb") as stream:
            stream.seek(start)
            for raw in stream:
                # 書込途中の最終行は次回に読む。
                if not raw.endswith(b"\n"):
                    break
                position += len(raw)
                if b'"queued_command"' in raw:
                    queued.append((position, raw))
    except OSError:
        return
    for offset, raw in queued:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        attachment = entry.get("attachment") if isinstance(entry, dict) and entry.get("type") == "attachment" else None
        if not isinstance(attachment, dict) or attachment.get("type") != "queued_command" or entry.get("isSidechain"):
            continue
        prompt = attachment.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            continue
        origin = attachment.get("origin")
        human = (
            isinstance(origin, dict)
            and origin.get("kind") == "human"
            and not runtime_inserted.is_runtime_inserted_text(prompt)
            and not automated_prompt.contains(prompt)
        )
        uuid = entry.get("uuid")
        reference = f"queued-{uuid}" if isinstance(uuid, str) and uuid else f"queued-{offset}"
        _store_input(data, reference, prompt, human=human, offset=offset)
    data["queued_offset"] = position


def evidence_available(payload: dict[str, Any]) -> bool:
    """現在の会話の証拠を読み取れるかを返す。別会話の継承値と旧版の状態は読み取れないとして記録する。"""
    data = session_state.read_state(payload["session_id"]).get(STATE_KEY)
    if data is None:
        return True
    if isinstance(data, dict) and data.get("session_id") == payload["session_id"] and data.get("version") == 1:
        return True
    append_stop_log(payload["session_id"], "termination_evidence_unavailable", {"reason": "状態の版または形式が異なる"})
    return False


def _session_works(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    """現在の会話が記録した作業を返し、別会話の継承値と旧版の状態は空とする。"""
    data = state.get(STATE_KEY)
    if not isinstance(data, dict) or data.get("session_id") != session_id or data.get("version") != 1:
        return {}
    works = data.get("works")
    return works if isinstance(works, dict) else {}


def _async_target_alive(state: dict[str, Any], work: dict[str, Any], target: object) -> bool:
    """作業が起動して所有する委譲先が、実行中か未回収の結果を持つ場合に真を返す。"""
    child = agents_server_observations.session_record(state, target)
    return (
        isinstance(target, str)
        and target in work.get("async_targets", {})
        and isinstance(child, dict)
        and child.get("owner_agent_id") == "main"
        and (
            child.get("pending_observation") is True
            or target in agents_server_observations.actively_waited_session_ids([target])
        )
    )


def session_works(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """現在の会話の全作業を、中止・置換した作業も含めて記録順に返す。"""
    works = _session_works(session_state.read_state(payload["session_id"]), payload["session_id"])
    return [(work_id, work) for work_id, work in works.items() if isinstance(work, dict)]


def waits_on_delegated_session(payload: dict[str, Any], work: dict[str, Any]) -> bool:
    """作業が起動したagents_serverのsessionのうち、実行中か未回収の結果を持つものがあれば真を返す。"""
    state = session_state.read_state(payload["session_id"])
    return any(_async_target_alive(state, work, target) for target in work.get("async_targets", {}))


def pending_work(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """現在の会話の観測済み作業のうち、中止・置換・技術的不成立・正当な待機に当たらないものを返す。"""
    state = session_state.read_state(payload["session_id"])
    pending: list[tuple[str, dict[str, Any]]] = []
    for work_id, work in _session_works(state, payload["session_id"]).items():
        if not isinstance(work, dict) or _last_decision(work) in {"cancel", "replace", "blocked"}:
            continue
        if _last_decision(work) == "wait":
            decision = work["decisions"][-1]
            if _async_target_alive(state, work, decision.get("target_session_id")):
                continue
            path = decision.get("uwi_file")
            if isinstance(path, str):
                try:
                    if not uwi_scan.is_uwi_answered(pathlib.Path(path).read_text(encoding="utf-8")):
                        continue
                except (OSError, UnicodeError) as error:
                    append_stop_log(
                        payload["session_id"], "termination_wait_unknown", {"work_id": work_id, "reason": str(error)}
                    )
                    continue
        pending.append((work_id, work))
    return pending


def decision_hint(payload: dict[str, Any]) -> str:
    """判断記録に必要な会話と直近のユーザー入力の識別子を、不足の通知へ添える本文として返す。"""
    data = session_state.read_state(payload["session_id"]).get(STATE_KEY)
    last = data.get("last_human_input", data.get("last_input")) if isinstance(data, dict) else None
    entry = data.get("inputs", {}).get(last) if isinstance(data, dict) else None
    lines = [f"判断記録の`session_id`: {payload['session_id']}"]
    if isinstance(entry, dict) and entry.get("human") is True:
        lines.append(f"直近のユーザー入力の`input_id`: {last}")
    return "\n".join(lines)
