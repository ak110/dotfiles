"""commitのhookがpytestを再利用する条件と、再利用しない場合の全体実行を確かめる。"""

import datetime
import json
import os
import pathlib
import subprocess
import time
from typing import Any

import pyfltr_precommit
import pytest
from agent_toolkit._testing.git_repository import commit_all, init_repository

_VERSION = "3.30.1"
# 整形段がファイルを書き直す実行を表すため、実行の開始は作業ツリーの変更より前に置く。
_RUN_STARTED_AT = "2000-01-01T00:00:00+00:00"


def _repository(tmp_path: pathlib.Path) -> pathlib.Path:
    repository = init_repository(tmp_path / "repo")
    (repository / "src").mkdir()
    (repository / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(repository, "init")
    return repository


def _iso(offset_seconds: float) -> str:
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=offset_seconds)).isoformat()


@pytest.mark.parametrize("failure", ["missing-file", "git-listing"])
def test_consumer_failure_reports_diagnostic_and_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    """公開候補操作の失敗は診断を保持し、同じ操作の再試行を案内する。"""
    repository = _repository(tmp_path)
    monkeypatch.chdir(repository)
    source = "src/missing.py" if failure == "missing-file" else "src/a.py"
    diagnostic = source if failure == "missing-file" else "listing-failed"
    if failure == "git-listing":
        monkeypatch.setattr(
            pyfltr_precommit.git_command,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess([], 1, b"", b"listing-failed"),
        )
    assert pyfltr_precommit.main(["--consumer-tests", source]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert diagnostic in captured.err
    assert "次の操作:" in captured.err
    assert "--consumer-tests" in captured.err


@pytest.mark.parametrize(
    "targets, expected",
    [(["src/a.py"], 1), (["src/a.py", "src/consumer_test.py"], 0), (["src"], 0), ([], 0)],
)
@pytest.mark.parametrize("options", [["--output-format", "json"], ["--output-format=json"], []])
def test_verify_run_discovers_consumers_from_public_argv(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, targets: list[str], expected: int, options: list[str]
) -> None:
    """標準の統合呼出しが実ファイルの候補不足を拒否し、明示試験・親ディレクトリ・全体を受理する。"""
    repository = _repository(tmp_path)
    (repository / "src" / "consumer_test.py").write_text("import a as alias\n", encoding="utf-8")
    commit_all(repository, "consumer")
    monkeypatch.chdir(repository)
    monkeypatch.setattr(pyfltr_precommit.shutil, "which", lambda name: name)
    _install_pyfltr(monkeypatch, repository, pytest_started_at=_iso(60), argv=["run", *options, *targets])
    assert pyfltr_precommit.main(["--verify-run", "01RUN"]) == expected


def test_consumer_and_verify_modes_are_exclusive() -> None:
    """併用によるverify-runの黙った無視を防ぐ。"""
    with pytest.raises(SystemExit) as exc:
        pyfltr_precommit.main(["--consumer-tests", "--verify-run", "01RUN"])
    assert exc.value.code == 2


@pytest.mark.parametrize("option", ["--pytest-args=-v", "--pytest-args=-v -rA --maxfail=10", "--only-failed"])
def test_planned_argv_rejects_same_non_neutral_input_as_verify(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    """長い試験を起動する前と実受理で同じ引数の拒否理由を取得する。"""
    repository = _repository(tmp_path)
    monkeypatch.chdir(repository)
    planned = ["run", option, "src/a.py"]
    assert pyfltr_precommit.main(["--check-argv", json.dumps(planned)]) == 1
    diagnostic = capsys.readouterr().err
    assert option in diagnostic
    assert "次の操作:" in diagnostic and "--check-argv" in diagnostic
    _install_pyfltr(monkeypatch, repository, pytest_started_at=_iso(60), argv=planned)
    result = pyfltr_precommit.find_reusable_run(repository, "pyfltr", run_id="01RUN")
    assert result.run_id is None
    assert option in result.reason


def test_ambiguous_candidate_proof_is_shared_by_preflight_and_verify(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """偶然一致の除外理由は受理するが、欠落・古い候補の判定は受理しない。"""
    repository = _repository(tmp_path)
    (repository / "unrelated_test.py").write_text("# a.pyという名称への言及だけ\n", encoding="utf-8")
    commit_all(repository, "candidate")
    monkeypatch.chdir(repository)
    planned = ["run", "src/a.py"]
    assert pyfltr_precommit.main(["--check-argv", json.dumps(planned)]) == 1
    snapshot = pyfltr_precommit.consumer_tests(repository, ["src/a.py"])
    proof = tmp_path / "proof.json"
    review = {
        **snapshot,
        "decisions": [
            {
                "source": "src/a.py",
                "test": "unrelated_test.py",
                "include": False,
                "reason": "コメントでのみ名称へ言及し、対象の処理を利用しない",
            }
        ],
    }
    proof.write_text(json.dumps(review), encoding="utf-8")
    assert pyfltr_precommit.main(["--check-argv", json.dumps(planned), "--consumer-review", str(proof)]) == 0
    assert "未確認" in capsys.readouterr().out
    _install_pyfltr(monkeypatch, repository, pytest_started_at=_iso(60), argv=planned)
    assert pyfltr_precommit.find_reusable_run(repository, "pyfltr", run_id="01RUN", review=review).run_id == "01RUN"
    (repository / "unrelated_test.py").write_text("# 別の内容でa.pyに言及\n", encoding="utf-8")
    assert pyfltr_precommit.main(["--check-argv", json.dumps(planned), "--consumer-review", str(proof)]) == 1
    diagnostic = capsys.readouterr().err
    assert "次の操作:" in diagnostic and "--consumer-tests" in diagnostic and "--consumer-review" in diagnostic


@pytest.mark.parametrize("value", ["{}", "[1]", "null", "invalid"])
def test_preflight_rejects_invalid_argv_json(value: str) -> None:
    """公開境界で不正な入力を実行済みの成功へ変換しない。"""
    assert pyfltr_precommit.main(["--check-argv", value]) == 2


@pytest.mark.parametrize("planned", [["fast"], ["run", "--commands=ruff-check"], ["run", "--commands", "ruff-check"]])
def test_preflight_rejects_known_pytest_scope_omissions(planned: list[str]) -> None:
    """不変条件だけのfastやpytestを外す指定を最終検証として受理しない。"""
    assert pyfltr_precommit.main(["--check-argv", json.dumps(planned)]) == 1


def _install_pyfltr(
    monkeypatch: pytest.MonkeyPatch,
    repository: pathlib.Path,
    *,
    pytest_started_at: str,
    argv: list[str] | None = None,
    cwd: pathlib.Path | str | None = None,
    version: str = _VERSION,
    pytest_status: str = "succeeded",
    list_returncode: int = 0,
    launch_boundary_at: str | None = None,
) -> list[list[str]]:
    """pyfltrの照会コマンドの公開出力を差し替え、呼び出された引数を記録する。"""
    calls: list[list[str]] = []
    pytest_command: dict[str, str] = {"command": "pytest", "status": pytest_status, "started_at": pytest_started_at}
    if launch_boundary_at is not None:
        pytest_command["launch_boundary_at"] = launch_boundary_at
    run = {
        "run_id": "01RUN",
        "started_at": _RUN_STARTED_AT,
        "finished_at": _iso(120),
        "exit_code": 0,
        "commands": ["ruff-check", "pytest"],
    }
    shown = {
        "run_id": "01RUN",
        "meta": {
            "cwd": str(repository if cwd is None else cwd),
            "version": version,
            "argv": argv if argv is not None else ["run", "--no-fix", "src/a.py"],
            "started_at": _RUN_STARTED_AT,
        },
        "commands": [{"command": "ruff-check", "status": "succeeded"}, pytest_command],
    }

    def _fake(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(cmd[1:])
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, f"pyfltr {_VERSION}\n", "")
        if cmd[1] == "list-runs":
            return subprocess.CompletedProcess(
                cmd, list_returncode, json.dumps({"runs": [run]}), "broken" if list_returncode else ""
            )
        return subprocess.CompletedProcess(cmd, 0, json.dumps(shown), "")

    monkeypatch.setattr(pyfltr_precommit, "run_subprocess", _fake)
    return calls


def test_reuses_successful_pytest_on_unchanged_tree(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pytestの開始後に作業ツリーが変わっていなければ、整形段より前の実行開始によらずpytestを再利用する。"""
    repository = _repository(tmp_path)
    _install_pyfltr(
        monkeypatch, repository, pytest_started_at=_iso(60), argv=["run", "--output-format", "jsonl", "--commands=pytest"]
    )

    assert pyfltr_precommit.find_reusable_run(repository, "pyfltr") == pyfltr_precommit.Reuse("01RUN", "")


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["run", "--pytest-args=-n 2 --maxfail=10"], id="integration-values"),
        pytest.param(["run", "--pytest-args", "--numprocesses=auto"], id="separate-value"),
    ],
)
def test_reuses_run_with_parallelism_and_maxfail_only(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """並列数と打ち切り数だけを変える`--pytest-args`は試験の範囲を変えないため再利用する。"""
    repository = _repository(tmp_path)
    _install_pyfltr(monkeypatch, repository, pytest_started_at=_iso(60), argv=argv)

    assert pyfltr_precommit.find_reusable_run(repository, "pyfltr") == pyfltr_precommit.Reuse("01RUN", "")


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"pytest_started_at": _iso(-3600)}, id="changed-after-pytest-start"),
        pytest.param({"pytest_status": "failed"}, id="pytest-failed"),
        pytest.param({"version": "3.29.0"}, id="other-version"),
        pytest.param({"argv": ["run", "--pytest-args=-k one"]}, id="narrowed-pytest"),
        pytest.param({"argv": ["run", "--pytest-args=-n 2 -k one"]}, id="narrowed-with-parallelism"),
        pytest.param({"argv": ["run", "--pytest-args=--maxfail=auto"]}, id="non-numeric-maxfail"),
        pytest.param({"argv": ["run", "--only-failed"]}, id="only-failed"),
        pytest.param({"argv": ["run", "--commands"]}, id="option-without-value"),
        pytest.param({"argv": ["grep", "x"]}, id="other-subcommand"),
        pytest.param({"cwd_other": True}, id="other-worktree"),
        pytest.param({"cwd": ""}, id="empty-cwd"),
        pytest.param({"list_returncode": 1}, id="query-failed"),
    ],
)
def test_runs_pytest_unless_every_condition_holds(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    """条件のどれかを満たさない実行や、照会の失敗では再利用しない。"""
    repository = _repository(tmp_path)
    options: dict[str, Any] = {"pytest_started_at": _iso(60)}
    if overrides.pop("cwd_other", False):
        options["cwd"] = tmp_path
    options.update(overrides)
    _install_pyfltr(monkeypatch, repository, **options)

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr")

    assert reuse.run_id is None
    assert reuse.reason


@pytest.mark.parametrize(
    "change",
    [
        pytest.param("modify", id="unstaged-modification"),
        pytest.param("delete", id="unstaged-deletion"),
    ],
)
def test_worktree_differing_from_index_is_not_reused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """commit対象のindexと作業ツリーが異なる場合は、pyfltrを照会せずに全体を実行する。"""
    repository = _repository(tmp_path)
    target = repository / "src" / "a.py"
    if change == "modify":
        target.write_text("x = 2\n", encoding="utf-8")
    else:
        target.unlink()
    calls = _install_pyfltr(monkeypatch, repository, pytest_started_at=_iso(60))

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr")

    assert reuse.run_id is None
    assert not calls


@pytest.mark.parametrize(
    ("reuse", "expected_extra"),
    [
        (pyfltr_precommit.Reuse("01RUN", ""), ["--disable", "pytest"]),
        (pyfltr_precommit.Reuse(None, "理由"), []),
    ],
)
def test_main_disables_only_pytest_when_reusing(
    monkeypatch: pytest.MonkeyPatch, reuse: pyfltr_precommit.Reuse, expected_extra: list[str]
) -> None:
    """再利用する場合だけpytestを外し、他のチェックと終了コードはpyfltrのものを返す。"""
    launched: list[list[str]] = []

    def _find(directory: pathlib.Path, pyfltr: str) -> pyfltr_precommit.Reuse:
        del directory, pyfltr
        return reuse

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        launched.append(cmd)
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(pyfltr_precommit.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pyfltr_precommit, "current_branch", lambda directory: "develop")
    monkeypatch.setattr(pyfltr_precommit, "find_reusable_run", _find)
    monkeypatch.setattr(pyfltr_precommit.subprocess, "run", _run)

    assert pyfltr_precommit.main(["src/a.py"]) == 3
    assert launched == [["/bin/pyfltr", "fast", *expected_extra, "src/a.py"]]


@pytest.mark.parametrize(("add_untracked", "reused"), [(False, True), (True, False)], ids=["unchanged", "untracked-added"])
def test_untracked_file_added_after_pytest_start_is_not_reused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, add_untracked: bool, reused: bool
) -> None:
    """pytestの開始後に加えた未追跡ファイルは、その更新時刻で検出して再利用しない。"""
    repository = _repository(tmp_path)
    # 作成した作業ツリーの更新時刻より後、追加するファイルの更新時刻より前にpytestが始まった実行を表す。
    # 秒未満を記録しないファイルシステムでも作業ツリーの作成と区別できるよう、次の秒に入ってから開始とする。
    time.sleep(1.05 - time.time() % 1)
    pytest_started_at = _iso(0)
    time.sleep(0.05)
    if add_untracked:
        (repository / "src" / "b.py").write_text("y = 1\n", encoding="utf-8")
    _install_pyfltr(monkeypatch, repository, pytest_started_at=pytest_started_at)

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr")

    assert (reuse.run_id == "01RUN") is reused


def _set_second_precision_times(repository: pathlib.Path, second: int) -> None:
    """秒未満を記録しないファイルシステムで、整形が秒`second`の中に書き直した状態を作る。"""
    index = pathlib.Path(
        subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--path-format=absolute", "--git-path", "index"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    for path in [index, *(p for p in repository.rglob("*") if p.is_file() and ".git" not in p.parts)]:
        os.utime(path, ns=(second * 1_000_000_000, second * 1_000_000_000))


@pytest.mark.parametrize("run_id", [None, "01RUN"])
@pytest.mark.parametrize(
    ("boundary", "edit_after", "reused"),
    [
        pytest.param(False, False, False, id="legacy-run-same-second-is-conservative"),
        pytest.param(True, False, True, id="formatted-same-second-is-accepted"),
        pytest.param(True, True, False, id="edit-after-boundary-is-rejected"),
    ],
)
def test_launch_boundary_accepts_same_second_formatting_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    run_id: str | None,
    boundary: bool,
    edit_after: bool,
    reused: bool,
) -> None:
    """整形と同じ秒に始まった試験は、整形の秒を過ぎた開始境界の記録がある場合だけ受理する。

    pyfltrはテスターを整形の秒が終わってから起動し、その時刻を`launch_boundary_at`へ記録する。
    境界の後の秒に記録された変更は、境界より後に起きた可能性があるため受理しない。
    """
    repository = _repository(tmp_path)
    if edit_after:
        # commit済みにして、指定runでもindexとHEADの一致ではなく時刻の判定で拒否されることを確かめる。
        (repository / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        commit_all(repository, "edit")
    # ctimeは書き換えられないため、秒の先頭で整形を模した更新を行い、ctimeも同じ秒に収める。
    time.sleep(1.05 - time.time() % 1)
    formatted_second = int(time.time())
    epoch = datetime.datetime.fromtimestamp(formatted_second, tz=datetime.UTC)
    _set_second_precision_times(repository, formatted_second)
    if edit_after:
        # 境界の後に記録された変更。秒精度の記録では境界の直後の変更と区別できない。
        time.sleep(formatted_second + 1.05 - time.time())
        edited = (formatted_second + 1) * 1_000_000_000
        os.utime(repository / "src" / "a.py", ns=(edited, edited))
    started = epoch + datetime.timedelta(seconds=1.012 if boundary else 0.012)
    _install_pyfltr(
        monkeypatch,
        repository,
        pytest_started_at=started.isoformat(),
        launch_boundary_at=(epoch + datetime.timedelta(seconds=1)).isoformat() if boundary else None,
    )

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr", run_id=run_id)

    assert (reuse.run_id == "01RUN") is reused


@pytest.mark.parametrize("change", ["ignored-create", "ignored-delete", "modify", "delete", "rename"])
@pytest.mark.parametrize("run_id", [None, "01RUN"])
def test_reuse_tracks_commit_inputs_not_ignored_directory_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, change: str, run_id: str | None
) -> None:
    """除外ファイルの生成・削除は受理し、追跡内容の変更・削除・改名は拒否する。"""
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text("src/*.log\n", encoding="utf-8")
    commit_all(repository, "ignore logs")
    ignored = repository / "src" / "output.log"
    if change == "ignored-delete":
        ignored.write_text("before", encoding="utf-8")
    # 秒精度のファイルシステムでも、準備と操作の時刻を分離する。
    time.sleep(1.05 - time.time() % 1)
    pytest_started_at = _iso(0)
    time.sleep(0.05)
    target = repository / "src" / "a.py"
    if change == "ignored-create":
        ignored.write_text("after", encoding="utf-8")
    elif change == "ignored-delete":
        ignored.unlink()
    elif change == "modify":
        target.write_text("x = 2\n", encoding="utf-8")
    elif change == "delete":
        target.unlink()
    else:
        target.rename(repository / "src" / "renamed.py")
    if not change.startswith("ignored-"):
        subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
    _install_pyfltr(monkeypatch, repository, pytest_started_at=pytest_started_at)

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr", run_id=run_id)

    assert (reuse.run_id == "01RUN") is change.startswith("ignored-")


def test_main_runs_pytest_when_query_output_is_unexpected(monkeypatch: pytest.MonkeyPatch) -> None:
    """照会出力の形式の変化などで判定中に例外が出た場合も、hookは止まらずpytestを含めて実行する。"""
    launched: list[list[str]] = []

    def _find(directory: pathlib.Path, pyfltr: str) -> pyfltr_precommit.Reuse:
        del directory, pyfltr
        raise KeyError("run_id")

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        launched.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pyfltr_precommit.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pyfltr_precommit, "current_branch", lambda directory: "master")
    monkeypatch.setattr(pyfltr_precommit, "find_reusable_run", _find)
    monkeypatch.setattr(pyfltr_precommit.subprocess, "run", _run)

    assert pyfltr_precommit.main(["src/a.py"]) == 0
    assert launched == [["/bin/pyfltr", "fast", "src/a.py"]]


@pytest.mark.parametrize(
    ("branch", "expected_extra", "queried"),
    [
        pytest.param("process-wi-lane-01", ["--disable", "pytest"], False, id="lane-branch"),
        pytest.param("develop", [], True, id="develop"),
        pytest.param("master", [], True, id="master"),
        pytest.param("main", [], True, id="main"),
        pytest.param(None, [], True, id="detached-head"),
    ],
)
def test_main_skips_pytest_only_outside_integration_branches(
    monkeypatch: pytest.MonkeyPatch, branch: str | None, expected_extra: list[str], queried: bool
) -> None:
    """統合先のブランチとブランチを特定できない状態ではpytestを含め、他のブランチではpytestを外す。"""
    launched: list[list[str]] = []
    finds: list[pathlib.Path] = []

    def _find(directory: pathlib.Path, pyfltr: str) -> pyfltr_precommit.Reuse:
        del pyfltr
        finds.append(directory)
        return pyfltr_precommit.Reuse(None, "理由")

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        launched.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pyfltr_precommit.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pyfltr_precommit, "current_branch", lambda directory: branch)
    monkeypatch.setattr(pyfltr_precommit, "find_reusable_run", _find)
    monkeypatch.setattr(pyfltr_precommit.subprocess, "run", _run)

    assert pyfltr_precommit.main(["src/a.py"]) == 0
    assert launched == [["/bin/pyfltr", "fast", *expected_extra, "src/a.py"]]
    assert bool(finds) is queried


def test_current_branch_reads_branch_and_detached_head(tmp_path: pathlib.Path) -> None:
    """実際のリポジトリでブランチ名を返し、detached HEADではNoneを返す。"""
    repository = _repository(tmp_path)
    subprocess.run(["git", "-C", str(repository), "checkout", "-q", "-b", "work"], check=True)
    assert pyfltr_precommit.current_branch(repository) == "work"
    subprocess.run(["git", "-C", str(repository), "checkout", "-q", "--detach"], check=True)
    assert pyfltr_precommit.current_branch(repository) is None


def test_current_branch_is_not_confused_by_same_named_tag(tmp_path: pathlib.Path) -> None:
    """統合先のブランチと同名のタグがあっても、ブランチ名をそのまま返す。"""
    repository = _repository(tmp_path)
    subprocess.run(["git", "-C", str(repository), "checkout", "-q", "-b", "develop"], check=True)
    subprocess.run(["git", "-C", str(repository), "tag", "develop"], check=True)
    assert pyfltr_precommit.current_branch(repository) == "develop"


@pytest.mark.parametrize("staged_change", [False, True], ids=["head-content", "index-differs-from-head"])
def test_verify_run_requires_head_content(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, staged_change: bool) -> None:
    """指定した実行はHEADの内容でpytestに成功した場合だけ確かめられ、indexがHEADと異なれば確かめられない。"""
    repository = _repository(tmp_path)
    if staged_change:
        (repository / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "src/a.py"], check=True)
    calls = _install_pyfltr(monkeypatch, repository, pytest_started_at=_iso(60))

    reuse = pyfltr_precommit.find_reusable_run(repository, "pyfltr", run_id="01RUN")

    assert (reuse.run_id == "01RUN") is not staged_change
    # 指定した実行だけを調べ、一覧の照会はしない。
    assert all(call[0] != "list-runs" for call in calls)


@pytest.mark.parametrize(
    ("reuse", "expected"), [(pyfltr_precommit.Reuse("01RUN", ""), 0), (pyfltr_precommit.Reuse(None, "理由"), 1)]
)
def test_main_verify_run_returns_result_without_commit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reuse: pyfltr_precommit.Reuse,
    expected: int,
) -> None:
    """`--verify-run`は`pyfltr fast`を起動せず、確かめた結果を終了コードで返す。"""
    launched: list[list[str]] = []

    def _find(directory: pathlib.Path, pyfltr: str, *, run_id: str | None = None) -> pyfltr_precommit.Reuse:
        del directory, pyfltr
        assert run_id == "01RUN"
        return reuse

    monkeypatch.setattr(pyfltr_precommit.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pyfltr_precommit, "find_reusable_run", _find)
    monkeypatch.setattr(pyfltr_precommit.subprocess, "run", lambda cmd, **kwargs: launched.append(cmd))

    assert pyfltr_precommit.main(["--verify-run", "01RUN"]) == expected
    assert not launched
    if expected == 1:
        captured = capsys.readouterr()
        assert reuse.reason in captured.err
        assert "次の操作:" in captured.err
        assert "--verify-run" in captured.err


@pytest.mark.parametrize("verify", [False, True])
def test_git_failure_reports_context_independent_index_inspection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, verify: bool
) -> None:
    """index照会に失敗した検収では、Gitの診断と対象を調べる公開コマンドを示す。"""
    repository = _repository(tmp_path)
    monkeypatch.chdir(repository)
    monkeypatch.setattr(pyfltr_precommit.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(pyfltr_precommit, "current_branch", lambda directory: "develop")
    original = pyfltr_precommit.git_command.run
    original_subprocess = subprocess.run
    launched: list[list[str]] = []

    def fail_index(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if "--git-path" in command:
            return subprocess.CompletedProcess(command, 128, "", "index lookup failed")
        return original(command, **kwargs)

    monkeypatch.setattr(pyfltr_precommit.git_command, "run", fail_index)

    def run_command(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if command[0] == "/bin/pyfltr":
            launched.append(command)
            return subprocess.CompletedProcess(command, 0)
        return original_subprocess(command, check=kwargs.pop("check", False), **kwargs)

    monkeypatch.setattr(pyfltr_precommit.subprocess, "run", run_command)

    assert pyfltr_precommit.main(["--verify-run", "01RUN"] if verify else []) == (1 if verify else 0)
    error = capsys.readouterr().err
    assert "index lookup failed" in error
    assert "git rev-parse --git-path index" in error
    if not verify:
        assert "--verify-run" not in error
    assert launched == ([] if verify else [["/bin/pyfltr", "fast"]])
