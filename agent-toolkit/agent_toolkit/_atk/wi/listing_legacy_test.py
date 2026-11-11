# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのlisting: legacyのテスト。"""

import datetime
import pathlib
import subprocess
import typing

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi import legacy as legacy_module
from agent_toolkit._atk.wi.layout import entry_path, entry_state_of
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file


class TestLegacyReservationMigration:
    """通常の読取の前に旧予約を移行し、軽量な読取では移行を後の通常の読取へ回すことを検証する。"""

    @staticmethod
    def _write_legacy_main(
        notes: pathlib.Path,
        *,
        reservation: str,
        state: str = "processing",
        legacy_layout: bool = False,
    ) -> pathlib.Path:
        path = (
            notes / state / "main.md"
            if legacy_layout
            else entry_path(notes, "main.md", fallback_date=datetime.date(2026, 10, 1))
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nstate: {state}\ntarget_repo: github.com/example/foo\ntype: awi\n"
            "depends_on: [companion.md, normal.md]\n"
            f"reservation: {reservation}\n"
            "target_commit_history: [abc123]\n---\n\nユーザー本文\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _write_companion(
        notes: pathlib.Path,
        filename: str = "companion.md",
        target_filename: str = "main.md",
    ) -> pathlib.Path:
        path = entry_path(notes, filename, fallback_date=datetime.date(2026, 10, 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nstate: inbox\ntarget_repo: internal/agent-toolkit/reservations\ntype: awi\n"
            "reservation_companion: {target_repo: github.com/example/foo, "
            f"target_filename: {target_filename}, token_hash: {'a' * 64}}}\n---\n\n内部項目\n",
            encoding="utf-8",
        )
        return path

    @pytest.mark.parametrize(
        "reservation",
        [
            (
                "{token_hash: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, owner: owner, "
                "generation: '1', reason: plan, reserved_at: '2026-08-08T00:00:00+00:00', "
                "updated_at: '2026-08-08T00:00:00+00:00', expires_at: '2099-01-01T00:00:00+00:00', "
                "companion: companion.md, companion_dependency_added: 'true', companion_dependency_filename: companion.md}"
            ),
            (
                "{token_hash: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, owner: owner, "
                "generation: '1', reason: plan, reserved_at: '2026-08-08T00:00:00+00:00', "
                "updated_at: '2026-08-08T00:00:00+00:00', expires_at: '2000-01-01T00:00:00+00:00', "
                "companion: companion.md, companion_dependency_added: 'true', companion_dependency_filename: companion.md}"
            ),
            "broken",
        ],
    )
    def test_normal_read_migrates_valid_expired_and_invalid_reservations_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        reservation: str,
    ) -> None:
        """予約の妥当性や期限にかかわらずユーザーデータを通常inboxへ戻す。"""
        notes = _setup_notes(tmp_path)
        self._write_legacy_main(notes, reservation=reservation)
        self._write_companion(notes)
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        for _ in range(2):
            with pytest.raises(SystemExit) as exc_info:
                atk.main(["wi", "list"], home=tmp_path)
            assert exc_info.value.code == 0

        migrated = entry_path(notes, "main.md")
        parsed = frontmatter.parse_frontmatter(migrated.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        assert metadata["state"] == "inbox"
        assert "reservation" not in metadata
        assert "target_commit_history" not in metadata
        assert metadata["depends_on"] == ["normal.md"]
        assert "ユーザー本文" in body
        assert not (notes / "processing/main.md").exists()
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/companion.md"))
        migration_commits = [
            call
            for call in git_calls
            if "commit" in call["cmd"] and any("legacy queue reservations" in value for value in call["cmd"])
        ]
        assert len(migration_commits) == 1

    def test_migration_skips_yaml_parsing_without_reservation_keys(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """予約・付随項目のキーを持つ項目が無ければYAMLを解析せずに終え、値や本文の同じ語では解析へ進まない。

        同期のたびに全件を解析すると、`repo_lock`を保持する時間が件数に比例して伸びるためである。
        """
        notes = _setup_notes(tmp_path)
        for filename, text in (
            (
                "20261001-000000-001.md",
                "---\nstate: inbox\ntype: uwi\nscope: feedback-reservation-public-contract\n---\n\n本文\n",
            ),
            ("20261001-000000-002.md", "---\nstate: processing\ntype: awi\n---\n\nreservation: 本文中の記述\n"),
        ):
            path = entry_path(notes, filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        def fail_parse(_text: str) -> None:
            raise AssertionError("予約キーの無い項目を解析した")

        def fail_commit(_root: pathlib.Path, _message: str, _paths: typing.Iterable[str], /) -> None:
            raise AssertionError("移行対象の無い状態でcommitした")

        monkeypatch.setattr(legacy_module, "parse_frontmatter", fail_parse)

        assert legacy_module.migrate_legacy_reservations(notes, assert_lock_fn=lambda _path: None, commit_fn=fail_commit) == 0

    def test_conflicting_inbox_entry_stops_migration_with_manual_steps(self, tmp_path: pathlib.Path) -> None:
        """移行先inboxに同名項目がある場合は何も変えず、比較と削除の手順を次の操作として返す。"""
        notes = _setup_notes(tmp_path, legacy_layout=True)
        main = self._write_legacy_main(notes, reservation="broken", legacy_layout=True)
        self._write_companion(notes)
        conflict = notes / "inbox/main.md"
        conflict.write_text("---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n別本文\n", encoding="utf-8")
        before = main.read_bytes()

        with pytest.raises(ActionableError, match="同名項目が存在します: main.md") as error_info:
            legacy_module.migrate_legacy_reservations(notes, assert_lock_fn=lambda _path: None, commit_fn=lambda *_args: None)

        assert main.read_bytes() == before
        assert f"git -C {notes} rm" in error_info.value.next_action

    def test_invalid_reservation_keeps_unrelated_dependency(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """破損予約の未検証ファイル名で通常依存を削除しない。"""
        notes = _setup_notes(tmp_path)
        self._write_legacy_main(
            notes,
            reservation="{companion_dependency_added: 'true', companion_dependency_filename: normal.md}",
        )
        self._write_companion(notes)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter.parse_frontmatter(entry_path(notes, "main.md").read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["depends_on"] == ["normal.md"]
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/companion.md"))

    def test_orphan_companion_and_only_its_dependencies_are_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """孤立した内部項目を削除し、通常依存を保持する。"""
        notes = _setup_notes(tmp_path)
        self._write_companion(notes, "orphan.md")
        dependent = _write_awi_file(notes, "dependent.md", body="本文")
        dependent.write_text(
            dependent.read_text(encoding="utf-8").replace(
                "type: awi\n",
                "type: awi\ndepends_on: [orphan.md, normal.md]\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter.parse_frontmatter(dependent.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["depends_on"] == ["normal.md"]
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/orphan.md"))

    def test_companion_like_user_metadata_is_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """内部repo以外の同名metadataを旧生成物と誤認しない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "user.md", body="ユーザー本文")
        original = path.read_text(encoding="utf-8").replace(
            "type: awi\n",
            "type: awi\nreservation_companion: {target_repo: github.com/example/foo}\n",
        )
        path.write_text(original, encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == original
        assert not any(any("legacy queue reservations" in value for value in call["cmd"]) for call in git_calls)

    def test_user_reservation_metadata_is_unchanged_without_legacy_companion(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """旧companionが無いユーザーmetadataは移行対象にしない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "user.md", body="ユーザー本文")
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8")
        original = path.read_text(encoding="utf-8").replace(
            "type: awi\n",
            "type: awi\nreservation: {owner: user, purpose: custom}\ntarget_commit_history: [custom]\n",
        )
        path.write_text(original, encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == original
        assert not any(any("legacy queue reservations" in value for value in call["cmd"]) for call in git_calls)

    def test_broken_frontmatter_is_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """frontmatterを解析できない項目は移行せず、既存の修復処理で扱えるよう元の内容のまま残す。"""
        notes = _setup_notes(tmp_path)
        path = entry_path(notes, "broken.md", fallback_date=datetime.date(2026, 10, 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        original = "---\ntarget_repo: [broken\nreservation: forged\n---\n本文\n"
        path.write_text(original, encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == original
        assert not any(any("legacy queue reservations" in value for value in call["cmd"]) for call in git_calls)

    def test_skip_pull_defers_migration_until_normal_read(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """軽量読取は通信と変更を避け、後続の通常読取で移行する。"""
        notes = _setup_notes(tmp_path)
        legacy = self._write_legacy_main(notes, reservation="broken")
        companion = self._write_companion(notes)
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as skip_exit:
            atk.main(["wi", "list", "--skip-pull", "--target-repo=all"], home=tmp_path)

        assert skip_exit.value.code == 0
        assert legacy.is_file()
        assert companion.is_file()
        assert not git_calls

        with pytest.raises(SystemExit) as normal_exit:
            atk.main(["wi", "list", "--target-repo=all"], home=tmp_path)

        assert normal_exit.value.code == 0
        assert entry_path(notes, "main.md") == legacy
        assert entry_state_of(legacy) == "inbox"
        assert not companion.exists()


class TestListPlanImplementationClassification:
    """トップレベルの`plan_file`を持つ項目を`plan`区分と表示し、未分類（`unclassified`）として表示しない。"""

    def test_missing_schedule_metadata_is_labeled_plan_implementation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """queue_schedule欠落時もトップレベルplan_fileを優先して`plan`区分と表示する。"""
        notes = _setup_notes(tmp_path)
        plan = tmp_path / "plan.md"
        path = _write_awi_file(notes, "plan.md", target_repo="github.com/example/repo", body="本文")
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: awi\n", f"type: awi\nplan_file: {plan}\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "[inbox/plan/ready]" in output
        assert "unclassified" not in output

    def test_missing_dependency_displays_repair_reason(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """修復対象のblocked項目へ具体的な理由を表示する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "awi.md", target_repo="github.com/example/repo", body="本文")
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [missing.md]\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--target-repo", "github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "blocked_reason=missing-dependency" in capsys.readouterr().out

    def test_stale_schedule_metadata_is_labeled_plan_implementation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本文ハッシュ不一致時もトップレベルplan_fileを優先して`plan`区分と表示する。"""
        notes = _setup_notes(tmp_path)
        plan = tmp_path / "plan.md"
        path = _write_awi_file(notes, "plan.md", target_repo="github.com/example/repo", body="本文")
        text = path.read_text(encoding="utf-8").replace(
            "type: awi\n",
            f"type: awi\nplan_file: {plan}\n",
        )
        path.write_text(text + "\n本文変更\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "[inbox/plan/ready]" in output
        assert "unclassified" not in output
