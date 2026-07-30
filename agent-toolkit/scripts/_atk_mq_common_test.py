"""`atk mq`共通の警告・通知処理を検証する。"""

import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time

import filelock
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_common as _common  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_schedule as schedule  # noqa: E402  # pylint: disable=wrong-import-position


def _write_tbd(
    private_notes: pathlib.Path,
    filename: str,
    *,
    target_repo: str = "github.com/example/repo",
    question: str = "確認事項",
    answer: str = "",
) -> None:
    """テスト用TBDをinboxへ書き込む。"""
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / filename).write_text(
        f"---\ntarget_repo: {target_repo}\ntype: tbd\n---\n\n## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )


def _write_feedback(
    private_notes: pathlib.Path,
    filename: str,
    *,
    dependency: schedule.Dependency | None = None,
    state: str = "inbox",
) -> pathlib.Path:
    """分類メタデータ付きのテスト用feedbackを書き込む。"""
    directory = private_notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    text = "---\ntarget_repo: github.com/example/repo\ntype: feedback\n---\n\n本文\n"
    metadata = schedule.ScheduleMetadata(
        schedule.body_sha256(text),
        "github.com/example/repo",
        "normal",
        dependency or schedule.Dependency("none"),
        None,
        ("README.md",),
        0,
        (),
    )
    path.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")
    return path


def _classify_tbd(path: pathlib.Path, dependency: schedule.Dependency | None = None) -> None:
    """既存のテスト用TBDへ分類メタデータを付与する。"""
    text = path.read_text(encoding="utf-8")
    metadata = schedule.ScheduleMetadata(
        schedule.body_sha256(text),
        "github.com/example/repo",
        "normal",
        dependency or schedule.Dependency("none"),
        None,
        ("README.md",),
        0,
        (),
    )
    path.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")


class TestScheduleEntryLoading:
    """frontmatter全体破損とtypeキー不正を区別する。"""

    @pytest.mark.parametrize(
        ("frontmatter_line", "expected"),
        [
            ("plan_file: /tmp/plan.md", "/tmp/plan.md"),
            ("source: manual", None),
            ("plan_file: [invalid]", None),
        ],
    )
    def test_load_schedule_entries_reads_string_plan_file(
        self,
        frontmatter_line: str,
        expected: str | None,
        tmp_path: pathlib.Path,
    ) -> None:
        """独立キーは文字列だけをQueueEntryへ読み込む。"""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "feedback.md").write_text(
            f"---\ntarget_repo: github.com/example/repo\ntype: feedback\n{frontmatter_line}\n---\n\n本文\n",
            encoding="utf-8",
        )

        entries = _common._load_schedule_entries(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            ("inbox",),
        )

        assert entries[0].plan_file == expected

    @pytest.mark.parametrize(
        ("repair_kind_line", "expected"),
        [
            ("", "frontmatter"),
            ("repair_kind: frontmatter\n", "frontmatter"),
            ("repair_kind: missing-plan-file\n", "missing-plan-file"),
            ("repair_kind: invalid\n", None),
        ],
    )
    def test_load_schedule_entries_reads_repair_kind_with_legacy_default(
        self,
        repair_kind_line: str,
        expected: schedule.RepairKind | None,
        tmp_path: pathlib.Path,
    ) -> None:
        """理由区分のない既存修復TBDをfrontmatter修復として読み込む。"""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "repair.md").write_text(
            "---\n"
            "target_repo: github.com/example/repo\n"
            "type: tbd\n"
            "repair_target: item.md\n"
            f"{repair_kind_line}"
            "question_type: free-form\n"
            "---\n\n"
            "## 質問\n\n修復する\n\n"
            "## 回答\n\n",
            encoding="utf-8",
        )

        entries = _common._load_schedule_entries(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            ("inbox",),
        )

        assert entries[0].repair_kind == expected

    def test_iter_entries_yields_frontmatter_broken_entry_without_exiting(self, tmp_path: pathlib.Path) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        path = inbox / "broken.md"
        path.write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")

        all_entries = tuple(_common.iter_entries(tmp_path, ("inbox",), None, "all"))

        assert all_entries[0][0] == path
        assert all_entries[0][4] is None

    def test_iter_entries_excludes_frontmatter_broken_entry_when_filtered_by_specific_type(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "broken.md").write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")

        feedback_entries = tuple(_common.iter_entries(tmp_path, ("inbox",), None, "feedback"))
        tbd_entries = tuple(_common.iter_entries(tmp_path, ("inbox",), None, "tbd"))

        assert not feedback_entries
        assert not tbd_entries

    @pytest.mark.parametrize("type_line", ["", "type: invalid\n"])
    def test_require_type_still_exits_when_type_key_itself_is_missing_or_invalid(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        type_line: str,
    ) -> None:
        path = tmp_path / "entry.md"
        text = f"---\ntarget_repo: github.com/example/repo\n{type_line}---\n本文\n"

        with pytest.raises(SystemExit) as exc_info:
            _common.entry_type_of(path, text)

        assert exc_info.value.code == 2
        assert "typeが不正または欠落" in capsys.readouterr().err

    def test_count_pending_entries_excludes_normal_entry_blocked_on_unanswered_external_dependency(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        _write_tbd(tmp_path, "answer.md")
        _write_feedback(
            tmp_path,
            "feedback.md",
            dependency=schedule.Dependency(
                "external-user",
                condition="回答後",
                tbd_filename="answer.md",
            ),
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 0

    def test_count_pending_entries_excludes_normal_entry_blocked_on_chained_dependency_to_unanswered_external(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        _write_tbd(tmp_path, "answer.md")
        _write_feedback(
            tmp_path,
            "dependency.md",
            dependency=schedule.Dependency(
                "external-user",
                condition="回答後",
                tbd_filename="answer.md",
            ),
        )
        _write_feedback(
            tmp_path,
            "feedback.md",
            dependency=schedule.Dependency("entries", filenames=("dependency.md",)),
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 0

    def test_count_pending_entries_includes_normal_entry_with_answered_external_dependency(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        _write_tbd(tmp_path, "answer.md", answer="回答済み")
        answer = tmp_path / "inbox" / "answer.md"
        adopted = tmp_path / "adopted"
        adopted.mkdir()
        answer.rename(adopted / answer.name)
        _write_feedback(
            tmp_path,
            "feedback.md",
            dependency=schedule.Dependency(
                "external-user",
                condition="回答後",
                tbd_filename="answer.md",
            ),
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 1

    def test_count_pending_entries_includes_unclassified_entry(self, tmp_path: pathlib.Path) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "feedback.md").write_text(
            "---\ntarget_repo: github.com/example/repo\ntype: feedback\n---\n\n本文\n",
            encoding="utf-8",
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 1

    def test_count_pending_entries_includes_entry_with_missing_dependency_tbd_request(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        _write_feedback(
            tmp_path,
            "feedback.md",
            dependency=schedule.Dependency("entries", filenames=("missing.md",)),
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 1

    def test_count_pending_entries_excludes_unanswered_tbd_and_does_not_double_count_answered_tbd(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        _write_tbd(tmp_path, "answer.md")
        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 0

        answer_path = tmp_path / "inbox" / "answer.md"
        answer_path.write_text(answer_path.read_text(encoding="utf-8") + "\n回答済み\n", encoding="utf-8")

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 1

    def test_count_pending_entries_returns_zero_when_only_answered_tbd_remains_with_no_actionable_work(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        _write_tbd(tmp_path, "dependency.md")
        _write_tbd(tmp_path, "answer.md", answer="回答済み")
        answer_path = tmp_path / "inbox" / "answer.md"
        _classify_tbd(
            answer_path,
            schedule.Dependency(
                "external-user",
                condition="依存先の回答後",
                tbd_filename="dependency.md",
            ),
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 0

    def test_count_pending_entries_counts_frontmatter_broken_item_only_until_repair_tbd_is_filed(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "broken.md").write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 1

        (inbox / "repair.md").write_text(
            "---\n"
            "target_repo: github.com/example/repo\n"
            "type: tbd\n"
            "question_type: free-form\n"
            "repair_target: broken.md\n"
            "---\n\n"
            "## 質問\n\n修復する\n\n"
            "## 回答\n\n",
            encoding="utf-8",
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 0

    def test_count_pending_entries_counts_missing_plan_only_until_repair_tbd_is_filed(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """消失した計画ファイルの修復TBD要求を1件と数え、投入後は待機状態とする。"""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        missing_plan = tmp_path / "missing-plan.md"
        (inbox / "plan.md").write_text(
            f"---\ntarget_repo: github.com/example/repo\ntype: feedback\nplan_file: {missing_plan}\n---\n\n本文\n",
            encoding="utf-8",
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 1

        (inbox / "repair.md").write_text(
            "---\n"
            "target_repo: github.com/example/repo\n"
            "type: tbd\n"
            "question_type: free-form\n"
            "repair_target: plan.md\n"
            "repair_kind: missing-plan-file\n"
            "---\n\n"
            "## 質問\n\n修復する\n\n"
            "## 回答\n\n",
            encoding="utf-8",
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 0

    def test_count_pending_entries_uses_existing_independent_plan_file(self, tmp_path: pathlib.Path) -> None:
        """分類メタデータ欠落時も独立キーの実在計画ファイルを読み込み、実行対象へ数える。"""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        plan = tmp_path / "plan.md"
        plan.write_text("### 対象ファイル一覧\n\n- [ ] `README.md`\n", encoding="utf-8")
        (inbox / "plan.md").write_text(
            f"---\ntarget_repo: github.com/example/repo\ntype: feedback\nplan_file: {plan}\n---\n\n本文\n",
            encoding="utf-8",
        )

        assert _common.count_pending_entries(tmp_path, "github.com/example/repo") == 1


class TestWarnSpaceSeparatedOption:
    """空白区切りオプションの検出条件を検証する。"""

    @pytest.mark.parametrize(
        "top_command,subcommand",
        [("mq", "adopt"), ("mq", "reject"), ("mq", "adopt")],
    )
    @pytest.mark.parametrize("option", ["--note", "--commit"])
    def test_warns_for_target_subcommands(
        self,
        top_command: str,
        subcommand: str,
        option: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象サブコマンドの空白区切り指定では推奨形式を警告する。"""
        _common.warn_space_separated_option([top_command, subcommand, "item.md", option, "value"])

        assert capsys.readouterr().err == f"警告: {option}は{option}=VALUE形式で渡すことを推奨します。\n"

    @pytest.mark.parametrize(
        "argv",
        [
            ["mq", "add", "/repo", "adopt", "--note", "value"],
            ["mq", "adopt", "item.md", "--note=value"],
            ["mq", "adopt", "item.md", "--note", "value=with-equals"],
            ["mq", "adopt", "item.md", "--note", "--target-repo=example/repo"],
        ],
    )
    def test_does_not_warn_for_excluded_forms(
        self,
        argv: list[str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象外サブコマンド・等号形式・次オプションでは警告しない。"""
        _common.warn_space_separated_option(argv)

        assert not capsys.readouterr().err


class TestNotifyUnansweredTbdsIfAny:
    """未回答TBD通知の件数・フィルター・形式を検証する。"""

    def test_does_not_notify_without_unanswered_entries(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """TBDが0件または全件回答済みの場合は何も通知しない。"""
        _write_tbd(tmp_path, "answered.md", answer="回答済み")

        _common.notify_unanswered_tbds_if_any(tmp_path, None)

        assert not capsys.readouterr().err

    def test_notifies_one_unanswered_entry(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未回答TBDが1件の場合はヘッダと1行を通知する。"""
        _write_tbd(tmp_path, "one.md", question="最初の質問")

        _common.notify_unanswered_tbds_if_any(tmp_path, None)

        assert capsys.readouterr().err == "# tbd\none.md: github.com/example/repo [inbox/unanswered] 最初の質問\n"

    def test_notifies_matching_unanswered_entries_in_filename_order(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数件では対象リポジトリの未回答項目だけをファイル名順で通知する。"""
        _write_tbd(tmp_path, "002.md", question="質問2")
        _write_tbd(tmp_path, "001.md", question="質問1")
        _write_tbd(tmp_path, "003.md", target_repo="github.com/example/other", question="対象外")

        _common.notify_unanswered_tbds_if_any(tmp_path, "github.com/example/repo")

        assert capsys.readouterr().err == (
            "# tbd\n001.md: github.com/example/repo [inbox/unanswered] 質問1\n"
            "002.md: github.com/example/repo [inbox/unanswered] 質問2\n"
        )

    def test_narrow_terminal_truncates_long_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """狭幅端末(50桁)で長いtarget_repoが動的省略幅内へ収まること。

        `_atk_mq_list.py`の狭幅端末対応（`_target_repo_budget`・`_truncate_target_repo`）を
        本関数も共有して適用していることを検証する。
        """
        long_repo = "github.com/organization-name/very-long-repository-name-example"
        _write_tbd(tmp_path, "one.md", target_repo=long_repo, question="最初の質問")
        monkeypatch.setattr(shutil, "get_terminal_size", lambda: os.terminal_size((50, 24)))

        _common.notify_unanswered_tbds_if_any(tmp_path, None)

        line = capsys.readouterr().err.splitlines()[1]
        display_repo = line.split(": ", 1)[1].split(" [", 1)[0]
        budget = _common._target_repo_budget("one.md", "unanswered")  # noqa: SLF001  # pylint: disable=protected-access
        assert _common._display_width(display_repo) <= budget  # noqa: SLF001  # pylint: disable=protected-access
        assert display_repo != long_repo


class TestIsExistingDir:
    """長大な文字列候補に対する`is_existing_dir`のOSError耐性を検証する。"""

    def test_returns_true_for_existing_directory(self, tmp_path: pathlib.Path) -> None:
        """実在ディレクトリはTrueを返す。"""
        assert _common.is_existing_dir(tmp_path) is True

    def test_returns_false_for_missing_path(self, tmp_path: pathlib.Path) -> None:
        """存在しないパスはFalseを返す。"""
        assert _common.is_existing_dir(tmp_path / "missing") is False

    def test_returns_false_for_oversized_name_without_raising(self) -> None:
        """OS上限を超える長さの文字列でも`OSError`を送出せずFalseを返す。"""
        oversized = pathlib.Path("x" * 5000)

        assert _common.is_existing_dir(oversized) is False


class TestRepoLock:
    """`_repo_lock`のプロセス間排他動作を検証する。"""

    @pytest.fixture(autouse=True)
    def _isolate_lock_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ロックファイル配置先を実環境の`user_state_dir`から隔離する。"""
        monkeypatch.setattr(_common.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))

    def test_second_acquire_times_out_while_held(self, tmp_path: pathlib.Path) -> None:
        """1つ目のロック保持中は、別インスタンスからの2つ目の取得がタイムアウトする。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        lock1 = _common._repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        lock1.acquire()
        try:
            lock2 = _common._repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
            with pytest.raises(filelock.Timeout):
                lock2.acquire(timeout=0.2)
        finally:
            lock1.release()

    def test_constructor_timeout_bounds_plain_with_statement(self, tmp_path: pathlib.Path) -> None:
        """`_repo_lock(..., timeout=...)`のコンストラクタ既定値が`with lock:`（引数無し取得）へ伝搬する。

        Web要求経路は`acquire(timeout=...)`を明示呼び出しせず`with _repo_lock(private_notes, timeout=...):`
        の形でロックを使うため、コンストラクタで指定した`timeout`が実際の`with`文へ反映されることを保証する。
        """
        target = tmp_path / "private-notes"
        target.mkdir()
        lock1 = _common._repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        lock1.acquire()
        try:
            with (
                pytest.raises(filelock.Timeout),
                _common._repo_lock(target, timeout=0.2),  # pylint: disable=protected-access  # noqa: SLF001
            ):
                pass
        finally:
            lock1.release()

    def test_second_acquire_succeeds_after_release(self, tmp_path: pathlib.Path) -> None:
        """1つ目のロック解放後は、別インスタンスからの2つ目の取得が成功する。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        with _common._repo_lock(target):  # pylint: disable=protected-access  # noqa: SLF001
            pass
        lock2 = _common._repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        with lock2:
            assert lock2.is_locked

    def test_concurrent_transactions_are_serialized(self, tmp_path: pathlib.Path) -> None:
        """2スレッドが同時に`_repo_lock`を取得しても、臨界区間が直列化されること。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        order: list[str] = []

        def worker(label: str) -> None:
            with _common._repo_lock(target):  # pylint: disable=protected-access  # noqa: SLF001
                order.append(f"{label}-start")
                time.sleep(0.05)
                order.append(f"{label}-end")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join()
        t2.join()

        assert order in (
            ["a-start", "a-end", "b-start", "b-end"],
            ["b-start", "b-end", "a-start", "a-end"],
        )


class TestAssertRepoLockHeld:
    """`_assert_repo_lock_held`の不変条件表明を検証する。"""

    def test_pull_raises_runtime_error_when_lock_not_held(self, tmp_path: pathlib.Path) -> None:
        """`_repo_lock`未保持で`_pull`を呼ぶと`RuntimeError`を送出する。"""
        with pytest.raises(RuntimeError, match="不変条件違反"):
            _common._pull(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    def test_commit_and_push_raises_runtime_error_when_lock_not_held(self, tmp_path: pathlib.Path) -> None:
        """`_repo_lock`未保持で`_commit_and_push`を呼ぶと`RuntimeError`を送出する。"""
        with pytest.raises(RuntimeError, match="不変条件違反"):
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001


class TestCommitAndPushRetry:
    """`_commit_and_push`のpush失敗時再試行動作を検証する。"""

    @pytest.fixture(autouse=True)
    def _isolate_lock_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ロックファイル配置先を実環境の`user_state_dir`から隔離する。"""
        monkeypatch.setattr(_common.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))

    def test_retries_once_after_pull_rebase_on_push_failure(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """push失敗時は`pull --rebase`実行後にpushを1回だけ再試行する。"""
        calls: list[list[str]] = []
        push_attempts = 0

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            nonlocal push_attempts
            del cwd
            calls.append(args)
            if args[0] == "push":
                push_attempts += 1
                if push_attempts == 1:
                    raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_common, "_run_git", fake_run_git)

        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [
            ["add", "feedback"],
            ["commit", "-m", "chore: test"],
            ["push"],
            ["pull", "--rebase"],
            ["push"],
        ]

    def test_reraises_when_retry_push_also_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """再試行後もpushが失敗した場合は例外をそのまま送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            del cwd
            if args[0] == "push":
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_common, "_run_git", fake_run_git)

        with (
            pytest.raises(subprocess.CalledProcessError),
            _common._repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

    def test_aborts_rebase_and_reports_success_when_pull_rebase_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """経由した`pull --rebase`が失敗した場合は`git rebase --abort`を呼び、
        復元成功をstderrへ出力してから例外を送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            del cwd
            if args[0] == "push" or args == ["pull", "--rebase"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        abort_calls: list[list[str]] = []

        def fake_subprocess_run(args: list[str], cwd: pathlib.Path, check: bool) -> subprocess.CompletedProcess[bytes]:
            del cwd
            assert check is False
            abort_calls.append(args)
            return subprocess.CompletedProcess(args, returncode=0)

        monkeypatch.setattr(_common, "_run_git", fake_run_git)
        monkeypatch.setattr(_common.subprocess, "run", fake_subprocess_run)

        with pytest.raises(subprocess.CalledProcessError), _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

        assert abort_calls == [["git", "rebase", "--abort"]]
        assert "復元しました" in capsys.readouterr().err

    def test_warns_manual_recovery_when_rebase_abort_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`git rebase --abort`自体が失敗した場合は手動復旧が必要な旨をstderrへ出力してから例外を送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            del cwd
            if args[0] == "push" or args == ["pull", "--rebase"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        def fake_subprocess_run(args: list[str], cwd: pathlib.Path, check: bool) -> subprocess.CompletedProcess[bytes]:
            del cwd
            assert check is False
            return subprocess.CompletedProcess(args, returncode=1)

        monkeypatch.setattr(_common, "_run_git", fake_run_git)
        monkeypatch.setattr(_common.subprocess, "run", fake_subprocess_run)

        with (
            pytest.raises(subprocess.CalledProcessError),
            _common._repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

        assert "手動復旧が必要です" in capsys.readouterr().err


class TestValidateFilename:
    """`_validate_filename`の拡張子`.md`省略入力の正規化を検証する（fb 20260721-164301-001反映）。"""

    def test_appends_md_extension_when_missing(self, tmp_path: pathlib.Path) -> None:
        """拡張子.md省略入力は正規形へ補完される。"""
        (tmp_path / "20260721-160220-001.md").write_text("dummy", encoding="utf-8")
        path = _common._validate_filename("20260721-160220-001", tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert path == tmp_path / "20260721-160220-001.md"

    def test_preserves_md_extension_when_present(self, tmp_path: pathlib.Path) -> None:
        """拡張子.md付き入力は従来どおり解決される（後方互換）。"""
        (tmp_path / "20260721-160220-001.md").write_text("dummy", encoding="utf-8")
        path = _common._validate_filename("20260721-160220-001.md", tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert path == tmp_path / "20260721-160220-001.md"


class TestPrivateNotesAutoCreate:
    """`AGENT_TOOLKIT_PRIVATE_NOTES`未設定かつ既定パス不在時のローカルリポジトリ自動生成を検証する。

    conftestの`_atk_private_notes_env`autouseフィクスチャが全テストへ環境変数を設定するため、
    本クラスの各テストは`monkeypatch.delenv`で明示的に解除してから検証する。
    """

    @pytest.fixture(autouse=True)
    def _isolate_data_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """自動生成先を実環境の`user_data_dir`から隔離し、既定の環境変数上書きを解除する。"""
        monkeypatch.delenv("AGENT_TOOLKIT_PRIVATE_NOTES", raising=False)
        monkeypatch.setattr(_common.platformdirs, "user_data_dir", lambda _name, **_kwargs: str(tmp_path / "data"))

    def test_private_notes_path_falls_back_to_platformdirs_when_default_missing(self, tmp_path: pathlib.Path) -> None:
        """既定パス`home/private-notes`が不在の場合、platformdirs配下へフォールバックする。"""
        home = tmp_path / "home"
        home.mkdir()
        resolved = _common._private_notes_path(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert resolved == tmp_path / "data" / "private-notes"

    def test_private_notes_path_prefers_existing_default(self, tmp_path: pathlib.Path) -> None:
        """既定パスが実在する場合はplatformdirsへフォールバックせずそちらを返す。"""
        home = tmp_path / "home"
        (home / "private-notes").mkdir(parents=True)
        resolved = _common._private_notes_path(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert resolved == home / "private-notes"

    def test_ensure_environment_initializes_local_repo(self, tmp_path: pathlib.Path) -> None:
        """既定パス不在時、`_ensure_environment`はローカルgitリポジトリを自動生成して返す。"""
        home = tmp_path / "home"
        home.mkdir()
        root = _common._ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert root == tmp_path / "data" / "private-notes"
        assert (root / ".git").is_dir()
        assert (root / _common._LOCAL_ONLY_MARKER).exists()  # pylint: disable=protected-access  # noqa: SLF001
        expected_state_dirs = (
            "inbox",
            "processing",
            "adopted",
            "rejected",
            "inbox",
            "adopted",
        )
        for name in expected_state_dirs:
            assert (root / name).is_dir()

    def test_ensure_environment_is_idempotent(self, tmp_path: pathlib.Path) -> None:
        """2回連続で呼んでも2回目は既存のローカルリポジトリをそのまま返す（再初期化しない）。"""
        home = tmp_path / "home"
        home.mkdir()
        first = _common._ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        marker = first / "sentinel.txt"
        marker.write_text("kept", encoding="utf-8")
        second = _common._ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert second == first
        assert marker.read_text(encoding="utf-8") == "kept"


_LEGACY_FEEDBACK = "---\ntarget_repo: github.com/example/repo\n---\n\n本文\n"
_LEGACY_TBD = "---\ntarget_repo: github.com/example/repo\nquestion_type: free-form\n---\n\n## 質問\n\nQ\n\n## 回答\n\n"


def _init_legacy_repo(root: pathlib.Path, entries: dict[str, str]) -> None:
    """旧2階層レイアウトのローカル限定リポジトリを`root`へ作成する。

    remote未設定を示すマーカーを置き、移行処理のpull・pushをスキップさせる。
    `entries`はrepo root相対パスと本文の対応とする。
    """
    root.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / _common._LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
    for relative, text in entries.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True)


def _git_stdout(root: pathlib.Path, *args: str) -> str:
    """`root`でgitコマンドを実行し標準出力を返す。"""
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    assert isinstance(result.stdout, str)
    return result.stdout


class TestMigrateLegacyLayout:
    """旧2階層レイアウトから平坦レイアウトへの自動移行を検証する。

    管理repoのパスはconftestの`_atk_private_notes_env`が`tmp_path/private-notes`へ差し替えるため、
    `_ensure_environment`へ渡すhomeは解決結果に影響しない。
    """

    def test_migrates_entries_and_removes_legacy_dirs(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """種別ディレクトリ配下のエントリへtypeを補って状態ディレクトリ直下へ移し、旧ディレクトリを削除する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_FEEDBACK,
                "feedback/adopted/20260101-000000-002.md": _LEGACY_FEEDBACK,
                "tbd/inbox/20260102-000000-001.md": _LEGACY_TBD,
            },
        )

        assert _common._ensure_environment(tmp_path) == root  # pylint: disable=protected-access  # noqa: SLF001

        assert not (root / "feedback").exists()
        assert not (root / "tbd").exists()
        assert (root / "inbox" / "20260101-000000-001.md").read_text(encoding="utf-8") == (
            "---\ntarget_repo: github.com/example/repo\ntype: feedback\n---\n\n本文\n"
        )
        assert (root / "adopted" / "20260101-000000-002.md").read_text(encoding="utf-8").splitlines()[2] == "type: feedback"
        assert (root / "inbox" / "20260102-000000-001.md").read_text(encoding="utf-8").splitlines()[1:4] == [
            "target_repo: github.com/example/repo",
            "type: tbd",
            "question_type: free-form",
        ]
        assert "3件を平坦レイアウトへ移行" in capsys.readouterr().err
        assert not _git_stdout(root, "status", "--porcelain")

    def test_is_noop_after_migration(self, tmp_path: pathlib.Path) -> None:
        """移行後の再実行では追加のコミットを生成しない。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(root, {"feedback/inbox/20260101-000000-001.md": _LEGACY_FEEDBACK})
        _common._ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        head = _git_stdout(root, "rev-parse", "HEAD")

        _common._ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert _git_stdout(root, "rev-parse", "HEAD") == head

    def test_removes_empty_legacy_dirs_without_commit(self, tmp_path: pathlib.Path) -> None:
        """エントリを含まない旧ディレクトリだけがある場合は削除のみで完結する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(root, {"inbox/20260101-000000-001.md": "---\ntarget_repo: r\ntype: feedback\n---\n\n本文\n"})
        (root / "feedback" / "inbox").mkdir(parents=True)
        head = _git_stdout(root, "rev-parse", "HEAD")

        _common._ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert not (root / "feedback").exists()
        assert _git_stdout(root, "rev-parse", "HEAD") == head
        assert not _git_stdout(root, "status", "--porcelain")

    def test_aborts_without_changes_when_entry_is_broken(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """frontmatterが不正なエントリがある場合、何も移さずexit 2で原因を案内する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_FEEDBACK,
                "feedback/inbox/20260101-000000-002.md": "frontmatterのない本文\n",
            },
        )

        with pytest.raises(SystemExit) as excinfo:
            _common._ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert excinfo.value.code == 2
        assert "frontmatterが不正" in capsys.readouterr().err
        assert (root / "feedback" / "inbox" / "20260101-000000-001.md").exists()
        assert not (root / "inbox").exists()

    def test_aborts_when_destination_conflicts(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """種別違いで同名のエントリがある場合は移行先衝突として中止する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_FEEDBACK,
                "tbd/inbox/20260101-000000-001.md": _LEGACY_TBD,
            },
        )

        with pytest.raises(SystemExit) as excinfo:
            _common._ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert excinfo.value.code == 2
        assert "移行先が既に存在" in capsys.readouterr().err


class TestHasRemote:
    """`_has_remote`のローカル限定マーカー判定を検証する。"""

    def test_true_when_marker_absent(self, tmp_path: pathlib.Path) -> None:
        """マーカーファイルが無い場合はTrue（通常のremote設定済みリポジトリ扱い）。"""
        assert _common._has_remote(tmp_path) is True  # pylint: disable=protected-access  # noqa: SLF001

    def test_false_when_marker_present(self, tmp_path: pathlib.Path) -> None:
        """マーカーファイルが存在する場合はFalse（ローカル限定自動生成リポジトリ扱い）。"""
        (tmp_path / _common._LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        assert _common._has_remote(tmp_path) is False  # pylint: disable=protected-access  # noqa: SLF001


class TestPullAndCommitPushSkipWithoutRemote:
    """remote未設定のローカル限定リポジトリではpull・pushをスキップすることを検証する。"""

    def test_pull_is_noop_without_remote(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """マーカー付きディレクトリでは`_pull`が`git pull`を実行しない。"""
        (tmp_path / _common._LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005
        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._pull(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert not calls

    def test_commit_and_push_skips_push_without_remote(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """マーカー付きディレクトリでは`_commit_and_push`がadd・commitのみ実行しpushしない。"""
        (tmp_path / _common._LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005
        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001
        assert calls == [["add", "feedback"], ["commit", "-m", "chore: test"]]
