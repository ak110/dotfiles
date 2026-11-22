import dataclasses

import psutil


@dataclasses.dataclass
class ObservedProcess:
    pid: int = 2147483000
    state: str = psutil.STATUS_RUNNING
    account: str = "test-account"
    arguments: tuple[str, ...] = ("codex",)
    descendants: list = dataclasses.field(default_factory=list)
    calls: list[str] = dataclasses.field(default_factory=list)

    def create_time(self) -> float:
        return 43.0 if self.state == "pid-reused" else 42.0

    def status(self) -> str:
        if self.state == "access-denied":
            raise psutil.AccessDenied(self.pid)
        if self.state == "missing":
            raise psutil.NoSuchProcess(self.pid)
        return psutil.STATUS_RUNNING if self.state == "pid-reused" else self.state

    def is_running(self) -> bool:
        return self.state not in {"pid-reused", "missing"}

    def username(self) -> str:
        return self.account

    def name(self) -> str:
        return "codex"

    def exe(self) -> str:
        return "/fake/codex"

    def cmdline(self) -> list[str]:
        return list(self.arguments)

    def children(self, *, recursive: bool = False) -> list:
        del recursive
        return list(self.descendants)

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")
