"""メッセージキューの純粋スケジューリング計算を検証する。"""

import datetime
import pathlib
import sys
import typing

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_frontmatter as frontmatter  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_schedule as schedule  # noqa: E402  # pylint: disable=wrong-import-position


def _metadata(
    filename: str,
    *,
    feedback_type: schedule.FeedbackKind = "normal",
    dependency: schedule.Dependency | None = None,
    target_files: tuple[str, ...] = ("README.md",),
    plan_file: str | None = None,
    carry_count: int = 0,
) -> schedule.ScheduleMetadata:
    body = f"\n本文 {filename}\n"
    return schedule.ScheduleMetadata(
        body_sha256=schedule.body_sha256(body),
        normalized_target_repo="github.com/example/repo",
        feedback_type=feedback_type,
        dependency=dependency or schedule.Dependency("none"),
        plan_file=plan_file,
        target_files=target_files if feedback_type == "normal" else (),
        carry_count=carry_count,
        carry_reasons=tuple("dependency-unmet" for _ in range(carry_count)),
    )


def _entry(
    filename: str,
    *,
    metadata: schedule.ScheduleMetadata | None = None,
    kind: schedule.FeedbackEntryKind = "feedback",
    answered: bool | None = None,
    broken: bool = False,
    plan_file: str | None = None,
    repair_target: str | None = None,
    repair_kind: schedule.RepairKind | None = None,
    normalized_target_repo: str | None = "github.com/example/repo",
    state: str | None = "inbox",
) -> schedule.QueueEntry:
    body = f"\n本文 {filename}\n"
    text = frontmatter.serialize_frontmatter(
        {"target_repo": "github.com/example/repo", "type": kind if kind != "unknown" else "feedback"},
        body,
    )
    return schedule.QueueEntry(
        filename=filename,
        text=text,
        kind=kind,
        tbd_answered=answered,
        frontmatter_broken=broken,
        metadata=metadata,
        repair_target_filename=repair_target,
        plan_file=plan_file,
        repair_kind=repair_kind,
        normalized_target_repo=normalized_target_repo,
        state=state,
    )


_FIXED_NOW = datetime.datetime(2026, 8, 4, 0, 0, tzinfo=datetime.UTC)


def _calculate(
    active: tuple[schedule.QueueEntry, ...],
    terminal: tuple[schedule.QueueEntry, ...] = (),
    plans: dict[str, tuple[str, ...]] | None = None,
    repairs: frozenset[schedule.RepairKey] = frozenset(),
    cross_repo: dict[str, schedule.QueueEntry] | None = None,
    now: datetime.datetime = _FIXED_NOW,
) -> schedule.ScheduleResult:
    return schedule.calculate_schedule(active, terminal, plans or {}, repairs, cross_repo or {}, now)


class TestMetadata:
    """分類メタデータのYAML往復契約を検証する。"""

    def test_metadata_round_trip_preserves_body_and_native_mapping(self) -> None:
        body = "\n本文\n"
        text = frontmatter.serialize_frontmatter(
            {"target_repo": "github.com/example/repo", "type": "feedback"},
            body,
        )
        metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(body),
            "github.com/example/repo",
            "normal",
            schedule.Dependency("entries", filenames=("a.md",)),
            None,
            ("README.md",),
            1,
            ("dependency-unmet",),
        )

        serialized = schedule.serialize_schedule_metadata(text, metadata)

        assert schedule.parse_schedule_metadata(serialized) == metadata
        parsed = frontmatter.parse_frontmatter(serialized)
        assert parsed is not None
        assert parsed[1] == body

    def test_plan_metadata_omits_target_files(self) -> None:
        metadata = _metadata("a.md", feedback_type="plan-impl", plan_file="/tmp/plan.md")

        mapping = schedule.metadata_to_mapping(metadata)

        assert "target_files" not in mapping
        assert mapping["plan_file"] == "/tmp/plan.md"


class TestCalculateSchedule:
    """依存・優先度・上限・競合順を検証する。"""

    def test_unclassified_entry_prevents_selection(self) -> None:
        result = _calculate((_entry("a.md"),))
        assert result.classification_required == ("a.md",)
        assert not result.plan_items

    def test_plan_file_regenerates_missing_metadata(self, tmp_path: pathlib.Path) -> None:
        """独立キーがある項目は分類欠落時も計画実装型として選抜する。"""
        plan = tmp_path / "plan.md"
        entry = _entry("plan.md", plan_file=str(plan))

        result = _calculate((entry,), plans={str(plan): ("README.md",)})

        assert not result.classification_required
        assert result.plan_items == ("plan.md",)

    def test_plan_file_regenerates_stale_metadata_without_classification(self, tmp_path: pathlib.Path) -> None:
        """本文変更で分類が失効しても独立キーがあれば再分類を要求しない。"""
        plan = tmp_path / "plan.md"
        stale = _metadata("different.md", feedback_type="plan-impl", plan_file=str(plan))
        entry = _entry("plan.md", metadata=stale, plan_file=str(plan))

        result = _calculate((entry,), plans={str(plan): ("README.md",)})

        assert not result.classification_required
        assert result.plan_items == ("plan.md",)

    def test_missing_plan_file_is_diagnostic_only_and_repair_is_deduplicated(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """独立キーの計画ファイルが消失した項目は選抜せず、修復TBD要求を重複抑止する。"""
        plan = tmp_path / "missing-plan.md"
        entry = _entry("plan.md", plan_file=str(plan))

        result = _calculate((entry,))

        assert result.missing_plan_file_filenames == ("plan.md",)
        assert result.missing_plan_file_needs_tbd_filenames == ("plan.md",)
        assert not result.classification_required
        assert not result.plan_items

        deduplicated = _calculate((entry,), repairs=frozenset({("plan.md", "missing-plan-file")}))

        assert deduplicated.missing_plan_file_filenames == ("plan.md",)
        assert not deduplicated.missing_plan_file_needs_tbd_filenames
        assert not deduplicated.plan_items

    def test_frontmatter_repair_does_not_suppress_missing_plan_repair(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """同じfilenameのfrontmatter修復TBDは計画ファイル修復TBDの要求を抑止しない。"""
        plan = tmp_path / "missing-plan.md"
        entry = _entry("plan.md", plan_file=str(plan))

        result = _calculate((entry,), repairs=frozenset({("plan.md", "frontmatter")}))

        assert result.missing_plan_file_needs_tbd_filenames == ("plan.md",)

    def test_same_filename_can_request_both_repair_kinds(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """同じfilenameで両理由が成立する入力は両方の修復TBDを要求する。"""
        broken = _entry("item.md", broken=True, kind="unknown")
        missing_plan = _entry("item.md", plan_file=str(tmp_path / "missing-plan.md"))

        result = _calculate((broken, missing_plan))

        assert result.frontmatter_broken_needs_tbd_filenames == ("item.md",)
        assert result.missing_plan_file_needs_tbd_filenames == ("item.md",)

    def test_missing_self_and_cycle_dependencies_request_tbd(self) -> None:
        a = _entry("a.md", metadata=_metadata("a.md", dependency=schedule.Dependency("entries", ("b.md",))))
        b = _entry("b.md", metadata=_metadata("b.md", dependency=schedule.Dependency("entries", ("a.md",))))
        self_dependent = _entry(
            "self.md",
            metadata=_metadata("self.md", dependency=schedule.Dependency("entries", ("self.md",))),
        )
        missing = _entry(
            "missing.md",
            metadata=_metadata("missing.md", dependency=schedule.Dependency("entries", ("gone.md",))),
        )

        result = _calculate((a, b, self_dependent, missing))

        reasons = {(item.filename, item.reason) for item in result.missing_dependency_tbds}
        assert ("a.md", "cycle") in reasons
        assert ("b.md", "cycle") in reasons
        assert ("self.md", "self") in reasons
        assert ("missing.md", "missing") in reasons

    def test_carry_count_three_precedes_older_filename(self) -> None:
        older = _entry("001.md", metadata=_metadata("001.md"))
        starved = _entry("999.md", metadata=_metadata("999.md", carry_count=3))
        fillers = tuple(
            _entry(f"{index:03d}.md", metadata=_metadata(f"{index:03d}.md")) for index in range(2, schedule.NORMAL_LIMIT + 1)
        )

        result = _calculate((older, *fillers, starved))

        assert "999.md" in result.parallel_normal_items
        assert any(item.filename == "020.md" and item.reason == "limit-exceeded" for item in result.deferred)

    def test_plan_limit_selects_first_three_and_defers_fourth(self, tmp_path: pathlib.Path) -> None:
        plan_entries = tuple(
            _entry(
                f"{index}.md",
                metadata=_metadata(
                    f"{index}.md",
                    feedback_type="plan-impl",
                    plan_file=str(tmp_path / f"{index}.md"),
                ),
            )
            for index in range(1, 5)
        )

        plan_targets: dict[str, tuple[str, ...]] = {str(tmp_path / f"{index}.md"): (f"{index}.py",) for index in range(1, 5)}

        result = _calculate(plan_entries, plans=plan_targets)

        assert result.plan_items == ("1.md", "2.md", "3.md")
        assert result.deferred == (schedule.DeferredItem("4.md", "limit-exceeded"),)

    def test_multiple_plans_use_target_file_union(self, tmp_path: pathlib.Path) -> None:
        first_plan = tmp_path / "first.md"
        second_plan = tmp_path / "second.md"
        plans = (
            _entry(
                "1-plan.md",
                metadata=_metadata("1-plan.md", feedback_type="plan-impl", plan_file=str(first_plan)),
            ),
            _entry(
                "2-plan.md",
                metadata=_metadata("2-plan.md", feedback_type="plan-impl", plan_file=str(second_plan)),
            ),
        )
        parallel = _entry("parallel.md", metadata=_metadata("parallel.md", target_files=("other.py",)))
        second_conflict = _entry(
            "second-conflict.md",
            metadata=_metadata("second-conflict.md", target_files=("second.py",)),
        )

        result = _calculate(
            (*plans, parallel, second_conflict),
            plans={str(first_plan): ("first.py",), str(second_plan): ("second.py",)},
        )

        assert result.parallel_normal_items == ("parallel.md",)
        assert result.post_plan_normal_items == ("second-conflict.md",)

    def test_missing_or_empty_plan_targets_are_distinguished(self, tmp_path: pathlib.Path) -> None:
        """計画ファイル消失は当該計画だけを保留し、実在する空一覧は通常型との並行を抑止する。"""
        known_plan = tmp_path / "known.md"
        unknown_plan = tmp_path / "unknown.md"
        plans = (
            _entry(
                "1-plan.md",
                metadata=_metadata("1-plan.md", feedback_type="plan-impl", plan_file=str(known_plan)),
            ),
            _entry(
                "2-plan.md",
                metadata=_metadata("2-plan.md", feedback_type="plan-impl", plan_file=str(unknown_plan)),
            ),
        )
        normal = _entry("normal.md", metadata=_metadata("normal.md", target_files=("other.py",)))
        missing_result = _calculate(
            (*plans, normal),
            plans={str(known_plan): ("known.py",)},
        )

        assert missing_result.plan_items == ("1-plan.md",)
        assert missing_result.missing_plan_file_filenames == ("2-plan.md",)
        assert missing_result.parallel_normal_items == ("normal.md",)
        assert not missing_result.post_plan_normal_items

        empty_result = _calculate(
            (*plans, normal),
            plans={str(known_plan): ("known.py",), str(unknown_plan): ()},
        )

        assert empty_result.plan_items == ("1-plan.md", "2-plan.md")
        assert not empty_result.missing_plan_file_filenames
        assert not empty_result.parallel_normal_items
        assert empty_result.post_plan_normal_items == ("normal.md",)

    def test_answered_tbd_resolves_external_dependency(self) -> None:
        tbd = _entry("tbd.md", kind="tbd", answered=True, metadata=_metadata("tbd.md"))
        feedback = _entry(
            "feedback.md",
            metadata=_metadata(
                "feedback.md",
                dependency=schedule.Dependency(
                    "external-user",
                    condition="回答後",
                    tbd_filename="tbd.md",
                ),
            ),
        )

        result = _calculate((feedback, tbd))

        assert "feedback.md" in result.parallel_normal_items

    def test_answered_tbd_refreshes_targets_from_current_dependencies(self, tmp_path: pathlib.Path) -> None:
        """依存元の対象変更後も回答済みTBDを競合する計画と並列実行しない。"""
        plan_path = tmp_path / "plan.md"
        plan = _entry(
            "plan.md",
            metadata=_metadata("plan.md", feedback_type="plan-impl", plan_file=str(plan_path)),
        )
        tbd = _entry(
            "tbd.md",
            kind="tbd",
            answered=True,
            metadata=_metadata("tbd.md", target_files=("stale.py",)),
        )
        first = _entry(
            "first.md",
            metadata=_metadata(
                "first.md",
                dependency=schedule.Dependency("external-user", condition="回答後", tbd_filename="tbd.md"),
                target_files=("shared.py",),
            ),
        )
        second = _entry(
            "second.md",
            metadata=_metadata(
                "second.md",
                dependency=schedule.Dependency("external-user", condition="回答後", tbd_filename="tbd.md"),
                target_files=("other.py", "shared.py"),
            ),
        )

        result = _calculate(
            (plan, tbd, first, second),
            plans={str(plan_path): ("shared.py",)},
        )

        assert "tbd.md" in result.post_plan_normal_items

    def test_answered_tbd_without_current_source_avoids_plan_parallelism(self, tmp_path: pathlib.Path) -> None:
        """参照元が無い回答済みTBDは永続化済み対象を信用せず計画後に処理する。"""
        plan_path = tmp_path / "plan.md"
        plan = _entry(
            "plan.md",
            metadata=_metadata("plan.md", feedback_type="plan-impl", plan_file=str(plan_path)),
        )
        tbd = _entry(
            "tbd.md",
            kind="tbd",
            answered=True,
            metadata=_metadata("tbd.md", target_files=("unrelated.py",)),
        )

        result = _calculate((plan, tbd), plans={str(plan_path): ("shared.py",)})

        assert "tbd.md" in result.post_plan_normal_items

    def test_answered_repair_tbd_uses_current_repair_target(self, tmp_path: pathlib.Path) -> None:
        """修復TBDは修復対象の現行対象ファイルで計画との競合を判定する。"""
        plan_path = tmp_path / "plan.md"
        plan = _entry(
            "plan.md",
            metadata=_metadata("plan.md", feedback_type="plan-impl", plan_file=str(plan_path)),
        )
        repair_target = _entry(
            "feedback.md",
            metadata=_metadata("feedback.md", target_files=("shared.py",)),
        )
        tbd = _entry(
            "tbd.md",
            kind="tbd",
            answered=True,
            metadata=_metadata("tbd.md", target_files=("stale.py",)),
            repair_target="feedback.md",
            repair_kind="frontmatter",
        )

        result = _calculate(
            (plan, repair_target, tbd),
            plans={str(plan_path): ("shared.py",)},
        )

        assert "tbd.md" in result.post_plan_normal_items

    def test_plan_conflict_and_empty_targets_are_post_plan(self, tmp_path: pathlib.Path) -> None:
        plan_path = tmp_path / "plan.md"
        plan = _entry(
            "plan.md",
            metadata=_metadata("plan.md", feedback_type="plan-impl", plan_file=str(plan_path)),
        )
        parallel = _entry("parallel.md", metadata=_metadata("parallel.md", target_files=("other.py",)))
        conflict = _entry("conflict.md", metadata=_metadata("conflict.md", target_files=("shared.py",)))
        unknown = _entry("unknown.md", metadata=_metadata("unknown.md", target_files=()))

        result = _calculate((plan, parallel, conflict, unknown), plans={str(plan_path): ("shared.py",)})

        assert result.plan_items == ("plan.md",)
        assert result.parallel_normal_items == ("parallel.md",)
        assert result.post_plan_normal_items == ("conflict.md", "unknown.md")

    def test_broken_frontmatter_is_diagnostic_only_and_repair_is_deduplicated(self) -> None:
        broken = _entry("broken.md", broken=True, kind="unknown")

        result = _calculate((broken,), repairs=frozenset({("broken.md", "frontmatter")}))

        assert result.frontmatter_broken_filenames == ("broken.md",)
        assert not result.frontmatter_broken_needs_tbd_filenames
        assert not result.classification_required


def test_parse_plan_target_files_rejects_external_paths() -> None:
    """対象一覧から相対POSIXパスだけを抽出する。"""
    text = """### 対象ファイル一覧

- [ ] `README.md`（現行1行）
- [ ] `../outside.md`（現行1行）
- [ ] `/tmp/absolute.md`（現行1行）

### 次
"""
    assert schedule.parse_plan_target_files(text) == ("README.md",)


def _classification(
    filename: str,
    *,
    source_body_sha256: str,
    feedback_type: schedule.FeedbackKind = "normal",
    dependency: schedule.Dependency | None = None,
    plan_file: str | None = None,
    target_files: tuple[str, ...] = ("README.md",),
) -> schedule.Classification:
    return schedule.Classification(
        filename,
        source_body_sha256,
        feedback_type,
        dependency or schedule.Dependency("none"),
        plan_file,
        target_files if feedback_type == "normal" else (),
    )


class TestApplyClassifications:
    """`apply_classifications`が受理する補正の範囲を検証する。"""

    def test_rejects_plan_impl_classification(self, tmp_path: pathlib.Path) -> None:
        """分類結果からの計画実装型指定を拒否する。"""
        entry = _entry("a.md")
        classification = _classification(
            "a.md",
            source_body_sha256=schedule.body_sha256(entry.text),
            feedback_type="plan-impl",
            plan_file=str(tmp_path / "plan.md"),
        )

        with pytest.raises(ValueError, match="計画実装型"):
            schedule.apply_classifications((entry,), (), (classification,))

    def test_corrects_missing_dependency_target_to_external_user(self) -> None:
        """依存先消失と診断される`entries`依存項目だけ、外部・ユーザー依存への補正を受理する。"""
        entry = _entry(
            "a.md",
            metadata=_metadata("a.md", dependency=schedule.Dependency("entries", filenames=("gone.md",))),
        )
        classification = _classification(
            "a.md",
            source_body_sha256=schedule.body_sha256("\n本文 a.md\n"),
            dependency=schedule.Dependency("external-user", condition="回答後に着手する", tbd_filename="tbd.md"),
        )

        updated = schedule.apply_classifications((entry,), (), (classification,))

        assert updated[0].metadata is not None
        assert updated[0].metadata.dependency.kind == "external-user"

    def test_rejects_correction_when_dependency_is_not_currently_broken(self) -> None:
        """依存先がactiveで単に未成立なだけの`entries`依存は、補正扱いされず既存分類を保持する。"""
        target = _entry("gone.md", metadata=_metadata("gone.md"))
        entry = _entry(
            "a.md",
            metadata=_metadata("a.md", dependency=schedule.Dependency("entries", filenames=("gone.md",))),
        )
        classification = _classification(
            "a.md",
            source_body_sha256=schedule.body_sha256("\n本文 a.md\n"),
            dependency=schedule.Dependency("external-user", condition="回答後に着手する", tbd_filename="tbd.md"),
        )

        updated = schedule.apply_classifications((entry, target), (), (classification,))

        by_filename = {item.filename: item for item in updated}
        assert by_filename["a.md"].metadata is not None
        assert by_filename["a.md"].metadata.dependency.kind == "entries"

    def test_rejects_correction_that_also_changes_target_files(self) -> None:
        """依存先消失の補正であっても、依存以外のフィールド（target_files等）の変更は拒否する。"""
        entry = _entry(
            "a.md",
            metadata=_metadata("a.md", dependency=schedule.Dependency("entries", filenames=("gone.md",))),
        )
        classification = _classification(
            "a.md",
            source_body_sha256=schedule.body_sha256("\n本文 a.md\n"),
            dependency=schedule.Dependency("external-user", condition="回答後に着手する", tbd_filename="tbd.md"),
            target_files=("other.py",),
        )

        updated = schedule.apply_classifications((entry,), (), (classification,))

        assert updated[0].metadata is not None
        assert updated[0].metadata.dependency.kind == "entries"
        assert updated[0].metadata.target_files == ("README.md",)

    def test_rejects_correction_to_non_external_user_dependency(self) -> None:
        """診断済みの依存先消失であっても、補正先が`external-user`以外の場合は拒否する。"""
        entry = _entry(
            "a.md",
            metadata=_metadata("a.md", dependency=schedule.Dependency("entries", filenames=("gone.md",))),
        )
        classification = _classification(
            "a.md",
            source_body_sha256=schedule.body_sha256("\n本文 a.md\n"),
            dependency=schedule.Dependency("none"),
        )

        updated = schedule.apply_classifications((entry,), (), (classification,))

        assert updated[0].metadata is not None
        assert updated[0].metadata.dependency.kind == "entries"

    def test_diagnosis_matches_calculate_schedule_when_target_is_frontmatter_broken(self) -> None:
        """依存先がfrontmatter破損項目の場合も`calculate_schedule`と同じ診断（消失扱い）を行い、

        本関数が独自に緩い診断をして`calculate_schedule`側の判定と不一致にならないことを確認する。
        """
        broken_target = _entry("gone.md", broken=True, kind="unknown")
        entry = _entry(
            "a.md",
            metadata=_metadata("a.md", dependency=schedule.Dependency("entries", filenames=("gone.md",))),
        )
        classification = _classification(
            "a.md",
            source_body_sha256=schedule.body_sha256("\n本文 a.md\n"),
            dependency=schedule.Dependency("external-user", condition="回答後に着手する", tbd_filename="tbd.md"),
        )

        # calculate_scheduleが実際に"missing"と診断することを前提として確認する
        result = _calculate((entry, broken_target))
        assert any(item.filename == "a.md" and item.reason == "missing" for item in result.missing_dependency_tbds)

        # apply_classificationsも同じ診断のもとで補正を受理する
        updated = schedule.apply_classifications((entry, broken_target), (), (classification,))
        by_filename = {item.filename: item for item in updated}
        assert by_filename["a.md"].metadata is not None
        assert by_filename["a.md"].metadata.dependency.kind == "external-user"


class TestExternalUpstreamDependency:
    """外部条件待ちの依存種別: 再評価時刻まで選抜から外し、繰越も加算しない。"""

    @staticmethod
    def _dependency(recheck_after: str) -> schedule.Dependency:
        return schedule.Dependency(
            "external-upstream",
            condition="上流の対応状況を確認する",
            recheck_after=recheck_after,
            hold_reason="上流ツールのメジャー版対応待ち",
        )

    def test_before_recheck_is_suppressed_without_carry(self) -> None:
        """再評価時刻が未到来の項目は選抜されず、繰越の対象にもならない。"""
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=self._dependency("2026-09-01T00:00:00+00:00")))

        result = _calculate((entry,))

        assert not result.parallel_normal_items
        assert not result.deferred
        assert [item.filename for item in result.suppressed] == ["a.md"]
        assert result.suppressed[0].recheck_after == "2026-09-01T00:00:00+00:00"
        assert result.suppressed[0].hold_reason == "上流ツールのメジャー版対応待ち"
        assert result.suppressed[0].condition == "上流の対応状況を確認する"

    def test_after_recheck_becomes_eligible(self) -> None:
        """再評価時刻が到来した項目は通常の選抜対象へ戻る。"""
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=self._dependency("2026-08-03T00:00:00+00:00")))

        result = _calculate((entry,))

        assert result.parallel_normal_items == ("a.md",)
        assert not result.suppressed

    def test_boundary_time_is_satisfied(self) -> None:
        """再評価時刻と現在時刻が等しい場合は成立とする。"""
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=self._dependency("2026-08-04T00:00:00+00:00")))

        assert _calculate((entry,)).parallel_normal_items == ("a.md",)

    @pytest.mark.parametrize(
        "recheck_after",
        [
            "2026-08-04T00:00:00",  # タイムゾーン情報なし
            "2026-08-04",  # 日付のみでタイムゾーン情報なし
            "not-a-datetime",
            "",
        ],
    )
    def test_invalid_recheck_after_is_rejected(self, recheck_after: str) -> None:
        """解析できない値とnaiveな日時は分類メタデータごと拒否する。"""
        mapping = {
            "kind": "external-upstream",
            "condition": "確認する",
            "recheck_after": recheck_after,
            "hold_reason": "待機中",
        }

        assert schedule.dependency_from_mapping(mapping) is None

    def test_round_trip_keeps_string_type(self) -> None:
        """直列化と復元で再評価時刻の型が変わらない。"""
        dependency = self._dependency("2026-09-01T00:00:00+00:00")
        metadata = _metadata("a.md", dependency=dependency)
        body = "\n本文 a.md\n"
        text = frontmatter.serialize_frontmatter({"target_repo": "github.com/example/repo", "type": "feedback"}, body)

        restored = schedule.parse_schedule_metadata(schedule.serialize_schedule_metadata(text, metadata))

        assert restored is not None
        assert restored.dependency == dependency


class TestExternalRepoEntryDependency:
    """別リポジトリ依存: 依存先が終端状態にあることを機械判定する。"""

    @staticmethod
    def _dependency() -> schedule.Dependency:
        return schedule.Dependency(
            "external-repo-entry",
            filenames=("upstream.md",),
            target_repo="github.com/example/other",
        )

    def test_satisfied_when_dependency_reaches_terminal_state(self) -> None:
        """依存先が別リポジトリの終端状態にあれば成立する。"""
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=self._dependency()))
        upstream = _entry("upstream.md", normalized_target_repo="github.com/example/other", state="adopted")

        result = _calculate((entry,), cross_repo={"upstream.md": upstream})

        assert result.parallel_normal_items == ("a.md",)

    def test_unsatisfied_while_dependency_is_active(self) -> None:
        """依存先が未処理の状態では成立しない。"""
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=self._dependency()))
        upstream = _entry("upstream.md", normalized_target_repo="github.com/example/other", state="processing")

        result = _calculate((entry,), cross_repo={"upstream.md": upstream})

        assert not result.parallel_normal_items
        assert [item.filename for item in result.deferred] == ["a.md"]

    def test_unsatisfied_when_repository_differs(self) -> None:
        """同名エントリが別のリポジトリに存在しても成立しない。"""
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=self._dependency()))
        same_name = _entry("upstream.md", normalized_target_repo="github.com/example/unrelated", state="adopted")

        result = _calculate((entry,), cross_repo={"upstream.md": same_name})

        assert not result.parallel_normal_items

    def test_absent_dependency_is_not_diagnosed_as_missing(self) -> None:
        """依存先が対象リポジトリの一覧に無くても消失と診断しない。"""
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=self._dependency()))

        result = _calculate((entry,))

        assert not result.missing_dependency_tbds
        assert [item.filename for item in result.deferred] == ["a.md"]


class TestDeferralIdempotency:
    """繰越の冪等化: 同一実行単位の再計算で二重計上しない。"""

    def test_same_run_id_increments_once(self) -> None:
        """同じ実行単位では2回目以降の加算を行わない。"""
        metadata = _metadata("a.md")

        first = schedule.with_deferral(metadata, "limit-exceeded", "run-1")
        second = schedule.with_deferral(first, "limit-exceeded", "run-1")

        assert first.carry_count == 1
        assert second.carry_count == 1
        assert second.carry_reasons == ("limit-exceeded",)

    def test_different_run_id_increments(self) -> None:
        """異なる実行単位からの繰越は従来どおり加算する。"""
        metadata = _metadata("a.md")

        first = schedule.with_deferral(metadata, "limit-exceeded", "run-1")
        second = schedule.with_deferral(first, "limit-exceeded", "run-2")

        assert second.carry_count == 2
        assert second.carry_reasons == ("limit-exceeded", "limit-exceeded")

    def test_without_run_id_increments_each_time(self) -> None:
        """実行単位を指定しない場合は毎回加算する。"""
        metadata = _metadata("a.md")

        first = schedule.with_deferral(metadata, "conflict")
        second = schedule.with_deferral(first, "conflict")

        assert second.carry_count == 2

    def test_run_id_survives_round_trip(self) -> None:
        """実行単位の識別値は直列化と復元をまたいで保持される。"""
        metadata = schedule.with_deferral(_metadata("a.md"), "limit-exceeded", "run-1")
        body = "\n本文 a.md\n"
        text = frontmatter.serialize_frontmatter({"target_repo": "github.com/example/repo", "type": "feedback"}, body)

        restored = schedule.parse_schedule_metadata(schedule.serialize_schedule_metadata(text, metadata))

        assert restored is not None
        assert restored.last_deferral_run_id == "run-1"

    def test_legacy_metadata_without_run_id_is_accepted(self) -> None:
        """当該キーを持たない既存キューのエントリを再分類の対象にしない。"""
        metadata = _metadata("a.md")
        mapping = schedule.metadata_to_mapping(metadata)

        assert "last_deferral_run_id" not in mapping
        assert schedule.mapping_to_metadata(mapping) is not None


class TestPlanMetadataRegeneration:
    """計画実装型の再生成: 独立キーから復元できない情報を保持する。"""

    def test_regeneration_preserves_dependency(self) -> None:
        """再生成で依存が初期化されない。"""
        dependency = schedule.Dependency(
            "external-upstream",
            condition="上流の対応状況を確認する",
            recheck_after="2026-09-01T00:00:00+00:00",
            hold_reason="上流ツールのメジャー版対応待ち",
        )
        entry = _entry(
            "a.md",
            metadata=_metadata("a.md", feedback_type="plan-impl", plan_file="/tmp/plan.md", dependency=dependency),
            plan_file="/tmp/plan.md",
        )

        regenerated = schedule.regenerate_plan_metadata((entry,))

        assert regenerated[0].metadata is not None
        assert regenerated[0].metadata.dependency == dependency

    def test_regeneration_preserves_run_id(self) -> None:
        """再生成で実行単位の識別値が失われない。"""
        metadata = schedule.with_deferral(
            _metadata("a.md", feedback_type="plan-impl", plan_file="/tmp/plan.md"),
            "limit-exceeded",
            "run-1",
        )
        entry = _entry("a.md", metadata=metadata, plan_file="/tmp/plan.md")

        regenerated = schedule.regenerate_plan_metadata((entry,))

        assert regenerated[0].metadata is not None
        assert regenerated[0].metadata.last_deferral_run_id == "run-1"


class TestUnknownDependencyKind:
    """未知の依存種別は不成立として扱う。"""

    def test_unknown_kind_is_unsatisfied(self) -> None:
        """列挙外の種別が混入した場合、他種別の判定が適用されず不成立とする。

        型注釈では表せない値を、frontmatterの直接編集などで持つ場合の防御を検査する。
        """
        unknown_kind = typing.cast(schedule.DependencyKind, "unknown-kind")
        entry = _entry("a.md", metadata=_metadata("a.md", dependency=schedule.Dependency(unknown_kind)))

        result = _calculate((entry,))

        assert not result.parallel_normal_items
        assert [item.filename for item in result.deferred] == ["a.md"]
