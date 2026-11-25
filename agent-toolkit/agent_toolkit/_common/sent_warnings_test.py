"""送達した報告への警告を呼出しごとに預け、一度だけ取り出せることを確かめる。 pyfltr: allow-comment"""

import pathlib
import tempfile

import pytest

from agent_toolkit._common import sent_warnings
from agent_toolkit._common.session_state import state_path

_WARN = '<atk-auto source="send-to-user" kind="warn">\n本文は表示済みである。\n</atk-auto>'


@pytest.fixture(autouse=True)
def _redirect_tempdir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    state_path("sid").parent.mkdir(parents=True, exist_ok=True)


def test_take_returns_stashed_warnings_once() -> None:
    sent_warnings.stash("sid", "toolu_1", [_WARN])
    sent_warnings.stash("sid", "toolu_2", ["別の警告"])
    assert sent_warnings.take("sid", "toolu_1") == [_WARN]
    assert not sent_warnings.take("sid", "toolu_1")
    assert sent_warnings.take("sid", "toolu_2") == ["別の警告"]


def test_held_warnings_are_bounded() -> None:
    for index in range(30):
        sent_warnings.stash("sid", f"toolu_{index}", [f"警告{index}"])
    assert not sent_warnings.take("sid", "toolu_0")
    assert sent_warnings.take("sid", "toolu_29") == ["警告29"]
