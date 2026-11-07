"""`atk wi submission`: add-awi-by-userの投入先と作業期間を会話別に記録する。

process-loopの開始前の活動猶予は、この記録を持つ会話とその子孫の活動だけを理由にする。
投入先は保存時にoriginの正規化URLへ解決し、process-loopの対象リポジトリ集合と同じ規則で比べる。
正常な`end`は作業期間の実ユーザー発話と投入前レビューの指摘が指摘対応記録に対応付いているかを検査し、不足があれば記録を保持して
失敗する。成果物の保存完了だけで、受けた指摘の処置と報告を欠いたまま作業を終えられないようにするためである。
"""

import argparse
import os
import pathlib

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import submission_findings as _findings
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
    try:
        _session_activity.record_submission(session_id, targets)
    except ValueError as error:
        _outcome.report_failure(str(error), next_action=_SESSION_ID_NEXT_ACTION)
        return 1
    _outcome.report_success(f"WI投入作業の投入先を記録した: {session_id} -> {', '.join(sorted(set(targets)))}")
    return 0


_END_MODE_NEXT_ACTION = (
    "投入を終えた場合は`--findings-file <指摘対応記録の絶対パス>`、ユーザーが中止した場合は`--abort`を付けて再実行する"
)


def _end(args: argparse.Namespace, session_id: str) -> int:
    """正常終了は検査が成立した場合だけ記録を削除し、中止は検査せずに削除する。"""
    if args.abort == (args.findings_file is not None) or (args.abort and args.review_result):
        _outcome.report_failure("`--findings-file`と`--abort`のどちらか一方だけを指定する", next_action=_END_MODE_NEXT_ACTION)
        return 1
    try:
        if not args.abort:
            lines = _checked_summary(args, session_id)
            if lines is None:
                return 1
            _session_activity.record_submission_report(session_id, lines)
            print("\n".join(lines))
        removed = _session_activity.clear_submission(session_id)
    except ValueError as error:
        _outcome.report_failure(str(error), next_action=_SESSION_ID_NEXT_ACTION)
        return 1
    result = "記録を削除した" if removed else "記録は無かった"
    _outcome.report_success(f"WI投入作業の{'中止で' if args.abort else ''}{result}: {session_id}")
    return 0


def _checked_summary(args: argparse.Namespace, session_id: str) -> list[str] | None:
    """検査が成立すれば報告用要約の行を返し、不足があれば表示してNoneを返す。"""
    started_at = _session_activity.submission_started_at(session_id)
    if started_at is None:
        _outcome.report_failure(
            f"会話{session_id}の投入作業の開始記録が無いため、作業期間の発話を検査できない",
            next_action="`atk wi submission begin`を実行した会話で`--session-id`を確かめるか、中止なら`--abort`で再実行する",
        )
        return None
    try:
        findings = _findings.load_findings(pathlib.Path(args.findings_file))
        reviews = [item for path in args.review_result or [] for item in _findings.review_findings(pathlib.Path(path))]
        events = _findings.user_events(session_id, started_at)
    except ValueError as error:
        _outcome.report_failure(
            f"指摘対応記録を検査できない: {error}",
            next_action="表示された原因を直して同じコマンドを再実行する。記録の形式は`atk wi submission end --help`で確かめる",
        )
        return None
    problems = _findings.check(findings, events, reviews)
    if problems:
        _outcome.report_failure(
            "指摘対応記録に不足があるため、投入作業の記録を保持した:\n" + "\n".join(f"- {problem}" for problem in problems),
            next_action="不足する項目を指摘対応記録へ補い、同じコマンドを再実行する。分類と処置は"
            "add-awi-by-user「指摘の分類と処置」に従う",
        )
        return None
    return _findings.summary_lines(findings)
