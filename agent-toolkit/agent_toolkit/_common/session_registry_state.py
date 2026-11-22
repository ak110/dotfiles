from __future__ import annotations

import enum
from typing import Literal


class PublicationState(enum.Enum):
    PENDING_STARTING = (False, "starting")
    PENDING_RUNNING = (False, "running")
    PENDING_COMPLETED = (False, "completed")
    PENDING_FAILED = (False, "failed")
    PENDING_INTERRUPTED = (False, "interrupted")
    RESULT_COMPLETED = (True, "completed")
    RESULT_FAILED = (True, "failed")
    RESULT_INTERRUPTED = (True, "interrupted")

    @property
    def terminal(self) -> bool:
        return self.value[0]

    @property
    def status(self) -> Literal["starting", "running", "completed", "failed", "interrupted"]:
        return self.value[1]

    @classmethod
    def from_status(cls, status: str, *, result_available: bool) -> PublicationState:
        if not isinstance(result_available, bool):
            raise TypeError("result_available must be a bool")
        return cls((result_available, status))


class Resolution(enum.StrEnum):
    TERMINAL = "terminal"
    RUNNING = "running"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    RELEASED = "released"


class RunningOwnership(enum.StrEnum):
    OWNED = "owned"
    """生存の印が有効な状態ファイルがsessionを載せている。所有サーバーが終了を公開する。"""
    OWNER_LOST = "owner_lost"
    """所有サーバーを失った実行中。生存の印が有効な状態ファイルが無く、記録プロセスのうち委譲先CLIが実在する。"""
    ORPHANED = "orphaned"
    """所有者も委譲先CLIもいない。記録プロセスを持たない旧形式の記録と、委譲先CLIが実在しない記録が該当する。"""
