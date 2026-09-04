"""フィードバックキューの実行可否判定エンジン。"""

import dataclasses
import datetime
import pathlib
import sys
from collections.abc import Iterable, Iterator
from typing import Literal

import _git_remote
import _plan_file
from _atk_mq_formatters import _parse_target_repo
from _atk_mq_frontmatter import parse_frontmatter
from _tbd_scan import _ACTIVE_STATES as MQ_PROCESSABLE_STATES
from _tbd_scan import _TBD_TYPE as MQ_TYPE_TBD
from _tbd_scan import is_tbd_answered as _is_tbd_answered

MQ_STATE_ADOPTED = "adopted"
MQ_STATE_REJECTED = "rejected"
MQ_TYPE_FEEDBACK = "feedback"
MQ_TYPES = (MQ_TYPE_FEEDBACK, MQ_TYPE_TBD)

type RepairKind = Literal["frontmatter", "missing-plan-file"]


def _plan_file_exists(value: str) -> bool:
    """保存済みplan_fileを解決して実在通常ファイルか判定する。"""
    try:
        return _plan_file.resolve_plan_file(value).is_file()
    except (OSError, ValueError):
        return False


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """着手可否判定に必要なキュー項目の現行状態。"""

    filename: str
    text: str
    kind: str | None
    target_repo: str | None
    tbd_answered: bool | None
    frontmatter_broken: bool
    plan_file: str | None
    cooldown_present: bool
    cooldown_until: object | None
    legacy_dependency: dict[str, object] | None
    repair_target_filename: str | None
    repair_kind: RepairKind | None


@dataclasses.dataclass(frozen=True)
class ReadinessResult:
    """対象リポジトリの`active`項目に対する着手可否と修復診断。"""

    ready: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    frontmatter_broken: tuple[str, ...] = ()
    frontmatter_broken_needs_tbd: tuple[str, ...] = ()
    missing_plan_file: tuple[str, ...] = ()
    missing_plan_file_needs_tbd: tuple[str, ...] = ()
    invalid_dependencies: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()
    self_dependencies: tuple[str, ...] = ()
    cyclic_dependencies: tuple[str, ...] = ()
    cooldown_pending: tuple[str, ...] = ()
    invalid_cooldowns: tuple[str, ...] = ()
    cooldown_values: tuple[tuple[str, str], ...] = ()

    @property
    def actionable_count(self) -> int:
        """処理セッションを起動すべき項目数を返す。"""
        repair_targets = {
            *self.frontmatter_broken_needs_tbd,
            *self.missing_plan_file_needs_tbd,
            *self.invalid_dependencies,
            *self.missing_dependencies,
            *self.self_dependencies,
            *self.cyclic_dependencies,
            *self.invalid_cooldowns,
        }
        return len(set(self.ready) | repair_targets)


_SPACE_SEPARATED_OPTION_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "mq": frozenset(("adopt", "reject", "rm")),
}
_SPACE_SEPARATED_OPTIONS = frozenset(("--note", "--commit"))


def _require_type(path: pathlib.Path, text: str) -> str | None:
    """エントリの種別を検証して返す。"""
    parsed = parse_frontmatter(text)
    if parsed is None:
        return None
    value = parsed[0].get("type")
    entry_type = value if isinstance(value, str) and value else None
    if entry_type not in MQ_TYPES:
        print(f"frontmatterのtypeが不正または欠落しています（feedback・tbdのいずれかが必要）: {path}", file=sys.stderr)
        raise SystemExit(2)
    return entry_type


def _iter_entries(
    private_notes: pathlib.Path,
    states: Iterable[str],
    filter_repo: str | None,
) -> Iterator[tuple[pathlib.Path, str, str, str, str | None]]:
    """指定状態のエントリを着手可否判定用に列挙する。"""
    for state in states:
        state_dir = private_notes / state
        if not state_dir.exists():
            continue
        for path in sorted(state_dir.iterdir()):
            if path.suffix != ".md":
                continue
            text = path.read_text(encoding="utf-8")
            entry_repo = _parse_target_repo(text)
            entry_type = _require_type(path, text)
            if filter_repo is not None and entry_type is not None and entry_repo != filter_repo:
                continue
            yield path, entry_repo, text, state, entry_type


def _normalized_repo_or_none(
    value: str | None,
    resolver_cache: dict[str, str | None] | None = None,
) -> str | None:
    """対象リポジトリを正規化する。解析できない値はNoneを返す。

    frontmatterが破損したエントリや対象リポジトリ未設定のエントリが1件でもあると
    キュー全体の読み込みが失敗するため、当該エントリだけを依存判定の対象外にする。
    """
    if not value:
        return None
    if resolver_cache is None:
        resolver_cache = {}
    if value not in resolver_cache:
        resolver_cache[value] = _git_remote.resolve_repo_identifier(value)
    return resolver_cache[value]


def _count_pending_entries(
    private_notes: pathlib.Path,
    target_repo: str | None = None,
) -> int:
    """当該対象リポジトリでこのセッションが着手可能な項目数を返す。"""
    return calculate_readiness(private_notes, target_repo).actionable_count


def _load_queue_entries(
    private_notes: pathlib.Path,
    target_repo: str | None,
    states: tuple[str, ...],
    resolver_cache: dict[str, str | None] | None = None,
) -> tuple[QueueEntry, ...]:
    """指定状態のフィードバック・TBDを着手可否判定用表現へ変換する。"""
    return tuple(
        _queue_entry(path, entry_repo, text, entry_type, resolver_cache)
        for path, entry_repo, text, _state, entry_type in _iter_entries(private_notes, states, target_repo)
    )


def _queue_entry(
    path: pathlib.Path,
    entry_repo: str,
    text: str,
    entry_type: str | None,
    resolver_cache: dict[str, str | None] | None = None,
) -> QueueEntry:
    """1件のキュー項目を着手可否判定用表現へ変換する。"""
    parsed = parse_frontmatter(text)
    frontmatter_broken = parsed is None
    data = parsed[0] if parsed is not None else {}
    repair_target = data.get("repair_target") if entry_type == MQ_TYPE_TBD else None
    raw_repair_kind = data.get("repair_kind") if entry_type == MQ_TYPE_TBD else None
    repair_kind: RepairKind | None
    if not isinstance(repair_target, str):
        repair_kind = None
    elif raw_repair_kind is None or raw_repair_kind == "frontmatter":
        repair_kind = "frontmatter"
    elif raw_repair_kind == "missing-plan-file":
        repair_kind = "missing-plan-file"
    else:
        repair_kind = None
    plan_file = data.get("plan_file")
    schedule = data.get("queue_schedule")
    legacy_dependency = schedule.get("dependency") if isinstance(schedule, dict) else None
    return QueueEntry(
        filename=path.name,
        text=text,
        kind=entry_type,
        target_repo=_normalized_repo_or_none(entry_repo, resolver_cache),
        tbd_answered=_is_tbd_answered(text) if entry_type == MQ_TYPE_TBD else None,
        frontmatter_broken=frontmatter_broken,
        plan_file=plan_file if isinstance(plan_file, str) else None,
        cooldown_present="cooldown_until" in data,
        cooldown_until=data.get("cooldown_until"),
        legacy_dependency=legacy_dependency if isinstance(legacy_dependency, dict) else None,
        repair_target_filename=repair_target if isinstance(repair_target, str) else None,
        repair_kind=repair_kind,
    )


def _load_referenced_terminal_entries(
    private_notes: pathlib.Path,
    dependency_names: set[str],
    resolver_cache: dict[str, str | None] | None = None,
) -> tuple[QueueEntry, ...]:
    """active項目が安全なbasenameで参照する終端項目だけを読み込む。"""
    safe_names = {
        name
        for name in dependency_names
        if pathlib.Path(name).name == name and "/" not in name and "\\" not in name and name.endswith(".md")
    }
    entries: list[QueueEntry] = []
    for state in (MQ_STATE_ADOPTED, MQ_STATE_REJECTED):
        state_dir = private_notes / state
        for name in sorted(safe_names):
            path = state_dir / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            entry_type = _require_type(path, text)
            entries.append(_queue_entry(path, _parse_target_repo(text), text, entry_type, resolver_cache))
    return tuple(entries)


def _effective_dependencies(
    entry: QueueEntry,
    resolver_cache: dict[str, str | None],
) -> tuple[str, ...] | None:
    """トップレベル依存を返し、無い場合はlegacy依存を読み取る。"""
    parsed = parse_frontmatter(entry.text)
    if parsed is not None and "depends_on" in parsed[0]:
        raw = parsed[0]["depends_on"]
        if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
            return None
        return tuple(dict.fromkeys(value for value in raw if isinstance(value, str)))
    legacy = entry.legacy_dependency
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
        filename = legacy.get("tbd_filename")
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
    """トップレベルの`depends_on`が正本として存在するか返す。"""
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


def _parse_cooldown_until(value: object) -> datetime.datetime | None:
    """再処理抑制期限をaware datetimeとして返す。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


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
        filename = legacy.get("tbd_filename")
        if not isinstance(filename, str):
            return False
        target = next(
            (candidate for candidate in (*all_active, *terminal) if candidate.filename == filename),
            None,
        )
        return target is not None and target.kind == MQ_TYPE_TBD and target.tbd_answered is True
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


def calculate_readiness(
    private_notes: pathlib.Path,
    target_repo: str | None,
    *,
    now: datetime.datetime | None = None,
) -> ReadinessResult:
    """`active`全件と参照された終端項目から対象リポジトリの着手可否を算出する。"""
    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    if now.tzinfo is None:
        raise ValueError("nowはタイムゾーン付き日時で指定してください")
    now_utc = now.astimezone(datetime.UTC)
    resolver_cache: dict[str, str | None] = {}
    canonical_target = _normalized_repo_or_none(target_repo, resolver_cache)
    all_active = _load_queue_entries(private_notes, None, MQ_PROCESSABLE_STATES, resolver_cache)
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
    terminal = _load_referenced_terminal_entries(private_notes, dependency_names, resolver_cache)
    existing_repairs = {
        (entry.repair_target_filename, entry.repair_kind)
        for entry in all_active
        if entry.kind == MQ_TYPE_TBD and entry.tbd_answered is False
    }
    active_by_name = {entry.filename: entry for entry in all_active}
    terminal_names = {entry.filename for entry in terminal}
    cooldown_values: dict[str, str] = {}
    invalid_cooldowns: set[str] = set()
    cooldown_pending: set[str] = set()
    for entry in active:
        if entry.frontmatter_broken or not entry.cooldown_present:
            continue
        if entry.kind != MQ_TYPE_FEEDBACK:
            invalid_cooldowns.add(entry.filename)
            continue
        parsed_cooldown = _parse_cooldown_until(entry.cooldown_until)
        if parsed_cooldown is None:
            invalid_cooldowns.add(entry.filename)
        elif parsed_cooldown.astimezone(datetime.UTC) > now_utc:
            cooldown_pending.add(entry.filename)
            assert isinstance(entry.cooldown_until, str)
            cooldown_values[entry.filename] = entry.cooldown_until

    broken = tuple(sorted(entry.filename for entry in active if entry.frontmatter_broken))
    broken_needs_tbd = tuple(name for name in broken if (name, "frontmatter") not in existing_repairs)
    missing_plan = tuple(
        sorted(
            entry.filename
            for entry in active
            if entry.filename not in cooldown_pending and entry.plan_file is not None and not _plan_file_exists(entry.plan_file)
        )
    )
    missing_plan_needs_tbd = tuple(name for name in missing_plan if (name, "missing-plan-file") not in existing_repairs)

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
        filename = legacy.get("tbd_filename")
        target = all_entries_by_name.get(filename) if isinstance(filename, str) else None
        if target is not None and target.kind != MQ_TYPE_TBD:
            invalid_external_user_targets.add(entry.filename)
    invalid = tuple(
        sorted(
            ({name for name, dependencies in dependency_map.items() if dependencies is None} | invalid_external_user_targets)
            - cooldown_pending
        )
    )
    graph = {name: dependencies for name, dependencies in dependency_map.items() if dependencies is not None}
    self_dependencies = tuple(
        sorted(name for name, dependencies in graph.items() if name in dependencies and name not in cooldown_pending)
    )
    missing_dependencies = tuple(
        sorted(
            name
            for name, dependencies in graph.items()
            if name not in cooldown_pending
            and any(dependency not in active_by_name and dependency not in terminal_names for dependency in dependencies)
        )
    )
    all_graph = {name: dependencies for name, dependencies in all_dependency_map.items() if dependencies is not None}
    cyclic = tuple(sorted((set(_cycle_members(all_graph)) & set(dependency_map)) - cooldown_pending))
    permanently_blocked = set(
        (*broken, *missing_plan, *invalid, *self_dependencies, *missing_dependencies, *cyclic, *invalid_cooldowns)
    )
    ready: list[str] = []
    blocked: list[str] = []
    for entry in active:
        if entry.filename in cooldown_pending:
            blocked.append(entry.filename)
            continue
        if entry.filename in permanently_blocked or (entry.kind == MQ_TYPE_TBD and entry.tbd_answered is False):
            blocked.append(entry.filename)
            continue
        dependencies = graph.get(entry.filename, ())
        legacy_satisfied = _legacy_dependency_is_satisfied(
            entry,
            all_active=all_active,
            terminal=terminal,
            target_active=active,
            now=now_utc,
            resolver_cache=resolver_cache,
        )
        if _has_explicit_dependencies(entry):
            waiting = any(dependency in active_by_name for dependency in dependencies)
        else:
            waiting = legacy_satisfied is False or any(
                dependency in active_by_name
                and not (active_by_name[dependency].kind == MQ_TYPE_TBD and active_by_name[dependency].tbd_answered is True)
                for dependency in dependencies
            )
        if waiting:
            blocked.append(entry.filename)
        else:
            ready.append(entry.filename)
    return ReadinessResult(
        ready=tuple(sorted(ready)),
        blocked=tuple(sorted(blocked)),
        frontmatter_broken=broken,
        frontmatter_broken_needs_tbd=broken_needs_tbd,
        missing_plan_file=missing_plan,
        missing_plan_file_needs_tbd=missing_plan_needs_tbd,
        invalid_dependencies=invalid,
        missing_dependencies=missing_dependencies,
        self_dependencies=self_dependencies,
        cyclic_dependencies=cyclic,
        cooldown_pending=tuple(sorted(cooldown_pending)),
        invalid_cooldowns=tuple(sorted(invalid_cooldowns)),
        cooldown_values=tuple(sorted(cooldown_values.items())),
    )
