"""WI状態遷移を独立した単純モデルと比較する。"""

from __future__ import annotations

import contextlib
import datetime
import pathlib
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_toolkit._atk.wi import mutations, readiness
from agent_toolkit.atk_test import _setup_notes, _write_awi_file

_NOW = datetime.datetime(2026, 10, 5, tzinfo=datetime.UTC)
_ACTIONS = ("start-processing", "return-to-inbox", "hold", "unhold", "adopt", "reject", "remove")
_DESTINATION = {
    "start-processing": "processing",
    "return-to-inbox": "inbox",
    "hold": "hold",
    "unhold": "inbox",
    "adopt": "adopted",
    "reject": "rejected",
    "remove": None,
}
_SOURCES = {
    "start-processing": {"inbox"},
    "return-to-inbox": {"processing", "adopted", "rejected"},
    "hold": {"inbox", "processing", "adopted", "rejected"},
    "unhold": {"hold"},
    "adopt": {"inbox", "processing", "hold"},
    "reject": {"inbox", "processing", "hold"},
    "remove": {"inbox", "processing", "hold", "adopted", "rejected"},
}


def _disable_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)


def _state(notes: pathlib.Path) -> str | None:
    locations = [
        state for state in ("inbox", "processing", "hold", "adopted", "rejected") if (notes / state / "entry.md").exists()
    ]
    assert len(locations) <= 1
    return locations[0] if locations else None


@settings(max_examples=35, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(actions=st.lists(st.sampled_from(_ACTIONS), min_size=1, max_size=20))
def test_transition_sequences_match_reference_model(actions: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """受理と拒否の操作列で、配置・本文・一意性をモデルと一致させる。"""
    _disable_git(monkeypatch)
    with tempfile.TemporaryDirectory() as directory:
        notes = _setup_notes(pathlib.Path(directory))
        _write_awi_file(notes, "entry.md")
        expected: str | None = "inbox"
        for action in actions:
            before = {path.relative_to(notes): path.read_bytes() for path in notes.rglob("entry.md")}
            accepted = expected is not None and expected in _SOURCES[action]
            state = (
                expected
                if (action == "hold" and expected in {"processing", "adopted", "rejected"})
                or (action == "return-to-inbox" and expected in {"adopted", "rejected"})
                else None
            )
            force = action == "remove" and expected == "processing"
            if accepted:
                mutations.transition_entries(
                    notes,
                    action=action,
                    filenames=["entry.md"],
                    now=_NOW,
                    skip_remote_sync=True,
                    state=state,
                    force=force,
                )
                expected = _DESTINATION[action]
            else:
                with pytest.raises((SystemExit, mutations.WebInputError)):
                    mutations.transition_entries(
                        notes,
                        action=action,
                        filenames=["entry.md"],
                        now=_NOW,
                        skip_remote_sync=True,
                        state=state,
                        force=force,
                    )
                after = {path.relative_to(notes): path.read_bytes() for path in notes.rglob("entry.md")}
                assert after == before
            assert _state(notes) == expected


def test_reopening_terminal_entry_strips_previous_result(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """既知事例: 終端後の再開は前回の処理結果を次の処理へ渡さない。"""
    _disable_git(monkeypatch)
    notes = _setup_notes(tmp_path)
    source = _write_awi_file(notes, "entry.md")
    terminal = notes / "adopted" / source.name
    terminal.parent.mkdir(exist_ok=True)
    source.replace(terminal)
    terminal.write_text(terminal.read_text(encoding="utf-8") + "\n## 処理結果\n\n- 旧結果\n", encoding="utf-8")
    mutations.transition_entries(
        notes,
        action="hold",
        filenames=[source.name],
        state="adopted",
        now=_NOW,
        skip_remote_sync=True,
    )
    assert "## 処理結果" not in (notes / "hold" / source.name).read_text(encoding="utf-8")


def test_bulk_actor_cooldown_and_repeat_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複数対象、actor保護、cooldownと同一操作の再実行を一つの回帰列で固定する。"""
    _disable_git(monkeypatch)
    notes = _setup_notes(tmp_path)
    first = _write_awi_file(notes, "first.md")
    second = _write_awi_file(notes, "second.md")
    first_body = first.read_text(encoding="utf-8")
    second_body = second.read_text(encoding="utf-8")
    assert mutations.transition_entries(
        notes,
        action="start-processing",
        filenames=[first.name, second.name],
        now=_NOW,
        skip_remote_sync=True,
    ) == [first.name, second.name]

    processing = notes / "processing" / first.name
    with pytest.raises(mutations.WebInputError):
        mutations.transition_entries(
            notes,
            action="hold",
            filenames=[first.name],
            now=_NOW,
            actor_is_agent=True,
            skip_remote_sync=True,
        )
    assert processing.read_text(encoding="utf-8") == first_body

    mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=[first.name],
        now=_NOW,
        cooldown_days=3,
        skip_remote_sync=True,
    )
    returned = notes / "inbox" / first.name
    assert "cooldown_until: '2026-10-08T00:00:00+00:00'" in returned.read_text(encoding="utf-8")
    assert (notes / "processing" / second.name).read_text(encoding="utf-8") == second_body

    snapshot = returned.read_bytes()
    with pytest.raises(SystemExit):
        mutations.transition_entries(
            notes,
            action="return-to-inbox",
            filenames=[first.name],
            now=_NOW,
            cooldown_days=3,
            skip_remote_sync=True,
        )
    assert returned.read_bytes() == snapshot


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    size=st.integers(min_value=1, max_value=6),
    completed=st.integers(min_value=0, max_value=6),
    terminal_state=st.sampled_from(("adopted", "rejected")),
)
def test_dependency_order_matches_reference_model(
    size: int,
    completed: int,
    terminal_state: str,
    tmp_path: pathlib.Path,
) -> None:
    """依存鎖では終端済みの接頭辞に続く1件だけが処理可能になる。"""
    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        notes = _setup_notes(pathlib.Path(directory))
        completed = min(completed, size)
        names = [f"entry-{index}.md" for index in range(size)]
        for index, name in enumerate(names):
            path = _write_awi_file(notes, name)
            if index:
                path.write_text(
                    path.read_text(encoding="utf-8").replace(
                        "type: awi\n",
                        f"type: awi\ndepends_on: [{names[index - 1]}]\n",
                    ),
                    encoding="utf-8",
                )
        for name in names[:completed]:
            source = notes / "inbox" / name
            destination = notes / terminal_state / name
            destination.parent.mkdir(exist_ok=True)
            source.replace(destination)

        result = readiness.calculate_readiness(notes, "github.com/example/foo", now=_NOW)
        expected_ready = (names[completed],) if completed < size else ()
        assert result.ready == expected_ready
        assert set(result.blocked) == set(names[completed + 1 :])
        assert set(result.internal_dependency_waits) == set(names[completed + 1 :])
