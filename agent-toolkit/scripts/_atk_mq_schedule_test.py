"""メッセージキューの純粋スケジューリング計算を検証する。"""

import pathlib
import sys

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
) -> schedule.QueueEntry:
    body = f"\n本文 {filename}\n"
    text = frontmatter.serialize_frontmatter(
        {"target_repo": "github.com/example/repo", "type": kind if kind != "unknown" else "feedback"},
        body,
    )
    return schedule.QueueEntry(filename, text, kind, answered, broken, metadata, None)


def _calculate(
    active: tuple[schedule.QueueEntry, ...],
    terminal: tuple[schedule.QueueEntry, ...] = (),
    plans: dict[str, tuple[str, ...]] | None = None,
    repairs: frozenset[str] = frozenset(),
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

        result = _calculate((broken,), repairs=frozenset({"broken.md"}))

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


def test_detect_plan_impl_reference_requires_existing_absolute_file(tmp_path: pathlib.Path) -> None:
    """実在する絶対パスだけを計画実装型として検出する。"""
    plan = tmp_path / "example.md"
    plan.write_text("# plan\n", encoding="utf-8")
    assert schedule.detect_plan_impl_reference(f"対象計画: `{plan}`") == str(plan)
    assert schedule.detect_plan_impl_reference(f"対象計画: `{tmp_path / 'missing.md'}`") is None


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

        updated = schedule.apply_classifications((entry,), (), (classification,), {})

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

        updated = schedule.apply_classifications((entry, target), (), (classification,), {})

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

        updated = schedule.apply_classifications((entry,), (), (classification,), {})

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

        updated = schedule.apply_classifications((entry,), (), (classification,), {})

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
        updated = schedule.apply_classifications((entry, broken_target), (), (classification,), {})
        by_filename = {item.filename: item for item in updated}
        assert by_filename["a.md"].metadata is not None
        assert by_filename["a.md"].metadata.dependency.kind == "external-user"
