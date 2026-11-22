"""`atk wi rm --all`の公開CLI契約を検証する。"""

import contextlib
import io
import pathlib
import subprocess
import sys
from collections.abc import Callable

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import bulk as remove_all  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.layout import entry_state_of
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.git_fakes import make_outside_worktree_fake as _make_outside_worktree_fake  # noqa: E402
from agent_toolkit._testing.wi_entry_files import entry_path as _test_entry_path
from agent_toolkit.atk_test import _setup_notes, _write_awi_file  # noqa: E402  # pylint: disable=wrong-import-position


class _TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def _write_entry(
    notes: pathlib.Path,
    state: str,
    filename: str,
    *,
    target_repo: str = "github.com/example/foo",
    entry_type: str = "awi",
    body: str = "テスト本文",
    source: str | None = None,
    legacy_layout: bool = False,
) -> pathlib.Path:
    path = _test_entry_path(notes, filename, state=state, legacy_layout=legacy_layout)
    source_line = f"source: {source}\n" if source is not None else ""
    state_line = "" if legacy_layout else f"state: {state}\n"
    path.write_text(
        f"---\ntarget_repo: {target_repo}\ntype: {entry_type}\n{state_line}{source_line}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class _PullTracker:
    def __init__(self, action: Callable[[], None] | None = None, *, at_call: int = 1) -> None:
        self.count = 0
        self._action = action
        self._at_call = at_call

    def __call__(self) -> None:
        self.count += 1
        if self._action is not None and self.count == self._at_call:
            self._action()


def _change_action(change: str, path: pathlib.Path) -> Callable[[], None]:

    def move() -> None:
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8")

    def edit() -> None:
        path.write_text(path.read_text(encoding="utf-8") + "\n更新\n", encoding="utf-8")

    return {"move": move, "edit": edit, "remove": path.unlink}[change]


def _patch_storage(
    monkeypatch: pytest.MonkeyPatch,
    commit_calls: list[tuple[str, list[str]]],
    *,
    on_pull: Callable[[], None] | None = None,
) -> None:
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())

    def fake_pull(_private_notes: pathlib.Path) -> None:
        if on_pull is not None:
            on_pull()

    monkeypatch.setattr(_wi_sync, "pull", fake_pull)
    monkeypatch.setattr(
        _wi_sync,
        "commit_and_push",
        lambda _private_notes, message, paths: commit_calls.append((message, list(paths))),
    )


def _run_main(argv: list[str], home: pathlib.Path) -> int:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=home)
    assert isinstance(exc_info.value.code, int)
    return exc_info.value.code


def test_remove_all_accepts_multiple_target_repositories(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _write_entry(notes, "inbox", "foo.md", target_repo="github.com/example/foo")
    _write_entry(notes, "hold", "bar.md", target_repo="github.com/example/bar")
    _write_entry(notes, "inbox", "keep.md", target_repo="github.com/example/baz")
    commits: list[tuple[str, list[str]]] = []
    _patch_storage(monkeypatch, commits)

    removed = remove_all.remove_all_entries(
        notes,
        target_repo=["github.com/example/foo", "github.com/example/bar", "github.com/example/foo"],
        assume_yes=True,
        force=False,
        note=None,
        skip_pull=True,
        status=["active"],
        entry_type=["all"],
        answered=["all"],
        source=None,
        actor_is_agent=False,
    )

    assert set(removed) == {"foo.md", "bar.md"}
    assert _test_entry_path(notes, "keep.md").exists()
    assert len(commits) == 1


class TestRemoveAllArguments:
    """一括削除と個別削除の引数制約を検証する。"""

    @pytest.mark.parametrize(
        "argv",
        [
            ["wi", "rm", "--all", "--target-repo", "all"],
            ["wi", "rm", "--all", "--target-repo", "github.com/example/foo", "entry.md"],
            ["wi", "rm"],
            ["wi", "rm", "--yes", "entry.md"],
            ["wi", "rm", "--skip-pull", "entry.md"],
            ["wi", "rm", "--all", "--state=inbox", "--target-repo", "github.com/example/foo"],
            ["wi", "rm", "--status=all", "entry.md"],
        ],
    )
    def test_rejects_invalid_combinations(
        self,
        argv: list[str],
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """不足または排他的な引数の組合せを環境初期化前に拒否する。"""
        assert _run_main(argv, tmp_path) == 2
        assert "使い方:" in capsys.readouterr().err
        assert not (tmp_path / "private-notes").exists()

    def test_rejects_all_without_resolvable_repository(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(subprocess, "run", _make_outside_worktree_fake())

        assert _run_main(["wi", "rm", "--all"], tmp_path) == 2
        assert "--allの対象リポジトリを確定できません。" in capsys.readouterr().err


class TestRemoveAllConfirmation:
    def test_confirms_once_and_removes_awi_and_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md")
        _write_entry(notes, "inbox", "question.md", entry_type="uwi", body="## 質問\n\n確認事項\n\n## 回答\n")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        stdin = _TtyInput("y\n")
        monkeypatch.setattr(sys, "stdin", stdin)

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        captured = capsys.readouterr()
        assert "# awi" in captured.out
        assert "[inbox/" in captured.out
        assert "# uwi" in captured.out
        assert "[inbox/unanswered]" in captured.out
        assert "上記2件を削除します" in captured.out
        assert "[Y/n]" in captured.out
        assert stdin.tell() == 2
        assert not _test_entry_path(notes, "awi.md").exists()
        assert not _test_entry_path(notes, "question.md").exists()
        assert commits == [("chore: remove 2 entries", ["2026/10/awi.md", "2026/10/question.md"])]

    def test_includes_hold_entries_for_user(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        held = _write_entry(notes, "hold", "held.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not held.exists()
        assert commits
        output = capsys.readouterr().out
        assert output.splitlines()[0].startswith("成功: ")
        assert "[hold/" not in output

    def test_commits_only_removed_fixed_paths(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--target-repo", "github.com/example/foo", "--yes"],
                tmp_path,
            )
            == 0
        )

        assert commits[0][1] == ["2026/10/awi.md"]

    def test_empty_answer_removes_candidates(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "awi.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", _TtyInput("\n"))

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        assert not path.exists()
        assert len(commits) == 1

    @pytest.mark.parametrize("answer", ["n\n", "later\n"])
    def test_non_approval_keeps_all_candidates(
        self,
        answer: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "awi.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", _TtyInput(answer))

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        assert path.exists()
        assert not commits
        assert "成功: 確認で中止したため削除しなかった（変更は無い）" in capsys.readouterr().out

    def test_non_tty_requires_yes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "awi.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 2

        assert path.exists()
        assert not commits
        assert "--yes" in capsys.readouterr().err

    @pytest.mark.parametrize("agent_environment", [False, True])
    def test_yes_omits_list_without_reading_input(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        agent_environment: bool,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "awi.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        if agent_environment:
            monkeypatch.setenv("AI_AGENT", "1")

        class _UnreadableInput:
            def readline(self, _size: int = -1, /) -> str:
                raise AssertionError("標準入力を読んではならない")

        monkeypatch.setattr(sys, "stdin", _UnreadableInput())

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not path.exists()
        output = capsys.readouterr().out
        assert output.splitlines()[0].startswith("成功: ")
        assert "# awi" not in output
        assert "[inbox/" not in output
        assert len(commits) == 1


class TestRemoveAllScope:
    def test_removes_only_matching_active_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        matching_inbox = _write_entry(notes, "inbox", "awi.md")
        matching_processing = _write_entry(notes, "processing", "question.md", entry_type="uwi")
        other_repo = _write_entry(notes, "inbox", "other.md", target_repo="github.com/example/other")
        adopted = _write_entry(notes, "adopted", "adopted.md")
        rejected = _write_entry(notes, "rejected", "rejected.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--force", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not matching_inbox.exists()
        assert not matching_processing.exists()
        assert other_repo.exists()
        assert adopted.exists()
        assert rejected.exists()

    def test_user_can_remove_terminal_states_with_status_all(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        adopted = _write_entry(notes, "adopted", "adopted.md")
        rejected = _write_entry(notes, "rejected", "rejected.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--status=all", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not adopted.exists()
        assert not rejected.exists()

    def test_filters_use_same_intersection_as_list(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        matching = _write_entry(
            notes,
            "hold",
            "matching.md",
            entry_type="uwi",
            body="## 質問\n\n確認\n\n## 回答\n\n済み",
            source="session-review",
        )
        wrong_source = _write_entry(
            notes,
            "hold",
            "wrong-source.md",
            entry_type="uwi",
            body="## 質問\n\n確認\n\n## 回答\n\n済み",
            source="agent",
        )
        unanswered = _write_entry(
            notes,
            "hold",
            "unanswered.md",
            entry_type="uwi",
            body="## 質問\n\n確認\n\n## 回答\n",
            source="session-review",
        )
        awi = _write_entry(notes, "hold", "awi.md", source="session-review")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                [
                    "wi",
                    "rm",
                    "--all",
                    "--yes",
                    "--status=hold",
                    "--type=uwi",
                    "--answered=yes",
                    "--source=session-review",
                    "--target-repo",
                    "github.com/example/foo",
                ],
                tmp_path,
            )
            == 0
        )

        assert not matching.exists()
        assert wrong_source.exists()
        assert unanswered.exists()
        assert awi.exists()

    def test_agent_environment_limits_all_to_inbox_and_hold(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        paths = {
            state: _write_entry(notes, state, f"{state}.md") for state in ("inbox", "processing", "hold", "adopted", "rejected")
        }
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setenv("AI_AGENT", "1")

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--status=all", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not paths["inbox"].exists()
        assert not paths["hold"].exists()
        assert all(paths[state].exists() for state in ("processing", "adopted", "rejected"))

    def test_removes_legacy_path_and_url_forms_together(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        git_repository.init_repository(local_repo, origin="git@github.com:example/myrepo.git")
        legacy = _write_entry(notes, "inbox", "legacy.md", target_repo=str(local_repo))
        current = _write_entry(notes, "inbox", "current.md", target_repo="github.com/example/myrepo")
        missing = _write_entry(notes, "inbox", "missing.md", target_repo=str(tmp_path / "missing"))
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--target-repo", "github.com/example/myrepo"],
                tmp_path,
            )
            == 0
        )

        assert not legacy.exists()
        assert not current.exists()
        assert missing.exists()

    def test_keeps_entries_without_verifiable_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        broken = _test_entry_path(notes, "broken.md")
        broken.write_text("---\ntarget_repo: github.com/example/foo\n", encoding="utf-8")
        unknown_repo = _test_entry_path(notes, "unknown-repo.md")
        unknown_repo.write_text(
            "---\nstate: inbox\ntype: awi\n---\n\n本文\n",
            encoding="utf-8",
        )
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert broken.exists()
        assert unknown_repo.exists()
        assert not commits

    def test_zero_candidates_skips_confirmation_and_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _setup_notes(tmp_path)
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", _TtyInput("標準入力を消費しない"))

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        assert not commits

        assert "成功: 対象0件のため削除しなかった（変更は無い）: github.com/example/foo" in capsys.readouterr().out

    def test_legacy_duplicate_names_stop_before_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        inbox = _write_entry(notes, "inbox", "same.md", legacy_layout=True)
        processing = _write_entry(notes, "processing", "same.md", legacy_layout=True)
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--force", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 2
        )

        assert inbox.exists()
        assert processing.exists()
        assert not commits


class TestRemoveAllProcessingProtection:
    @pytest.mark.parametrize("assume_yes", [False, True])
    def test_force_is_required_independently_from_yes(
        self,
        assume_yes: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        inbox = _write_entry(notes, "inbox", "inbox.md")
        processing = _write_entry(notes, "processing", "processing.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", _TtyInput("y\n"))
        argv = ["wi", "rm", "--all", "--target-repo", "github.com/example/foo"]
        if assume_yes:
            argv.append("--yes")

        assert _run_main(argv, tmp_path) == 2

        assert inbox.exists()
        assert processing.exists()
        assert not commits
        assert "--force" in capsys.readouterr().err


class TestRemoveAllConcurrentChanges:
    @pytest.mark.parametrize("change", ["move", "edit", "remove"])
    def test_keeps_changed_entries_and_reports_them(
        self,
        change: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        original = _write_entry(notes, "inbox", "original.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits, on_pull=_PullTracker(_change_action(change, original), at_call=2))
        monkeypatch.setattr(sys, "stdin", _TtyInput("yes\n"))

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        captured = capsys.readouterr()
        assert "警告: 確認後に変更されたため削除しない: original.md\n次の操作: " in captured.out
        assert "atk wi show" in captured.out and "再実行せよ" in captured.out
        assert "成功: 対象0件のため削除しなかった（変更は無い）: github.com/example/foo" in captured.out
        assert not commits

    def test_removes_confirmed_entry_and_keeps_added_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        original = _write_entry(notes, "inbox", "original.md")
        added = _test_entry_path(notes, "added.md")
        commits: list[tuple[str, list[str]]] = []

        def add_entry() -> None:
            _write_entry(notes, "inbox", "added.md")

        _patch_storage(monkeypatch, commits, on_pull=_PullTracker(add_entry, at_call=2))
        monkeypatch.setattr(sys, "stdin", _TtyInput("yes\n"))

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        assert not original.exists()
        assert added.exists()
        assert commits == [("chore: remove 1 entry", ["2026/10/original.md"])]
        assert "確認後に変更されたため削除しません" not in capsys.readouterr().out

    def test_removes_only_unchanged_entry_of_two(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        unchanged = _write_entry(notes, "inbox", "unchanged.md")
        changed = _write_entry(notes, "inbox", "changed.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits, on_pull=_PullTracker(_change_action("move", changed), at_call=2))
        monkeypatch.setattr(sys, "stdin", _TtyInput("yes\n"))

        assert _run_main(["wi", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        assert not unchanged.exists()
        assert _test_entry_path(notes, "changed.md").exists()
        assert entry_state_of(_test_entry_path(notes, "changed.md")) == "processing"
        assert commits == [("chore: remove 1 entry", ["2026/10/unchanged.md"])]
        assert "警告: 確認後に変更されたため削除しない: changed.md\n次の操作: " in capsys.readouterr().out


class TestRemoveAllSkipPull:
    """`--skip-pull`が選定・確認をローカル状態で行い削除直前だけ同期する挙動を検証する。"""

    def test_syncs_only_before_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "awi.md")
        commits: list[tuple[str, list[str]]] = []
        tracker = _PullTracker()
        _patch_storage(monkeypatch, commits, on_pull=tracker)
        monkeypatch.setattr(sys, "stdin", _TtyInput("y\n"))

        assert (
            _run_main(
                ["wi", "rm", "--all", "--skip-pull", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert tracker.count == 1
        assert not path.exists()
        assert len(commits) == 1

    def test_yes_syncs_once_and_keeps_changed_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--yes`併用時も削除直前に1回同期し、同期後に変化した項目を削除しない。"""
        notes = _setup_notes(tmp_path)
        unchanged = _write_entry(notes, "inbox", "unchanged.md")
        changed = _write_entry(notes, "inbox", "changed.md")
        commits: list[tuple[str, list[str]]] = []
        tracker = _PullTracker(_change_action("edit", changed))
        _patch_storage(monkeypatch, commits, on_pull=tracker)

        assert (
            _run_main(
                ["wi", "rm", "--all", "--yes", "--skip-pull", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert tracker.count == 1
        assert not unchanged.exists()
        assert changed.exists()
        assert commits == [("chore: remove 1 entry", ["2026/10/unchanged.md"])]
        assert "警告: 確認後に変更されたため削除しない: changed.md\n次の操作: " in capsys.readouterr().out
