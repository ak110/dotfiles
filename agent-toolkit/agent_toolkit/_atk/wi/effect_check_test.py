"""効果記録と旧確認キーの公開契約を検証する。"""

import contextlib
import datetime
import json
import os
import pathlib
import signal
import subprocess
import time
import types
from collections.abc import Iterator
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import background_sync, effect_check, frontmatter, readiness, sync
from agent_toolkit._atk.wi.mutations import targets
from agent_toolkit._common.windows_job import WindowsJob, start_owned_process
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_entry_files import entry_path, write_awi, write_uwi

_SYNC_WORKER_TIMEOUT = 30


@pytest.fixture(name="sync_workers")
def _sync_workers(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[subprocess.Popen]]:
    workers: list[subprocess.Popen] = []

    with contextlib.ExitStack() as stack:
        job = stack.enter_context(WindowsJob()) if os.name == "nt" else None

        def start(args: list[str], **kwargs: Any) -> subprocess.Popen:
            kwargs.pop("start_new_session")
            kwargs.pop("creationflags")
            worker = start_owned_process(args, job=job, **kwargs)
            workers.append(worker)
            return worker

        worker_subprocess = types.SimpleNamespace(**vars(subprocess))
        worker_subprocess.Popen = start
        monkeypatch.setattr(background_sync, "subprocess", worker_subprocess)
        try:
            yield workers
        finally:
            try:
                if job is not None:
                    if workers:
                        job.terminate()
                else:
                    for worker in workers:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(worker.pid, signal.SIGKILL)
            finally:
                unreaped = []
                for worker in workers:
                    try:
                        worker.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        unreaped.append(worker.pid)
                assert not unreaped, f"同期workerを回収できない: {unreaped}"


def _wait_for_push(notes: pathlib.Path, workers: list[subprocess.Popen]) -> None:
    assert workers, "ローカル保存後に同期workerが起動していない"
    deadline = time.monotonic() + _SYNC_WORKER_TIMEOUT
    for worker in workers:
        assert worker.wait(timeout=max(0, deadline - time.monotonic())) == 0
    workers.clear()
    status = background_sync.read_status(notes).get("state")
    assert status == "synced", f"同期worker終了後の状態: {status!r}"
    assert git_repository.git_output(notes, "rev-parse", "origin/main") == git_repository.git_output(notes, "rev-parse", "HEAD")


def test_legacy_confirmation_does_not_defer_processing(tmp_path: pathlib.Path) -> None:
    path = write_awi(tmp_path, "legacy.md")
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\nconfirmation: required\n"), encoding="utf-8"
    )
    assert readiness.calculate_readiness(tmp_path, "github.com/example/repo").ready == (path.name,)


@pytest.fixture(name="effect_repository")
def _effect_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    worktree = git_repository.init_repository(
        tmp_path / "repo",
        origin="https://github.com/example/repo.git",
        files={"agent-toolkit/skills/a/playbooks/a.md": "規範A", "agent-toolkit/skills/b/SKILL.md": "規範B", "app.py": "pass"},
        commit_message="試験用の規範変更",
    )
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    notes = tmp_path / "notes"
    path = write_awi(notes, "effect.md", state="adopted")
    original = (
        path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\nconfirmation: required\ncustom: 保持\n")
        + "\n## ユーザーコメント\n\n保持対象\n\n## 処理結果\n\n- 採否: adopted\n"
        + f"- 処理日時: 2026-10-20T00:00:00Z\n- 対応commit: {oid}\n"
        + "- 既存メモ: 保持\n\n## 後続メモ\n\n保持する後続節\n"
    )
    path.write_text(original, encoding="utf-8")
    write_awi(notes, "corrective.md")
    remote = git_repository.init_bare_repository(tmp_path / "notes.git")
    git_repository.init_repository(notes, initial_branch="main", origin=str(remote), commit_message="採用済みAWI")
    git_repository.run_git(notes, "push", "--set-upstream", "origin", "main")
    monkeypatch.setattr(sync, "ensure_environment", lambda *_args, **_kwargs: notes)
    return notes, worktree, path


def _invoke(
    notes: pathlib.Path, worktree: pathlib.Path, filename: str | list[str], file: str | None, verdict: str, *extra: str
) -> int:
    with pytest.raises(SystemExit) as finished:
        atk.main(
            [
                "wi",
                "effect-check",
                *([filename] if isinstance(filename, str) else filename),
                "--worktree",
                str(worktree),
                *(["--file", file] if file is not None else []),
                "--verdict",
                verdict,
                "--checked-on",
                "2026-10-31",
                "--review-record",
                "claude:session:42",
                *extra,
            ],
            home=notes.parent,
            now=datetime.datetime(2026, 10, 31, tzinfo=datetime.UTC),
        )
    assert isinstance(finished.value.code, int)
    return finished.value.code


@pytest.mark.cpu_limit(
    2.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.023秒。"
)
def test_public_effect_check_partial_then_complete_preserves_original(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    sync_workers: list[subprocess.Popen],
) -> None:
    notes, worktree, path = effect_repository
    original = path.read_text(encoding="utf-8")
    assert _invoke(notes, worktree, path.name, "agent-toolkit/skills/a/playbooks/a.md", "confirmed") == 0
    result = capsys.readouterr().out.splitlines()
    assert result[0].startswith("成功: ")
    assert result[1:] == [str(path)]
    partial = path.read_text(encoding="utf-8")
    assert "effect_check: pending" in partial
    assert list(effect_check.checked_files(partial)) == ["agent-toolkit/skills/a/playbooks/a.md"]
    assert _invoke(notes, worktree, path.name, "agent-toolkit/skills/b/SKILL.md", "confirmed") == 0
    assert "effect_check: pending" in path.read_text(encoding="utf-8")
    assert _invoke(notes, worktree, path.name, "app.py", "confirmed") == 0
    saved = path.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(saved)
    assert parsed is not None
    assert parsed[0]["effect_check"] == "confirmed"
    restored = "\n".join(
        line for line in saved.split("\n") if not line.startswith(("effect_check:", effect_check.RECORD_PREFIX))
    )
    assert restored == original
    _wait_for_push(notes, sync_workers)
    capsys.readouterr()
    with pytest.raises(SystemExit) as show:
        atk.main(["wi", "show", path.name, "--skip-pull"], home=notes.parent)
    assert show.value.code == 0
    assert "effect_check: confirmed" in capsys.readouterr().out
    with pytest.raises(SystemExit) as listed:
        atk.main(["wi", "list", "--target-repo", str(worktree), "--state=adopted", "--jsonl", "--skip-pull"], home=notes.parent)
    assert listed.value.code == 0
    assert json.loads(capsys.readouterr().out)["effect_check"] == "confirmed"


@pytest.mark.cpu_limit(1.5, reason="是正先の有無による保存拒否から、実Gitへ不足判定を保存する最小の遷移。所有CPU最大0.656秒。")
def test_public_effect_insufficient_requires_saved_corrective_awi(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, "agent-toolkit/skills/a/playbooks/a.md", "insufficient") != 0
    assert path.read_bytes() == original
    assert (
        _invoke(
            notes,
            worktree,
            path.name,
            "agent-toolkit/skills/a/playbooks/a.md",
            "insufficient",
            "--corrective-awi",
            "missing.md",
        )
        != 0
    )
    assert path.read_bytes() == original
    assert (
        _invoke(
            notes,
            worktree,
            path.name,
            "agent-toolkit/skills/a/playbooks/a.md",
            "insufficient",
            "--corrective-awi",
            "corrective.md",
        )
        == 0
    )
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["effect_check"] == "insufficient"


@pytest.mark.parametrize(
    "file", ["../rules/a.md", "/etc/passwd", "agent-toolkit/skills/a/playbooks/missing.md", "agent-toolkit//rules/a.md"]
)
def test_public_effect_rejects_invalid_file_without_writing(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], file: str
) -> None:
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, file, "confirmed") != 0
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "extra",
    [
        ("--checked-on", "2026-02-30"),
        ("--checked-on", "20261031"),
        ("--verdict", "unknown"),
        ("--review-record", ""),
        ("--review-record", "line1\nline2"),
    ],
)
def test_public_effect_rejects_invalid_values(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], extra: tuple[str, str]
) -> None:
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, "agent-toolkit/skills/a/playbooks/a.md", "confirmed", *extra) != 0
    assert path.read_bytes() == original


@pytest.mark.cpu_limit(1.5, reason="実Gitに部分確認を保存した後の重複拒否で、元記録の非上書きを確かめる。所有CPU最大0.672秒。")
def test_public_effect_rejects_duplicate_file(effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path]) -> None:
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, path.name, "agent-toolkit/skills/a/playbooks/a.md", "confirmed") == 0
    original = path.read_bytes()
    assert (
        _invoke(
            notes,
            worktree,
            path.name,
            "agent-toolkit/skills/a/playbooks/a.md",
            "insufficient",
            "--corrective-awi",
            "corrective.md",
        )
        != 0
    )
    assert path.read_bytes() == original


def test_result_values_ignore_fenced_examples() -> None:
    text = "## 処理結果\n\n```text\n- 対応commit: example\n```\n\n- 対応commit: abcdef0\n"
    assert effect_check.result_values(text, "対応commit") == ["abcdef0"]


def test_public_effect_rejects_unadopted_and_ordinary_edit(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, "corrective.md", "agent-toolkit/skills/a/playbooks/a.md", "confirmed") != 0
    body = tmp_path / "body.md"
    body.write_text("上書き", encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(SystemExit) as edited:
        atk.main(["wi", "edit", path.name, "--body-file", str(body)], home=notes.parent)
    assert edited.value.code != 0
    assert path.read_bytes() == original


@pytest.mark.cpu_limit(
    1.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.757秒。"
)
def test_public_effect_overall_reason_and_listing_without_commit(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    notes, worktree, path = effect_repository
    text = path.read_text(encoding="utf-8")
    original = "\n".join(line for line in text.split("\n") if not line.startswith(("- 処理日時:", "- 対応commit:")))
    path.write_text(original, encoding="utf-8")
    git_repository.commit_all(notes, "日時とcommitのない保存済み項目")
    git_repository.run_git(notes, "push")
    for verdict in ("pending", "not_required"):
        before = path.read_bytes()
        assert _invoke(notes, worktree, path.name, None, verdict) != 0
        assert path.read_bytes() == before
        assert _invoke(notes, worktree, path.name, None, verdict, "--reason", "記録を確認した理由") == 0
        assert effect_check.pending_reason(path.read_text(encoding="utf-8")) == "記録を確認した理由"
        capsys.readouterr()
        for output in ("--jsonl", "--no-jsonl", "--summary-only", "--count"):
            with pytest.raises(SystemExit) as listed:
                atk.main(
                    [
                        "wi",
                        "list",
                        "--state=adopted",
                        f"--effect-check={verdict}",
                        "--target-repo",
                        str(worktree),
                        "--skip-pull",
                        output,
                    ],
                    home=notes.parent,
                )
            assert listed.value.code == 0
            result = capsys.readouterr().out
            if output == "--count":
                assert result.strip() == "1"
            elif output == "--no-jsonl":
                assert f"effect_check={verdict}" in result
                assert "記録を確認した理由" in result
            else:
                record = json.loads(result)
                assert record["filename"] == path.name
                if output == "--jsonl":
                    assert record["effect_check"] == verdict
                    assert record["effect_check_reason"] == "記録を確認した理由"
    restored = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").split("\n")
        if not line.startswith(("effect_check:", effect_check.OVERALL_RECORD_PREFIX))
    )
    assert restored == original


@pytest.mark.cpu_limit(
    1.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.521秒。"
)
def test_public_effect_retry_after_commit_failure_is_idempotent(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    sync_workers: list[subprocess.Popen],
) -> None:
    """部分記録後のGit失敗でもpendingを保持し、同じ公開操作でcommitから再開する。"""
    notes, worktree, path = effect_repository

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    with monkeypatch.context() as failing:
        failing.setattr(sync, "commit_and_push", fail_commit)
        assert _invoke(notes, worktree, path.name, "app.py", "confirmed") != 0
    partial = path.read_text(encoding="utf-8")
    assert "effect_check: pending" in partial
    assert _invoke(notes, worktree, path.name, "app.py", "confirmed") == 0
    assert path.read_text(encoding="utf-8") == partial
    assert len(effect_check.result_values(partial, "効果確認")) == 1
    _wait_for_push(notes, sync_workers)


@pytest.mark.cpu_limit(
    2.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.123秒。"
)
def test_public_transition_without_implemented_awi_leaves_effect_unset(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    notes, worktree, _path = effect_repository
    write_awi(notes, "nochange.md", state="processing")
    write_awi(notes, "rejected.md", state="processing")
    write_uwi(notes, "answer.md", state="processing", answer="回答済み")
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    for action, filename, commits in (
        ("adopt", "nochange.md", []),
        ("adopt", "answer.md", ["--commit", oid]),
        ("reject", "rejected.md", ["--commit", oid]),
    ):
        with pytest.raises(SystemExit) as finished:
            atk.main(["wi", action, filename, "--target-repo", str(worktree), *commits], home=notes.parent)
        assert finished.value.code == 0
        assert "effect_check:" not in entry_path(notes, filename).read_text(encoding="utf-8")


def _adopt(notes: pathlib.Path, worktree: pathlib.Path, filename: str, *commits: str) -> int:
    with pytest.raises(SystemExit) as finished:
        atk.main(["wi", "adopt", filename, "--target-repo", str(worktree), *commits], home=notes.parent)
    assert isinstance(finished.value.code, int)
    return finished.value.code


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "実commit付きinboxの採用で、保存成功と処理中抜けの警告を両方確認する。同じ実Git保存群の所有CPU最大0.672秒を基準にする。"
    ),
)
def test_adopt_warns_when_inbox_entry_has_commit(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    notes, worktree, _path = effect_repository
    write_awi(notes, "skipped.md", state="inbox")
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    capsys.readouterr()

    assert _adopt(notes, worktree, "skipped.md", "--commit", oid, "--effect-check=pending") == 0

    captured = capsys.readouterr()
    assert "effect_check: pending" in entry_path(notes, "skipped.md").read_text(encoding="utf-8")
    assert "成功: 1件をadoptedへ移した" in captured.out
    assert captured.err.count("skipped.mdは作業を示すcommitがあるのに処理中（processing）を経ずにinboxから終了した") == 1
    assert "atk wi start-processing" in captured.err


@pytest.mark.cpu_limit(
    3.0, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大1.500秒。"
)
def test_public_effect_batch_validates_all_and_resumes_partial_save(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sync_workers: list[subprocess.Popen],
) -> None:
    """公開複数指定で全件を検証し、旧本文を保持して部分保存・commit失敗から再開する。"""
    notes, worktree, path = effect_repository
    legacy = write_awi(notes, "legacy.md", state="adopted")
    legacy.write_text(
        legacy.read_text(encoding="utf-8") + "\n## ユーザーコメント\n\n旧本文と末尾空白を保持する  ", encoding="utf-8"
    )
    git_repository.commit_all(notes, "旧形式の採用済み項目")
    git_repository.run_git(notes, "push")
    originals = {item: item.read_bytes() for item in (path, legacy)}
    base = git_repository.git_output(notes, "rev-parse", "HEAD")
    names = [path.name, legacy.name]
    reason = ("--reason", "実利用の適用機会を待つ")

    for invalid in ("missing.md", "corrective.md"):
        assert _invoke(notes, worktree, [*names, invalid], None, "pending", *reason) != 0
        captured = capsys.readouterr()
        assert "成功: " not in captured.out
        assert captured.err
        assert all(item.read_bytes() == before for item, before in originals.items())
        assert git_repository.git_output(notes, "rev-parse", "HEAD") == base

    original_write = targets.atomic_write_text

    def fail_second_write(target: pathlib.Path, text: str, **_kwargs: object) -> None:
        if target == legacy:
            raise OSError("試験用の2件目の保存失敗")
        original_write(target, text)

    with monkeypatch.context() as failing:
        failing.setattr(targets, "atomic_write_text", fail_second_write)
        assert _invoke(notes, worktree, names, None, "pending", *reason) != 0
    assert len(effect_check.result_values(path.read_text(encoding="utf-8"), "効果確認全体")) == 1
    assert legacy.read_bytes() == originals[legacy]
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == base

    calls: list[list[str]] = []
    original_git = sync.run_git

    def observe_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True, **_kwargs: object) -> None:
        calls.append(args)
        original_git(args, cwd, forward_error_output=forward_error_output)

    with monkeypatch.context() as recording:
        recording.setattr(sync, "run_git", observe_git)
        assert _invoke(notes, worktree, names, None, "pending", *reason) == 0
    result = capsys.readouterr().out.splitlines()
    assert result[0].startswith("成功: ")
    assert result[1:] == [str(path), str(legacy)]
    assert sum(args[0] == "commit" for args in calls) == 1
    assert sum(args[0] == "push" for args in calls) == 0
    _wait_for_push(notes, sync_workers)
    assert git_repository.git_output(notes, "rev-list", "--count", f"{base}..HEAD") == "1"
    saved = {item: item.read_bytes() for item in (path, legacy)}
    for item, before in originals.items():
        text = item.read_text(encoding="utf-8")
        assert len(effect_check.result_values(text, "効果確認全体")) == 1
        assert "effect_check: pending" in text
        parsed = frontmatter.parse_frontmatter(text)
        previous = frontmatter.parse_frontmatter(before.decode("utf-8"))
        assert parsed is not None and previous is not None
        assert parsed[0]["state"] == "adopted"
        if item == legacy:
            assert parsed[1].startswith(previous[1])
            assert parsed[1][len(previous[1]) :].startswith("\n\n## 処理結果\n")
        else:
            restored = "\n".join(
                line for line in parsed[1].split("\n") if not line.startswith(effect_check.OVERALL_RECORD_PREFIX)
            )
            assert restored == previous[1]
    assert _invoke(notes, worktree, names, None, "pending", *reason) == 0
    assert capsys.readouterr().out.splitlines() == result
    assert all(item.read_bytes() == saved[item] for item in saved)
    assert git_repository.git_output(notes, "rev-list", "--count", f"{base}..HEAD") == "1"

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    with monkeypatch.context() as failing:
        failing.setattr(sync, "commit_and_push", fail_commit)
        assert _invoke(notes, worktree, names, None, "not_required", "--reason", "後日の観測を要しない") != 0
    assert _invoke(notes, worktree, names, None, "not_required", "--reason", "後日の観測を要しない") == 0
    for item in (path, legacy):
        text = item.read_text(encoding="utf-8")
        assert "effect_check: not_required" in text
        assert len(effect_check.result_values(text, "効果確認全体")) == 2
    assert git_repository.git_output(notes, "rev-list", "--count", f"{base}..HEAD") == "2"
    _wait_for_push(notes, sync_workers)


@pytest.mark.cpu_limit(
    1.5, reason="実Gitの隔離した資源・履歴・保存状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.677秒。"
)
def test_public_effect_whole_item_verdicts_without_file(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, path.name, "agent-toolkit/skills/a/playbooks/a.md", "confirmed") == 0
    assert _invoke(notes, worktree, path.name, None, "insufficient") != 0
    assert (
        _invoke(
            notes,
            worktree,
            path.name,
            None,
            "insufficient",
            "--corrective-awi",
            "corrective.md",
            "--reason",
            "目的の値が延びた",
        )
        == 0
    )
    saved = path.read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(saved)
    assert parsed is not None and parsed[0]["effect_check"] == "insufficient"
    assert list(effect_check.checked_files(saved)) == ["agent-toolkit/skills/a/playbooks/a.md"]
    overall = json.loads(effect_check.result_values(saved, "効果確認全体")[-1])
    assert overall == {
        "checked_on": "2026-10-31",
        "corrective_awi": "corrective.md",
        "reason": "目的の値が延びた",
        "review_record": "claude:session:42",
        "verdict": "insufficient",
    }
    capsys.readouterr()
    with pytest.raises(SystemExit) as listed:
        atk.main(
            [
                "wi",
                "list",
                "--target-repo",
                str(worktree),
                "--state=adopted",
                "--effect-check=insufficient",
                "--jsonl",
                "--skip-pull",
            ],
            home=notes.parent,
        )
    assert listed.value.code == 0
    assert json.loads(capsys.readouterr().out)["filename"] == path.name


@pytest.mark.cpu_limit(
    1.5,
    reason=("ファイル指定なしの全体判定を実Gitへ保存し、部分判定と区別する。同じ実Git保存群の所有CPU最大0.672秒を基準にする。"),
)
def test_public_effect_whole_item_confirmed_without_file(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, path.name, None, "confirmed") == 0
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None and parsed[0]["effect_check"] == "confirmed"


def test_implemented_awi_can_adopt_without_effect_decision(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    notes, worktree, _path = effect_repository
    path = write_awi(notes, "implemented.md", state="processing")
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    assert _adopt(notes, worktree, path.name, "--commit", oid) == 0
    saved = path.read_text(encoding="utf-8")
    assert "state: adopted" in saved
    assert "effect_check:" not in saved


@pytest.mark.parametrize(
    "selection", [["--effect-check=not_required"], ["--effect-check=not_required", "--effect-check-reason=  "]]
)
def test_implemented_awi_rejects_invalid_explicit_effect_decision(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], selection: list[str]
) -> None:
    notes, worktree, _path = effect_repository
    write_awi(notes, "implemented.md", state="processing")
    path = entry_path(notes, "implemented.md")
    original = path.read_bytes()
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    before = git_repository.git_output(notes, "rev-parse", "HEAD")
    assert _adopt(notes, worktree, path.name, "--commit", oid, *selection) == 1
    assert path.read_bytes() == original
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == before


def test_implemented_awi_records_exemption_with_multiline_note(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    notes, worktree, _path = effect_repository
    write_awi(notes, "implemented.md", state="processing")
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    assert (
        _adopt(
            notes,
            worktree,
            "implemented.md",
            "--commit",
            oid,
            "--effect-check=not_required",
            "--effect-check-reason=採用前の検収で目的を達成",
            "--note=検収結果\n## 処理結果\n```",
        )
        == 0
    )
    saved = entry_path(notes, "implemented.md").read_text(encoding="utf-8")
    assert "effect_check: not_required" in saved
    assert effect_check.pending_reason(saved) == "採用前の検収で目的を達成"


@pytest.mark.parametrize("bulk", [False, True])
def test_implemented_awi_validates_all_targets_before_adopting(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    bulk: bool,
) -> None:
    notes, worktree, _path = effect_repository
    write_uwi(notes, "answer.md", state="inbox", answer="回答済み")
    write_awi(notes, "implemented.md", state="inbox")
    selection = ["--all", "--yes", "--skip-pull"] if bulk else ["answer.md", "implemented.md"]
    paths = [entry_path(notes, filename) for filename in ("answer.md", "implemented.md")]
    before = [path.read_bytes() for path in paths]
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    with pytest.raises(SystemExit) as finished:
        atk.main(
            ["wi", "adopt", *selection, "--target-repo", str(worktree), "--commit", oid, "--effect-check=not_required"],
            home=notes.parent,
        )
    assert finished.value.code == 1
    assert [path.read_bytes() for path in paths] == before
