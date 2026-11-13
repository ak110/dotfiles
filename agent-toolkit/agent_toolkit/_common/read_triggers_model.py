"""読込宣言の語彙・型と要求内の解決scopeを定める。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import functools
import pathlib
import re
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from agent_toolkit._common import (
        read_triggers,  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
    )


KINDS = frozenset({"操作", "起動時", "判断"})
WI_SAVE_OPERATIONS = frozenset(
    {
        "wi-standard-dry-run",
        "wi-standard-commit",
        "wi-report-dry-run",
        "wi-report-commit",
        "wi-standard-self-review",
    }
)

OPERATIONS = (
    frozenset(
        {
            "delegation",
            "delegation-result",
            "search",
            "root-cause-writing",
            "managed-temp-create",
            "body-writing",
            "existing-file-edit",
            "claude-existing-file-edit",
            "verification",
            "agent-document-writing",
            "confirmation",
            "awi-draft-writing",
            "uwi-draft-writing",
            "plan-draft-writing",
        }
    )
    | WI_SAVE_OPERATIONS
)

PREFIXES = ("skill:", "write:", "path:", "dependency:", "tool:", "bash:", "role:", "read:")

DYNAMIC_SELECTORS = frozenset(
    {
        "role-contract",
        "review-reference",
        "external-pytilpack-index",
        "artifact-standards",
        "same-document",
        "project-skill",
    }
)

# 役割文書以外の読込表で使える起動入力は、委譲入力が無い主体でも
# 解決できる入力だけに限る。値は配送側の解決元を識別する。
INPUT_SELECTOR_RESOLUTION_SOURCES = {"input:作業契約": "main-work-contract"}

_CODE = re.compile(r"`([^`]+)`")

_REFERENCE = re.compile(r"`([^`]+)`|\[[^\]]+\]\(([^)]+)\)")

_REFERENCE_METHOD_HEADING = "名指し参照の読込方法"

# 本文が名指しする資料へ補う宣言の条件。読込表へ書かれていた定型の行と同じ文言を保つ。
NAMED_REFERENCE_CONDITION = "本文がこの資料の基準を使う場面で、資料を選択して取得する時"

_RESOLUTION_CACHE: contextvars.ContextVar[dict[tuple, object] | None] = contextvars.ContextVar(
    "read_trigger_resolution_cache", default=None
)


@contextlib.contextmanager
def resolution_scope() -> Generator[None]:
    """1回の要求内でだけ解決を共有し、次の要求ではファイルの変更を読み直す。

    入れ子のscopeは外側の共有を引き継ぐ。hookの1回の呼出しが宣言一覧と取得済み資料の宣言を
    続けて解決しても、同じ不変の参照を解決し直さない。
    """
    if _RESOLUTION_CACHE.get() is not None:
        yield
        return
    token = _RESOLUTION_CACHE.set({})
    try:
        yield
    finally:
        _RESOLUTION_CACHE.reset(token)


_DECLARATION_ISSUES_KEY = ("declaration-issues",)


def record_declaration_issue(message: str) -> None:
    """宣言から除いた行の診断を、現在の要求の範囲へ記録する。

    要求の範囲の外では記録せず、行の除外だけが行われる。同じ診断は1回だけ保持する。
    """
    cache = _RESOLUTION_CACHE.get()
    if cache is None:
        return
    issues = cache.setdefault(_DECLARATION_ISSUES_KEY, {})
    if isinstance(issues, dict):
        issues[message] = None


def declaration_issues() -> tuple[str, ...]:
    """現在の要求の範囲で、宣言から除いた行の診断を記録順に返す。"""
    cache = _RESOLUTION_CACHE.get()
    issues = cache.get(_DECLARATION_ISSUES_KEY) if cache is not None else None
    return tuple(issues) if isinstance(issues, dict) else ()


def _scope_cached[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """循環検出用の訪問済み資料を含む全入力が同じ解決だけを、解析中に保存する。"""

    @functools.wraps(function)
    def resolve(*args: P.args, **kwargs: P.kwargs) -> R:
        cache = _RESOLUTION_CACHE.get()
        if cache is None:
            return function(*args, **kwargs)
        key = (function, args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = function(*args, **kwargs)
        return cast(R, cache[key])

    return resolve


@dataclasses.dataclass(frozen=True)
class ReadTrigger:
    """資料の到達契機を持つ読込表の1行。"""

    source: pathlib.Path
    line: int
    kind: str
    operations: tuple[str, ...]
    documents: tuple[pathlib.Path, ...]
    headings: tuple[str, ...] = ()
    selector: str = ""
    condition: str = ""
    material: str = ""
    plugin_root: pathlib.Path | None = None
    host: str = "claude"
    project: pathlib.Path | str | None = None
    # 「契機」「配送操作」を省略した行と、本文の名指しへ補った行。配送操作は資料自身の読取として導出する。
    derived_read: bool = False

    @functools.cached_property
    def targets(self) -> tuple[read_triggers.ReadTarget, ...]:
        """不変な宣言行の寿命内で、解決済みの資料と指定範囲を共有する。"""
        from agent_toolkit._common import read_triggers  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
        from agent_toolkit._common.read_triggers import _row_targets  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

        if self.plugin_root is not None:
            try:
                return _row_targets(
                    self.material, self.source, self.line, self.plugin_root, self.host, self.project, self.selector
                )
            except ValueError as error:
                # 資料の解決を遅らせた行は、解析時と同じくユーザー側の宣言元だけを行単位で除く。
                if not read_triggers.user_side_source(self.source):
                    raise
                record_declaration_issue(str(error))
                return ()
        headings = self.headings or ("",) * len(self.documents)
        return tuple(read_triggers.ReadTarget(path, heading) for path, heading in zip(self.documents, headings, strict=True))

    @functools.cached_property
    def effective_operations(self) -> tuple[str, ...]:
        """宣言された配送操作か、省略時に資料から導出した読取の配送操作を返す。

        導出は資料の解決を伴うため、解決を遅らせた行では、読取対象と名前が一致して候補に残った行だけで参照する。
        """
        # pylint: disable=import-outside-toplevel,cyclic-import
        from agent_toolkit._common import read_triggers_resolution  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        # pylint: enable=import-outside-toplevel,cyclic-import

        if not self.derived_read:
            return self.operations
        return tuple(dict.fromkeys(read_triggers_resolution.read_operation(target.path) for target in self.targets))
