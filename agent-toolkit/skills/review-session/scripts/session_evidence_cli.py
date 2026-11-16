"""セッション証拠照会の公開引数とJSONL表示。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from session_evidence_catalog import CATALOG_PARENT_FIELDS
from session_evidence_extract import (
    _CollectedRecord,
    _default_events,
    _error_event,
    _referenced_provenance_events,
    _UnresolvedRecord,
)

from agent_toolkit._agents_server import record_paths as _record_paths
from agent_toolkit._common import host_homes as _host_homes

_LOCAL_TIME_NOTE = "タイムゾーン（`Z`や`+09:00`など）を省いた値は実行ホストのローカルタイムゾーンの時刻として扱う。"
"""時刻を受け取る引数の説明へ加える、タイムゾーンを省いた値の解釈。"""


_HOOK_RECORD_NOTE = (
    "Claude Codeの記録は、出力（標準出力、標準エラー、追加コンテキスト）を返さなかったhookの実行を残さない。"
    "このためhookの記録が無いことや一致0件は、そのhookが発火しなかったことを示さない。"
)
"""hookの記録の有無を照会する引数の説明へ加える、記録が残る条件。"""


def _print_events(events: list[dict[str, Any]], *, collected: list[_CollectedRecord] | None = None) -> None:
    """イベント列を1イベント1 JSONのJSONLとして標準出力へ書く。

    `collected`を渡すと、イベント列が参照する記録とその起動元の`record-provenance`を前置する。
    """
    seen_gaps = set()
    for event in [*(_referenced_provenance_events(collected, events) if collected is not None else []), *events]:
        if event.get("kind") == "record-gap":
            key = (event["record"], event["line"])
            if key in seen_gaps:
                continue
            seen_gaps.add(key)
        print(json.dumps(event, ensure_ascii=False))


def _summary_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """照会が生成した件数・判定・保存先と診断を保持して表示を選ぶ。"""
    omitted = {
        "match",
        "context-match",
        "tool-call",
        "warning",
        "bundle-locator",
        "bundle-warning-group",
        "delivery-repost-candidate",
    }
    selected = [
        {key: value for key, value in event.items() if key != "locators"} if event["kind"] == "fixed-string-summary" else event
        for event in events
        if event["kind"] not in omitted
    ]
    if any(event["kind"] == "warning" for event in events):
        selected.append(
            {"kind": "warning-summary", "count": sum(event["kind"] == "warning" and "record" in event for event in events)}
        )
    return selected


# 照会の公開指定と、併用できる表示・入力形式を同じ表で管理する。
_QUERY_OPTIONS = {
    "warn": (True, False),
    "grep": (True, True),
    "fixed_string": (True, False),
    "context_at": (True, False),
    "tool_calls": (True, True),
    "bundle": (True, False),
    "stats": (True, False),
    "user_events": (False, True),
    "detail": (False, False),
    "record_schema": (False, False),
    "elapsed_until": (False, False),
    "hook_notices": (False, False),
    "reported_improvements": (False, False),
    "delivery_reposts": (True, True),
}


def _active_queries(args: argparse.Namespace) -> list[str]:
    """指定された照会名を、許可する組の表から取得する。"""
    return [name for name in _QUERY_OPTIONS if getattr(args, name) is not None and getattr(args, name) is not False]


def _query_option_names(names: list[str]) -> str:
    """照会名を修正操作へ渡す公開オプションの列へ変換する。"""
    return "・".join(f"`--{name.replace('_', '-')}`" for name in names)


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
            "stats-toolの合計秒は並列実行分を含むため壁時計時間とは一致しない。"
        )
    )
    parser.add_argument("--allow-partial-records", action="store_true", help="破損行を欠測として保持して正常行を読む。")
    parser.add_argument("--failure-fixed-string", help="失敗したツール結果の固定文字列。--tool-callsと併用する。")
    scan = parser.add_mutually_exclusive_group()
    scan.add_argument("--scan-claude-projects", metavar="DIR", help="Claudeの複数projectを収納する絶対ディレクトリ。")
    scan.add_argument("--scan-codex-history", metavar="DIR", help="Codexの履歴を収納する絶対ディレクトリ。")
    parser.add_argument(
        "transcript_path",
        nargs="?",
        help="transcriptの絶対パス。読み込み失敗時はエラーイベントを出力して終了コード2を返す。"
        "`--transcript`・`--claude-session-id`・`--codex-thread-id`・`--opencode-session-id`・カタログ走査とは併用しない。",
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
        "--opencode-session-id",
        metavar="SESSION_ID",
        help="OpenCodeのses_で始まるセッションIDから、SQLiteのsession・message・partを読取専用で取得する。",
    )
    parser.add_argument(
        "--opencode-db",
        metavar="PATH",
        help="OpenCodeのSQLite記録。--opencode-session-idと併用する。省略時はXDG_DATA_HOMEか~/.local/shareのopencode/opencode.db。",
    )
    parser.add_argument(
        "--codex-home",
        metavar="DIR",
        help="Codexの記録の保存先。`--codex-thread-id`か`--delegation-record`と併用する。",
    )
    parser.add_argument(
        "--delegation-record",
        choices=("parent", "root"),
        help="委譲先自身の環境と既存の親子対応から記録を解決する。parentは直接の委譲元の操作照会、"
        "rootは最上位の会話の--user-eventsに使う。解決不能時は終了コード2で明示指定による再実行を案内する。",
    )
    parser.add_argument(
        "--owner-session",
        action="store_true",
        help="環境の所有ルートからセッション記録を解決する。Claude Codeの委譲先で最上位の発話を照会する。"
        "会話を示さないルートと記録が0件または複数の場合は終了コード2で再実行を案内する。",
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
    parser.add_argument("--catalog-workflow", metavar="NAME", help="カタログの親行をworkflowの完全一致で選ぶ。")
    parser.add_argument(
        "--catalog-known-workflow", action="store_true", help="カタログの親行をworkflowがunknownでないものに限る。"
    )
    parser.add_argument("--catalog-cwd-contains", metavar="TEXT", help="カタログの親行をcwdの部分文字列で選ぶ。")
    parser.add_argument(
        "--catalog-fields",
        metavar="FIELD,FIELD",
        help="カタログの親行の表示項目をASCIIカンマ区切りの指定順で返す。省略時は全項目。kindと走査要約は常に返す。"
        "選択時の要約はselected_parent_countと走査したparent_record_countを区別する。選択できる項目: "
        + ",".join(CATALOG_PARENT_FIELDS),
    )
    parser.add_argument(
        "--compaction-record-dir",
        metavar="DIR",
        help="Codexコンパクションの計測記録ディレクトリ。省略時はagent-toolkitのstate_dir配下を使う。",
    )
    parser.add_argument("--after", metavar="PATH", help="前回のscope JSON。bundleの分析範囲を処理済み位置より後へ限定する。")
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
        "キー名とtypeなどの管理用フィールドの値は検索しない。会話圧縮は--statsのstats-compactionから得る。"
        "委譲先の一致も含み、record-provenanceの記録由来から区別できる。"
        "カタログと併用すると、sinceより後でobservation-boundary以前の一致を記録別に返す。"
        "summary-onlyは記録別件数と合計を残し、時刻不明の一致はundated_match_countへ分ける。"
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
        "本文は返さず、0件も文字列ごとに明示する。複数指定ではオプションを繰り返す。"
        "キー名とtypeなどの管理用フィールドの値は検索しない。会話圧縮は--statsのstats-compactionから得る。"
        "委譲先の一致も含み、record-provenanceの記録由来から区別できる。" + _HOOK_RECORD_NOTE,
    )
    parser.add_argument(
        "--record-schema",
        action="append",
        default=None,
        metavar="RECORD:LINE",
        help="指定したlocatorの元JSON recordについてkey pathと観測したJSON型だけを返す。"
        "record由来の値と本文は返さない。複数指定ではオプションを繰り返す。`--detail`と併用でき、"
        "双方の全locatorをkind・record・lineで識別して返す。結果のkindを区別できる照会と併用できる。",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="経過時間、トークン消費、ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を照会する。"
        "コンパクションの発生位置と回数は`stats-compaction`と`stats-compaction-total`が返し、"
        "圧縮で終わる区間ごとのツール出力とエージェント向け文書の表示のバイト数、表示の多いエージェント向け文書は`stats-compaction-usage`が返す。"
        "`elapsed_seconds`は記録の最初と最後の差であり、turnの完了を持つ記録では最初のturnの開始から"
        "最後のturnの完了までの`turn_elapsed_seconds`、完了時刻の`last_turn_completed_at`、"
        "完了後に続く記録の`after_last_turn_seconds`を併せて返す。",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="`--grep`・`--fixed-string`・`--context-at`・`--tool-calls`・`--warn`・`--bundle`・`--stats`と併用し、"
        "`--delivery-reposts`とも併用でき、件数・判定・保存先だけを返す。警告は重複除去後のwarningイベント数を返す。"
        "statsは総量・親・サブエージェント・threadの要約を全フィールド・既存順で返す。診断と由来を保持する。",
    )
    parser.add_argument(
        "--delivery-reposts",
        action="store_true",
        help="Claude Codeの非sidechainの送達呼び出しと後続の地の文から再掲候補を集計する。"
        "送達60文字以上・地の文30文字以上、空白を除いた4-gramの少ない側が20要素以上で共通割合0.5以上を候補とする。"
        "送達ごとに次の人間入力までの最初の一致を数え、確認済みの再掲とは区別する。"
        "--sinceより後・--observation-boundary以前の送達を選び、UTCの送達日別と全期間の件数・割合を返す。"
        "期間指定時の時刻不明の送達は除く。--summary-onlyでは集計だけを返す。"
        "--catalog-claude-projectとも併用できる。",
    )
    parser.add_argument(
        "--reported-improvements",
        action="store_true",
        help="全収集記録のreported_improvementsを、record・line・timestamp・text付きで1報告行ずつ返す。"
        "同文の別出所と同じ出所の複数行を保持し、本文を切り詰めない。0件は専用の要約で示す。",
    )
    parser.add_argument(
        "--hook-notices",
        action="store_true",
        help="hook実行の記録（追加コンテキスト・システムメッセージ・遮断エラー・実行成功）に"
        "格納された通知本文だけを集計し、hook識別子・発動元・タグ・種別ごとの件数と"
        "重複を除いた通知件数を照会する。Claude Code形式の記録と、Codexの由来付きhook追加コンテキスト・遮断結果を含む。"
        + _HOOK_RECORD_NOTE,
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
        "スキル展開、実行環境の生成本文およびClaude Codeの中断の定型文は除く。"
        "カタログと併用すると親セッションを横断し、workflow・cwdの条件で親を選べる。"
        "検索は元記録行の時刻で期間を判定し、時刻なし入力はuser-event-undatedで示す。"
        "要約は走査した親・選定した親・一致イベントの件数を区別し、0件も明示する。件数上限は設けない。",
    )
    parser.add_argument(
        "--user-match-fixed", metavar="TEXT", help="`--user-events`の人間が入力した値を固定文字列の部分一致で検索する。"
    )
    parser.add_argument("--user-match-regex", metavar="REGEX", help="`--user-events`の人間が入力した値を正規表現で検索する。")
    parser.add_argument(
        "--with-preceding-assistant",
        action="store_true",
        help="`--user-events`の各一致へ同じ記録の直前のアシスタント発話を全文・位置・時刻付きで添える。"
        "該当発話が無い場合はnull。単一記録とカタログで共通して使える。",
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
        "一致する全イベントを入力順・全文で返す。lineは元記録の正の整数位置を指す。出力の物理行番号とは別の値である。",
    )
    parser.add_argument(
        "--since",
        metavar="TIMESTAMP",
        help="`--user-events`・`--delivery-reposts`・`--tool-calls`またはカタログ走査の開始境界をISO 8601の時刻で指定する。"
        "カタログ走査では必須、それ以外では省略すると記録の最初からを対象とする。"
        "`--tool-calls`では時刻不明の呼び出しを除き、要約のmissing_timestampsに件数を示す。" + _LOCAL_TIME_NOTE,
    )
    parser.add_argument(
        "--tool-calls",
        action="store_true",
        help="メイン記録と全ての委譲先の記録（Claude Code形式とCodex形式）のツール呼び出しを、1件ずつ`tool-call`イベント"
        "（`record`・`line`・`timestamp`・`tool`・`call_id`・切り詰めない代表入力`text`・結果の記録行`result_line`）で"
        "時刻順に返し、末尾の`tool-call-summary`で件数、ツール名ごとと記録ごとの件数を返す。"
        "`by_role`は役割文書がある記録を`document:<役割名>`、無い記録を`record:<role>`へまとめ、"
        "各内訳の`count`・`by_tool`・起動入力の`by_mode`を同じ照会で返す。"
        "`record-provenance`は`tool-call`が参照する記録とその起動元の記録について、親の由来・起動mode・役割文書を示し、"
        "未取得の値はnullとなる。"
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
        "（全収集記録の発話、ツール呼び出しと成功・失敗の結果）の抽出を"
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
        args.reported_improvements,
        args.delivery_reposts,
    )


def _write_query_events(
    args: argparse.Namespace,
    query_modes: tuple[bool, ...],
    collected: list[_CollectedRecord],
    events: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    exit_code: int,
    unresolved: list[_UnresolvedRecord],
) -> int:
    """照会の重複を除き、由来と部分取得の診断を保持して表示する。"""
    if any(query_modes):
        seen = set()
        unique = []
        for event in events:
            if event.get("kind") in {"unresolved-record", "unresolved-delegation"}:
                key = json.dumps(event, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
            unique.append(event)
        provenance = (
            args.warn
            or args.grep is not None
            or args.fixed_string is not None
            or args.stats
            or args.hook_notices
            or args.tool_calls
            or args.reported_improvements
            or args.delivery_reposts
            or args.user_events
            and (args.delegation_record is not None or args.owner_session)
        )
        provenance_events = _referenced_provenance_events(collected, unique) if provenance else []
        if args.summary_only:
            unique = _summary_events(unique)
            if args.warn and not any(event["kind"] == "warning-summary" for event in unique):
                unique.append({"kind": "warning-summary", "count": 0})
        _print_events([{**event, **({"partial": True} if gaps else {})} for event in [*provenance_events, *unique, *gaps]])
        return exit_code

    _print_events([{**event, **({"partial": True} if gaps else {})} for event in _default_events(collected, unresolved)])
    return 0
