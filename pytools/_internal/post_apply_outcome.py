"""post-applyステップの構造化結果を定義する。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PostApplyNotice:
    """post-apply完了時に表示する案内を表す。"""

    message: str
    command: str | None = None


@dataclass(frozen=True)
class PostApplyOutcome:
    """post-applyステップの変更有無と案内を表す。"""

    changed: bool
    notices: tuple[PostApplyNotice, ...] = ()
