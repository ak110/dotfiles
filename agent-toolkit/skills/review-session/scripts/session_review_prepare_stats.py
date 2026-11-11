"""振り返りの入力準備（session_review_prepare_stats）。"""

from __future__ import annotations

import collections
import json
from typing import Any

from session_review_prepare_text import _SUMMARY_TOP_COUNT, _clip_utf8, _one_line

from agent_toolkit._common import next_action as _next_action

_SLOW_CALL_LIMIT = 10


def _stats_document(stats: list[dict[str, Any]]) -> str:
    """総量と上位だけを初回表示し、全担当・全対象の原始集計への参照を保つ。"""
    by_kind: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for item in stats:
        by_kind[str(item.get("kind"))].append(item)

    def token_volume(item: dict[str, Any]) -> int:
        tokens = item.get("tokens")
        if not isinstance(tokens, dict):
            return 0
        # reasoningとcache readは実行系によって総成分に含まれるため、順位にも重ねて足さない。
        keys = ["input_tokens", "output_tokens", "cache_creation_input_tokens"]
        if item.get("engine") != "codex":
            keys.append("cache_read_input_tokens")
        return sum(int(tokens.get(key, 0)) for key in keys)

    owners = sorted([*by_kind["stats-subagent"], *by_kind["stats-agent-thread"]], key=token_volume, reverse=True)[
        :_SUMMARY_TOP_COUNT
    ]
    selected: list[dict[str, Any]] = [*by_kind["stats-total"], *by_kind["stats-summary"], *owners]
    selected.extend(by_kind["stats-mode"])
    selected.extend(by_kind["stats-hook-timings"])
    for kind in ("stats-critical-path", "stats-compaction-total"):
        for item in by_kind[kind]:
            row = dict(item)
            if kind == "stats-critical-path":
                row["segments"] = sorted(
                    item.get("segments", []), key=lambda segment: -float(segment.get("exclusive_seconds", 0))
                )[:10]
                row["unmeasured_threads"] = item.get("unmeasured_threads", [])[:10]
            else:
                row["by_record"] = dict(sorted(item.get("by_record", {}).items(), key=lambda pair: -pair[1])[:10])
            selected.append(row)
    for kind in ("stats-compaction", "stats-compaction-usage", "stats-slow-call", "stats-token-peak"):
        selected.extend(by_kind[kind][:_SUMMARY_TOP_COUNT])
    for kind in ("stats-thread-tool", "stats-thread-gap", "stats-thread-repeat", "stats-thread-slow-call"):
        threads: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for item in by_kind[kind]:
            threads[str(item.get("thread"))].append(item)
        for items in threads.values():
            selected.extend(items[:5])
    resources = sorted(by_kind["stats-resource-usage"], key=lambda item: -int(item.get("recorded_result_utf8_bytes", 0)))
    for item in resources[:10]:
        row = dict(item)
        row["read_count"] = len(item.get("reads", []))
        row["skill_count"] = len(item.get("skills", []))
        for field in ("reads", "skills", "repeats", "largest_results", "hook_notices", "partial_repetitions"):
            row[field] = item.get(field, [])[:5]
        components: dict[str, int] = collections.defaultdict(int)
        for generated in item.get("generated_inputs", []):
            components[generated["component"]] += generated["utf8_bytes"]
        row["input_components"] = dict(components)
        row["generated_inputs"] = sorted(item.get("generated_inputs", []), key=lambda generated: -generated["utf8_bytes"])[:5]
        row["omitted_largest_result_count"] = item.get("omitted_largest_result_count", 0) + max(
            0, len(item["largest_results"]) - 5
        )
        row["omitted_repeat_count"] = item.get("omitted_repeat_count", 0) + max(0, len(item["repeats"]) - 5)
        selected.append(row)
    for item in by_kind["stats-resource-summary"]:
        row = dict(item)
        for field in ("reads_by_target", "skills_by_name", "shell_paths_by_target"):
            groups = sorted(
                item.get(field, []), key=lambda group: (-int(group.get("count", 0)), -int(group.get("utf8_bytes", 0)))
            )
            row[field] = [dict(group, by_record=dict(list(group["by_record"].items())[:3])) for group in groups[:10]]
        selected.append(row)
    total = next(iter(by_kind["stats-total"]), {})
    text = _stats_detail_document(selected)
    text = _clip_utf8(text, 16_000)
    large_results = sorted(
        [row for item in resources for row in item.get("large_output_reviews", [])],
        key=lambda row: (-int(row["utf8_bytes"]), row["location"]),
    )[:10]
    large_total = sum(item.get("large_output_review_count", 0) for item in resources)
    text += (
        "\n## 大出力の点検上位\n\n"
        f"エージェントが受け取った本文8192バイト以上を、単独の明示本文取得を除き大きい順に最大10件表示する。対象{large_total}件、"
        f"上位外{max(0, large_total - len(large_results))}件。想定内本文取得の除外は"
        f"{sum(item.get('expected_large_read_count', 0) for item in resources)}件。"
        "複合コマンドや解釈できない入力は点検対象へ残す。バイト数は記録された本文だけを数える。\n"
    )
    for row in large_results:
        text += (
            f"- {row['utf8_bytes']}バイト {row['tool']} 入力={row['location']} 結果={row['result_location']}: "
            + _clip_utf8(str(row.get("input_summary", "")), 240)
            + "\n"
        )
    persisted = sorted(
        [row for item in resources for row in item.get("persisted_results", [])],
        key=lambda row: (-int(row["original_utf8_bytes"]), row["location"]),
    )[:10]
    persisted_count = sum(item.get("persisted_result_count", 0) for item in resources)
    text += (
        f"\n## 退避された大出力\n\n退避前8192バイト以上の結果は{persisted_count}件。退避前の量が大きい順に最大10件を示す。\n"
    )
    for row in persisted:
        text += (
            f"- 退避前{row['original_utf8_bytes']}バイト、受領{row['utf8_bytes']}バイト {row['tool']} "
            f"入力={row['location']} 結果={row['result_location']}: "
            + _clip_utf8(str(row.get("input_summary", "")), 240)
            + "\n"
        )
    pointers = [
        "",
        "## 集計の母集団と詳細",
        "",
        "母集団はメイン・全担当の収集済み記録である。初回表示は担当・対象の上位10件、担当内の各内訳は先頭5件まで。"
        "全量と表示外の非律速担当はbundle/stats.jsonlとbundle/resource-usage.jsonlへ保持する。"
        "以下の省略件数は表示の省略であり、未解決記録とは別である。",
        f"- 共通4成分の総量: {json.dumps(total.get('tokens', '未確認'), ensure_ascii=False)}",
        "- 担当別tokensの表示外: "
        f"{max(0, len(by_kind['stats-subagent']) + len(by_kind['stats-agent-thread']) - len(owners))}件",
        f"- 読込・反復・結果量の担当別詳細の表示外: {max(0, len(resources) - 10)}件",
        f"- 未計測threadの総数: {sum(len(item.get('unmeasured_threads', [])) for item in by_kind['stats-critical-path'])}件",
        "- 担当内・対象別の表示外の件数と粒度は各全量配列から確認できる。原記録は候補文書の詳細照会を使う。",
        "",
        "## 費用確認17観点の照会先",
        "",
        "観点はreview-session/references/analysis.md「コストの無駄の確認」に従う。"
        "初回の上位と会話の流れを比較し、目的・契約の判断に足りない範囲だけ次を照会する。",
    ]
    for label, source in (
        ("同じファイルの重複読込", "対象別集計・resource-usage.jsonlのreads_by_targetとreads"),
        ("同じ記録内の再取得・再実行", "担当別のrepeats"),
        ("大量結果の直接取込み", "担当別のlargest_resultsと大出力点検"),
        ("スキルと規範の累積読込", "skills_by_nameと担当別skills"),
        ("委譲入力の過剰・冗長", "comparison-materials.jsonlの直接本文と参照位置"),
        ("複数担当の重複調査", "対象別のby_recordと担当別reads"),
        ("モデル段位の不適合", "stats-agent-threadのobserved_identitiesとtokens"),
        ("委譲の総費用", "全担当のtokens・api_messagesと会話の成果"),
        ("キャッシュ効率", "全担当のtokensのcache成分と応答間隔"),
        ("会話圧縮と読み直し", "stats-compactionと担当別readsの記録位置"),
        ("照会・待機の反復", "repeatsとstats-thread-gap"),
        ("振り返り自身の費用", "会話の準備・照会と担当別repeats"),
        ("レビューの往復・後出し", "会話と全担当のtokens・再読位置"),
        ("重複した検証", "stats-slow-callと会話・CIの範囲"),
        ("入力形式の誤りによる再試行", "会話とrepeats"),
        ("出力トークンの過大", "stats-token-peakと報告位置"),
        ("使われなかった取得・返却", "会話とreads・結果位置の後続消費"),
    ):
        pointers.append(f"- {label}: {source}（bundle内）")
    return text + "\n".join(pointers) + "\n"


def _stats_detail_document(stats: list[dict[str, Any]]) -> str:
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
    for item in by_kind.get("stats-hook-timings", []):
        lines.extend(
            [
                "",
                "## hookの所要時間",
                "",
                f"- 対象セッション: {item['session_id']}、取得状態: {item['status']}、観測{item['count']}件、"
                f"不正・書込途中の行{item['invalid_rows']}件。保存先: bundle/hook-timings.jsonl",
                "- 保持されている計測だけを対象とする。保持上限で古い計測が除かれるため、セッション全体の網羅は保証しない。"
                "計測に時刻が無いため、batchの差分期間へ限定できない。",
                "- totalとhandlerは区間別に示し、重ねて合算しない。並行hookの累積秒数を経過時間の占有率へ変換しない。",
            ]
        )
        if item["status"] == "unreadable":
            lines.extend(
                [
                    f"- hook計測を取得できなかった: {item['source']}。診断: {item['diagnostic']}",
                    "- " + _next_action.next_action_line(item["next_action"]),
                ]
            )
        for row in item["groups"]:
            lines.append(
                f"- {row['event'] or 'イベント不明'} / {row['operation']} / {row['stage']}: {row['count']}件、"
                f"中央値{row['median_seconds']:.6f}秒、合計{row['total_seconds']:.6f}秒"
            )
    for mode in by_kind.get("stats-mode", []):
        lines.append(f"- mode別費用: {json.dumps(mode, ensure_ascii=False)}")
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
    usage = {(item["record"], item["line"]): item for item in by_kind.get("stats-compaction-usage", [])}
    for item in by_kind.get("stats-compaction", []):
        line = f"  - {item['record']}:{item['line']}: {item.get('timestamp') or '時刻なし'}"
        interval = usage.get((item["record"], item["line"]))
        if interval is not None:
            top = "、".join(
                f"{document['path']}（{document['count']}回、{document['bytes']}バイト）"
                for document in interval.get("norm_doc_display_top", [])
            )
            line += (
                f"、区間のツール出力{interval.get('tool_output_bytes', 0)}バイト、"
                f"うちエージェント向け文書の表示{interval.get('norm_doc_display_bytes', 0)}バイト（上限寄り）"
                + (f"、表示の多いエージェント向け文書: {top}" if top else "")
            )
        lines.append(line)
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
                f"- 明示的なファイル読取: {item.get('read_count', len(item['reads']))}回、"
                f"Skill呼出: {item.get('skill_count', len(item['skills']))}回、"
                f"ツール呼出: {item['tool_calls']}回、記録の結果本文: {item['recorded_result_characters']}字・"
                f"{item['recorded_result_utf8_bytes']}バイト",
                f"- 起動mode: {item.get('mode') or '未確認'}、実モデル: "
                f"{json.dumps(item.get('observed_identities', []), ensure_ascii=False)}",
                f"- 生成入力: {item.get('generated_input_utf8_bytes', 0)}バイト、成分別: "
                f"{json.dumps(item.get('input_components', {}), ensure_ascii=False)}",
                "- 入力量は記録されたツール入力、結果量はエージェントが受け取った本文を数える。API履歴の再送量・"
                "未記録の注入資料・実際の読了量・料金はこの集計から推定しない。",
            ]
        )
        for field, label in (
            ("generated_inputs", "生成入力"),
            ("hook_notices", "hook生成元・同一本文"),
            ("partial_repetitions", "文の部分一致候補"),
        ):
            for row in item.get(field, []):
                lines.append(f"- {label}: {json.dumps(row, ensure_ascii=False)}")
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
