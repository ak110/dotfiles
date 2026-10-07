"""WIの着手可否の判定のテスト。"""

import datetime
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_entry_files import write_awi, write_uwi


class TestReadiness:
    """明示依存、UWI、修復診断から着手可否を算出する。"""

    def test_ready_awi_is_actionable(self, tmp_path: pathlib.Path) -> None:
        write_awi(tmp_path, "awi.md")

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("awi.md",)
        assert result.actionable_count == 1

    def test_legacy_local_path_matches_canonical_readiness_target(self, tmp_path: pathlib.Path) -> None:
        """着手可否判定は旧パス形とURL形を同じ対象リポジトリへ分類する。"""
        local_repo = tmp_path / "repo"
        git_repository.init_repository(local_repo, origin="git@github.com:example/repo.git")
        write_awi(tmp_path, "legacy.md", target_repo=str(local_repo))
        write_awi(tmp_path, "current.md", target_repo="github.com/example/repo")
        write_awi(tmp_path, "missing.md", target_repo=str(tmp_path / "missing"))

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("current.md", "legacy.md")

    def test_cooldown_uses_utc_boundary_and_does_not_block_other_ready_entries(self, tmp_path: pathlib.Path) -> None:
        """期限前だけ対象項目を抑制し、同値境界では通常の着手可否へ戻す。"""
        now = datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC)
        write_awi(tmp_path, "cooldown.md", cooldown_until="2026-08-12T09:00:00+09:00")
        write_awi(tmp_path, "ready.md")

        before = _wi_readiness.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=now - datetime.timedelta(microseconds=1),
        )
        boundary = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo", now=now)

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
        write_awi(tmp_path, "awi.md", cooldown_until=value)

        result = _wi_readiness.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC),
        )

        assert result.invalid_cooldowns == ("awi.md",)
        assert result.blocked == ("awi.md",)
        assert result.actionable_count == 1

    def test_uwi_cooldown_is_invalid_instead_of_suppressing_user_decision(self, tmp_path: pathlib.Path) -> None:
        """外部編集でUWIへ設定された期限はユーザー判断待ちへ適用しない。"""
        write_uwi(tmp_path, "uwi.md")
        path = tmp_path / "inbox/uwi.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: uwi\n",
                "type: uwi\ncooldown_until: '2999-01-01T00:00:00+00:00'\n",
            ),
            encoding="utf-8",
        )

        result = _wi_readiness.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC),
        )

        assert result.invalid_cooldowns == ("uwi.md",)
        assert not result.cooldown_pending
        assert result.actionable_count == 1

    def test_pending_cooldown_suppresses_existing_repairs_until_deadline(self, tmp_path: pathlib.Path) -> None:
        """期限前は既存修復診断を抑制し、期限到達後に再び有効化する。"""
        now = datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC)
        write_awi(
            tmp_path,
            "missing.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
            depends_on=("absent.md",),
        )
        invalid = write_awi(
            tmp_path,
            "invalid.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
        )
        invalid.write_text(
            invalid.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: malformed\n"),
            encoding="utf-8",
        )
        write_awi(
            tmp_path,
            "self.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
            depends_on=("self.md",),
        )
        write_awi(
            tmp_path,
            "cycle.md",
            cooldown_until="2026-08-15T00:00:00+00:00",
            depends_on=("cycle-peer.md",),
        )
        write_awi(
            tmp_path,
            "cycle-peer.md",
            target_repo="github.com/example/other",
            depends_on=("cycle.md",),
        )

        pending = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo", now=now)
        expired = _wi_readiness.calculate_readiness(
            tmp_path,
            "github.com/example/repo",
            now=now + datetime.timedelta(days=3),
        )

        assert pending.actionable_count == 0
        assert not pending.invalid_dependencies
        assert not pending.missing_dependencies
        assert not pending.self_dependencies
        assert not pending.cyclic_dependencies
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
        write_awi(tmp_path, "other.md", target_repo="github.com/example/other")

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.frontmatter_broken == ("broken.md",)
        assert result.frontmatter_broken_needs_uwi == ("broken.md",)
        assert result.actionable_count == 1
        assert "other.md" not in (*result.ready, *result.blocked)

    def test_unanswered_uwi_blocks_explicit_dependency(self, tmp_path: pathlib.Path) -> None:
        write_uwi(tmp_path, "answer.md")
        write_awi(tmp_path, "awi.md", depends_on=("answer.md",))

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.blocked == ("answer.md", "awi.md")
        assert result.actionable_count == 0

    def test_run_skill_answered_uwi_is_not_ready(self, tmp_path: pathlib.Path) -> None:
        """`atk run-skill`のUWIは次回の同じスキルの実行が扱うため、process-loopの起動件数へ数えない。

        数えると、process-wiの選定が除くUWIのためにprocess-loopが子セッションの起動を繰り返す。
        `source`を持たないUWIとprocess-wiのUWIは従来どおり回答済みでready件数へ数える。
        """
        write_uwi(tmp_path, "run-skill.md", answer="回答済み", source="run-skill")

        only_run_skill = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert not only_run_skill.ready
        assert _wi_readiness.count_pending_entries(tmp_path, target_repo="github.com/example/repo") == 0  # pylint: disable=protected-access  # noqa: SLF001

        write_uwi(tmp_path, "no-source.md", answer="回答済み")
        write_uwi(tmp_path, "process-wi.md", answer="回答済み", source="process-wi")

        mixed = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert mixed.ready == ("no-source.md", "process-wi.md")
        assert _wi_readiness.count_pending_entries(tmp_path, target_repo="github.com/example/repo") == 2  # pylint: disable=protected-access  # noqa: SLF001

    def test_answered_uwi_does_not_satisfy_explicit_dependency_while_active(self, tmp_path: pathlib.Path) -> None:
        write_uwi(tmp_path, "answer.md", answer="回答済み")
        write_awi(tmp_path, "awi.md", depends_on=("answer.md",))

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("answer.md",)
        assert result.blocked == ("awi.md",)
        assert result.actionable_count == 1

    @pytest.mark.parametrize("relation", ["missing", "self", "cycle"])
    def test_invalid_dependency_relationship_is_actionable_for_repair(
        self,
        tmp_path: pathlib.Path,
        relation: str,
    ) -> None:
        if relation == "missing":
            write_awi(tmp_path, "first.md", depends_on=("absent.md",))
        elif relation == "self":
            write_awi(tmp_path, "first.md", depends_on=("first.md",))
        else:
            write_awi(tmp_path, "first.md", depends_on=("second.md",))
            write_awi(tmp_path, "second.md", depends_on=("first.md",))

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.actionable_count >= 1
        assert not result.ready

    def test_legacy_entry_dependency_remains_readable(self, tmp_path: pathlib.Path) -> None:
        write_awi(tmp_path, "dependency.md")
        write_awi(
            tmp_path,
            "awi.md",
            legacy_dependency="    kind: entries\n    filenames:\n      - dependency.md",
        )

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("dependency.md",)
        assert result.blocked == ("awi.md",)

    def test_legacy_external_repo_dependencies_share_readiness_resolver_cache(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同じ旧パス形の外部依存は着手可否計算全体で1回だけGit解決する。"""
        external_repo = tmp_path / "external-repo"
        git_repository.init_repository(external_repo, origin="git@github.com:example/external.git")
        legacy_dependency = f"    kind: external-repo-entry\n    filenames:\n      - done.md\n    target_repo: {external_repo}"
        write_awi(tmp_path, "first.md", legacy_dependency=legacy_dependency)
        write_awi(tmp_path, "second.md", legacy_dependency=legacy_dependency)
        write_awi(
            tmp_path,
            "done.md",
            state="adopted",
            target_repo="github.com/example/external",
        )
        original_run = subprocess.run
        git_resolutions = 0

        def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            nonlocal git_resolutions
            if args == ["git", "-C", str(external_repo), "remote", "get-url", "origin"]:
                git_resolutions += 1
            return original_run(args, **kwargs)  # pylint: disable=subprocess-run-check

        monkeypatch.setattr(subprocess, "run", run)

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

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
        write_awi(tmp_path, "awi.md", legacy_dependency=legacy_dependency)
        if other_active:
            write_awi(tmp_path, "other.md")

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert "awi.md" in result.blocked
        assert "awi.md" not in result.ready

    def test_legacy_external_upstream_becomes_ready_after_recheck_time(self, tmp_path: pathlib.Path) -> None:
        """legacy外部条件は再評価時刻の到来後だけreadyとなる。"""
        write_awi(
            tmp_path,
            "awi.md",
            legacy_dependency=(
                "    kind: external-upstream\n"
                "    condition: upstream待ち\n"
                "    recheck_after: '2000-01-01T00:00:00+00:00'\n"
                "    hold_reason: 再評価"
            ),
        )

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("awi.md",)

    @pytest.mark.parametrize(("answer", "expected_ready"), [("", False), ("回答済み", True)])
    def test_legacy_external_user_requires_answered_uwi(
        self,
        tmp_path: pathlib.Path,
        answer: str,
        expected_ready: bool,
    ) -> None:
        """legacyのユーザー依存は参照UWIの回答後だけ成立する。"""
        write_uwi(tmp_path, "answer.md", answer=answer)
        write_awi(
            tmp_path,
            "awi.md",
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    uwi_filename: answer.md"),
        )

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert ("awi.md" in result.ready) is expected_ready
        assert ("awi.md" in result.blocked) is not expected_ready

    @pytest.mark.parametrize("state", ["inbox", "adopted"])
    def test_legacy_external_user_rejects_non_uwi_target(self, tmp_path: pathlib.Path, state: str) -> None:
        """旧形式のユーザー依存がAWIを参照した場合は修復対象として示す。"""
        write_awi(tmp_path, "answer.md", state=state)
        write_awi(
            tmp_path,
            "awi.md",
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    uwi_filename: answer.md"),
        )

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.invalid_dependencies == ("awi.md",)
        assert "awi.md" not in result.ready

    @pytest.mark.parametrize("terminal_state", ["adopted", "rejected"])
    def test_explicit_dependency_waits_for_answered_uwi_to_reach_terminal_state(
        self,
        tmp_path: pathlib.Path,
        terminal_state: str,
    ) -> None:
        """明示依存は回答済みUWIがactiveな間も成立せず、終端遷移後に成立する。"""
        write_uwi(tmp_path, "answer.md", answer="回答済み")
        write_awi(tmp_path, "awi.md", depends_on=("answer.md",))

        active = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert active.ready == ("answer.md",)
        assert active.blocked == ("awi.md",)

        terminal_dir = tmp_path / terminal_state
        terminal_dir.mkdir()
        (tmp_path / "inbox" / "answer.md").rename(terminal_dir / "answer.md")

        terminal = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert terminal.ready == ("awi.md",)
        assert not terminal.blocked

    def test_explicit_dependency_blocks_again_when_terminal_target_is_retried(self, tmp_path: pathlib.Path) -> None:
        """終端依存先をprocessingへ戻した再試行では依存元を再びblockedにする。"""
        write_uwi(tmp_path, "answer.md", answer="回答済み")
        write_awi(tmp_path, "awi.md", depends_on=("answer.md",))
        adopted = tmp_path / "adopted"
        adopted.mkdir()
        answer = adopted / "answer.md"
        (tmp_path / "inbox" / "answer.md").rename(answer)
        assert "awi.md" in _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo").ready

        processing = tmp_path / "processing"
        processing.mkdir()
        answer.rename(processing / "answer.md")

        retried = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert retried.ready == ("answer.md",)
        assert retried.blocked == ("awi.md",)

    def test_explicit_dependency_ignores_legacy_external_user_target(self, tmp_path: pathlib.Path) -> None:
        """トップレベル依存がある場合は併存する旧ユーザー依存を検証対象にしない。"""
        write_awi(tmp_path, "done.md", state="adopted")
        write_awi(tmp_path, "not-uwi.md")
        write_awi(
            tmp_path,
            "awi.md",
            depends_on=("done.md",),
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    uwi_filename: not-uwi.md"),
        )

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert not result.invalid_dependencies
        assert "awi.md" in result.ready

    def test_malformed_explicit_dependency_is_invalid_even_with_valid_legacy_value(self, tmp_path: pathlib.Path) -> None:
        """トップレベルに保存した明示依存が不正な場合は旧依存へフォールバックせず修復対象にする。"""
        path = write_awi(
            tmp_path,
            "awi.md",
            legacy_dependency=("    kind: external-user\n    condition: 回答後\n    uwi_filename: answer.md"),
        )
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: answer.md\n"),
            encoding="utf-8",
        )

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.invalid_dependencies == ("awi.md",)
        assert not result.ready

    @pytest.mark.parametrize(
        "legacy_dependency",
        [
            "    kind: entries\n    filenames: []",
            "    kind: external-user\n    uwi_filename: answer.md",
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
        write_awi(tmp_path, "awi.md", legacy_dependency=legacy_dependency)

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.invalid_dependencies == ("awi.md",)
        assert result.actionable_count == 1
        assert not result.ready

    def test_cross_repo_cycle_is_actionable_for_each_target_repo(self, tmp_path: pathlib.Path) -> None:
        """対象repoをまたぐ明示依存の循環も修復対象として検出する。"""
        write_awi(tmp_path, "first.md", depends_on=("second.md",), target_repo="github.com/example/first")
        write_awi(tmp_path, "second.md", depends_on=("first.md",), target_repo="github.com/example/second")

        first = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/first")
        second = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/second")

        assert first.cyclic_dependencies == ("first.md",)
        assert second.cyclic_dependencies == ("second.md",)
        assert first.actionable_count == second.actionable_count == 1

    def test_readiness_reads_active_once_and_only_referenced_terminal_entries(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """activeは1回だけ読み、未参照の終端項目はfrontmatter解析から除外する。"""
        active_path = write_awi(tmp_path, "awi.md", depends_on=("done.md",))
        referenced = write_awi(tmp_path, "done.md", state="adopted")
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

        result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")

        assert result.ready == ("awi.md",)
        assert reads.count(active_path) == 1
        assert reads.count(referenced) == 1
        assert unreferenced not in reads


class TestUpstreamCrossRepoDependency:
    """着手できるかは、別の対象リポジトリにある依存先の状態でも決まる。"""

    def test_cross_repo_dependency_blocks_until_terminal(self, tmp_path: pathlib.Path) -> None:
        """別target_repoの依存先がactiveの間はblockedで、終端でreadyへ戻る。"""
        write_awi(tmp_path, "downstream.md", depends_on=("upstream.md",), target_repo="github.com/example/downstream")
        write_awi(tmp_path, "upstream.md", target_repo="github.com/example/upstream")
        readiness = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/downstream")
        assert not readiness.ready
        assert readiness.blocked == ("downstream.md",)
        assert not readiness.missing_dependencies

        (tmp_path / "adopted").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "upstream.md").rename(tmp_path / "adopted" / "upstream.md")
        readiness = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/downstream")
        assert readiness.ready == ("downstream.md",)
        assert not readiness.blocked

    def test_terminal_entry_in_other_repo_does_not_release_wait(self, tmp_path: pathlib.Path) -> None:
        """同じtarget_repoの依存先が未終端の間は、別target_repoの同名項目が終端していても待機する。"""
        write_awi(tmp_path, "downstream.md", depends_on=("upstream.md",), target_repo="github.com/example/downstream")
        write_awi(tmp_path, "upstream.md", target_repo="github.com/example/downstream")
        write_awi(tmp_path, "upstream.md", state="adopted", target_repo="github.com/example/other")

        readiness = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/downstream")

        assert readiness.ready == ("upstream.md",)
        assert readiness.blocked == ("downstream.md",)
        assert not readiness.missing_dependencies

    def test_active_entry_in_other_repo_does_not_block_resolved_dependency(self, tmp_path: pathlib.Path) -> None:
        """同じtarget_repoの依存先が終端していれば、別target_repoの同名項目が未終端でも着手できる。"""
        write_awi(tmp_path, "downstream.md", depends_on=("upstream.md",), target_repo="github.com/example/downstream")
        write_awi(tmp_path, "upstream.md", state="adopted", target_repo="github.com/example/downstream")
        write_awi(tmp_path, "upstream.md", target_repo="github.com/example/other")

        readiness = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/downstream")

        assert readiness.ready == ("downstream.md",)
        assert not readiness.blocked
