"""セッション振り返りの入力を抽出し、会話・比較材料・候補・統計を作業ディレクトリへ書く。

1回の実行で証拠bundleを抽出し、会話の流れ・比較材料・問題候補・統計を書いて、所在を1行のJSONで返す。
会話の流れはメインのユーザー発話ごとの時系列と委譲先ごとの活動概要を、詳細の記録位置とともに示した文書であり、
メインが最初に全体を通読する。
原因と対策の確定、AWIの起草と投入はメインが自身のコンテキストで行うため、本スクリプトはキューを変更しない。
`candidates.md`は1候補を1行の要約と、記録の由来（役割文書、起動mode、起動元）を添えた記録位置で示す。全文が要る候補だけを、
メインが`atk run-script session-review-evidence`の`--detail`で照会する。

本スクリプトはデータ取得を目的とし、合否を判定しないため、
`agent-basics:writing-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と準備項目を取得できない実行前エラーを終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import io
import json
import pathlib
import shlex
import sys
from typing import Any

import session_conversation
import session_evidence_execution
import session_review_evidence
from session_review_prepare_candidates import (
    _SIMILAR_RECORD_KINDS,
    _candidate_bodies,
    _candidates_document,
    _past_wi_sections,
    _similar_records,
    is_mandatory,
)
from session_review_prepare_conversation import _CONVERSATION_MAX_BYTES, _conversation_document
from session_review_prepare_failures import _select_repeated_failures
from session_review_prepare_stats import _stats_document
from session_review_prepare_text import _clip_utf8, _comparison_document, _parse_jsonl, _read_jsonl
from session_review_prepare_timings import _hook_timing_stats

from agent_toolkit._atk import managed_temp, run_script
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import session_record_paths, session_registry, session_review
from agent_toolkit._git import command as _git_command

CONVERSATION_FILENAME = "conversation.md"


CANDIDATES_FILENAME = "candidates.md"


STATS_FILENAME = "stats.md"


COMPARISON_FILENAME = "comparison-materials.md"


_CANDIDATES_MAX_BYTES = 70_000


_STATS_MAX_BYTES = 30_000


def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数を定義する。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--transcript", metavar="PATH", help="Claude Codeのtranscriptパス。")
    session.add_argument("--codex-thread-id", metavar="ID", help="Codexのthread ID。")
    session.add_argument("--opencode-session-id", metavar="ID", help="OpenCodeのses_で始まるセッションID。")
    session.add_argument("--agents-session-id", metavar="ID", help="agents_serverが起動した委譲先のsession ID。")
    parser.add_argument("--opencode-db", metavar="PATH", help="OpenCodeのSQLite記録。--opencode-session-idと併用する。")
    parser.add_argument(
        "--work-dir",
        metavar="PATH",
        help="作成済みの出力ディレクトリの絶対パス。省略時は対象セッションのmanaged-temp内へ実行ごとの領域を確保する。",
    )
    parser.add_argument(
        "--target-repo",
        metavar="PATH",
        help="振り返り対象のリポジトリ。対象リポジトリ固有の振り返り参照文書の解決に使う。",
    )
    parser.add_argument(
        "--conversation-only",
        action="store_true",
        help="引き継ぎ用の会話の流れと証拠bundleを生成して終了する。候補文書・統計・failure ledgerは更新しない。",
    )
    parser.add_argument("--after", metavar="PATH", help="前回のscope JSON。処理済み位置より後だけをbatchの分析対象にする。")
    parser.add_argument("--conversation-window", action="store_true", help="会話を少量表示する。候補・統計は更新しない。")
    parser.add_argument("--conversation-record", action="append", default=None, help="対象の正規記録ID。複数指定可。")
    parser.add_argument(
        "--conversation-since",
        type=session_conversation.timestamp,
        help="開始日時（timezone付きISO8601、境界を含む）。",
    )
    parser.add_argument(
        "--conversation-until",
        type=session_conversation.timestamp,
        help="終了日時（timezone付きISO8601、境界を含む）。",
    )
    parser.add_argument(
        "--conversation-recent",
        type=session_conversation.positive_integer,
        help="全担当の既知時刻から直近の正整数件。同時刻は元の抽出順。未知時刻は件数を示して除外する。",
    )
    parser.add_argument(
        "--conversation-after",
        type=session_conversation.nonnegative_integer,
        help="同じwork-dirの保存bundleから定まる時系列順のイベント位置より後。未知時刻は末尾の別区分。0は先頭。",
    )
    parser.add_argument(
        "--conversation-max-bytes",
        type=session_conversation.positive_integer,
        default=16000,
        help="少量表示文書全体のUTF-8上限（省略時16000）。",
    )
    return parser


_MISSING_NEXT_ACTIONS = {
    "evidence_script": (
        "`atk run-script session-review-prepare -- <引数>`で起動する。"
        "解消しない場合はagent-toolkitの導入が壊れているため、ユーザーへ報告する"
    ),
    "work_dir": (
        "通知されたセッションのmanaged-temp内に`mktemp -d '<親>/session-review-XXXXXXXX'`で作成した"
        "ディレクトリの絶対パスを`--work-dir`へ渡して再実行する"
    ),
    "transcript_path": (
        "`--transcript`へClaude Codeの記録、Codexでは`--codex-thread-id`、OpenCodeでは`--opencode-session-id`を渡す"
    ),
    "bundle": (
        "表示した原因と入力引数を確かめ、記録を持つホストで再実行する。"
        "OpenCodeでは`--opencode-session-id`と`--opencode-db`の値・読取権限・schemaを確認する"
    ),
}
"""準備項目ごとの取得方法。項目名は標準エラーの`不足:`行と一致させる。"""


def _missing(item: str, detail: str | None = None) -> int:
    """取得できなかった準備項目を、項目に対応する引数と取得方法とともに報告する。"""
    print(f"不足: {item}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    print(_next_action.next_action_line(_MISSING_NEXT_ACTIONS[item]), file=sys.stderr)
    return 2


def _reference_document(target_repo: pathlib.Path | None, *, codex: bool) -> pathlib.Path | None:
    """Git共通ディレクトリ（`--git-common-dir`）から対象リポジトリ固有の振り返り参照文書を解決する。"""
    if target_repo is None:
        return None
    try:
        result = _git_command.run(
            ["-C", str(target_repo), "rev-parse", "--git-common-dir"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 1:
        return None
    common_dir = pathlib.Path(lines[0])
    if not common_dir.is_absolute():
        common_dir = target_repo / common_dir
    repository_name = common_dir.resolve().parent.name
    home = _host_homes.codex_home() if codex else _host_homes.claude_config_dir()
    path = home / "docs" / f"session-review-{repository_name}.md"
    return path if path.is_file() else None


# 未解決の対象と回復した記録行は、どのbundleファイルにも保存されず標準出力だけで届く。
_SCOPE_EVENT_KINDS = frozenset({"unresolved-record", "unresolved-delegation", "recovered-record-line"})


def _extract_bundle(session_arguments: list[str], bundle_dir: pathlib.Path) -> tuple[str | None, list[dict[str, Any]]]:
    """bundleの成功時も未解決通知を受け渡し、走査全量と最終JSONへ混ぜない。"""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = session_review_evidence.main([*session_arguments, "--bundle", str(bundle_dir)])
    if exit_code != 0:
        return captured.getvalue() or f"atk run-script session-review-evidenceの終了コード: {exit_code}", []
    try:
        events = _parse_jsonl(captured.getvalue(), f"抽出標準出力（入力={shlex.join(session_arguments)}, bundle={bundle_dir}）")
    except ValueError as error:
        return str(error), []
    return None, [event for event in events if event.get("kind") in _SCOPE_EVENT_KINDS]


def _unconfirmed_lines(events: list[dict[str, Any]], recovered: list[dict[str, Any]]) -> list[str]:
    """候補件数と区別した確認範囲と回復した記録行を、全ての準備文書へ同じ集合で示す。"""
    lines = ["", "## 未確認範囲", ""]
    if not events:
        lines.append("未解決の対象は無い。")
    else:
        lines.extend(
            [
                f"未解決{len(events)}件。全件はbundle/unconfirmed.jsonlへ保持する。以下は先頭10件の概要である。",
                *(f"- {event['kind']}: {_clip_utf8(json.dumps(event, ensure_ascii=False), 200)}" for event in events[:10]),
            ]
        )
    if recovered:
        lines.extend(
            [
                "",
                f"回復した記録行{len(recovered)}件。JSONとして成立しない行を、直後にある同じordinalとtypeの完全な行で置き換えた。"
                "原本は変更していない。全件はbundle/recovered.jsonlへ保持する。",
                *(
                    f"- {event['record']}: {event['line']}行目 → {event['superseded_by']}行目"
                    f"（ordinal={event['ordinal']}、type={event['entry_type']}）"
                    for event in recovered[:10]
                ),
            ]
        )
    return [*lines, ""]


@session_evidence_execution.shell_parse_scope()
def main(argv: list[str] | None = None, *, now: datetime.datetime | None = None) -> int:
    """振り返りの入力を作業ディレクトリへ書き、所在と件数を1行のJSONで出力する。"""
    args = _build_parser().parse_args(argv)
    if args.agents_session_id is not None:
        try:
            resolution = session_registry.resolve(args.agents_session_id)
            info = resolution.resume_info or resolution.handoff_info
            if info is None:
                raise ValueError("session登録簿から実行エンジンを解決できない")
            record = session_record_paths.find_session_record(args.agents_session_id)
            if record is None or len(record.paths) != 1 or record.engine != info.engine:
                raise ValueError("登録済みsessionのセッション記録を一意に解決できない")
            if info.engine in {"claude", "agy"}:
                # Antigravityの記録も単一ファイルのtranscriptとして証拠抽出が読めるため、同じ入力で渡す。
                args.transcript = str(record.paths[0])
            elif info.engine == "codex":
                args.codex_thread_id = args.agents_session_id
            elif info.engine == "opencode":
                args.opencode_session_id = args.agents_session_id
            else:
                raise ValueError(f"この抜粋取得では対応していない実行エンジン: {info.engine}")
        except (OSError, ValueError) as error:
            return _missing(
                "bundle",
                f"--agents-session-id {args.agents_session_id}: {error}。"
                "`atk agents show <session_id>`と`atk agents logs <session_id>`で登録と記録の所在を確認する",
            )
    if not args.conversation_window and (
        args.conversation_record
        or args.conversation_since
        or args.conversation_until
        or args.conversation_recent is not None
        or args.conversation_after is not None
        or args.conversation_max_bytes != 16000
    ):
        return _missing("bundle", "会話の選択条件は--conversation-windowと併用する")
    if args.conversation_since and args.conversation_until and args.conversation_since > args.conversation_until:
        return _missing("bundle", "開始日時が終了日時より後。--conversation-sinceと--conversation-untilを確認する")
    if args.conversation_after is not None and args.work_dir is None:
        return _missing("bundle", "--conversation-afterには同じbundleを持つ--work-dirを指定する")
    if args.opencode_db is not None and args.opencode_session_id is None:
        return _missing("bundle", "--opencode-dbは--opencode-session-idと併用する")
    local_evidence_script = pathlib.Path(__file__).resolve().with_name("session_review_evidence.py")
    try:
        evidence_script = run_script.registered_script_path("session-review-evidence")
    except ValueError:
        return _missing("evidence_script")
    if local_evidence_script != evidence_script:
        return _missing("evidence_script")

    transcript_path = pathlib.Path(args.transcript).expanduser().resolve() if args.transcript is not None else None
    if transcript_path is not None and not transcript_path.is_file():
        return _missing("transcript_path")
    target_repo = pathlib.Path(args.target_repo).expanduser().resolve() if args.target_repo is not None else None
    session_id = transcript_path.stem if transcript_path is not None else str(args.codex_thread_id or args.opencode_session_id)
    identity = session_review.prepare_identity(
        "claude" if transcript_path is not None else "codex" if args.codex_thread_id else "opencode", session_id
    )
    if args.work_dir is not None:
        work_dir = pathlib.Path(args.work_dir).expanduser()
        if not work_dir.is_absolute() or not work_dir.is_dir():
            return _missing("work_dir", f"既存の絶対ディレクトリではない: {work_dir}")
    else:
        session_id = (
            transcript_path.stem if transcript_path is not None else str(args.codex_thread_id or args.opencode_session_id)
        )
        try:
            session_root = managed_temp.resolve_session_temp(session_id)
            work_dir = managed_temp.create_session_temp("session-review", session_root)
        except (managed_temp.ManagedTempError, OSError) as error:
            return _missing("work_dir", str(error))

    if transcript_path is not None:
        session_arguments = [str(transcript_path)]
    elif args.opencode_session_id is not None:
        session_arguments = ["--opencode-session-id", args.opencode_session_id]
        if args.opencode_db is not None:
            session_arguments.extend(["--opencode-db", str(pathlib.Path(args.opencode_db).expanduser().resolve())])
    else:
        session_arguments = ["--codex-thread-id", args.codex_thread_id]
    if args.after is not None:
        session_arguments.extend(["--after", args.after])
    bundle_dir = work_dir / "bundle"
    bundle_dir.mkdir(exist_ok=True)
    source_path = bundle_dir / "conversation-source.json"
    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    observed_at = current.astimezone(datetime.UTC).isoformat()
    if args.conversation_after is not None:
        try:
            source = json.loads(source_path.read_text(encoding="utf-8"))
            if not isinstance(source, dict) or not isinstance(source.get("observed_at"), str):
                raise ValueError("保存bundleの会話入力情報が不正")
            if source["session_arguments"] != session_arguments:
                raise ValueError("保存bundleの入力指定が異なる。同じ入力と--work-dirを指定する")
            observed_at = source["observed_at"]
            scope_events = [*_read_jsonl(bundle_dir / "unconfirmed.jsonl"), *_read_jsonl(bundle_dir / "recovered.jsonl")]
        except (OSError, ValueError, KeyError) as error:
            return _missing("bundle", f"継続用bundleを読めない: {error}。--conversation-afterを外して初回取得する")
        failure = None
    else:
        failure, scope_events = _extract_bundle(session_arguments, bundle_dir)
    unresolved = [event for event in scope_events if event["kind"] != "recovered-record-line"]
    recovered = [event for event in scope_events if event["kind"] == "recovered-record-line"]
    if failure is not None:
        return _missing("bundle", f"{failure}\n診断用に保持した作業領域: {work_dir}")
    try:
        candidate_rows = _read_jsonl(bundle_dir / "candidates.jsonl")
        stats = _read_jsonl(bundle_dir / "stats.jsonl")
        stats.extend(_read_jsonl(bundle_dir / "resource-usage.jsonl"))
        timeline = _read_jsonl(bundle_dir / "timeline.jsonl")
        conversation = _read_jsonl(bundle_dir / "conversation.jsonl")
        comparison = _read_jsonl(bundle_dir / "comparison-materials.jsonl")
        candidates = [row for row in candidate_rows if row.get("kind") == "candidate"]
        summary = next((row for row in candidate_rows if row.get("kind") == "candidate-summary"), {})
        evidence_index = _read_jsonl(bundle_dir / "candidate-evidence.jsonl")
    except (OSError, ValueError, KeyError) as error:
        return _missing("bundle", str(error))
    (bundle_dir / "unconfirmed.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in unresolved), encoding="utf-8"
    )
    (bundle_dir / "recovered.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in recovered), encoding="utf-8"
    )
    source_path.write_text(
        json.dumps({"session_arguments": session_arguments, "observed_at": observed_at}, ensure_ascii=False), encoding="utf-8"
    )

    # 準備と証拠照会で同じ入力指定を使い、
    # 全文を照会するコマンドとして示す。
    detail_command = "atk run-script session-review-evidence -- " + shlex.join(session_arguments)
    conversation_path = work_dir / CONVERSATION_FILENAME
    candidates_path = work_dir / CANDIDATES_FILENAME
    stats_path = work_dir / STATS_FILENAME
    comparison_path = work_dir / COMPARISON_FILENAME
    main_record = next(
        (item.get("record") for item in timeline if item.get("kind") == "record-provenance" and item.get("role") == "main"),
        None,
    )
    conversation_text = (
        None
        if args.conversation_window
        else _conversation_document(conversation, detail_command, str(main_record) if main_record else None)
    )
    conversation_limit = args.conversation_max_bytes if args.conversation_window else _CONVERSATION_MAX_BYTES
    conversation_window: dict[str, Any] | None = None
    try:
        if conversation_text is None:
            continue_arguments = ["--transcript", *session_arguments] if transcript_path else list(session_arguments)
            continue_arguments.extend(["--work-dir", str(work_dir)])
            conversation_text, conversation_window = session_conversation.window_document(
                conversation,
                args=args,
                observed_at=observed_at,
                main_record=str(main_record) if main_record else None,
                unresolved_count=len(unresolved),
                recovered_count=len(recovered),
                detail_command=detail_command,
                continue_command="atk run-script session-review-prepare -- " + shlex.join(continue_arguments),
                max_bytes=conversation_limit,
            )
    except ValueError as error:
        return _missing("bundle", str(error))
    conversation_path.write_text(conversation_text, encoding="utf-8", newline="\n")
    conversation_bytes = len(conversation_text.encode("utf-8"))
    if args.conversation_only or args.conversation_window:
        print(
            json.dumps(
                {
                    "work_dir": str(work_dir),
                    "conversation_path": str(conversation_path),
                    "conversation_utf8_bytes": conversation_bytes,
                    "conversation_limit_bytes": conversation_limit,
                    "conversation_window": conversation_window,
                    "unconfirmed_scope": unresolved,
                    "recovered_record_lines": recovered,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    comparison_path.write_text(_comparison_document(comparison, detail_command), encoding="utf-8")
    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    session_id = transcript_path.stem if transcript_path is not None else str(args.codex_thread_id or args.opencode_session_id)
    candidates, single_failures, summary, ledger_skipped = _select_repeated_failures(candidates, summary, session_id, current)
    similar_targets = [item for item in candidates if item["candidate_kind"] in _SIMILAR_RECORD_KINDS]
    similar_ids = {str(item["candidate_id"]) for item in similar_targets}
    try:
        evidence_by_id = {
            str(row["candidate_id"]): json.loads((bundle_dir / row["path"]).read_text(encoding="utf-8"))
            for row in evidence_index
            if str(row["candidate_id"]) in similar_ids
        }
    except (OSError, ValueError, KeyError) as error:
        return _missing("bundle", str(error))
    past = _past_wi_sections() if similar_targets else []
    similar_by_id = {
        str(item["candidate_id"]): _similar_records(
            "\n".join(_candidate_bodies(item, evidence_by_id.get(str(item["candidate_id"]), {}))[0])
            or str(item.get("text", "")),
            past,
            exclude=item.get("wi_file"),
        )
        for item in similar_targets
    }
    evidence_paths = {str(item["candidate_id"]): item["path"] for item in evidence_index}
    (bundle_dir / "review-index.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "candidate_id": item["candidate_id"],
                    "candidate_kind": item["candidate_kind"],
                    "mandatory": is_mandatory(item),
                    "state": state,
                    "locators": item.get("locators", []),
                    "evidence_path": evidence_paths.get(str(item["candidate_id"])),
                    "failure_session_count": item.get("failure_session_count"),
                    "occurrence_count": item.get("occurrence_count", item.get("count", 1)),
                    "similar_records": similar_by_id.get(str(item["candidate_id"]), []),
                },
                ensure_ascii=False,
            )
            + "\n"
            for state, items in (("candidate", candidates), ("single-failure", single_failures))
            for item in items
        ),
        encoding="utf-8",
    )
    unconfirmed_text = "\n".join(_unconfirmed_lines(unresolved, recovered))
    improvement_rows = [
        f"- {event['record']}:{event['line']}: {line}" for event in timeline for line in event.get("reported_improvements", [])
    ]
    improvement_text = (
        f"\n## 取り込んだ改善点\n\n{len(improvement_rows)}件。全量はbundle/timeline.jsonlのreported_improvements。\n"
        + "\n".join(_clip_utf8(row, 150) for row in improvement_rows[:10])
        + "\n"
        if improvement_rows
        else ""
    )
    candidate_text = (
        _candidates_document(candidates, summary, timeline, detail_command, single_failures)
        + unconfirmed_text
        + improvement_text
    )
    hook_timings = _hook_timing_stats(session_id)
    hook_timings_path = bundle_dir / "hook-timings.jsonl"
    hook_timings_path.write_text(json.dumps(hook_timings, ensure_ascii=False) + "\n", encoding="utf-8")
    stats.append(hook_timings)
    stats_text = _stats_document(stats) + unconfirmed_text
    if len(candidate_text.encode("utf-8")) > _CANDIDATES_MAX_BYTES or len(stats_text.encode("utf-8")) > _STATS_MAX_BYTES:
        return _missing("bundle", "候補または統計の初回表示が表示予算を超えた。全量の証拠はbundleへ保持した。")
    candidates_path.write_text(candidate_text, encoding="utf-8")
    stats_path.write_text(stats_text, encoding="utf-8")

    reference_document = _reference_document(target_repo, codex=args.codex_thread_id is not None)
    total = next((event for event in stats if event.get("kind") == "stats-total"), {})
    compaction = next((event for event in stats if event.get("kind") == "stats-compaction-total"), {})
    role_counts = collections.Counter(
        str(item["role"]) for item in conversation if item.get("kind", "utterance") == "utterance"
    )
    record = {
        **identity,
        "scope_path": str(bundle_dir / "scope.json"),
        "work_dir": str(work_dir),
        "conversation_path": str(conversation_path),
        "conversation_utf8_bytes": conversation_bytes,
        "conversation_limit_bytes": conversation_limit,
        "conversation_window": conversation_window,
        "comparison_materials_path": str(comparison_path),
        "candidates_path": str(candidates_path),
        "stats_path": str(stats_path),
        "hook_timings_path": str(hook_timings_path),
        "candidates_utf8_bytes": len(candidate_text.encode("utf-8")),
        "stats_utf8_bytes": len(stats_text.encode("utf-8")),
        "review_index_path": str(bundle_dir / "review-index.jsonl"),
        "reference_document": str(reference_document) if reference_document is not None else None,
        "prepared_at": current.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utterance_counts": {"user": role_counts.get("user", 0), "assistant": role_counts.get("assistant", 0)},
        "candidate_total": len(candidates),
        "candidate_counts": dict(sorted(collections.Counter(str(item["candidate_kind"]) for item in candidates).items())),
        "excluded_counts": dict(sorted(summary.get("excluded", {}).items())),
        "mandatory_candidates": [str(item["candidate_id"]) for item in candidates if is_mandatory(item)],
        "similar_records": {
            candidate_id: [filename for filename, _state, _title in records] for candidate_id, records in similar_by_id.items()
        },
        "failure_ledger_skipped": ledger_skipped,
        "unconfirmed_scope": unresolved,
        "recovered_record_lines": recovered,
        "improvement_lines": list(dict.fromkeys(line for event in timeline for line in event.get("reported_improvements", []))),
        "elapsed_seconds": total.get("elapsed_seconds"),
        "compaction_count": compaction.get("count", 0),
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
