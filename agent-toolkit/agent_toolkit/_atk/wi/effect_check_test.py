"""効果記録と旧確認キーの公開契約を検証する。"""

import datetime
import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import effect_check, frontmatter, readiness, sync
from agent_toolkit._atk.wi.mutations import targets
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_entry_files import entry_path, write_awi, write_uwi


def test_legacy_confirmation_does_not_defer_processing(tmp_path: pathlib.Path) -> None:
    """保存済み確認キーは通常の未知キーとして着手可否に影響しない。"""
    path = write_awi(tmp_path, "legacy.md")
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\nconfirmation: required\n"), encoding="utf-8"
    )
    assert readiness.calculate_readiness(tmp_path, "github.com/example/repo").ready == (path.name,)


@pytest.fixture(name="effect_repository")
def _effect_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """実commitとローカルremoteを持つWI保存先を用意する。"""
    worktree = git_repository.init_repository(
        tmp_path / "repo",
        origin="https://github.com/example/repo.git",
        files={"agent-toolkit/share/rules/common/a.md": "規範A", "agent-toolkit/skills/b/SKILL.md": "規範B", "app.py": "pass"},
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


def test_public_effect_check_partial_then_complete_preserves_original(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """公開保存操作の部分確認・全確認とshow・一覧JSON、remoteへの反映を確認する。"""
    notes, worktree, path = effect_repository
    original = path.read_text(encoding="utf-8")
    assert _invoke(notes, worktree, path.name, "agent-toolkit/share/rules/common/a.md", "confirmed") == 0
    result = capsys.readouterr().out.splitlines()
    assert result[0].startswith("成功: ")
    assert result[1:] == [str(path)]
    partial = path.read_text(encoding="utf-8")
    assert "effect_check: pending" in partial
    assert list(effect_check.checked_files(partial)) == ["agent-toolkit/share/rules/common/a.md"]
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
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == git_repository.git_output(notes, "rev-parse", "origin/main")
    capsys.readouterr()
    with pytest.raises(SystemExit) as show:
        atk.main(["wi", "show", path.name, "--skip-pull"], home=notes.parent)
    assert show.value.code == 0
    assert "effect_check: confirmed" in capsys.readouterr().out
    with pytest.raises(SystemExit) as listed:
        atk.main(["wi", "list", "--target-repo", str(worktree), "--state=adopted", "--jsonl", "--skip-pull"], home=notes.parent)
    assert listed.value.code == 0
    assert json.loads(capsys.readouterr().out)["effect_check"] == "confirmed"


def test_public_effect_insufficient_requires_saved_corrective_awi(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    """不足判定は是正先保存を要し、成功時にinsufficientを記録する。"""
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, "agent-toolkit/share/rules/common/a.md", "insufficient") != 0
    assert path.read_bytes() == original
    assert (
        _invoke(
            notes,
            worktree,
            path.name,
            "agent-toolkit/share/rules/common/a.md",
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
            "agent-toolkit/share/rules/common/a.md",
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
    "file", ["../rules/a.md", "/etc/passwd", "agent-toolkit/share/rules/common/missing.md", "agent-toolkit//rules/a.md"]
)
def test_public_effect_rejects_invalid_file_without_writing(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], file: str
) -> None:
    """逸脱・正規化の差・対応commitに無い対象を拒否する。"""
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
    """不正な日付・判定・記録位置を保存前に拒否する。"""
    notes, worktree, path = effect_repository
    original = path.read_bytes()
    assert _invoke(notes, worktree, path.name, "agent-toolkit/share/rules/common/a.md", "confirmed", *extra) != 0
    assert path.read_bytes() == original


def test_public_effect_rejects_duplicate_file(effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path]) -> None:
    """部分確認済みのファイルを上書きしない。"""
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, path.name, "agent-toolkit/share/rules/common/a.md", "confirmed") == 0
    original = path.read_bytes()
    assert (
        _invoke(
            notes,
            worktree,
            path.name,
            "agent-toolkit/share/rules/common/a.md",
            "insufficient",
            "--corrective-awi",
            "corrective.md",
        )
        != 0
    )
    assert path.read_bytes() == original


def test_result_values_ignore_fenced_examples() -> None:
    """処理結果のコード例を実際の対応commitとして読み取らない。"""
    text = "## 処理結果\n\n```text\n- 対応commit: example\n```\n\n- 対応commit: abcdef0\n"
    assert effect_check.result_values(text, "対応commit") == ["abcdef0"]


def test_public_effect_rejects_unadopted_and_ordinary_edit(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """未採用の効果記録と通常editによる採用済み本文の変更を拒否する。"""
    notes, worktree, path = effect_repository
    assert _invoke(notes, worktree, "corrective.md", "agent-toolkit/share/rules/common/a.md", "confirmed") != 0
    body = tmp_path / "body.md"
    body.write_text("上書き", encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(SystemExit) as edited:
        atk.main(["wi", "edit", path.name, "--body-file", str(body)], home=notes.parent)
    assert edited.value.code != 0
    assert path.read_bytes() == original


def test_public_effect_overall_reason_and_listing_without_commit(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """日時・commit不明でも理由を保存でき、一覧の各出力形式で確認待ちを限定できる。"""
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


def test_public_effect_retry_after_commit_failure_is_idempotent(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path], monkeypatch: pytest.MonkeyPatch
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
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == git_repository.git_output(notes, "rev-parse", "origin/main")


def test_public_transition_only_implemented_awi_adoption_starts_pending(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    """差分なしAWI・回答UWI・却下項目は自動の効果確認待ちへ入れない。"""
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


def test_public_effect_batch_validates_all_and_resumes_partial_save(
    effect_repository: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    assert sum(args[0] == "push" for args in calls) == 1
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
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == git_repository.git_output(notes, "rev-parse", "origin/main")
