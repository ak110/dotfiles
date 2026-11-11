"""WIのmutations content: baseのテスト。"""

import contextlib
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import batch as wi_batch
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._atk.wi.mutations import content as mutation_content
from agent_toolkit._atk.wi.mutations import dependencies as mutation_dependencies
from agent_toolkit._atk.wi.mutations import result_commits, targets
from agent_toolkit._atk.wi.mutations import transitions as mutation_transitions
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_mutations_content_support import _USER_COMMENT_ERROR, _edit_body_args
from agent_toolkit._testing.wi_mutations_support import (
    _disable_convert_git,
    _disable_transition_git,
    _write_convert_awi,
    _write_uwi_entry,
)
from agent_toolkit.atk_test import _FIXED_DT, _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file


@pytest.mark.parametrize(
    "operation",
    ["hold", "unhold", "start-processing", "dependencies", "cooldown", "target-repo", "batch", "result", "rewrite"],
)
def test_public_append_survives_following_updates(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """公開追記と後続の本文・メタデータ更新を結合し、更新対象外の作業記録を保持する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "record.md", body="元の要求")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as appended:
        atk.main(_edit_body_args(tmp_path, path.name, "再開に必要な記録", append=True), home=tmp_path)
    assert appended.value.code == 0
    original = path.read_text(encoding="utf-8")
    parsed = frontmatter_parser.parse_frontmatter(original)
    assert parsed is not None
    original_body = parsed[1]
    if operation in {"hold", "unhold", "start-processing"}:
        if operation == "unhold":
            with pytest.raises(SystemExit) as held:
                atk.main(["wi", "hold", path.name], home=tmp_path)
            assert held.value.code == 0
        with pytest.raises(SystemExit) as changed:
            atk.main(["wi", operation, path.name], home=tmp_path)
        assert changed.value.code == 0
    elif operation == "dependencies":
        _write_awi_file(notes, "dependency.md")
        mutation_dependencies.set_entry_dependencies(notes, filename=path.name, depends_on=("dependency.md",))
    elif operation == "cooldown":
        with pytest.raises(SystemExit) as changed:
            atk.main(["wi", "edit", path.name, "--cooldown-until", "2999-01-01T00:00:00+00:00"], home=tmp_path)
        assert changed.value.code == 0
    elif operation == "target-repo":
        body_file = tmp_path / "repo.md"
        body_file.write_text(original.replace("github.com/example/foo", "github.com/example/bar"), encoding="utf-8")
        with pytest.raises(SystemExit) as changed:
            atk.main(["wi", "edit", path.name, "--body-file", str(body_file)], home=tmp_path)
        assert changed.value.code == 0
    elif operation == "batch":
        destination = tmp_path / "imported"
        destination.mkdir()
        original = original.rstrip("\n") + "\n"
        wi_batch.add_batch_entries(destination, texts=[f"### {path.name} [inbox]\n{original}"], now=_FIXED_DT)
        notes = destination
        path = wi_entries.entry_path(notes, path.name)
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    else:
        with pytest.raises(SystemExit) as changed:
            atk.main(["wi", "adopt", path.name, "--note", "終了結果"], home=tmp_path)
        assert changed.value.code == 0
        if operation == "rewrite":
            text = path.read_text(encoding="utf-8")
            text += "- 対応commit: aaaaaaa\n- 対応commit件名: 旧件名\n"
            path.write_text(text, encoding="utf-8")
            monkeypatch.setattr(result_commits, "resolve_repo_id", lambda _value: "github.com/example/foo")
            monkeypatch.setattr(targets, "resolve_commit", lambda *_args: wi_entries.CommitMetadata("bbbbbbb", "新件名"))
            result_commits.rewrite_adopted_commits(
                notes, worktree=tmp_path, filenames={path.name}, rewrite={"a" * 40: "b" * 40}
            )
    saved = path.read_text(encoding="utf-8")
    assert original_body in saved
    metadata = frontmatter_parser.parse_frontmatter(saved)
    assert metadata is not None
    if operation in {"hold", "unhold", "start-processing", "result", "rewrite"}:
        expected_state = {
            "hold": "hold",
            "unhold": "inbox",
            "start-processing": "processing",
            "result": "adopted",
            "rewrite": "adopted",
        }[operation]
        assert metadata[0]["state"] == expected_state
    elif operation == "dependencies":
        assert metadata[0]["depends_on"] == ["dependency.md"]
    elif operation == "cooldown":
        assert metadata[0]["cooldown_until"] == "2999-01-01T00:00:00+00:00"
    elif operation == "target-repo":
        assert metadata[0]["target_repo"] == "github.com/example/bar"
    elif operation == "batch":
        assert saved == original
    if operation == "rewrite":
        assert "- 対応commit: bbbbbbb\n- 対応commit件名: 新件名\n" in saved
        assert "- 対応commit: aaaaaaa" not in saved
    capsys.readouterr()
    with pytest.raises(SystemExit) as shown:
        atk.main(["wi", "show", path.name, "--skip-pull"], home=tmp_path)
    assert shown.value.code == 0
    assert "再開に必要な記録" in capsys.readouterr().out


def test_record_preservation_assertion_detects_dependency_body_loss(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """差戻し以外の更新が記録を削除する対照で、結合試験の保持判定が失敗する。"""
    update = mutation_dependencies.set_entry_dependencies

    def lose_record(notes: pathlib.Path, **kwargs: Any) -> object:
        result = update(notes, **kwargs)
        path = _fixed_path(notes, str(kwargs["filename"]))
        text = path.read_text(encoding="utf-8")
        path.write_text(text.partition("\n## 処理結果\n")[0] + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr(mutation_dependencies, "set_entry_dependencies", lose_record)
    with pytest.raises(AssertionError):
        test_public_append_survives_following_updates(tmp_path, monkeypatch, capsys, "dependencies")


def test_edit_keeps_section_error_without_style_warning(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """必須節の欠落を拒否し、表記診断は出さず保存済み本文を保持する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "entry.md", body=AGENT_AWI_BODY, source="test")
    original = path.read_bytes()
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as result:
        atk.main(_edit_body_args(tmp_path, "entry.md", "本文\u2014補足"), home=tmp_path)
    assert result.value.code == 1
    error = capsys.readouterr().err
    assert "必須節" in error
    assert "表記診断" not in error
    assert path.read_bytes() == original


@pytest.mark.parametrize("dry_run", [False, True])
def test_edit_accepts_colloquial_and_dash_without_warnings(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    """frontmatter付き置換の口語・ダッシュを、保存とdry-runで警告せず保持する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "entry.md", body=AGENT_AWI_BODY, source="test")
    original = path.read_bytes()
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    body = AGENT_AWI_BODY + "\n説明\u2014補足。すぐ\u6765\u3044。\n"
    args = _edit_body_args(tmp_path, "entry.md", "---\ntitle: 原稿\n---\n" + body)
    with pytest.raises(SystemExit) as result:
        atk.main([*args, "--dry-run"] if dry_run else args, home=tmp_path)
    assert result.value.code == 0
    assert not capsys.readouterr().err
    if dry_run:
        assert path.read_bytes() == original
    else:
        assert body in path.read_text(encoding="utf-8")


def test_hold_and_unhold_reuse_standard_transition(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """holdとunholdは専用復旧状態を作成せず既存の状態遷移で往復する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    _disable_transition_git(monkeypatch)

    mutation_transitions.transition_entries(notes, action="hold", filenames=["entry.md"], now=_FIXED_DT)
    assert "state: hold\n" in _fixed_path(notes, "entry.md").read_text(encoding="utf-8")

    mutation_transitions.transition_entries(notes, action="unhold", filenames=["entry.md"], now=_FIXED_DT)
    assert "state: inbox\n" in _fixed_path(notes, "entry.md").read_text(encoding="utf-8")


def test_return_rejected_entry_to_inbox_strips_terminal_result(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不採用項目をinboxへ戻す際に終了時の結果節だけを除去する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    _disable_transition_git(monkeypatch)
    mutation_transitions.transition_entries(notes, action="reject", filenames=["entry.md"], now=_FIXED_DT)
    rejected = _fixed_path(notes, "entry.md")
    assert "## 処理結果" in rejected.read_text(encoding="utf-8")

    mutation_transitions.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["entry.md"],
        state="rejected",
        now=_FIXED_DT,
    )

    returned = _fixed_path(notes, "entry.md")
    assert returned.is_file()
    assert "## 処理結果" not in returned.read_text(encoding="utf-8")


def test_remove_targets_explicit_and_stored_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """明示した状態と保存した状態に応じて対象を削除する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    content = "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n本文\n"
    inbox = _fixed_path(notes, "inbox.md")
    processing = _fixed_path(notes, "processing.md")
    inbox.write_text(content, encoding="utf-8")
    processing_content = content.replace("state: inbox", "state: processing")
    processing.write_text(processing_content, encoding="utf-8")

    removed = mutation_transitions.transition_entries(
        notes,
        action="remove",
        filenames=[inbox.name],
        now=_FIXED_DT,
        state="inbox",
        expected_content=content,
    )
    assert removed == [inbox.name]
    assert not inbox.exists()
    assert processing.read_text(encoding="utf-8") == processing_content

    inbox.write_text(content, encoding="utf-8")
    removed = mutation_transitions.transition_entries(
        notes,
        action="remove",
        filenames=[processing.name],
        now=_FIXED_DT,
        force=True,
    )
    assert removed == [processing.name]
    assert inbox.exists()
    assert not processing.exists()


def test_set_dependencies_can_clear_dependencies(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """依存オプション省略時は既存の明示依存を解除する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    text = path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [old.md]\n")
    path.write_text(text, encoding="utf-8")
    _disable_convert_git(monkeypatch)

    mutation_dependencies.set_entry_dependencies(notes, filename="awi.md", depends_on=())

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert "depends_on" not in parsed[0]


def test_return_to_inbox_moves_processing_to_inbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """return-to-inboxがprocessingからinboxへ戻す。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["entry.md"], now=_FIXED_DT)
    filenames = mutation_transitions.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert "state: inbox\n" in _fixed_path(notes, "entry.md").read_text(encoding="utf-8")


def test_cooldown_return_rejects_uwi_mixture_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UWI混在入力を一括検証し、移動もfrontmatter更新も行わない。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "awi.md")
    _write_uwi_entry(notes, "uwi.md")
    _disable_transition_git(monkeypatch)
    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["awi.md", "uwi.md"], now=_FIXED_DT)
    awi = _fixed_path(notes, "awi.md")
    uwi_path = _fixed_path(notes, "uwi.md")
    original_awi = awi.read_text(encoding="utf-8")
    original_uwi = uwi_path.read_text(encoding="utf-8")

    with pytest.raises(_wi_web_input.WebInputError, match="AWI専用"):
        mutation_transitions.transition_entries(
            notes,
            action="return-to-inbox",
            filenames=["awi.md", "uwi.md"],
            now=_FIXED_DT,
            cooldown_days=3,
        )

    assert awi.read_text(encoding="utf-8") == original_awi
    assert uwi_path.read_text(encoding="utf-8") == original_uwi


class TestRejectDeletes:
    """rejectサブコマンド: ファイルをinboxからrejected/へ移動する。"""

    def test_single_file_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rejectでファイルがinboxから移動されrejected/に置かれコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "state: rejected\n" in _fixed_path(notes, "fb-001.md").read_text(encoding="utf-8")
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (rejected)" in commit_cmd


class TestRejectZeroArgs:
    """rejectサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject"], home=tmp_path)

        assert exc_info.value.code == 2


class TestRmMultiple:
    """rmサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrmで両方削除と単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 entries" in commit_cmds[0]


def test_agent_environment_allows_comment_neutral_edit_and_append(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """ユーザーコメントを持たない通常本文の編集と追記はエージェント環境でも成功する。"""
    notes = _setup_notes(tmp_path)
    edit_path = _write_awi_file(notes, "edit.md", body="編集前", source="test")
    append_path = _write_awi_file(notes, "append.md", body="追記前")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as edit_exit:
        atk.main(_edit_body_args(tmp_path, "edit.md", "編集後"), home=tmp_path)
    with pytest.raises(SystemExit) as append_exit:
        atk.main(_edit_body_args(tmp_path, "append.md", "追記後", append=True), home=tmp_path)

    assert edit_exit.value.code == 0
    assert append_exit.value.code == 0
    assert edit_path.read_text(encoding="utf-8").endswith("編集後\n")
    assert append_path.read_text(encoding="utf-8").endswith("追記前\n\n\n## 処理結果\n\n追記後")


@pytest.mark.parametrize("source", [None, "test"])
def test_agent_environment_edit_preserves_saved_user_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    source: str | None,
) -> None:
    """予約節を含まないMESSAGEで保存済みユーザーコメント節を保持する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(
        notes,
        "fb.md",
        body="編集前\n\n## ユーザーコメント\n\n保持する",
        source=source,
    )
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb.md", "編集後"), home=tmp_path)

    assert exc_info.value.code == 0
    assert path.read_text(encoding="utf-8").endswith("編集後\n\n## ユーザーコメント\n\n保持する\n")


def test_agent_environment_append_inserts_before_user_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """追記本文を保存済みユーザーコメント節の直前へ追加する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb.md", body="追記前\n\n## ユーザーコメント\n\n保持する")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb.md", "追記後", append=True), home=tmp_path)

    assert exc_info.value.code == 0
    text = path.read_text(encoding="utf-8")
    assert text.index("追記前") < text.index("## 処理結果") < text.index("追記後") < text.index("## ユーザーコメント")
    assert text.endswith("## ユーザーコメント\n\n保持する\n")


@pytest.mark.parametrize(
    ("body", "message", "result_count"),
    [
        ("追記前", "追記後", 1),
        ("追記前\n\n## 処理結果\n\n既存メモ", "追記後", 1),
        ("追記前", "## 処理結果\n\n追記後", 1),
    ],
)
def test_agent_append_adds_one_result_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    body: str,
    message: str,
    result_count: int,
) -> None:
    """エージェント追記は処理メモの直前に処理結果境界を一度だけ保存する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb.md", body=body)
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb.md", message, append=True), home=tmp_path)

    assert exc_info.value.code == 0
    text = path.read_text(encoding="utf-8")
    assert text.count("## 処理結果") == result_count
    assert text.index(message.splitlines()[-1]) > text.index("## 処理結果")


def test_agent_append_preserves_original_and_user_comment_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """境界の追加は原本文と保存済みユーザーコメントのバイト列を変えない。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb.md", body="原本文\n\n```text\n添付ログ\n```\n\n## ユーザーコメント\n\n要求")
    original = path.read_bytes()
    comment = "## ユーザーコメント\n\n要求\n".encode()
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb.md", "処理メモ", append=True), home=tmp_path)

    assert exc_info.value.code == 0
    saved = path.read_bytes()
    assert original.removesuffix(comment) in saved
    assert saved.endswith(comment)


def test_human_append_inserts_before_first_result_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """人間の追記は既存の最初の処理結果境界より前へ保存する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb.md", body="元の要求\n\n## 処理結果\n\n処理メモ")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb.md", "追加要求", append=True), home=tmp_path)

    assert exc_info.value.code == 0
    text = path.read_text(encoding="utf-8")
    assert text.index("元の要求") < text.index("追加要求") < text.index("## 処理結果")


def test_agent_environment_rejects_malformed_user_comment_structure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """予約節を一意に抽出できない編集結果も変更として拒否する。"""
    monkeypatch.setenv("AI_AGENT", "1")

    assert mutation_content._reject_agent_user_comment_change(  # pylint: disable=protected-access
        "本文\n",
        "本文\n\n## ユーザーコメント\n\n1\n\n## ユーザーコメント\n\n2\n",
    )
    failure, next_action = capsys.readouterr().err.splitlines()
    assert failure == _USER_COMMENT_ERROR
    # 受信側が再実行の前に行う操作（予約節を本文から除く）を次の操作の行で受け取れること。
    assert next_action.startswith("次の操作: ")
    assert "ユーザーコメント節" in next_action
    assert "除いて再実行する" in next_action
