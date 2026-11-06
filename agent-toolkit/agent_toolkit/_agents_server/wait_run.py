"""CLI待機runの保存形式と、所有ロック内で実行する遷移を定義する。"""

from __future__ import annotations

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
        """保存済みの対象を保持して追加する。"""
        return RunningRun.model_validate(
            {**self.model_dump(exclude_unset=True), "targets": sorted(set(self.targets) | set(targets))}
        )

    def publish(self, output: str, exit_code: int, stream: Literal["stdout", "stderr"]) -> PublishedRun:
        """flush前に保存する公開結果を作る。配送を確定するまで継続可能とはしない。"""
        return PublishedRun.model_validate(
            {
                **self.model_dump(exclude_unset=True),
                "status": "published",
                "output": output,
                "exit_code": exit_code,
                "stream": stream,
                "continuable": False,
            }
        )


class _ResultRun(_Run):
    output: str
    exit_code: int
    stream: Literal["stdout", "stderr"] = "stdout"
    continuable: bool = False


class PublishedRun(_ResultRun):
    """保存済みだが前景配送を確定していない結果。"""

    status: Literal["published"] = "published"

    def deliver(self, *, continuable: bool) -> ForegroundDeliveredRun:
        """前景へのflush後に保存する状態を作る。"""
        return ForegroundDeliveredRun.model_validate(
            {**self.model_dump(exclude_unset=True), "status": "foreground-delivered", "continuable": continuable}
        )

    def consume(self) -> ConsumedRun:
        """回復配送のflush後に保存する状態を作る。"""
        return ConsumedRun.model_validate({**self.model_dump(exclude_unset=True), "status": "consumed"})


class ForegroundDeliveredRun(_ResultRun):
    """最初の待機が前景へ配送した結果。後続waitは再配送しない。"""

    status: Literal["foreground-delivered"] = "foreground-delivered"


class ConsumedRun(_ResultRun):
    """後続waitが回復配送した結果。"""

    status: Literal["consumed"] = "consumed"


type WaitRun = RunningRun | PublishedRun | ForegroundDeliveredRun | ConsumedRun
_RUN = TypeAdapter(Annotated[WaitRun, Field(discriminator="status")])


def read_run(path: pathlib.Path) -> WaitRun | None:
    """不在だけをNoneとし、読取不能・不正な状態は呼出側の診断へ渡す。"""
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return _RUN.validate_json(contents)


def read_current(path: pathlib.Path) -> CurrentRun | None:
    """run参照を検証し、不在だけをNoneで返す。"""
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return CurrentRun.model_validate_json(contents)


def uncollected(run: WaitRun) -> bool:
    """回収途中または未配送のrunかを返す。"""
    return isinstance(run, (RunningRun, PublishedRun))


def reusable(run: WaitRun, targets: list[str]) -> bool:
    """同じ対象の待機を再開または回復配送するrunかを返す。"""
    return run.targets == targets and (isinstance(run, RunningRun) or isinstance(run, PublishedRun) and not run.continuable)
