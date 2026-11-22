import argparse
import json
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
    _outcome.force_utf8_stdio()
    session_id = _session_id(args)
    if session_id is None and args.submission_subcommand != "activity":
        _outcome.report_failure(
            "会話IDを決められないため記録しなかった",
            next_action="AWI投入（`/atk-mode add-awi-by-user`）を実行する会話から実行するか、`--session-id`で会話IDを指定する",
        )
        return 1
    if args.submission_subcommand == "end":
        assert session_id is not None
        return _end(args, session_id)
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
    if args.submission_subcommand == "activity":
        snapshot = _session_activity.submission_activity(set(targets), excluded_pids={os.getpid()})
        print(json.dumps({"active": snapshot.has_activity, "uncertain": snapshot.uncertain}))
        return 0
    assert session_id is not None
    try:
        _session_activity.record_submission(session_id, targets)
    except ValueError as error:
        _outcome.report_failure(str(error), next_action=_SESSION_ID_NEXT_ACTION)
        return 1
    _outcome.report_success(f"WI投入作業の投入先を記録した: {session_id} -> {', '.join(sorted(set(targets)))}")
    return 0


def _end(args: argparse.Namespace, session_id: str) -> int:
    try:
        removed = _session_activity.clear_submission(session_id)
    except ValueError as error:
        _outcome.report_failure(str(error), next_action=_SESSION_ID_NEXT_ACTION)
        return 1
    result = "記録を削除した" if removed else "記録は無かった"
    _outcome.report_success(f"WI投入作業の{'中止で' if args.abort else ''}{result}: {session_id}")
    return 0
