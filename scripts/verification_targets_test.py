"""候補探索と実行対象の被覆を確かめる。"""

import pathlib
import subprocess

import pytest
import verification_targets
from verification_targets import consumer_tests, uncovered_tests


def test_direct_and_named_consumers(tmp_path: pathlib.Path) -> None:
    """別名の直接importと命名候補を保持し、中間importは手動探索へ委ねる。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, body in {
        "source.py": "",
        "middle.py": "import source as alias",
        "other_test.py": "import source as alias",
        "indirect_test.py": "from middle import alias",
        "source_interval_test.py": "",
    }.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    assert consumer_tests(tmp_path, ["source.py"]) == ["other_test.py", "source_interval_test.py"]
    assert consumer_tests(tmp_path, [str(tmp_path / "source.py")]) == consumer_tests(tmp_path, ["source.py"])
    assert uncovered_tests(tmp_path, ["other_test.py"], ["source.py"]) == ["other_test.py"]
    assert uncovered_tests(tmp_path, ["other_test.py"], [str(tmp_path)]) == []
    assert uncovered_tests(tmp_path, ["other_test.py"], []) == []
    with pytest.raises(ValueError):
        consumer_tests(tmp_path, ["../outside.py"])


def test_empty_result_and_failed_listing_are_distinct(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正常な空集合とGitの取得失敗を区別する。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert consumer_tests(tmp_path, []) == []
    monkeypatch.setattr(
        verification_targets.git_command, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 1, b"", b"failure")
    )
    with pytest.raises(ValueError, match="追跡ファイルを取得できない"):
        consumer_tests(tmp_path, [])


def test_invalid_source_and_unreadable_tracked_source(tmp_path: pathlib.Path) -> None:
    """不在入力と追跡試験の欠損を空集合へ変換しない。構文解析は行わない。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(ValueError):
        consumer_tests(tmp_path, ["missing.py"])
    source = tmp_path / "bad_test.py"
    source.write_text("def (", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    assert consumer_tests(tmp_path, []) == []
    source.unlink()
    module = tmp_path / "source.py"
    module.write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        consumer_tests(tmp_path, ["source.py"])
    with pytest.raises(ValueError):
        uncovered_tests(tmp_path, ["a_test.py"], [str(tmp_path.parent)])
