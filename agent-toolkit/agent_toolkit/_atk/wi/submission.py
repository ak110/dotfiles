"""`atk wi submission`: add-awi-by-userの投入先と作業期間を会話別に記録する。

process-loopの開始前の活動猶予は、この記録を持つ会話とその子孫の活動だけを理由にする。
投入先は保存時にoriginの正規化URLへ解決し、process-loopの対象リポジトリ集合と同じ規則で比べる。
"""

import argparse
import os

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import session_activity as _session_activity
from agent_toolkit._git import remote as _git_remote

_SESSION_ENVIRONMENT = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
"""会話IDを保持するホストの環境変数。Claude Code、Codexの順に読む。"""

_SESSION_ID_NEXT_ACTION = "`--session-id`へ英数字・ハイフン・アンダースコアの会話IDを指定する"


def _session_id(args: argparse.Namespace) -> str | None:
    explicit = getattr(args, "session_id", None)
    if explicit:
        return explicit
    for name in _SESSION_ENVIRONMENT:
        if value := os.environ.get(name):
            return value
    return None


def cmd_submission(args: argparse.Namespace) -> int:
    """`begin`で投入先を記録・置換し、`end`で記録を削除する。"""
    _outcome.force_utf8_stdio()
    session_id = _session_id(args)
    if session_id is None:
        _outcome.report_failure(
            "会話IDを決められないため記録しなかった",
            next_action="add-awi-by-userを実行する会話から実行するか、`--session-id`で会話IDを指定する",
        )
        return 1
    if args.submission_subcommand == "end":
        try:
            removed = _session_activity.clear_submission(session_id)
        except ValueError as error:
            _outcome.report_failure(str(error), next_action=_SESSION_ID_NEXT_ACTION)
            return 1
        result = "記録を削除した" if removed else "記録は無かった"
        _outcome.report_success(f"WI投入作業の{result}: {session_id}")
        return 0
    targets: list[str] = []
    for value in args.target_repo:
        canonical = _git_remote.resolve_repo_identifier(value)
        if canonical is None:
            _outcome.report_failure(
                f"投入先をoriginの正規化URLへ解決できないため記録しなかった: {value}",
                next_action="投入先へoriginを持つローカル作業ツリーのパスか、`host/owner/repository`形式のURLを指定する",
            )
            return 1
        targets.append(canonical)
    try:
        _session_activity.record_submission(session_id, targets)
    except ValueError as error:
        _outcome.report_failure(str(error), next_action=_SESSION_ID_NEXT_ACTION)
        return 1
    _outcome.report_success(f"WI投入作業の投入先を記録した: {session_id} -> {', '.join(sorted(set(targets)))}")
    return 0
