from collections.abc import Iterable

from agent_toolkit._atk.wi import user_comment as _wi_user_comment
from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import WI_ACTIVE_STATES, WI_PROCESSABLE_STATES, WI_STATES, WI_TYPE_UWI
from agent_toolkit._atk.wi.entry_text import parse_frontmatter
from agent_toolkit._git import remote as _git_remote


def _values(value: str | Iterable[str]) -> tuple[str, ...]:
    """単一値の指定を複数値の指定へそろえる。"""
    return (value,) if isinstance(value, str) else tuple(value)


def resolve_states(statuses: str | Iterable[str]) -> tuple[str, ...]:
    selected: set[str] = set()
    for status in _values(statuses):
        if status == "active":
            selected.update(WI_ACTIVE_STATES)
        elif status == "processable":
            selected.update(WI_PROCESSABLE_STATES)
        elif status == "all":
            selected.update(WI_STATES)
        else:
            selected.add(status)
    return tuple(state for state in WI_STATES if state in selected)


def answered_matches(entry_type: str | None, text: str, answered_filters: str | Iterable[str]) -> bool:
    filters = set(_values(answered_filters))
    if "all" in filters:
        return True
    if entry_type != WI_TYPE_UWI or _wi_uwi_scan.is_report(text):
        return False
    answered = _wi_uwi_scan.is_uwi_answered(text)
    return (answered and "yes" in filters) or (not answered and "no" in filters)


def user_comment_matches(text: str, user_comment_filter: str) -> bool:
    if user_comment_filter == "all":
        return True
    return _wi_user_comment.has_user_comment(text) == (user_comment_filter == "yes")


def normalized_source(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def source_matches(entry_source: str | None, filter_value: str) -> bool:
    if filter_value.startswith("!"):
        return entry_source != filter_value[1:]
    return entry_source == filter_value


def source_matches_any(entry_source: str | None, filter_values: str | Iterable[str] | None) -> bool:
    if filter_values is None:
        return True
    return any(source_matches(entry_source, value) for value in _values(filter_values))


def is_important(text: str) -> bool:
    parsed = parse_frontmatter(text)
    return parsed is not None and parsed[0].get("important") is True


def important_matches(text: str, filter_values: str | Iterable[str]) -> bool:
    values = set(_values(filter_values))
    return "all" in values or important_value_matches(is_important(text), values)


def important_value_matches(important: bool, filter_values: str | Iterable[str]) -> bool:
    values = set(_values(filter_values))
    return "all" in values or ("yes" if important else "no") in values


class TargetRepoMatcher:
    def __init__(self, filter_values: str | Iterable[str] | None) -> None:
        self._cache: dict[str, str | None] = {}
        values = () if filter_values is None else _values(filter_values)
        self.active = bool(values)
        self._canonical: set[str] = set()
        self._raw: set[str] = set()
        for value in values:
            canonical = _git_remote.canonical_repo(value, self._cache)
            if canonical is None:
                self._raw.add(value)
            else:
                self._canonical.add(canonical)

    def matches(self, entry_target_repo: object) -> bool:
        if not self.active:
            return True
        if not isinstance(entry_target_repo, str):
            return False
        if entry_target_repo in self._raw:
            return True
        return bool(self._canonical) and _git_remote.canonical_repo(entry_target_repo, self._cache) in self._canonical


def selection_matches(
    *,
    kind: str | None,
    kinds: set[str],
    text: str,
    target_repo: object,
    repository: TargetRepoMatcher,
    answered: str | Iterable[str],
    source: str | None,
    sources: str | Iterable[str] | None,
    allow_missing_repo_for_unknown_kind: bool = False,
) -> bool:
    return (
        ("all" in kinds or kind in kinds)
        and ((allow_missing_repo_for_unknown_kind and kind is None) or repository.matches(target_repo))
        and answered_matches(kind, text, answered)
        and source_matches_any(source, sources)
    )
