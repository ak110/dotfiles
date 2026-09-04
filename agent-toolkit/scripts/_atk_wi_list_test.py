"""atk (agent-toolkit `atk wi`) のlistサブコマンドのテスト。

AWI/`uwi`一覧出力・各種フィルター（target-repo・source・type・status・skip-pull・count）の
単体テストを集約する。他サブコマンドの分割先はatk_test.pyの分割方針一覧docstringを参照する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import unicodedata

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_wi_frontmatter as frontmatter  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position

# pylint: disable-next=wrong-import-position,import-error
from _atk_git_fake_test_helpers import make_git_remote_fake as _make_git_remote_fake  # noqa: E402
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
    _write_uwi_file,
)  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


@pytest.fixture(autouse=True)
def _clear_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存のテキスト出力テストを実行ホストのエージェント環境から隔離する。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class TestListEmpty:
    """listサブコマンド: inbox空の場合は何も出力しない。"""

    def test_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空時は標準出力が空であること。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSingle:
    """listサブコマンド: 1件のAWIを1行で出力する。"""

    def test_single_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1件のAWIがファイル名・`target_repo`・本文冒頭要約のタブ区切り1行で出力されること。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# awi\nfb-001.md: github.com/example/foo [inbox/normal/ready] 本文1\n"

    def test_cooldown_and_invalid_cooldown_display_stable_reasons(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """期限待ちと不正期限を安定した理由で表示する。"""
        notes = _setup_notes(tmp_path)
        pending = _write_awi_file(notes, "pending.md")
        invalid = _write_awi_file(notes, "invalid.md")
        pending.write_text(
            pending.read_text(encoding="utf-8").replace(
                "type: awi\n",
                "type: awi\ncooldown_until: '2999-01-01T00:00:00+00:00'\n",
            ),
            encoding="utf-8",
        )
        invalid.write_text(
            invalid.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ncooldown_until: bad\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path, now=datetime.datetime.now(datetime.UTC))

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "blocked_reason=cooldown-until cooldown_until=2999-01-01T00:00:00+00:00" in output
        assert "blocked_reason=invalid-cooldown" in output

    def test_working_plan_without_saved_copy_is_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """作業rootにだけ実体があるplan_fileをmissing-plan-fileとして表示する。"""
        notes = _setup_notes(tmp_path)
        entry = _write_awi_file(notes, "plan.md")
        working = pathlib.Path.home() / ".claude/plans/30-working-plan-a1b2.md"
        working.parent.mkdir(parents=True)
        working.write_text("# 計画\n", encoding="utf-8")
        entry.write_text(
            entry.read_text(encoding="utf-8").replace(
                "type: awi\n",
                "type: awi\nplan_file: $(atk config get private_notes)/plans/2026/08/30-working-plan-a1b2.md\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "blocked_reason=missing-plan-file" in capsys.readouterr().out


class TestLegacyReservationMigration:
    """通常読取前の旧予約移行と軽量読取での延期を検証する。"""

    @staticmethod
    def _write_legacy_main(
        notes: pathlib.Path,
        *,
        reservation: str,
        state: str = "processing",
    ) -> pathlib.Path:
        path = notes / state / "main.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\n"
            "depends_on: [companion.md, normal.md]\n"
            f"reservation: {reservation}\n"
            "target_commit_history: [abc123]\n---\n\n利用者本文\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _write_companion(
        notes: pathlib.Path,
        filename: str = "companion.md",
        target_filename: str = "main.md",
    ) -> pathlib.Path:
        path = notes / "inbox" / filename
        path.write_text(
            "---\ntarget_repo: internal/agent-toolkit/reservations\ntype: awi\n"
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
        """予約の妥当性や期限にかかわらず利用者データを通常inboxへ戻す。"""
        notes = _setup_notes(tmp_path)
        self._write_legacy_main(notes, reservation=reservation)
        self._write_companion(notes)
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        for _ in range(2):
            with pytest.raises(SystemExit) as exc_info:
                atk.main(["wi", "list"], home=tmp_path)
            assert exc_info.value.code == 0

        migrated = notes / "inbox/main.md"
        parsed = frontmatter.parse_frontmatter(migrated.read_text(encoding="utf-8"))
        assert parsed is not None
        metadata, body = parsed
        assert "reservation" not in metadata
        assert "target_commit_history" not in metadata
        assert metadata["depends_on"] == ["normal.md"]
        assert "利用者本文" in body
        assert not (notes / "processing/main.md").exists()
        assert not (notes / "inbox/companion.md").exists()
        migration_commits = [
            call
            for call in git_calls
            if "commit" in call["cmd"] and any("legacy queue reservations" in value for value in call["cmd"])
        ]
        assert len(migration_commits) == 1

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
        parsed = frontmatter.parse_frontmatter((notes / "inbox/main.md").read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["depends_on"] == ["normal.md"]
        assert not (notes / "inbox/companion.md").exists()

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
        assert not (notes / "inbox/orphan.md").exists()

    def test_companion_like_user_metadata_is_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """内部repo以外の同名metadataを旧生成物と誤認しない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "user.md", body="利用者本文")
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
        """旧companionが無い利用者metadataは移行対象にしない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "user.md", body="利用者本文")
        processing_path = notes / "processing" / path.name
        processing_path.parent.mkdir()
        path.rename(processing_path)
        path = processing_path
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
        """frontmatterを解析できない項目は既存修復経路へ残す。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox/broken.md"
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
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path)

        assert skip_exit.value.code == 0
        assert legacy.is_file()
        assert companion.is_file()
        assert not git_calls

        with pytest.raises(SystemExit) as normal_exit:
            atk.main(["wi", "list"], home=tmp_path)

        assert normal_exit.value.code == 0
        assert (notes / "inbox/main.md").is_file()
        assert not companion.exists()


class TestListPlanImplementationClassification:
    """独立キーを持つ計画実装型を未分類として分類委譲へ混入させない。"""

    def test_missing_schedule_metadata_is_labeled_plan_implementation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """queue_schedule欠落時もトップレベルplan_fileを優先して計画実装型と表示する。"""
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
        assert "[inbox/plan/blocked]" in output
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
        """本文ハッシュ不一致時もトップレベルplan_fileを優先して計画実装型と表示する。"""
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
        assert "[inbox/plan/blocked]" in output
        assert "unclassified" not in output


class TestListMalformedFrontmatter:
    """listサブコマンド: 異常frontmatterは`(unknown)`グループへ振り分けられる。"""

    @pytest.mark.parametrize(
        ("content", "label", "expected_exit"),
        [
            ("本文のみ\n", "frontmatterなし", 0),
            ("---\ncreated: 2024\n本文\n", "閉じ区切りなし", 0),
            ("---\ncreated: 2024\n---\n\n本文\n", "target_repo欠落", 2),
        ],
    )
    def test_malformed_frontmatter_falls_back_to_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        content: str,
        label: str,
        expected_exit: int,
    ) -> None:
        """typeを確定できない異常frontmatter形式は拒否される。"""
        del label  # parametrize idのみ
        notes = _setup_notes(tmp_path)
        (notes / "inbox" / "malformed.md").write_text(content, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == expected_exit
        captured = capsys.readouterr()
        if expected_exit == 0:
            assert "[inbox/frontmatter-broken/blocked]" in captured.out
            assert not captured.err
        else:
            assert not captured.out
            assert "frontmatterのtypeが不正または欠落" in captured.err


class TestListMultipleRepos:
    """listサブコマンド: 複数target_repo混在でも1件1行で全件出力される。"""

    def test_multiple_repos_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target_repoが異なる複数のAWIがそれぞれ1行で出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "# awi",
            "fb-001.md: github.com/example/foo [inbox/normal/ready] テスト本文",
            "fb-002.md: github.com/example/bar [inbox/normal/ready] テスト本文",
        ]

    def test_type_groups_sort_by_filename_independently_of_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """種別見出しを維持し、各グループ内を状態によらずファイル名順に並べる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "z-awi.md", body="AWI")
        awi_processing = _write_awi_file(notes, "a-awi.md", body="処理中AWI")
        (notes / "processing").mkdir()
        awi_processing.replace(notes / "processing" / awi_processing.name)
        _write_uwi_file(notes, "z-uwi.md", question="未回答", answer="")
        uwi_processing = _write_uwi_file(notes, "a-uwi.md", question="回答済み", answer="回答")
        uwi_processing.replace(notes / "processing" / uwi_processing.name)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        lines = output.splitlines()
        assert [line.split(":", 1)[0] for line in lines] == [
            "# awi",
            "a-awi.md",
            "z-awi.md",
            "# uwi",
            "a-uwi.md",
            "z-uwi.md",
        ]
        assert "[processing/" in lines[1]
        assert "[inbox/" in lines[2]
        assert "[processing/answered]" in lines[4]
        assert "[inbox/unanswered]" in lines[5]


class TestListTargetRepoFilter:
    """listサブコマンド: --target-repo指定で一致するエントリのみ出力する。"""

    def test_filter_matches_single_group(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数target_repo混在でも--target-repo指定値と一致するエントリのみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--target-repo=github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "github.com/example/foo" in captured.out
        assert "github.com/example/bar" not in captured.out

    def test_filter_matches_legacy_path_and_url_forms(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """ローカルパス指定は旧パス形と現行URL形を同じ対象集合として列挙する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_awi_file(notes, "legacy.md", target_repo=str(local_repo))
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo")
        _write_awi_file(notes, "missing.md", target_repo=str(tmp_path / "missing"))
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", f"--target-repo={local_repo}", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "legacy.md" in output
        assert "current.md" in output
        assert "missing.md" not in output

    def test_filter_expands_tilde(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """~プレフィックスのローカルパスがgit remote get-urlで正規化され、対応するエントリが出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/myrepo")
        monkeypatch.setenv("HOME", str(tmp_path))

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--target-repo=~/myrepo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# awi\nfb-001.md: github.com/example/myrepo [inbox/normal/ready] テスト本文\n"

    def test_filter_no_match_outputs_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致するエントリが存在しない場合、標準出力は空になる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--target-repo=github.com/example/nomatch"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSourceFilter:
    """listサブコマンド: --source指定でfrontmatterのsourceが一致するエントリのみ出力する。"""

    @pytest.mark.parametrize(
        ("source_filter", "expected_filename", "excluded_filename"),
        [
            ("session-review", "fb-001.md", "fb-002.md"),
            ("!session-review", "fb-002.md", "fb-001.md"),
        ],
    )
    def test_source_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        source_filter: str,
        expected_filename: str,
        excluded_filename: str,
    ) -> None:
        """--sourceの一致指定と否定指定が該当エントリだけを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", source="session-review")
        _write_awi_file(notes, "fb-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", f"--source={source_filter}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert expected_filename in captured.out
        assert excluded_filename not in captured.out

    @pytest.mark.parametrize("value", ["--source=", "--source=!"])
    def test_empty_source_value_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        value: str,
    ) -> None:
        """--source=・--source=!（空文字列）はargparseエラーでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", value], home=tmp_path)

        assert exc_info.value.code == 2

    def test_filter_matches_exact_source_for_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--source=NAME指定時、uwi側も同一sourceのエントリのみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, "uwi-001.md", source="session-review")
        _write_uwi_file(notes, "uwi-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--status=all", "--source=session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "uwi-001.md" in captured.out
        assert "uwi-002.md" not in captured.out


class TestListLegacyTypeValues:
    """`list`サブコマンド: `atk wi migrate`の実行前に保存された`type`値を読み取る。"""

    def test_legacy_type_values_are_listed_as_current_types(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """旧値のfeedback・tbdを持つ項目が、現行の種別の項目として一覧へ現れる。"""
        notes = _setup_notes(tmp_path)
        (notes / "inbox" / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n本文1\n",
            encoding="utf-8",
        )
        (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: tbd\n---\n\nq1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == (
            "# awi\nfb-001.md: github.com/example/foo [inbox/normal/ready] 本文1\n"
            f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"
        )

    def test_legacy_type_values_match_current_type_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--type=uwi`が旧値のtbdを持つ項目を選び、旧値は受理値に加わらない。"""
        notes = _setup_notes(tmp_path)
        (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: tbd\n---\n\nq1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"

        with pytest.raises(SystemExit) as legacy_info:
            atk.main(["wi", "list", "--type=tbd"], home=tmp_path)

        assert legacy_info.value.code == 2


class TestListTypeFilter:
    """`list`サブコマンド: `--type`でAWI/`uwi`出力を限定する。"""

    def test_type_awi_outputs_only_awi_section(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--type=awi`指定時はAWI部のみ出力され`uwi`ヘッダは出力されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=awi"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# awi\nfb-001.md: github.com/example/foo [inbox/normal/ready] 本文1\n"

    def test_type_uwi_outputs_status_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=uwi指定時はuwi部のみ出力され回答状況ラベルが付与される。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=no"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"

    def test_answered_uwi_displays_blocked_reason(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """回答済みUWIが依存異常でblockedの場合は両状態を表示する。"""
        notes = _setup_notes(tmp_path)
        path = _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="回答済み")
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: uwi\n", "type: uwi\ndepends_on: [missing.md]\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "[inbox/answered/blocked]" in output
        assert "blocked_reason=missing-dependency" in output

    def test_answered_uwi_status_label_omits_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """回答済みUWIの状態ラベルは種別を含まない従来形式である。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="回答")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/answered]" in captured.out
        assert "[uwi/" not in captured.out

    def test_type_all_omits_empty_section_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=all（既定）でuwi側が0件の場合はuwi種別ヘッダを省略する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# awi" in captured.out
        assert "# uwi" not in captured.out


class TestListSkipPull:
    """listサブコマンド: --skip-pull指定時はremote同期全体をスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はfetch・merge・rebaseが実行されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] in (["git", "fetch"], ["git", "merge"], ["git", "rebase"]) for c in git_calls)

    def test_recent_sync_is_reused_without_remote_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """直近の同期形跡がある通常一覧ではremote同期を省略して再利用を案内する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(call["cmd"][:2] in (["git", "fetch"], ["git", "merge"]) for call in git_calls)
        stderr = capsys.readouterr().err
        assert stderr == (
            "注記: 直近30秒に他プロセスを含む同期形跡があるため、直近の同期結果を再利用しました。"
            "最新化する場合は`--pull`を指定してください。\n"
        )

    def test_pull_forces_remote_sync_after_recent_sync(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--pull指定時は直近の同期形跡があってもfetch・mergeを実行する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        command = ["wi", "list", "--pull"]
        with pytest.raises(SystemExit) as exc_info:
            atk.main(command, home=tmp_path)

        assert exc_info.value.code == 0
        git_commands = [call["cmd"][:2] for call in git_calls]
        assert ["git", "fetch"] in git_commands
        assert ["git", "merge"] in git_commands

    def test_skip_pull_and_pull_are_mutually_exclusive(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--skip-pullと--pullの同時指定は終了コード2で拒否する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull", "--pull"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err


class TestListStatusFilter:
    """listサブコマンド: --answeredでuwi側のみ回答状況を限定する。"""

    @pytest.mark.parametrize(
        ("answered", "expected_suffix", "excluded_suffix"),
        [
            ("yes", "002.md", "001.md"),
            ("no", "001.md", "002.md"),
        ],
    )
    def test_answered_status_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        answered: str,
        expected_suffix: str,
        excluded_suffix: str,
    ) -> None:
        """--answered=yes/noが回答状況と一致するUWIだけを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", f"--answered={answered}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-{expected_suffix}" in captured.out
        assert f"{_FIXED_TIMESTAMP}-{excluded_suffix}" not in captured.out

    def test_active_covers_hold_for_both_types(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=activeがawiとUWIのどちらでもholdを含む3状態を返す。"""
        notes = _setup_notes(tmp_path)
        hold_dir = notes / "hold"
        hold_dir.mkdir(parents=True, exist_ok=True)
        (hold_dir / "fb-hold.md").write_text(
            "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\nhold本文\n",
            encoding="utf-8",
        )
        (hold_dir / "uwi-hold.md").write_text(
            "---\ntype: uwi\ntarget_repo: github.com/example/foo\n---\n\n## 質問\n\nhold質問\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--status=active", "--no-json"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-hold.md" in captured.out
        assert "uwi-hold.md" in captured.out

    def test_status_all_outputs_every_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=all指定時に全UWIが出力される。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.out
        assert f"{_FIXED_TIMESTAMP}-002.md" in captured.out

    def test_status_answered_does_not_affect_awi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--answered=yes指定時に回答概念を持たないAWIは除外される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文1")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# awi" not in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out

    def test_status_invalid_choice_exits_2(self, tmp_path: pathlib.Path) -> None:
        """--statusに不正値を指定するとargparseがexit 2で終了する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--status=invalid"], home=tmp_path)

        assert exc_info.value.code == 2

    @pytest.mark.parametrize("status", ["planning", "editing"])
    def test_status_rejects_withdrawn_states(self, tmp_path: pathlib.Path, status: str) -> None:
        """廃止した状態名を--statusへ渡すとargparseがexit 2で終了する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", f"--status={status}"], home=tmp_path)

        assert exc_info.value.code == 2


class TestListCount:
    """listサブコマンド: --count指定時は種別ヘッダ・エントリ行を抑制し件数のみ出力する。"""

    def test_count_outputs_total_of_awi_and_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--count`指定時にAWI件数とUWI件数の合計が整数1行で出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--count", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "3\n"

    def test_count_suppresses_headers_and_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--count指定時は種別ヘッダ・エントリ行を出力しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "1\n"

    def test_count_with_status_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--countと--statusを併用すると、statusフィルター適用後の件数が出力される。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=yes", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "1\n"

    def test_count_empty_inbox_outputs_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空時は0を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "0\n"


class TestListJson:
    """listサブコマンド: --jsonは端末幅に依存しないJSON Linesを返す。"""

    def test_json_preserves_long_fields_and_reports_readiness(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """狭い端末でも本文要約・target_repo・blocked理由を切り詰めず出力する。"""
        notes = _setup_notes(tmp_path)
        long_repo = "github.com/example/" + "repository-" * 12
        body = "本文の長い要約 " + "長文" * 100
        path = _write_awi_file(notes, "json.md", target_repo=long_repo, body=body)
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [missing.md]\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", lambda: os.terminal_size((40, 24)))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--json"], home=tmp_path)

        assert exc_info.value.code == 0
        record = json.loads(capsys.readouterr().out)
        assert record["filename"] == "json.md"
        assert record["type"] == "awi"
        assert record["target_repo"] == long_repo
        assert record["summary"] == body
        assert record["ready"] is False
        assert record["blocked_reason"] == "missing-dependency"

    def test_json_and_count_are_mutually_exclusive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--jsonと--countの同時指定はargparseの利用エラーとなる。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--json", "--count"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err

    @pytest.mark.parametrize("environment_name", _AGENT_ENVIRONMENT_VARIABLES)
    def test_agent_environment_defaults_to_json_lines(
        self,
        environment_name: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """各エージェント環境変数で明示指定なしの出力をJSON Linesにする。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md", body="全文")
        monkeypatch.setenv(environment_name, "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        assert json.loads(capsys.readouterr().out)["filename"] == "awi.md"

    def test_no_json_overrides_agent_default_and_count_keeps_integer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エージェント環境でも明示テキストと件数形式を優先する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md", body="全文")
        monkeypatch.setenv("CODEX_CI", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as text_exit:
            atk.main(["wi", "list", "--no-json"], home=tmp_path)
        assert text_exit.value.code == 0
        assert capsys.readouterr().out.startswith("# awi\n")

        with pytest.raises(SystemExit) as count_exit:
            atk.main(["wi", "list", "--count"], home=tmp_path)
        assert count_exit.value.code == 0
        assert capsys.readouterr().out == "1\n"


class TestMultipleFiltersCombinedAsAnd:
    """target-repo・source・type・status・answeredの同時指定がAND条件で対象を限定する。

    `--answered`はAWIを無条件除外する仕様（`_answered_matches`が`entry_type != WI_TYPE_UWI`時に
    `False`を返す）のため、`--answered=no`とtype不一致（awi）を1回の呼び出しへ同居させると
    type条件の除外効果がanswered条件の除外効果と区別できなくなる。
    target-repo・source・type・statusの4条件は`--answered=all`（無効化）の下で検証し、
    answered条件は同一の4条件（uwiのみ）を満たすエントリ同士の回答有無差分で別途検証する。
    """

    def test_target_repo_source_type_status_combined_narrows_to_intersection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target-repo・source・type・statusの4条件全てに一致するuwiだけを出力する（answeredは無効化）。"""
        matching_repo = "github.com/example/matching"
        notes = _setup_notes(tmp_path)
        # 4条件全てに一致する唯一のエントリ（uwi・inbox）。
        _write_uwi_file(notes, "uwi-matching.md", target_repo=matching_repo, source="session-review")
        # target-repoのみ不一致。
        _write_uwi_file(
            notes,
            "uwi-other-repo.md",
            target_repo="github.com/example/other",
            source="session-review",
        )
        # sourceのみ不一致。
        _write_uwi_file(notes, "uwi-other-source.md", target_repo=matching_repo, source="user-issue")
        # typeのみ不一致（`--answered=all`のためAWIも回答状況フィルターでは除外されない）。
        _write_awi_file(notes, "fb-other-type.md", target_repo=matching_repo, source="session-review")
        # statusのみ不一致（processing配下、--status=inboxで除外される）。
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True, exist_ok=True)
        (processing_dir / "uwi-other-status.md").write_text(
            f"---\ntarget_repo: {matching_repo}\ntype: uwi\nquestion_type: free-form\n"
            "source: session-review\n---\n\n## 質問\n\n本文\n\n## 回答\n\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "list",
                    f"--target-repo={matching_repo}",
                    "--source=session-review",
                    "--type=uwi",
                    "--status=inbox",
                    "--answered=all",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "uwi-matching.md" in captured.out
        assert "uwi-other-repo.md" not in captured.out
        assert "uwi-other-source.md" not in captured.out
        assert "fb-other-type.md" not in captured.out
        assert "uwi-other-status.md" not in captured.out

    def test_answered_narrows_within_already_matching_four_conditions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target-repo・source・type・statusが一致する2エントリのうち、未回答のみが`--answered=no`で残る。"""
        matching_repo = "github.com/example/matching"
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, "uwi-unanswered.md", target_repo=matching_repo, source="session-review", answer="")
        _write_uwi_file(
            notes,
            "uwi-answered.md",
            target_repo=matching_repo,
            source="session-review",
            answer="回答済み",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "list",
                    f"--target-repo={matching_repo}",
                    "--source=session-review",
                    "--type=uwi",
                    "--status=inbox",
                    "--answered=no",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "uwi-unanswered.md" in captured.out
        assert "uwi-answered.md" not in captured.out


class TestListNonTtyTargetRepo:
    """listサブコマンド: 非TTYでは対象repoと要約を短縮しないことを検証する。"""

    _LONG_REPO = "github.com/organization-name/very-long-repository-name-example"

    @staticmethod
    def _display_width(text: str) -> int:
        """東アジア文字幅に基づく出力文字列の表示幅を返す。"""
        return sum(2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1 for char in text)

    def test_awi_non_tty_preserves_full_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非TTYのAWI行はtarget_repoを完全に保持する。"""
        terminal_columns = 70

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((terminal_columns, 24))

        notes = _setup_notes(tmp_path)
        body = "本文" * 80
        _write_awi_file(notes, "fb-001.md", target_repo=self._LONG_REPO, body=body)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()
        assert self._LONG_REPO in captured.out
        assert body in captured.out
        assert any(self._display_width(line) > terminal_columns for line in output_lines)

    def test_awi_tty_shortens_to_terminal_width(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """TTYだけはtarget_repoと要約を端末幅へ短縮する。"""
        terminal_columns = 70
        notes = _setup_notes(tmp_path)
        body = "本文" * 80
        _write_awi_file(notes, "fb-001.md", target_repo=self._LONG_REPO, body=body)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", lambda: os.terminal_size((terminal_columns, 24)))
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        output_lines = capsys.readouterr().out.splitlines()
        assert self._LONG_REPO not in output_lines[-1]
        assert self._display_width(output_lines[-1]) <= terminal_columns

    def test_uwi_non_tty_preserves_full_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非TTYのUWI行はtarget_repoを完全に保持する。"""
        terminal_columns = 70

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((terminal_columns, 24))

        notes = _setup_notes(tmp_path)
        _write_uwi_file(
            notes,
            f"{_FIXED_TIMESTAMP}-001.md",
            question="q1",
            answer="",
            target_repo=self._LONG_REPO,
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=no"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()
        assert self._LONG_REPO in captured.out
        assert any(self._display_width(line) > terminal_columns for line in output_lines)
