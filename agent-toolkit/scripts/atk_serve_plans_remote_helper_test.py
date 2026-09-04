import os
import pathlib

import atk_serve_plans_remote_helper as helper
import pytest


@pytest.mark.parametrize("delay", [5, 8])
def test_resolve_private_notes_waits_for_atk_startup(
    delay: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_notes = tmp_path / "private-notes"
    executable = tmp_path / "atk"
    executable.write_text(
        f"#!/bin/sh\nsleep {delay}\nprintf '%s\\n' '{private_notes}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(helper, "_atk_executable", lambda: os.fspath(executable))

    path, warning = helper._resolve_private_notes_result()  # pylint: disable=protected-access

    assert path == private_notes.resolve()
    assert warning is None
    assert capsys.readouterr().err == ""


def test_private_notes_timeout_exceeds_previous_limit() -> None:
    assert helper._PRIVATE_NOTES_TIMEOUT_SEC != 5  # pylint: disable=protected-access
