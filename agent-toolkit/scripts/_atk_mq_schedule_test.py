"""メッセージキューの純粋スケジューリング計算を検証する。"""

import pathlib
import sys

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
    )


def _calculate(
    active: tuple[schedule.QueueEntry, ...],
    terminal: tuple[schedule.QueueEntry, ...] = (),
    plans: dict[str, tuple[str, ...]] | None = None,
    repairs: frozenset[schedule.RepairKey] = frozenset(),
) -> schedule.ScheduleResult:
    return schedule.calculate_schedule(active, terminal, plans or {}, repairs)


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
