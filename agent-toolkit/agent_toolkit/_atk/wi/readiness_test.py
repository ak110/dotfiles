"""観測待ちと通常候補・不正メタデータを同じreadinessから分ける。"""

import pathlib

import pytest

from agent_toolkit._atk.wi import frontmatter, readiness

REPO = "github.com/example/project"
WAIT = "20260930-175957-001.md"
WORK = "20261004-044541-001.md"
METADATA = {"condition": "selection-empty", "plan_file": "30-1849_process-wi_レーン02.md", "commit": "a" * 40}


def entry(notes: pathlib.Path, name: str, *, state: str = "processing", metadata: object = None) -> pathlib.Path:
    """本文と状態を保持した入力を作成する。"""
    directory = notes / state
    directory.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {"type": "awi", "target_repo": REPO}
    if metadata is not None:
        data["observation_wait"] = metadata
    path = directory / name
    path.write_text(frontmatter.serialize_frontmatter(data, "## 完成条件\n- 元の条件\n"), encoding="utf-8")
    return path


def test_waiting_only_does_not_start_processing(tmp_path: pathlib.Path) -> None:
    """待機自身を候補へ戻さず、保存状態・元の要求・commitを保つ。"""
    path = entry(tmp_path, WAIT, metadata=METADATA)
    original = path.read_bytes()
    result = readiness.calculate_readiness(tmp_path, REPO)
    assert result.observation_waiting == (WAIT,)
    assert not result.ready and result.actionable_count == 0
    assert path.read_bytes() == original and path.parent.name == "processing"


def test_mixed_queue_only_runs_normal_work(tmp_path: pathlib.Path) -> None:
    entry(tmp_path, WAIT, metadata=METADATA)
    entry(tmp_path, WORK)
    result = readiness.calculate_readiness(tmp_path, REPO)
    assert result.ready == (WORK,) and result.actionable_count == 1
    assert result.observation_waiting == (WAIT,)


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {**METADATA, "condition": "other"},
        {**METADATA, "commit": "missing"},
        {**METADATA, "plan_file": "../plan.md"},
        "broken",
    ],
)
def test_invalid_wait_is_diagnosed_without_silent_exclusion(tmp_path: pathlib.Path, metadata: object) -> None:
    entry(tmp_path, WAIT, metadata=metadata)
    result = readiness.calculate_readiness(tmp_path, REPO)
    assert result.invalid_observation_waits == (WAIT,)
    assert result.actionable_count == 1 and not result.observation_waiting


def test_wrong_saved_state_cannot_be_observation_wait(tmp_path: pathlib.Path) -> None:
    entry(tmp_path, WAIT, state="inbox", metadata=METADATA)
    result = readiness.calculate_readiness(tmp_path, REPO)
    assert result.invalid_observation_waits == (WAIT,) and result.actionable_count == 1


def test_dependency_on_observation_wait_is_not_internal_work(tmp_path: pathlib.Path) -> None:
    entry(tmp_path, WAIT, metadata=METADATA)
    path = entry(tmp_path, WORK)
    path.write_text(
        frontmatter.serialize_frontmatter({"type": "awi", "target_repo": REPO, "depends_on": [WAIT]}, "完成条件"),
        encoding="utf-8",
    )
    result = readiness.calculate_readiness(tmp_path, REPO)
    assert not result.ready and not result.internal_dependency_waits and result.actionable_count == 0
