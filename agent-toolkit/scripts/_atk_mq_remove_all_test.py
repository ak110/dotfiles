"""`atk mq rm --all`の公開CLI契約を検証する。"""

import contextlib
import io
import pathlib
import subprocess
import sys
from collections.abc import Callable

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_remove_all as remove_all  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import _setup_notes, _write_feedback_file  # noqa: E402  # pylint: disable=wrong-import-position


class _TtyInput(io.StringIO):
    """対話端末として応答するテスト用標準入力。"""

    def isatty(self) -> bool:
        """対話端末であることを返す。"""
        return True


def _write_entry(
    notes: pathlib.Path,
    state: str,
    filename: str,
    *,
    target_repo: str = "github.com/example/foo",
    entry_type: str = "feedback",
    body: str = "テスト本文",
) -> pathlib.Path:
    """指定状態へテスト用エントリを書き込む。"""
    directory = notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\ntype: {entry_type}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _patch_storage(
    monkeypatch: pytest.MonkeyPatch,
    commit_calls: list[tuple[str, list[str]]],
    *,
    on_pull: Callable[[], None] | None = None,
) -> None:
    """外部git操作を抑止し、commit要求を記録する。"""
    monkeypatch.setattr(remove_all, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())

    def fake_pull(_private_notes: pathlib.Path) -> None:
        if on_pull is not None:
            on_pull()

    monkeypatch.setattr(remove_all, "_pull", fake_pull)
    monkeypatch.setattr(
        remove_all,
        "_commit_and_push",
        lambda _private_notes, message, paths: commit_calls.append((message, list(paths))),
    )


def _run_main(argv: list[str], home: pathlib.Path) -> int:
    """`atk.main`の終了コードを返す。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=home)
    assert isinstance(exc_info.value.code, int)
    return exc_info.value.code


class TestRemoveAllArguments:
    """一括削除と個別削除の引数制約を検証する。"""

    @pytest.mark.parametrize(
        "argv",
        [
            ["mq", "rm", "--all"],
            ["mq", "rm", "--all", "--target-repo", "github.com/example/foo", "entry.md"],
            ["mq", "rm"],
            ["mq", "rm", "--yes", "entry.md"],
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
        assert "usage:" in capsys.readouterr().err
        assert not (tmp_path / "private-notes").exists()


class TestRemoveAllConfirmation:
    """一覧表示と1回確認の挙動を検証する。"""

    def test_confirms_once_and_removes_feedback_and_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """feedbackとTBDを一覧表示し、1回の承認で単一commitへまとめる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        _write_entry(notes, "inbox", "question.md", entry_type="tbd", body="## 質問\n\n確認事項\n\n## 回答\n")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        stdin = _TtyInput("y\n")
        monkeypatch.setattr(sys, "stdin", stdin)

        assert _run_main(["mq", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        captured = capsys.readouterr()
        assert "# feedback" in captured.out
        assert "[inbox/" in captured.out
        assert "# tbd" in captured.out
        assert "[inbox/unanswered]" in captured.out
        assert "上記2件を削除します" in captured.out
        assert stdin.tell() == 2
        assert not (notes / "inbox/feedback.md").exists()
        assert not (notes / "inbox/question.md").exists()
        assert commits == [("chore: remove 2 entries", ["inbox", "processing", "adopted", "rejected"])]

    @pytest.mark.parametrize("answer", ["n\n", "\n", "later\n"])
    def test_non_approval_keeps_all_candidates(
        self,
        answer: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """承認以外の入力では候補を保持し、commitしない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "feedback.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", _TtyInput(answer))

        assert _run_main(["mq", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        assert path.exists()
        assert not commits
        assert "削除を中止しました。" in capsys.readouterr().out

    def test_non_tty_requires_yes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非対話入力では`--yes`を案内して削除を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "feedback.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))

        assert _run_main(["mq", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 2

        assert path.exists()
        assert not commits
        assert "--yes" in capsys.readouterr().err

    def test_yes_shows_list_without_reading_input(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--yes`でも一覧を表示し、標準入力を読まずに削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "feedback.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        class _UnreadableInput:
            def readline(self, _size: int = -1, /) -> str:
                raise AssertionError("標準入力を読んではならない")

        monkeypatch.setattr(sys, "stdin", _UnreadableInput())

        assert (
            _run_main(
                ["mq", "rm", "--all", "--yes", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not path.exists()
        output = capsys.readouterr().out
        assert "# feedback" in output
        assert "[inbox/" in output
        assert len(commits) == 1


class TestRemoveAllScope:
    """状態・リポジトリ・種別による削除範囲を検証する。"""

    def test_removes_only_matching_active_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """一致するinbox・processingだけを種別横断で削除し、履歴と他リポジトリを保持する。"""
        notes = _setup_notes(tmp_path)
        matching_inbox = _write_entry(notes, "inbox", "feedback.md")
        matching_processing = _write_entry(notes, "processing", "question.md", entry_type="tbd")
        other_repo = _write_entry(notes, "inbox", "other.md", target_repo="github.com/example/other")
        adopted = _write_entry(notes, "adopted", "adopted.md")
        rejected = _write_entry(notes, "rejected", "rejected.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["mq", "rm", "--all", "--yes", "--force", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not matching_inbox.exists()
        assert not matching_processing.exists()
        assert other_repo.exists()
        assert adopted.exists()
        assert rejected.exists()

    def test_removes_legacy_path_and_url_forms_together(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """一括削除は旧パス形とURL形のactive項目を同じ対象として削除する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        subprocess.run(["git", "init", str(local_repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(local_repo), "remote", "add", "origin", "git@github.com:example/myrepo.git"],
            check=True,
        )
        legacy = _write_entry(notes, "inbox", "legacy.md", target_repo=str(local_repo))
        current = _write_entry(notes, "inbox", "current.md", target_repo="github.com/example/myrepo")
        missing = _write_entry(notes, "inbox", "missing.md", target_repo=str(tmp_path / "missing"))
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["mq", "rm", "--all", "--yes", "--target-repo", "github.com/example/myrepo"],
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
        """frontmatter破損または対象不明の項目を候補から除外する。"""
        notes = _setup_notes(tmp_path)
        broken = notes / "inbox/broken.md"
        broken.write_text("---\ntarget_repo: github.com/example/foo\n", encoding="utf-8")
        unknown_repo = notes / "inbox/unknown-repo.md"
        unknown_repo.write_text(
            "---\ntype: feedback\n---\n\n本文\n",
            encoding="utf-8",
        )
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["mq", "rm", "--all", "--yes", "--target-repo", "github.com/example/foo"],
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
        """候補0件では確認せず正常終了する。"""
        _setup_notes(tmp_path)
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", _TtyInput("標準入力を消費しない"))

        assert _run_main(["mq", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 0

        assert not commits
        assert "削除対象なし: github.com/example/foo" in capsys.readouterr().out

    def test_removes_same_filename_from_both_active_states(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """inboxとprocessingの同名項目を状態付き候補として両方削除する。"""
        notes = _setup_notes(tmp_path)
        inbox = _write_entry(notes, "inbox", "same.md")
        processing = _write_entry(notes, "processing", "same.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)

        assert (
            _run_main(
                ["mq", "rm", "--all", "--yes", "--force", "--target-repo", "github.com/example/foo"],
                tmp_path,
            )
            == 0
        )

        assert not inbox.exists()
        assert not processing.exists()


class TestRemoveAllProcessingProtection:
    """processing項目の明示的な保護解除を検証する。"""

    @pytest.mark.parametrize("assume_yes", [False, True])
    def test_force_is_required_independently_from_yes(
        self,
        assume_yes: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--yes`の有無によらずprocessing候補には`--force`を要求する。"""
        notes = _setup_notes(tmp_path)
        inbox = _write_entry(notes, "inbox", "inbox.md")
        processing = _write_entry(notes, "processing", "processing.md")
        commits: list[tuple[str, list[str]]] = []
        _patch_storage(monkeypatch, commits)
        monkeypatch.setattr(sys, "stdin", _TtyInput("y\n"))
        argv = ["mq", "rm", "--all", "--target-repo", "github.com/example/foo"]
        if assume_yes:
            argv.append("--yes")

        assert _run_main(argv, tmp_path) == 2

        assert inbox.exists()
        assert processing.exists()
        assert not commits
        assert "--force" in capsys.readouterr().err


class TestRemoveAllConcurrentChanges:
    """確認前後の候補変更を検出する。"""

    @pytest.mark.parametrize("change", ["add", "move", "edit", "remove"])
    def test_rejects_changed_snapshot(
        self,
        change: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """追加・移動・本文変更・削除のいずれでも全件削除を中止する。"""
        notes = _setup_notes(tmp_path)
        original = _write_entry(notes, "inbox", "original.md")
        commits: list[tuple[str, list[str]]] = []
        pull_count = 0

        def change_on_second_pull() -> None:
            nonlocal pull_count
            pull_count += 1
            if pull_count != 2:
                return
            if change == "add":
                _write_entry(notes, "inbox", "added.md")
            elif change == "move":
                target = notes / "processing/original.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                original.rename(target)
            elif change == "edit":
                original.write_text(original.read_text(encoding="utf-8") + "\n更新\n", encoding="utf-8")
            else:
                original.unlink()

        _patch_storage(monkeypatch, commits, on_pull=change_on_second_pull)
        monkeypatch.setattr(sys, "stdin", _TtyInput("yes\n"))

        assert _run_main(["mq", "rm", "--all", "--target-repo", "github.com/example/foo"], tmp_path) == 2

        assert not commits
        assert "確認後に削除対象が変更された" in capsys.readouterr().err
