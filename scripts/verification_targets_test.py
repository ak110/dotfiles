"""候補探索と実行対象の被覆を確かめる。"""

import pathlib
import subprocess

import pytest
import verification_targets
from agent_toolkit._testing.git_repository import commit_all, init_repository
from verification_targets import consumer_tests, required_tests, uncovered_tests


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
    commit_all(tmp_path, "init")
    snapshot = consumer_tests(tmp_path, ["source.py"])
    assert [(item["test"], item["required"]) for item in snapshot["candidates"]] == [
        ("other_test.py", True),
        ("source_interval_test.py", False),
    ]
    with pytest.raises(ValueError, match="意味判定が不足"):
        required_tests(snapshot)
    review = {
        **snapshot,
        "decisions": [
            {
                "source": "source.py",
                "test": "source_interval_test.py",
                "include": True,
                "reason": "名前で対応する契約を検査する",
            }
        ],
    }
    assert required_tests(snapshot, review) == ["other_test.py", "source_interval_test.py"]
    assert consumer_tests(tmp_path, [str(tmp_path / "source.py")]) == consumer_tests(tmp_path, ["source.py"])
    assert uncovered_tests(tmp_path, ["other_test.py"], ["source.py"]) == ["other_test.py"]
    assert uncovered_tests(tmp_path, ["other_test.py"], [str(tmp_path)]) == []
    assert uncovered_tests(tmp_path, ["other_test.py"], []) == []
    with pytest.raises(ValueError):
        consumer_tests(tmp_path, ["../outside.py"])


def test_empty_result_and_failed_listing_are_distinct(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正常な空集合とGitの取得失敗を区別する。"""
    init_repository(tmp_path, files={"keep.txt": ""}, commit_message="init")
    assert not consumer_tests(tmp_path, [])["candidates"]
    monkeypatch.setattr(
        verification_targets.git_command, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 1, b"", b"failure")
    )
    with pytest.raises(ValueError, match="追跡ファイルを取得できない"):
        consumer_tests(tmp_path, [])


def test_invalid_source_and_unreadable_tracked_source(tmp_path: pathlib.Path) -> None:
    """対象なしでは構文を要しないが、不在入力と追跡試験の欠損は拒否する。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "keep.txt").write_text("", encoding="utf-8")
    commit_all(tmp_path, "init")
    with pytest.raises(ValueError):
        consumer_tests(tmp_path, ["missing.py"])
    source = tmp_path / "bad_test.py"
    source.write_text("def (", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    assert not consumer_tests(tmp_path, [])["candidates"]
    source.unlink()
    module = tmp_path / "source.py"
    module.write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        consumer_tests(tmp_path, ["source.py"])
    with pytest.raises(ValueError):
        uncovered_tests(tmp_path, ["a_test.py"], [str(tmp_path.parent)])


def test_package_imports_and_ambiguous_references(tmp_path: pathlib.Path) -> None:
    """別package、別名、CLI、動的/中間利用の根拠を消費側判定まで保つ。"""
    root = init_repository(
        tmp_path / "repo",
        files={
            "pkg/__init__.py": "",
            "pkg/atk.py": "",
            "other/__init__.py": "",
            "other/atk.py": "",
            "alias_test.py": "from pkg import atk as alias\nalias.main()\n",
            "different_test.py": "from other import atk\n",
            "dynamic_test.py": "import importlib\nimportlib.import_module('pkg.atk')\n",
            "cli_test.py": "command = ['python', 'pkg/atk.py']\n",
            "middle.py": "from pkg import atk\n",
            "indirect_test.py": "from middle import atk\n",
        },
        commit_message="init",
    )
    snapshot = consumer_tests(root, ["pkg/atk.py"])
    candidates = {row["test"]: row for row in snapshot["candidates"]}
    assert candidates["alias_test.py"]["required"] is True
    assert candidates["different_test.py"]["required"] is False
    assert candidates["dynamic_test.py"]["required"] is False
    assert "path-reference" in candidates["cli_test.py"]["reasons"]
    review = {
        **snapshot,
        "decisions": [
            {
                "source": "pkg/atk.py",
                "test": name,
                "include": name != "different_test.py",
                "reason": "公開CLI/動的/中間利用を確認" if name != "different_test.py" else "別packageの同名モジュール",
            }
            for name, row in candidates.items()
            if not row["required"]
        ],
    }
    assert required_tests(snapshot, review) == ["alias_test.py", "cli_test.py", "dynamic_test.py", "indirect_test.py"]
    commit_all(root, "new head")
    with pytest.raises(ValueError, match="一致しない"):
        required_tests(consumer_tests(root, ["pkg/atk.py"]), review)


@pytest.mark.parametrize(
    "decisions",
    [
        [],
        [{"source": "x.py", "test": "x_test.py", "include": False, "reason": ""}],
        [{"source": "x.py", "test": "x_test.py", "include": 0, "reason": "wrong type"}],
    ],
)
def test_incomplete_decisions_are_not_confirmation(decisions: list[dict[str, object]]) -> None:
    """欠落・空理由・数値の真偽値を確認済みへ変換しない。"""
    snapshot = {
        "head": "a",
        "sources": ["x.py"],
        "candidates": [{"source": "x.py", "test": "x_test.py", "required": False, "reasons": ["test-name"]}],
    }
    with pytest.raises(ValueError):
        required_tests(snapshot, {**snapshot, "decisions": decisions})
