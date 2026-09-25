"""セッション振り返りの証拠を抽出し、振り返り素材AWIを生成して投入する。

1回の実行で、証拠bundleの抽出、統計と既存キュー項目の取得、メイン由来の改善点の取込、
素材AWI本文の生成及び`atk wi add`による投入までを行い、結果を1行のJSONで返す。
候補の判定、原因分析と対策は素材AWIを取得した後続セッションのレーンが担うため、
本文は元のセッション記録を開かずに判断できる情報に限って載せる。

本スクリプトは検査スクリプトではなくデータ取得ツールであるため、
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
import shutil
import subprocess
import sys
from typing import Any

import session_review_decisions  # pylint: disable=import-error
import session_review_evidence  # pylint: disable=import-error

from agent_toolkit._atk import run_script

MAIN_OBSERVATIONS_FILENAME = "main-observations.md"
"""メインが自身のコンテキストから列挙した改善点を置くファイル名。作業領域の直下に置く。"""

MATERIAL_FILENAME = "material.md"
LANE_PROCESSING_DOCUMENT = pathlib.Path("skills/session-review/references/lane-processing.md")
"""素材AWIを処理する手順の正本。plugin rootからの相対パス。"""

_SLOW_CALL_LIMIT = 10
_ADOPTED_LIST_LIMIT = 30
"""素材へ載せる採用済みの振り返り由来の項目の件数。

採用済み項目は運用とともに増え続け、全件を載せると素材の大半を一覧が占める。
反復の判定に要るのは近い時期の項目であり、それより前の項目は処理側が`atk wi grep`で検索する。
"""
_SUMMARY_MAX_CHARS = 120
_ADD_SUCCESS_PATH = re.compile(r"^\s+\S*?([^/\s]+\.md)$")


def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数を定義する。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--transcript", metavar="PATH", help="Claude Codeのtranscriptパス。")
    session.add_argument("--codex-thread-id", metavar="ID", help="Codexのthread ID。")
    parser.add_argument(
        "--work-dir",
        metavar="PATH",
        required=True,
        help=f"メインが作成した管理対象一時領域の絶対パス。直下に{MAIN_OBSERVATIONS_FILENAME}を置いてから起動する。",
    )
    parser.add_argument(
        "--target-repo", metavar="PATH", help="振り返り対象のリポジトリ。省略時は素材AWIを生成するが投入しない。"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="`atk wi add --dry-run`で素材AWIの受理だけを検証し、キューを変更しない。",
    )
    return parser


def _missing(item: str, detail: str | None = None) -> int:
    """取得できなかった準備項目を報告する。"""
    print(f"不足: {item}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    return 2


def _run_atk(executable: str, arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    """`atk`を実行し、起動できない場合は`None`を返す。"""
    try:
        return subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None


def _reference_document(target_repo: pathlib.Path | None, *, codex: bool) -> pathlib.Path | None:
    """Git共通dirから対象リポジトリ固有の振り返り参照文書を解決する。"""
    if target_repo is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(target_repo), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
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
    path = pathlib.Path.home() / (".codex" if codex else ".claude") / "docs" / f"session-review-{repository_name}.md"
    return path if path.is_file() else None


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _extract_bundle(session_arguments: list[str], bundle_dir: pathlib.Path) -> str | None:
    """抽出器の集約実行で証拠bundleを作成する。失敗時は診断の文字列を返す。

    抽出器は標準出力へ要約イベントを書くため、1行JSONの出力契約を保つよう取り込んで破棄する。
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = session_review_evidence.main([*session_arguments, "--bundle", str(bundle_dir)])
    return None if exit_code == 0 else captured.getvalue() or f"抽出器の終了コード: {exit_code}"


def _queue_lists(executable: str, target_repo: pathlib.Path) -> dict[str, list[dict[str, str]]] | str:
    """素材AWIへ載せる既存キュー項目の一覧を取得する。失敗時は診断の文字列を返す。"""
    queries = {
        "active": ["--state=active"],
        "adopted-session-review": ["--state=adopted", "--source=session-review"],
        "answered-uwi": ["--type=uwi", "--answered=yes"],
    }
    lists: dict[str, list[dict[str, str]]] = {}
    for name, options in queries.items():
        result = _run_atk(executable, ["wi", "list", f"--target-repo={target_repo}", *options, "--summary-only", "--skip-pull"])
        if result is None or result.returncode != 0:
            return f"atk wi list {' '.join(options)}: {result.stderr if result is not None else '起動できない'}"
        lists[name] = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return lists


def _fence(body: str, info: str = "text") -> list[str]:
    """本文中のバッククォート連続より長いフェンスで囲み、本文中の見出しを見出しとして扱わせない。"""
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{info}", body.rstrip("\n"), fence]


def _readable_entry_text(text: str) -> str:
    """抽出器がエントリ全体をJSONで返した詳細から、人が読む本文を取り出す。

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


def _candidate_lines(candidate: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    """候補1件の見出し、発生件数、逐語本文、対象のツール呼び出し及び前後のユーザー発話を組み立てる。

    記録位置と集約キーは載せない。これらを載せると、後続の分析主体が裏付けのために元記録を検索し直す契機になる。
    """
    kind = str(candidate["candidate_kind"])
    heading = session_review_decisions.CANDIDATE_HEADING_FORMAT.format(
        candidate_id=candidate["candidate_id"], candidate_kind=kind
    )
    occurrence = candidate.get("occurrence_count", candidate.get("count", 1))
    omitted = candidate.get("omitted_locator_count", 0)
    count_line = f"- 発生件数: {occurrence}件" + (f"（同じ種類の{omitted}件は代表1件で示す）" if omitted else "")
    lines = ["", heading, "", count_line, ""]
    events = evidence.get("events", [])
    if kind in session_review_evidence.UNTRUNCATED_EVIDENCE_KINDS:
        bodies = [
            _readable_entry_text(str(event["text"]))
            for event in events
            if event.get("kind") == "detail" and isinstance(event.get("text"), str) and event["text"].strip()
        ]
        bodies.extend(
            json.dumps(event["input"], ensure_ascii=False, indent=2)
            for event in events
            if event.get("kind") == "detail" and "input" in event
        )
    else:
        bodies = []
    if not bodies and isinstance(candidate.get("text"), str):
        bodies = [candidate["text"]]
    lines.append("本文:")
    lines.append("")
    lines.extend(_fence("\n\n".join(bodies) if bodies else "（本文なし）"))
    tool_uses = [event for event in events if event.get("kind") == "tool-use"]
    if tool_uses:
        lines.extend(["", "対象のツール呼び出し:", ""])
        for event in tool_uses:
            lines.extend(_fence(f"{event.get('name', '')}\n{json.dumps(event.get('input'), ensure_ascii=False, indent=2)}"))
    contexts = [event for event in events if event.get("kind") == "user-context"]
    if contexts:
        lines.extend(["", "直前と直後のユーザー発話:", ""])
        for event in contexts:
            label = "直前" if event.get("direction") == "before" else "直後"
            text = str(event.get("text", "")).strip()
            lines.append(f"- {label}: {' '.join(text.split()) if text else '（本文なし）'}")
    if kind == "user-intervention" and not any(event.get("direction") == "after" for event in contexts):
        lines.append("- 直後: 同じ記録に後続のユーザー発話は無い")
    return lines


def _stats_lines(stats: list[dict[str, Any]]) -> list[str]:
    by_kind: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in stats:
        by_kind[str(event.get("kind"))].append(event)
    total = next(iter(by_kind.get("stats-total", [])), {})
    lines = [
        "",
        "## セッション統計",
        "",
        f"- 経過秒: {total.get('elapsed_seconds', 'unknown')}秒（セッションの最初の記録から準備時点まで）",
    ]
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
    threads = by_kind.get("stats-agent-thread", [])
    if threads:
        lines.extend(["", "| thread | 実行系 | 経過秒 | 応答回数 |", "| --- | --- | --- | --- |"])
        lines.extend(
            "| "
            + " | ".join(str(item.get(key, "unknown")) for key in ("thread", "engine", "elapsed_seconds", "api_messages"))
            + " |"
            for item in threads
        )
    slow = sorted(by_kind.get("stats-slow-call", []), key=lambda item: -float(item.get("seconds", 0)))[:_SLOW_CALL_LIMIT]
    if slow:
        lines.extend(["", "遅い呼び出しの上位:", ""])
        for item in slow:
            hint = " ".join(str(item.get("hint", "")).split())
            lines.append(f"- {item.get('tool')} {item.get('seconds')}秒" + (f": {hint}" if hint else ""))
    return lines


def _queue_lines(lists: dict[str, list[dict[str, str]]] | None) -> list[str]:
    lines = ["", "## 既存キュー項目", ""]
    if lists is None:
        return [*lines, "対象リポジトリを指定しない準備のため、既存キュー項目を取得していない。"]
    titles = {
        "active": "未終端の項目",
        "adopted-session-review": "採用済みの振り返り由来の項目",
        "answered-uwi": "回答済みのUWI",
    }
    for key, title in titles.items():
        items = lists.get(key, [])
        shown = items
        if key == "adopted-session-review" and len(items) > _ADOPTED_LIST_LIMIT:
            shown = sorted(items, key=lambda item: str(item.get("filename")))[-_ADOPTED_LIST_LIMIT:]
            title += (
                f"のうち新しい{_ADOPTED_LIST_LIMIT}件。"
                "それより前の項目は`atk wi grep --state=adopted --source=session-review`で検索する"
            )
        lines.extend([f"{title}（{len(items)}件）:", ""])
        lines.extend(f"- {item.get('filename')}: {_summary(item.get('summary', ''))}" for item in shown)
        lines.append("")
    return lines[:-1]


def _summary(value: object) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= _SUMMARY_MAX_CHARS else text[:_SUMMARY_MAX_CHARS] + "…"


def build_material(
    *,
    session_label: str,
    session_reference: str,
    target_repo: pathlib.Path | None,
    candidates: list[dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    stats: list[dict[str, Any]],
    observations: str,
    queue_lists: dict[str, list[dict[str, str]]] | None,
    reference_document: pathlib.Path | None,
    prepared_at: str,
) -> str:
    """振り返り素材AWIの本文を組み立てる。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[3]
    counts = collections.Counter(str(item["candidate_kind"]) for item in candidates)
    count_text = "、".join(f"{kind} {count}件" for kind, count in sorted(counts.items())) or "なし"
    observation_count = _observation_count(observations)
    lines = [
        f"# セッション{session_label}の振り返り素材を分析し、対策を実装する",
        "",
        "対象セッションで抽出した問題候補とメイン由来の改善点について、候補ごとの原因を確定し、対策と再発防止策を実装する。"
        "本文は準備スクリプトが機械生成した振り返り素材であり、元のセッション記録を開かずに判定と原因分析を完了できる情報を載せる。",
        "",
        "## 反映内容と反映先",
        "",
        f"`{plugin_root / LANE_PROCESSING_DOCUMENT}`の手順に従い、`## 問題候補`の全候補とメイン由来の改善点を一次選別し、"
        "原因分析、対策と再発防止策の実装、統合までを本項目の処理で完了する。"
        "処理の完了時に、候補ごとの問題、原因、対策、再発防止策及び見送りの理由をまとめた事後承認型UWIを1件投入する。"
        f"対象リポジトリは`{target_repo if target_repo is not None else 'なし'}`とする。",
        "",
        "## 適用範囲",
        "",
        f"対象セッションで抽出した候補{len(candidates)}件（{count_text}）と、メイン由来の改善点{observation_count}件を扱う。"
        "誤りの機構が依存する条件と、観測事象と表面構造が異なる該当例は、候補ごとの原因分析で確定する。",
        "",
        "## 実現性",
        "",
        f"候補、統計及び既存キュー項目の一覧は、準備スクリプトが{prepared_at}に抽出器と`atk wi list`から取得した。"
        "ユーザー介入、失敗したコマンドの診断、委譲返却及びエスカレーションの本文は切り詰めずに載せ、"
        "hook通知など定型の本文は通知本文と対象のツール呼び出しで示す。",
        "",
        "## 完成条件",
        "",
        "- 全候補の判定を`atk run-script session-review-report -- check`が受理する",
        "- 欠陥と判定した候補とユーザー介入の候補が、実装済みの成果物、未完了のAWI又は確認中のUWIへ"
        "再発防止策として対応付いている",
        "- 本項目専用の事後承認型UWIが投入されている",
        "",
        session_review_decisions.CANDIDATES_HEADING,
    ]
    if not candidates:
        lines.extend(["", "抽出器が問題候補を返さなかった。"])
    for candidate in candidates:
        lines.extend(_candidate_lines(candidate, evidence_by_id.get(str(candidate["candidate_id"]), {})))
    lines.extend(["", "## メイン由来の改善点", ""])
    lines.extend(_fence(observations) if observations.strip() else ["メインが列挙した改善点は0件である。"])
    lines.extend(_stats_lines(stats))
    lines.extend(_queue_lines(queue_lists))
    lines.extend(
        [
            "",
            "## 参考情報",
            "",
            f"- 振り返りの参照文書: {f'`{reference_document}`' if reference_document is not None else 'なし'}",
            f"- 対象セッション: {session_reference}",
            f"- 準備時刻: {prepared_at}",
            "- 処理側は通常これらを開かずに判断できる。対象セッションの記録は、素材で直接原因を確定できない候補に限り"
            "抽出器`session-review-evidence`の`--grep`と`--detail`で照会する。"
            "参照文書は対象リポジトリ固有の振り返り観点と所要時間目標を持つ場合に読む",
        ]
    )
    return "\n".join(lines) + "\n"


def _observation_count(observations: str) -> int:
    return sum(1 for line in observations.splitlines() if line.startswith("- "))


def _submitted_filename(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if matched := _ADD_SUCCESS_PATH.match(line):
            return matched.group(1)
    return None


def main(argv: list[str] | None = None, *, now: datetime.datetime | None = None) -> int:
    """素材AWIを生成して投入し、結果を1行のJSONで出力する。"""
    args = _build_parser().parse_args(argv)
    local_evidence_script = pathlib.Path(__file__).resolve().with_name("session_review_evidence.py")
    try:
        evidence_script = run_script.registered_script_path("session-review-evidence")
    except ValueError:
        return _missing("evidence_script")
    if local_evidence_script != evidence_script:
        return _missing("evidence_script")

    work_dir = pathlib.Path(args.work_dir).expanduser()
    if not work_dir.is_absolute() or not work_dir.is_dir():
        return _missing("work_dir")
    observations_path = work_dir / MAIN_OBSERVATIONS_FILENAME
    if not observations_path.is_file():
        return _missing("main-observations", str(observations_path))
    transcript_path = pathlib.Path(args.transcript).expanduser().resolve() if args.transcript is not None else None
    if transcript_path is not None and not transcript_path.is_file():
        return _missing("transcript_path")
    target_repo = pathlib.Path(args.target_repo).expanduser().resolve() if args.target_repo is not None else None
    executable = shutil.which("atk")
    if target_repo is not None and executable is None:
        return _missing("atk")

    session_arguments = [str(transcript_path)] if transcript_path is not None else ["--codex-thread-id", args.codex_thread_id]
    bundle_dir = work_dir / "bundle"
    bundle_dir.mkdir(exist_ok=True)
    if (failure := _extract_bundle(session_arguments, bundle_dir)) is not None:
        return _missing("bundle", failure)
    try:
        candidate_rows = _read_jsonl(bundle_dir / "candidates.jsonl")
        stats = _read_jsonl(bundle_dir / "stats.jsonl")
        candidates = [row for row in candidate_rows if row.get("kind") == "candidate"]
        evidence_by_id = {
            str(row["candidate_id"]): json.loads((bundle_dir / row["path"]).read_text(encoding="utf-8"))
            for row in _read_jsonl(bundle_dir / "candidate-evidence.jsonl")
        }
    except (OSError, ValueError, KeyError) as error:
        return _missing("bundle", str(error))

    queue_lists: dict[str, list[dict[str, str]]] | None = None
    if target_repo is not None:
        assert executable is not None
        fetched = _queue_lists(executable, target_repo)
        if isinstance(fetched, str):
            return _missing("queue_lists", fetched)
        queue_lists = fetched

    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    prepared_at = current.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    session_label = f"Claude Code {transcript_path.stem}" if transcript_path is not None else f"Codex {args.codex_thread_id}"
    # 抽出器の`--transcript`と`--codex-thread-id`へそのまま渡せる値を参考情報へ載せる。
    session_reference = (
        f"Claude Code（transcript: `{transcript_path}`）"
        if transcript_path is not None
        else f"Codex（thread ID: `{args.codex_thread_id}`）"
    )
    observations = observations_path.read_text(encoding="utf-8")
    material = build_material(
        session_label=session_label,
        session_reference=session_reference,
        target_repo=target_repo,
        candidates=candidates,
        evidence_by_id=evidence_by_id,
        stats=stats,
        observations=observations,
        queue_lists=queue_lists,
        reference_document=_reference_document(target_repo, codex=args.codex_thread_id is not None),
        prepared_at=prepared_at,
    )
    material_path = work_dir / MATERIAL_FILENAME
    material_path.write_text(material, encoding="utf-8")

    awi_filename: str | None = None
    submitted = False
    # 候補も改善点も無いセッションは分析の対象を持たないため、キューへ空の素材を積まない。
    has_material = bool(candidates) or _observation_count(observations) > 0
    if target_repo is not None and has_material:
        assert executable is not None
        add_arguments = [
            "wi",
            "add",
            "--source",
            "session-review",
            f"--target-repo={target_repo}",
            "--body-file",
            str(material_path),
        ]
        if args.dry_run:
            add_arguments.append("--dry-run")
        result = _run_atk(executable, add_arguments)
        if result is None or result.returncode != 0:
            return _missing("atk wi add", result.stderr if result is not None else "起動できない")
        if not args.dry_run:
            awi_filename = _submitted_filename(result.stdout)
            if awi_filename is None:
                return _missing("awi_filename", result.stdout)
            submitted = True

    total = next((event for event in stats if event.get("kind") == "stats-total"), {})
    compaction = next((event for event in stats if event.get("kind") == "stats-compaction-total"), {})
    record = {
        "work_dir": str(work_dir),
        "material_path": str(material_path),
        "submitted": submitted,
        "skipped_reason": None if has_material else "no-candidates",
        "awi_filename": awi_filename,
        "candidate_total": len(candidates),
        "candidate_counts": dict(sorted(collections.Counter(str(item["candidate_kind"]) for item in candidates).items())),
        "main_observation_count": _observation_count(observations),
        "elapsed_seconds": total.get("elapsed_seconds"),
        "compaction_count": compaction.get("count", 0),
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
