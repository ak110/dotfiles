"""WIキューの実行可否判定エンジン。"""

import dataclasses
import datetime
import pathlib
from collections.abc import Iterable
from typing import Literal

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi.constants import (
    CONFIRMATION_CONFIRMED,
    CONFIRMATION_KEY,
    CONFIRMATION_REQUIRED,
    WI_PROCESSABLE_STATES,
    WI_STATE_ADOPTED,
    WI_STATE_PROCESSING,
    WI_STATE_REJECTED,
    WI_STATES,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
    WI_TYPES,
    normalized_wi_type,
    unrepairable_entry_next_action,
)
from agent_toolkit._atk.wi.entry_text import EntryText, EntryTexts, parse_frontmatter
from agent_toolkit._atk.wi.uwi_scan import is_uwi_answered as _is_uwi_answered
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._git import remote as _git_remote

type RepairKind = Literal["frontmatter"]


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """着手可否判定に必要なキュー項目の現行状態。"""

    filename: str
    text: str
    kind: str | None
    target_repo: str | None
    uwi_answered: bool | None
    frontmatter_broken: bool
    cooldown_present: bool
    cooldown_until: object | None
    legacy_dependency: dict[str, object] | None
    repair_target_filename: str | None
    repair_kind: RepairKind | None
    source: str | None = None
    confirmation: str | None = None


@dataclasses.dataclass(frozen=True)
class ReadinessResult:
    """対象リポジトリの`active`項目に対する着手可否と修復診断。

    `internal_dependency_waits`は、未終了の依存先を1件以上持ち、その全てが引数`target_repo`の
    `processable`集合の内側にある項目を保持する。
    """

    ready: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    internal_dependency_waits: tuple[str, ...] = ()
    frontmatter_broken: tuple[str, ...] = ()
    frontmatter_broken_needs_uwi: tuple[str, ...] = ()
    invalid_dependencies: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()
    self_dependencies: tuple[str, ...] = ()
    cyclic_dependencies: tuple[str, ...] = ()
    cooldown_pending: tuple[str, ...] = ()
    invalid_cooldowns: tuple[str, ...] = ()
    cooldown_values: tuple[tuple[str, str], ...] = ()
    confirmation_pending: tuple[str, ...] = ()

    @property
    def actionable_count(self) -> int:
        """処理セッションを起動すべき項目数を返す。"""
        repair_targets = {
            *self.frontmatter_broken_needs_uwi,
            *self.invalid_dependencies,
            *self.missing_dependencies,
            *self.self_dependencies,
            *self.cyclic_dependencies,
            *self.invalid_cooldowns,
        }
        return len(set(self.ready) | repair_targets)


def _require_type(path: pathlib.Path, text: str) -> str | None:
    """エントリの種別を検証して返す。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    entry_type = normalized_wi_type(parsed[0].get("type"))
    if entry_type is None:
        # 終了コード2で処理を打ち切るため、失敗行で返す。
        _outcome.report_failure(
            f"frontmatterのtypeが不正または欠落している（{'・'.join(WI_TYPES)}のいずれかが必要）: {path}",
            next_action=unrepairable_entry_next_action(path.name),
        )
        raise SystemExit(2)
    return entry_type


def _normalized_repo_or_none(
    value: str | None,
    resolver_cache: dict[str, str | None] | None = None,
) -> str | None:
    """対象リポジトリを正規化する。解析できない値はNoneを返す。

    frontmatterが破損したエントリや対象リポジトリ未設定のエントリが1件でもあると
    キュー全体の読み込みが失敗するため、そのエントリだけを依存判定の対象外にする。
    """
    if not value:
        return None
    if resolver_cache is None:
        resolver_cache = {}
    if value not in resolver_cache:
        resolver_cache[value] = _git_remote.resolve_repo_identifier(value)
    return resolver_cache[value]


def count_pending_entries(
    private_notes: pathlib.Path,
    target_repo: str | None = None,
) -> int:
    """指定された対象リポジトリでこのセッションが着手可能な項目数を返す。"""
    return calculate_readiness(private_notes, target_repo).actionable_count


def _queue_entry(
    path: pathlib.Path,
    entry_repo: str,
    text: str,
    entry_type: str | None,
    resolver_cache: dict[str, str | None] | None = None,
) -> QueueEntry:
    """1件のキュー項目を着手可否判定用表現へ変換する。"""
    text = text if isinstance(text, EntryText) else EntryText(text)
    parsed = parse_frontmatter(text)
    frontmatter_broken = parsed is None
    data = parsed[0] if parsed is not None else {}
    repair_target = data.get("repair_target") if entry_type == WI_TYPE_UWI else None
    raw_repair_kind = data.get("repair_kind") if entry_type == WI_TYPE_UWI else None
    repair_kind: RepairKind | None
    if not isinstance(repair_target, str):
        repair_kind = None
    elif raw_repair_kind is None or raw_repair_kind == "frontmatter":
        repair_kind = "frontmatter"
    else:
        repair_kind = None
    schedule = data.get("queue_schedule")
    legacy_dependency = schedule.get("dependency") if isinstance(schedule, dict) else None
    return QueueEntry(
        filename=path.name,
        text=text,
        kind=entry_type,
        target_repo=_normalized_repo_or_none(entry_repo, resolver_cache),
        uwi_answered=_is_uwi_answered(text) if entry_type == WI_TYPE_UWI else None,
        frontmatter_broken=frontmatter_broken,
        cooldown_present="cooldown_until" in data,
        cooldown_until=data.get("cooldown_until"),
        legacy_dependency=legacy_dependency if isinstance(legacy_dependency, dict) else None,
        repair_target_filename=repair_target if isinstance(repair_target, str) else None,
        repair_kind=repair_kind,
        source=data.get("source") if isinstance(data.get("source"), str) else None,
        confirmation=applicable_confirmation(entry_type, data),
    )


def _effective_dependencies(
    entry: QueueEntry,
    resolver_cache: dict[str, str | None],
) -> tuple[str, ...] | None:
    """トップレベル依存を返し、無い場合はlegacy依存を読み取る。"""
    parsed = parse_frontmatter(entry.text)
    return _dependency_names(parsed[0] if parsed is not None else {}, entry.legacy_dependency, resolver_cache)


def _dependency_names(
    data: dict[str, object],
    legacy: dict[str, object] | None,
    resolver_cache: dict[str, str | None],
) -> tuple[str, ...] | None:
    """解析済みfrontmatterから依存先のファイル名を返す。トップレベル依存が無い場合はlegacy依存を読む。"""
    if "depends_on" in data:
        raw = data["depends_on"]
        if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
            return None
        return tuple(dict.fromkeys(value for value in raw if isinstance(value, str)))
    if legacy is None or legacy.get("kind") in (None, "none"):
        return ()
    kind = legacy.get("kind")
    if kind in ("entries", "external-repo-entry"):
        filenames = legacy.get("filenames")
        if (
            not isinstance(filenames, list)
            or not filenames
            or any(not isinstance(value, str) or not value for value in filenames)
        ):
            return None
        if kind == "external-repo-entry":
            target_repo = legacy.get("target_repo")
            if not isinstance(target_repo, str) or _normalized_repo_or_none(target_repo, resolver_cache) is None:
                return None
        return tuple(dict.fromkeys(value for value in filenames if isinstance(value, str)))
    if kind == "external-user":
        filename = legacy.get("uwi_filename")
        condition = legacy.get("condition")
        return (filename,) if isinstance(filename, str) and filename and isinstance(condition, str) and condition else None
    if kind == "external-upstream":
        recheck_after = legacy.get("recheck_after")
        condition = legacy.get("condition")
        hold_reason = legacy.get("hold_reason")
        if (
            not all(isinstance(value, str) and value for value in (recheck_after, condition, hold_reason))
            or _parse_legacy_recheck_after(recheck_after) is None
        ):
            return None
        return ()
    if kind == "inbox-empty":
        return ()
    return None


def _has_explicit_dependencies(entry: QueueEntry) -> bool:
    """トップレベルに`depends_on`が保存されているかを返す。"""
    parsed = parse_frontmatter(entry.text)
    return parsed is not None and "depends_on" in parsed[0]


def _parse_legacy_recheck_after(value: object) -> datetime.datetime | None:
    """legacy外部条件の再評価時刻をaware datetimeとして返す。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def applicable_cooldown_until(kind: str | None, value: object) -> datetime.datetime | None:
    """保存された再処理抑制期限のうち、冷却期限として適用できる日時を返す。

    期限を適用するのはAWIだけとする。UWIはユーザーの判断を待つ項目であり、時間経過で解消する待機へ変えない。
    AWI以外の種別、空・解析不能・タイムゾーンなしの値では`None`を返す。
    着手可否判定と`atk serve`の画面用の期限はこの判定を共有し、日時の解析だけを再利用して種別の条件を省かない。
    """
    if kind != WI_TYPE_AWI or not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def applicable_confirmation(kind: str | None, metadata: dict[str, object]) -> str | None:
    """AWIのfrontmatterから内容確認の状態を返す。

    キーを持たない項目とAWI以外は`None`を返し、確認を待たない。
    `confirmed`以外の値は`required`として扱う。手書きの誤記でユーザーの確認前に自動着手しないためである。
    着手可否判定と`atk serve`の画面はこの判定を共有する。
    """
    if kind != WI_TYPE_AWI or CONFIRMATION_KEY not in metadata:
        return None
    return CONFIRMATION_CONFIRMED if metadata[CONFIRMATION_KEY] == CONFIRMATION_CONFIRMED else CONFIRMATION_REQUIRED


def _can_become_candidate(entry: QueueEntry) -> bool:
    """依存先がprocess-wiの同じ実行の候補へ入り得るかを返す。UWIは回答済みで`atk run-skill`由来でない場合だけ候補になる。"""
    if entry.kind != WI_TYPE_UWI:
        return True
    return entry.uwi_answered is True and entry.source != _automated_prompt.SOURCE_RUN_SKILL


def _internal_dependency_waits(
    wait_targets: dict[tuple[str, str | None], tuple[QueueEntry, ...]],
    ready_names: set[str],
    processing_names: set[str],
) -> list[str]:
    """依存先が全て同じ実行の候補へ入る項目だけを内部依存待ちとして返す。

    候補は着手可能な項目、処理中の項目、内部依存待ちの項目であり、未回答のUWIと`atk run-skill`のUWIは除く。
    依存先の候補外は推移的に伝わる（AがB、Bが未回答UWIを待つ場合、Bが候補外なのでAも候補外）ため、
    候補外の依存先を持つ項目が無くなるまで内部依存待ちの集合を縮める。
    候補外の依存先を待つ項目を内部依存待ちに数えると、回答前の依存元が実装対象へ投入される。
    """
    internal = set(wait_targets)

    def is_candidate(target: QueueEntry) -> bool:
        if not _can_become_candidate(target):
            return False
        return (
            target.filename in ready_names
            or target.filename in processing_names
            or (target.filename, target.target_repo) in internal
        )

    changed = True
    while changed:
        changed = False
        for key in tuple(internal):
            if not all(is_candidate(target) for target in wait_targets[key]):
                internal.discard(key)
                changed = True
    return [filename for filename, _repo in internal]


def unanswered_uwi_dependents(
    entries: Iterable[tuple[pathlib.Path, str | None, str | None, dict[str, object], str]],
) -> frozenset[pathlib.Path]:
    """依存先に未回答のUWIを持つAWIのパスを返す。

    WI画面は回答待ちを保存状態ではなくこの導出値で`blocked`と表示する。
    依存先の回答だけが変わった場合も正しく変わるよう、呼び出し側は項目ごとの表示キャッシュの外で求める。
    `entries`には未終了の項目（`inbox`・`processing`・`hold`）を、パス・frontmatterの`target_repo`の値・種別・
    解析済みのfrontmatter・本文の組で渡す。一覧の1操作で本文の解析と対象リポジトリの正規化を繰り返さないよう、
    解析済みの値を受け取る。依存先は、同名の項目のうち`target_repo`の値が依存元と一致するものを優先して決める。
    """
    records = tuple(entries)
    repos_by_name: dict[str, set[str | None]] = {}
    unanswered: dict[str, set[str | None]] = {}
    for path, target_repo, kind, _metadata, text in records:
        repos_by_name.setdefault(path.name, set()).add(target_repo)
        if kind == WI_TYPE_UWI and not _is_uwi_answered(text):
            unanswered.setdefault(path.name, set()).add(target_repo)
    resolver_cache: dict[str, str | None] = {}
    dependents: set[pathlib.Path] = set()
    for path, target_repo, kind, metadata, _text in records:
        if kind != WI_TYPE_AWI:
            continue
        schedule = metadata.get("queue_schedule")
        legacy = schedule.get("dependency") if isinstance(schedule, dict) else None
        names = _dependency_names(metadata, legacy if isinstance(legacy, dict) else None, resolver_cache) or ()
        for name in names:
            waiting = unanswered.get(name, set())
            # 依存元と同じ対象リポジトリの同名項目があればそれを依存先とし、無ければ同名の全項目を依存先とする。
            same_repo = target_repo in repos_by_name.get(name, set())
            if (target_repo in waiting) if same_repo else bool(waiting):
                dependents.add(path)
                break
    return frozenset(dependents)


def _legacy_dependency_is_satisfied(
    entry: QueueEntry,
    *,
    all_active: tuple[QueueEntry, ...],
    terminal: tuple[QueueEntry, ...],
    target_active: tuple[QueueEntry, ...],
    now: datetime.datetime,
    resolver_cache: dict[str, str | None],
) -> bool | None:
    """legacy固有依存の成立状態を返し、通常のファイル名依存では`None`を返す。"""
    parsed = parse_frontmatter(entry.text)
    if parsed is not None and "depends_on" in parsed[0]:
        return None
    legacy = entry.legacy_dependency
    if legacy is None:
        return None
    kind = legacy.get("kind")
    if kind in (None, "none", "entries"):
        return None
    if kind == "external-user":
        filename = legacy.get("uwi_filename")
        if not isinstance(filename, str):
            return False
        target = next(
            (candidate for candidate in (*all_active, *terminal) if candidate.filename == filename),
            None,
        )
        return target is not None and target.kind == WI_TYPE_UWI and target.uwi_answered is True
    if kind == "external-upstream":
        recheck_after = _parse_legacy_recheck_after(legacy.get("recheck_after"))
        return recheck_after is not None and recheck_after <= now
    if kind == "inbox-empty":
        return not any(candidate.filename != entry.filename for candidate in target_active)
    if kind == "external-repo-entry":
        filenames = legacy.get("filenames")
        target_repo = legacy.get("target_repo")
        dependency_repo = _normalized_repo_or_none(
            target_repo if isinstance(target_repo, str) else None,
            resolver_cache,
        )
        if not isinstance(filenames, list) or any(not isinstance(value, str) for value in filenames) or dependency_repo is None:
            return False
        terminal_pairs = {(candidate.filename, candidate.target_repo) for candidate in terminal}
        active_pairs = {(candidate.filename, candidate.target_repo) for candidate in all_active}
        return all(
            (filename, dependency_repo) in terminal_pairs and (filename, dependency_repo) not in active_pairs
            for filename in filenames
        )
    return False


def _cycle_members(graph: dict[str, tuple[str, ...]]) -> set[str]:
    """依存グラフ内で循環に属する全項目名を返す。"""
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            cyclic.update(path[path.index(node) :])
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, ()):
            if dependency in graph:
                visit(dependency, (*path, dependency))
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, (node,))
    return cyclic


def _entries_by_filename(entries: Iterable[QueueEntry]) -> dict[str, tuple[QueueEntry, ...]]:
    """ファイル名ごとのキュー項目を返す。対象リポジトリが異なる同名の項目は同じ値へ並べる。"""
    grouped: dict[str, list[QueueEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.filename, []).append(entry)
    return {filename: tuple(group) for filename, group in grouped.items()}


def _dependency_targets(
    entry: QueueEntry,
    dependency: str,
    entries_by_name: dict[str, tuple[QueueEntry, ...]],
) -> tuple[QueueEntry, ...]:
    """依存先のファイル名を、ファイル名と対象リポジトリの組で解決する。

    キュー項目のファイル名は投入時刻から生成するため、対象リポジトリをまたぐと同じ値になり得る。
    依存元と同じ対象リポジトリの候補があればそれだけを依存先とし、無い場合だけ同名の全候補を
    依存先として対象リポジトリを横断する依存を解決する。
    """
    candidates = entries_by_name.get(dependency, ())
    same_repo = tuple(candidate for candidate in candidates if candidate.target_repo == entry.target_repo)
    return same_repo or candidates


def calculate_readiness(
    private_notes: pathlib.Path,
    target_repo: str | None,
    *,
    now: datetime.datetime | None = None,
    snapshot: tuple[_wi_entries.EntryRecord, ...] | None = None,
    texts: EntryTexts | None = None,
) -> ReadinessResult:
    """`active`全件と参照された終了項目から対象リポジトリの着手可否を算出する。"""
    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    if now.tzinfo is None:
        raise ValueError("nowはタイムゾーン付き日時で指定してください")
    if snapshot is None:
        snapshot = tuple(_wi_entries.iter_entries(private_notes, WI_STATES, None, texts=texts))
    now_utc = now.astimezone(datetime.UTC)
    resolver_cache: dict[str, str | None] = {}
    canonical_target = _normalized_repo_or_none(target_repo, resolver_cache)
    all_active = tuple(
        _queue_entry(path, repo, text, kind, resolver_cache)
        for path, repo, text, state, kind in snapshot
        if state in WI_PROCESSABLE_STATES
    )
    processing_names = {path.name for path, _repo, _text, state, _kind in snapshot if state == WI_STATE_PROCESSING}
    active = (
        all_active
        if target_repo is None
        else tuple(
            entry
            for entry in all_active
            if entry.frontmatter_broken or (canonical_target is not None and entry.target_repo == canonical_target)
        )
    )
    all_dependency_map = {
        entry.filename: _effective_dependencies(entry, resolver_cache) for entry in all_active if not entry.frontmatter_broken
    }
    dependency_names = {
        dependency for dependencies in all_dependency_map.values() if dependencies is not None for dependency in dependencies
    }
    terminal = tuple(
        _queue_entry(path, repo, text, kind, resolver_cache)
        for path, repo, text, state, kind in snapshot
        if state in (WI_STATE_ADOPTED, WI_STATE_REJECTED) and path.name in dependency_names
    )
    existing_repairs = {
        (entry.repair_target_filename, entry.repair_kind)
        for entry in all_active
        if entry.kind == WI_TYPE_UWI and entry.uwi_answered is False
    }
    entries_by_name = _entries_by_filename((*all_active, *terminal))
    active_pairs = {(entry.filename, entry.target_repo) for entry in all_active}
    target_pairs = {(entry.filename, entry.target_repo) for entry in active}
    # 要確認の項目はユーザーが確認するまで時間経過でも修復でも着手しない。
    # 冷却中の項目と同じく、修復対象の判定からも除いて処理セッションの起動理由にしない。
    confirmation_pending = {
        entry.filename for entry in active if not entry.frontmatter_broken and entry.confirmation == CONFIRMATION_REQUIRED
    }
    cooldown_values: dict[str, str] = {}
    invalid_cooldowns: set[str] = set()
    cooldown_pending: set[str] = set()
    for entry in active:
        if entry.frontmatter_broken or not entry.cooldown_present or entry.filename in confirmation_pending:
            continue
        parsed_cooldown = applicable_cooldown_until(entry.kind, entry.cooldown_until)
        if parsed_cooldown is None:
            invalid_cooldowns.add(entry.filename)
        elif parsed_cooldown.astimezone(datetime.UTC) > now_utc:
            cooldown_pending.add(entry.filename)
            assert isinstance(entry.cooldown_until, str)
            cooldown_values[entry.filename] = entry.cooldown_until

    deferred = cooldown_pending | confirmation_pending
    broken = tuple(sorted(entry.filename for entry in active if entry.frontmatter_broken))
    broken_needs_uwi = tuple(name for name in broken if (name, "frontmatter") not in existing_repairs)

    dependency_map = {
        entry.filename: _effective_dependencies(entry, resolver_cache) for entry in active if not entry.frontmatter_broken
    }
    all_entries_by_name = {entry.filename: entry for entry in (*all_active, *terminal)}
    invalid_external_user_targets: set[str] = set()
    for entry in active:
        if _has_explicit_dependencies(entry):
            continue
        legacy = entry.legacy_dependency
        if legacy is None or legacy.get("kind") != "external-user":
            continue
        filename = legacy.get("uwi_filename")
        target = all_entries_by_name.get(filename) if isinstance(filename, str) else None
        if target is not None and target.kind != WI_TYPE_UWI:
            invalid_external_user_targets.add(entry.filename)
    invalid = tuple(
        sorted(
            ({name for name, dependencies in dependency_map.items() if dependencies is None} | invalid_external_user_targets)
            - deferred
        )
    )
    graph = {name: dependencies for name, dependencies in dependency_map.items() if dependencies is not None}
    self_dependencies = tuple(
        sorted(name for name, dependencies in graph.items() if name in dependencies and name not in deferred)
    )
    missing_dependencies = tuple(
        sorted(
            {
                entry.filename
                for entry in active
                if entry.filename in graph
                and entry.filename not in deferred
                and any(not _dependency_targets(entry, dependency, entries_by_name) for dependency in graph[entry.filename])
            }
        )
    )
    all_graph = {name: dependencies for name, dependencies in all_dependency_map.items() if dependencies is not None}
    cyclic = tuple(sorted((set(_cycle_members(all_graph)) & set(dependency_map)) - deferred))
    permanently_blocked = set((*broken, *invalid, *self_dependencies, *missing_dependencies, *cyclic, *invalid_cooldowns))
    ready: list[str] = []
    blocked: list[str] = []
    internal_wait_targets: dict[tuple[str, str | None], tuple[QueueEntry, ...]] = {}
    for entry in active:
        if entry.kind == WI_TYPE_UWI and entry.source == _automated_prompt.SOURCE_RUN_SKILL:
            # `atk run-skill`のUWIは同じ対象リポジトリと同じスキルの次回の実行が扱い、process-wiは選定から除く。
            # readyへ含めると、process-loopが選定で除かれるUWIのために子セッションの起動を繰り返す。
            continue
        if entry.filename in deferred:
            blocked.append(entry.filename)
            continue
        if entry.filename in permanently_blocked or (entry.kind == WI_TYPE_UWI and entry.uwi_answered is False):
            blocked.append(entry.filename)
            continue
        waiting_targets = tuple(
            target
            for dependency in graph.get(entry.filename, ())
            for target in _dependency_targets(entry, dependency, entries_by_name)
            if (target.filename, target.target_repo) in active_pairs
        )
        legacy_satisfied = _legacy_dependency_is_satisfied(
            entry,
            all_active=all_active,
            terminal=terminal,
            target_active=active,
            now=now_utc,
            resolver_cache=resolver_cache,
        )
        if _has_explicit_dependencies(entry):
            waiting = bool(waiting_targets)
        else:
            waiting = legacy_satisfied is False or any(
                not (target.kind == WI_TYPE_UWI and target.uwi_answered is True) for target in waiting_targets
            )
        if not waiting:
            ready.append(entry.filename)
            continue
        blocked.append(entry.filename)
        if waiting_targets and all((target.filename, target.target_repo) in target_pairs for target in waiting_targets):
            internal_wait_targets[(entry.filename, entry.target_repo)] = waiting_targets
    internal_waits = _internal_dependency_waits(internal_wait_targets, set(ready), processing_names)
    return ReadinessResult(
        ready=tuple(sorted(ready)),
        blocked=tuple(sorted(blocked)),
        internal_dependency_waits=tuple(sorted(internal_waits)),
        frontmatter_broken=broken,
        frontmatter_broken_needs_uwi=broken_needs_uwi,
        invalid_dependencies=invalid,
        missing_dependencies=missing_dependencies,
        self_dependencies=self_dependencies,
        cyclic_dependencies=cyclic,
        cooldown_pending=tuple(sorted(cooldown_pending)),
        invalid_cooldowns=tuple(sorted(invalid_cooldowns)),
        cooldown_values=tuple(sorted(cooldown_values.items())),
        confirmation_pending=tuple(sorted(confirmation_pending)),
    )
