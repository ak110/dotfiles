"""実受信した規範のセッション累積文字量と、effort調整の1回の案内。"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Mapping

from agent_toolkit._common import effort, session_registry
from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks import agent_id, document_reads, notice

THRESHOLD = 250_000
STATE_KEY = "normative_read_volume"
DELIVERED_KEY = "normative_effort_notified"
RESERVATION_SECONDS = 60


def accumulate(state: dict, actor: str) -> int:
    """同じ論理資料・版・行の和集合を圧縮リセットと別のキーへ保存する。"""
    volume = state.setdefault(STATE_KEY, {"documents": {}, "characters": 0})
    documents = volume["documents"]
    for key, actors in state.get("operation_skill_delivered_agents", {}).items():
        if not key.startswith(document_reads.RECEIPT_PREFIX) or not isinstance(actors, list) or actor not in actors:
            continue
        item = json.loads(key.removeprefix(document_reads.RECEIPT_PREFIX))
        sizes = item.get("line_chars")
        if not isinstance(sizes, list):
            continue
        identity = json.dumps([item["logical"], item["version"]], separators=(",", ":"))
        observed = documents.setdefault(identity, {"ranges": [], "characters": 0})
        ranges = item.get("coverage", []) if item.get("partial") else item.get("ranges", [])
        merged: list[list[int]] = []
        for start, end in sorted([*observed["ranges"], *ranges]):
            if merged and start <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        observed["ranges"] = merged
        observed["characters"] = sum(sum(sizes[max(0, start - 1) : end]) for start, end in merged)
    volume["characters"] = sum(item["characters"] for item in documents.values())
    return volume["characters"]


def observe(payload: Mapping[str, object]) -> str | None:
    """通常の委譲会話へ、閾値を超えた場合だけ相対操作を案内する。"""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not re.fullmatch(r"[0-9A-Za-z_-]+", session_id) or payload.get("agent_id"):
        return None
    resolution = session_registry.resolve(session_id)
    info = resolution.resume_info
    if info is None or info.launch_kind != "delegate" or info.model_type == "orchestrate":
        return None
    actor = agent_id.resolve_hook_agent_id(dict(payload))
    total = 0
    already = False

    def record(state: dict) -> dict:
        nonlocal total, already
        total = accumulate(state, actor)
        already = actor in state.get(DELIVERED_KEY, {}).get(str(THRESHOLD), [])
        return state

    if not update_state(session_id, record) or already or total <= THRESHOLD:
        return None
    try:
        levels = info.launch_info.effort_levels or effort.cli_levels(info.engine, info.model)
        next_effort = effort.relative(info.effort, "up", levels)
    except OSError, ValueError, subprocess.SubprocessError:
        # 不明なモデルを最上段とみなさない。操作時に所有MCPが受理段を確認して理由を返す。
        next_effort = None
    if next_effort == info.effort:
        return None
    claimed = False
    now = time.time()

    def claim(state: dict) -> dict | None:
        nonlocal claimed
        if actor in state.get(DELIVERED_KEY, {}).get(str(THRESHOLD), []):
            return None
        reservations = state.setdefault(STATE_KEY, {}).setdefault("reserved", {})
        if now - reservations.get(str(THRESHOLD), 0) < RESERVATION_SECONDS:
            return None
        reservations[str(THRESHOLD)] = now
        claimed = True
        return state

    if not update_state(session_id, claim) or not claimed:
        return None
    body = (
        f"この会話で実受信した規範は累積{total:,}文字で、目安の{THRESHOLD:,}文字を超えた。"
        "作業に必要な規範量と成果の品質から、effortを1段上げるか判断せよ。"
        "上げる場合は`atk agents effort up`を実行し、現在turnを終えよ。同じ会話が次turnで1回再開する。"
    )
    if next_effort is None:
        body += "現在のモデルの受理段は未確認であり、操作時に確認できなければ理由が返る。"
    text = notice.formatter("effort-volume")(body, tag="notice", subject=str(THRESHOLD))
    notice.stage_delivery(text, session_id, DELIVERED_KEY, str(THRESHOLD), actor)
    return text
