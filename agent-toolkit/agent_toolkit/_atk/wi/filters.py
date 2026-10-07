"""WIの一覧を限定する条件（状態、回答状況、source、target_repo）の判定。

`atk wi list`・`atk wi grep`などのCLIと、`atk serve`のWI画面の一覧が同じ判定を使う。
画面は各条件を単一値で送り、CLIは複数値を受け取る。単一値は複数値の特殊な形として同じ判定へ渡す。
画面だけが持つ条件（検索語、期間、`plan`、`kind`、`source_empty`、`source_kind`）は画面側で判定する。
"""

from collections.abc import Iterable

from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import WI_ACTIVE_STATES, WI_PROCESSABLE_STATES, WI_STATES, WI_TYPE_UWI
from agent_toolkit._git import remote as _git_remote


def _values(value: str | Iterable[str]) -> tuple[str, ...]:
    """単一値の指定を複数値の指定へそろえる。"""
    return (value,) if isinstance(value, str) else tuple(value)


def resolve_states(statuses: str | Iterable[str]) -> tuple[str, ...]:
    """状態の指定値（`active`・`processable`・`all`・個別の状態）を走査する状態フォルダの列へ変換する。

    結果は`WI_STATES`の順に並べ、状態フォルダに当たらない値は含めない。
    """
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
    """回答状況の指定値（`all`・`yes`・`no`）との一致を返す。`all`以外ではUWIだけが一致し得る。"""
    filters = set(_values(answered_filters))
    if "all" in filters:
        return True
    if entry_type != WI_TYPE_UWI:
        return False
    answered = _wi_uwi_scan.is_uwi_answered(text)
    return (answered and "yes" in filters) or (not answered and "no" in filters)


def normalized_source(value: object) -> str | None:
    """frontmatterの`source`の値を判定用に正規化する。空文字列と文字列以外は無指定（None）とする。"""
    return value if isinstance(value, str) and value else None


def source_matches(entry_source: str | None, filter_value: str) -> bool:
    """`source`の指定値の1つにエントリの`source`が一致するかを返す。

    先頭`!`は否定指定とし、無指定（None）エントリも否定側の一致に含める。
    """
    if filter_value.startswith("!"):
        return entry_source != filter_value[1:]
    return entry_source == filter_value


def source_matches_any(entry_source: str | None, filter_values: str | Iterable[str] | None) -> bool:
    """`source`の指定値のいずれかに一致するかを返す。指定が無い場合は常に一致する。"""
    if filter_values is None:
        return True
    return any(source_matches(entry_source, value) for value in _values(filter_values))


class TargetRepoMatcher:
    """`target_repo`の指定値による判定。

    指定値とエントリの値は正規化した識別子で比べ、旧パス形とURL形を同じリポジトリとして扱う。
    正規化できない指定値は、エントリの保存値と原値のまま比べる（WI画面の選択肢は正規化できない保存値を原値で示すため）。
    正規化の結果は判定1回分の操作単位でキャッシュする。
    """

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
        """エントリの`target_repo`が指定値に一致するかを返す。指定が無い場合は常に一致する。"""
        if not self.active:
            return True
        if not isinstance(entry_target_repo, str):
            return False
        if entry_target_repo in self._raw:
            return True
        return bool(self._canonical) and _git_remote.canonical_repo(entry_target_repo, self._cache) in self._canonical
