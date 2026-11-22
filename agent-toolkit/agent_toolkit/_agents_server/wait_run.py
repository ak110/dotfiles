"""CLI待機runの保存形式と、所有ロック内で実行する遷移を定義する。"""

from __future__ import annotations

import contextlib
import pathlib
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter

from agent_toolkit._agents_server import shared_layout


def _identifier(value: str) -> str:
    if not shared_layout.valid_session_id(value):
        raise ValueError("識別子の形式が不正です")
    return value


type _Identifier = Annotated[str, AfterValidator(_identifier)]


class CurrentRun(BaseModel):
    """current.jsonが持つrun参照。run本体とは別の保存契約を持つ。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    run_id: _Identifier


class _Run(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    run_id: _Identifier
    targets: list[_Identifier]
    started_at: str | None = None


class RunningRun(_Run):
    """結果を公開する前のrun。対象追加は同じ所有ロック内で行う。"""

    status: Literal["running"] = "running"

    def add_targets(self, targets: list[str]) -> RunningRun:
        return RunningRun.model_validate(
            {**self.model_dump(exclude_unset=True), "targets": sorted(set(self.targets) | set(targets))}
        )

    def publish(
        self, output: str, exit_code: int, stream: Literal["stdout", "stderr"], *, result_turns: dict[str, int] | None = None
    ) -> PublishedRun:
        return PublishedRun.model_validate(
            {
                **self.model_dump(exclude_unset=True),
                "status": "published",
                "output": output,
                "exit_code": exit_code,
                "stream": stream,
                "continuable": False,
                **({"result_turns": result_turns} if result_turns else {}),
            }
        )


class _ResultRun(_Run):
    output: str
    exit_code: int
    stream: Literal["stdout", "stderr"] = "stdout"
    continuable: bool = False
    result_turns: dict[_Identifier, int] = Field(default_factory=dict)


class PublishedRun(_ResultRun):
    status: Literal["published"] = "published"

    def deliver(self, *, continuable: bool) -> ForegroundDeliveredRun:
        return ForegroundDeliveredRun.model_validate(
            {**self.model_dump(exclude_unset=True), "status": "foreground-delivered", "continuable": continuable}
        )

    def consume(self) -> ConsumedRun:
        return ConsumedRun.model_validate({**self.model_dump(exclude_unset=True), "status": "consumed"})


class ForegroundDeliveredRun(_ResultRun):
    """最初の待機が前景へ配送した結果。後続waitは再配送しない。"""

    status: Literal["foreground-delivered"] = "foreground-delivered"


class ConsumedRun(_ResultRun):
    status: Literal["consumed"] = "consumed"


type WaitRun = RunningRun | PublishedRun | ForegroundDeliveredRun | ConsumedRun
_RUN = TypeAdapter(Annotated[WaitRun, Field(discriminator="status")])


def read_run(path: pathlib.Path) -> WaitRun | None:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return _RUN.validate_json(contents)


def read_current(path: pathlib.Path) -> CurrentRun | None:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return CurrentRun.model_validate_json(contents)


def uncollected(run: WaitRun) -> bool:
    return isinstance(run, (RunningRun, PublishedRun))


def reusable(run: WaitRun, targets: list[str]) -> bool:
    """同じ対象の待機を再開または回復配送するrunかを返す。"""
    return run.targets == targets and (isinstance(run, RunningRun) or isinstance(run, PublishedRun) and not run.continuable)


def delivered_children(directory: pathlib.Path, turns: dict[str, int]) -> set[str]:
    delivered: set[str] = set()
    for path in directory.glob("*.json"):
        if path.name == "current.json":
            continue
        with contextlib.suppress(OSError, UnicodeError, ValueError):
            run = read_run(path)
            if isinstance(run, (ForegroundDeliveredRun, ConsumedRun)):
                delivered.update(key for key, seq in run.result_turns.items() if turns.get(key) == seq)
                if delivered == turns.keys():
                    break
    return delivered
