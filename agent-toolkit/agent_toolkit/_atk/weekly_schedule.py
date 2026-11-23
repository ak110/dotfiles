"""日曜14時（ホストの現地時刻）を単位とする週枠で、週次の作業の起動を判定する。  pyfltr: allow-comment

週次の作業はメタ分析（`meta`）と外部情報の調査（`research`）で、どちらも`review-session`のモードとして起動する。
回の完了の根拠は、その回の結果のUWIがキューに保存されていることとする。
結果のUWIは`weekly_slot`（`<作業>@<日曜の日付>`）を持ち、全状態のどこかに同じ値があればその回を実行しない。
UWIの保存の直後、ローカルの記録の前に止まっても、同じ回の再実行・再課金・二重の通知は起きない。
保存の前に止まった回は未完了のまま残り、次の判定で再実行する。

判定は最新の到来済みの週枠だけを対象にする。日曜14時より前は前週の枠が最新であり、
前週が完了済みなら何も起動せず、未完了なら前週の枠だけを1回追って起動する。
2回以上逃した場合も、過去の枠は対象にせず最新の枠だけを実行する。
"""

from __future__ import annotations

import datetime
import pathlib
import re
from typing import Any

from agent_toolkit._atk import config
from agent_toolkit._atk.wi import frontmatter, layout
from agent_toolkit._common import private_notes, session_review
from agent_toolkit._common.wi_layout import DATED_NAME

WORKS = ("meta", "research")

SLOT_ENV = session_review.WEEKLY_SLOT_ENV

SLOT_KEY = "weekly_slot"

SLOT_WEEKDAY = 6

SLOT_TIME = datetime.time(14)

RESEARCH_RETRY_SECONDS = 6 * 60 * 60

RESEARCH_FAILURE_REPORT_THRESHOLD = 2

_SLOT_PATTERN = re.compile(r"(?P<work>meta|research)@(?P<date>\d{4}-\d{2}-\d{2})")

_AUTO_START_KEYS = {"meta": "weekly_meta_auto_start", "research": "weekly_research_auto_start"}

LEGACY_META_RECORD = "meta-start.json"


def latest_slot(now: datetime.datetime) -> datetime.date:
    local = now.astimezone()
    days = (local.weekday() - SLOT_WEEKDAY) % 7
    sunday = local.date() - datetime.timedelta(days=days)
    if days == 0 and local.time() < SLOT_TIME:
        sunday -= datetime.timedelta(days=7)
    return sunday


def slot_start(slot: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(slot, SLOT_TIME).astimezone()


def slot_id(work: str, slot: datetime.date) -> str:
    if work not in WORKS:
        raise ValueError(f"週次の作業ではない: {work}")
    return f"{work}@{slot.isoformat()}"


def parse_slot_id(value: str) -> tuple[str, datetime.date]:
    match = _SLOT_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"週枠の識別子が不正: {value}")
    return match["work"], datetime.date.fromisoformat(match["date"])


def work_of_scope(scope: str) -> str | None:
    for work in WORKS:
        if scope == f"{session_review.REVIEW_SESSION_SCOPE} {work}":
            return work
    return None


def enabled(work: str) -> bool:
    if config.resolve_mutable_setting(_AUTO_START_KEYS[work]) != "true":
        return False
    return work != "meta" or config.resolve_mutable_setting("session_review_auto_start") != "false"


def record_path(work: str) -> pathlib.Path:
    if work not in WORKS:
        raise ValueError(f"週次の作業ではない: {work}")
    return session_review.directory() / "weekly" / f"{work}.json"


def read_record(work: str) -> dict[str, Any]:
    path = record_path(work)
    if work == "meta":
        migrate_legacy_record()
    try:
        return session_review.read(path)
    except FileNotFoundError:
        return {}


def write_record(work: str, value: dict[str, Any]) -> None:
    path = record_path(work)
    path.parent.mkdir(parents=True, exist_ok=True)
    session_review.write(path, value)


def migrate_legacy_record() -> None:
    legacy = session_review.directory() / LEGACY_META_RECORD
    if not legacy.is_file():
        return
    target = record_path("meta")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        session_review.write(target, session_review.read(legacy))
    legacy.unlink(missing_ok=True)


def saved_slot(slot: str, *, notes: pathlib.Path | None = None) -> pathlib.Path | None:
    _work, date = parse_slot_id(slot)
    root = private_notes.default_private_notes() if notes is None else notes
    if not root.is_dir():
        return None
    earliest = (date - datetime.timedelta(days=1)).strftime("%Y%m%d")
    for path in layout.iter_entry_paths(root):
        if DATED_NAME.fullmatch(path.name) is not None and path.name[:8] < earliest:
            continue
        try:
            parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError, UnicodeDecodeError:
            continue
        if parsed is not None and parsed[0].get(SLOT_KEY) == slot:
            return path
    return None


def completed(work: str, slot: datetime.date) -> bool:
    identifier = slot_id(work, slot)
    record = read_record(work)
    if record.get("completed_slot") == identifier:
        return True
    if saved_slot(identifier) is None:
        return False
    write_record(work, {**record, "completed_slot": identifier})
    return True


def research_retry_wait(record: dict[str, Any], slot: str, *, now: float) -> bool:
    if record.get("slot") != slot or not isinstance(failed_at := record.get("failed_at"), str):
        return False
    return now - datetime.datetime.fromisoformat(failed_at).timestamp() < RESEARCH_RETRY_SECONDS
