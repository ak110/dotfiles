"""turnの開始受理段階と、終了結果を保留する排他的な理由。

再開の消費履歴・接続選択・外部CLIの状態は別の寿命を持つため、ここへ複製しない。
"""

import dataclasses
import enum
from typing import Any

USAGE_LIMIT_ERROR_TYPE = "usage_limit"
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})

HELD_RESULT_FINALIZED_KEY = "heldResultFinalized"
"""再開turnの結果ではなく、元の待機表明を確定したときに`error`へ付ける項目名。"""


@dataclasses.dataclass(frozen=True)
class EffortResumePending:
    turn_seq: int


@dataclasses.dataclass(frozen=True)
class EffortResumeRunning:
    pass


class TurnStart(enum.Enum):
    UNSENT = enum.auto()
    SENT = enum.auto()
    STARTED = enum.auto()
    AMBIGUOUS = enum.auto()
    REJECTED = enum.auto()


class TurnEnd(enum.Enum):
    """turnの終了段階。対話入力の失敗後も、実行中turnの終了観測を待つ場合を区別する。"""

    IN_PROGRESS = enum.auto()
    FAILURE_PENDING = enum.auto()
    COMPLETED = enum.auto()


@dataclasses.dataclass(frozen=True)
class BackgroundWait:
    """子・背景処理の完了を待つ結果。期限は対象の寿命からだけ定める。"""

    result: dict[str, Any]
    deadline: float | None


@dataclasses.dataclass(frozen=True)
class OverloadWait:
    """過負荷の再試行を予定した結果。連鎖の履歴はsessionが別に所有する。"""

    result: dict[str, Any]
    resume_at: float


@dataclasses.dataclass(frozen=True)
class UsageLimitWait:
    result: dict[str, Any]
    resume_at: float


@dataclasses.dataclass(frozen=True)
class ClaudeConnectionWait:
    """選択済みのClaude接続で再開するまで保留する結果。"""

    result: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class CodexProviderWait:
    """選択済みのCodex providerで再開するまで保留する結果。"""

    result: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class ResultContractWait:
    result: dict[str, Any]
    reason: str


@dataclasses.dataclass(frozen=True)
class InterruptedWait:
    result: dict[str, Any]


type ResultHold = (
    BackgroundWait
    | OverloadWait
    | UsageLimitWait
    | ClaudeConnectionWait
    | CodexProviderWait
    | ResultContractWait
    | InterruptedWait
)
