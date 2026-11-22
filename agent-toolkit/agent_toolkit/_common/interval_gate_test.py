import pathlib

import pytest

from agent_toolkit._common import interval_gate

_NOW = 1_800_000_000.0


@pytest.mark.parametrize(
    ("recorded", "due"),
    [(None, True), ("broken", True), (repr(_NOW + 60), True), (repr(_NOW - 60), False), (repr(_NOW - 3600), True)],
    ids=["missing", "broken", "future", "within", "elapsed"],
)
def test_claim_skips_only_within_the_interval(tmp_path: pathlib.Path, recorded: str | None, due: bool) -> None:
    record = tmp_path / "sweeps" / "test"
    if recorded is not None:
        record.parent.mkdir()
        record.write_text(recorded, encoding="ascii")
    assert interval_gate.claim(record, interval_seconds=1800, now=_NOW) is due
    assert interval_gate.claim(record, interval_seconds=1800, now=_NOW + 1) is False
