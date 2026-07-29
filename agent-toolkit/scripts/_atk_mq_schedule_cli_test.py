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
