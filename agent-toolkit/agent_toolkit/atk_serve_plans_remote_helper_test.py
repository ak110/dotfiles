import importlib.util
import pathlib
import subprocess

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


def test_resolve_private_notes_waits_for_atk_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_notes = tmp_path / "private-notes"
    monkeypatch.setattr(helper, "_atk_executable", lambda: "/tmp/atk")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["/tmp/atk", "config", "get", "private_notes"]
        assert isinstance(kwargs["timeout"], int) and kwargs["timeout"] > 8
        return subprocess.CompletedProcess(command, 0, f"{private_notes}\n", "")

    monkeypatch.setattr(helper.subprocess, "run", run)

    path, warning = helper._resolve_private_notes_result()  # pylint: disable=protected-access

    assert path == private_notes.resolve()
    assert warning is None
    assert capsys.readouterr().err == ""
