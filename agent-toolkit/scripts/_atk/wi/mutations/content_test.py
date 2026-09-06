# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _managed_temp  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
)  # noqa: E402  # pylint: disable=wrong-import-position

from _atk.wi import (  # pylint: disable=wrong-import-position
    common,  # noqa: E402  # pylint: disable=wrong-import-position
    mutations,  # noqa: E402  # pylint: disable=wrong-import-position
    user_comment,  # noqa: E402  # pylint: disable=wrong-import-position
    uwi,  # noqa: E402  # pylint: disable=wrong-import-position
)
from _atk.wi import frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = user_comment.AGENT_USER_COMMENT_EDIT_ERROR + "\n"


pytest_plugins = ["_atk.wi.mutations.test_support_test"]
from _atk.wi.mutations.test_support_test import *  # noqa: F403


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


def test_convert_to_plan_accepts_and_stores_portable_plan_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新規計画のportable値を受理し、同じ可搬表記をfrontmatterへ保存する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    relative = pathlib.Path("plans/2026/08/30-portable-plan-a1b2.md")
    plan = notes / relative
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")
    portable = f"$(atk config get private_notes)/{relative.as_posix()}"
    _disable_convert_git(monkeypatch)

    details = mutations.convert_entries_to_plan(
        notes,
        filenames=("awi.md",),
        plan_file=portable,
        target_repo="github.com/example/foo",
    )

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["plan_file"] == portable
    assert details["plan_file"] == portable


def test_plan_file_write_paths_store_portable_value_for_absolute_input(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存root配下の計画を絶対パスで渡しても、全ての書込経路が可搬値を保存する。"""
    _disable_convert_git(monkeypatch)
    _disable_transition_git(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)
    plan_relative = pathlib.PurePosixPath("plans/2026/08/plan.md")
    expected = f"$(atk config get private_notes)/{plan_relative}"

    def setup_notes(case: str) -> pathlib.Path:
        root = tmp_path / case
        root.mkdir()
        return _setup_notes(root)

    def stored_plan_file(path: pathlib.Path) -> object:
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        return parsed[0]["plan_file"]

    notes = setup_notes("convert")
    entry = _write_convert_awi(notes, "awi.md")
    plan = _write_convert_plan(notes / plan_relative.parent, "a" * 40)
    details = mutations.convert_entries_to_plan(notes, filenames=("awi.md",), plan_file=str(plan))
    assert stored_plan_file(entry) == expected
    assert details["plan_file"] == expected

    notes = setup_notes("hold")
    held_names = ("20260827-000000-001.md", "20260827-000000-002.md")
    for name in held_names:
        _write_convert_awi(notes, name, state="hold")
    plan = _write_integration_plan(notes / plan_relative.parent, "a" * 40, held_names)
    details = mutations.convert_entries_to_plan(
        notes,
        filenames=held_names,
        plan_file=str(plan),
        message="統合本文",
        local_worktree=tmp_path / "target-worktree",
        skip_push=True,
    )
    assert stored_plan_file(notes / "inbox" / held_names[0]) == expected
    assert details["plan_file"] == expected

    notes = setup_notes("edit")
    edit_name = "20260827-000000-001.md"
    _write_convert_awi(notes, edit_name, state="hold")
    plan = _write_integration_plan(notes / plan_relative.parent, "a" * 40, (edit_name,))
    details = mutations.edit_entry_to_plan(
        notes,
        filename=edit_name,
        content="---\nsummary: 編集本文\n---\n\n編集本文\n",
        plan_file=str(plan),
        target_commit="a" * 40,
        target_repo="github.com/example/foo",
    )
    assert stored_plan_file(notes / "inbox" / edit_name) == expected
    assert details["plan_file"] == expected


@pytest.mark.parametrize(
    ("plan_value", "expected"),
    [("relative.md", "絶対パス"), ("missing", "保存先に実体がありません")],
)
def test_convert_to_plan_rejects_invalid_plan(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    plan_value: str,
    expected: str,
) -> None:
    """相対パスと未存在の計画ファイルを拒否する。"""
    notes = _setup_notes(tmp_path)
    _write_convert_awi(notes, "awi.md")
    _disable_convert_git(monkeypatch)
    value = plan_value if plan_value == "relative.md" else str(tmp_path / plan_value)

    with pytest.raises(mutations.WebInputError, match=expected):
        mutations.convert_entry_to_plan(notes, filename="awi.md", plan_file=value)


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


def test_convert_to_plan_keeps_saved_change_when_push_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit後のpush失敗契約に従い、変換済みファイルを保持して例外を伝播する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)

    commit_calls: list[tuple[object, ...]] = []

    def fail_after_commit(*args: object, **_kwargs: object) -> None:
        commit_calls.append(args)
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(mutations, "_commit_and_push", fail_after_commit)

    with pytest.raises(subprocess.CalledProcessError):
        mutations.convert_entry_to_plan(notes, filename="awi.md", plan_file=str(plan))
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["plan_file"] == str(plan)
    assert commit_calls[0][2] == ("inbox/awi.md",)

    push_calls: list[pathlib.Path] = []
    monkeypatch.setattr(
        mutations,
        "_commit_and_push",
        lambda *_args, **_kwargs: pytest.fail("再実行時に新規commitを作成してはならない"),
    )
    monkeypatch.setattr(mutations, "_push_pending_commits", push_calls.append)

    with pytest.raises(mutations.WebInputError, match="既に計画型"):
        mutations.convert_entry_to_plan(notes, filename="awi.md", plan_file=str(plan))

    assert push_calls == [notes]


def test_convert_held_entries_rejects_unrepresentable_legacy_dependency_before_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """holdの旧形式依存を移行できない場合は書込み前に拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_convert_awi(notes, filename, state="hold")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "type: awi\n",
            "type: awi\nqueue_schedule:\n  dependency:\n    kind: external-user\n",
        ),
        encoding="utf-8",
    )
    original = path.read_text(encoding="utf-8")
    plan = _write_integration_plan(tmp_path, "a" * 40, (filename,))
    _disable_convert_git(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="旧形式の依存を計画実装型へ移行できません"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert path.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists()


def test_convert_held_entries_rejects_destination_conflict_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """統合先inboxの同名競合時はholdを変更しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_convert_awi(notes, filename, state="hold")
    original = source.read_text(encoding="utf-8")
    conflict = notes / "inbox" / filename
    conflict.write_text("---\ntype: awi\n---\n\n競合本文\n", encoding="utf-8")
    conflict_original = conflict.read_text(encoding="utf-8")
    plan = _write_integration_plan(tmp_path, "a" * 40, (filename,))
    _disable_convert_git(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="同名"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert source.read_text(encoding="utf-8") == original
    assert conflict.read_text(encoding="utf-8") == conflict_original


def test_convert_held_entries_restores_all_paths_after_write_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """書込み失敗時にholdの全入力とindexを開始時へ戻す。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_convert_awi(notes, filename, state="hold")
    original = source.read_text(encoding="utf-8")
    start_head = _initialize_private_notes_git(notes)
    plan = _write_integration_plan(tmp_path, "a" * 40, (filename,))
    _disable_real_convert_network(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)

    def fail_after_write(path: pathlib.Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        raise OSError("テスト用書込み失敗")

    monkeypatch.setattr(mutations, "_atomic_write_text", fail_after_write)
    with pytest.raises(OSError, match="書込み失敗"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert source.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists()
    assert (
        subprocess.run(
            ["git", "-C", str(notes), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        == start_head
    )
    assert (
        subprocess.run(["git", "-C", str(notes), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        == ""
    )


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


class TestAdoptMissing:
    """adoptサブコマンド: 存在しないファイル指定でexit 2となる。"""

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


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
    edit_path = _write_awi_file(notes, "edit.md", body="編集前")
    append_path = _write_awi_file(notes, "append.md", body="追記前")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as edit_exit:
        atk.main(["wi", "edit", "edit.md", "編集後"], home=tmp_path)
    with pytest.raises(SystemExit) as append_exit:
        atk.main(["wi", "edit", "--append", "append.md", "追記後"], home=tmp_path)

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
    path = _write_awi_file(notes, "fb.md", body="編集前\n\n## ユーザーコメント\n\n保持する")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "edit", "fb.md", "編集後"], home=tmp_path)

    assert exc_info.value.code == 0
    assert path.read_text(encoding="utf-8").endswith("編集後\n\n## ユーザーコメント\n\n保持する\n")


def test_agent_environment_append_inserts_before_user_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """追記MESSAGEを保存済みユーザーコメント節の直前へ追加する。"""
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb.md", body="追記前\n\n## ユーザーコメント\n\n保持する")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "edit", "--append", "fb.md", "追記後"], home=tmp_path)

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
    assert capsys.readouterr().err == _USER_COMMENT_ERROR


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
    """editサブコマンドのMESSAGE指定による非対話編集を検証する。"""

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
            atk.main(["wi", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == (
            "---\ntarget_repo: github.com/example/foo\ntype: awi\nsource: session-review\n---\n\n編集後\n"
        )

    def test_message_does_not_start_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE指定時はEDITORが設定済みでもエディターを起動しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "must-not-run")
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[0] == "must-not-run":
                pytest.fail("MESSAGE指定時にEDITORが起動された")
            return _make_subprocess_fake(git_calls)(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", "編集後"], home=tmp_path)

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
            atk.main(["wi", "edit", "fb-001.md", message], home=tmp_path)

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
            ("---\ntype: uwi\n---\n\n本文", 2, "typeを変更"),
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
            atk.main(["wi", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == exit_code
        assert error_fragment in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_existing_file_path_is_rejected_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """実在ファイルパスだけのMESSAGEをtracebackなしで拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="編集前")
        message_file = tmp_path / "message.txt"
        message_file.write_text("編集後", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", str(message_file)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ファイルパス" in captured.err
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

        with pytest.raises(SystemExit) as add_exit:
            atk.main(
                [
                    "wi",
                    "add",
                    "--target-repo",
                    "github.com/example/foo",
                    "--type=uwi",
                    "--question-type=free-form",
                    "",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert add_exit.value.code == 0
        filename = f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"
        assert (notes / "inbox" / filename).is_file()

        with pytest.raises(SystemExit) as edit_exit:
            atk.main(["wi", "edit", filename, ""], home=tmp_path)

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
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

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
            atk.main(["wi", "edit", "uwi-001.md", message], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert "scope: new" in content
        assert "変更後の質問" in content
        assert content.split(uwi.ANSWER_HEADING, maxsplit=1)[1] == original_answer

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
            atk.main(["wi", "edit", "uwi-001.md", message], home=tmp_path)

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
            atk.main(["wi", "edit", "uwi-001.md", "変更後の質問"], home=tmp_path)

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
            atk.main(["wi", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映されていません" in captured.err
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
            atk.main(["wi", "edit", "fb-001.md", "本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "差分なし。" in capsys.readouterr().out
        assert not [call for call in git_calls if "commit" in call["cmd"]]

    def test_edit_rejects_explicit_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非対話edit経路からtarget_commitを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\ntarget_commit: {'b' * 40}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", message], home=tmp_path)

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
        """edit経路からplan_fileを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nplan_file: /tmp/plan.md\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_rejects_explicit_cooldown_until(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """通常edit経路から再処理抑制期限を注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ncooldown_until: 2026-08-15T00:00:00+00:00\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", message], home=tmp_path)

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
            atk.main(["wi", "edit", "fb-001.md", "編集後"], home=tmp_path)

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
        """edit経路から修復UWIの予約キーを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\n{reserved_key}: {reserved_value}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", message], home=tmp_path)

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
            atk.main(["wi", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        assert "frontmatterが破損" in capsys.readouterr().err
