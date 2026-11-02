"""状態分類の試験へ、実OSに作用しないpsutil観測対象を供給する。"""

import dataclasses

import psutil


@dataclasses.dataclass
class ObservedProcess:
    """PID・起動時刻と状態を独立に変え、終了要求を記録する。"""

    pid: int = 2147483000
    state: str = psutil.STATUS_RUNNING
    account: str = "test-account"
    arguments: tuple[str, ...] = ("codex",)
    descendants: list = dataclasses.field(default_factory=list)
    calls: list[str] = dataclasses.field(default_factory=list)

    def create_time(self) -> float:
        """PID再利用では記録と異なる起動時刻を返す。"""
        return 43.0 if self.state == "pid-reused" else 42.0

    def status(self) -> str:
        """生存状態と照会拒否・消滅を供給する。"""
        if self.state == "access-denied":
            raise psutil.AccessDenied(self.pid)
        if self.state == "missing":
            raise psutil.NoSuchProcess(self.pid)
        return psutil.STATUS_RUNNING if self.state == "pid-reused" else self.state

    def is_running(self) -> bool:
        """取得済みhandleのPID再利用を生存と区別する。"""
        return self.state not in {"pid-reused", "missing"}

    def username(self) -> str:
        """模擬OSアカウントを返す。"""
        return self.account

    def name(self) -> str:
        """活動候補に含まれるホスト名を返す。"""
        return "codex"

    def exe(self) -> str:
        """模擬ホスト実行ファイルの場所を返す。"""
        return "/fake/codex"

    def cmdline(self) -> list[str]:
        """起動引数を返す。"""
        return list(self.arguments)

    def children(self, *, recursive: bool = False) -> list:
        """所有側が列挙した模擬子孫を返す。"""
        del recursive
        return list(self.descendants)

    def terminate(self) -> None:
        """終了要求を記録する。"""
        self.calls.append("terminate")

    def kill(self) -> None:
        """強制終了要求を記録する。"""
        self.calls.append("kill")
