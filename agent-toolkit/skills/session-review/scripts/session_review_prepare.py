"""セッション振り返りの入力を抽出し、会話・比較材料・候補・統計を作業ディレクトリへ書く。

1回の実行で証拠bundleを抽出し、会話の流れ・比較材料・問題候補・統計を書いて、所在を1行のJSONで返す。
会話の流れはメインのユーザー発話ごとの時系列と委譲先ごとの活動概要を、詳細の記録位置とともに示した文書であり、
メインが最初に全体を通読する。
原因と対策の確定、AWIの起草と投入はメインが自身のコンテキストで行うため、本スクリプトはキューを変更しない。
`candidates.md`は1候補を1行の要約と、記録の由来（役割文書、起動mode、起動元）を添えた記録位置で示す。全文が要る候補だけを、
メインが`atk run-script session-review-evidence`の`--detail`で照会する。

本スクリプトはデータ取得を目的とし、合否を判定しないため、
`agent-toolkit:writing-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
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
import re
import sys
import unicodedata
from typing import Any

import session_evidence_candidates  # pylint: disable=import-error
import session_review_evidence  # pylint: disable=import-error

from agent_toolkit._atk import managed_temp, run_script
from agent_toolkit._atk.wi import constants as _wi_constants
from agent_toolkit._atk.wi import sections as _wi_sections
from agent_toolkit._atk.wi import uwi_scan as _uwi_scan
from agent_toolkit._common import atomic_file, file_lock
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._git import command as _git_command

CONVERSATION_FILENAME = "conversation.md"
CANDIDATES_FILENAME = "candidates.md"
STATS_FILENAME = "stats.md"
COMPARISON_FILENAME = "comparison-materials.md"

_CONVERSATION_MAX_BYTES = 100_000
"""1エージェントがセッション全体の流れを保持して通読する文書の原則上限。"""

_CONVERSATION_MAIN_BYTES = 48_000
_CONVERSATION_DELEGATE_BYTES = 46_000
"""文書の固定説明を除き、メインの時系列と委譲先の概要へ割り当てる量。"""


_CODEX_EXIT_CODE = re.compile(r"^Process exited with code (\d+)$", re.MULTILINE)

_SUMMARY_LENGTH = 200
"""候補の概要へ載せる文字数。通読の本文と操作入力の全文は別に保持する。"""
_SLOW_CALL_LIMIT = 10
_FULL_TEXT_KINDS = frozenset({"user-intervention", "wi-user-response", "escalation"})
"""`candidates.md`へ本文の全文を載せる候補種別。ユーザーの是正と上位判断の要求は要約すると趣旨が変わるため全文を載せる。"""
MANDATORY_KINDS = frozenset({"user-intervention", "confirmation-request", "wi-user-response"})
"""評価を必須とする候補種別。

ユーザー介入、ユーザー確認（選択肢どおりの回答と投入したUWI）、WIの記入欄の是正は、
前例の有無によらず評価を要し、除外区分は`agent-toolkit:bugfix`の
`references/root-cause-analysis.md`「ユーザー介入と確認の判定」で確かめる。探索の深さと再発防止策の要否・終了は同書「QとC・Dの区別」の重大度判断に従う。
必須であることを参照資料の読込に依存させないよう候補一覧と1行JSONへ示し、
振り返り結果報告は各候補の扱いを候補IDで示す（書式は`agent-toolkit:completion-report`の`references/report-formats.md`）。
"""
_SIMILAR_RECORD_KINDS = frozenset({"user-intervention", "wi-user-response"})
"""過去の同種記録を添える候補種別。ユーザーの発話を本文に持つ種別に限る。"""
_SIMILAR_SECTIONS = ("ユーザー指摘の逐語引用", "回答", "ユーザーコメント")
"""過去の同種記録を探すWIの節。ユーザーの発話を逐語で保持する節に限り、エージェントが書いた本文との一致を除く。"""
_SIMILAR_MIN_SENTENCE = 20
_SIMILAR_NGRAM = 5
_SIMILAR_RATIO = 0.8
"""過去の同種記録の一致条件。空白を除きNFKC正規化した20字以上の文の文字5-gramのうち8割以上を節の本文が含む場合に一致とする。

過去の振り返りの`user-intervention`候補207件とprivate-notesの全WIで比べ、12字以上・6割以上の条件では
スキル展開の定型文などの短い文が多数のWIへ一致した。20字以上・8割以上では、包含率9割5分未満の一致が
同じ是正の言い換えと同じ環境のログに限られ、無関係な一致は観測されなかった。
"""
_FAILURE_KINDS = frozenset({"command-failure", "tool-failure"})
_FAILURE_LEDGER_DAYS = 30


def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数を定義する。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--transcript", metavar="PATH", help="Claude Codeのtranscriptパス。")
    session.add_argument("--codex-thread-id", metavar="ID", help="Codexのthread ID。")
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
    return parser


_MISSING_NEXT_ACTIONS = {
    "evidence_script": (
        "`atk run-script session-review-prepare -- <引数>`で起動する。"
        "解消しない場合はagent-toolkitの導入が壊れているため、ユーザーへ報告する"
    ),
    "work_dir": "`atk managed-temp create`で作成したディレクトリの絶対パスを`--work-dir`へ渡して再実行する",
    "transcript_path": (
        "`--transcript`へ実在するClaude Codeのtranscriptの絶対パスを渡すか、Codexでは`--codex-thread-id`を渡して再実行する"
    ),
    "bundle": (
        "表示した原因を確かめ、同じ引数で再実行する。記録が読めない場合は`--transcript`・`--codex-thread-id`の値を確かめる"
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


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _extract_bundle(session_arguments: list[str], bundle_dir: pathlib.Path) -> tuple[str | None, list[dict[str, Any]]]:
    """bundleの成功時も未解決通知を受け渡し、走査全量と最終JSONへ混ぜない。"""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = session_review_evidence.main([*session_arguments, "--bundle", str(bundle_dir)])
    if exit_code != 0:
        return captured.getvalue() or f"atk run-script session-review-evidenceの終了コード: {exit_code}", []
    try:
        events = [json.loads(line) for line in captured.getvalue().splitlines() if line.strip()]
    except ValueError as error:
        return str(error), []
    return None, [event for event in events if event.get("kind") in {"unresolved-record", "unresolved-delegation"}]


def _unconfirmed_lines(events: list[dict[str, Any]]) -> list[str]:
    """候補件数と区別した確認範囲を、全ての準備文書へ同じ集合で示す。"""
    lines = ["", "## 未確認範囲", ""]
    if not events:
        return [*lines, "未解決の対象は無い。", ""]
    return [
        *lines,
        *(f"- {event['kind']}: {json.dumps(event, ensure_ascii=False)}" for event in events),
        "",
    ]


def _fence(body: str, info: str = "text") -> list[str]:
    """本文中のバッククォート連続より長いフェンスで囲み、本文中の見出しを見出しとして扱わせない。"""
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{info}", body, fence]


def _one_line(text: str, limit: int = _SUMMARY_LENGTH) -> str:
    """空白を正規化した先頭の文字列を返す。"""
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[:limit] + "…"


def _clip_utf8(text: str, limit: int) -> str:
    """UTF-8の文字境界を保ち、本文を指定バイト数以内へ縮める。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = "…"
    body = encoded[: max(0, limit - len(marker.encode("utf-8")))].decode("utf-8", errors="ignore")
    return body + marker


def _conversation_excerpt(text: str, limit: int) -> str:
    """詳細照会を前提に、発話または入力の概要を1行で返す。"""
    return _clip_utf8(" ".join(text.split()), limit)


def _activity_summary(events: list[dict[str, Any]]) -> str:
    """ツール呼び出しの種類・量・失敗と記録位置を1行へ集約する。"""
    calls = [item for item in events if item.get("kind") == "tool-call"]
    results = [item for item in events if item.get("kind") in {"tool-result", "tool-failure"}]
    failures = {
        (item["record"], item["line"])
        for item in results
        if item.get("kind") == "tool-failure" or _codex_failed(str(item.get("text", "")))
    }
    tools = collections.Counter(str(item.get("tool") or "unknown") for item in calls)
    tool_text = "、".join(f"{name}×{count}" for name, count in tools.most_common(6)) or "なし"
    if len(tools) > 6:
        tool_text += f"、他{len(tools) - 6}種"
    input_bytes = sum(len(str(item.get("text", "")).encode("utf-8")) for item in calls)
    if calls:
        first = f"{calls[0]['record']}:{calls[0]['line']}"
        last = f"{calls[-1]['record']}:{calls[-1]['line']}"
        locations = first if first == last else f"{first}〜{last}"
    else:
        locations = "なし"
    result_locations = [f"{item['record']}:{item['line']}" for item in results]
    if result_locations:
        result_range = result_locations[0] if len(result_locations) == 1 else f"{result_locations[0]}〜{result_locations[-1]}"
    else:
        result_range = "なし"
    return (
        f"呼出{len(calls)}件（{tool_text}）、失敗{len(failures)}件、入力{input_bytes}バイト、"
        f"呼出記録{locations}、結果記録{result_range}"
    )


def _main_turns(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """メイン記録を実ユーザー発話から次の実ユーザー発話までの区間へ分ける。"""
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for event in events:
        if event.get("kind") == "utterance" and event.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(event)
    if current:
        turns.append(current)
    return turns


def _main_turn_summary(events: list[dict[str, Any]], limit: int) -> str:
    """ユーザー発話1件と、その後の判断・操作・結果を同じ区間の概要にする。"""
    utterances = [item for item in events if item.get("kind") == "utterance"]
    user = next((item for item in utterances if item.get("role") == "user"), None)
    assistants = [item for item in utterances if item.get("role") == "assistant"]
    parts: list[str] = []
    if user is not None:
        locator = f"{user['record']}:{user['line']}"
        parts.append(
            f"ユーザー {user.get('timestamp') or '時刻なし'} {locator}: "
            f"{_conversation_excerpt(str(user.get('text', '')), max(80, limit // 2))}"
        )
    if assistants:
        first = _conversation_excerpt(str(assistants[0].get("text", "")), max(40, limit // 8))
        last = _conversation_excerpt(str(assistants[-1].get("text", "")), max(40, limit // 8))
        location = f"{assistants[0]['record']}:{assistants[0]['line']}〜{assistants[-1]['record']}:{assistants[-1]['line']}"
        parts.append(f"アシスタント{len(assistants)}発話（{location}）: 冒頭={first} / 末尾={last}")
    parts.append(_activity_summary(events))
    return _clip_utf8("\n".join(f"- {part}" for part in parts), limit)


def _delegate_summary(record: str, events: list[dict[str, Any]], limit: int) -> str:
    """委譲先1記録の目的・返却・操作量を1行にする。"""
    utterances = [item for item in events if item.get("kind") == "utterance"]
    users = [item for item in utterances if item.get("role") == "user"]
    assistants = [item for item in utterances if item.get("role") == "assistant"]
    task = _conversation_excerpt(str(users[0].get("text", "")), max(30, limit // 4)) if users else "なし"
    result = _conversation_excerpt(str(assistants[-1].get("text", "")), max(30, limit // 4)) if assistants else "なし"
    first = events[0].get("timestamp") or "時刻なし"
    last = events[-1].get("timestamp") or "時刻なし"
    summary = (
        f"- {record}（{first}〜{last}）: 依頼={task}; 返却={result}; "
        f"アシスタント{len(assistants)}発話; {_activity_summary(events)}"
    )
    return _clip_utf8(summary, limit)


def _conversation_document(events: list[dict[str, Any]], detail_command: str, main_record: str | None) -> str:
    """セッション全体の目的・判断・操作と手戻りを、1エージェントが通読できる量で示す。"""
    by_record: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in events:
        by_record[str(event["record"])].append(event)
    if main_record is None and by_record:
        main_record = next(iter(by_record))
    lines = [
        "# 会話の流れ",
        "",
        "セッション全体の目的、判断、主要な操作、分担、待機、再読、失敗と手戻りを、"
        "1エージェントが一続きで見直すための概要である。自動挿入本文と詳細本文の写しは含めない。"
        f"全文は`{detail_command} --detail <記録位置>`から取得する。",
        "",
        "## メインの流れ",
        "",
    ]
    main_events = by_record.pop(str(main_record), []) if main_record is not None else []
    turns = _main_turns(main_events)
    if not turns:
        lines.append("- メイン記録の発話と操作は0件である。")
    else:
        per_turn = max(240, _CONVERSATION_MAIN_BYTES // len(turns))
        lines.extend(_main_turn_summary(turn, per_turn) for turn in turns)
    lines.extend(["", "## 委譲先の流れ", ""])
    delegates = sorted(by_record.items(), key=lambda item: str(item[1][0].get("timestamp") or ""))
    if not delegates:
        lines.append("- 委譲先記録は0件である。")
    else:
        per_delegate = max(100, _CONVERSATION_DELEGATE_BYTES // len(delegates))
        lines.extend(_delegate_summary(record, record_events, per_delegate) for record, record_events in delegates)
    document = "\n".join(lines) + "\n"
    if len(document.encode("utf-8")) > _CONVERSATION_MAX_BYTES:
        raise ValueError("会話の流れを原則上限の100000バイト以下へ縮約できなかった")
    return document


def _codex_failed(result: str) -> bool:
    """Codexのシェル実行の結果が0以外の終了コードを報告しているかを返す。

    結果は`Chunk ID:`・`Wall time:`・`Process exited with code`などの行と`Output:`の行で始まる。
    抽出はこの形の失敗を失敗の標識にしないため、結果の行の成否はここで判定する。
    """
    header = result.split("\nOutput:\n", 1)[0]
    match = _CODEX_EXIT_CODE.search(header)
    return match is not None and match[1] != "0"


def _full_body_lines(text: str, *, title: str) -> list[str]:
    """長い単一行も上限内で読み、連続する文字位置から全文を復元できる形で返す。"""
    lines = []
    for start in range(0, max(1, len(text)), 4000):
        body = text[start : start + 4000]
        lines.extend(["", f"## {title}（文字{start + 1}〜{start + len(body)}）", "", *_fence(body)])
    return lines


def _readable_entry_text(text: str) -> str:
    """`atk run-script session-review-evidence`がエントリ全体をJSONで返した詳細から、人が読む本文を取り出す。

    本文を取り出せないJSONと、切り詰めでJSONとして解釈できない文字列はそのまま返す。
    """
    try:
        entry = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(entry, dict):
        return text
    parts: list[str] = []
    for container in (entry.get("message"), entry.get("payload")):
        if not isinstance(container, dict):
            continue
        for key in ("content", "output", "message"):
            parts.extend(_text_parts(container.get(key)))
    attachment = entry.get("attachment")
    if isinstance(attachment, dict):
        for key in ("prompt", "content"):
            parts.extend(_text_parts(attachment.get(key)))
    joined = "\n".join(part for part in parts if part.strip())
    return joined or text


def _text_parts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content"):
                    parts.extend(_text_parts(item.get(key)))
        return parts
    return []


def _tool_use_summary(event: dict[str, Any]) -> str:
    """対象のツール呼び出しを、ツール名と入力の要約1行で表す。"""
    tool_input = event.get("input")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        summary = tool_input["command"]
    else:
        summary = json.dumps(tool_input, ensure_ascii=False)
    return f"{event.get('name', '')} {_one_line(summary)}"


def _preceding_assistant_text(timeline: list[dict[str, Any]], record: str, line: int) -> str | None:
    """同じ記録で指定行以前にある最後のアシスタント発話を返す。"""
    preceding = [
        event
        for event in timeline
        if event.get("kind") in {"assistant", "final-result"}
        and event.get("record") == record
        and isinstance(event.get("line"), int)
        and int(event["line"]) <= line
        and isinstance(event.get("text"), str)
    ]
    return str(max(preceding, key=lambda event: int(event["line"]))["text"]) if preceding else None


def _candidate_bodies(candidate: dict[str, Any], evidence: dict[str, Any]) -> tuple[list[str], str]:
    """候補の証拠から人が読む本文の一覧と、その先頭（無ければ候補の本文）を返す。"""
    bodies = [
        _readable_entry_text(str(event["text"]))
        for event in evidence.get("events", [])
        if event.get("kind") == "detail" and isinstance(event.get("text"), str) and event["text"].strip()
    ]
    return bodies, bodies[0] if bodies else str(candidate.get("text", ""))


def _provenance_text(records: list[str], timeline: list[dict[str, Any]]) -> str:
    """記録ごとの由来（役割文書、起動mode、起動元の記録とその役割文書）を、同じbundleの`record-provenance`から示す。

    候補の要約の文言（引数のパスやコマンド）からは実行主体を推せないため、記録位置へ由来を添えて
    読み手が別の照会をせずに実行主体を確かめられるようにする。由来の無い記録は取得できなかったことを示す。
    """
    provenance = {
        str(event["record"]): event
        for event in timeline
        if event.get("kind") == "record-provenance" and isinstance(event.get("record"), str)
    }

    def describe(record: str) -> str:
        item = provenance.get(record)
        if item is None:
            return f"{record}=由来を取得できなかった"
        source = item.get("source_record")
        if not isinstance(source, str):
            return f"{record}={'メイン' if item.get('role') == 'main' else '起動元なし'}"
        parent = provenance.get(source)
        if parent is None:
            parent_text = "起動元の由来を取得できなかった"
        elif parent.get("role") == "main" and not parent.get("source_record"):
            parent_text = "メイン"
        else:
            parent_text = f"役割文書{parent.get('role_document') or 'なし'}"
        line = item.get("source_line")
        return (
            f"{record}=役割文書{item.get('role_document') or 'なし'}・mode {item.get('mode') or '不明'}・"
            f"起動元{source}{f':{line}' if line is not None else ''}（{parent_text}）"
        )

    return "由来: " + "; ".join(describe(record) for record in dict.fromkeys(records))


def _candidate_lines(
    candidate: dict[str, Any],
    evidence: dict[str, Any],
    timeline: list[dict[str, Any]],
    similar: list[tuple[str, str, str]] | None = None,
) -> list[str]:
    """候補1件を、1行の要約、記録位置および判断に要る補足で組み立てる。

    hook通知は通知が判定した入力を読まないと是非を判断できないため、直前のアシスタント発話を添える。
    その場のコードによる加工は、記録ごとの件数と、呼び出しごとの記録位置と代表入力の一覧で示す。
    加工の目的と反復は呼び出しの列を見比べて判定するため、代表の1件だけでは判断できない。
    """
    kind = str(candidate["candidate_kind"])
    occurrence = candidate.get("occurrence_count", candidate.get("count", 1))
    if kind == "adhoc-processing":
        record = candidate["locators"][0]["record"]
        lines = [
            f"- {candidate['candidate_id']} {kind}（発生{occurrence}件）: 記録{record}のその場のコードによる加工",
            f"  - 記録の{_provenance_text([str(record)], timeline)}",
        ]
        lines.extend(
            f"  - {call['record']}:{call['line']}: {_one_line(str(call.get('text', ''))) or '（入力なし）'}"
            for call in candidate.get("calls", [])
        )
        for call in candidate.get("calls", []):
            text = str(call.get("text", ""))
            if len(text) > _SUMMARY_LENGTH or "\n" in text:
                lines.extend(_full_body_lines(text, title=f"操作 {call['record']}:{call['line']}"))
        return lines
    events = evidence.get("events", [])
    bodies, text = _candidate_bodies(candidate, evidence)
    readable = candidate.get("failure_summary") if kind in _FAILURE_KINDS else text
    contexts = candidate.get("assistant_context")
    if isinstance(contexts, list) and contexts and isinstance(contexts[0], dict):
        # 確認への回答は回答の文字列（選択肢のlabelなど）だけでは何を問うたか分からないため、質問を要約に使う。
        readable = str(contexts[0].get("question", "")) or readable
    sessions = candidate.get("failure_session_count")
    session_count = f"、{sessions}セッション" if isinstance(sessions, int) else ""
    mandatory = "、評価が必須" if kind in MANDATORY_KINDS else ""
    heading = f"- {candidate['candidate_id']} {kind}（発生{occurrence}件{session_count}{mandatory}）: "
    lines = [heading + (_one_line(str(readable)) or "（本文なし）")]
    if isinstance(candidate.get("wi_file"), str):
        section = f"の`## {candidate['wi_section']}`" if isinstance(candidate.get("wi_section"), str) else ""
        lines.append(
            f"  - WI: {candidate['wi_file']}（{candidate.get('wi_state')}）{section}。"
            f"全文は`atk wi show --skip-pull {candidate['wi_file']}`で取得する"
        )
    else:
        locators = ", ".join(f"{locator['record']}:{locator['line']}" for locator in candidate["locators"])
        omitted = candidate.get("omitted_locator_count", 0)
        lines.append(
            f"  - 記録位置: {locators}"
            + (f"（同じ種類の{omitted}件は省略）" if omitted else "")
            + f"。{_provenance_text([str(locator['record']) for locator in candidate['locators']], timeline)}"
        )
    lines.extend(_question_answer_lines(candidate))
    lines.extend(f"  - 対象: {_tool_use_summary(event)}" for event in events if event.get("kind") == "tool-use")
    if kind == "hook-notice":
        first = candidate["locators"][0]
        preceding = _preceding_assistant_text(timeline, str(first["record"]), int(first["line"]))
        lines.append(f"  - 直前のアシスタント発話: {_one_line(preceding) if preceding else '（なし）'}")
    if similar is not None:
        lines.append("  - 過去の同種記録:" + ("" if similar else " 一致なし"))
        lines.extend(f"    - {filename}（{state}）: {title or '（タイトルなし）'}" for filename, state, title in similar)
    if kind in _FULL_TEXT_KINDS:
        lines.extend(["", *("  " + line if line else "" for line in _fence("\n\n".join(bodies) if bodies else text)), ""])
    return lines


def _question_answer_lines(candidate: dict[str, Any]) -> list[str]:
    """確認への回答の候補へ、質問、提示した選択肢、選んだ回答を添える。"""
    contexts = candidate.get("assistant_context")
    responses = candidate.get("user_response")
    if not isinstance(contexts, list) or not isinstance(responses, list):
        return []
    lines: list[str] = []
    for context, response in zip(contexts, responses, strict=False):
        if not isinstance(context, dict) or not isinstance(response, dict):
            continue
        options = " / ".join(str(option.get("label", "")) for option in context.get("options", []) if isinstance(option, dict))
        answers = "、".join(str(answer) for answer in response.get("answers", []))
        lines.extend(
            [
                f"  - 質問: {_one_line(str(context.get('question', '')))}",
                f"  - 選択肢: {options or '（なし）'}",
                f"  - 回答: {answers or '（なし）'}"
                + (f"（自由記述: {_one_line(str(response['notes']))}）" if response.get("notes") else ""),
            ]
        )
    return lines


def _normalize_for_similarity(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _ngrams(text: str) -> set[str]:
    return {text[index : index + _SIMILAR_NGRAM] for index in range(len(text) - _SIMILAR_NGRAM + 1)}


def _sentences(text: str) -> list[set[str]]:
    """発話を文へ分け、一致の判定に使う長さの文の5-gram集合を返す。"""
    pieces = re.split(r"[。！？!?\n]", text)
    return [_ngrams(piece) for piece in map(_normalize_for_similarity, pieces) if len(piece) >= _SIMILAR_MIN_SENTENCE]


def _past_wi_sections() -> list[tuple[str, str, str, list[set[str]]]]:
    """private-notesの全状態のWIから、ファイル名、状態、タイトルと、ユーザーの発話を持つ節ごとの5-gram集合を返す。"""
    root = _uwi_scan.private_notes_root()
    if root is None:
        return []
    records: list[tuple[str, str, str, list[set[str]]]] = []
    for state in _wi_constants.WI_STATES:
        directory = root / state
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError, UnicodeError:
                continue
            sections = _wi_sections.h2_sections(text)
            grams = [_ngrams(_normalize_for_similarity(sections[name])) for name in _SIMILAR_SECTIONS if sections.get(name)]
            if grams:
                records.append((path.name, state, _wi_sections.title(text), grams))
    return records


def _similar_records(
    text: str, past: list[tuple[str, str, str, list[set[str]]]], *, exclude: str | None
) -> list[tuple[str, str, str]]:
    """発話のいずれかの文の5-gramの8割以上を1つの節が含むWIを、ファイル名、状態、タイトルで返す。"""
    sentences = _sentences(text)
    matched: list[tuple[str, str, str]] = []
    for filename, state, title, sections in past:
        if filename == exclude:
            continue
        if any(
            len(sentence & section) >= _SIMILAR_RATIO * len(sentence)
            for sentence in sentences
            for section in sections
            if sentence
        ):
            matched.append((filename, state, title))
    return sorted(matched)


def _candidates_document(
    candidates: list[dict[str, Any]],
    summary: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    timeline: list[dict[str, Any]],
    detail_command: str,
    single_failures: list[dict[str, Any]],
    similar_by_id: dict[str, list[tuple[str, str, str]]] | None = None,
) -> str:
    """`candidates.md`の本文を組み立てる。"""
    counts = collections.Counter(str(item["candidate_kind"]) for item in candidates)
    mandatory = [str(item["candidate_id"]) for item in candidates if item["candidate_kind"] in MANDATORY_KINDS]
    count_text = "、".join(f"{kind} {count}件" for kind, count in sorted(counts.items())) or "なし"
    excluded = summary.get("excluded", {})
    excluded_text = "、".join(f"{name} {count}件" for name, count in sorted(excluded.items())) or "なし"
    lines = [
        "# 問題候補",
        "",
        f"- 候補: {len(candidates)}件（{count_text}）",
        f"- 候補から除いた件数: {excluded_text}",
        f"- 記録位置の全文は`{detail_command} --detail <記録位置>`で取得する",
        "- 探索の深さと再発防止策の要否・終了はbugfixの重大度判断に従う。"
        "軽微な問題は探索範囲・限界・許容する残存リスクを記録する",
        f"- 評価が必須の候補: {', '.join(mandatory) or 'なし'}",
        "",
    ]
    if not candidates:
        lines.append("候補として残す問題は無かった。")
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        lines.extend(
            _candidate_lines(candidate, evidence_by_id.get(candidate_id, {}), timeline, (similar_by_id or {}).get(candidate_id))
        )
    if single_failures:
        lines.extend(
            ["", "## 単発の失敗（件数のみ）", "", "| 失敗署名の要約 | 今回の発生件数 | 代表の記録位置 |", "| --- | --- | --- |"]
        )
        for candidate in single_failures:
            first = candidate["locators"][0]
            summary_text = str(candidate.get("failure_summary", "診断なし")).replace("|", "\\|")
            lines.append(
                f"| {_one_line(summary_text)} | {candidate.get('occurrence_count', candidate.get('count', 1))} | "
                f"{first['record']}:{first['line']} |"
            )
    return "\n".join(lines).rstrip("\n") + "\n"


def _failure_ledger(candidates: list[dict[str, Any]], session_id: str, now: datetime.datetime) -> tuple[dict[str, int], int]:
    """ロックを取得した状態で過去30日の失敗署名を更新し、署名ごとの最上位セッション数を返す。

    判定に使うイベントの欄（`failure_event`）を持つ記録は、読込のたびに抽出と同じ除外判定と署名計算で判定し直し、
    現在の判定が除く記録を数えず書き戻しからも除く。欄を持たない旧形式の記録は保存済みの代表診断から署名だけを再計算する。
    """
    path = _state_paths.state_dir() / "session-review" / "failure-signatures.jsonl"
    lock_path = path.with_name(path.name + ".lock")
    cutoff = now.astimezone(datetime.UTC) - datetime.timedelta(days=_FAILURE_LEDGER_DAYS)
    skipped = 0
    records: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            file_lock.acquire_lock(lock)
            try:
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except FileNotFoundError:
                    lines = []
                except OSError, UnicodeError:
                    lines = []
                    skipped += 1
                for line in lines:
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            raise ValueError("記録がobjectではない")
                        signature, recorded_session, observed_at, summary = (
                            item["failure_signature"],
                            item["session_id"],
                            item["observed_at"],
                            item["failure_summary"],
                        )
                        if not all(
                            isinstance(value, str) and value for value in (signature, recorded_session, observed_at, summary)
                        ):
                            raise ValueError("記録欄が不正")
                        timestamp = datetime.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                        if timestamp.tzinfo is None:
                            raise ValueError("時刻のtimezoneが無い")
                    except KeyError, ValueError, TypeError, json.JSONDecodeError:
                        skipped += 1
                        continue
                    if timestamp >= cutoff:
                        try:
                            judged = _rejudge_failure_record(item)
                        except ValueError:
                            skipped += 1
                            continue
                        if judged is None:
                            continue
                        records.setdefault((judged["failure_signature"], recorded_session), judged)
                for candidate in candidates:
                    signature = candidate.get("failure_signature")
                    if not isinstance(signature, str):
                        continue
                    record = {
                        "failure_signature": signature,
                        "session_id": session_id,
                        "observed_at": now.astimezone(datetime.UTC).isoformat(),
                        "failure_summary": str(candidate.get("failure_summary", "診断なし")),
                    }
                    if isinstance(candidate.get("failure_event"), dict):
                        record["candidate_kind"] = str(candidate.get("candidate_kind", ""))
                        record["failure_event"] = candidate["failure_event"]
                    records.setdefault((signature, session_id), record)
                payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for _, item in sorted(records.items()))
                atomic_file.atomic_write(path, payload)
            finally:
                file_lock.release_lock(lock)
    except OSError:
        skipped += 1
        return {}, skipped
    counts = collections.Counter(signature for signature, _ in records)
    return dict(counts), skipped


def _rejudge_failure_record(item: dict[str, Any]) -> dict[str, Any] | None:
    """台帳の1記録を現在の判定で判定し直し、数える記録を返す。現在の判定が除く記録は`None`を返す。

    記録時の判定の結論だけを保持すると、除外規則を後から広げても30日の期限まで反復の計数に残るため、
    判定に使う欄を保持する記録は抽出と同じ関数で判定し直す。形式が不正な記録は`ValueError`を送出する。
    """
    event = item.get("failure_event")
    if event is None:
        signature = session_evidence_candidates.recalculate_failure_signature(
            item["failure_signature"], item["failure_summary"]
        )
        return {**item, "failure_signature": signature}
    candidate_kind = item.get("candidate_kind")
    if not isinstance(event, dict) or candidate_kind not in session_evidence_candidates.FAILURE_CANDIDATE_KINDS:
        raise ValueError("判定に使う欄が不正")
    exclusion, hook_event = session_evidence_candidates.classify_failure_candidate(candidate_kind, event)
    if exclusion is not None or hook_event is not None:
        return None
    signature, summary = session_evidence_candidates.failure_signature(candidate_kind, event)
    return {**item, "failure_signature": signature, "failure_summary": summary}


def _select_repeated_failures(
    candidates: list[dict[str, Any]], summary: dict[str, Any], session_id: str, now: datetime.datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], int]:
    """`atk run-script session-review-evidence`が検出した失敗候補を別セッションの反復と単発へ分ける。"""
    failures = [item for item in candidates if item.get("candidate_kind") in _FAILURE_KINDS]
    session_counts, skipped = _failure_ledger(failures, session_id, now)
    retained: list[dict[str, Any]] = []
    singles: list[dict[str, Any]] = []
    excluded = collections.Counter(summary.get("excluded", {}))
    for candidate in candidates:
        if candidate.get("candidate_kind") not in _FAILURE_KINDS:
            retained.append(candidate)
            continue
        count = session_counts.get(str(candidate.get("failure_signature", "")), 1)
        if count >= 2:
            candidate["failure_session_count"] = count
            retained.append(candidate)
        else:
            singles.append(candidate)
            excluded["single-session-failure"] += int(candidate.get("occurrence_count", candidate.get("count", 1)))
    return retained, singles, {**summary, "excluded": dict(sorted(excluded.items()))}, skipped


def _stats_document(stats: list[dict[str, Any]]) -> str:
    """セッション統計を組み立てる。"""
    by_kind: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in stats:
        by_kind[str(event.get("kind"))].append(event)
    total = next(iter(by_kind.get("stats-total", [])), {})
    lines = [
        "# セッション統計",
        "",
        f"- 経過秒: {total.get('elapsed_seconds', 'unknown')}秒（セッションの最初の記録から準備時点まで）",
    ]
    lines.extend(
        [
            "",
            "## メイン・全担当の費用",
            "",
            "Claude Codeのinput_tokensは新規入力で、cache_creation_input_tokensとcache_read_input_tokensは別成分。"
            "Codexのinput_tokensはcached_input_tokensを含む総入力で、reasoning_output_tokensはoutput_tokensに含まれる。"
            "stats-totalは共通の4成分へ変換して合算済みであり、個別の原始値と重ねて足さない。",
            "tokensと応答回数は記録で観測した値だけを示し、不在は未確認とする。",
        ]
    )
    main_record = next(iter(by_kind.get("stats-summary", [])), {}).get("record", "main")
    for item in stats:
        if item.get("kind") not in {"stats-summary", "stats-subagent", "stats-agent-thread"}:
            continue
        owner = item.get("record", item.get("thread", item.get("agent", "メイン")))
        if item.get("line") is not None:
            location = f"{item.get('agent', main_record)}:{item['line']}"
        else:
            location = item.get("record", item.get("agent", f"{item.get('engine', '未確認')}:{owner}"))
        tokens = json.dumps(item.get("tokens", "未確認"), ensure_ascii=False)
        lines.append(
            f"- {owner}（{item.get('engine', '未確認')}）: tokens={tokens}、"
            f"応答回数={item.get('api_messages', '未確認')}、記録または起動位置={location}"
        )
    critical = next(iter(by_kind.get("stats-critical-path", [])), None)
    if critical is not None:
        segments = "、".join(f"{item.get('owner')} {item.get('exclusive_seconds')}秒" for item in critical.get("segments", []))
        lines.append(
            f"- 律速区間: メインだけの区間{critical.get('main_only_seconds')}秒、agent thread別の排他区間: {segments or 'なし'}"
        )
        if critical.get("unmeasured_threads"):
            lines.append(f"- 未計測のthread: {'、'.join(str(item) for item in critical['unmeasured_threads'])}")
    compaction = next(iter(by_kind.get("stats-compaction-total", [])), {})
    lines.append(
        f"- コンパクション: {compaction.get('count', 0)}回、合計{compaction.get('total_duration_seconds', 0)}秒"
        f"（所要時間不明{compaction.get('duration_unknown_count', 0)}回）"
    )
    lines.extend(f"  - 記録{record}: {count}回" for record, count in compaction.get("by_record", {}).items())
    lines.extend(
        f"  - {item['record']}:{item['line']}: {item.get('timestamp') or '時刻なし'}"
        for item in by_kind.get("stats-compaction", [])
    )
    threads = by_kind.get("stats-agent-thread", [])
    if threads:
        lines.extend(["", "| thread | 実行系 | 観測identity | 経過秒 | 応答回数 |", "| --- | --- | --- | --- | --- |"])
        lines.extend(
            "| "
            + " | ".join(
                [
                    str(item.get("thread", "unknown")),
                    str(item.get("engine", "unknown")),
                    ", ".join(
                        f"{identity.get('engine')}:{identity.get('model')}/{identity.get('effort')}"
                        for identity in item.get("observed_identities", [])
                    )
                    or "unknown",
                    str(item.get("elapsed_seconds", "unknown")),
                    str(item.get("api_messages", "unknown")),
                ]
            )
            + " |"
            for item in threads
        )
    slow = sorted(by_kind.get("stats-slow-call", []), key=lambda item: -float(item.get("seconds", 0)))[:_SLOW_CALL_LIMIT]
    if slow:
        lines.extend(["", "遅い呼び出しの上位:", ""])
        for item in slow:
            hint = " ".join(str(item.get("hint", "")).split())
            lines.append(f"- {item.get('tool')} {item.get('seconds')}秒" + (f": {hint}" if hint else ""))
    if critical is not None:
        for segment in critical.get("segments", []):
            lines.extend(_thread_breakdown_lines(segment, threads, by_kind))
    lines.extend(
        [
            "",
            "## 全担当の読込・反復・結果量",
            "",
            "全記録が母集団であり、量と反復は比較材料として示す。問題の有無は目的と前後の操作から判断する。",
        ]
    )
    for item in by_kind.get("stats-resource-usage", []):
        lines.extend(
            [
                "",
                f"### {item['record']}（{item['role']}）",
                "",
                f"- 明示的なファイル読取: {len(item['reads'])}回、Skill呼出: {len(item['skills'])}回、"
                f"ツール呼出: {item['tool_calls']}回、記録の結果本文: {item['recorded_result_characters']}字・"
                f"{item['recorded_result_utf8_bytes']}バイト",
            ]
        )
        for row in [*item["reads"], *item["skills"]]:
            lines.append(
                f"- {row['tool']} 対象={row['target']}、範囲={json.dumps(row['range'], ensure_ascii=False)}、{row['location']}"
            )
        for row in item["repeats"]:
            lines.append(
                f"- 反復: {row['tool']} {row['count']}回、{'、'.join(row['locations'])}、入力={_one_line(row['input'])}"
            )
        for row in item["largest_results"]:
            lines.append(
                f"- 結果量上位: {row['tool']} {row['characters']}字・{row['utf8_bytes']}バイト、"
                f"入力={row['location']}、結果={row['result_location']}"
            )
        lines.append(f"- 上位一覧の省略: 結果{item['omitted_largest_result_count']}件、反復{item['omitted_repeat_count']}組")
    for item in by_kind.get("stats-resource-summary", []):
        lines.extend(_resource_summary_lines(item))
    for item in by_kind.get("stats-token-peak", []):
        lines.append(f"- メインのtoken上位: {json.dumps(item, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def _resource_summary_lines(summary: dict[str, Any]) -> list[str]:
    """担当横断の明示対象別集計と、読了を表さないシェル文字列の出現集計を描画する。"""
    lines = [
        "",
        "## 対象別の担当横断集計",
        "",
        "読取とSkillは明示ツールの呼出回数を数える。結果量は記録で観測できた返却本文の合計である。",
        "シェル内の絶対パスは入力文字列の出現を別に数える。実行・読取・読了の推定には使わない。",
    ]
    for field, label in (
        ("reads_by_target", "ファイル読取"),
        ("skills_by_name", "Skill"),
        ("shell_paths_by_target", "シェル内の絶対パス出現"),
    ):
        lines.extend(["", f"### {label}", ""])
        for group in summary[field]:
            lines.append(
                f"- {group['target']}: 合計{group['count']}回、観測結果{group['observed_result_count']}件・"
                f"{group['characters']}字・{group['utf8_bytes']}バイト"
            )
            for record, values in group["by_record"].items():
                lines.append(
                    f"  - {record}: {values['count']}回、観測結果{values['observed_result_count']}件・"
                    f"{values['characters']}字・{values['utf8_bytes']}バイト、{'、'.join(values['locations'])}"
                )
    return lines


def _thread_breakdown_lines(
    segment: dict[str, Any], threads: list[dict[str, Any]], by_kind: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """律速threadの内部の工程、待機および反復の内訳の節を組み立てる。

    記録から得た内訳を、振り返りが追加の照会なしで読めるようにする。
    件数の上限はメイン記録の集計と同じであり、記録位置は`--detail`へそのまま渡せる。
    """
    owner = segment.get("owner")
    thread = next((item for item in threads if item.get("thread") == owner), {})

    def of_thread(kind: str) -> list[dict[str, Any]]:
        return [item for item in by_kind.get(kind, []) if item.get("thread") == owner]

    def with_hint(text: str, item: dict[str, Any]) -> str:
        hint = _one_line(str(item.get("hint", "")))
        return f"{text}: {hint}" if hint else text

    lines = [
        "",
        f"## 律速threadの内訳: {owner}",
        "",
        f"- 排他区間: {segment.get('exclusive_seconds')}秒",
        f"- turnの経過秒: {thread.get('turn_elapsed_seconds', 'unknown')}秒",
    ]
    tools = of_thread("stats-thread-tool")
    lines.append(
        "- ツール別: "
        + ("、".join(f"{item.get('tool')} {item.get('count')}件{item.get('total_seconds')}秒" for item in tools) or "なし")
    )
    gaps = of_thread("stats-thread-gap")
    lines.append("- 60秒以上の間隔:" + ("" if gaps else " なし"))
    lines.extend(f"  - {item.get('seconds')}秒: {item.get('before')}〜{item.get('after')}" for item in gaps)
    repeats = of_thread("stats-thread-repeat")
    lines.append("- 反復:" + ("" if repeats else " なし"))
    lines.extend(
        with_hint(f"  - {item.get('tool')} {item.get('count')}回（{'、'.join(item.get('locations', []))}）", item)
        for item in repeats
    )
    slow = of_thread("stats-thread-slow-call")
    lines.append("- 遅い呼び出しの上位:" + ("" if slow else " なし"))
    lines.extend(with_hint(f"  - {item.get('tool')} {item.get('seconds')}秒 {item.get('location')}", item) for item in slow)
    return lines


def main(argv: list[str] | None = None, *, now: datetime.datetime | None = None) -> int:
    """振り返りの入力を作業ディレクトリへ書き、所在と件数を1行のJSONで出力する。"""
    args = _build_parser().parse_args(argv)
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
    if args.work_dir is not None:
        work_dir = pathlib.Path(args.work_dir).expanduser()
        if not work_dir.is_absolute() or not work_dir.is_dir():
            return _missing("work_dir", f"既存の絶対ディレクトリではない: {work_dir}")
    else:
        session_id = transcript_path.stem if transcript_path is not None else str(args.codex_thread_id)
        try:
            session_root = managed_temp.resolve_session_temp(session_id)
            work_dir = managed_temp.create_session_temp("session-review", session_root)
        except (managed_temp.ManagedTempError, OSError) as error:
            return _missing("work_dir", str(error))

    session_arguments = [str(transcript_path)] if transcript_path is not None else ["--codex-thread-id", args.codex_thread_id]
    bundle_dir = work_dir / "bundle"
    bundle_dir.mkdir(exist_ok=True)
    failure, unresolved = _extract_bundle(session_arguments, bundle_dir)
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
        evidence_by_id = {
            str(row["candidate_id"]): json.loads((bundle_dir / row["path"]).read_text(encoding="utf-8"))
            for row in _read_jsonl(bundle_dir / "candidate-evidence.jsonl")
        }
    except (OSError, ValueError, KeyError) as error:
        return _missing("bundle", str(error))

    # `atk run-script session-review-evidence`の`--transcript`と`--codex-thread-id`へそのまま渡せる形を、
    # 全文を照会するコマンドとして示す。
    detail_command = (
        f"atk run-script session-review-evidence -- {transcript_path}"
        if transcript_path is not None
        else f"atk run-script session-review-evidence -- --codex-thread-id {args.codex_thread_id}"
    )
    conversation_path = work_dir / CONVERSATION_FILENAME
    candidates_path = work_dir / CANDIDATES_FILENAME
    stats_path = work_dir / STATS_FILENAME
    comparison_path = work_dir / COMPARISON_FILENAME
    main_record = next(
        (item.get("record") for item in timeline if item.get("kind") == "record-provenance" and item.get("role") == "main"),
        None,
    )
    try:
        conversation_text = _conversation_document(conversation, detail_command, str(main_record) if main_record else None)
    except ValueError as error:
        return _missing("bundle", str(error))
    conversation_path.write_text(conversation_text, encoding="utf-8")
    conversation_bytes = len(conversation_text.encode("utf-8"))
    if args.conversation_only:
        print(
            json.dumps(
                {
                    "work_dir": str(work_dir),
                    "conversation_path": str(conversation_path),
                    "conversation_utf8_bytes": conversation_bytes,
                    "conversation_limit_bytes": _CONVERSATION_MAX_BYTES,
                    "unconfirmed_scope": unresolved,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    comparison_path.write_text(_comparison_document(comparison, detail_command), encoding="utf-8")
    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    session_id = transcript_path.stem if transcript_path is not None else str(args.codex_thread_id)
    candidates, single_failures, summary, ledger_skipped = _select_repeated_failures(candidates, summary, session_id, current)
    similar_targets = [item for item in candidates if item["candidate_kind"] in _SIMILAR_RECORD_KINDS]
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
    unconfirmed_text = "\n".join(_unconfirmed_lines(unresolved))
    improvement_rows = [
        f"- {event['record']}:{event['line']}: {line}" for event in timeline for line in event.get("reported_improvements", [])
    ]
    improvement_text = "\n## 取り込んだ改善点\n\n" + "\n".join(improvement_rows) + "\n" if improvement_rows else ""
    candidates_path.write_text(
        _candidates_document(candidates, summary, evidence_by_id, timeline, detail_command, single_failures, similar_by_id)
        + unconfirmed_text
        + improvement_text,
        encoding="utf-8",
    )
    stats_path.write_text(_stats_document(stats) + unconfirmed_text, encoding="utf-8")

    reference_document = _reference_document(target_repo, codex=args.codex_thread_id is not None)
    total = next((event for event in stats if event.get("kind") == "stats-total"), {})
    compaction = next((event for event in stats if event.get("kind") == "stats-compaction-total"), {})
    role_counts = collections.Counter(
        str(item["role"]) for item in conversation if item.get("kind", "utterance") == "utterance"
    )
    record = {
        "work_dir": str(work_dir),
        "conversation_path": str(conversation_path),
        "conversation_utf8_bytes": conversation_bytes,
        "conversation_limit_bytes": _CONVERSATION_MAX_BYTES,
        "comparison_materials_path": str(comparison_path),
        "candidates_path": str(candidates_path),
        "stats_path": str(stats_path),
        "reference_document": str(reference_document) if reference_document is not None else None,
        "prepared_at": current.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utterance_counts": {"user": role_counts.get("user", 0), "assistant": role_counts.get("assistant", 0)},
        "candidate_total": len(candidates),
        "candidate_counts": dict(sorted(collections.Counter(str(item["candidate_kind"]) for item in candidates).items())),
        "excluded_counts": dict(sorted(summary.get("excluded", {}).items())),
        "mandatory_candidates": [str(item["candidate_id"]) for item in candidates if item["candidate_kind"] in MANDATORY_KINDS],
        "similar_records": {
            candidate_id: [filename for filename, _state, _title in records] for candidate_id, records in similar_by_id.items()
        },
        "failure_ledger_skipped": ledger_skipped,
        "unconfirmed_scope": unresolved,
        "improvement_lines": list(dict.fromkeys(line for event in timeline for line in event.get("reported_improvements", []))),
        "elapsed_seconds": total.get("elapsed_seconds"),
        "compaction_count": compaction.get("count", 0),
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


def _comparison_document(events: list[dict[str, Any]], detail_command: str) -> str:
    """正常な操作を比較する事実と全文位置を示し、量の多寡と欠陥の判定を分ける。"""
    lines = [
        "",
        "## 委譲入力・規範読込の比較材料",
        "",
        "各記録の行を記録別に並べる。記録をまたいだ時系列ではない。",
        "explicit-readは明示された対象、opaque-commandは命令文字列だけの観測であり、読了した資料は推定しない。"
        "入力文字数は観測した直接本文だけ、結果文字数は記録の返却本文の量である。"
        "ファイル参照の本文量、自動配送、現在のファイルサイズ、token量を取得量へ代用しない。",
    ]
    previous_record: str | None = None
    for event in events:
        if event["record"] != previous_record:
            lines.extend(["", f"### {event['record']}（{event['role']}）", ""])
            previous_record = event["record"]
        locator = f"{event['record']}:{event['line']}"
        result = f"{event['record']}:{event['result_line']}" if event.get("result_line") is not None else "未確認"
        body_count = event.get("observed_body_characters")
        result_count = event.get("recorded_result_characters")
        lines.append(
            f"- {event['category']} {event['tool']}、担当={event['record']}（{event['role']}）、"
            f"時点={event.get('timestamp') or '未確認'}、形式={event['input_form']}、"
            f"直接本文={body_count if body_count is not None else '未確認'}字、"
            f"記録の結果本文={result_count if result_count is not None else '未確認'}字、"
            f"対象={json.dumps(event['target'], ensure_ascii=False)}、入力={locator}、結果={result}、"
            f"代表入力={_one_line(str(event.get('summary', '')))}"
        )
        lines.extend(_full_body_lines(str(event.get("summary", "")), title=f"入力 {locator}"))
    lines.extend(["", f"全文と参照先は`{detail_command} --detail <入力または結果の記録位置>`から取得する。", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
