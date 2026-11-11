"""振り返りの入力準備（session_review_prepare_failures）。"""

from __future__ import annotations

import collections
import datetime
import json
from typing import Any

import session_evidence_candidates
from session_review_prepare_candidates import _FAILURE_KINDS, _FAILURE_LEDGER_DAYS

from agent_toolkit._common import atomic_file, file_lock
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.json_lines import split_lines


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
                    lines = split_lines(path.read_text(encoding="utf-8"))
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
