import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location("ci_pytest_scope_test_subject", Path(__file__).with_name("ci_pytest_scope.py"))
assert _SPEC is not None and _SPEC.loader is not None
subject = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(subject)


def test_removed_notice_matches_unchanged_test_with_shorter_literal() -> None:
    diff = '-        "元の操作の結果を確認せよ。検査を先に行う。",\n+        "診断に従う。",\n'
    tests = {
        "hook_failures_test.py": 'def test_notice():\n    assert "元の操作の結果を確認せよ" in output\n',
        "other_test.py": 'def test_notice():\n    assert "現在の案内だけを返すこと" in output\n',
    }
    assert subject.matching_old_assertions(diff, tests) == ["hook_failures_test.py"]


def test_current_workflow_provides_both_scopes() -> None:
    workflow = _ROOT / ".github/workflows/ci.yaml"
    root = subject.load_scope(workflow, "root")
    toolkit = subject.load_scope(workflow, "toolkit")
    assert root["pathspecs"]
    assert toolkit["path"]
    assert toolkit["shards"]


def test_root_runs_workflow_pathspecs_and_preserves_file_names(monkeypatch, tmp_path) -> None:
    commands = []

    def git(argv, **kwargs):
        commands.append((argv, kwargs))
        return SimpleNamespace(stdout=b"a test.py\0nested/test_b.py\0")

    def pytest_command(argv, **kwargs):
        commands.append((argv, kwargs))
        return 0

    monkeypatch.setattr(subject.subprocess, "run", git)
    monkeypatch.setattr(subject.subprocess, "call", pytest_command)
    root = subject.load_scope(_ROOT / ".github/workflows/ci.yaml", "root")
    assert subject.run_scope("root", root, tmp_path, collect_only=True) == 0
    assert commands[0][0] == ["git", "ls-files", "-z", "--", *root["pathspecs"]]
    assert commands[1][0][1:] == [
        "-m",
        "pytest",
        *(arg for arg in root["pytest_args"] if arg != "-v"),
        "--collect-only",
        "-q",
        "--",
        "a test.py",
        "nested/test_b.py",
    ]
    assert commands[1][1]["env"]["UV_NO_SYNC"] == "true"


def test_toolkit_runs_every_shard_and_preserves_first_failure(monkeypatch, tmp_path) -> None:
    commands = []

    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        return 5 if len(commands) == 1 else 0

    monkeypatch.setattr(subject.subprocess, "call", run)
    selected = subject.load_scope(_ROOT / ".github/workflows/ci.yaml", "toolkit")
    assert subject.run_scope("toolkit", selected, tmp_path, collect_only=False) == 5
    assert len(commands) == len(selected["shards"])
    for (argv, kwargs), shard in zip(commands, selected["shards"], strict=True):
        assert argv[1:6] == ["-m", "pyfltr", "ci", "--no-always-targets", "--commands=pytest"]
        assert f"--test-shard={shard}" in argv[6]
        assert "--collect-only" not in argv[6]
        assert argv[7:] == [selected["path"]]
        assert kwargs["env"]["UV_NO_SYNC"] == "true"


@pytest.mark.parametrize("shards", [[], ["1/2"], ["1/2", "1/2"], ["1/3", "2/3"], ["1/1", 2]])
def test_toolkit_rejects_incomplete_or_invalid_matrix(shards) -> None:
    command = (
        "pyfltr ci --no-always-targets --commands=pytest "
        '--pytest-args="-v --junitxml=$junit --test-shard=$TEST_SHARD" agent-toolkit'
    )
    with pytest.raises(ValueError, match="shard"):
        subject._toolkit_scope(command, {"strategy": {"matrix": {"shard": shards}}})


def test_toolkit_never_evaluates_unknown_shell(tmp_path) -> None:
    marker = tmp_path / "executed"
    command = (
        "pyfltr ci --no-always-targets --commands=pytest "
        f'--pytest-args="-v --junitxml=$junit --test-shard=$TEST_SHARD" "$(touch {marker})"'
    )
    with pytest.raises(ValueError, match="形式|対象"):
        subject._toolkit_scope(command, {"strategy": {"matrix": {"shard": ["1/1"]}}})
    assert not marker.exists()


def test_toolkit_collect_prints_ids_from_the_exact_run(monkeypatch, tmp_path, capsys) -> None:
    def run(argv, **kwargs):
        if argv[2] == "pyfltr" and argv[3] == "ci":
            assert "--collect-only" in argv[6] and "-v " not in argv[6]
            return SimpleNamespace(returncode=0, stdout='{"kind":"header","run_id":"test-run"}\n', stderr="")
        assert argv[3:5] == ["show-run", "test-run"]
        return SimpleNamespace(stdout='{"content":"module_test.py::test_case\\n1 test collected"}\n')

    monkeypatch.setattr(subject.subprocess, "run", run)
    selected = {"pytest_args": ["-v"], "shards": ["1/1"], "path": "agent-toolkit"}
    assert subject.run_scope("toolkit", selected, tmp_path, collect_only=True) == 0
    assert "1/1\tmodule_test.py::test_case" in capsys.readouterr().out


def test_root_only_selects_a_difference_when_the_history_and_ancestor_exist(monkeypatch, tmp_path) -> None:
    base = "a" * 40
    history = tmp_path / ".testscope"
    history.mkdir()
    (history / "map.json").write_text("{}", encoding="utf-8")
    selected = {"pathspecs": ["*_test.py"], "pytest_args": ["-v", "--maxfail=0"]}
    monkeypatch.setattr(subject.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=b"case_test.py\0"))
    with pytest.raises(ValueError, match="履歴"):
        subject.run_scope("root", selected, tmp_path, collect_only=True, impact_base=base)
    (history / "pyfltr.json").write_text("{}", encoding="utf-8")
    calls = []

    def call(argv, **kwargs):
        calls.append(argv)
        if argv[0] != "git":
            config = Path(kwargs["env"]["PYFLTR_GLOBAL_CONFIG"])
            assert 'pytest-targets = ["case_test.py"]' in config.read_text(encoding="utf-8")
        return 0

    monkeypatch.setattr(subject.subprocess, "call", call)

    def collect(command, _root, env):
        call(command, env=env)
        return 0, ["case_test.py::test_case"]

    monkeypatch.setattr(subject, "_collect_pyfltr", collect)
    assert subject.run_scope("root", selected, tmp_path, collect_only=True, impact_base=base) == 0
    assert calls[0] == ["git", "merge-base", "--is-ancestor", base, "HEAD"]
    assert f"--changed-since={base}" in calls[1]

    monkeypatch.setattr(subject, "_collect_pyfltr", lambda *_args: (0, []))
    fallback = []

    def record_fallback(argv, **kwargs):
        fallback.append((argv, kwargs))
        return 0

    monkeypatch.setattr(subject.subprocess, "call", record_fallback)
    assert subject.run_scope("root", selected, tmp_path, collect_only=True, impact_base=base) == 0
    assert fallback[-1][0][-2:] == ["--", "case_test.py"]
    assert fallback[-1][1]["env"]["TESTSCOPE"] == "off"
