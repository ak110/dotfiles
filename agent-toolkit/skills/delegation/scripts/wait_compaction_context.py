#!/usr/bin/env python3
"""待機時の会話圧縮に必要な所有・起動区分・保存状態を一度だけ取得する。"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import shared_roots, status_reader, wait_targets
from agent_toolkit._atk import managed_temp
from agent_toolkit._common.bash_invocations import extract_bash_invocations
from agent_toolkit._common.shell_tokens import is_atk_command


def wait_context(
    session_id: str, command: str, not_before: float, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """実行位置のwaitと既存所有記録を使い、保存済みでexplore・shell以外の待機を返す。"""
    if not math.isfinite(not_before) or not_before < 0:
        raise ValueError("--not-beforeは有限で0以上のPOSIX秒で指定する")
    invocations = extract_bash_invocations(command)
    waits = [
        invocation.static_tokens
        for invocation in invocations
        if is_atk_command(invocation.static_tokens) and invocation.static_tokens[1:3] == ("agents", "wait")
    ]
    if len(waits) != 1 or len(invocations) != 1:
        return {"eligible": False, "reason": "単独のatk agents waitではない"}
    tokens = waits[0]
    explicit = None
    if len(tokens) > 3:
        if len(tokens) == 5 and tokens[3] == "--root-session-id":
            explicit = tokens[4]
        elif len(tokens) == 4 and tokens[3].startswith("--root-session-id="):
            explicit = tokens[3].split("=", 1)[1]
        else:
            return {"eligible": False, "reason": "waitの入力を確定できない"}
    env = dict(os.environ if environment is None else environment)
    env["CLAUDE_CODE_SESSION_ID"] = session_id
    identity = shared_roots.resolve_wait_identity(env, explicit)
    if identity is None:
        return {"eligible": False, "reason": "待機所有を取得できない"}
    targets, error = wait_targets.read_wait_targets(identity.root_session_id, identity.file_name)
    snapshot = status_reader.load_root(identity.root_session_id)
    if error or snapshot.failures:
        return {"eligible": False, "reason": "待機の登録または状態を取得できない"}
    sessions = [
        {"session_id": identifier, "mode": session["launch_kind"]}
        for identifier, session in snapshot.sessions.items()
        if identifier in targets
        and session.get("owner_status_file") == identity.file_name
        and session.get("status") == "running"
        and session.get("launch_kind") in {"delegate", "write"}
    ]
    if not sessions:
        return {"eligible": False, "reason": "所有するexplore・shell以外の担当の待機がない"}
    directory = managed_temp.find_session_temp(session_id)
    handoff = directory / "parent-handoff.md" if directory is not None else None
    if (
        handoff is None
        or not handoff.is_file()
        or not handoff.read_text(encoding="utf-8").strip()
        or handoff.stat().st_mtime < not_before
    ):
        return {"eligible": False, "reason": "現在のturnの作業状態が保存されていない"}
    return {"eligible": True, "handoff": str(handoff), "sessions": sessions}


def main() -> None:
    """公開引数を検証し、自動圧縮の成立条件をJSONで返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True, help="Claudeメインのsession ID")
    parser.add_argument("--command", required=True, help="背景起動したBashのcommand原文")
    parser.add_argument("--not-before", type=float, required=True, help="保存を要するturn開始のPOSIX秒")
    args = parser.parse_args()
    try:
        result = wait_context(args.session_id, args.command, args.not_before)
    except (OSError, ValueError) as error:
        result = {"eligible": False, "reason": str(error)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
