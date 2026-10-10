"""Claude CodeとCodexのtranscript、および委譲先のAntigravityログから振り返り用の時系列証拠を抽出し、照会する。

モードを指定しない場合はセッション全体の時系列イベントをJSONLで出力し、各イベントへ由来行の行番号`line`を付ける。
`--warn`・`--grep`・`--detail`・`--stats`・`--hook-notices`・`--user-events`・`--tool-calls`の照会モードは、抽出結果に無い詳細をtranscriptから
1コマンドで取得するためのもので、都度のワンライナーによる再解析を置き換える。
`--bundle`の集約実行は、通常表示と`--warn`・`--stats`・`--hook-notices`の走査、問題候補および会話の流れの抽出を
1回の記録読み込みでまとめて行い、走査ごとの全量を指定ディレクトリ配下のファイルへ書いて標準出力へは要約だけを返す。
対象の記録はtranscriptの絶対パス、Claude Codeのセッション識別子、Codex thread IDおよびカタログ走査のいずれか1つで指定する。
ユーザーイベントの由来は原文と生成標識から判定し、その結果を保持してから通常表示の本文を短縮する。
`--user-events`は人間の発話と確認回答だけを逐語引用の原文として返す。
本文を切り詰めず、確認回答には提示した全選択肢を含め、スキル展開、実行環境の生成本文およびClaude Codeの中断の定型文は除く。
`--user-events-file`と`--user-event-at`は、その保存済みJSONLから元記録のrecordとlineに一致する全イベントを返す。
元記録の読み込みと由来の再判定をせず、全欄と値を保持する。

本スクリプトはデータ抽出を目的とし、合否を判定しないため、
`agent-toolkit:writing-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と照会不能（対象記録の読込不能・モード併用・不正な正規表現・範囲外の行番号）を
終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from session_evidence_bundle import (
    _bundle_events,
)
from session_evidence_catalog import (
    _catalog_events,
    _catalog_tool_call_events,
)
from session_evidence_context import (
    _context_at_events,
    _record_schema_collection_events,
)
from session_evidence_detail import (
    _detail_collection_events,
    _fixed_string_collection_events,
    _grep_collection_events,
)
from session_evidence_extract import (
    _BOUNDARY_NEXT_ACTION,
    _ELAPSED_UNTIL_NEXT_ACTION,
    _SINCE_NEXT_ACTION,
    _default_events,
    _error_event,
    _load_records,
    _Runtime,
    _unresolved_events,
)
from session_evidence_hook_notices import (
    _hook_notice_events,
)
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
    _tool_call_collection_events,
)
from session_evidence_user_events import (
    _saved_user_events_at,
    _user_events_since,
)
from session_evidence_warn import (
    _warning_collection_events,
)

from agent_toolkit._agents_server import record_paths as _record_paths
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import state_paths as _state_paths

_CLAUDE_ONLY_NOTE = "集計の母集団はClaude Code形式の記録に限られ、Codex形式の記録からは件数が上がらない。"


_LOCAL_TIME_NOTE = "タイムゾーン（`Z`や`+09:00`など）を省いた値は実行ホストのローカルタイムゾーンの時刻として扱う。"
"""時刻を受け取る引数の説明へ加える、タイムゾーンを省いた値の解釈。"""


_HOOK_RECORD_NOTE = (
    "Claude Codeの記録は、出力（標準出力、標準エラー、追加コンテキスト）を返さなかったhookの実行を残さない。"
    "このためhookの記録が無いことや一致0件は、そのhookが発火しなかったことを示さない。"
)
"""hookの記録の有無を照会する引数の説明へ加える、記録が残る条件。"""


def _print_events(events: list[dict[str, Any]]) -> None:
    """イベント列を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    for event in events:
        print(json.dumps(event, ensure_ascii=False))


def _print_error(text: str, *, next_action: str) -> int:
    """照会不能を示すエラーを出力し、終了コード2を返す。"""
    _print_events([_error_event(text, next_action=next_action)])
    return 2


def _codex_transcript(thread_id: str, codex_home: str | None) -> Path:
    """Codex thread IDから親transcriptのロールアウトを1件解決する。一致が0件または複数件の場合は例外を送出する。"""
    root = Path(codex_home) if codex_home else _host_homes.codex_home()
    candidates = _record_paths.find_codex_records(thread_id, codex_home=root)
    if not candidates:
        raise ValueError(f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが{root / 'sessions'}配下に無い")
    first, *others = candidates
    if others:
        joined = ", ".join(str(path) for path in candidates)
        raise ValueError(f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが複数ある: {joined}")
    return first


def _build_parser() -> argparse.ArgumentParser:
    """オプションを指定しない場合の抽出と照会モードの引数を定義する。"""
    parser = argparse.ArgumentParser(
        description=(
            "transcriptから振り返り用の時系列証拠を抽出・照会する。--statsは経過時間、トークン消費、"
            "ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を返す。"
            "メイン記録・補助記録ともセッション全体を対象とする。"
            "stats-toolの合計秒は並列実行分を含むため壁時計時間とは一致しない。" + _CLAUDE_ONLY_NOTE
        )
    )
    parser.add_argument(
        "transcript_path",
        nargs="?",
        help="transcriptの絶対パス。読み込み失敗時はエラーイベントを出力して終了コード2を返す。"
        "`--transcript`・`--claude-session-id`・`--codex-thread-id`・カタログ走査とは併用しない。",
    )
    parser.add_argument(
        "--transcript",
        metavar="PATH",
        help="位置引数と同じ単一transcriptの絶対パス。"
        "位置引数・セッション識別子・Codex thread ID・カタログ走査とは併用しない。",
    )
    parser.add_argument(
        "--claude-session-id",
        metavar="SESSION_ID",
        help="Claude Codeのセッション識別子（`CLAUDE_CODE_SESSION_ID`の値など）から、`projects`配下の親transcriptを"
        "解決して抽出を開始する。一致する親セッションの記録が1件でない場合はエラーイベントを出力して終了コード2を返す。",
    )
    parser.add_argument(
        "--codex-thread-id",
        metavar="THREAD_ID",
        help="Codex thread IDから親transcriptの記録ファイルを解決して抽出を開始する。"
        "保存先は`--codex-home`、空でない`CODEX_HOME`、`~/.codex`の順に解決し、"
        "`sessions`配下で完全suffix一致するrolloutが1件でない場合はエラーイベントを出力して終了コード2を返す。",
    )
    parser.add_argument(
        "--codex-home",
        metavar="DIR",
        help="Codexの記録の保存先。`--codex-thread-id`と併用する。",
    )
    catalog_group = parser.add_mutually_exclusive_group()
    catalog_group.add_argument(
        "--catalog-claude-project",
        metavar="DIR",
        help="指定したClaude projectディレクトリ内だけを走査し、比較用の親セッションカタログを返す。"
        "`--since`と`--observation-boundary`が必須。",
    )
    catalog_group.add_argument(
        "--catalog-codex-history",
        metavar="DIR",
        help="指定したCodex履歴ディレクトリ内だけを走査し、比較用の親セッションカタログを返す。"
        "`--since`と`--observation-boundary`が必須。",
    )
    parser.add_argument(
        "--compaction-record-dir",
        metavar="DIR",
        help="Codexコンパクションの計測記録ディレクトリ。省略時はagent-toolkitのstate_dir配下を使う。",
    )
    parser.add_argument(
        "--observation-boundary",
        metavar="TIMESTAMP",
        help="ISO 8601の時刻を観測境界とし、メイン記録のうちその時刻より後の`timestamp`を持つレコードを"
        "全モードの対象外にする。委譲先の記録へは適用しない。`--detail`の行番号は元ファイルの行番号を維持する。"
        "解析できない値はエラーイベントを出力して終了コード2を返す。" + _LOCAL_TIME_NOTE,
    )
    parser.add_argument(
        "--elapsed-until",
        metavar="TIMESTAMP",
        help="ISO 8601の時刻を経過時間の終端とし、メイン記録の最初のレコードからその時刻までの経過秒数を返す。"
        "解析できない値、算出できる記録が無い場合およびその時刻が最初のレコードより前の場合は"
        "エラーイベントを出力して終了コード2を返す。" + _LOCAL_TIME_NOTE,
    )
    parser.add_argument(
        "--warn",
        action="store_true",
        help="セッション全体のエントリから、行頭の警告マーカーまたは"
        "構造化された警告フィールドを持つ実行時警告だけを照会する。任意文字列の検索は`--grep`を使う。",
    )
    parser.add_argument(
        "--grep",
        metavar="REGEX",
        help="エントリ内の全本文（hook通知を含む。管理用フィールドと"
        "本スクリプト自身の実行記録は除く）を正規表現で検索し、"
        "一致行と一致エントリ数を照会する。各一致行は元記録行の時刻`timestamp`（無ければnull）を持つ。" + _HOOK_RECORD_NOTE,
    )
    parser.add_argument(
        "--detail",
        action="append",
        default=None,
        metavar="RECORD:LINE",
        help="指定した<記録>:<行番号>（数値だけならメイン記録）のエントリの詳細（tool_useの入力全体・tool_result本文。"
        "本文が退避されている場合はツール実行結果側の本文）を照会する。ユーザーとアシスタントの発話本文は切り詰めずに返す。"
        "複数指定ではオプションを繰り返す。"
        "各イベントは元記録行の時刻`timestamp`（無ければnull）を持つ。"
        "出力量の上限で本文を省略したエントリのイベントには`omitted`を付ける。",
    )
    parser.add_argument(
        "--fixed-string",
        action="append",
        default=None,
        metavar="TEXT",
        help="大小文字を区別する固定文字列ごとに、既存`--grep`と同じ論理entryの一致件数と全locatorだけを返す。"
        "本文は返さず、0件も文字列ごとに明示する。複数指定ではオプションを繰り返す。" + _HOOK_RECORD_NOTE,
    )
    parser.add_argument(
        "--record-schema",
        action="append",
        default=None,
        metavar="RECORD:LINE",
        help="指定したlocatorの元JSON recordについてkey pathと観測したJSON型だけを返す。"
        "record由来の値と本文は返さない。複数指定ではオプションを繰り返す。`--detail`と併用でき、"
        "双方の全locatorをkind・record・lineで識別して返す。他の照会とは併用しない。",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="経過時間、トークン消費、ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を照会する。"
        "コンパクションの発生位置と回数は`stats-compaction`と`stats-compaction-total`が返す。"
        "`elapsed_seconds`は記録の最初と最後の差であり、turnの完了を持つ記録では最初のturnの開始から"
        "最後のturnの完了までの`turn_elapsed_seconds`、完了時刻の`last_turn_completed_at`、"
        "完了後に続く記録の`after_last_turn_seconds`を併せて返す。" + _CLAUDE_ONLY_NOTE,
    )
    parser.add_argument(
        "--hook-notices",
        action="store_true",
        help="hook実行の記録（追加コンテキスト・システムメッセージ・遮断エラー・実行成功）に"
        "格納された通知本文だけを集計し、hook識別子・発動元・タグ・種別ごとの件数と"
        "重複を除いた通知件数を照会する。" + _CLAUDE_ONLY_NOTE + _HOOK_RECORD_NOTE,
    )
    parser.add_argument(
        "--context-at",
        metavar="RECORD:LINE",
        help="指定した<記録>:<行番号>（数値だけならメイン記録）の時点で、`--phrase`の各文字列がその記録の文脈にあったかを"
        "判定する。母集団は指定した記録の指定行より前の行だけとし、親セッションや他の委譲先の記録は含めない。"
        "判定はphraseごとの`context-verdict`（`present`・`dropped-by-compaction`・`absent`）と、"
        "一致ごとの`context-match`（行、時刻、本文、文脈へ入る方法を表す`channel`、有効な圧縮境界より前か）で返す。"
        "記録が不明、行番号が範囲外、`--phrase`が無いか空の場合はエラーイベントを出力して終了コード2を返す。",
    )
    parser.add_argument(
        "--phrase",
        action="append",
        default=None,
        help="`--context-at`で判定する固定文字列。繰り返し指定できる。一致は本文の1行ごとの部分文字列で判定するため、"
        "事象時点の条文の1行の範囲から選ぶ（原文の折り返しをまたぐ文字列は一致しない）。"
        "先頭がハイフンの文字列は`--phrase=<文字列>`の形で渡す。",
    )
    parser.add_argument(
        "--user-events",
        action="store_true",
        help="メイン記録にある人間の発話と確認回答を、観測境界まで全文で照会する。"
        "`--since`を指定した場合はその時刻より後だけを、省略した場合は記録の最初からを対象とする。"
        "スキル展開、実行環境の生成本文およびClaude Codeの中断の定型文は除く。",
    )
    parser.add_argument(
        "--user-events-file",
        metavar="PATH",
        help="保存済みの--user-events出力の絶対パス。--user-event-atと組で使い、元記録の指定・照会モードとは併用しない。",
    )
    parser.add_argument(
        "--user-event-at",
        metavar="RECORD:LINE",
        help="保存済み出力のrecord欄とline欄の値の組。末尾のコロンで区切り、record内のコロンは保持する。"
        "一致する全イベントを入力順・全文で返す。lineは元記録の正の整数位置で、出力の物理行番号ではない。",
    )
    parser.add_argument(
        "--since",
        metavar="TIMESTAMP",
        help="`--user-events`またはカタログ走査の開始境界をISO 8601の時刻で指定する。"
        "カタログ走査では必須、`--user-events`では省略すると記録の最初からを対象とする。" + _LOCAL_TIME_NOTE,
    )
    parser.add_argument(
        "--tool-calls",
        action="store_true",
        help="メイン記録と全ての委譲先の記録（Claude Code形式とCodex形式）のツール呼び出しを、1件ずつ`tool-call`イベント"
        "（`record`・`line`・`timestamp`・`tool`・`call_id`・切り詰めない代表入力`text`・結果の記録行`result_line`）で"
        "時刻順に返し、末尾の`tool-call-summary`で件数、ツール名ごとと記録ごとの件数を返す。"
        "`record`と`line`、または`record`と`result_line`を`--detail <記録>:<行番号>`へ渡すと入力と結果の全文を得られる。"
        "本スクリプト自身の呼び出しも含める。カタログ走査と併用すると、窓の中の親セッションごとに、"
        "走査rootの外の委譲先を含む記録から`timestamp`が`--since`より後で`--observation-boundary`以前の呼び出しを"
        "`session_id`付きで返し、要約へ親セッションごとの件数と被覆範囲を加える。",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=None,
        metavar="NAME",
        help="`--tool-calls`で返す呼び出しを、記録上のツール名と完全一致するものに限る。"
        "繰り返し指定するといずれかに一致した呼び出しを返す。",
    )
    parser.add_argument(
        "--input-regex",
        metavar="REGEX",
        help="`--tool-calls`で返す呼び出しを、代表入力の文字列全体へ`re.search`で一致するものに限る。"
        "行ごとには適用しないため、`^`は代表入力の先頭だけに一致し、ヒアドキュメントの本文などコマンドの途中に"
        "埋め込まれた同じ文字列を除ける。`--tool`と併用すると両方に一致した呼び出しを返す。",
    )
    parser.add_argument(
        "--bundle",
        metavar="DIR",
        help="通常表示、`--warn`、`--stats`および`--hook-notices`の走査と、問題候補と会話の流れ"
        "（メイン記録の発話、ツール呼び出しと失敗の標識）の抽出を"
        "1回の記録読み込みで行い、走査ごとの全量を指定したディレクトリ配下のファイルへ書く。"
        "標準出力へは、走査ごとのファイルの絶対パスとイベント件数、通常表示のイベント種別ごとの件数、"
        "問題候補の特定に用いるイベントの位置と本文の冒頭、および警告の種別ごとの件数を返す。"
        "集計と通知の走査の全量は保存先のファイルから読む。"
        "指定するディレクトリは実在していることを要する。他の照会オプションとは併用しない。",
    )
    return parser


def _single_transcript_query_modes(args: argparse.Namespace) -> tuple[bool, ...]:
    """単一transcriptの照会モードを返す。"""
    return (
        args.warn,
        args.grep is not None,
        args.detail is not None or args.record_schema is not None,
        args.fixed_string is not None,
        args.stats,
        args.hook_notices,
        args.bundle is not None,
        args.elapsed_until is not None,
        args.user_events,
        args.context_at is not None,
        args.tool_calls,
    )


def main(argv: list[str] | None = None) -> int:
    """証拠または照会結果を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
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
    if sum(query_modes) > 1:
        return _print_error(
            "--warn・--grep・--detail・--fixed-string・--record-schema・--stats・--hook-notices・--bundle・--elapsed-until・"
            "--user-events・--context-at・--tool-callsは、--detailと--record-schemaの組以外では併用できない",
            next_action="本文と構造は`--detail`・`--record-schema`を併用できる。他の照会は1回に1つだけ指定して再実行する",
        )
    catalog_root = args.catalog_claude_project or args.catalog_codex_history
    catalog_runtime: _Runtime | None = (
        "claude" if args.catalog_claude_project else "codex" if args.catalog_codex_history else None
    )
    if catalog_root is not None and any(query_modes) and not args.tool_calls:
        return _print_error(
            "カタログ走査は`--tool-calls`以外の単一transcriptの照会モードと併用できない",
            next_action="カタログ走査（`--catalog-claude-project`・`--catalog-codex-history`）と単一transcriptの照会を別々に実行する",
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
    if args.since is not None and not args.user_events and catalog_root is None:
        return _print_error(
            "--sinceは--user-eventsまたはカタログ走査と併用する",
            next_action="`--user-events`かカタログ走査の引数を付けるか、`--since`を外して再実行する",
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
        args.catalog_claude_project,
        args.catalog_codex_history,
    )
    if sum(source is not None for source in sources) != 1:
        return _print_error(
            "transcript_path・--transcript・--claude-session-id・--codex-thread-id・カタログ走査はいずれか一つだけを指定する",
            next_action=(
                "対象の記録を`transcript_path`・`--transcript`・`--claude-session-id`・`--codex-thread-id`・"
                "カタログ走査のいずれか1つだけで指定して再実行する"
            ),
        )
    if args.codex_home is not None and args.codex_thread_id is None:
        return _print_error(
            "--codex-homeは--codex-thread-idと併用する",
            next_action="`--codex-thread-id`を付けるか、`--codex-home`を外して再実行する",
        )
    if catalog_root is not None:
        assert since is not None and catalog_runtime is not None and args.observation_boundary is not None
        try:
            catalog_boundary = _parse_cli_timestamp(args.observation_boundary)
        except ValueError:
            return _print_error(f"観測境界が不正: {args.observation_boundary}", next_action=_BOUNDARY_NEXT_ACTION)
        if catalog_boundary < since:
            return _print_error("観測境界は開始境界以後を指定する", next_action=_BOUNDARY_NEXT_ACTION)
        if args.tool_calls:
            events, exit_code = _catalog_tool_call_events(
                Path(catalog_root), catalog_runtime, since, catalog_boundary, args.tool, input_pattern
            )
        else:
            events, exit_code = _catalog_events(Path(catalog_root), catalog_runtime, since, catalog_boundary)
        _print_events(events)
        return exit_code
    if args.claude_session_id is not None:
        try:
            transcript_path = str(_resolve_claude_transcript(args.claude_session_id))
        except ValueError as error:
            return _print_error(
                str(error),
                next_action=(
                    "`--claude-session-id`の値を確かめる。記録が見つからない場合や複数ある場合は、"
                    "`--transcript`で記録の絶対パスを直接渡して再実行する"
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
                    "複数ある場合は`--transcript`でrolloutの絶対パスを直接渡して再実行する"
                ),
            )
    else:
        transcript_path = args.transcript or args.transcript_path

    records = _load_records(transcript_path)
    if records is None:
        return _print_error(
            f"対象記録を読み込めない: {transcript_path}",
            next_action="`--transcript`へ実在する記録の絶対パスを渡し、読み取り権限を確かめて再実行する",
        )
    boundary = None
    if args.observation_boundary is not None:
        try:
            boundary = _parse_cli_timestamp(args.observation_boundary)
        except ValueError:
            return _print_error(f"観測境界が不正: {args.observation_boundary}", next_action=_BOUNDARY_NEXT_ACTION)
    if args.elapsed_until is not None:
        event = _elapsed_until_event(records, args.elapsed_until)
        if isinstance(event, str):
            message = event or f"経過時間を算出できる記録が無い: {transcript_path}"
            return _print_error(message, next_action=_ELAPSED_UNTIL_NEXT_ACTION)
        _print_events([event])
        return 0
    if boundary is not None:
        records = _apply_observation_boundary(records, boundary)
    delegate_codex_home = args.codex_home if args.codex_thread_id is not None else None
    collected, unresolved = _collect_records(transcript_path, records, delegate_codex_home, boundary)
    compaction_record_dir = (
        Path(args.compaction_record_dir)
        if args.compaction_record_dir is not None
        else _state_paths.state_dir() / "agents-server" / "compaction"
    )

    if args.bundle is not None:
        events, exit_code = _bundle_events(collected, unresolved, Path(args.bundle), compaction_record_dir)
        _print_events(events)
        return exit_code
    if args.warn:
        _print_events(_warning_collection_events(collected, unresolved))
        return 0
    if args.grep is not None:
        try:
            pattern = re.compile(args.grep)
        except re.error as error:
            return _print_error(
                f"正規表現が不正: {error}", next_action="`--grep`の正規表現の構文（括弧の対応、エスケープ）を直して再実行する"
            )
        _print_events(_grep_collection_events(collected, unresolved, pattern))
        return 0
    if args.detail is not None or args.record_schema is not None:
        events = []
        exit_code = 0
        for locators, query in (
            (args.detail, _detail_collection_events),
            (args.record_schema, _record_schema_collection_events),
        ):
            if locators is not None:
                results, code = query(collected, locators)
                events.extend(results)
                exit_code = max(exit_code, code)
        _print_events(events)
        return exit_code
    if args.fixed_string is not None:
        _print_events(_fixed_string_collection_events(collected, unresolved, args.fixed_string))
        return 0
    if args.context_at is not None:
        events, exit_code = _context_at_events(collected, args.context_at, args.phrase or [])
        _print_events(events)
        return exit_code
    if args.stats:
        _print_events([*_stats_events(collected, compaction_record_dir), *_unresolved_events(unresolved)])
        return 0
    if args.hook_notices:
        _print_events(
            [*_hook_notice_events([record for item in collected for record in item.records]), *_unresolved_events(unresolved)]
        )
        return 0
    if args.user_events:
        _print_events(_user_events_since(collected, since))
        return 0
    if args.tool_calls:
        _print_events(_tool_call_collection_events(collected, unresolved, args.tool, input_pattern))
        return 0

    _print_events(_default_events(collected, unresolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
