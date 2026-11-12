"""WIのmutations content: metadataのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi
from agent_toolkit._atk.wi.mutations import content as mutation_content
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args
from agent_toolkit._testing.wi_mutations_support import _write_uwi_entry
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file


class TestEditBodyFile:
    """本文ファイルによる編集の検証。"""

    @pytest.mark.parametrize("question_type", ["choice", "post-approval"])
    def test_uwi_question_and_scope_update_preserves_answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        question_type: str,
    ) -> None:
        """UWIの質問とscopeだけを更新し、回答領域を保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_uwi_entry(
            notes,
            "uwi-001.md",
            frontmatter=("target_repo: github.com/example/foo\ntype: uwi\nscope: old\nquestion_type: choice\nchoices: A,B"),
        )
        original_answer = path.read_text(encoding="utf-8").split(uwi.ANSWER_HEADING, maxsplit=1)[1]
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\nscope: new\nquestion_type: {question_type}\n---\n\n変更後の質問"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "uwi-001.md", message), home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert "scope: new" in content
        assert f"question_type: {question_type}" in content
        if question_type == "post-approval":
            assert "choices:" not in content
        assert "変更後の質問" in content
        assert content.split(uwi.ANSWER_HEADING, maxsplit=1)[1] == original_answer

    @pytest.mark.parametrize(
        ("message", "expected_code"),
        [
            ("---\nsubmitter_session: other-session\n---\n\n変更後の質問", 1),
            ("---\nsubmitter_session: self-session\n---\n\n変更後の質問", 0),
        ],
    )
    def test_uwi_edit_keeps_stored_submitter_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
        expected_code: int,
    ) -> None:
        """本文のfrontmatterから投入元セッションを書き換えられない。

        回答済みUWIの通知は保存した投入元セッションだけへ届くため、編集で値が変わると通知の宛先が変わる。
        """
        notes = _setup_notes(tmp_path)
        path = _write_uwi_entry(
            notes,
            "uwi-001.md",
            frontmatter=(
                "target_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\nsubmitter_session: self-session"
            ),
        )
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "uwi-001.md", message), home=tmp_path)

        assert exc_info.value.code == expected_code
        if expected_code:
            assert "予約キー" in capsys.readouterr().err
            assert path.read_text(encoding="utf-8") == original
        else:
            saved = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
            assert saved is not None and saved[0]["submitter_session"] == "self-session"
            assert "変更後の質問" in saved[1]

    @pytest.mark.parametrize(
        "message",
        [
            f"変更後\n\n{uwi.ANSWER_HEADING}\n記入済みの回答\n",
            f"変更後\n\n{uwi.ANSWER_MARKER}\n",
            "---\nquestion_type: invalid\n---\n\n変更後",
            "---\nquestion_type: choice\nchoices:\n---\n\n変更後",
        ],
    )
    def test_uwi_rejects_invalid_question_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
    ) -> None:
        """予約要素と不正な質問メタデータを拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_uwi_entry(notes, "uwi-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "uwi-001.md", message), home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8") == original

    def test_uwi_uses_last_answer_marker_and_preserves_answer_region(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """回答マーカー重複時も終了側を基準に質問だけを更新する。"""
        notes = _setup_notes(tmp_path)
        path = _write_uwi_entry(
            notes,
            "uwi-001.md",
            question=f"前半\n\n{uwi.ANSWER_MARKER}\n\n後半",
            answer="保持する回答",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "uwi-001.md", "変更後の質問"), home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert content.count(uwi.ANSWER_MARKER) == 1
        assert content.endswith(f"{uwi.ANSWER_MARKER}\n保持する回答\n")

    @pytest.mark.parametrize("operation", ["add", "change", "delete"])
    @pytest.mark.parametrize(
        ("key", "before", "after"),
        [
            ("target_commit", "a" * 40, "b" * 40),
            ("depends_on", ["before.md"], ["after.md"]),
            ("cooldown_until", "2026-09-01T00:00:00+00:00", "2026-09-02T00:00:00+00:00"),
            ("repair_target", "before.md", "after.md"),
            ("repair_kind", "before", "after"),
            ("plan_file", "/tmp/before.md", "/tmp/after.md"),
        ],
    )
    def test_edit_content_allows_reserved_frontmatter_mutations(
        self,
        operation: str,
        key: str,
        before: object,
        after: object,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """一般全文編集は予約frontmatterキーの追加・変更・削除を保存する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        original_data, body = parsed
        if operation != "add":
            original_data[key] = before
        original = frontmatter_parser.serialize_frontmatter(original_data, body)
        path.write_text(original, encoding="utf-8")
        updated_data = dict(original_data)
        if operation == "delete":
            updated_data.pop(key)
        else:
            updated_data[key] = after
        updated = frontmatter_parser.serialize_frontmatter(updated_data, body)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        assert mutation_content.edit_entry_content(
            notes,
            state="inbox",
            filename="fb-001.md",
            content=updated,
            storage=_wi_sync.Storage(lock_timeout=2.0),
        )

        saved = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert saved is not None
        assert saved[0] == updated_data

    def test_edit_content_boundary_invalidates_target_commit_on_target_repo_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Web API共通保存境界はtarget_repo変更時に旧target_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: awi\n",
            f"type: awi\ntarget_commit: {'a' * 40}\n",
        )
        path.write_text(original, encoding="utf-8")
        updated = original.replace("target_repo: github.com/example/foo", "target_repo: github.com/example/new")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        assert mutation_content.edit_entry_content(
            notes,
            state="inbox",
            filename="fb-001.md",
            content=updated,
            storage=_wi_sync.Storage(lock_timeout=2.0),
        )

        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_edit_preserves_plan_file_when_only_body_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """plan_fileを持つ項目も本文だけの編集を許容する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: awi\n",
            "type: awi\nplan_file: /tmp/plan.md\n",
        )
        path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["plan_file"] == "/tmp/plan.md"

    def test_edit_raises_web_input_error_when_frontmatter_is_corrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter全体が破損している場合は編集を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _fixed_path(notes, "fb-001.md")
        path.write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 1
        assert "frontmatterのstateが不正または欠落" in capsys.readouterr().err


@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
@pytest.mark.parametrize("operation", ["same", "omit", "change", "new"])
@pytest.mark.parametrize(
    "key,value",
    [
        ("target_commit", "a" * 40),
        ("depends_on", ["dependency.md"]),
        ("cooldown_until", "2026-10-20T00:00:00+09:00"),
        ("repair_target", "repair.md"),
        ("repair_kind", "input"),
        ("plan_file", "/plans/example.md"),
        ("submitter_session", "session-1"),
    ],
)
def test_edit_reserved_metadata_roundtrip(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry_type: str,
    operation: str,
    key: str,
    value: object,
) -> None:
    """非対話CLIは保存値の再提示・省略を受理し、予約値の追加・変更を保存前に拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = "fb-001.md"
    path = _write_awi_file(notes, filename) if entry_type == "awi" else _write_uwi_entry(notes, filename)
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    if operation != "new":
        data[key] = value
    original = frontmatter_parser.serialize_frontmatter(data, body)
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    updates = {} if operation == "omit" else {key: "changed" if operation == "change" else value}
    message = frontmatter_parser.serialize_frontmatter(updates, "編集後の本文")
    args = _edit_body_args(tmp_path, filename, message)
    for dry_run in (True, False):
        with pytest.raises(SystemExit) as result:
            atk.main([*args, *(["--dry-run"] if dry_run else [])], home=tmp_path)
        output = capsys.readouterr()
        accepted = operation in ("same", "omit")
        assert result.value.code == (0 if accepted else 1), output.err
        saved = path.read_text(encoding="utf-8")
        if not accepted or dry_run:
            assert saved == original
        else:
            updated = frontmatter_parser.parse_frontmatter(saved)
            assert updated is not None and updated[0][key] == value
            assert "編集後の本文" in saved
            if entry_type == "uwi":
                assert "既存回答" in saved
        if not accepted:
            assert key in output.err and "次の操作:" in output.err


@pytest.mark.parametrize("change_repo", [False, True])
def test_show_body_edit_keeps_or_invalidates_target_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change_repo: bool,
) -> None:
    """showの保存本文をdry-runと実保存へ戻し、対象変更時には再提示された旧commitを無効化する。"""
    notes = _setup_notes(tmp_path)
    filename = "fb-001.md"
    path = _write_awi_file(notes, filename, body="変更前")
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    data["target_commit"] = "a" * 40
    path.write_text(frontmatter_parser.serialize_frontmatter(data, body), encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as shown:
        atk.main(["wi", "show", filename, "--skip-pull"], home=tmp_path)
    assert shown.value.code == 0
    message = capsys.readouterr().out.split("\n", 2)[2].replace("変更前", "変更後")
    if change_repo:
        message = message.replace("github.com/example/foo", "github.com/example/bar")
    args = _edit_body_args(tmp_path, filename, message)
    for dry_run in (True, False):
        with pytest.raises(SystemExit) as result:
            atk.main([*args, *(["--dry-run"] if dry_run else [])], home=tmp_path)
        assert result.value.code == 0, capsys.readouterr().err
    saved = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert saved is not None and "変更後" in saved[1]
    assert saved[0].get("target_commit") == (None if change_repo else "a" * 40)
