import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._testing.wi_mutations_content_support import _AGENT_ENVIRONMENT_VARIABLES, _edit_body_args
from agent_toolkit._testing.wi_mutations_support import _write_uwi_entry
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file


class TestEditNoEditor:
    def test_no_editor_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        monkeypatch.delenv("EDITOR", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "EDITOR" in captured.err


class TestNoninteractiveEdit:
    @pytest.mark.parametrize("entry_type", ["awi", "uwi"])
    @pytest.mark.parametrize(
        ("stored_source", "replacement", "agent_environment", "accepted"),
        [
            ("human", "human", True, True),
            (None, "human", True, False),
            ("agent", "human", True, False),
            ("human", "agent", True, True),
            ("agent", "human", False, True),
        ],
    )
    def test_public_edit_human_source_contract(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        entry_type: str,
        stored_source: str | None,
        replacement: str,
        agent_environment: bool,
        accepted: bool,
    ) -> None:
        notes = _setup_notes(tmp_path)
        if entry_type == "awi":
            path = _write_awi_file(notes, "entry.md", body="編集前", source=stored_source)
        else:
            source_line = f"\nsource: {stored_source}" if stored_source is not None else ""
            path = _write_uwi_entry(
                notes,
                "entry.md",
                frontmatter="target_repo: github.com/example/foo\ntype: uwi\nquestion_type: yes-no" + source_line,
            )
        original = path.read_text(encoding="utf-8")
        for name in _AGENT_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        if agent_environment:
            monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\nsource: {replacement}\n---\n\n編集後"
        with pytest.raises(SystemExit) as edited:
            atk.main(_edit_body_args(tmp_path, path.name, message), home=tmp_path)
        assert edited.value.code == (0 if accepted else 1)
        saved = path.read_text(encoding="utf-8")
        if accepted:
            parsed = frontmatter_parser.parse_frontmatter(saved)
            assert parsed is not None
            assert parsed[0]["source"] == replacement
            assert "編集後" in parsed[1]
        else:
            assert saved == original
            error = capsys.readouterr().err
            assert "human" in error
            assert "起動中の作業かスキルの名前を指定する（プラグイン接頭辞を除く。" in error
            assert "agent" in error

    def test_awi_body_updates_without_editor_and_preserves_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前", source="session-review")
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == (
            "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: awi\nsource: session-review\n---\n\n編集後\n"
        )

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_agent_environment_preserves_missing_source_on_edit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        dry_run: bool,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="人間が入力した要求。")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "人間が入力した要求。\n\n## 処理結果\n\n確認した結果。"
        args = _edit_body_args(tmp_path, "fb-001.md", message)

        with pytest.raises(SystemExit) as exc_info:
            atk.main([*args, *(["--dry-run"] if dry_run else [])], home=tmp_path)

        assert exc_info.value.code == 0
        expected = original if dry_run else original + "\n## 処理結果\n\n確認した結果。\n"
        assert path.read_text(encoding="utf-8") == expected

    @pytest.mark.parametrize("source", [None, "test"])
    @pytest.mark.parametrize("replacement", ["", "''", "[]"])
    def test_agent_environment_rejects_invalid_explicit_source_on_edit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        source: str | None,
        replacement: str,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前", source=source)
        original = path.read_text(encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\nsource: {replacement}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 1
        assert path.read_text(encoding="utf-8") == original
        error = capsys.readouterr().err
        assert "frontmatter" in error
        assert "--source" not in error

    def test_agent_environment_rejects_source_removal_by_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前", source="test")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setenv("EDITOR", "fake-editor")
        fallback = _make_subprocess_fake([])

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[0] == "fake-editor":
                pathlib.Path(cmd[-1]).write_text(original.replace("source: test\n", ""), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0)
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 1
        assert path.read_text(encoding="utf-8") == original

    def test_agent_environment_accepts_source_added_or_changed_by_edit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        missing = _write_awi_file(notes, "missing.md", body="編集前")
        existing = _write_awi_file(notes, "existing.md", body="編集前", source="old")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        for filename, source in (("missing.md", "new"), ("existing.md", "changed")):
            message = f"---\nsource: {source}\n---\n\n編集後"
            with pytest.raises(SystemExit) as exc_info:
                atk.main(_edit_body_args(tmp_path, filename, message), home=tmp_path)
            assert exc_info.value.code == 0

        assert "source: new" in missing.read_text(encoding="utf-8")
        assert "source: changed" in existing.read_text(encoding="utf-8")
