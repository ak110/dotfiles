# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
"""atk (agent-toolkit `atk wi`) のadopt/reject/rm/edit・パストラバーサル検証のテスト。

adopt・reject・rm・editサブコマンドと、ファイル名引数の不正値拒否の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_wi_list_test.py`・
`_atk_wi_show_test.py`・`_atk_wi_process_loop_test.py`に分離する。
位置引数の重複除去（FB7）テストは`too-many-lines`回避のため`_atk_wi_dedup_test.py`へ分離する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import argparse
import contextlib
import datetime
import pathlib
import re
import subprocess
import sys

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import managed_temp as _managed_temp  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import (  # pylint: disable=wrong-import-position
    common,  # noqa: E402  # pylint: disable=wrong-import-position
    mutations,  # noqa: E402  # pylint: disable=wrong-import-position
    user_comment,  # noqa: E402  # pylint: disable=wrong-import-position
    uwi,  # noqa: E402  # pylint: disable=wrong-import-position
)
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit.atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
)  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = "失敗: " + user_comment.AGENT_USER_COMMENT_EDIT_ERROR


from agent_toolkit._atk.wi.mutations.test_support_test import *  # noqa: F403


def _edit_body_args(tmp_path: pathlib.Path, filename: str, message: str, *, append: bool = False) -> list[str]:
    """本文をファイルへ保存し、`atk wi edit`の引数列を返す。"""
    body_path = tmp_path / f"{filename}.body.md"
    body_path.write_text(message, encoding="utf-8")
    args = ["wi", "edit", filename]
    if append:
        args.append("--append")
    return [*args, "--body-file", str(body_path)]


def test_edit_reports_style_warning_with_new_section_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """整った通常AWIを不完全な本文へ変更するとき警告と構造エラーを返す。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "entry.md", body=AGENT_AWI_BODY, source="test")
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "entry.md", "本文\u2014補足"), home=tmp_path)

    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    lines = error.splitlines()
    warning_index = next(index for index, line in enumerate(lines) if line.startswith("警告: 本文:2:3: ダッシュ"))
    # 表記の警告の直後に、直し方（本文を置き換えるコマンド）を示す次の操作の行が続くこと。
    assert lines[warning_index + 1].startswith("次の操作: ")
    assert "atk wi edit entry.md --body-file" in lines[warning_index + 1]
    assert "必須節" in error
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("diagnostic_count", [0, 2])
def test_public_edit_reports_style_actions_once(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    diagnostic_count: int,
) -> None:
    """編集は全診断を出力して案内を一度だけ置き、診断なしの編集では案内を省く。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    body = "\n".join(["説明\u2014補足"] * diagnostic_count) or "説明。"
    with pytest.raises(SystemExit) as result:
        atk.main(_edit_body_args(tmp_path, "entry.md", body), home=tmp_path)
    assert result.value.code == 0
    lines = capsys.readouterr().err.splitlines()
    assert len([line for line in lines if line.startswith("警告: 本文:") and "ダッシュ" in line]) == diagnostic_count
    assert len([line for line in lines if line.startswith("次の操作: ")]) == bool(diagnostic_count)


def test_hold_and_unhold_reuse_standard_transition(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """holdとunholdは専用復旧状態を作成せず既存の状態遷移で往復する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    _disable_transition_git(monkeypatch)

    mutations.transition_entries(notes, action="hold", filenames=["entry.md"], now=_FIXED_DT)
    assert (notes / "hold/entry.md").is_file()

    mutations.transition_entries(notes, action="unhold", filenames=["entry.md"], now=_FIXED_DT)
    assert (notes / "inbox/entry.md").is_file()


def test_return_rejected_entry_to_inbox_strips_terminal_result(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不採用項目をinboxへ戻す際に終端結果節だけを除去する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="reject", filenames=["entry.md"], now=_FIXED_DT)
    rejected = notes / "rejected/entry.md"
    assert "## 処理結果" in rejected.read_text(encoding="utf-8")

    mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["entry.md"],
        state="rejected",
        now=_FIXED_DT,
    )

    returned = notes / "inbox/entry.md"
    assert returned.is_file()
    assert "## 処理結果" not in returned.read_text(encoding="utf-8")


def test_remove_targets_explicit_state_and_keeps_legacy_priority(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定時は指定側を削除し、省略時はprocessing優先を維持する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    content = "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n本文\n"
    inbox = notes / "inbox/same.md"
    processing = notes / "processing/same.md"
    processing.parent.mkdir()
    inbox.write_text(content, encoding="utf-8")
    processing.write_text(content, encoding="utf-8")

    removed = mutations.transition_entries(
        notes,
        action="remove",
        filenames=["same.md"],
        now=_FIXED_DT,
        state="inbox",
        expected_content=content,
    )
    assert removed == ["same.md"]
    assert not inbox.exists()
    assert processing.read_text(encoding="utf-8") == content

    inbox.write_text(content, encoding="utf-8")
    removed = mutations.transition_entries(
        notes,
        action="remove",
        filenames=["same.md"],
        now=_FIXED_DT,
        force=True,
    )
    assert removed == ["same.md"]
    assert inbox.exists()
    assert not processing.exists()


def test_set_dependencies_can_clear_dependencies(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """依存オプション省略時は既存の明示依存を解除する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    text = path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [old.md]\n")
    path.write_text(text, encoding="utf-8")
    _disable_convert_git(monkeypatch)

    mutations.set_entry_dependencies(notes, filename="awi.md", depends_on=())

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert "depends_on" not in parsed[0]


def test_return_to_inbox_moves_processing_to_inbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """return-to-inboxがprocessingからinboxへ戻す。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    mutations.transition_entries(notes, action="start-processing", filenames=["entry.md"], now=_FIXED_DT)
    filenames = mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "inbox/entry.md").is_file()
    assert not (notes / "processing/entry.md").exists()


def test_cooldown_return_rejects_uwi_mixture_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UWI混在入力を一括検証し、移動もfrontmatter更新も行わない。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "awi.md")
    _write_uwi_entry(notes, "uwi.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["awi.md", "uwi.md"], now=_FIXED_DT)
    awi = notes / "processing/awi.md"
    uwi_path = notes / "processing/uwi.md"
    original_awi = awi.read_text(encoding="utf-8")
    original_uwi = uwi_path.read_text(encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="AWI専用"):
        mutations.transition_entries(
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
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "rejected" / "fb-001.md").exists()
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
    assert append_path.read_text(encoding="utf-8").endswith("追記前\n\n\n追記後")


def test_agent_environment_edit_preserves_saved_user_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """予約節を含まないMESSAGEで保存済みユーザーコメント節を保持する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(
        notes,
        "fb.md",
        body="編集前\n\n## ユーザーコメント\n\n保持する",
        source="test",
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
    assert text.index("追記前") < text.index("追記後") < text.index("## ユーザーコメント")
    assert text.endswith("## ユーザーコメント\n\n保持する\n")


def test_agent_environment_rejects_malformed_user_comment_structure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """予約節を一意に抽出できない編集結果も変更として拒否する。"""
    monkeypatch.setenv("AI_AGENT", "1")

    assert mutations._reject_agent_user_comment_change(  # pylint: disable=protected-access
        "本文\n",
        "本文\n\n## ユーザーコメント\n\n1\n\n## ユーザーコメント\n\n2\n",
    )
    failure, next_action = capsys.readouterr().err.splitlines()
    assert failure == _USER_COMMENT_ERROR
    # 受信側が再実行の前に行う操作（予約節を本文から除く）を次の操作の行で受け取れること。
    assert next_action.startswith("次の操作: ")
    assert "ユーザーコメント節" in next_action
    assert "除いて再実行する" in next_action


class TestEditNoEditor:
    """editサブコマンド: $EDITOR未設定でexit 1となる。"""

    def test_no_editor_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITORが未設定の場合はexit 1と案内が出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        monkeypatch.delenv("EDITOR", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "EDITOR" in captured.err


class TestNoninteractiveEdit:
    """editサブコマンドの本文ファイル指定による非対話編集を検証する。"""

    def test_awi_body_updates_without_editor_and_preserves_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """EDITOR未設定でも本文を更新し、未指定メタデータを保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前", source="session-review")
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == (
            "---\ntarget_repo: github.com/example/foo\ntype: awi\nsource: session-review\n---\n\n編集後\n"
        )

    def test_agent_environment_rejects_edit_without_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """sourceを持たない項目のsource未指定編集を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 1
        assert path.read_text(encoding="utf-8") == original
        error = capsys.readouterr().err
        assert "frontmatter" in error
        assert "--source" not in error

    def test_agent_environment_accepts_source_added_or_changed_by_edit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """エージェント編集でsourceの新規指定と変更を受理する。"""
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


class TestCooldownEdit:
    """再処理抑制期限の設定と解除を状態別に検証する。"""

    @pytest.mark.parametrize("state", ["inbox", "hold"])
    def test_set_and_clear(
        self, state: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="本文", source="test")
        if state == "hold":
            (notes / state).mkdir(exist_ok=True)
            path = path.rename(notes / state / path.name)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as set_exit:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", "2999-01-01T00:00:00+00:00"], home=tmp_path)
        assert set_exit.value.code == 0
        set_lines = capsys.readouterr().out.splitlines()
        assert set_lines[0].startswith("成功: ")
        assert set_lines[1:] == ["    cooldown_until: 2999-01-01T00:00:00+00:00"]
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["cooldown_until"] == "2999-01-01T00:00:00+00:00"
        if state == "inbox":
            capsys.readouterr()
            with pytest.raises(SystemExit) as listing_exit:
                atk.main(["wi", "list", "--skip-pull"], home=tmp_path)
            assert listing_exit.value.code == 0
            assert "blocked_reason=cooldown-until cooldown_until=2999-01-01T00:00:00+00:00" in capsys.readouterr().out

        with pytest.raises(SystemExit) as clear_exit:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", ""], home=tmp_path)
        assert clear_exit.value.code == 0
        clear_lines = capsys.readouterr().out.splitlines()
        assert clear_lines[0].startswith("成功: ")
        assert clear_lines[1:] == ["    cooldown_until: なし"]
        assert "cooldown_until" not in path.read_text(encoding="utf-8")
        if state == "inbox":
            capsys.readouterr()
            with pytest.raises(SystemExit) as listing_exit:
                atk.main(["wi", "list", "--skip-pull"], home=tmp_path)
            assert listing_exit.value.code == 0
            assert "[inbox/normal/ready]" in capsys.readouterr().out

    @pytest.mark.parametrize("value", ["2026-10-01T12:00:00", "invalid"])
    def test_rejects_invalid_datetime(self, value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="本文", source="test")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as failure:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", value], home=tmp_path)
        assert failure.value.code == 1
        assert path.read_text(encoding="utf-8") == original

    def test_rejects_processing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "entry.md", body="本文", source="test")
        (notes / "processing").mkdir(exist_ok=True)
        path = path.rename(notes / "processing" / path.name)
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as failure:
            atk.main(["wi", "edit", "entry.md", "--cooldown-until", ""], home=tmp_path)
        assert failure.value.code == 2
        assert path.read_text(encoding="utf-8") == original


class TestEditBodyFile:
    """editサブコマンドの本文ファイル入力を検証する。"""

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
            mutations._add,  # pylint: disable=protected-access
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
        parsed = frontmatter_parser.parse_frontmatter((notes / "hold" / filename).read_text(encoding="utf-8"))
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

    def test_target_repo_is_normalized_and_existing_frontmatter_lines_are_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """明示したtarget_repoを正規化し、他の意味的なキーを保持する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text(
            "---\n# 保持するコメント\ntarget_repo: old.example/a/b\n\n"
            "target_repo: old.example/a/b\ntype: awi\nsource: manual\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ntarget_repo: https://github.com/Example/Repo.git\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0] == {
            "target_repo": "github.com/example/repo",
            "type": "awi",
            "source": "manual",
        }
        assert parsed[1] == "\n編集後\n"

    @pytest.mark.parametrize(
        ("message", "exit_code", "error_fragment"),
        [
            ("---\ntype: uwi\n---\n\n本文", 2, "typeは変更できない"),
            ("---\nscope: item\n---\n\n本文", 1, "AWIでは指定できない"),
            (" \n-\n ", 1, "実質空"),
        ],
    )
    def test_awi_rejects_invalid_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
        exit_code: int,
        error_fragment: str,
    ) -> None:
        """種別変更・UWI専用キー・実質空本文を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == exit_code
        assert error_fragment in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_positional_message_is_rejected_without_modifying_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """位置引数で渡した本文を拒否し、保存済み項目を変更しない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        message_file = tmp_path / "message.txt"
        message_file.write_text("編集後", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", str(message_file)], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "解釈できない引数" in captured.err
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n編集前\n")

    def test_empty_uwi_add_is_allowed_but_empty_edit_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空のUWI質問はaddで許容し、既存質問を削除するeditでは拒否する。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        add_body = tmp_path / "add-body.md"
        add_body.write_text("", encoding="utf-8")

        with pytest.raises(SystemExit) as add_exit:
            atk.main(
                [
                    "wi",
                    "add",
                    "--target-repo",
                    "github.com/example/foo",
                    "--type=uwi",
                    "--question-type=free-form",
                    "--body-file",
                    str(add_body),
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert add_exit.value.code == 0
        filename = f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"
        assert (notes / "inbox" / filename).is_file()

        with pytest.raises(SystemExit) as edit_exit:
            atk.main(_edit_body_args(tmp_path, filename, ""), home=tmp_path)

        assert edit_exit.value.code == 1
        captured = capsys.readouterr()
        assert "質問本文は空にできません" in captured.err
        assert "Traceback" not in captured.err

    def test_processing_awi_can_be_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`processing`配下のAWIも非対話で編集する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir()
        path = processing / "fb-001.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n編集前\n",
            encoding="utf-8",
        )
        for name in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_agent_environment_can_edit_hold_item(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """エージェント環境でもholdの本文置換を受理する。"""
        notes = _setup_notes(tmp_path)
        inbox_path = _write_awi_file(notes, "fb-001.md", body="編集前", source="test")
        hold = notes / "hold"
        path = inbox_path.rename(hold / inbox_path.name)
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_edit_rejects_undetermined_direct_cause_even_for_legacy_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """既存本文が必須節を欠く旧書式でも、原因の未確定を宣言する本文への置換を拒否し保存本文を保つ。"""
        notes = _setup_notes(tmp_path)
        inbox_path = _write_awi_file(notes, "fb-001.md", body="編集前", source="test")
        path = inbox_path.rename(notes / "hold" / inbox_path.name)
        original = path.read_text(encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "編集後\n\n| 項目 | 内容 |\n| --- | --- |\n| 直接的原因 | 調査中 |\n"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 1
        error = capsys.readouterr().err
        assert "編集を拒否した: 直接的原因が未確定のまま保存しようとした" in error
        assert "保存済みの本文がそのまま残る" in error
        assert path.read_text(encoding="utf-8") == original

    def test_processing_hold_allows_agent_replacement(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processingから保留した項目も、inboxと同じくエージェント環境から本文を置換できる。

        holdの項目の編集はinboxと同じ条件で許すため、保留前の状態で置換を拒否すると
        処理中だった項目の更新だけが操作不能になる。エージェント環境で処理中の項目を保留するには
        `--state=processing`を要するため、保留はその形で行う。
        """
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="編集前", source="test")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        for argv in (["start-processing", "fb-001.md"], ["hold", "--state=processing", "fb-001.md"]):
            with pytest.raises(SystemExit) as transition:
                atk.main(["wi", *argv], home=tmp_path, now=_FIXED_DT)
            assert transition.value.code == 0

        with pytest.raises(SystemExit) as replaced:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "変更後"), home=tmp_path)
        assert replaced.value.code == 0
        saved = (notes / "hold/fb-001.md").read_text(encoding="utf-8")
        assert "変更後" in saved
        assert "編集前" not in saved
        assert "held_from_state" not in saved

        with pytest.raises(SystemExit) as unheld:
            atk.main(["wi", "unhold", "fb-001.md"], home=tmp_path, now=_FIXED_DT)
        assert unheld.value.code == 0
        assert "変更後" in (notes / "inbox/fb-001.md").read_text(encoding="utf-8")

    def test_agent_environment_rejects_processing_body_replacement(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エージェント環境からprocessingの本文を置換せず終了コード2で拒否する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir()
        path = processing / "fb-001.md"
        original = "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n編集前\n"
        path.write_text(original, encoding="utf-8")
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 2
        failure, next_action = capsys.readouterr().err.splitlines()
        assert failure.startswith("失敗: processingの項目はエージェント環境から編集できない: fb-001.md")
        # 置換の代わりに使える投入と追記の2つの操作を、実在するコマンドで示すこと。
        assert next_action.startswith("次の操作: ")
        assert "atk wi add" in next_action
        assert "atk wi edit --append" in next_action
        assert path.read_text(encoding="utf-8") == original

    def test_agent_environment_can_append_to_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """エージェント環境でもprocessingへの追記を受理する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir()
        path = processing / "fb-001.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n追記前\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AI_AGENT", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "追記後", append=True), home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("追記前\n\n\n追記後")

    def test_uwi_question_and_scope_update_preserves_answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
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
        message = "---\nscope: new\n---\n\n変更後の質問"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "uwi-001.md", message), home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert "scope: new" in content
        assert "変更後の質問" in content
        assert content.split(uwi.ANSWER_HEADING, maxsplit=1)[1] == original_answer

    @pytest.mark.parametrize(
        "message",
        [
            "---\nsubmitter_session: other-session\n---\n\n変更後の質問",
            "---\nsubmitter_session: self-session\n---\n\n変更後の質問",
        ],
    )
    def test_uwi_edit_keeps_stored_submitter_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
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

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize(
        "message",
        [
            f"変更後\n\n{uwi.ANSWER_HEADING}\n",
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
        """回答マーカー重複時も終端側を基準に質問だけを更新する。"""
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

    def test_expected_content_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """競合時は上書きせず、FILENAMEと未反映を案内する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        original_edit = mutations.edit_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: str,
            target_repo: str | None = None,
            lock_timeout: float = -1,
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
                lock_timeout=lock_timeout,
                expected_content=expected_content,
                finalized_content=finalized_content,
            )

        monkeypatch.setattr(mutations, "edit_entry_content", conflict)
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

    def test_edit_rejects_explicit_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非対話editによってtarget_commitを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\ntarget_commit: {'b' * 40}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

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

        assert mutations.edit_entry_content(
            notes,
            state="inbox",
            filename="fb-001.md",
            content=updated,
            lock_timeout=2.0,
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

        assert mutations.edit_entry_content(
            notes,
            state="inbox",
            filename="fb-001.md",
            content=updated,
            lock_timeout=2.0,
        )

        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_edit_rejects_explicit_plan_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """editによってplan_fileを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nplan_file: /tmp/plan.md\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_rejects_explicit_cooldown_until(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """通常editによって再処理抑制期限を注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ncooldown_until: 2026-08-15T00:00:00+00:00\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

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

    @pytest.mark.parametrize(
        ("reserved_key", "reserved_value"),
        [("repair_target", "broken.md"), ("repair_kind", "frontmatter")],
    )
    def test_edit_rejects_explicit_repair_metadata(
        self,
        reserved_key: str,
        reserved_value: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """editによって修復UWIの予約キーを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\n{reserved_key}: {reserved_value}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_raises_web_input_error_when_frontmatter_is_corrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter全体が破損している場合は編集を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(_edit_body_args(tmp_path, "fb-001.md", "編集後"), home=tmp_path)

        assert exc_info.value.code == 1
        assert "frontmatterが破損" in capsys.readouterr().err


def _next_action_lines(err: str) -> list[str]:
    """標準エラーから次の操作の行だけを返す。"""
    return [line for line in err.splitlines() if line.startswith("次の操作: ")]


@pytest.mark.parametrize(
    ("reserved_key", "reserved_value", "expected_route"),
    [
        ("depends_on", "[other.md]", "atk wi set-dependencies"),
        ("cooldown_until", "2026-08-15T00:00:00+00:00", "--cooldown-until"),
        ("target_commit", "b" * 40, "target_commitを除いて"),
        ("plan_file", "/tmp/plan.md", "plan_fileを除いて"),
    ],
)
def test_edit_reserved_key_rejection_names_alternative_route(
    reserved_key: str,
    reserved_value: str,
    expected_route: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """予約キーを拒否する際は、その値を変更するために使える別の操作を示す。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb-001.md")
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    message = f"---\n{reserved_key}: {reserved_value}\n---\n\n編集後"

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb-001.md", message), home=tmp_path)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert f"{reserved_key}は予約キー" in err
    next_actions = _next_action_lines(err)
    assert len(next_actions) == 1
    assert expected_route in next_actions[0]
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("message", "expected_values"),
    [
        ("---\nquestion_type: invalid\n---\n\n変更後", ("choice", "yes-no", "free-form")),
        ("---\nquestion_type: choice\nchoices:\n---\n\n変更後", ("choices",)),
    ],
)
def test_uwi_invalid_question_metadata_names_accepted_values(
    message: str,
    expected_values: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """不正な回答形式の拒否は、受理する値か補う項目を次の操作として示す。"""
    notes = _setup_notes(tmp_path)
    _write_uwi_entry(notes, "uwi-001.md")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "uwi-001.md", message), home=tmp_path)

    assert exc_info.value.code == 1
    next_actions = _next_action_lines(capsys.readouterr().err)
    assert len(next_actions) == 1
    for value in expected_values:
        assert value in next_actions[0]


def test_uwi_edit_with_broken_stored_structure_points_to_show(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存済みUWIの構造が壊れている場合は、確認するコマンドとユーザーへの報告を示す。"""
    notes = _setup_notes(tmp_path)
    path = notes / "inbox" / "uwi-001.md"
    path.write_text(
        f"---\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{uwi.QUESTION_HEADING}\n\n質問\n",
        encoding="utf-8",
    )
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "uwi-001.md", "変更後の質問"), home=tmp_path)

    assert exc_info.value.code == 1
    next_actions = _next_action_lines(capsys.readouterr().err)
    assert len(next_actions) == 1
    assert "atk wi show uwi-001.md" in next_actions[0]
    assert "ユーザーへ報告する" in next_actions[0]
    assert path.read_text(encoding="utf-8") == original


def test_editor_failure_keeps_entry_and_names_retry_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エディターが非0で終わった場合は、tracebackではなく失敗行と再実行の手段を示す。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb-001.md")
    original = path.read_text(encoding="utf-8")
    monkeypatch.setenv("EDITOR", "fake-editor")
    fallback = _make_subprocess_fake([])

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        if cmd[0] == "fake-editor":
            return subprocess.CompletedProcess(cmd, returncode=1)
        return fallback(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    next_actions = _next_action_lines(err)
    assert len(next_actions) == 1
    assert "atk wi edit fb-001.md --body-file" in next_actions[0]
    assert path.read_text(encoding="utf-8") == original


def test_terminal_state_edit_rejection_guides_to_accepted_hold_form(tmp_path: pathlib.Path) -> None:
    """終端した項目の編集拒否は、`atk wi hold`が受理する`--state`の値付きの形を案内する。"""
    with pytest.raises(mutations.WebInputError) as raised:
        mutations.edit_entry_content(tmp_path, state="adopted", filename="20260930-000000-001.md", content="本文")

    next_action = raised.value.next_action
    assert "`atk wi hold <ファイル名> --state <adopted|rejected>`" in next_action
    parser = atk._build_parser()  # pylint: disable=protected-access
    parser.parse_args(["wi", "hold", "20260930-000000-001.md", "--state", "adopted"])
    # 値を省いた`--state`は受理されないため、案内は値付きの形でなければならない。
    with pytest.raises(SystemExit):
        parser.parse_args(["wi", "hold", "20260930-000000-001.md", "--state"])
