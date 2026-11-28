"""CLI待機runの保存形式と、所有ロック内で実行する遷移を定義する。"""

from __future__ import annotations

import contextlib
import json
import pathlib
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter

from agent_toolkit._agents_server import result_projection, shared_layout
from agent_toolkit._common import session_registry
from agent_toolkit._common.json_lines import split_lines


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


def add_first_review_reply_actions(
    results: list[dict[str, Any]], result_turns: dict[str, int], run_directory: pathlib.Path, state_root: pathlib.Path | None
) -> None:
    candidates: dict[str, int] = {}
    for result in results:
        seq = result_turns.get(result["session_id"])
        label = result.get("label")
        if (
            seq is None
            or seq <= 1
            or result.get("status") != "completed"
            or result_projection.is_finalized_wait_statement(result)
            or not isinstance(label, str)
            or not label.endswith(result_projection.REVIEW_LABEL_SUFFIX)
            or label.endswith("reader-fit-review")
        ):
            continue
        try:
            info = session_registry.resolve(result["session_id"], state_root=state_root).resume_info
        except KeyError, TypeError:
            continue
        if info is not None and info.launch_kind in {"delegate", "explore"} and not info.launch_info.task_role:
            candidates[result["session_id"]] = seq
    if not candidates:
        return
    previous_paths = _previous_completed_paths(run_directory, candidates)
    for index, result in enumerate(results):
        if result["session_id"] in previous_paths:
            previous = previous_paths[result["session_id"]] or "前回の`atk agents wait`の出力"
            results[index] = result_projection.append_result_next_action(
                result,
                f"同じレビュー担当の前回の返却（本文: {previous}）を受領済みで、今回はその後の返却である。"
                "前回と今回の指摘を原因と害で対応付け、`reviewee.md`「方式見直しと再レビュー」手順11の条件に当たるかを判定してから"
                "修正を依頼する。この行は、論点の再発や再レビューの要否を判定したものではない。",
            )


def _previous_completed_paths(directory: pathlib.Path, turns: dict[str, int]) -> dict[str, str | None]:
    candidates = turns.copy()
    previous: dict[str, tuple[int, str | None]] = {}
    try:
        for path in directory.iterdir():
            if path.suffix != ".json" or path.name == "current.json":
                continue
            run = read_run(path)
            if not isinstance(run, (ForegroundDeliveredRun, ConsumedRun)) or run.stream != "stdout":
                continue
            if not any(key in candidates and 0 < seq <= candidates[key] for key, seq in run.result_turns.items()):
                continue
            for line in split_lines(run.output):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(key := row.get("session_id"), str):
                    continue
                seq = run.result_turns.get(key)
                current = candidates.get(key)
                if seq is None or current is None or not 0 < seq <= current:
                    continue
                if row.get("status") != "completed" or result_projection.is_finalized_wait_statement(row):
                    continue
                known = previous.get(key)
                if seq == current or (known is not None and known[0] != seq):
                    candidates.pop(key)
                    previous.pop(key, None)
                elif known is None or known[1] is None:
                    body = row.get("agent_message_path")
                    previous[key] = (seq, body if isinstance(body, str) and body else None)
            if not candidates:
                break
    except OSError, UnicodeError, ValueError:
        return {}
    return {key: previous_path for key, (_, previous_path) in previous.items()}
