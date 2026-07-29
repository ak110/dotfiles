"""メッセージキューの分類メタデータと機械スケジューリングを提供する。"""

import dataclasses
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

type DependencyKind = Literal["none", "entries", "inbox-empty", "external-user"]
type FeedbackKind = Literal["plan-impl", "normal"]
type FeedbackEntryKind = Literal["feedback", "tbd", "unknown"]
type DeferralReason = Literal["dependency-unmet", "limit-exceeded", "conflict"]


@dataclasses.dataclass(frozen=True)
class Dependency:
    """エントリの処理開始条件。"""

    kind: DependencyKind
    filenames: tuple[str, ...] = ()
    condition: str | None = None
    tbd_filename: str | None = None


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
    missing_dependency_tbds: tuple[MissingDependencyTbd, ...] = ()
    frontmatter_broken_filenames: tuple[str, ...] = ()
    frontmatter_broken_needs_tbd_filenames: tuple[str, ...] = ()


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
        or feedback_type not in ("plan-impl", "normal")
        or not isinstance(dependency_mapping, dict)
    ):
        return None
    dependency = _mapping_to_dependency(dependency_mapping)
    if dependency is None:
        return None
    plan_file = mapping.get("plan_file")
    raw_target_files = mapping.get("target_files")

    if feedback_type == "plan-impl":
        # 計画実装型: plan_file必須、target_files禁止
        if not isinstance(plan_file, str) or not pathlib.PurePath(plan_file).is_absolute():
            return None
        if "target_files" in mapping:
            return None
        target_files: tuple[str, ...] = ()
    else:
        # 通常型: plan_file禁止、target_files必須
        if plan_file is not None:
            return None
        if raw_target_files is None:
            return None
        if not isinstance(raw_target_files, list):
            return None
        normalized = _normalize_target_files(raw_target_files)
        if normalized is None:
            return None
        target_files = normalized
    return Classification(
        filename,
        source_hash,
        typing.cast(FeedbackKind, feedback_type),
        dependency,
        plan_file if feedback_type == "plan-impl" else None,
        target_files,
    )


def apply_classifications(
    entries: tuple[QueueEntry, ...],
    terminal_entries: tuple[QueueEntry, ...],
    classifications: tuple[Classification, ...],
    plan_target_files: Mapping[str, tuple[str, ...]],
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
        if classification.feedback_type == "plan-impl" and classification.plan_file not in plan_target_files:
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


def detect_plan_impl_reference(body: str) -> str | None:
    """本文が言及する実在計画ファイルの絶対パスを返す。"""
    for candidate in re.findall(r"(?<![\w./-])(/[^\s`\"'<>]+\.md)(?![\w.-])", body):
        path = pathlib.Path(candidate)
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return None


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
    existing_unanswered_repair_target_filenames: frozenset[str],
) -> ScheduleResult:
    """依存成立、優先度、上限、競合順、繰越理由を副作用なしで算出する。"""
    broken = tuple(sorted(entry.filename for entry in active_entries if entry.frontmatter_broken))
    broken_needs_tbd = tuple(filename for filename in broken if filename not in existing_unanswered_repair_target_filenames)
    candidates, typed = _candidates_and_typed(active_entries)
    schedulable = tuple(entry for entry in candidates if entry.kind != "tbd" or entry.tbd_answered is True)
    classification_required = tuple(
        sorted(
            entry.filename
            for entry in schedulable
            if entry.metadata is None
            or entry.metadata.body_sha256 != body_sha256(entry.text)
            or entry.metadata.normalized_target_repo != _target_repo(entry.text)
        )
    )
    if classification_required:
        return ScheduleResult(
            classification_required=classification_required,
            frontmatter_broken_filenames=broken,
            frontmatter_broken_needs_tbd_filenames=broken_needs_tbd,
        )

    # 依存先の存在確認には未回答TBDを含む全active項目を使う
    missing = _missing_dependency_tbds(typed, candidates, terminal_entries)
    if missing:
        return ScheduleResult(
            missing_dependency_tbds=missing,
            frontmatter_broken_filenames=broken,
            frontmatter_broken_needs_tbd_filenames=broken_needs_tbd,
        )

    all_entries = {entry.filename: entry for entry in (*active_entries, *terminal_entries)}
    eligible: list[QueueEntry] = []
    deferred: list[DeferredItem] = []
    for entry in typed:
        assert entry.metadata is not None
        if _dependency_is_satisfied(entry, active_entries, all_entries):
            eligible.append(entry)
        else:
            deferred.append(DeferredItem(entry.filename, "dependency-unmet"))

    eligible.sort(key=_priority_key)
    plans = [entry for entry in eligible if entry.metadata and entry.metadata.feedback_type == "plan-impl"]
    normals = [entry for entry in eligible if entry.metadata and entry.metadata.feedback_type == "normal"]
    selected_plans, overflow_plans = plans[:PLAN_LIMIT], plans[PLAN_LIMIT:]
    selected_normals, overflow_normals = normals[:NORMAL_LIMIT], normals[NORMAL_LIMIT:]
    deferred.extend(DeferredItem(entry.filename, "limit-exceeded") for entry in (*overflow_plans, *overflow_normals))

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
        targets = set(entry.metadata.target_files)
        if not selected_plans or (plan_targets_complete and targets and targets.isdisjoint(plan_files)):
            parallel.append(entry.filename)
        else:
            post_plan.append(entry.filename)
    deferred.sort(key=lambda item: item.filename)
    return ScheduleResult(
        plan_items=tuple(entry.filename for entry in selected_plans),
        parallel_normal_items=tuple(parallel),
        post_plan_normal_items=tuple(post_plan),
        deferred=tuple(deferred),
        frontmatter_broken_filenames=broken,
        frontmatter_broken_needs_tbd_filenames=broken_needs_tbd,
    )


def with_deferral(metadata: ScheduleMetadata, reason: DeferralReason) -> ScheduleMetadata:
    """繰越理由を1回追加した分類メタデータを返す。"""
    return dataclasses.replace(
        metadata,
        carry_count=metadata.carry_count + 1,
        carry_reasons=(*metadata.carry_reasons, reason),
    )


def format_schedule_label(text: str) -> str:
    """一覧表示用の分類型と繰越回数を返す。"""
    if _frontmatter.parse_frontmatter(text) is None:
        return "frontmatter-broken/carry=0"
    metadata = parse_schedule_metadata(text)
    if metadata is None:
        return "unclassified/carry=0"
    return f"{metadata.feedback_type}/carry={metadata.carry_count}"


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
    return None


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
) -> bool:
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
    return not any(candidate.filename != entry.filename for candidate in active_entries)
