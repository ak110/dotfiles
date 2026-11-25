from pathlib import Path

import capture_lane_generated as capture
import pytest
from agent_toolkit._atk.lane_cleanup import PROJECTION_MANIFEST, _hashed_output_blocker


def test_records_only_outputs_from_owned_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    monkeypatch.chdir(tree)
    temp = tmp_path / "generated"
    monkeypatch.setattr(capture.managed_temp, "temp_root_path", lambda: temp)

    def generate(kind: str, worktree: Path, output: Path, arguments: list[str]) -> int:
        assert kind == "hypothesis" and worktree == tree and arguments == ["test_example.py"]
        assert output.is_relative_to(temp) and not list(output.iterdir())
        (output / "machine").write_text("生成物", encoding="utf-8")
        (output / "empty").mkdir()
        return 0

    monkeypatch.setattr(capture, "_produce", generate)
    assert capture.main(["hypothesis", "test_example.py"]) == 0
    target = tree / ".hypothesis"
    assert (target / PROJECTION_MANIFEST).is_file()
    assert _hashed_output_blocker(tree, Path(".hypothesis")) is None
    (target / "machine").write_text("追記", encoding="utf-8")
    assert capture.main(["hypothesis", "test_example.py"]) == 2
    assert (target / "machine").read_text(encoding="utf-8") == "追記"
    assert not list(temp.iterdir())
