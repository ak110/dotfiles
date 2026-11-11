"""WIのmutations content: body_fileのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.mutations import content as mutation_content
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git, _write_convert_awi
from agent_toolkit.atk_test import _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file


class TestEditBodyFile:
    """本文ファイルによる編集の検証。"""

    @pytest.mark.parametrize("newline", ["\n", "\r\n"])
    def test_body_file_preserves_multiline_content(
        self,
        newline: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """改行をLFへ正規化し、本文の記号と構造を保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="編集前")
        body = '---\n# 見出し\n\n"引用" \\ path\n```sh\necho ok\n```\n'
        body_path = tmp_path / "body.md"
        body_path.write_bytes(body.replace("\n", newline).encode())
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "entry.md", "--body-file", str(body_path)], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[1] == '\n---\n# 見出し\n\n"引用" \\ path\n```sh\necho ok\n```\n'

    def test_body_file_appends_and_rejects_positional_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本文ファイルを追記でき、位置引数の本文は書込前に拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="編集前")
        body_path = tmp_path / "body.md"
        body_path.write_text("追記本文", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "entry.md", "--append", "--body-file", str(body_path)], home=tmp_path)
        assert exc_info.value.code == 0
        assert capsys.readouterr().out == "成功: 追記を反映した: entry.md\n"
        appended = path.read_text(encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "entry.md", "MESSAGE", "--body-file", str(body_path)], home=tmp_path)
        assert exc_info.value.code == 2
        assert path.read_text(encoding="utf-8") == appended

    @pytest.mark.parametrize(
        ("append", "expected_body"),
        [
            (False, "\n{message_file}\n"),
            (True, "\n編集前\n\n\n{message_file}"),
        ],
    )
    def test_body_file_accepts_existing_file_path_as_content(
        self,
        append: bool,
        expected_body: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """既存ファイルパスだけの本文を通常編集と追記でそのまま保存する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="編集前")
        message_file = tmp_path / "message.txt"
        message_file.write_text("本文ファイルが参照する既存ファイル", encoding="utf-8")
        body_path = tmp_path / "body.md"
        body_path.write_text(str(message_file), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        command = ["wi", "edit", "entry.md", "--body-file", str(body_path)]
        if append:
            command.append("--append")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(command, home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[1] == expected_body.format(message_file=message_file)

    def test_hold_body_file_accepts_existing_file_path_as_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """本文ファイル内の既存ファイルパスを本文として保存する。"""
        notes = _setup_notes(tmp_path)
        filename = "20260907-000000-001.md"
        _write_convert_awi(notes, filename, state="hold")
        target_worktree = tmp_path / "target-worktree"
        target_worktree.mkdir()
        message_file = tmp_path / "message.txt"
        message_file.write_text("本文ファイルが参照する既存ファイル", encoding="utf-8")
        body_path = tmp_path / "body.md"
        body_path.write_text(str(message_file), encoding="utf-8")
        _disable_transition_git(monkeypatch)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(
            wi_add,  # pylint: disable=protected-access
            "resolve_add_target",
            lambda _value: ("github.com/example/foo", target_worktree),
        )

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "edit",
                    filename,
                    "--body-file",
                    str(body_path),
                    "--target-repo",
                    "github.com/example/foo",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(_fixed_path(notes, filename).read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[1] == f"\n{message_file}\n"

    def test_unreadable_body_file_does_not_modify_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """解釈できない本文ファイルは終了コード1で拒否し、項目を保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="編集前")
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "entry.md", "--body-file", str(tmp_path / "missing")], home=tmp_path)
        assert exc_info.value.code == 1
        assert path.read_bytes() == original

    def test_body_file_does_not_start_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """本文ファイル指定時はEDITORが設定済みでもエディターを起動しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "must-not-run")
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[0] == "must-not-run":
                pytest.fail("本文ファイル指定時にEDITORが起動された")
            return _make_subprocess_fake(git_calls)(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0

    def test_expected_content_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """競合時は上書きせず、FILENAMEと未反映を案内する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        original_edit = mutation_content.edit_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: str,
            target_repo: str | None = None,
            storage: _wi_sync.Storage = _wi_sync.DEFAULT_STORAGE,
            expected_content: str | None = None,
            finalized_content: dict[str, str] | None = None,
        ) -> bool:
            path.write_text(path.read_text(encoding="utf-8").replace("編集前", "競合側の変更"), encoding="utf-8")
            return original_edit(
                private_notes,
                state=state,
                filename=filename,
                content=content,
                target_repo=target_repo,
                storage=storage,
                expected_content=expected_content,
                finalized_content=finalized_content,
            )

        monkeypatch.setattr(mutation_content, "edit_entry_content", conflict)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映していない" in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n競合側の変更\n")

    def test_logically_identical_awi_reports_no_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """論理本文が同一ならコミットせず差分なしを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "本文"), home=tmp_path)

        assert exc_info.value.code == 0
        # 接頭辞の無い行は成否を読み取れないため、差分なしも成功行として報告すること。
        assert capsys.readouterr().out.startswith("成功: 差分なし")
        assert not [call for call in git_calls if "commit" in call["cmd"]]
