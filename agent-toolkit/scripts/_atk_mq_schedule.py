"""メッセージキューの分類メタデータと機械スケジューリングを提供する。"""

import dataclasses
import datetime
import hashlib
import pathlib
import re
import typing
from collections.abc import Mapping
from typing import Literal

import _atk_mq_frontmatter as _frontmatter

SCHEDULE_KEY = "queue_schedule"
STARVATION_THRESHOLD = 3
PLAN_LIMIT = 3
NORMAL_LIMIT = 20

# 依存種別。`external-upstream`は上流リポジトリの対応待ちで、ユーザー判断を要さない。
# `external-repo-entry`は別リポジトリのフィードバック完了待ちで、依存先の状態から機械判定できる。
type DependencyKind = Literal[
    "none",
    "entries",
    "inbox-empty",
    "external-user",
    "external-upstream",
    "external-repo-entry",
]
type FeedbackKind = Literal["plan-impl", "normal"]
type FeedbackEntryKind = Literal["feedback", "tbd", "unknown"]
type DeferralReason = Literal["dependency-unmet", "limit-exceeded", "conflict"]
type RepairKind = Literal["frontmatter", "missing-plan-file"]
type RepairKey = tuple[str, RepairKind]


# 依存先エントリが処理を終えたと判定する状態ディレクトリ名。
_TERMINAL_STATES = frozenset(("adopted", "rejected"))


@dataclasses.dataclass(frozen=True)
class Dependency:
    """エントリの処理開始条件。"""

    kind: DependencyKind
    filenames: tuple[str, ...] = ()
    condition: str | None = None
    tbd_filename: str | None = None
    # `external-upstream`の再評価時刻。ISO 8601の文字列として保持する。
    # 無引用で書くとYAMLが日時型として構築するため、直列化では必ず文字列として渡す。
    recheck_after: str | None = None
    # 外部条件を保留した理由。`condition`は次回再評価時の確認事項として使う。
    # 両項目は`queue_schedule`へ置き、本文hashと分類の有効性判定へ含めない。
    hold_reason: str | None = None
    # `external-repo-entry`の依存先リポジトリ（正規化済みリモートURL）。
    target_repo: str | None = None


@dataclasses.dataclass(frozen=True)
class ScheduleMetadata:
    """frontmatterへ永続化する分類メタデータ。"""

    body_sha256: str
    normalized_target_repo: str
    feedback_type: FeedbackKind
    dependency: Dependency
    plan_file: str | None
    target_files: tuple[str, ...]
    carry_count: int
    carry_reasons: tuple[DeferralReason, ...]
    # 直近で繰越を加算した実行単位の識別値。同一実行単位での二重計上を防ぐ。
    last_deferral_run_id: str | None = None


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """計算対象となるfeedback・TBD共通のエントリ表現。"""

    filename: str
    text: str
    kind: FeedbackEntryKind
    tbd_answered: bool | None
    frontmatter_broken: bool
    metadata: ScheduleMetadata | None
    repair_target_filename: str | None
    plan_file: str | None = None
    repair_kind: RepairKind | None = None
    # 依存先評価用の正規化済みリモートURL。
    normalized_target_repo: str | None = None
    # エントリを読み込んだ状態ディレクトリ名。
    state: str | None = None


@dataclasses.dataclass(frozen=True)
class Classification:
    """分類プロセスが返す1エントリ分の分類結果。"""

    filename: str
    source_body_sha256: str
    feedback_type: FeedbackKind
    dependency: Dependency
    plan_file: str | None
    target_files: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class DeferredItem:
    """次回へ繰り越す項目と理由。"""

    filename: str
    reason: DeferralReason


@dataclasses.dataclass(frozen=True)
class SuppressedItem:
    """再評価時刻まで選抜から外す項目と、その判断材料。

    繰越とは別扱いとする。充足時期が外部に依存する項目へ繰越を積むと、
    スターベーション優先で選抜され続ける一方で毎回除外される状態になる。
    """

    filename: str
    recheck_after: str
    hold_reason: str
    condition: str


@dataclasses.dataclass(frozen=True)
class MissingDependencyTbd:
    """恒常的な依存不成立についてTBD作成を要求する診断。"""

    filename: str
    reason: Literal["missing", "self", "cycle"]
    dependency_filenames: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ScheduleResult:
    """スケジューリング計算の固定出力。"""

    classification_required: tuple[str, ...] = ()
    plan_items: tuple[str, ...] = ()
    parallel_normal_items: tuple[str, ...] = ()
    post_plan_normal_items: tuple[str, ...] = ()
    deferred: tuple[DeferredItem, ...] = ()
    suppressed: tuple[SuppressedItem, ...] = ()
    missing_dependency_tbds: tuple[MissingDependencyTbd, ...] = ()
    frontmatter_broken_filenames: tuple[str, ...] = ()
    frontmatter_broken_needs_tbd_filenames: tuple[str, ...] = ()
    missing_plan_file_filenames: tuple[str, ...] = ()
    missing_plan_file_needs_tbd_filenames: tuple[str, ...] = ()


def body_sha256(text: str) -> str:
    """frontmatterを除いた本文のSHA-256を返す。"""
    parsed = _frontmatter.parse_frontmatter(text)
    body = parsed[1] if parsed is not None else text
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse_schedule_metadata(text: str) -> ScheduleMetadata | None:
    """frontmatterの分類メタデータを検証して返す。"""
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        return None
    mapping = parsed[0].get(SCHEDULE_KEY)
    if not isinstance(mapping, dict):
        return None
    metadata = mapping_to_metadata(mapping)
    if metadata is None or metadata.body_sha256 != body_sha256(text):
        return None
    return metadata


def serialize_schedule_metadata(text: str, metadata: ScheduleMetadata) -> str:
    """分類メタデータをYAMLマッピングとしてfrontmatterへ反映する。"""
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        raise ValueError("frontmatterが解析できません")
    data, body = parsed
    data[SCHEDULE_KEY] = metadata_to_mapping(metadata)
    return _frontmatter.serialize_frontmatter(data, body)


def metadata_to_mapping(metadata: ScheduleMetadata) -> dict[str, object]:
    """分類メタデータをYAML直列化可能なマッピングへ変換する。"""
    dependency: dict[str, object] = {"kind": metadata.dependency.kind}
    if metadata.dependency.kind == "entries":
        dependency["filenames"] = list(metadata.dependency.filenames)
    elif metadata.dependency.kind == "external-user":
        dependency["condition"] = metadata.dependency.condition
        dependency["tbd_filename"] = metadata.dependency.tbd_filename
    elif metadata.dependency.kind == "external-upstream":
        # 日時は文字列のまま渡す。YAMLへ日時型として書くと読み戻しで型が変わる。
        dependency["condition"] = metadata.dependency.condition
        dependency["recheck_after"] = metadata.dependency.recheck_after
        dependency["hold_reason"] = metadata.dependency.hold_reason
    elif metadata.dependency.kind == "external-repo-entry":
        dependency["filenames"] = list(metadata.dependency.filenames)
        dependency["target_repo"] = metadata.dependency.target_repo
    mapping: dict[str, object] = {
        "body_sha256": metadata.body_sha256,
        "normalized_target_repo": metadata.normalized_target_repo,
        "type": metadata.feedback_type,
        "dependency": dependency,
    }
    if metadata.feedback_type == "plan-impl":
        mapping["plan_file"] = metadata.plan_file
    else:
        mapping["target_files"] = list(metadata.target_files)
    mapping["carry_count"] = metadata.carry_count
    mapping["carry_reasons"] = list(metadata.carry_reasons)
    if metadata.last_deferral_run_id is not None:
        mapping["last_deferral_run_id"] = metadata.last_deferral_run_id
    return mapping


def mapping_to_metadata(mapping: dict[str, typing.Any]) -> ScheduleMetadata | None:
    """永続化マッピングを検証して分類メタデータへ変換する。"""
    body_hash = mapping.get("body_sha256")
    target_repo = mapping.get("normalized_target_repo")
    feedback_type = mapping.get("type")
    dependency_mapping = mapping.get("dependency")
    carry_count = mapping.get("carry_count")
    carry_reasons = mapping.get("carry_reasons")
    if (
        not isinstance(body_hash, str)
        or not isinstance(target_repo, str)
        or feedback_type not in ("plan-impl", "normal")
        or not isinstance(dependency_mapping, dict)
        or not isinstance(carry_count, int)
        or isinstance(carry_count, bool)
        or carry_count < 0
        or not isinstance(carry_reasons, list)
        or any(reason not in ("dependency-unmet", "limit-exceeded", "conflict") for reason in carry_reasons)
        or carry_count != len(carry_reasons)
    ):
        return None
    dependency = _mapping_to_dependency(dependency_mapping)
    if dependency is None:
        return None
    plan_file = mapping.get("plan_file")
    target_files = mapping.get("target_files", [])
    if feedback_type == "plan-impl":
        if not isinstance(plan_file, str) or not pathlib.PurePath(plan_file).is_absolute():
            return None
        normalized_files: tuple[str, ...] = ()
    else:
        if plan_file is not None or not isinstance(target_files, list):
            return None
        normalized = _normalize_target_files(target_files)
        if normalized is None:
            return None
        normalized_files = normalized
    # 既存キューのエントリは当該キーを持たないため、欠落を許容して再分類の対象にしない。
    last_run_id = mapping.get("last_deferral_run_id")
    if last_run_id is not None and (not isinstance(last_run_id, str) or not last_run_id):
        return None
    typed_feedback_type = typing.cast(FeedbackKind, feedback_type)
    typed_reasons = typing.cast(tuple[DeferralReason, ...], tuple(carry_reasons))
    return ScheduleMetadata(
        body_sha256=body_hash,
        normalized_target_repo=target_repo,
        feedback_type=typed_feedback_type,
        dependency=dependency,
        plan_file=plan_file if isinstance(plan_file, str) else None,
        target_files=normalized_files,
        carry_count=carry_count,
        carry_reasons=typed_reasons,
        last_deferral_run_id=last_run_id,
    )


def classification_from_mapping(mapping: typing.Any) -> Classification | None:
    """交換用JSONの1要素を検証して分類結果へ変換する。

    型ごとの必須キー・禁止キーを厳密に検証し、欠損・混入があれば拒否する。
    """
    if not isinstance(mapping, dict):
        return None
    filename = mapping.get("filename")
    source_hash = mapping.get("source_body_sha256")
    feedback_type = mapping.get("type")
    dependency_mapping = mapping.get("dependency")
    if (
        not isinstance(filename, str)
        or not isinstance(source_hash, str)
        or feedback_type != "normal"
        or not isinstance(dependency_mapping, dict)
    ):
        return None
    dependency = _mapping_to_dependency(dependency_mapping)
    if dependency is None:
        return None
    plan_file = mapping.get("plan_file")
    raw_target_files = mapping.get("target_files")

    if plan_file is not None or raw_target_files is None or not isinstance(raw_target_files, list):
        return None
    normalized = _normalize_target_files(raw_target_files)
    if normalized is None:
        return None
    return Classification(
        filename,
        source_hash,
        "normal",
        dependency,
        None,
        normalized,
    )


def apply_classifications(
    entries: tuple[QueueEntry, ...],
    terminal_entries: tuple[QueueEntry, ...],
    classifications: tuple[Classification, ...],
) -> tuple[QueueEntry, ...]:
    """分類結果を本文が一致する未分類エントリへ適用する。

    既に分類済み（`metadata`が非None）の項目への上書きは、対象リポジトリ変更に伴う
    再分類、または現時点で依存先消失・自己依存・依存循環と診断される項目に対する
    外部・ユーザー依存（TBD filename付き）への補正だけを受理する。
    後者は`entries`依存であれば無条件に任意の内容へ上書きできてしまうと、計画
    `### 機械スケジューリング`が定める「自己依存・循環・依存先消失の3種のTBD filename付き
    補正だけを受理する」契約を超えて通常の未成立依存まで書き換え可能になるため、
    診断対象filenameへ限定し、`feedback_type`・`plan_file`・`target_files`は既存値から
    変更しないことも要求する。
    """
    if any(classification.feedback_type == "plan-impl" for classification in classifications):
        raise ValueError("分類結果JSONでは計画実装型を指定できません")
    # `calculate_schedule`と同一の抽出条件（frontmatter破損除外、未回答TBD除外、
    # metadata欠損除外）で`candidates`・`typed`を導出する。抽出条件がずれると、
    # 本体の`calculate_schedule`が実際に下す診断と本関数の診断が食い違い、
    # 受理すべき補正を誤って拒否し得るため、単一実装（`_candidates_and_typed`）を共有する
    candidates, typed_for_diagnosis = _candidates_and_typed(entries)
    currently_broken = {
        item.filename
        for item in _missing_dependency_tbds(typed_for_diagnosis, candidates, terminal_entries)
        if item.reason in ("self", "missing", "cycle")
    }
    by_filename = {classification.filename: classification for classification in classifications}
    updated: list[QueueEntry] = []
    for entry in entries:
        classification = by_filename.get(entry.filename)
        if classification is None or classification.source_body_sha256 != body_sha256(entry.text):
            updated.append(entry)
            continue
        current = entry.metadata
        target_repo = _target_repo(entry.text)
        # 対象リポジトリ不一致は上書きを許可
        repo_changed = current is not None and current.normalized_target_repo != target_repo
        # 依存補正は、現時点で自己依存・循環・依存先消失と診断される項目に限り、
        # 外部・ユーザー依存への変更かつ他フィールドが既存値と一致する場合だけ許可する
        dependency_corrective = (
            current is not None
            and current.dependency.kind == "entries"
            and entry.filename in currently_broken
            and classification.dependency.kind == "external-user"
            and classification.dependency.tbd_filename is not None
            and classification.feedback_type == current.feedback_type
            and classification.plan_file == current.plan_file
            and classification.target_files == current.target_files
        )
        if current is not None and not repo_changed and not dependency_corrective:
            # 既存分類を保持（上書きしない）
            updated.append(entry)
            continue
        metadata = ScheduleMetadata(
            body_sha256=classification.source_body_sha256,
            normalized_target_repo=target_repo,
            feedback_type=classification.feedback_type,
            dependency=classification.dependency,
            plan_file=classification.plan_file,
            target_files=classification.target_files,
            carry_count=current.carry_count if current is not None else 0,
            carry_reasons=current.carry_reasons if current is not None else (),
        )
        updated.append(dataclasses.replace(entry, metadata=metadata))
    return tuple(updated)


def parse_plan_target_files(plan_text: str) -> tuple[str, ...]:
    """計画ファイルの対象ファイル一覧から相対POSIXパスを抽出する。"""
    match = re.search(r"^### 対象ファイル一覧\s*$([\s\S]*?)(?=^### |\Z)", plan_text, re.MULTILINE)
    if match is None:
        return ()
    found: list[str] = []
    for value in re.findall(r"^- \[[ xX]\] `([^`]+)`", match.group(1), re.MULTILINE):
        normalized = _normalize_target_file(value)
        if normalized is not None and normalized not in found:
            found.append(normalized)
    return tuple(found)


def regenerate_plan_metadata(entries: tuple[QueueEntry, ...]) -> tuple[QueueEntry, ...]:
    """独立キー`plan_file`から計画実装型の分類メタデータを再生成する。"""
    regenerated: list[QueueEntry] = []
    for entry in entries:
        if entry.plan_file is None:
            regenerated.append(entry)
            continue
        current = entry.metadata
        metadata = ScheduleMetadata(
            body_sha256=body_sha256(entry.text),
            normalized_target_repo=_target_repo(entry.text),
            feedback_type="plan-impl",
            # 依存は再生成の対象外とする。独立キーから復元できない情報であり、
            # 毎回初期化すると計画実装型へ依存を設定できない。
            dependency=current.dependency if current is not None else Dependency(kind="none"),
            plan_file=entry.plan_file,
            target_files=(),
            carry_count=current.carry_count if current is not None else 0,
            carry_reasons=current.carry_reasons if current is not None else (),
            last_deferral_run_id=current.last_deferral_run_id if current is not None else None,
        )
        regenerated.append(dataclasses.replace(entry, metadata=metadata))
    return tuple(regenerated)


def _candidates_and_typed(active_entries: tuple[QueueEntry, ...]) -> tuple[tuple[QueueEntry, ...], tuple[QueueEntry, ...]]:
    """`active_entries`から依存診断用の`candidates`（存在確認用）・`typed`（グラフ頂点用）を導出する。

    `calculate_schedule`と`apply_classifications`の双方が同一の抽出条件
    （frontmatter破損除外、未回答TBD除外、metadata欠損除外）を共有するための単一実装。
    抽出条件が両者でずれると、依存先消失・自己依存・循環の診断結果が呼び出し元ごとに
    食い違い、`apply_classifications`が本来受理すべき補正を誤って拒否し得る。
    """
    candidates = tuple(entry for entry in active_entries if not entry.frontmatter_broken)
    schedulable = tuple(entry for entry in candidates if entry.kind != "tbd" or entry.tbd_answered is True)
    typed = tuple(entry for entry in schedulable if entry.metadata is not None)
    return candidates, typed


def calculate_schedule(
    active_entries: tuple[QueueEntry, ...],
    terminal_entries: tuple[QueueEntry, ...],
    plan_target_files: Mapping[str, tuple[str, ...]],
    existing_unanswered_repair_keys: frozenset[RepairKey],
    cross_repo_entries: Mapping[str, QueueEntry],
    now: datetime.datetime,
) -> ScheduleResult:
    """依存成立、優先度、上限、競合順、繰越理由を副作用なしで算出する。

    `cross_repo_entries`は別リポジトリ依存の充足判定に使う全リポジトリのエントリ、
    `now`は外部条件待ちの再評価時刻との比較に使う現在時刻とする。
    いずれも引数で受け取り、純関数性を保つ。
    """
    active_entries = regenerate_plan_metadata(active_entries)
    broken = tuple(sorted(entry.filename for entry in active_entries if entry.frontmatter_broken))
    broken_needs_tbd = tuple(
        filename for filename in broken if (filename, "frontmatter") not in existing_unanswered_repair_keys
    )
    candidates, typed = _candidates_and_typed(active_entries)
    missing_plan_files = tuple(
        sorted(
            entry.filename
            for entry in typed
            if entry.metadata is not None
            and entry.metadata.feedback_type == "plan-impl"
            and entry.metadata.plan_file not in plan_target_files
        )
    )
    missing_plan_file_names = set(missing_plan_files)
    missing_plan_files_need_tbd = tuple(
        filename for filename in missing_plan_files if (filename, "missing-plan-file") not in existing_unanswered_repair_keys
    )
    typed = tuple(entry for entry in typed if entry.filename not in missing_plan_file_names)
    schedulable = tuple(entry for entry in candidates if entry.kind != "tbd" or entry.tbd_answered is True)
    classification_required = tuple(
        sorted(
            entry.filename
            for entry in schedulable
            if entry.plan_file is None
            and (
                entry.metadata is None
                or entry.metadata.body_sha256 != body_sha256(entry.text)
                or entry.metadata.normalized_target_repo != _target_repo(entry.text)
            )
        )
    )
    if classification_required:
        return ScheduleResult(
            classification_required=classification_required,
            frontmatter_broken_filenames=broken,
            frontmatter_broken_needs_tbd_filenames=broken_needs_tbd,
            missing_plan_file_filenames=missing_plan_files,
            missing_plan_file_needs_tbd_filenames=missing_plan_files_need_tbd,
        )

    # 依存先の存在確認には未回答TBDを含む全active項目を使う
    missing = _missing_dependency_tbds(typed, candidates, terminal_entries)
    if missing:
        return ScheduleResult(
            missing_dependency_tbds=missing,
            frontmatter_broken_filenames=broken,
            frontmatter_broken_needs_tbd_filenames=broken_needs_tbd,
            missing_plan_file_filenames=missing_plan_files,
            missing_plan_file_needs_tbd_filenames=missing_plan_files_need_tbd,
        )

    all_entries = {entry.filename: entry for entry in (*active_entries, *terminal_entries)}
    eligible: list[QueueEntry] = []
    deferred: list[DeferredItem] = []
    suppressed: list[SuppressedItem] = []
    for entry in typed:
        assert entry.metadata is not None
        if _dependency_is_satisfied(entry, active_entries, all_entries, cross_repo_entries, now):
            eligible.append(entry)
        elif entry.metadata.dependency.kind == "external-upstream":
            # 再評価時刻が未到来の外部条件待ちは繰越の対象外とする。
            # 充足時期が外部に依存する項目へ繰越を積むと、スターベーション優先で
            # 選抜され続ける一方で毎回除外される状態になる。
            dependency = entry.metadata.dependency
            suppressed.append(
                SuppressedItem(
                    entry.filename,
                    dependency.recheck_after or "",
                    dependency.hold_reason or "",
                    dependency.condition or "",
                )
            )
        else:
            deferred.append(DeferredItem(entry.filename, "dependency-unmet"))

    eligible.sort(key=_priority_key)
    plans = [entry for entry in eligible if entry.metadata and entry.metadata.feedback_type == "plan-impl"]
    normals = [entry for entry in eligible if entry.metadata and entry.metadata.feedback_type == "normal"]
    selected_plans, overflow_plans = plans[:PLAN_LIMIT], plans[PLAN_LIMIT:]
    selected_normals, overflow_normals = normals[:NORMAL_LIMIT], normals[NORMAL_LIMIT:]
    deferred.extend(DeferredItem(entry.filename, "limit-exceeded") for entry in (*overflow_plans, *overflow_normals))
    answered_tbd_targets = _answered_tbd_target_files(active_entries)

    plan_files: set[str] = set()
    plan_targets_complete = True
    for entry in selected_plans:
        assert entry.metadata is not None
        if entry.metadata.plan_file is None:
            plan_targets_complete = False
            continue
        target_files = plan_target_files.get(entry.metadata.plan_file, ())
        if not target_files:
            plan_targets_complete = False
        plan_files.update(target_files)
    parallel: list[str] = []
    post_plan: list[str] = []
    for entry in selected_normals:
        assert entry.metadata is not None
        targets = set(answered_tbd_targets.get(entry.filename, entry.metadata.target_files))
        if not selected_plans or (plan_targets_complete and targets and targets.isdisjoint(plan_files)):
            parallel.append(entry.filename)
        else:
            post_plan.append(entry.filename)
    deferred.sort(key=lambda item: item.filename)
    suppressed.sort(key=lambda item: item.filename)
    return ScheduleResult(
        plan_items=tuple(entry.filename for entry in selected_plans),
        parallel_normal_items=tuple(parallel),
        post_plan_normal_items=tuple(post_plan),
        deferred=tuple(deferred),
        suppressed=tuple(suppressed),
        frontmatter_broken_filenames=broken,
        frontmatter_broken_needs_tbd_filenames=broken_needs_tbd,
        missing_plan_file_filenames=missing_plan_files,
        missing_plan_file_needs_tbd_filenames=missing_plan_files_need_tbd,
    )


def with_deferral(metadata: ScheduleMetadata, reason: DeferralReason, run_id: str | None = None) -> ScheduleMetadata:
    """繰越理由を1回追加した分類メタデータを返す。

    同一の`run_id`で既に加算済みの場合は加算しない。
    1つのセッションが選抜計算を複数回実行しても繰越が二重計上されないようにする。
    `run_id`が`None`の場合は従来どおり無条件に加算する。
    """
    if run_id is not None and metadata.last_deferral_run_id == run_id:
        return metadata
    return dataclasses.replace(
        metadata,
        carry_count=metadata.carry_count + 1,
        carry_reasons=(*metadata.carry_reasons, reason),
        last_deferral_run_id=run_id,
    )


def format_schedule_label(text: str) -> str:
    """一覧表示用の分類型と繰越回数を返す。"""
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        return "frontmatter-broken/carry=0"
    metadata = parse_schedule_metadata(text)
    plan_file = parsed[0].get("plan_file")
    if isinstance(plan_file, str) and pathlib.PurePath(plan_file).is_absolute():
        carry_count = metadata.carry_count if metadata is not None else 0
        return f"plan-impl/carry={carry_count}"
    if metadata is None:
        return "unclassified/carry=0"
    return f"{metadata.feedback_type}/carry={metadata.carry_count}"


def dependency_from_mapping(mapping: dict[str, typing.Any]) -> Dependency | None:
    """依存マッピングを検証してDependencyへ変換する。CLIの依存更新経路が使う公開名。"""
    return _mapping_to_dependency(mapping)


def _mapping_to_dependency(mapping: dict[str, typing.Any]) -> Dependency | None:
    kind = mapping.get("kind")
    if kind in ("none", "inbox-empty"):
        return Dependency(kind=typing.cast(DependencyKind, kind))
    if kind == "entries":
        filenames = mapping.get("filenames")
        if not isinstance(filenames, list) or not filenames or any(not isinstance(value, str) for value in filenames):
            return None
        typed_filenames = typing.cast(list[str], filenames)
        return Dependency(kind="entries", filenames=tuple(dict.fromkeys(typed_filenames)))
    if kind == "external-user":
        condition = mapping.get("condition")
        tbd_filename = mapping.get("tbd_filename")
        if not isinstance(condition, str) or not condition or not isinstance(tbd_filename, str) or not tbd_filename:
            return None
        return Dependency(kind="external-user", condition=condition, tbd_filename=tbd_filename)
    if kind == "external-upstream":
        condition = mapping.get("condition")
        recheck_after = mapping.get("recheck_after")
        hold_reason = mapping.get("hold_reason")
        if not isinstance(condition, str) or not condition:
            return None
        if not isinstance(recheck_after, str) or not recheck_after:
            return None
        if not isinstance(hold_reason, str) or not hold_reason:
            return None
        if _parse_recheck_after(recheck_after) is None:
            return None
        return Dependency(
            kind="external-upstream",
            condition=condition,
            recheck_after=recheck_after,
            hold_reason=hold_reason,
        )
    if kind == "external-repo-entry":
        filenames = mapping.get("filenames")
        target_repo = mapping.get("target_repo")
        if not isinstance(filenames, list) or not filenames or any(not isinstance(value, str) for value in filenames):
            return None
        if not isinstance(target_repo, str) or not target_repo:
            return None
        typed_filenames = typing.cast(list[str], filenames)
        return Dependency(
            kind="external-repo-entry",
            filenames=tuple(dict.fromkeys(typed_filenames)),
            target_repo=target_repo,
        )
    return None


def _parse_recheck_after(value: str | None) -> datetime.datetime | None:
    """再評価時刻をawareな日時として解析する。解析できない値はNoneを返す。

    タイムゾーン情報を必須とする。比較対象の現在時刻がawareであり、
    naiveな日時との比較は例外になる。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _normalize_target_files(values: list[typing.Any]) -> tuple[str, ...] | None:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            return None
        path = _normalize_target_file(value)
        if path is None:
            return None
        if path not in normalized:
            normalized.append(path)
    return tuple(normalized)


def _normalize_target_file(value: str) -> str | None:
    path = pathlib.PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.as_posix()


def _target_repo(text: str) -> str:
    parsed = _frontmatter.parse_frontmatter(text)
    if parsed is None:
        return "(unknown)"
    value = parsed[0].get("target_repo")
    return value if isinstance(value, str) else "(unknown)"


def _priority_key(entry: QueueEntry) -> tuple[int, str]:
    assert entry.metadata is not None
    return (0 if entry.metadata.carry_count >= STARVATION_THRESHOLD else 1, entry.filename)


def _answered_tbd_target_files(active_entries: tuple[QueueEntry, ...]) -> dict[str, tuple[str, ...]]:
    """回答済みTBDの対象ファイルを現在の参照元から導出する。"""
    by_filename = {entry.filename: entry for entry in active_entries}
    referenced: dict[str, list[str]] = {}
    for entry in active_entries:
        metadata = entry.metadata
        if metadata is None or metadata.dependency.kind != "external-user":
            continue
        tbd_filename = metadata.dependency.tbd_filename
        if tbd_filename is not None:
            referenced.setdefault(tbd_filename, []).extend(metadata.target_files)

    result: dict[str, tuple[str, ...]] = {}
    for entry in active_entries:
        if entry.kind != "tbd" or entry.tbd_answered is not True or entry.metadata is None:
            continue
        target_files = referenced.get(entry.filename)
        if target_files is None and entry.repair_target_filename is not None:
            repair_target = by_filename.get(entry.repair_target_filename)
            if repair_target is not None and repair_target.metadata is not None:
                target_files = list(repair_target.metadata.target_files)
        result[entry.filename] = tuple(dict.fromkeys(target_files or ()))
    return result


def _missing_dependency_tbds(
    typed_entries: tuple[QueueEntry, ...],
    all_active_entries: tuple[QueueEntry, ...],
    terminal_entries: tuple[QueueEntry, ...],
) -> tuple[MissingDependencyTbd, ...]:
    """恒常的な依存不成立（消失・自己依存・循環）を診断する。

    引数：
    - typed_entries: 分類済みアクティブエントリ（グラフの頂点と対象）
    - all_active_entries: 分類済み・未回答TBD含む全アクティブエントリ（存在確認用）
    - terminal_entries: 完了状態エントリ（依存先の全体像構築用）

    依存先の存在確認には未回答TBDを含む全active項目を使い、循環解析の頂点は
    分類済み項目だけに限定する。
    """
    all_active_names = {entry.filename for entry in all_active_entries}
    all_names = all_active_names | {entry.filename for entry in terminal_entries}
    graph: dict[str, tuple[str, ...]] = {}
    missing: list[MissingDependencyTbd] = []
    for entry in typed_entries:
        assert entry.metadata is not None
        dependency = entry.metadata.dependency
        if dependency.kind != "entries":
            continue
        # グラフの構築には分類済みエントリだけを使う（循環検出用）
        graph[entry.filename] = tuple(name for name in dependency.filenames if name in {e.filename for e in typed_entries})
        if entry.filename in dependency.filenames:
            missing.append(MissingDependencyTbd(entry.filename, "self", (entry.filename,)))
        # 依存先の存在確認には未回答TBDを含む全active項目を使う
        absent = tuple(name for name in dependency.filenames if name not in all_names)
        if absent:
            missing.append(MissingDependencyTbd(entry.filename, "missing", absent))
    node_cycles = _cycle_members(graph)
    # 各ノードには自身が所属する循環だけを設定する
    for filename, cycle in node_cycles.items():
        if not any(item.filename == filename and item.reason == "self" for item in missing):
            missing.append(MissingDependencyTbd(filename, "cycle", tuple(sorted(cycle))))
    missing.sort(key=lambda item: (item.filename, item.reason))
    return tuple(missing)


def _cycle_members(graph: Mapping[str, tuple[str, ...]]) -> dict[str, set[str]]:
    """各ノードについて、そのノードが所属する強連結成分を返す。

    グラフ内の全ノードを走査し、Tarjanのアルゴリズムを使って各ノードが所属する
    強連結成分（循環）を特定する。独立した複数の循環がある場合、各ノードには
    自身が所属する循環だけが記録される。
    """
    node_cycles: dict[str, set[str]] = {}
    index_map: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    index_counter = [0]

    def strongconnect(node: str) -> None:
        """Tarjanのアルゴリズムで強連結成分を検出する。"""
        index_map[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)

        for dependency in graph.get(node, ()):
            if dependency not in index_map:
                strongconnect(dependency)
                lowlink[node] = min(lowlink[node], lowlink[dependency])
            elif dependency in on_stack:
                lowlink[node] = min(lowlink[node], index_map[dependency])

        # ノードが強連結成分の根である場合
        if lowlink[node] == index_map[node]:
            component: set[str] = set()
            while True:
                successor = stack.pop()
                on_stack.remove(successor)
                component.add(successor)
                if successor == node:
                    break
            # 2ノード以上の循環のみを記録
            if len(component) > 1:
                for member in component:
                    node_cycles[member] = component

    for node in graph:
        if node not in index_map:
            strongconnect(node)

    return node_cycles


def _dependency_is_satisfied(
    entry: QueueEntry,
    active_entries: tuple[QueueEntry, ...],
    all_entries: Mapping[str, QueueEntry],
    cross_repo_entries: Mapping[str, QueueEntry],
    now: datetime.datetime,
) -> bool:
    """依存の成立可否を返す。全種別を明示分岐で扱い、未知の種別は不成立とする。"""
    assert entry.metadata is not None
    dependency = entry.metadata.dependency
    if dependency.kind == "none":
        return True
    if dependency.kind == "entries":
        active_names = {candidate.filename for candidate in active_entries}
        return all(filename not in active_names and filename in all_entries for filename in dependency.filenames)
    if dependency.kind == "external-user":
        target = all_entries.get(dependency.tbd_filename or "")
        return target is not None and target.kind == "tbd" and target.tbd_answered is True
    if dependency.kind == "external-upstream":
        # 再評価時刻の到来だけを成立条件とする。到来後は通常の選抜対象へ戻り、
        # 条件が未充足であれば当該セッションが改めて繰越または再設定を行う。
        parsed = _parse_recheck_after(dependency.recheck_after)
        return parsed is not None and parsed <= now
    if dependency.kind == "external-repo-entry":
        # 依存先リポジトリのエントリが終端状態にあることを成立条件とする。
        # 依存先が未登録の場合は不成立とし、消失の診断は対象リポジトリ内の依存だけを扱う。
        return all(
            (target := cross_repo_entries.get(filename)) is not None
            and target.normalized_target_repo == dependency.target_repo
            and target.state in _TERMINAL_STATES
            for filename in dependency.filenames
        )
    if dependency.kind == "inbox-empty":
        return not any(candidate.filename != entry.filename for candidate in active_entries)
    return False
