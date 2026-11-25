from __future__ import annotations

import functools
import os
import re
import sys
from pathlib import Path
from typing import Any

import session_evidence_failures
from agent_toolkit._common import opencode_records
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.session_family import _RecordRecovery, _started_after_boundary
from session_evidence_ancestors import RESOLVE_NEXT_ACTION, AmbiguousOwnerRecord, delegation_record, owner_record
from session_evidence_bundle import (
    _bundle_events,
    _conversation_bundle,
)
from session_evidence_catalog import (
    CATALOG_PARENT_FIELDS,
    _catalog_events,
    _catalog_grep_events,
    _catalog_tool_call_events,
    _catalog_user_event_events,
    _CatalogSelection,
)
from session_evidence_cli import (
    _QUERY_OPTIONS,
    _active_queries,
    _build_parser,
    _codex_transcript,
    _print_error,
    _print_events,
    _query_option_names,
    _single_transcript_query_modes,
    _summary_events,
    _write_query_events,
)
from session_evidence_context import (
    _context_at_events,
    _record_schema_collection_events,
)
from session_evidence_delivery import catalog_delivery_events, delivery_events, delivery_summary
from session_evidence_detail import (
    _detail_collection_events,
    _fixed_string_collection_events,
    _grep_collection_events,
)
from session_evidence_extract import (
    _BOUNDARY_NEXT_ACTION,
    _ELAPSED_UNTIL_NEXT_ACTION,
    _SINCE_NEXT_ACTION,
    _CollectedRecord,
    _collection_events,
    _error_event,
    _extract_records,
    _load_records_checked,
    _referenced_provenance_events,
    _Runtime,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_hook_notices import (
    _hook_notice_events,
)
from session_evidence_lane_costs import lane_costs
from session_evidence_records import (
    _collect_records,
    _resolve_claude_transcript,
)
from session_evidence_stats import (
    _apply_observation_boundary,
    _elapsed_until_event,
    _parse_cli_timestamp,
    _stats_events,
)
from session_evidence_tool_calls import (
    _sorted_tool_call_events,
    _tool_call_collection_events,
    _tool_call_summary,
)
from session_evidence_user_events import (
    _query_user_events,
    _saved_user_events_at,
    _user_events_since,
)
from session_evidence_warn import (
    _warning_collection_events,
)
from session_quality_metrics import quality_metrics

_RECORD_QUERY_EXAMPLE = "発話の照会例: atk run-script session-review-evidence -- --transcript <元記録の絶対パス> --user-events"


def main(
    argv: list[str] | None = None,
    *,
    conversation_output: list[dict[str, Any]] | None = None,
    conversation_scope: Path | None = None,
    conversation_only: bool = False,
    conversation_main_only: bool = False,
) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.saved_plan or args.saved_rollout:
        if not args.stats or any(
            value is not None and value is not False
            for name, value in vars(args).items()
            if name not in {"stats", "saved_plan", "saved_rollout"}
        ):
            return _print_error(
                "保存済みレーン統計の引数が不正", next_action="--statsと--saved-plan・--saved-rolloutだけを指定する"
            )
        try:
            event = lane_costs(args.saved_plan or [], args.saved_rollout or [])
        except (OSError, ValueError) as error:
            return _print_error(str(error), next_action="計画とrolloutのパスと形式を確認する")
        _print_events([event])
        return 0
    if args.scan_claude_projects or args.scan_codex_history:
        if not args.tool_calls or not args.failure_fixed_string or args.since is None or args.observation_boundary is None:
            return _print_error(
                "横断照会の指定が不足",
                next_action="--tool-calls・--failure-fixed-string・--since・--observation-boundaryを指定する",
            )
        if (
            args.transcript_path
            or args.transcript
            or args.catalog_claude_project
            or args.catalog_codex_history
            or args.claude_session_id
            or args.codex_thread_id
            or args.opencode_session_id
            or args.delegation_record
            or args.owner_session
            or _active_queries(args) != ["tool_calls"]
        ):
            return _print_error("横断照会と対象・照会の指定が重複", next_action="走査対象と--tool-callsだけを指定する")
        try:
            events, code = session_evidence_failures.scan_failures(args)
        except (OSError, ValueError, re.error) as error:
            return _print_error(str(error), next_action="走査対象・固定文字列・時刻窓を確認する")
        _print_events(_summary_events(events) if args.summary_only else events)
        return code
    if args.failure_fixed_string is not None and (not args.tool_calls or not args.failure_fixed_string):
        return _print_error("失敗文言は--tool-callsと併用する", next_action="--tool-callsを指定する")
    if args.user_events_file is not None or args.user_event_at is not None:
        next_action = (
            "`--user-events-file <保存済み出力の絶対パス> --user-event-at <record>:<line>`だけで再実行する。"
            "出所ファイルの存在とUTF-8 JSONL、record欄とline欄の値を確認する"
        )
        if args.user_events_file is None or args.user_event_at is None:
            return _print_error("--user-events-fileと--user-event-atは組で指定する", next_action=next_action)
        if any(
            value is not None and value is not False
            for name, value in vars(args).items()
            if name not in {"user_events_file", "user_event_at"}
        ):
            return _print_error("保存済み発話の照会は元記録の指定・照会モードと併用できない", next_action=next_action)
        try:
            events = _saved_user_events_at(Path(args.user_events_file), args.user_event_at)
        except (OSError, UnicodeError, ValueError) as error:
            return _print_error(str(error), next_action=next_action)
        _print_events(events)
        return 0
    query_modes = _single_transcript_query_modes(args)
    main_only = conversation_main_only or ((args.user_events or args.elapsed_until is not None) and sum(query_modes) == 1)
    active_queries = _active_queries(args)
    if args.summary_only and (not active_queries or any(not _QUERY_OPTIONS[name][0] for name in active_queries)):
        supported = _query_option_names([name for name, (summary, _catalog) in _QUERY_OPTIONS.items() if summary])
        return _print_error(
            f"--summary-onlyの対象外の照会: {_query_option_names(active_queries) or '無指定の時系列表示・親一覧'}",
            next_action=f"要約は{supported}と併用する。詳細照会では`--summary-only`を外して再実行する",
        )
    user_query = args.user_match_fixed is not None or args.user_match_regex is not None or args.with_preceding_assistant
    user_pattern = None
    if user_query:
        user_next_action = (
            "`--user-events`と併用し、`--user-match-fixed`か`--user-match-regex`の一方だけを指定する。"
            "正規表現の構文を確認して再実行する"
        )
        if not args.user_events or args.user_match_fixed is not None and args.user_match_regex is not None:
            return _print_error("発話検索の指定の組が不正", next_action=user_next_action)
        try:
            user_pattern = (
                re.compile(re.escape(args.user_match_fixed))
                if args.user_match_fixed is not None
                else re.compile(args.user_match_regex)
                if args.user_match_regex is not None
                else None
            )
        except re.error as error:
            return _print_error(f"発話検索の正規表現が不正: {error}", next_action=user_next_action)
    summaries = [
        name
        for name, active in (
            ("--grep", args.grep is not None),
            ("--hook-notices", args.hook_notices),
            ("--user-events", args.user_events),
        )
        if active
    ]
    if args.bundle is not None and sum(query_modes) > 1:
        return _print_error(
            "--bundleは他の照会と併用できない",
            next_action="ファイルへ保存する`--bundle`と標準出力への照会を別々に実行する",
        )
    if len(summaries) > 1:
        return _print_error(
            f"{'・'.join(summaries)}はkind=summaryを共有するため結果を区別できず、併用できない",
            next_action="`--grep`・`--hook-notices`・`--user-events`を別々に実行する。他のkindを返す照会は併用できる",
        )
    catalog_root = args.catalog_claude_project or args.catalog_codex_history
    if catalog_root is not None and (args.failure_fixed_string is not None or args.allow_partial_records):
        return _print_error(
            "失敗・部分取得の照会は単一記録または横断走査を指定する",
            next_action="カタログ指定を単一記録または--scan-claude-projects・--scan-codex-historyへ変更する",
        )
    catalog_runtime: _Runtime | None = (
        "claude" if args.catalog_claude_project else "codex" if args.catalog_codex_history else None
    )
    catalog_selection = None
    if (
        args.catalog_workflow is not None
        or args.catalog_known_workflow
        or args.catalog_cwd_contains is not None
        or args.catalog_fields is not None
    ):
        selection_next_action = (
            "親行の選択は`--catalog-claude-project`か`--catalog-codex-history`と併用し、"
            "`--tool-calls`と`--delivery-reposts`を外す。"
            "`--catalog-fields`は親一覧だけで使い、`--user-events`との組では外す。"
            "表示項目は重複なしで指定して再実行する: " + ",".join(CATALOG_PARENT_FIELDS)
        )
        if (
            catalog_root is None
            or args.tool_calls
            or args.delivery_reposts
            or (args.user_events or args.grep is not None)
            and args.catalog_fields is not None
        ):
            return _print_error(
                "--catalog-fieldsはカタログの親セッション一覧だけで使える"
                if args.user_events and args.catalog_fields is not None
                else "親行の選択はカタログの親セッション一覧だけで使える",
                next_action=selection_next_action,
            )
        fields = tuple(args.catalog_fields.split(",")) if args.catalog_fields is not None else None
        if fields is not None and (
            len(set(fields)) != len(fields) or any(field not in CATALOG_PARENT_FIELDS for field in fields)
        ):
            return _print_error("--catalog-fieldsに未知、空または重複した項目がある", next_action=selection_next_action)
        catalog_selection = _CatalogSelection(
            args.catalog_workflow, args.catalog_known_workflow, args.catalog_cwd_contains, fields
        )
    if catalog_root is not None and active_queries and (len(active_queries) != 1 or not _QUERY_OPTIONS[active_queries[0]][1]):
        supported = _query_option_names([name for name, (_summary, catalog) in _QUERY_OPTIONS.items() if catalog])
        return _print_error(
            "カタログ走査と照会の指定の組が不正",
            next_action=f"カタログ走査は{supported}のいずれか1つと併用する。他の照会は単一記録で実行する",
        )
    if (args.tool is not None or args.input_regex is not None) and not args.tool_calls:
        return _print_error(
            "--toolと--input-regexは--tool-callsと併用する",
            next_action="`--tool-calls`を付けるか、`--tool`・`--input-regex`を外して再実行する",
        )
    input_pattern = None
    if args.input_regex is not None:
        try:
            input_pattern = re.compile(args.input_regex)
        except re.error as error:
            return _print_error(
                f"正規表現が不正: {error}",
                next_action="`--input-regex`の正規表現の構文（括弧の対応、エスケープ）を直して再実行する",
            )
    if args.phrase is not None and args.context_at is None:
        return _print_error(
            "--phraseは--context-atと併用する", next_action="`--context-at <記録ID>:<行番号>`を付けて再実行する"
        )
    if args.fixed_string is not None and any(not phrase for phrase in args.fixed_string):
        return _print_error(
            "--fixed-stringへ空でない固定文字列を指定する",
            next_action="`--fixed-string=<固定文字列>`を1件以上付けて再実行する",
        )
    if (
        args.since is not None
        and not args.user_events
        and not args.delivery_reposts
        and not args.tool_calls
        and catalog_root is None
    ):
        return _print_error(
            "--sinceは--user-events・--delivery-reposts・--tool-callsまたはカタログ走査と併用する",
            next_action="`--user-events`・`--delivery-reposts`・`--tool-calls`かカタログ走査の引数を付けるか、`--since`を外して再実行する",
        )
    if catalog_root is not None and args.since is None:
        return _print_error("カタログ走査には--sinceが必要", next_action=_SINCE_NEXT_ACTION)
    if catalog_root is not None and args.observation_boundary is None:
        return _print_error("カタログ走査には--observation-boundaryが必要", next_action=_BOUNDARY_NEXT_ACTION)
    since = None
    if args.since is not None:
        try:
            since = _parse_cli_timestamp(args.since)
        except ValueError:
            return _print_error(f"開始境界が不正: {args.since}", next_action=_SINCE_NEXT_ACTION)

    sources = (
        args.transcript_path,
        args.transcript,
        args.claude_session_id,
        args.codex_thread_id,
        args.opencode_session_id,
        args.catalog_claude_project,
        args.catalog_codex_history,
        args.delegation_record,
        True if args.owner_session else None,
    )
    source_count = sum(source is not None for source in sources)
    if source_count == 0 and active_queries and args.bundle is None and conversation_output is None and not conversation_only:
        claude_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        codex_id = os.environ.get("CODEX_THREAD_ID")
        if bool(claude_id) != bool(codex_id):
            args.claude_session_id = claude_id or None
            args.codex_thread_id = codex_id or None
            source_count = 1
    if source_count != 1:
        return _print_error(
            "transcript_path・--transcript・--claude-session-id・--codex-thread-id・--opencode-session-id・--delegation-record・--owner-session・"
            "カタログ走査はいずれか一つだけを指定する",
            next_action=(
                "対象の記録を`transcript_path`・`--transcript`・`--claude-session-id`・`--codex-thread-id`・`--opencode-session-id`・"
                "--delegation-record・--owner-session・カタログ走査のいずれか1つだけで指定して再実行する。"
                f"{_RECORD_QUERY_EXAMPLE}"
            ),
        )
    if args.opencode_db is not None and args.opencode_session_id is None:
        return _print_error(
            "--opencode-dbは--opencode-session-idと併用する",
            next_action="`--opencode-session-id <session ID>`を付けるか、`--opencode-db`を外して再実行する",
        )
    if args.codex_home is not None and args.codex_thread_id is None and args.delegation_record is None:
        return _print_error(
            "--codex-homeは--codex-thread-idか--delegation-recordと併用する",
            next_action="`--codex-thread-id`か`--delegation-record`を付けるか、`--codex-home`を外して再実行する",
        )
    if catalog_root is not None:
        assert since is not None and catalog_runtime is not None and args.observation_boundary is not None
        try:
            catalog_boundary = _parse_cli_timestamp(args.observation_boundary)
        except ValueError:
            return _print_error(f"観測境界が不正: {args.observation_boundary}", next_action=_BOUNDARY_NEXT_ACTION)
        if catalog_boundary < since:
            return _print_error("観測境界は開始境界以後を指定する", next_action=_BOUNDARY_NEXT_ACTION)
        if args.delivery_reposts:
            events, exit_code = catalog_delivery_events(Path(catalog_root), catalog_runtime, since, catalog_boundary)
        elif args.grep is not None:
            try:
                grep_pattern = re.compile(args.grep)
            except re.error as error:
                return _print_error(
                    f"正規表現が不正: {error}",
                    next_action="`--grep`の正規表現の構文を直して再実行する",
                )
            events, exit_code = _catalog_grep_events(
                Path(catalog_root), catalog_runtime, since, catalog_boundary, grep_pattern, catalog_selection
            )
        elif args.tool_calls:
            events, exit_code = _catalog_tool_call_events(
                Path(catalog_root), catalog_runtime, since, catalog_boundary, args.tool, input_pattern
            )
        elif args.user_events:
            events, exit_code = _catalog_user_event_events(
                Path(catalog_root),
                catalog_runtime,
                since,
                catalog_boundary,
                catalog_selection,
                user_pattern,
                preceding_assistant=args.with_preceding_assistant,
            )
        else:
            events, exit_code = _catalog_events(Path(catalog_root), catalog_runtime, since, catalog_boundary, catalog_selection)
        _print_events(_summary_events(events) if args.summary_only else events)
        return exit_code
    opencode_collected: list[_CollectedRecord] | None = None
    opencode_unresolved: list[_UnresolvedRecord] = []
    if args.opencode_session_id is not None:
        try:
            opencode_collected, opencode_unresolved = opencode_records.collect_records(
                args.opencode_session_id,
                Path(args.opencode_db) if args.opencode_db is not None else None,
                main_only=main_only,
            )
        except (OSError, UnicodeError, ValueError) as error:
            return _print_error(
                str(error),
                next_action="`--opencode-session-id`と`--opencode-db`の値・読取権限・schemaを確認する。"
                "記録を持つホストで再実行し、取得できないセッションの振り返りは未完了として扱う",
            )
        transcript_path = str(opencode_collected[0].path)
    elif args.delegation_record is not None or args.owner_session:
        try:
            transcript_path = str(
                delegation_record(args.delegation_record, args.codex_home)
                if args.delegation_record is not None
                else owner_record()
            )
        except AmbiguousOwnerRecord as error:
            candidates = "、".join(f"`{path}`" for path in error.paths)
            return _print_error(str(error), next_action=f"{RESOLVE_NEXT_ACTION}。候補: {candidates}")
        except (OSError, UnicodeError, ValueError) as error:
            return _print_error(str(error), next_action=RESOLVE_NEXT_ACTION)
    elif args.claude_session_id is not None:
        try:
            transcript_path = str(_resolve_claude_transcript(args.claude_session_id))
        except ValueError as error:
            return _print_error(
                str(error),
                next_action=(
                    "`--claude-session-id`の値を確かめる。記録が見つからない場合や複数ある場合は、"
                    f"`--transcript`で記録の絶対パスを直接渡して再実行する。{_RECORD_QUERY_EXAMPLE}"
                ),
            )
    elif args.codex_thread_id is not None:
        try:
            transcript_path = str(_codex_transcript(args.codex_thread_id, args.codex_home))
        except ValueError as error:
            return _print_error(
                str(error),
                next_action=(
                    "`--codex-thread-id`の値を確かめる。rolloutが別の場所にある場合は`--codex-home`へCodexのホームを渡し、"
                    f"複数ある場合は`--transcript`でrolloutの絶対パスを直接渡して再実行する。{_RECORD_QUERY_EXAMPLE}"
                ),
            )
    else:
        transcript_path = args.transcript or args.transcript_path

    main_recoveries: tuple[_RecordRecovery, ...] = ()
    loaded = None
    if opencode_collected is not None:
        records = opencode_collected[0].records
    else:
        loaded = _load_records_checked(transcript_path, allow_partial_records=args.allow_partial_records)
        records, main_recoveries = loaded.records, loaded.recoveries
        if records is None:
            failure = loaded.failure
            if failure is not None and failure.line is not None:
                return _print_error(
                    f"対象記録を読み込めない: {transcript_path}（{failure.reason}）",
                    next_action=(
                        f"`--transcript`の対象の{failure.line}行目のUTF-8とJSONを、原本を変更せず読取専用で確かめる。"
                        "正常部分だけを調査する場合は--allow-partial-recordsを付け、欠測位置とpartialを確認する。"
                        "完全な記録が必要な調査は、欠測範囲を未確認として保持する"
                    ),
                )
            return _print_error(
                f"対象記録を読み込めない: {transcript_path}",
                next_action="`--transcript`の対象の存在・読み取り権限・UTF-8を確かめて再実行する",
            )
    boundary = None
    if args.observation_boundary is not None:
        try:
            boundary = _parse_cli_timestamp(args.observation_boundary)
        except ValueError:
            return _print_error(f"観測境界が不正: {args.observation_boundary}", next_action=_BOUNDARY_NEXT_ACTION)
    elapsed_events = []
    elapsed_code = 0
    if args.elapsed_until is not None:
        event = _elapsed_until_event(records, args.elapsed_until)
        if isinstance(event, str):
            message = event or f"経過時間を算出できる記録が無い: {transcript_path}"
            elapsed_events.append(_error_event(message, next_action=_ELAPSED_UNTIL_NEXT_ACTION))
            elapsed_code = 2
        else:
            elapsed_events.append(event)
    if boundary is not None:
        records = _apply_observation_boundary(records, boundary)
    delegate_codex_home = args.codex_home if args.codex_thread_id is not None or args.delegation_record is not None else None
    if opencode_collected is not None:
        collected = [
            opencode_collected[0]._replace(records=records),
            *(
                item
                for item in opencode_collected[1:]
                if boundary is None or not _started_after_boundary(item.records, boundary)
            ),
        ]
        unresolved = opencode_unresolved
    else:
        collected, unresolved = _collect_records(
            transcript_path,
            records,
            delegate_codex_home,
            boundary,
            main_only=main_only,
            main_recoveries=main_recoveries,
            record_loader=functools.partial(_load_records_checked, allow_partial_records=args.allow_partial_records),
        )
    compaction_record_dir = (
        Path(args.compaction_record_dir)
        if args.compaction_record_dir is not None
        else _state_paths.state_dir() / "agents-server" / "compaction"
    )
    if loaded is not None and loaded.gaps:
        collected[0] = collected[0]._replace(gaps=loaded.gaps)
    gaps = [event for event in _collection_events(collected, []) if event["kind"] == "record-gap"]

    if args.bundle is not None:
        if conversation_only:
            assert conversation_output is not None
            events, exit_code = _conversation_bundle(
                collected,
                unresolved,
                Path(args.bundle),
                conversation_output,
                after=Path(args.after) if args.after else None,
                saved_scope=conversation_scope,
            )
        else:
            events, exit_code = _bundle_events(
                collected,
                unresolved,
                Path(args.bundle),
                compaction_record_dir,
                after=Path(args.after) if args.after else None,
                conversation_output=conversation_output,
            )
        provenance = _referenced_provenance_events(collected, events)
        _print_events(
            [
                {**event, **({"partial": True} if gaps else {})}
                for event in [*provenance, *(_summary_events(events) if args.summary_only else events), *gaps]
            ]
        )
        return exit_code
    events = list(elapsed_events)
    exit_code = elapsed_code
    if args.delivery_reposts:
        if since is not None and boundary is not None and boundary < since:
            return _print_error("観測境界は開始境界以後を指定する", next_action=_BOUNDARY_NEXT_ACTION)
        deliveries = delivery_events(collected, since=since, boundary=boundary)
        events.extend(
            event for event in deliveries if event["kind"] in {"delivery-repost-candidate", "post-delivery-long-text-candidate"}
        )
        events.append(delivery_summary(deliveries, since=since, boundary=boundary))
        events.extend(_unresolved_events(unresolved))
    if args.warn:
        events.extend(_warning_collection_events(collected, unresolved))
    if args.grep is not None:
        try:
            pattern = re.compile(args.grep)
        except re.error as error:
            return _print_error(
                f"正規表現が不正: {error}", next_action="`--grep`の正規表現の構文（括弧の対応、エスケープ）を直して再実行する"
            )
        events.extend(_grep_collection_events(collected, unresolved, pattern))
    if args.detail is not None or args.record_schema is not None:
        for locators, query in (
            (args.detail, _detail_collection_events),
            (args.record_schema, _record_schema_collection_events),
        ):
            if locators is not None:
                results, code = query(collected, locators)
                events.extend(results)
                exit_code = max(exit_code, code)
    if args.fixed_string is not None:
        events.extend(_fixed_string_collection_events(collected, unresolved, args.fixed_string))
    if args.context_at is not None:
        results, code = _context_at_events(collected, args.context_at, args.phrase or [])
        events.extend(results)
        exit_code = max(exit_code, code)
    if args.stats:
        stats = [*_stats_events(collected, compaction_record_dir), *quality_metrics(collected)]
        if args.summary_only:
            stats = [
                event
                for event in stats
                if event["kind"] in {"stats-total", "stats-summary", "stats-subagent", "stats-agent-thread"}
            ]
        events.extend([*stats, *_unresolved_events(unresolved)])
    if args.hook_notices:
        events.extend(
            [*_hook_notice_events([record for item in collected for record in item.records]), *_unresolved_events(unresolved)]
        )
    if args.user_events:
        if user_query:
            results = _query_user_events(
                collected[0], since, boundary, user_pattern, preceding_assistant=args.with_preceding_assistant
            )
            events.extend(
                [
                    *results,
                    {
                        "kind": "summary",
                        "count": sum(event["kind"] == "user" for event in results),
                        "undated_event_count": sum(event["kind"] == "user-event-undated" for event in results),
                    },
                ]
            )
        else:
            events.extend(_user_events_since(collected, since))
    if args.tool_calls:
        calls = _tool_call_collection_events(collected, unresolved, args.tool, input_pattern, since)
        if args.failure_fixed_string is not None:
            selected = session_evidence_failures.failure_calls(collected, calls, args.failure_fixed_string)
            summary = next(event for event in calls if event["kind"] == "tool-call-summary")
            calls = [
                *selected,
                *(event for event in calls if event["kind"] not in {"tool-call", "tool-call-summary"}),
                {**summary, **_tool_call_summary(selected, collected)},
            ]
        events.extend(calls)
    if args.reported_improvements:
        improvements = [
            {
                "kind": "reported-improvement",
                "record": item.record_id,
                "line": event["line"],
                "timestamp": event.get("timestamp"),
                "text": text,
            }
            for item in collected
            for event in _extract_records(item.records)
            for text in event.get("reported_improvements", [])
        ]
        events.extend(
            [
                *_sorted_tool_call_events(improvements),
                {"kind": "reported-improvement-summary", "count": len(improvements)},
                *_unresolved_events(unresolved),
            ]
        )
    return _write_query_events(args, query_modes, collected, events, gaps, exit_code, unresolved)


if __name__ == "__main__":
    raise SystemExit(main())
