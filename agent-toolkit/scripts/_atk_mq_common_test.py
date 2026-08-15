"""`atk mq`共通の警告・通知処理を検証する。"""

import datetime
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

import filelock
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_common as _common  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_readiness as _readiness  # noqa: E402  # pylint: disable=wrong-import-position


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
    depends_on: tuple[str, ...] = (),
    legacy_dependency: str | None = None,
    plan_file: pathlib.Path | None = None,
    state: str = "inbox",
    target_repo: str = "github.com/example/repo",
    cooldown_until: object | None = None,
) -> pathlib.Path:
    """readiness用frontmatterを持つテスト用feedbackを書き込む。"""
    directory = private_notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    lines = ["---", f"target_repo: {target_repo}", "type: feedback"]
    if depends_on:
        lines.extend(("depends_on:", *(f"  - {value}" for value in depends_on)))
    if legacy_dependency is not None:
        lines.extend(("queue_schedule:", "  dependency:", *legacy_dependency.splitlines()))
    if plan_file is not None:
        lines.append(f"plan_file: {plan_file}")
    if cooldown_until is not None:
        if isinstance(cooldown_until, str):
            lines.append(f"cooldown_until: {cooldown_until!r}")
        else:
            lines.append(f"cooldown_until: {cooldown_until!r}".lower())
    lines.extend(("---", "", "本文", ""))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_make_filename_completer_limits_states(tmp_path: pathlib.Path) -> None:
    """指定した状態のファイルだけを候補として返す。"""
    private_notes = tmp_path / "private-notes"
    _write_feedback(private_notes, "inbox.md", state="inbox")
    _write_feedback(private_notes, "processing.md", state="processing")
    completer = _common.make_filename_completer((_common.MQ_STATE_INBOX,))
    assert completer("") == ["inbox.md"]


def test_make_filename_completer_filters_entry_type(tmp_path: pathlib.Path) -> None:
    """種別を指定した場合はfrontmatterの種別が一致するものだけを返す。"""
    private_notes = tmp_path / "private-notes"
    _write_feedback(private_notes, "feedback.md")
    _write_tbd(private_notes, "tbd.md")
    completer = _common.make_filename_completer(_common.MQ_ACTIVE_STATES, _common.MQ_TYPE_TBD)
    assert completer("") == ["tbd.md"]


def test_make_filename_completer_matches_prefix_and_sorts(tmp_path: pathlib.Path) -> None:
    """prefix一致で限定し、結果をソートして返す。"""
    private_notes = tmp_path / "private-notes"
    _write_feedback(private_notes, "pre-z.md")
    _write_feedback(private_notes, "pre-a.md")
    _write_feedback(private_notes, "other.md")
    completer = _common.make_filename_completer(_common.MQ_ACTIVE_STATES)
    assert completer("pre-") == ["pre-a.md", "pre-z.md"]


class TestReadiness:
    """明示依存、TBD、修復診断からreadinessを算出する。"""

    def test_ready_feedback_is_actionable(self, tmp_path: pathlib.Path) -> None:
        _write_feedback(tmp_path, "feedback.md")

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("feedback.md",)
        assert result.actionable_count == 1

    def test_legacy_local_path_matches_canonical_readiness_target(self, tmp_path: pathlib.Path) -> None:
        """readinessは旧パス形とURL形を同じ対象リポジトリへ分類する。"""
        local_repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(local_repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(local_repo), "remote", "add", "origin", "git@github.com:example/repo.git"],
            check=True,
        )
        _write_feedback(tmp_path, "legacy.md", target_repo=str(local_repo))
        _write_feedback(tmp_path, "current.md", target_repo="github.com/example/repo")
        _write_feedback(tmp_path, "missing.md", target_repo=str(tmp_path / "missing"))

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("current.md", "legacy.md")

    def test_cooldown_uses_utc_boundary_and_does_not_block_other_ready_entries(self, tmp_path: pathlib.Path) -> None:
        """期限前だけ対象項目を抑制し、同値境界では通常のreadinessへ戻す。"""
        now = datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC)
        _write_feedback(tmp_path, "cooldown.md", cooldown_until="2026-08-12T09:00:00+09:00")
        _write_feedback(tmp_path, "ready.md")

        before = _common.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=now - datetime.timedelta(microseconds=1),
        )
        boundary = _common.calculate_readiness(tmp_path, "github.com/example/repo", now=now)

        assert before.cooldown_pending == ("cooldown.md",)
        assert before.ready == ("ready.md",)
        assert before.actionable_count == 1
        assert boundary.ready == ("cooldown.md", "ready.md")

    @pytest.mark.parametrize("value", ["", "not-a-date", "2026-08-12T00:00:00", 123])
    def test_invalid_cooldown_is_one_actionable_repair(
        self,
        tmp_path: pathlib.Path,
        value: object,
    ) -> None:
        """不正期限をblockedの単一修復対象として数える。"""
        _write_feedback(tmp_path, "feedback.md", cooldown_until=value)

        result = _common.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC),
        )

        assert result.invalid_cooldowns == ("feedback.md",)
        assert result.blocked == ("feedback.md",)
        assert result.actionable_count == 1

    def test_tbd_cooldown_is_invalid_instead_of_suppressing_user_decision(self, tmp_path: pathlib.Path) -> None:
        """外部編集でTBDへ設定された期限はユーザー判断待ちへ適用しない。"""
        _write_tbd(tmp_path, "tbd.md")
        path = tmp_path / "inbox/tbd.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: tbd\n",
                "type: tbd\ncooldown_until: '2999-01-01T00:00:00+00:00'\n",
            ),
            encoding="utf-8",
        )

        result = _common.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC),
        )

        assert result.invalid_cooldowns == ("tbd.md",)
        assert not result.cooldown_pending
        assert result.actionable_count == 1

    def test_pending_cooldown_suppresses_existing_repairs_until_deadline(self, tmp_path: pathlib.Path) -> None:
        """期限前は既存修復診断を抑制し、期限到達後に再び有効化する。"""
        now = datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC)
        _write_feedback(
            tmp_path,
            "missing.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
            plan_file=tmp_path / "missing-plan.md",
            depends_on=("absent.md",),
        )
        invalid = _write_feedback(
            tmp_path,
            "invalid.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
        )
        invalid.write_text(
            invalid.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: malformed\n"),
            encoding="utf-8",
        )
        _write_feedback(
            tmp_path,
            "self.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
            depends_on=("self.md",),
        )
        _write_feedback(
            tmp_path,
            "cycle.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
            depends_on=("cycle-peer.md",),
        )
        _write_feedback(
            tmp_path,
            "cycle-peer.md",
            target_repo="github.com/example/other",
            depends_on=("cycle.md",),
        )

        pending = _common.calculate_readiness(tmp_path, "github.com/example/repo", now=now)
        expired = _common.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=now + datetime.timedelta(days=3),
        )

        assert pending.actionable_count == 0
        assert not pending.missing_plan_file
        assert not pending.invalid_dependencies
        assert not pending.missing_dependencies
        assert not pending.self_dependencies
        assert not pending.cyclic_dependencies
        assert expired.missing_plan_file == ("missing.md",)
        assert expired.invalid_dependencies == ("invalid.md",)
        assert expired.missing_dependencies == ("missing.md",)
        assert expired.self_dependencies == ("self.md",)
        assert expired.cyclic_dependencies == ("cycle.md", "self.md")
        assert expired.actionable_count == 4

    def test_broken_frontmatter_remains_actionable_with_target_repo_filter(self, tmp_path: pathlib.Path) -> None:
        """対象repo指定時も破損項目を修復診断へ残し、正常な他repo項目は除外する。"""
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "broken.md").write_text("---\ntarget_repo: [broken\n", encoding="utf-8")
        _write_feedback(tmp_path, "other.md", target_repo="github.com/example/other")

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.frontmatter_broken == ("broken.md",)
        assert result.frontmatter_broken_needs_tbd == ("broken.md",)
        assert result.actionable_count == 1
        assert "other.md" not in (*result.ready, *result.blocked)

    def test_unanswered_tbd_blocks_explicit_dependency(self, tmp_path: pathlib.Path) -> None:
        _write_tbd(tmp_path, "answer.md")
        _write_feedback(tmp_path, "feedback.md", depends_on=("answer.md",))

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.blocked == ("answer.md", "feedback.md")
        assert result.actionable_count == 0

    def test_answered_tbd_does_not_satisfy_explicit_dependency_while_active(self, tmp_path: pathlib.Path) -> None:
        _write_tbd(tmp_path, "answer.md", answer="回答済み")
        _write_feedback(tmp_path, "feedback.md", depends_on=("answer.md",))

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("answer.md",)
        assert result.blocked == ("feedback.md",)
        assert result.actionable_count == 1

    @pytest.mark.parametrize("relation", ["missing", "self", "cycle"])
    def test_invalid_dependency_relationship_is_actionable_for_repair(
        self,
        tmp_path: pathlib.Path,
        relation: str,
    ) -> None:
        if relation == "missing":
            _write_feedback(tmp_path, "first.md", depends_on=("absent.md",))
        elif relation == "self":
            _write_feedback(tmp_path, "first.md", depends_on=("first.md",))
        else:
            _write_feedback(tmp_path, "first.md", depends_on=("second.md",))
            _write_feedback(tmp_path, "second.md", depends_on=("first.md",))

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.actionable_count >= 1
        assert not result.ready

    def test_legacy_entry_dependency_remains_readable(self, tmp_path: pathlib.Path) -> None:
        _write_feedback(tmp_path, "dependency.md")
        _write_feedback(
            tmp_path,
            "feedback.md",
            legacy_dependency="    kind: entries\n    filenames:\n      - dependency.md",
        )

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("dependency.md",)
        assert result.blocked == ("feedback.md",)

    def test_legacy_external_repo_dependencies_share_readiness_resolver_cache(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同じ旧パス形の外部依存はreadiness計算全体で1回だけGit解決する。"""
        external_repo = tmp_path / "external-repo"
        subprocess.run(["git", "init", str(external_repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(external_repo), "remote", "add", "origin", "git@github.com:example/external.git"],
            check=True,
        )
        legacy_dependency = f"    kind: external-repo-entry\n    filenames:\n      - done.md\n    target_repo: {external_repo}"
        _write_feedback(tmp_path, "first.md", legacy_dependency=legacy_dependency)
        _write_feedback(tmp_path, "second.md", legacy_dependency=legacy_dependency)
        _write_feedback(
            tmp_path,
            "done.md",
            state="adopted",
            target_repo="github.com/example/external",
        )
        original_run = subprocess.run
        git_resolutions = 0

        def run(
            args: list[str],
            *,
            capture_output: bool,
            text: bool,
            check: bool,
            timeout: float | None = None,
        ) -> subprocess.CompletedProcess[Any]:
            nonlocal git_resolutions
            if args == ["git", "-C", str(external_repo), "remote", "get-url", "origin"]:
                git_resolutions += 1
            return original_run(
                args,
                capture_output=capture_output,
                text=text,
                check=check,
                timeout=timeout,
            )

        git_remote = _readiness.__dict__["_git_remote"]
        monkeypatch.setattr(git_remote.subprocess, "run", run)

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("first.md", "second.md")
        assert git_resolutions == 1

    @pytest.mark.parametrize(
        ("legacy_dependency", "other_active"),
        [
            (
                "    kind: external-upstream\n"
                "    condition: upstream待ち\n"
                "    recheck_after: '2999-01-01T00:00:00+00:00'\n"
                "    hold_reason: 未到来",
                False,
            ),
            ("    kind: inbox-empty", True),
        ],
    )
    def test_legacy_condition_remains_blocked_until_satisfied(
        self,
        tmp_path: pathlib.Path,
        legacy_dependency: str,
        other_active: bool,
    ) -> None:
        """legacyの時刻条件とinbox空条件を無条件readyへ変換しない。"""
        _write_feedback(tmp_path, "feedback.md", legacy_dependency=legacy_dependency)
        if other_active:
            _write_feedback(tmp_path, "other.md")

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert "feedback.md" in result.blocked
        assert "feedback.md" not in result.ready

    def test_legacy_external_upstream_becomes_ready_after_recheck_time(self, tmp_path: pathlib.Path) -> None:
        """legacy外部条件は再評価時刻の到来後だけreadyとなる。"""
        _write_feedback(
            tmp_path,
            "feedback.md",
            legacy_dependency=(
                "    kind: external-upstream\n"
                "    condition: upstream待ち\n"
                "    recheck_after: '2000-01-01T00:00:00+00:00'\n"
                "    hold_reason: 再評価"
            ),
        )

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("feedback.md",)

    @pytest.mark.parametrize(("answer", "expected_ready"), [("", False), ("回答済み", True)])
    def test_legacy_external_user_requires_answered_tbd(
        self,
        tmp_path: pathlib.Path,
        answer: str,
        expected_ready: bool,
    ) -> None:
        """legacyのユーザー依存は参照TBDの回答後だけ成立する。"""
        _write_tbd(tmp_path, "answer.md", answer=answer)
        _write_feedback(
            tmp_path,
            "feedback.md",
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    tbd_filename: answer.md"),
        )

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert ("feedback.md" in result.ready) is expected_ready
        assert ("feedback.md" in result.blocked) is not expected_ready

    @pytest.mark.parametrize("state", ["inbox", "adopted"])
    def test_legacy_external_user_rejects_non_tbd_target(self, tmp_path: pathlib.Path, state: str) -> None:
        """legacyのユーザー依存がfeedbackを参照した場合は修復対象として示す。"""
        _write_feedback(tmp_path, "answer.md", state=state)
        _write_feedback(
            tmp_path,
            "feedback.md",
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    tbd_filename: answer.md"),
        )

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.invalid_dependencies == ("feedback.md",)
        assert "feedback.md" not in result.ready

    @pytest.mark.parametrize("terminal_state", ["adopted", "rejected"])
    def test_explicit_dependency_waits_for_answered_tbd_to_reach_terminal_state(
        self,
        tmp_path: pathlib.Path,
        terminal_state: str,
    ) -> None:
        """明示依存は回答済みTBDがactiveな間も成立せず、終端遷移後に成立する。"""
        _write_tbd(tmp_path, "answer.md", answer="回答済み")
        _write_feedback(tmp_path, "feedback.md", depends_on=("answer.md",))

        active = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert active.ready == ("answer.md",)
        assert active.blocked == ("feedback.md",)

        terminal_dir = tmp_path / terminal_state
        terminal_dir.mkdir()
        (tmp_path / "inbox" / "answer.md").rename(terminal_dir / "answer.md")

        terminal = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert terminal.ready == ("feedback.md",)
        assert not terminal.blocked

    def test_explicit_dependency_blocks_again_when_terminal_target_is_retried(self, tmp_path: pathlib.Path) -> None:
        """終端依存先をprocessingへ戻した再試行では依存元を再びblockedにする。"""
        _write_tbd(tmp_path, "answer.md", answer="回答済み")
        _write_feedback(tmp_path, "feedback.md", depends_on=("answer.md",))
        adopted = tmp_path / "adopted"
        adopted.mkdir()
        answer = adopted / "answer.md"
        (tmp_path / "inbox" / "answer.md").rename(answer)
        assert "feedback.md" in _common.calculate_readiness(tmp_path, "github.com/example/repo").ready

        processing = tmp_path / "processing"
        processing.mkdir()
        answer.rename(processing / "answer.md")

        retried = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert retried.ready == ("answer.md",)
        assert retried.blocked == ("feedback.md",)

    def test_explicit_dependency_ignores_legacy_external_user_target(self, tmp_path: pathlib.Path) -> None:
        """トップレベル依存がある場合は併存する旧ユーザー依存を検証対象にしない。"""
        _write_feedback(tmp_path, "done.md", state="adopted")
        _write_feedback(tmp_path, "not-tbd.md")
        _write_feedback(
            tmp_path,
            "feedback.md",
            depends_on=("done.md",),
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    tbd_filename: not-tbd.md"),
        )

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert not result.invalid_dependencies
        assert "feedback.md" in result.ready

    def test_malformed_explicit_dependency_is_invalid_even_with_valid_legacy_value(self, tmp_path: pathlib.Path) -> None:
        """正本の明示依存が不正な場合は旧依存へフォールバックせず修復対象にする。"""
        path = _write_feedback(
            tmp_path,
            "feedback.md",
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    tbd_filename: answer.md"),
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: feedback\n", "type: feedback\ndepends_on: answer.md\n"),
            encoding="utf-8",
        )

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.invalid_dependencies == ("feedback.md",)
        assert not result.ready

    @pytest.mark.parametrize(
        "legacy_dependency",
        [
            "    kind: entries\n    filenames: []",
            "    kind: external-user\n    tbd_filename: answer.md",
            "    kind: external-repo-entry\n    filenames: []\n    target_repo: github.com/example/other",
            "    kind: external-repo-entry\n    filenames: [other.md]\n    target_repo: invalid",
        ],
    )
    def test_invalid_legacy_schema_is_actionable_for_repair(
        self,
        tmp_path: pathlib.Path,
        legacy_dependency: str,
    ) -> None:
        """旧schemaの必須値欠落を依存なしや恒久待機へ変換しない。"""
        _write_feedback(tmp_path, "feedback.md", legacy_dependency=legacy_dependency)

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.invalid_dependencies == ("feedback.md",)
        assert result.actionable_count == 1
        assert not result.ready

    def test_cross_repo_cycle_is_actionable_for_each_target_repo(self, tmp_path: pathlib.Path) -> None:
        """対象repoをまたぐ明示依存の循環も修復対象として検出する。"""
        _write_feedback(tmp_path, "first.md", depends_on=("second.md",), target_repo="github.com/example/first")
        _write_feedback(tmp_path, "second.md", depends_on=("first.md",), target_repo="github.com/example/second")

        first = _common.calculate_readiness(tmp_path, "github.com/example/first")
        second = _common.calculate_readiness(tmp_path, "github.com/example/second")

        assert first.cyclic_dependencies == ("first.md",)
        assert second.cyclic_dependencies == ("second.md",)
        assert first.actionable_count == second.actionable_count == 1

    def test_missing_plan_file_requires_one_repair_tbd(self, tmp_path: pathlib.Path) -> None:
        _write_feedback(tmp_path, "plan.md", plan_file=tmp_path / "missing.md")

        first = _common.calculate_readiness(tmp_path, "github.com/example/repo")
        _write_tbd(tmp_path, "repair.md")
        repair = tmp_path / "inbox" / "repair.md"
        repair.write_text(
            repair.read_text(encoding="utf-8").replace(
                "type: tbd\n",
                "type: tbd\nrepair_target: plan.md\nrepair_kind: missing-plan-file\n",
            ),
            encoding="utf-8",
        )
        second = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert first.missing_plan_file_needs_tbd == ("plan.md",)
        assert not second.missing_plan_file_needs_tbd

    def test_queue_entry_loader_reads_plan_and_repair_kind(self, tmp_path: pathlib.Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("# 計画\n", encoding="utf-8")
        _write_feedback(tmp_path, "plan-item.md", plan_file=plan)
        _write_tbd(tmp_path, "repair.md")
        repair = tmp_path / "inbox" / "repair.md"
        repair.write_text(
            repair.read_text(encoding="utf-8").replace(
                "type: tbd\n",
                "type: tbd\nrepair_target: plan-item.md\nrepair_kind: frontmatter\n",
            ),
            encoding="utf-8",
        )

        entries = _readiness._load_queue_entries(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path, None, ("inbox",)
        )
        by_name = {entry.filename: entry for entry in entries}

        assert by_name["plan-item.md"].plan_file == str(plan)
        assert by_name["repair.md"].repair_kind == "frontmatter"

    def test_readiness_reads_active_once_and_only_referenced_terminal_entries(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """activeは1回だけ読み、未参照の終端項目はfrontmatter解析から除外する。"""
        active_path = _write_feedback(tmp_path, "feedback.md", depends_on=("done.md",))
        referenced = _write_feedback(tmp_path, "done.md", state="adopted")
        unreferenced = tmp_path / "rejected" / "unused.md"
        unreferenced.parent.mkdir(parents=True)
        unreferenced.write_text("frontmatterではない\n", encoding="utf-8")
        original_read_text = pathlib.Path.read_text
        reads: list[pathlib.Path] = []

        def read_text(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
            reads.append(path)
            if path == unreferenced:
                raise AssertionError("未参照の終端項目を読み込んだ")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "read_text", read_text)

        result = _common.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("feedback.md",)
        assert reads.count(active_path) == 1
        assert reads.count(referenced) == 1
        assert unreferenced not in reads


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

    def test_local_path_filter_notifies_legacy_and_current_repo_forms(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """生のローカルパス指定でも旧パス形とURL形の未回答TBDを通知する。"""
        local_repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(local_repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(local_repo), "remote", "add", "origin", "git@github.com:example/repo.git"],
            check=True,
        )
        _write_tbd(tmp_path, "legacy.md", target_repo=str(local_repo), question="旧形式")
        _write_tbd(tmp_path, "current.md", target_repo="github.com/example/repo", question="現行形式")
        _write_tbd(tmp_path, "missing.md", target_repo=str(tmp_path / "missing"), question="対象外")

        _common.notify_unanswered_tbds_if_any(tmp_path, str(local_repo))

        error = capsys.readouterr().err
        assert "legacy.md" in error
        assert "current.md" in error
        assert "missing.md" not in error

    def test_filter_resolves_each_raw_repo_value_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1回の反復では同じ保存値を複数項目が持っても解決を1回に限定する。"""
        _write_tbd(tmp_path, "first.md", target_repo="/legacy/repo")
        _write_tbd(tmp_path, "second.md", target_repo="/legacy/repo")
        calls: list[str] = []

        def resolve(value: str) -> str | None:
            calls.append(value)
            return "github.com/example/repo" if value in {"/legacy/repo", "github.com/example/repo"} else None

        git_remote = _common.__dict__["_git_remote"]
        monkeypatch.setattr(git_remote, "resolve_repo_identifier", resolve)

        iter_entries = _common.__dict__["_iter_entries"]
        entries = list(iter_entries(tmp_path, ("inbox",), "github.com/example/repo", "tbd"))

        assert [entry[0].name for entry in entries] == ["first.md", "second.md"]
        assert calls.count("/legacy/repo") == 1

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

    def test_retries_once_after_explicit_upstream_rebase_on_push_failure(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """push失敗時はfetch後に明示したupstreamへrebaseし、pushを1回だけ再試行する。"""
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
            ["fetch"],
            ["rebase", "@{u}"],
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

    def test_aborts_rebase_and_reports_success_when_explicit_rebase_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """明示したupstreamへのrebaseが失敗した場合は`git rebase --abort`を呼び、
        復元成功をstderrへ出力してから例外を送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            del cwd
            if args[0] == "push" or args == ["rebase", "@{u}"]:
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
            if args[0] == "push" or args == ["rebase", "@{u}"]:
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


class TestExplicitUpstreamIntegration:
    """実Gitで共有`FETCH_HEAD`と利用者設定から独立した同期対象を検証する。"""

    @staticmethod
    def _git(root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """`root`で実Gitを実行し、診断可能な出力を保持して結果を返す。"""
        return subprocess.run(
            ["git", *args],
            cwd=root,
            check=check,
            capture_output=True,
            text=True,
        )

    def _make_remote_and_clones(
        self,
        tmp_path: pathlib.Path,
    ) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        """mainとsideを持つbare remote及び同じmainを追跡する2作業コピーを作成する。"""
        remote = tmp_path / "remote.git"
        seed = tmp_path / "seed"
        old_copy = tmp_path / "old-copy"
        new_copy = tmp_path / "new-copy"
        self._git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
        self._git(tmp_path, "init", "--initial-branch=main", str(seed))
        self._git(seed, "config", "user.email", "test@example.com")
        self._git(seed, "config", "user.name", "test")
        (seed / "queue.md").write_text("initial\n", encoding="utf-8")
        self._git(seed, "add", "queue.md")
        self._git(seed, "commit", "-m", "initial")
        self._git(seed, "remote", "add", "origin", str(remote))
        self._git(seed, "push", "-u", "origin", "main")
        self._git(seed, "switch", "-c", "side")
        (seed / "side.md").write_text("side\n", encoding="utf-8")
        self._git(seed, "add", "side.md")
        self._git(seed, "commit", "-m", "side")
        self._git(seed, "push", "-u", "origin", "side")
        self._git(seed, "switch", "main")
        self._git(tmp_path, "clone", str(remote), str(old_copy))
        self._git(tmp_path, "clone", str(remote), str(new_copy))
        (seed / "queue.md").write_text("updated\n", encoding="utf-8")
        self._git(seed, "add", "queue.md")
        self._git(seed, "commit", "-m", "update")
        self._git(seed, "push")
        return remote, old_copy, new_copy

    def test_explicit_upstream_succeeds_when_fetch_head_has_multiple_candidates(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """複数fetch候補ではpull再現経路が失敗し、明示upstream同期は成功する。"""
        _remote, old_copy, new_copy = self._make_remote_and_clones(tmp_path)

        old_result = self._git(
            old_copy,
            "-c",
            "pull.rebase=true",
            "pull",
            "origin",
            "main",
            "side",
            check=False,
        )
        assert old_result.returncode != 0
        assert "multiple branches" in old_result.stderr

        original_run_git = _common._run_git  # pylint: disable=protected-access  # noqa: SLF001

        def run_with_competing_fetch(args: list[str], cwd: pathlib.Path) -> None:
            original_run_git(args, cwd)
            if args == ["fetch"]:
                self._git(cwd, "fetch", "origin", "main", "side")

        monkeypatch.setattr(_common, "_run_git", run_with_competing_fetch)
        with _common._repo_lock(new_copy):  # pylint: disable=protected-access  # noqa: SLF001
            _common._pull(new_copy)  # pylint: disable=protected-access  # noqa: SLF001

        assert self._git(new_copy, "rev-parse", "HEAD").stdout == self._git(new_copy, "rev-parse", "@{u}").stdout

    def test_sync_fails_when_upstream_is_unset(self, tmp_path: pathlib.Path) -> None:
        """upstream未設定では暗黙の別refへ退避せず同期を失敗させる。"""
        _remote, old_copy, _new_copy = self._make_remote_and_clones(tmp_path)
        self._git(old_copy, "branch", "--unset-upstream")

        with pytest.raises(subprocess.CalledProcessError), _common._repo_lock(old_copy):  # pylint: disable=protected-access  # noqa: SLF001
            _common._pull(old_copy)  # pylint: disable=protected-access  # noqa: SLF001


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
        """マーカー付きディレクトリでは`_pull`がremote同期を実行しない。"""
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


class TestPullIfStale:
    """定期バックグラウンド更新のレート制限を検証する。"""

    def test_skips_recent_pull(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """直近pull後の定期更新を省略することを確認する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_common.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005
        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            assert _common.pull_if_stale(tmp_path) is False
        assert not calls

    def test_pulls_when_due(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """更新期限を過ぎた定期更新がpullすることを確認する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_common.time, "time", lambda: 1100.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005
        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            assert _common.pull_if_stale(tmp_path) is True
        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]

    def test_pulls_when_fetch_head_missing(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`FETCH_HEAD`が無い場合は経過時間を判定できないためpullする。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005
        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            assert _common.pull_if_stale(tmp_path) is True
        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]

    def test_public_pull_ignores_rate_limit(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """利用者の操作に対応する`pull`は直近pullの有無によらず毎回実行する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_common.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005
        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common.pull(tmp_path)
        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]


class TestPullWithRecentWarning:
    """利用者操作のpullが直近同期を警告しつつ必ず実行されることを検証する。"""

    def test_warns_and_pulls_when_fetch_head_is_recent(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """直近30秒の同期形跡を検出してもpullは省略しない。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_common.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005

        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._pull_with_recent_warning(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]
        assert capsys.readouterr().err == (
            "警告: 直近30秒にfetchを含む同期形跡がある。"
            "同一連続操作で同期結果を再利用する場合は`list`・`show`・`grep`で"
            "`--skip-pull`を指定する（状態遷移系のサブコマンドは毎回同期する）。\n"
        )

    def test_pulls_without_warning_when_fetch_head_is_old(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """30秒以上前の同期形跡では警告せずpullする。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_common.time, "time", lambda: 1030.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_common, "_run_git", lambda args, cwd: calls.append(args))  # noqa: ARG005

        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._pull_with_recent_warning(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]
        assert "同期形跡" not in capsys.readouterr().err
