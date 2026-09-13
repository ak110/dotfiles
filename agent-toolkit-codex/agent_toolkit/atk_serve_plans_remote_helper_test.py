import importlib.util
import os
import pathlib

import pytest

_HELPER_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "atk_serve_plans_remote_helper.py"
_SPEC = importlib.util.spec_from_file_location("atk_serve_plans_remote_helper", _HELPER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
helper = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(helper)


@pytest.mark.parametrize("name", ("p.detail.md", "p.plan-review.tsv"))
def test_working_root_excludes_removed_attachments(tmp_path: pathlib.Path, name: str) -> None:
    """リモート側も作業rootの旧付属ファイルを対象にしない。"""
    root = tmp_path / "plans"
    root.mkdir()
    path = root / name
    path.write_text("legacy\n", encoding="utf-8")

    assert not helper._is_target_path(path, root, helper.LEGACY_SOURCE_ID)  # pylint: disable=protected-access
    assert helper._is_target_path(path, root, helper.NEW_SOURCE_ID)  # pylint: disable=protected-access


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
