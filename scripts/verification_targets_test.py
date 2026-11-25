import json
import pathlib
import shutil
import subprocess
import sys

import pytest
import verification_targets
from agent_toolkit._testing.git_repository import commit_all, init_repository
from agent_toolkit._testing.lane_integration_support import _integration_args, _integration_selection
from agent_toolkit._testing.lane_support import _create, _invoke
from pyfltr_precommit import main as precommit_main
from verification_targets import consumer_tests, required_tests, uncovered_tests


def test_direct_and_named_consumers(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, body in {
        "source.py": "",
        "middle.py": "import source as alias",
        "other_test.py": "import source as alias",
        "indirect_test.py": "from middle import alias",
        "source_interval_test.py": "",
        "mention_test.py": "resource = 'source'",
        "pkg/mod.py": "",
        "pkg_from_test.py": "from root.pkg import (\n    other,\n    mod,\n)",
        "pkg_dotted_test.py": "monkeypatch.setattr('root.pkg.mod.value', 1)",
        "pkg_other_test.py": "from root.other import mod",
    }.items():
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    commit_all(tmp_path, "init")
    snapshot = consumer_tests(tmp_path, ["source.py"])
    assert [(item["test"], item["required"]) for item in snapshot["candidates"]] == [
        ("mention_test.py", False),
        ("other_test.py", True),
        ("source_interval_test.py", False),
    ]
    with pytest.raises(ValueError, match="意味判定が不足"):
        required_tests(snapshot)
    review = {
        **snapshot,
        "decisions": [
            {"source": "source.py", "test": "mention_test.py", "include": False, "reason": "単なる文字列参照"},
            {
                "source": "source.py",
                "test": "source_interval_test.py",
                "include": True,
                "reason": "名前で対応する契約を検査する",
            },
        ],
    }
    assert required_tests(snapshot, review) == ["other_test.py", "source_interval_test.py"]
    package = consumer_tests(tmp_path, ["pkg/mod.py"])
    assert {row["test"] for row in package["candidates"]} == {"pkg_dotted_test.py", "pkg_from_test.py", "pkg_other_test.py"}
    assert not any(row["required"] for row in package["candidates"])
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


@pytest.mark.parametrize(
    ("path", "original", "changed", "expected"),
    [
        (
            "agent-toolkit/skills/atk-mode/SKILL.md",
            "# 前\n",
            "# 次\n",
            ["markdown_link_anchor_invariant_test.py", "norm_size_budget_invariant_test.py", "retired_terms_invariant_test.py"],
        ),
        (
            "agent-toolkit/agent_toolkit/_atk/run_skill.py",
            "",
            'commands.add_parser("sample")\n',
            ["agent-toolkit/agent_toolkit/atk_help_test.py", "markdown_link_anchor_invariant_test.py"],
        ),
        ("pytools/process_example.py", "", "child.terminate()\n", ["subprocess_stop_invariant_test.py"]),
        ("scripts/message.py", "", 'MESSAGE = "変更"\n', ["retired_terms_invariant_test.py"]),
        ("scripts/unrelated.py", "", "VALUE = 2\n", []),
    ],
)
def test_integration_argv_covers_cross_contracts_even_if_sources_are_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    path: str,
    original: str,
    changed: str,
    expected: list[str],
) -> None:
    root = init_repository(tmp_path / "repo", files={path: original, "one_test.py": ""}, commit_message="init")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    (root / path).write_text(changed, encoding="utf-8")
    assert required_tests(consumer_tests(root, [], base=base)) == expected
    monkeypatch.chdir(root)
    planned = ["run", "--no-always-targets", "--commands=pytest", "one_test.py"]
    assert precommit_main(["--check-argv", json.dumps(planned), "--against", base]) == bool(expected)
    err = capsys.readouterr().err
    assert all(test in err for test in expected)


@pytest.mark.parametrize("remove_policy", [False, True])
def test_individually_valid_norm_changes_fail_on_combined_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], remove_policy: bool
) -> None:
    source = pathlib.Path(__file__).resolve().parents[1] / "norm_size_budget_invariant_test.py"
    root, selection, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selection, monkeypatch, root)
    worktree = pathlib.Path(record["worktree"])
    documents = pathlib.Path("agent-toolkit/skills/atk-mode")
    (worktree / documents).mkdir(parents=True)
    (worktree / documents / "base.md").write_text("A" * (3_150_000 - 16), encoding="ascii")
    shutil.copyfile(source, worktree / source.name)
    verification = [
        sys.executable,
        "-c",
        "import runpy,sys; runpy.run_path(sys.argv[1])['test_norm_markdown_within_budget']()",
        source.name,
    ]
    policy = worktree / "pyproject.toml"
    policy.write_text(
        "[tool.agent-toolkit.lane-integration]\nverification = " + json.dumps(verification) + "\n", encoding="utf-8"
    )
    base = commit_all(worktree, "共通基点")
    subprocess.run(["git", "merge", "--ff-only", base], cwd=root, check=True, capture_output=True)

    def accepted_change(directory: pathlib.Path, name: str) -> str:
        (directory / documents / name).write_text("A" * 10, encoding="ascii")
        candidate = commit_all(directory, name)
        assert subprocess.run(verification, cwd=directory, check=False, capture_output=True).returncode == 0
        return candidate

    first = accepted_change(root, "first.md")
    if remove_policy:
        policy.write_text("[tool]\n", encoding="utf-8")
    second = accepted_change(worktree, "second.md")
    manifest = tmp_path / "manifest.json"
    handoff = tmp_path / "handoff.md"
    handoff.write_text("受入試験", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "verification": [[sys.executable, "-c", "pass"]],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    code, _, error = _invoke(capsys, *_integration_args(record, root, manifest, second))
    assert code == 2 and "verification-project-0が終了1" in error
    current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    assert current == first
    assert not (root / documents / "second.md").exists()


def test_documents_and_registered_stop_modules_reach_indirect_consumers(tmp_path: pathlib.Path) -> None:
    toolkit = "agent-toolkit/agent_toolkit"
    hook = f"{toolkit}/_hooks/bugfix_gate.py"
    document = "guide/process-wi-selection.md"
    root = init_repository(
        tmp_path / "repo",
        files={
            document: "",
            f"{toolkit}/__init__.py": "",
            f"{toolkit}/_testing/__init__.py": "",
            f"{toolkit}/_testing/support.py": "SOURCE = 'process-wi-selection.md'",
            f"{toolkit}/_hooks/__init__.py": "",
            f"{toolkit}/_hooks/stop.py": 'CHECK_MODULE_NAMES = ("bugfix_gate",)',
            hook: "",
            "reader_test.py": "from agent_toolkit._testing import support",
            "user_response_advisor_test.py": "from agent_toolkit._hooks import stop",
            "delivered_repost_gate_test.py": "import agent_toolkit._hooks.stop",
            "unrelated_test.py": "SOURCE = 'other-process-wi-selection.md'",
        },
        commit_message="init",
    )
    candidates = consumer_tests(root, [document, hook])["candidates"]
    assert [(c["source"], c["test"], c["required"], c["reasons"]) for c in candidates] == [
        (hook, "delivered_repost_gate_test.py", False, ["hook-entry-reference"]),
        (document, "reader_test.py", False, ["test-support-reference"]),
        (hook, "user_response_advisor_test.py", False, ["hook-entry-reference"]),
    ]


def test_invalid_source_and_unreadable_tracked_source(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "keep.txt").write_text("", encoding="utf-8")
    commit_all(tmp_path, "init")
    with pytest.raises(ValueError):
        consumer_tests(tmp_path, ["missing.py"])
    source = tmp_path / "bad_test.py"
    source.write_text("def (", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    assert not consumer_tests(tmp_path, [])["candidates"]
    module = tmp_path / "source.py"
    module.write_text("", encoding="utf-8")
    with pytest.raises(SyntaxError):
        consumer_tests(tmp_path, ["source.py"])
    with pytest.raises(ValueError):
        uncovered_tests(tmp_path, ["a_test.py"], [str(tmp_path.parent)])


def test_tracked_test_deleted_in_worktree_matches_staged_deletion(tmp_path: pathlib.Path) -> None:
    root = init_repository(
        tmp_path / "repo",
        files={"source.py": "", "kept_test.py": "import source\n", "deleted_test.py": "import source\n"},
        commit_message="init",
    )
    (root / "deleted_test.py").unlink()
    unstaged = consumer_tests(root, ["source.py"])
    assert [row["test"] for row in unstaged["candidates"]] == ["kept_test.py"]
    subprocess.run(["git", "-C", str(root), "rm", "-q", "--cached", "--", "deleted_test.py"], check=True)
    assert consumer_tests(root, ["source.py"]) == unstaged


def test_package_imports_and_ambiguous_references(tmp_path: pathlib.Path) -> None:
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
    snapshot = {
        "head": "a",
        "sources": ["x.py"],
        "candidates": [{"source": "x.py", "test": "x_test.py", "required": False, "reasons": ["test-name"]}],
    }
    with pytest.raises(ValueError):
        required_tests(snapshot, {**snapshot, "decisions": decisions})
