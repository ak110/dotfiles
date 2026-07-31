"""`atk mq schedule`のI/O・ロック・永続化を担うCLI層。"""

import argparse
import dataclasses
import datetime
import json
import pathlib
import sys
import typing

import _atk_mq_add as _add
import _atk_mq_common as _common
import _atk_mq_schedule as _schedule
from _atk_mq_repo import _resolve_repo_id

# pylint: disable=protected-access


def _load_unanswered_repair_keys(private_notes: pathlib.Path) -> frozenset[_schedule.RepairKey]:
    """全リポジトリの未回答修復TBDが参照する対象filenameと理由区分を返す。"""
    return frozenset(
        (entry.repair_target_filename, entry.repair_kind)
        for entry in _common._load_schedule_entries(private_notes, None, _common.MQ_ACTIVE_STATES)
        if entry.kind == _common.MQ_TYPE_TBD
        and entry.tbd_answered is False
        and entry.repair_target_filename is not None
        and entry.repair_kind is not None
    )


def cmd_schedule(args: argparse.Namespace, private_notes: pathlib.Path) -> int:
    """分類適用、選抜計算、繰越記録、修復TBD投入を単一ロック内で実行する。"""
    target_repo = _resolve_repo_id(args.target_repo)
    try:
        with _common._repo_lock(private_notes):
            _common._pull(private_notes)
            active = _common._load_schedule_entries(private_notes, target_repo, _common.MQ_ACTIVE_STATES)
            terminal = _common._load_schedule_entries(
                private_notes,
                target_repo,
                (_common.MQ_STATE_ADOPTED, _common.MQ_STATE_REJECTED),
            )
            if args.record_deferral is not None:
                result, changed = _record_deferrals(private_notes, active, args.record_deferral)
                if changed:
                    _common._commit_and_push(
                        private_notes,
                        "chore: record feedback queue deferrals",
                        list(_common.MQ_ACTIVE_STATES),
                    )
                _print_result(result)
                return 0

            classifications = _load_classifications(args.classifications)
            plan_target_files = _load_plan_target_files((*active, *terminal), classifications)
            regenerated = _schedule.regenerate_plan_metadata(active)
            updated = _apply_requested_classifications(regenerated, terminal, classifications)
            changed = _persist_metadata_changes(private_notes, active, updated)
            active = updated

            existing_repairs = _load_unanswered_repair_keys(private_notes)
            result = _schedule.calculate_schedule(active, terminal, plan_target_files, existing_repairs)
            deferred_active = _apply_calculated_deferrals(active, result.deferred)
            changed = _persist_metadata_changes(private_notes, active, deferred_active) or changed

            filed_frontmatter_repairs = _file_repair_tbds(
                private_notes,
                target_repo,
                result.frontmatter_broken_needs_tbd_filenames,
                "{filename}のYAML frontmatterが破損しています。対象ファイルを直接編集して修復してください。",
                repair_kind="frontmatter",
            )
            filed_plan_repairs = _file_repair_tbds(
                private_notes,
                target_repo,
                result.missing_plan_file_needs_tbd_filenames,
                "{filename}が参照する計画ファイルが実在しません。"
                "計画ファイルを復元するか、対象ファイルのplan_fileを修復してください。",
                repair_kind="missing-plan-file",
            )
            if changed or filed_frontmatter_repairs or filed_plan_repairs:
                _common._commit_and_push(
                    private_notes,
                    "chore: update feedback queue schedule",
                    list(_common.MQ_ACTIVE_STATES),
                )
            _print_result(result)
            return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"schedule入力を拒否しました: {error}", file=sys.stderr)
        return 2


def _load_classifications(path_value: str | None) -> tuple[_schedule.Classification, ...]:
    if path_value is None:
        return ()
    path = pathlib.Path(path_value)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"classifications"}:
        raise ValueError("分類結果JSONはclassificationsキーだけを持つobjectで指定してください")
    raw_items = payload["classifications"]
    if not isinstance(raw_items, list):
        raise ValueError("classificationsは配列で指定してください")
    classifications: list[_schedule.Classification] = []
    for raw_item in raw_items:
        classification = _schedule.classification_from_mapping(raw_item)
        if classification is None:
            raise ValueError("分類結果の必須キーまたは型が不正です")
        classifications.append(classification)
    filenames = [item.filename for item in classifications]
    if len(filenames) != len(set(filenames)):
        raise ValueError("分類結果に同一filenameが重複しています")
    return tuple(classifications)


def _load_plan_target_files(
    entries: tuple[_schedule.QueueEntry, ...],
    classifications: tuple[_schedule.Classification, ...],
) -> dict[str, tuple[str, ...]]:
    plan_files = {
        plan_file
        for entry in entries
        if (plan_file := entry.plan_file or (entry.metadata.plan_file if entry.metadata is not None else None)) is not None
    } | {classification.plan_file for classification in classifications if classification.plan_file is not None}
    result: dict[str, tuple[str, ...]] = {}
    for plan_file in plan_files:
        path = pathlib.Path(plan_file)
        if path.is_file():
            result[plan_file] = _schedule.parse_plan_target_files(path.read_text(encoding="utf-8"))
    return result


def _apply_requested_classifications(
    entries: tuple[_schedule.QueueEntry, ...],
    terminal_entries: tuple[_schedule.QueueEntry, ...],
    classifications: tuple[_schedule.Classification, ...],
) -> tuple[_schedule.QueueEntry, ...]:
    known = {entry.filename for entry in entries if not entry.frontmatter_broken}
    unknown = sorted(classification.filename for classification in classifications if classification.filename not in known)
    if unknown:
        raise ValueError(f"分類対象外filenameです: {', '.join(unknown)}")
    _warn_body_sha256_mismatches(entries, classifications)
    return _schedule.apply_classifications(entries, terminal_entries, classifications)


def _warn_body_sha256_mismatches(
    entries: tuple[_schedule.QueueEntry, ...],
    classifications: tuple[_schedule.Classification, ...],
) -> None:
    """`source_body_sha256`が実ファイルと一致しない分類を標準エラー出力へ報告する。

    不一致の分類は`apply_classifications`が例外を送出せず警告も出力しないまま除外する。
    未分類だった項目は結果として`classification_required`へ現れるが、既に有効な分類を
    持つ項目は既存分類が維持され同一覧へは現れない。したがって遷移先を一律には断定できず、
    「適用されない」事実のみを報告する。原因の特定を助けるため、期待値と受理値を突き合わせて示す。
    """
    by_name = {entry.filename: entry for entry in entries}
    mismatches = [
        (
            classification.filename,
            _schedule.body_sha256(by_name[classification.filename].text),
            classification.source_body_sha256,
        )
        for classification in classifications
        if classification.filename in by_name
        and _schedule.body_sha256(by_name[classification.filename].text) != classification.source_body_sha256
    ]
    if not mismatches:
        return
    print(
        f"source_body_sha256が実ファイルと一致しない分類が{len(mismatches)}件あります"
        "（当該分類は適用されません。既存の有効な分類が無い項目はclassification_requiredへ計上されます）",
        file=sys.stderr,
    )
    for filename, expected, received in mismatches:
        print(f"  {filename}: 期待={expected[:16]} 受理={received[:16]}", file=sys.stderr)


def _persist_metadata_changes(
    private_notes: pathlib.Path,
    before: tuple[_schedule.QueueEntry, ...],
    after: tuple[_schedule.QueueEntry, ...],
) -> bool:
    before_by_name = {entry.filename: entry for entry in before}
    changed = False
    for entry in after:
        previous = before_by_name[entry.filename]
        if entry.metadata == previous.metadata or entry.metadata is None:
            continue
        path = _find_active_path(private_notes, entry.filename)
        content = _schedule.serialize_schedule_metadata(path.read_text(encoding="utf-8"), entry.metadata)
        path.write_text(content, encoding="utf-8")
        changed = True
    return changed


def _apply_calculated_deferrals(
    entries: tuple[_schedule.QueueEntry, ...],
    deferred: tuple[_schedule.DeferredItem, ...],
) -> tuple[_schedule.QueueEntry, ...]:
    reasons: dict[str, _schedule.DeferralReason] = {item.filename: item.reason for item in deferred}
    return tuple(
        dataclasses.replace(entry, metadata=_schedule.with_deferral(entry.metadata, reasons[entry.filename]))
        if entry.filename in reasons and entry.metadata is not None
        else entry
        for entry in entries
    )


def _record_deferrals(
    private_notes: pathlib.Path,
    entries: tuple[_schedule.QueueEntry, ...],
    specifications: list[str],
) -> tuple[_schedule.ScheduleResult, bool]:
    by_name = {entry.filename: entry for entry in entries}
    deferred: list[_schedule.DeferredItem] = []
    updated = list(entries)
    positions = {entry.filename: index for index, entry in enumerate(entries)}
    for specification in specifications:
        reason, separator, filename = specification.partition(":")
        if separator != ":" or reason not in ("dependency-unmet", "limit-exceeded", "conflict") or not filename:
            raise ValueError(f"繰越指定はREASON:FILENAME形式で指定してください: {specification}")
        entry = by_name.get(filename)
        if entry is None or entry.metadata is None:
            raise ValueError(f"繰越対象外または未分類のfilenameです: {filename}")
        typed_reason = typing.cast(_schedule.DeferralReason, reason)
        updated[positions[filename]] = dataclasses.replace(
            entry,
            metadata=_schedule.with_deferral(entry.metadata, typed_reason),
        )
        deferred.append(_schedule.DeferredItem(filename, typed_reason))
    changed = _persist_metadata_changes(private_notes, entries, tuple(updated))
    return _schedule.ScheduleResult(deferred=tuple(deferred)), changed


def _file_repair_tbds(
    private_notes: pathlib.Path,
    target_repo: str,
    filenames: tuple[str, ...],
    question_template: str,
    *,
    repair_kind: _schedule.RepairKind,
) -> bool:
    if not filenames:
        return False
    messages: list[tuple[dict[str, object], str]] = [
        (
            {},
            question_template.format(filename=filename),
        )
        for filename in filenames
    ]
    _add._add_entries_locked(
        private_notes,
        parsed_messages=messages,
        target_repo=target_repo,
        source="atk-mq-schedule",
        now=datetime.datetime.now(),
        entry_type=_common.MQ_TYPE_TBD,
        scope="hold",
        question_type="free-form",
        choices=None,
        repair_targets=list(filenames),
        repair_kinds=[repair_kind for _ in filenames],
    )
    return True


def _find_active_path(private_notes: pathlib.Path, filename: str) -> pathlib.Path:
    matches = [private_notes / state / filename for state in _common.MQ_ACTIVE_STATES]
    existing = [path for path in matches if path.is_file()]
    if len(existing) != 1:
        raise ValueError(f"active状態からfilenameを一意に解決できません: {filename}")
    return existing[0]


def _print_result(result: _schedule.ScheduleResult) -> None:
    payload = {
        "classification_required": list(result.classification_required),
        "plan_items": list(result.plan_items),
        "parallel_normal_items": list(result.parallel_normal_items),
        "post_plan_normal_items": list(result.post_plan_normal_items),
        "deferred": [dataclasses.asdict(item) for item in result.deferred],
        "missing_dependency_tbds": [dataclasses.asdict(item) for item in result.missing_dependency_tbds],
        "frontmatter_broken_filenames": list(result.frontmatter_broken_filenames),
        "frontmatter_broken_needs_tbd_filenames": list(result.frontmatter_broken_needs_tbd_filenames),
        "missing_plan_file_filenames": list(result.missing_plan_file_filenames),
        "missing_plan_file_needs_tbd_filenames": list(result.missing_plan_file_needs_tbd_filenames),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=False))
