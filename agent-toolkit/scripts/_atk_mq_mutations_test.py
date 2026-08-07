"""atk (agent-toolkit `atk mq`) のadopt/reject/rm/edit・パストラバーサル検証のテスト。

adopt・reject・rm・editサブコマンドと、ファイル名引数の不正値拒否の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_mq_list_test.py`・
`_atk_mq_show_test.py`・`_atk_mq_process_loop_test.py`に分離する。
位置引数の重複除去（FB7）テストは`too-many-lines`回避のため`_atk_mq_dedup_test.py`へ分離する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import argparse
import contextlib
import datetime
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_mutations as mutations  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_tbd as tbd  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
)  # noqa: E402  # pylint: disable=wrong-import-position

_RESERVATION_NOW = datetime.datetime(2026, 8, 8, tzinfo=datetime.UTC)


def _write_tbd_entry(
    notes: pathlib.Path,
    filename: str,
    *,
    question: str = "変更前の質問",
    answer: str = "既存回答",
    frontmatter: str = "target_repo: github.com/example/foo\ntype: tbd\nquestion_type: free-form",
) -> pathlib.Path:
    """非対話edit用のTBDエントリを書き込む。"""
    path = notes / "inbox" / filename
    path.write_text(
        f"---\n{frontmatter}\n---\n\n"
        f"{tbd.QUESTION_HEADING}\n\n{question}\n\n"
        f"{tbd.ANSWER_HEADING}\n\n{tbd.ANSWER_MARKER}\n{answer}\n",
        encoding="utf-8",
    )
    return path


def _disable_transition_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """状態遷移テストからprivate-notesのgit操作を除外する。"""
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)


def _patch_reservation_storage(
    monkeypatch: pytest.MonkeyPatch,
    worktree: pathlib.Path,
    *,
    head: str = "1" * 40,
) -> None:
    """予約テストからGit操作を除外し、worktree解決を固定する。"""
    _disable_transition_git(monkeypatch)
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access  # noqa: SLF001
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", worktree),
    )
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access  # noqa: SLF001
        "resolve_head_commit",
        lambda _worktree: head,
    )


def test_add_empty_feedback_keeps_detailed_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実質空feedbackのCLI拒否案内に判定条件と対象先頭を含める。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "add", "--target-repo", "github.com/example/foo", "-"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "空文字・空白のみ・箇条書きマーカー単独文字" in captured.err
    assert "該当メッセージの先頭: -" in captured.err


def test_flat_feedback_operations_are_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数遷移が戻り値とファイル移動を一貫して反映する。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    filenames = mutations.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "processing/entry.md").is_file()


class TestReservationMutations:
    """予約の取得、更新、統合、解除、回収を検証する。"""

    def test_reserve_and_release_preserve_user_dependencies(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """予約companionだけを追加・除去し、利用者依存を保持する。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        path = _write_feedback_file(notes, "feedback.md")
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        data, body = parsed
        data["depends_on"] = ["user-dependency.md"]
        path.write_text(frontmatter_parser.serialize_frontmatter(data, body), encoding="utf-8")
        _patch_reservation_storage(monkeypatch, worktree)

        token = mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["feedback.md", "feedback.md"],
            reason="計画作成",
            now=_RESERVATION_NOW,
        )

        reserved_path = notes / "processing/feedback.md"
        reserved = frontmatter_parser.parse_frontmatter(reserved_path.read_text(encoding="utf-8"))
        assert reserved is not None
        reservation = reserved[0]["reservation"]
        assert isinstance(reservation, dict)
        assert token not in reserved_path.read_text(encoding="utf-8")
        assert reservation["reserved_at"] == "2026-08-08T00:00:00+00:00"
        assert reservation["updated_at"] == "2026-08-08T00:00:00+00:00"
        assert reservation["companion_dependency_added"] == "true"
        companion = reservation["companion"]
        assert isinstance(companion, str)
        assert reservation["companion_dependency_filename"] == companion
        assert (notes / "inbox" / companion).is_file()
        assert len(list((notes / "inbox").glob("*.md"))) == 1
        assert reserved[0]["depends_on"] == ["user-dependency.md", companion]

        mutations.release_reservations(
            notes,
            filenames=["feedback.md"],
            reservation_token=token,
        )

        released = frontmatter_parser.parse_frontmatter((notes / "inbox/feedback.md").read_text(encoding="utf-8"))
        assert released is not None
        assert "reservation" not in released[0]
        assert released[0]["depends_on"] == ["user-dependency.md"]
        assert not (notes / "inbox" / companion).exists()

    def test_renew_updates_timestamp_with_generation_and_expiry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """更新時刻を予約取得時に初期化し、更新時に世代・期限と一括変更する。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _write_feedback_file(notes, "feedback.md")
        _patch_reservation_storage(monkeypatch, worktree)
        token = mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["feedback.md"],
            reason="計画作成",
            now=_RESERVATION_NOW,
        )

        mutations.renew_reservations(
            notes,
            filenames=["feedback.md"],
            reservation_token=token,
            now=_RESERVATION_NOW + datetime.timedelta(minutes=1),
        )

        parsed = frontmatter_parser.parse_frontmatter((notes / "processing/feedback.md").read_text(encoding="utf-8"))
        assert parsed is not None
        reservation = parsed[0]["reservation"]
        assert isinstance(reservation, dict)
        assert reservation["generation"] == "2"
        assert reservation["updated_at"] == "2026-08-08T00:01:00+00:00"
        assert reservation["expires_at"] == "2026-08-08T00:31:00+00:00"

    @pytest.mark.parametrize(
        "companion_kind",
        ("missing", "ordinary", "mismatched", "invalid-type", "malformed-frontmatter", "token-mismatch"),
    )
    def test_invalid_recovery_preserves_unverified_companion(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        companion_kind: str,
    ) -> None:
        """不正回収は予約との相互対応を検証できないcompanionを削除しない。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        feedback_path = _write_feedback_file(notes, "feedback.md")
        parsed_feedback = frontmatter_parser.parse_frontmatter(feedback_path.read_text(encoding="utf-8"))
        assert parsed_feedback is not None
        feedback_data, feedback_body = parsed_feedback
        feedback_data["depends_on"] = ["user-dependency.md"]
        feedback_path.write_text(frontmatter_parser.serialize_frontmatter(feedback_data, feedback_body), encoding="utf-8")
        _patch_reservation_storage(monkeypatch, worktree)
        mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["feedback.md"],
            reason="計画作成",
            now=_RESERVATION_NOW,
        )
        reserved_path = notes / "processing/feedback.md"
        parsed = frontmatter_parser.parse_frontmatter(reserved_path.read_text(encoding="utf-8"))
        assert parsed is not None
        data, body = parsed
        reservation = data["reservation"]
        assert isinstance(reservation, dict)
        companion = reservation["companion"]
        assert isinstance(companion, str)
        if companion_kind == "ordinary":
            ordinary_path = _write_feedback_file(notes, "ordinary.md")
            ordinary = frontmatter_parser.parse_frontmatter(ordinary_path.read_text(encoding="utf-8"))
            assert ordinary is not None
            ordinary_data, ordinary_body = ordinary
            ordinary_data["reservation_companion"] = {
                "target_repo": "github.com/example/foo",
                "target_filename": "feedback.md",
                "token_hash": reservation["token_hash"],
            }
            ordinary_path.write_text(frontmatter_parser.serialize_frontmatter(ordinary_data, ordinary_body), encoding="utf-8")
            reservation["companion"] = "ordinary.md"
            reservation["companion_dependency_filename"] = "ordinary.md"
            data["depends_on"] = ["user-dependency.md", "ordinary.md"]
        elif companion_kind == "mismatched":
            companion_path = notes / "inbox" / companion
            companion_path.write_text(
                companion_path.read_text(encoding="utf-8").replace("target_filename: feedback.md", "target_filename: other.md"),
                encoding="utf-8",
            )
        elif companion_kind == "invalid-type":
            companion_path = notes / "inbox" / companion
            companion_path.write_text(
                companion_path.read_text(encoding="utf-8").replace("type: feedback", "type: tbd"),
                encoding="utf-8",
            )
        elif companion_kind == "malformed-frontmatter":
            companion_path = notes / "inbox" / companion
            companion_path.write_text("frontmatter破損", encoding="utf-8")
        elif companion_kind == "token-mismatch":
            companion_path = notes / "inbox" / companion
            companion_path.write_text(
                companion_path.read_text(encoding="utf-8").replace("token_hash: ", "token_hash: b"),
                encoding="utf-8",
            )
        else:
            reservation["companion"] = "missing.md"
        data["reservation"] = reservation
        reserved_path.write_text(frontmatter_parser.serialize_frontmatter(data, body), encoding="utf-8")

        mutations.recover_reservation(
            notes,
            repo_path=str(worktree),
            filename="feedback.md",
            invalid=True,
            now=_RESERVATION_NOW,
        )

        assert (notes / "inbox/feedback.md").is_file()
        recovered = frontmatter_parser.parse_frontmatter((notes / "inbox/feedback.md").read_text(encoding="utf-8"))
        assert recovered is not None
        assert "reservation" not in recovered[0]
        expected_companion = "ordinary.md" if companion_kind == "ordinary" else companion
        assert recovered[0]["depends_on"] == ["user-dependency.md", expected_companion]
        if companion_kind == "ordinary":
            assert (notes / "inbox/ordinary.md").is_file()
        else:
            assert (notes / "inbox" / companion).is_file()

    def test_normal_mutation_rejects_reserved_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """所有者用操作以外の状態遷移は予約項目を変更しない。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _write_feedback_file(notes, "feedback.md")
        _patch_reservation_storage(monkeypatch, worktree)
        mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["feedback.md"],
            reason="計画作成",
            now=_RESERVATION_NOW,
        )

        with pytest.raises(mutations.ReservationConflict):
            mutations.transition_entries(
                notes,
                action="adopt",
                filenames=["feedback.md"],
                now=_RESERVATION_NOW,
            )

        assert (notes / "processing/feedback.md").is_file()

    def test_release_validates_all_companions_before_mutation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数解除の後半が不正でも、先行項目を部分解除しない。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        for filename in ("first.md", "second.md"):
            _write_feedback_file(notes, filename)
        _patch_reservation_storage(monkeypatch, worktree)
        token = mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["first.md", "second.md"],
            reason="計画作成",
            now=_RESERVATION_NOW,
        )
        second = frontmatter_parser.parse_frontmatter((notes / "processing/second.md").read_text(encoding="utf-8"))
        assert second is not None
        raw_second = second[0]["reservation"]
        assert isinstance(raw_second, dict)
        companion = raw_second["companion"]
        assert isinstance(companion, str)
        (notes / "inbox" / companion).write_text("---\ntype: feedback\n---\n\n破損\n", encoding="utf-8")

        with pytest.raises(mutations.ReservationConflict):
            mutations.release_reservations(
                notes,
                filenames=["first.md", "second.md"],
                reservation_token=token,
            )

        for filename in ("first.md", "second.md"):
            current = frontmatter_parser.parse_frontmatter((notes / "processing" / filename).read_text(encoding="utf-8"))
            assert current is not None
            assert "reservation" in current[0]

    def test_renewed_generation_rejects_stale_recovery(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """更新前の世代・期限による回収をCAS競合として拒否する。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _write_feedback_file(notes, "feedback.md")
        _patch_reservation_storage(monkeypatch, worktree)
        token = mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["feedback.md"],
            reason="計画作成",
            lease_minutes=1,
            now=_RESERVATION_NOW,
        )
        before = frontmatter_parser.parse_frontmatter((notes / "processing/feedback.md").read_text(encoding="utf-8"))
        assert before is not None
        raw_before = before[0]["reservation"]
        assert isinstance(raw_before, dict)
        old_expiry = raw_before["expires_at"]
        assert isinstance(old_expiry, str)

        mutations.renew_reservations(
            notes,
            filenames=["feedback.md"],
            reservation_token=token,
            now=_RESERVATION_NOW + datetime.timedelta(seconds=30),
        )
        active_before = {path.name for state in ("inbox", "processing") for path in (notes / state).glob("*.md")}

        with pytest.raises(mutations.ReservationConflict):
            mutations.recover_reservation(
                notes,
                repo_path=str(worktree),
                filename="feedback.md",
                expected_generation=1,
                expected_expires_at=old_expiry,
                tbd_message="所有工程を確認できますか。理由と判断根拠を確認し、再開条件を確定してください。",
                tbd_question_type="free-form",
                now=_RESERVATION_NOW + datetime.timedelta(hours=1),
            )

        current = frontmatter_parser.parse_frontmatter((notes / "processing/feedback.md").read_text(encoding="utf-8"))
        assert current is not None
        assert current[0]["reservation"]["generation"] == "2"
        assert {path.name for state in ("inbox", "processing") for path in (notes / state).glob("*.md")} == active_before

    def test_expired_recovery_creates_tbd_and_dependency_atomically(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """CAS成立時だけTBDを作成し、依存追加とinbox復帰を同時に行う。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _write_feedback_file(notes, "feedback.md")
        _patch_reservation_storage(monkeypatch, worktree)
        mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["feedback.md"],
            reason="計画作成",
            lease_minutes=1,
            now=_RESERVATION_NOW,
        )
        reserved = frontmatter_parser.parse_frontmatter((notes / "processing/feedback.md").read_text(encoding="utf-8"))
        assert reserved is not None
        raw = reserved[0]["reservation"]
        assert isinstance(raw, dict)
        expires_at = raw["expires_at"]
        assert isinstance(expires_at, str)

        mutations.recover_reservation(
            notes,
            repo_path=str(worktree),
            filename="feedback.md",
            expected_generation=1,
            expected_expires_at=expires_at,
            tbd_message="所有工程を確認できますか。理由と判断根拠を確認し、再開条件を確定してください。",
            tbd_question_type="free-form",
            now=_RESERVATION_NOW + datetime.timedelta(minutes=2),
        )

        recovered = frontmatter_parser.parse_frontmatter((notes / "inbox/feedback.md").read_text(encoding="utf-8"))
        assert recovered is not None
        assert "reservation" not in recovered[0]
        dependencies = recovered[0]["depends_on"]
        assert isinstance(dependencies, list)
        assert len(dependencies) == 1
        assert (notes / "inbox" / dependencies[0]).is_file()

    def test_merge_updates_target_commit_and_finishes_duplicates(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """予約統合でHEAD更新、予約解除、重複終端を一括反映する。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        canonical = _write_feedback_file(notes, "canonical.md")
        _write_feedback_file(notes, "duplicate.md")
        parsed = frontmatter_parser.parse_frontmatter(canonical.read_text(encoding="utf-8"))
        assert parsed is not None
        data, body = parsed
        data["target_commit"] = "0" * 40
        canonical.write_text(frontmatter_parser.serialize_frontmatter(data, body), encoding="utf-8")
        _patch_reservation_storage(monkeypatch, worktree, head="1" * 40)
        token = mutations.reserve_inbox_entries(
            notes,
            repo_path=str(worktree),
            filenames=["canonical.md"],
            reason="計画作成",
            now=_RESERVATION_NOW,
        )

        mutations.merge_inbox_entry(
            notes,
            repo_path=str(worktree),
            filename="canonical.md",
            message="統合後の本文",
            reservation_token=token,
            supersede=("duplicate.md",),
            now=_RESERVATION_NOW,
        )

        stored = frontmatter_parser.parse_frontmatter((notes / "inbox/canonical.md").read_text(encoding="utf-8"))
        assert stored is not None
        assert stored[0]["target_commit"] == "1" * 40
        assert stored[0]["target_commit_history"] == ["0" * 40]
        assert "reservation" not in stored[0]
        assert "統合後の本文" in stored[1]
        assert (notes / "rejected/duplicate.md").is_file()


class TestAnsweredTbdMutationGate:
    """adopt・reject直前の回答済みactive TBD確認を検証する。"""

    def test_answered_tbd_for_target_repo_stops_adopt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同じ対象repoの回答済みTBDがあれば明示targetのadoptを停止する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        _write_tbd_entry(notes, "answered.md")
        _disable_transition_git(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            mutations.transition_entries(
                notes,
                action="adopt",
                filenames=["feedback.md"],
                target_repo="github.com/example/foo",
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert "answered.md" in capsys.readouterr().err
        assert (notes / "inbox/feedback.md").is_file()

    def test_unanswered_tbd_does_not_stop_reject(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """未回答TBDだけならrejectを続行する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        _write_tbd_entry(notes, "unanswered.md", answer="")
        _disable_transition_git(monkeypatch)

        mutations.transition_entries(notes, action="reject", filenames=["feedback.md"], now=_FIXED_DT)

        assert (notes / "rejected/feedback.md").is_file()
        assert (notes / "inbox/unanswered.md").is_file()

    def test_answered_tbds_in_same_batch_do_not_block(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """回答済みTBD自身を同じbatchで採用する場合は自己ブロックしない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_entry(notes, "answered-a.md")
        _write_tbd_entry(notes, "answered-b.md")
        _disable_transition_git(monkeypatch)

        mutations.transition_entries(
            notes,
            action="adopt",
            filenames=["answered-a.md", "answered-b.md"],
            now=_FIXED_DT,
        )

        assert (notes / "adopted/answered-a.md").is_file()
        assert (notes / "adopted/answered-b.md").is_file()

    def test_omitted_target_repo_is_derived_from_mutation_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """target省略時も操作対象のfrontmatterからrepoを特定して停止する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        _write_tbd_entry(notes, "answered.md")
        _disable_transition_git(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            mutations.transition_entries(notes, action="reject", filenames=["feedback.md"], now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert (notes / "inbox/feedback.md").is_file()

    def test_multiple_target_repos_are_checked_independently(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数repoのbatchではいずれかに回答済みTBDがあれば停止する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "foo.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "bar.md", target_repo="github.com/example/bar")
        _write_tbd_entry(
            notes,
            "bar-answer.md",
            frontmatter="target_repo: github.com/example/bar\ntype: tbd\nquestion_type: free-form",
        )
        _disable_transition_git(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            mutations.transition_entries(
                notes,
                action="adopt",
                filenames=["foo.md", "bar.md"],
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert "bar-answer.md" in capsys.readouterr().err
        assert (notes / "inbox/foo.md").is_file()
        assert (notes / "inbox/bar.md").is_file()

    def test_unparsable_active_entry_stops_before_mutation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """active走査を完了できない場合は不在とみなさず採否処理を停止する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        (notes / "inbox/broken.md").write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")
        _disable_transition_git(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            mutations.transition_entries(notes, action="adopt", filenames=["feedback.md"], now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert "frontmatterを解析できない" in capsys.readouterr().err
        assert (notes / "inbox/feedback.md").is_file()

    def test_blocker_stops_before_stamp_move_and_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """回答済みTBD検出時はstamp・move・commitのいずれも開始しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "feedback.md")
        _write_tbd_entry(notes, "answered.md")
        _disable_transition_git(monkeypatch)
        mutation_calls: list[str] = []
        monkeypatch.setattr(mutations, "_stamp_result", lambda *_args, **_kwargs: mutation_calls.append("stamp"))
        monkeypatch.setattr(mutations.shutil, "move", lambda *_args, **_kwargs: mutation_calls.append("move"))
        monkeypatch.setattr(
            mutations,
            "_commit_and_push",
            lambda *_args, **_kwargs: mutation_calls.append("commit"),
        )

        with pytest.raises(SystemExit):
            mutations.transition_entries(notes, action="adopt", filenames=["feedback.md"], now=_FIXED_DT)

        assert not mutation_calls


def test_remove_targets_explicit_state_and_keeps_legacy_priority(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定時は指定側を削除し、省略時はprocessing優先を維持する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    content = "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n本文\n"
    inbox = notes / "inbox/same.md"
    processing = notes / "processing/same.md"
    processing.parent.mkdir()
    inbox.write_text(content, encoding="utf-8")
    processing.write_text(content, encoding="utf-8")

    removed = mutations.transition_entries(
        notes,
        action="remove",
        filenames=["same.md"],
        now=_FIXED_DT,
        state="inbox",
        expected_content=content,
    )
    assert removed == ["same.md"]
    assert not inbox.exists()
    assert processing.read_text(encoding="utf-8") == content

    inbox.write_text(content, encoding="utf-8")
    removed = mutations.transition_entries(
        notes,
        action="remove",
        filenames=["same.md"],
        now=_FIXED_DT,
        force=True,
    )
    assert removed == ["same.md"]
    assert inbox.exists()
    assert not processing.exists()


def test_remove_rejects_changed_and_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """確認後の内容変更とUTF-8読取り不能を競合として削除しない。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    original = "---\ntype: feedback\n---\n\n確認時本文\n"

    changed = notes / "inbox/changed.md"
    changed.write_text(original.replace("確認時", "外部更新後"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[changed.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert changed.exists()

    pull_changed = notes / "inbox/pull-changed.md"
    pull_changed.write_text(original, encoding="utf-8")

    def update_during_pull(_path: pathlib.Path) -> None:
        pull_changed.write_text(original.replace("確認時", "pull後"), encoding="utf-8")

    monkeypatch.setattr(mutations, "_pull", update_during_pull)
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[pull_changed.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert pull_changed.read_text(encoding="utf-8").endswith("pull後本文\n")

    unreadable = notes / "inbox/unreadable.md"
    unreadable.write_bytes(b"\xff")
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[unreadable.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert unreadable.exists()


def _write_convert_plan(tmp_path: pathlib.Path, target_commit: str) -> pathlib.Path:
    """変換テスト用の計画ファイルを作成する。"""
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"# 計画\n\n## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{target_commit}`\n",
        encoding="utf-8",
    )
    return plan


def _write_convert_feedback(
    notes: pathlib.Path,
    filename: str,
    *,
    entry_type: str = "feedback",
    state: str = "inbox",
    target_repo: str = "github.com/example/foo",
    target_commit: str = "a" * 40,
    schedule_mapping: str = "",
) -> pathlib.Path:
    """変換テスト用のエントリを書き込む。"""
    directory = notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\ntype: {entry_type}\ntarget_commit: {target_commit}\n{schedule_mapping}---\n\n本文\n",
        encoding="utf-8",
    )
    return path


def _disable_convert_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """変換テストでprivate-notesへのgit操作を無効化する。"""
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize("state", ["inbox", "processing"])
def test_convert_to_plan_replaces_legacy_schedule_with_top_level_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """変換が状態を問わず計画・依存をトップレベルへ正規化する。"""
    notes = _setup_notes(tmp_path)
    schedule_mapping = (
        "queue_schedule:\n"
        "  body_sha256: stale\n"
        "  normalized_target_repo: github.com/example/foo\n"
        "  type: normal\n"
        "  dependency:\n"
        "    kind: none\n"
        "  target_files: []\n"
        "  carry_count: 2\n"
        "  carry_reasons: [limit-exceeded, conflict]\n"
        "  last_deferral_run_id: run-1\n"
        "  last_deferral_reason: conflict\n"
    )
    path = _write_convert_feedback(notes, "feedback.md", state=state, schedule_mapping=schedule_mapping)
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    details = mutations.convert_entry_to_plan(
        notes,
        filename="feedback.md",
        plan_file=str(plan),
        depends_on=("dependency.md", "dependency", "dependency.md"),
        target_repo="github.com/example/foo",
    )

    text = path.read_text(encoding="utf-8")
    parsed = frontmatter_parser.parse_frontmatter(text)
    assert parsed is not None
    data, _body = parsed
    assert data["plan_file"] == str(plan)
    assert data["depends_on"] == ["dependency.md"]
    assert "queue_schedule" not in data
    assert details["target_commit"] == "a" * 40
    assert details["depends_on"] == ["dependency.md"]


@pytest.mark.parametrize(
    ("plan_value", "expected"),
    [("relative.md", "絶対パス"), ("missing", "実在する通常ファイル")],
)
def test_convert_to_plan_rejects_invalid_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    plan_value: str,
    expected: str,
) -> None:
    """相対パスと未存在の計画ファイルを拒否する。"""
    notes = _setup_notes(tmp_path)
    _write_convert_feedback(notes, "feedback.md")
    _disable_convert_git(monkeypatch)
    value = plan_value if plan_value == "relative.md" else str(tmp_path / plan_value)

    with pytest.raises(mutations.WebInputError, match=expected):
        mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=value)


def test_convert_to_plan_rejects_tbd_repo_mismatch_and_self_dependency(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TBD、投入先不一致、自己依存をそれぞれ拒否する。"""
    notes = _setup_notes(tmp_path)
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    _write_convert_feedback(notes, "tbd.md", entry_type="tbd")
    with pytest.raises(mutations.WebInputError, match="feedbackだけ"):
        mutations.convert_entry_to_plan(notes, filename="tbd.md", plan_file=str(plan))

    _write_convert_feedback(notes, "feedback.md")
    with pytest.raises(mutations.WebInputError, match="target_repoが一致しません"):
        mutations.convert_entry_to_plan(
            notes,
            filename="feedback.md",
            plan_file=str(plan),
            target_repo="github.com/example/other",
        )
    with pytest.raises(mutations.WebInputError, match="自分自身"):
        mutations.convert_entry_to_plan(
            notes,
            filename="feedback.md",
            plan_file=str(plan),
            depends_on=("feedback",),
        )


def test_set_dependencies_updates_normal_feedback_without_converting_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通常feedbackの型と本文を保ち、依存だけを正規化して更新する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(
        notes,
        "feedback.md",
        schedule_mapping="queue_schedule:\n  dependency:\n    kind: none\n",
    )
    _disable_convert_git(monkeypatch)

    details = mutations.set_entry_dependencies(
        notes,
        filename="feedback.md",
        depends_on=("dependency", "dependency.md"),
        target_repo="github.com/example/foo",
    )

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["type"] == "feedback"
    assert "plan_file" not in data
    assert data["depends_on"] == ["dependency.md"]
    assert "queue_schedule" not in data
    assert "本文" in body
    assert details["depends_on"] == ["dependency.md"]


def test_set_dependencies_can_clear_dependencies(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """依存オプション省略時は既存の明示依存を解除する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(notes, "feedback.md")
    text = path.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: [old.md]\n")
    path.write_text(text, encoding="utf-8")
    _disable_convert_git(monkeypatch)

    mutations.set_entry_dependencies(notes, filename="feedback.md", depends_on=())

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert "depends_on" not in parsed[0]


def test_convert_to_plan_keeps_saved_change_when_push_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit後のpush失敗契約に従い、変換済みファイルを保持して例外を伝播する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_feedback(notes, "feedback.md")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)

    commit_calls: list[tuple[object, ...]] = []

    def fail_after_commit(*args: object, **_kwargs: object) -> None:
        commit_calls.append(args)
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(mutations, "_commit_and_push", fail_after_commit)

    with pytest.raises(subprocess.CalledProcessError):
        mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["plan_file"] == str(plan)
    assert commit_calls[0][2] == ["inbox/feedback.md"]

    push_calls: list[pathlib.Path] = []
    monkeypatch.setattr(
        mutations,
        "_commit_and_push",
        lambda *_args, **_kwargs: pytest.fail("再実行時に新規commitを作成してはならない"),
    )
    monkeypatch.setattr(mutations, "_push_pending_commits", push_calls.append)

    mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))

    assert push_calls == [notes]


def test_convert_to_plan_pushes_pending_commit_before_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再実行時の未送信commitをff-only pullより先に同期する。"""
    notes = _setup_notes(tmp_path)
    _write_convert_feedback(notes, "feedback.md")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    events: list[str] = []
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: events.append("push"))
    monkeypatch.setattr(mutations, "_pull", lambda _path: events.append("pull"))
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)

    mutations.convert_entry_to_plan(notes, filename="feedback.md", plan_file=str(plan))

    assert events[:2] == ["push", "pull"]


def test_cmd_convert_to_plan_displays_saved_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """変換コマンドが保存後に返された照合対象を表示する。"""
    details: dict[str, object | None] = {
        "target_repo": "github.com/example/foo",
        "target_commit": "a" * 40,
        "plan_file": "/tmp/plan.md",
        "depends_on": ["dependency.md"],
    }
    monkeypatch.setattr(mutations, "convert_entry_to_plan", lambda *_args, **_kwargs: details)
    args = argparse.Namespace(
        filename="feedback.md",
        plan_file="/tmp/plan.md",
        depends_on=["dependency.md"],
        target_repo="github.com/example/foo",
    )

    mutations._cmd_convert_to_plan(args, tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    output = capsys.readouterr().out
    assert "target_repo: github.com/example/foo" in output
    assert f"target_commit: {'a' * 40}" in output
    assert "plan_file: /tmp/plan.md" in output
    assert "depends_on: dependency.md" in output


def test_return_to_inbox_moves_processing_to_inbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """return-to-inboxがprocessingからinboxへ戻す。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    mutations.transition_entries(notes, action="start-processing", filenames=["entry.md"], now=_FIXED_DT)
    filenames = mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "inbox/entry.md").is_file()
    assert not (notes / "processing/entry.md").exists()


def test_return_to_inbox_missing_file_reports_processing_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """return-to-inboxで未存在ファイルを指定するとprocessing側の状態名で案内する。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "return-to-inbox", "nonexistent.md"], home=tmp_path)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "processingに存在しません" in captured.err


class TestAdoptSingle:
    """adoptサブコマンド: 1件指定でinboxからadopted/へ移動しコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のadopt実行でinboxから移動されadopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "adopted" / "fb-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (adopted)" in commit_cmd

    def test_adopt_bare_stem_from_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """拡張子.md省略入力がinbox側の実体を解決してadoptedへ移動する（fb 20260721-164301-001反映）。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "20260721-160220-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            # 拡張子.mdを省略した引数でadoptを呼ぶ
            atk.main(["mq", "adopt", "20260721-160220-001"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "20260721-160220-001.md").exists()
        assert (notes / "adopted" / "20260721-160220-001.md").exists()


class TestAdoptMultiple:
    """adoptサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のadoptで全件がadopted/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        _write_feedback_file(notes, "fb-003.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "fb-002.md", "fb-003.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        assert not (inbox / "fb-003.md").exists()
        adopted = notes / "adopted"
        assert (adopted / "fb-001.md").exists()
        assert (adopted / "fb-002.md").exists()
        assert (adopted / "fb-003.md").exists()

        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 3 entries (adopted)" in commit_cmds[0]


class TestAdoptZeroArgs:
    """adoptサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt"], home=tmp_path)

        assert exc_info.value.code == 2


class TestAdoptMissing:
    """adoptサブコマンド: 存在しないファイル指定でexit 2となる。"""

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestAdoptStampWithNoteAndCommit:
    """adopt: --note・--commit指定時に`## 処理結果`節へ全項目が追記される。"""

    def test_stamp_written_with_all_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note・--commit指定時、adopted/配下のファイル末尾に採否・処理日時・対応commit・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="元本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "adopt", "fb-001.md", "--note", "採用理由サマリー", "--commit", "abc1234"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: abc1234" in adopted_text
        assert "- メモ: 採用理由サマリー" in adopted_text


class TestAdoptStampWithCategory:
    """adopt: --category指定時に`## 処理結果`節へカテゴリが追記される。"""

    def test_stamp_written_with_category(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--category指定時、adopted/配下のファイル末尾にカテゴリ行が追記される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "- カテゴリ: scope-escalation" in adopted_text


class TestAdoptCategoryGate:
    """adopt: 同一カテゴリの採用件数が閾値へ到達した場合に警告を出力する。"""

    def test_below_threshold_has_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一カテゴリの採用件数が閾値未満の場合は標準エラー出力へ警告しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "採用件数" not in captured.err

    def test_threshold_reached_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一カテゴリの採用件数が閾値へ到達した場合は標準エラー出力へ警告する。"""
        notes = _setup_notes(tmp_path)
        adopted = notes / "adopted"
        adopted.mkdir(parents=True, exist_ok=True)
        for index in range(1, 3):
            (adopted / f"old-{index}.md").write_text(
                "---\ntarget_repo: github.com/example/foo\n---\n\n"
                "## 処理結果\n\n"
                "- 採否: adopted\n"
                "- カテゴリ: scope-escalation\n",
                encoding="utf-8",
            )
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "カテゴリ「scope-escalation」の採用件数が3件に到達した" in captured.err
        assert "上位カテゴリでの規範化・仕組み化" in captured.err


class TestAdoptStampWithoutOptional:
    """adopt: --note・--commit省略時も必須項目のみ追記される。"""

    def test_stamp_written_with_required_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """引数省略時、`## 処理結果`節に採否・処理日時のみ追記され、対応commit・メモ行は含まれない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: " not in adopted_text
        assert "- メモ: " not in adopted_text
        assert "- カテゴリ: " not in adopted_text


class TestRejectDeletes:
    """rejectサブコマンド: ファイルをinboxからrejected/へ移動する。"""

    def test_single_file_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rejectでファイルがinboxから移動されrejected/に置かれコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "rejected" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (rejected)" in commit_cmd


class TestRejectStampWithNote:
    """reject: --note指定時に`## 処理結果`節へメモが追記される。"""

    def test_reject_stamp_note_written(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note指定時、rejected/配下のファイル末尾に採否・処理日時・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "--note", "不採用理由"], home=tmp_path)

        assert exc_info.value.code == 0
        rejected_text = (notes / "rejected" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in rejected_text
        assert "- 採否: rejected" in rejected_text
        assert "- メモ: 不採用理由" in rejected_text


class TestRejectMultiple:
    """rejectサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_rejected_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrejectで両方がrejected/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        rejected = notes / "rejected"
        assert (rejected / "fb-001.md").exists()
        assert (rejected / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 2 entries (rejected)" in commit_cmds[0]


class TestRejectZeroArgs:
    """rejectサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject"], home=tmp_path)

        assert exc_info.value.code == 2


class TestRmSingle:
    """rmサブコマンド: 単純削除とコミット件名を検証する。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rmで対象ファイルが削除されコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in commit_cmd

    def test_processing_file_removed_with_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルは`--force`指定時のみrm対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "--force", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing_dir / "fb-001.md").exists()

    def test_processing_file_rejected_without_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--force`未指定時、processing配下のファイルは削除を拒否されexit 2する（フィードバック20260723-153526-001反映）。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (processing_dir / "fb-001.md").exists()
        captured = capsys.readouterr()
        assert "processing状態のファイルは既定で削除を保護します" in captured.err
        assert "fb-001.md" in captured.err

    def test_missing_file_reports_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processingいずれにも存在しない場合、両状態を明記したメッセージでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestRmMultiple:
    """rmサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrmで両方削除と単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 entries" in commit_cmds[0]


class TestEditNoEditor:
    """editサブコマンド: $EDITOR未設定でexit 1となる。"""

    def test_no_editor_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITORが未設定の場合はexit 1と案内が出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.delenv("EDITOR", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "EDITOR" in captured.err


class TestEditWithChanges:
    """editサブコマンド: 編集後差分ありでcommit・push実行。"""

    def test_edit_with_changes_commits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """編集後にファイル差分があればコミット・pushが実行される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd

    def test_processing_file_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルも編集対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (processing_dir / "fb-001.md").read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_editor_target_repo_change_invalidates_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """対話編集でtarget_repoを変更した場合は旧リポジトリのtarget_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: feedback\n",
                f"type: feedback\ntarget_commit: {'a' * 40}\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(
                    editor_path.read_text(encoding="utf-8").replace(
                        "target_repo: github.com/example/foo",
                        "target_repo: github.com/example/new",
                    ),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_missing_file_reports_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processingいずれにも存在しない場合、両状態を明記したメッセージでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestEditNoChanges:
    """editサブコマンド: 差分なしでcommitせず終了。"""

    def test_edit_no_changes_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集後にファイル差分がなければコミットされず案内のみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestNoninteractiveEdit:
    """editサブコマンドのMESSAGE指定による非対話編集を検証する。"""

    def test_feedback_body_updates_without_editor_and_preserves_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """EDITOR未設定でも本文を更新し、未指定メタデータを保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前", source="session-review")
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == (
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\nsource: session-review\n---\n\n編集後\n"
        )

    def test_message_does_not_start_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE指定時はEDITORが設定済みでもエディターを起動しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "must-not-run")
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[0] == "must-not-run":
                pytest.fail("MESSAGE指定時にEDITORが起動された")
            return _make_subprocess_fake(git_calls)(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0

    def test_target_repo_is_normalized_and_existing_frontmatter_lines_are_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """明示したtarget_repoを正規化し、他の意味的なキーを保持する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text(
            "---\n# 保持するコメント\ntarget_repo: old.example/a/b\n\n"
            "target_repo: old.example/a/b\ntype: feedback\nsource: manual\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ntarget_repo: https://github.com/Example/Repo.git\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0] == {
            "target_repo": "github.com/example/repo",
            "type": "feedback",
            "source": "manual",
        }
        assert parsed[1] == "\n編集後\n"

    @pytest.mark.parametrize(
        ("message", "exit_code", "error_fragment"),
        [
            ("---\ntype: tbd\n---\n\n本文", 2, "typeを変更"),
            ("---\nscope: item\n---\n\n本文", 1, "feedbackでは指定できない"),
            (" \n-\n ", 1, "実質空"),
        ],
    )
    def test_feedback_rejects_invalid_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
        exit_code: int,
        error_fragment: str,
    ) -> None:
        """種別変更・TBD専用キー・実質空本文を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == exit_code
        assert error_fragment in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_existing_file_path_is_rejected_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """実在ファイルパスだけのMESSAGEをtracebackなしで拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        message_file = tmp_path / "message.txt"
        message_file.write_text("編集後", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", str(message_file)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ファイルパス" in captured.err
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n編集前\n")

    def test_empty_tbd_add_is_allowed_but_empty_edit_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空のTBD質問はaddで許容し、既存質問を削除するeditでは拒否する。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as add_exit:
            atk.main(
                [
                    "mq",
                    "add",
                    "--target-repo",
                    "github.com/example/foo",
                    "--type=tbd",
                    "--question-type=free-form",
                    "",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert add_exit.value.code == 0
        filename = f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"
        assert (notes / "inbox" / filename).is_file()

        with pytest.raises(SystemExit) as edit_exit:
            atk.main(["mq", "edit", filename, ""], home=tmp_path)

        assert edit_exit.value.code == 1
        captured = capsys.readouterr()
        assert "質問本文は空にできません" in captured.err
        assert "Traceback" not in captured.err

    def test_processing_feedback_can_be_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing配下のfeedbackも非対話で編集する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir()
        path = processing / "fb-001.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_tbd_question_and_scope_update_preserves_answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """TBDの質問とscopeだけを更新し、回答領域を保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(
            notes,
            "tbd-001.md",
            frontmatter=("target_repo: github.com/example/foo\ntype: tbd\nscope: old\nquestion_type: choice\nchoices: A,B"),
        )
        original_answer = path.read_text(encoding="utf-8").split(tbd.ANSWER_HEADING, maxsplit=1)[1]
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nscope: new\n---\n\n変更後の質問"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", message], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert "scope: new" in content
        assert "変更後の質問" in content
        assert content.split(tbd.ANSWER_HEADING, maxsplit=1)[1] == original_answer

    @pytest.mark.parametrize(
        "message",
        [
            f"変更後\n\n{tbd.ANSWER_HEADING}\n",
            f"変更後\n\n{tbd.ANSWER_MARKER}\n",
            "---\nquestion_type: invalid\n---\n\n変更後",
            "---\nquestion_type: choice\nchoices:\n---\n\n変更後",
        ],
    )
    def test_tbd_rejects_invalid_question_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
    ) -> None:
        """予約要素と不正な質問メタデータを拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(notes, "tbd-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8") == original

    def test_tbd_uses_last_answer_marker_and_preserves_answer_region(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """回答マーカー重複時も終端側を基準に質問だけを更新する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(
            notes,
            "tbd-001.md",
            question=f"前半\n\n{tbd.ANSWER_MARKER}\n\n後半",
            answer="保持する回答",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", "変更後の質問"], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert content.count(tbd.ANSWER_MARKER) == 1
        assert content.endswith(f"{tbd.ANSWER_MARKER}\n保持する回答\n")

    def test_expected_content_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """競合時は上書きせず、FILENAMEと未反映を案内する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        original_edit = mutations.edit_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: str,
            target_repo: str | None = None,
            lock_timeout: float = -1,
            expected_content: str | None = None,
        ) -> bool:
            path.write_text(path.read_text(encoding="utf-8").replace("編集前", "競合側の変更"), encoding="utf-8")
            return original_edit(
                private_notes,
                state=state,
                filename=filename,
                content=content,
                target_repo=target_repo,
                lock_timeout=lock_timeout,
                expected_content=expected_content,
            )

        monkeypatch.setattr(mutations, "edit_entry_content", conflict)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映されていません" in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n競合側の変更\n")

    def test_logically_identical_feedback_reports_no_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """論理本文が同一ならコミットせず差分なしを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "差分なし。" in capsys.readouterr().out
        assert not [call for call in git_calls if "commit" in call["cmd"]]

    def test_edit_rejects_explicit_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非対話edit経路からtarget_commitを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\ntarget_commit: {'b' * 40}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("updated_commit", ["b" * 40, None])
    def test_edit_content_validator_rejects_target_commit_change_or_removal(
        self,
        updated_commit: str | None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """共通保存境界が同一リポジトリのtarget_commit変更と削除を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            f"type: feedback\ntarget_commit: {'a' * 40}\n",
        )
        path.write_text(original, encoding="utf-8")
        replacement = "" if updated_commit is None else f"target_commit: {updated_commit}\n"
        updated = original.replace(f"target_commit: {'a' * 40}\n", replacement)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(mutations.WebInputError):
            mutations.edit_entry_content(
                notes,
                state="inbox",
                filename="fb-001.md",
                content=updated,
                lock_timeout=2.0,
            )

        assert path.read_text(encoding="utf-8") == original

    def test_edit_content_boundary_invalidates_target_commit_on_target_repo_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Web API共通保存境界はtarget_repo変更時に旧target_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            f"type: feedback\ntarget_commit: {'a' * 40}\n",
        )
        path.write_text(original, encoding="utf-8")
        updated = original.replace("target_repo: github.com/example/foo", "target_repo: github.com/example/new")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        assert mutations.edit_entry_content(
            notes,
            state="inbox",
            filename="fb-001.md",
            content=updated,
            lock_timeout=2.0,
        )

        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_edit_rejects_explicit_plan_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """edit経路からplan_fileを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nplan_file: /tmp/plan.md\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("updated_plan_file", ["/tmp/other.md", None])
    def test_edit_content_validator_rejects_plan_file_change_or_removal(
        self,
        updated_plan_file: str | None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """共通保存境界が既存plan_fileの変更と削除を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            "type: feedback\nplan_file: /tmp/plan.md\n",
        )
        path.write_text(original, encoding="utf-8")
        replacement = "" if updated_plan_file is None else f"plan_file: {updated_plan_file}\n"
        updated = original.replace("plan_file: /tmp/plan.md\n", replacement)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(mutations.WebInputError):
            mutations.edit_entry_content(
                notes,
                state="inbox",
                filename="fb-001.md",
                content=updated,
                lock_timeout=2.0,
            )

        assert path.read_text(encoding="utf-8") == original

    def test_edit_preserves_plan_file_when_only_body_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """plan_fileを持つ項目も本文だけの編集を許容する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            "type: feedback\nplan_file: /tmp/plan.md\n",
        )
        path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["plan_file"] == "/tmp/plan.md"

    @pytest.mark.parametrize(
        ("reserved_key", "reserved_value"),
        [("repair_target", "broken.md"), ("repair_kind", "frontmatter")],
    )
    def test_edit_rejects_explicit_repair_metadata(
        self,
        reserved_key: str,
        reserved_value: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """edit経路から修復TBDの予約キーを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\n{reserved_key}: {reserved_value}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_raises_web_input_error_when_frontmatter_is_corrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter全体が破損している場合は編集を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        assert "frontmatterが破損" in capsys.readouterr().err


class TestEditNoArg:
    """editサブコマンド: 無引数時はinbox配下のファイル名順最大値（最終追加分）を対象とする。"""

    def test_edit_no_arg_selects_max_filename(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル存在時はファイル名順の最大値（最終追加分）が編集対象になる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "20240101-100000-001.md", body="旧")
        latest = _write_feedback_file(notes, "20240201-100000-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                # 対象選択（最終追加分）の検証はcommit対象の相対パスで行う。
                editor_path = pathlib.Path(cmd[1])
                content = editor_path.read_text(encoding="utf-8")
                editor_path.write_text(content.replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd
        add_cmd = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "add"]][0]
        assert str(latest.relative_to(notes)) in add_cmd

    def test_edit_no_arg_exits_on_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空の場合はexit 2でstderr案内を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox" in captured.err


class TestStartProcessingSingle:
    """start-processingサブコマンド: 1件指定でinboxからprocessing/へ移動しコミットする。"""

    def test_single_file_moved_to_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のstart-processing実行でinboxから移動されprocessing/に置かれコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "processing" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: start processing 1 entry" in commit_cmd


class TestStartProcessingMultiple:
    """start-processingサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_moved_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のstart-processingで両方がprocessing/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        processing = notes / "processing"
        assert (processing / "fb-001.md").exists()
        assert (processing / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: start processing 2 entries" in commit_cmds[0]


class TestStartProcessingMissing:
    """start-processingサブコマンド: 存在しないファイル指定でexit 2となる。"""

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inboxに存在しません" in captured.err


class TestAdoptFromProcessing:
    """adopt: processing配下のファイルもadopted/へ移動できる。"""

    def test_adopt_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがadopt対象に含まれadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "adopted" / "fb-p.md").exists()


class TestRejectFromProcessing:
    """reject: processing配下のファイルもrejected/へ移動できる。"""

    def test_reject_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがreject対象に含まれrejected/へ移動する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "rejected" / "fb-p.md").exists()


class TestProcessingPrecedence:
    """同名ファイルがinbox・processing双方に存在する場合processingを優先する。"""

    def test_adopt_prefers_processing_when_both_exist(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """同名ファイルがinbox・processing双方に存在する場合、processing側が移動元として選ばれる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md")
        inbox_path = notes / "inbox" / "fb-dup.md"
        inbox_path.write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\ninbox本文\n",
            encoding="utf-8",
        )
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-dup.md"], home=tmp_path)

        assert exc_info.value.code == 0
        # processing側が移動元として選ばれるため、inbox側は残存しprocessing側は消える。
        assert inbox_path.exists()
        assert not processing_path.exists()
        adopted_path = notes / "adopted" / "fb-dup.md"
        assert adopted_path.exists()
        # 実際に移動されたのはprocessing側の内容であることを確認する。
        assert "processing本文" in adopted_path.read_text(encoding="utf-8")


class TestTargetRepoVerification:
    """mutation系サブコマンド: `--target-repo`指定時のfrontmatter一致検証を検証する。

    既定のfrontmatter`target_repo`は`github.com/example/foo`（`_write_feedback_file`既定値）。
    """

    def test_adopt_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """adopt: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "target_repo不一致" in captured.err
        assert (notes / "inbox" / "fb-001.md").exists()
        assert not (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: `--target-repo`一致時は通常通りadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--target-repo", "github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_reject_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """reject: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_start_processing_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "start-processing", "fb-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_unspecified_target_repo_is_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--target-repo`未指定時は検証されず既存挙動のまま処理が進む。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_bare_stem_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: 拡張子.md省略入力でも`--target-repo`不一致時に検証が回避されない（回帰確認）。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001", "--target-repo", "github.com/other/repo"], home=tmp_path)
        assert exc_info.value.code == 2
        assert not (notes / "adopted" / "fb-001.md").exists()


class TestPathTraversalRejection:
    """パストラバーサル系の不正引数は早期に拒否されること。"""

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape.md",
            "subdir/file.md",
            "/abs/path.md",
            "..\\windows.md",
            "..",
            ".",
            "",
        ],
    )
    def test_rejects_bad_filenames(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        bad: str,
    ) -> None:
        """不正なファイル名引数はexit 2でstderr案内を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", bad], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err
