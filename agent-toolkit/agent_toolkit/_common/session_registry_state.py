"""登録簿の公開状態・解決区分・所有区分を定義する。"""

from __future__ import annotations

import enum
from typing import Literal


class PublicationState(enum.Enum):
    """登録簿へ書く結果公開可否とstatusの許容組。

    結果を保留している間もstatusは終状態になり得るため、公開前の終状態を個別に表す。
    """

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
        """保存する結果公開可否を返す。"""
        return self.value[0]

    @property
    def status(self) -> Literal["starting", "running", "completed", "failed", "interrupted"]:
        """保存するstatusを返す。"""
        return self.value[1]

    @classmethod
    def from_status(cls, status: str, *, result_available: bool) -> PublicationState:
        """sessionの公開状態を登録簿の書込状態へ変換する。"""
        if not isinstance(result_available, bool):
            raise TypeError("result_available must be a bool")
        return cls((result_available, status))


class Resolution(enum.StrEnum):
    """登録簿からsessionを観測した区分。"""

    TERMINAL = "terminal"
    RUNNING = "running"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    RELEASED = "released"


class RunningOwnership(enum.StrEnum):
    """`running`の記録の所有状態。`running_ownership`が記録プロセスの実在と状態ファイルの生存の印から判定する。"""

    OWNED = "owned"
    """生存の印が有効な状態ファイルがsessionを載せている。所有サーバーが終了を公開する。"""
    OWNER_LOST = "owner_lost"
    """所有サーバーを失った実行中。生存の印が有効な状態ファイルが無く、記録プロセスのうち委譲先CLIが実在する。"""
    ORPHANED = "orphaned"
    """所有者も委譲先CLIもいない。記録プロセスを持たない旧形式の記録と、委譲先CLIが実在しない記録が該当する。"""
