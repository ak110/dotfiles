"""`atk mq schedule`の公開CLI統合契約を検証する。"""

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_frontmatter as frontmatter  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_schedule as schedule  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # noqa: E402  # pylint: disable=wrong-import-position
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
)


class TestScheduleCli:
    """分類保存・繰越・引数検証を公開CLI経由で確認する。"""

    def test_missing_target_repo_is_rejected_by_argparse(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _setup_notes(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "--target-repo" in capsys.readouterr().err

    def test_unclassified_item_is_reported_without_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb.md", target_repo="github.com/example/repo")
        calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert payload["classification_required"] == ["fb.md"]
        assert not any("commit" in call["cmd"] for call in calls)

    def test_classification_file_is_persisted_and_selected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb.md", target_repo="github.com/example/repo")
        text = path.read_text(encoding="utf-8")
        classification_path = tmp_path / "classifications.json"
        classification_path.write_text(
            json.dumps(
                {
                    "classifications": [
                        {
                            "filename": "fb.md",
                            "source_body_sha256": schedule.body_sha256(text),
                            "type": "normal",
                            "dependency": {"kind": "none"},
                            "target_files": ["README.md"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "schedule",
                    "--target-repo=github.com/example/repo",
                    f"--classifications={classification_path}",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out.splitlines()[0])
        assert payload["parallel_normal_items"] == ["fb.md"]
        assert captured.err == ""
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert isinstance(parsed[0].get("queue_schedule"), dict)
        assert any("commit" in call["cmd"] for call in calls)

    def test_plan_file_regeneration_is_persisted_and_used_for_conflicts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """独立キーから再生成した分類を保存し、通常型との競合判定へ反映する。"""
        notes = _setup_notes(tmp_path)
        plan = tmp_path / "plan.md"
        plan.write_text("### 対象ファイル一覧\n\n- [ ] `shared.py`\n", encoding="utf-8")
        plan_entry = _write_feedback_file(notes, "plan.md", target_repo="github.com/example/repo")
        plan_entry.write_text(
            plan_entry.read_text(encoding="utf-8").replace(
                "type: feedback\n",
                f"type: feedback\nplan_file: {plan}\n",
            ),
            encoding="utf-8",
        )
        normal_entry = _write_feedback_file(notes, "normal.md", target_repo="github.com/example/repo")
        normal_text = normal_entry.read_text(encoding="utf-8")
        normal_metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(normal_text),
            "github.com/example/repo",
            "normal",
            schedule.Dependency("none"),
            None,
            ("shared.py",),
            0,
            (),
        )
        normal_entry.write_text(
            schedule.serialize_schedule_metadata(normal_text, normal_metadata),
            encoding="utf-8",
        )
        calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert payload["plan_items"] == ["plan.md"]
        assert payload["post_plan_normal_items"] == ["normal.md"]
        regenerated = schedule.parse_schedule_metadata(plan_entry.read_text(encoding="utf-8"))
        assert regenerated is not None
        assert regenerated.feedback_type == "plan-impl"
        assert regenerated.plan_file == str(plan)
        assert any("commit" in call["cmd"] for call in calls)

    def test_legacy_metadata_plan_file_is_selected_without_repair_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """分類メタデータだけに計画パスを持つ旧形式を実行対象として扱う。"""
        notes = _setup_notes(tmp_path)
        plan = tmp_path / "plan.md"
        plan.write_text("### 対象ファイル一覧\n\n- [ ] `shared.py`\n", encoding="utf-8")
        plan_entry = _write_feedback_file(notes, "plan.md", target_repo="github.com/example/repo")
        text = plan_entry.read_text(encoding="utf-8")
        metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(text),
            "github.com/example/repo",
            "plan-impl",
            schedule.Dependency("none"),
            str(plan),
            (),
            0,
            (),
        )
        plan_entry.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")
        calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert payload["plan_items"] == ["plan.md"]
        assert not payload["missing_plan_file_filenames"]
        assert not payload["missing_plan_file_needs_tbd_filenames"]
        assert list((notes / "inbox").glob("*.md")) == [plan_entry]
        assert not any("commit" in call["cmd"] for call in calls)

    def test_missing_plan_file_is_held_with_deduplicated_repair_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """消失した計画ファイルを実行対象から除外し、修復TBDを1回だけ自動投入する。"""
        notes = _setup_notes(tmp_path)
        missing_plan = tmp_path / "missing-plan.md"
        plan_entry = _write_feedback_file(notes, "plan.md", target_repo="github.com/example/repo")
        plan_entry.write_text(
            plan_entry.read_text(encoding="utf-8").replace(
                "type: feedback\n",
                f"type: feedback\nplan_file: {missing_plan}\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        first_payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert first_payload["missing_plan_file_filenames"] == ["plan.md"]
        assert first_payload["missing_plan_file_needs_tbd_filenames"] == ["plan.md"]
        assert not first_payload["plan_items"]
        repair_paths = [path for path in (notes / "inbox").glob("*.md") if path.name != "plan.md"]
        assert len(repair_paths) == 1
        repair_text = repair_paths[0].read_text(encoding="utf-8")
        parsed = frontmatter.parse_frontmatter(repair_text)
        assert parsed is not None
        assert parsed[0]["repair_target"] == "plan.md"
        assert parsed[0]["repair_kind"] == "missing-plan-file"
        assert "計画ファイルを復元するか" in repair_text

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        second_payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert second_payload["missing_plan_file_filenames"] == ["plan.md"]
        assert not second_payload["missing_plan_file_needs_tbd_filenames"]
        assert len([path for path in (notes / "inbox").glob("*.md") if path.name != "plan.md"]) == 1

    def test_frontmatter_repair_tbd_records_kind_and_is_deduplicated(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter修復TBDへ理由区分を保存し、同一理由の再実行では重複投入しない。"""
        notes = _setup_notes(tmp_path)
        (notes / "inbox" / "broken.md").write_text(
            "---\ntarget_repo: [broken\n---\n本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        first_payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert first_payload["frontmatter_broken_needs_tbd_filenames"] == ["broken.md"]
        repair_paths = [path for path in (notes / "inbox").glob("*.md") if path.name != "broken.md"]
        assert len(repair_paths) == 1
        parsed = frontmatter.parse_frontmatter(repair_paths[0].read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["repair_target"] == "broken.md"
        assert parsed[0]["repair_kind"] == "frontmatter"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        second_payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert not second_payload["frontmatter_broken_needs_tbd_filenames"]
        assert len([path for path in (notes / "inbox").glob("*.md") if path.name != "broken.md"]) == 1

    def test_legacy_frontmatter_repair_does_not_suppress_later_missing_plan_repair(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """理由区分のない既存TBDはfrontmatter修復として扱い、後発の別理由TBDを投入する。"""
        notes = _setup_notes(tmp_path)
        target = notes / "inbox" / "item.md"
        target.write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")
        legacy_repair = notes / "inbox" / "legacy-repair.md"
        legacy_repair.write_text(
            "---\n"
            "target_repo: github.com/example/repo\n"
            "type: tbd\n"
            "question_type: free-form\n"
            "repair_target: item.md\n"
            "---\n\n"
            "## 質問\n\nfrontmatterを修復する\n\n"
            "## 回答\n\n"
            "<!-- 回答を記入してください -->\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        initial_payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert not initial_payload["frontmatter_broken_needs_tbd_filenames"]
        assert len(list((notes / "inbox").glob("*.md"))) == 2

        missing_plan = tmp_path / "missing-plan.md"
        target.write_text(
            f"---\ntarget_repo: github.com/example/repo\ntype: feedback\nplan_file: {missing_plan}\n---\n\n本文\n",
            encoding="utf-8",
        )

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        repaired_payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert repaired_payload["missing_plan_file_needs_tbd_filenames"] == ["item.md"]
        repair_paths = [path for path in (notes / "inbox").glob("*.md") if path.name not in {"item.md", "legacy-repair.md"}]
        assert len(repair_paths) == 1
        parsed = frontmatter.parse_frontmatter(repair_paths[0].read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["repair_target"] == "item.md"
        assert parsed[0]["repair_kind"] == "missing-plan-file"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "schedule", "--target-repo=github.com/example/repo"], home=tmp_path)

        assert exc_info.value.code == 0
        rerun_payload = json.loads(capsys.readouterr().out.splitlines()[0])
        assert not rerun_payload["missing_plan_file_needs_tbd_filenames"]
        assert len([path for path in (notes / "inbox").glob("*.md") if path.name not in {"item.md", "legacy-repair.md"}]) == 1

    def test_record_deferral_appends_reason_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb.md", target_repo="github.com/example/repo")
        text = path.read_text(encoding="utf-8")
        metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(text),
            "github.com/example/repo",
            "normal",
            schedule.Dependency("none"),
            None,
            ("README.md",),
            0,
            (),
        )
        path.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "schedule",
                    "--target-repo=github.com/example/repo",
                    "--record-deferral",
                    "conflict:fb.md",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        updated = schedule.parse_schedule_metadata(path.read_text(encoding="utf-8"))
        assert updated is not None
        assert updated.carry_count == 1
        assert updated.carry_reasons == ("conflict",)

    def test_body_sha256_mismatch_is_reported_for_unclassified_item(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb.md", target_repo="github.com/example/repo")
        text = path.read_text(encoding="utf-8")
        expected = schedule.body_sha256(text)
        received = "0" * 64
        classification_path = tmp_path / "classifications.json"
        classification_path.write_text(
            json.dumps(
                {
                    "classifications": [
                        {
                            "filename": "fb.md",
                            "source_body_sha256": received,
                            "type": "normal",
                            "dependency": {"kind": "none"},
                            "target_files": ["README.md"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "schedule",
                    "--target-repo=github.com/example/repo",
                    f"--classifications={classification_path}",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out.splitlines()[0])
        assert payload["classification_required"] == ["fb.md"]
        assert "source_body_sha256が実ファイルと一致しない分類が1件あります" in captured.err
        assert f"fb.md: 期待={expected[:16]} 受理={received[:16]}" in captured.err

    def test_body_sha256_mismatch_is_reported_for_classified_item(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb.md", target_repo="github.com/example/repo")
        text = path.read_text(encoding="utf-8")
        expected = schedule.body_sha256(text)
        metadata = schedule.ScheduleMetadata(
            expected,
            "github.com/example/repo",
            "normal",
            schedule.Dependency("none"),
            None,
            ("README.md",),
            0,
            (),
        )
        path.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")
        received = "0" * 64
        classification_path = tmp_path / "classifications.json"
        classification_path.write_text(
            json.dumps(
                {
                    "classifications": [
                        {
                            "filename": "fb.md",
                            "source_body_sha256": received,
                            "type": "normal",
                            "dependency": {"kind": "none"},
                            "target_files": ["README.md"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "schedule",
                    "--target-repo=github.com/example/repo",
                    f"--classifications={classification_path}",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out.splitlines()[0])
        assert "fb.md" not in payload["classification_required"]
        assert payload["parallel_normal_items"] == ["fb.md"]
        assert "source_body_sha256が実ファイルと一致しない分類が1件あります" in captured.err
