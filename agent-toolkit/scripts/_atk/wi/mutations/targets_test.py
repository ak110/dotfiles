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

from _atk import managed_temp as _managed_temp  # noqa: E402  # pylint: disable=wrong-import-position
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


from _atk.wi.mutations.test_support_test import *  # noqa: F403


def test_add_empty_awi_keeps_detailed_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実質空AWIのCLI拒否案内に判定条件と対象先頭を含める。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "add", "--target-repo", "github.com/example/foo", "-"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "空文字・空白のみ・箇条書きマーカー単独文字" in captured.err
    assert "該当メッセージの先頭: -" in captured.err


def test_return_rejected_entry_conflict_preserves_terminal_result(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """復帰先が競合する場合は不採用項目を変更せず保持する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="reject", filenames=["entry.md"], now=_FIXED_DT)
    rejected = notes / "rejected/entry.md"
    before = rejected.read_bytes()
    _write_awi_file(notes, "entry.md")

    with pytest.raises(SystemExit) as exc_info:
        mutations.transition_entries(
            notes,
            action="return-to-inbox",
            filenames=["entry.md"],
            state="rejected",
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 2
    assert rejected.read_bytes() == before
    assert "## 処理結果" in rejected.read_text(encoding="utf-8")


def test_remove_rejects_changed_and_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """確認後の内容変更とUTF-8読取り不能を競合として削除しない。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    original = "---\ntype: awi\n---\n\n確認時本文\n"

    changed = notes / "inbox/changed.md"
    changed.write_text(original.replace("確認時", "外部更新後"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[changed.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert changed.exists()

    pull_changed = notes / "inbox/pull-changed.md"
    pull_changed.write_text(original, encoding="utf-8")

    def update_during_pull(_path: pathlib.Path) -> None:
        pull_changed.write_text(original.replace("確認時", "pull後"), encoding="utf-8")

    monkeypatch.setattr(mutations, "_pull", update_during_pull)
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[pull_changed.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert pull_changed.read_text(encoding="utf-8").endswith("pull後本文\n")

    unreadable = notes / "inbox/unreadable.md"
    unreadable.write_bytes(b"\xff")
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.transition_entries(
            notes,
            action="remove",
            filenames=[unreadable.name],
            now=_FIXED_DT,
            state="inbox",
            expected_content=original,
        )
    assert unreadable.exists()


def test_edit_entry_to_plan_rejects_plan_file_only_in_working_root_without_changes(
    tmp_path: pathlib.Path,
) -> None:
    """保存前の作業計画を拒否し、hold項目の状態を維持する。"""
    notes = _setup_notes(tmp_path)
    entry = _write_convert_awi(notes, "awi.md", state="hold")
    original = entry.read_text(encoding="utf-8")
    plan = pathlib.Path.home() / ".claude/plans/30-working-plan-a1b2.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="atk plans commit"):
        mutations.edit_entry_to_plan(
            notes,
            filename="awi.md",
            content="本文",
            plan_file=str(plan),
            target_commit="a" * 40,
        )

    assert entry.read_text(encoding="utf-8") == original


def test_convert_to_plan_migrates_legacy_entry_dependencies_when_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依存指定を省略した変換は旧entries依存をトップレベルへ移行する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(
        notes,
        "awi.md",
        schedule_mapping=(
            "queue_schedule:\n  dependency:\n    kind: entries\n    filenames: [predecessor.md, predecessor.md]\n"
        ),
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    details = mutations.convert_entry_to_plan(notes, filename="awi.md", plan_file=str(plan))

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == ["predecessor.md"]
    assert "queue_schedule" not in parsed[0]
    assert details["depends_on"] == ["predecessor.md"]


def test_convert_to_plan_removes_legacy_none_dependency_when_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧none依存は依存なしの意味を保って旧scheduleだけを除去する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(
        notes,
        "awi.md",
        schedule_mapping="queue_schedule:\n  dependency:\n    kind: none\n",
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    mutations.convert_entry_to_plan(notes, filename="awi.md", plan_file=str(plan))

    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert "depends_on" not in parsed[0]
    assert "queue_schedule" not in parsed[0]


def test_convert_to_plan_rejects_uwi_repo_mismatch_and_self_dependency(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UWI、投入先不一致、自己依存をそれぞれ拒否する。"""
    notes = _setup_notes(tmp_path)
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    _write_convert_awi(notes, "uwi.md", entry_type="uwi")
    with pytest.raises(mutations.WebInputError, match="AWIだけ"):
        mutations.convert_entry_to_plan(notes, filename="uwi.md", plan_file=str(plan))

    _write_convert_awi(notes, "awi.md")
    with pytest.raises(mutations.WebInputError, match="target_repoが一致しません"):
        mutations.convert_entry_to_plan(
            notes,
            filename="awi.md",
            plan_file=str(plan),
            target_repo="github.com/example/other",
        )
    with pytest.raises(mutations.WebInputError, match="自分自身"):
        mutations.convert_entry_to_plan(
            notes,
            filename="awi.md",
            plan_file=str(plan),
            depends_on=("awi",),
        )


def test_set_dependencies_cli_rejects_cycle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """直接CLI呼び出しも循環拒否をユーザー向け終了状態へ変換する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_awi(notes, "first.md")
    _write_convert_awi(notes, "second.md")
    first.write_text(
        first.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [second.md]\n"),
        encoding="utf-8",
    )
    _disable_convert_git(monkeypatch)

    with pytest.raises(SystemExit) as captured:
        atk.main(
            ["wi", "set-dependencies", "second.md", "--depends-on", "first.md", "--target-repo", "github.com/example/foo"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 1
    assert "循環する依存" in capsys.readouterr().err


def test_convert_multiple_entries_rejects_duplicate_before_writing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重複入力を全体拒否し、対象本文を変更しない。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    original = path.read_text(encoding="utf-8")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="重複"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=("awi.md", "awi"),
            plan_file=str(plan),
        )

    assert path.read_text(encoding="utf-8") == original


def test_convert_to_plan_rejects_mixed_states_before_writing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """inboxとholdの入力混在を全体拒否する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_awi(notes, "20260827-000000-001.md")
    second = _write_convert_awi(notes, "20260827-000000-002.md", state="hold")
    first_original = first.read_text(encoding="utf-8")
    second_original = second.read_text(encoding="utf-8")
    plan = _write_integration_plan(tmp_path, "a" * 40, (first.name, second.name))
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="異なる状態"):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(first.name, second.name),
            plan_file=str(plan),
            message="統合本文",
        )

    assert first.read_text(encoding="utf-8") == first_original
    assert second.read_text(encoding="utf-8") == second_original
    assert not (notes / "inbox" / second.name).exists()


@pytest.mark.parametrize(
    ("legacy", "source_description"),
    [(False, "計画メタ情報の関連WI"), (True, "計画の提示素材")],
)
def test_convert_held_entries_identifies_source_for_awi_outside_hold(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
    source_description: str,
) -> None:
    """hold外の変換元awiを新旧計画入力の参照元付きで拒否する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_awi(notes, "20260827-000000-001.md", state="hold")
    second = _write_convert_awi(notes, "20260827-000000-002.md", state="inbox")
    originals = {path: path.read_text(encoding="utf-8") for path in (first, second)}
    writer = _write_legacy_integration_plan if legacy else _write_integration_plan
    plan = writer(tmp_path, "a" * 40, (first.name, second.name))
    _disable_convert_git(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)

    expected = f"{source_description}の変換元awiがholdに存在しません: {second.name}"
    with pytest.raises(mutations.WebInputError, match=re.escape(expected)):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(first.name,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert all(path.read_text(encoding="utf-8") == content for path, content in originals.items())


def test_convert_held_entries_restores_all_paths_after_commit_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit失敗時にholdの全入力とindexを開始時へ戻す。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_convert_awi(notes, filename, state="hold")
    original = source.read_text(encoding="utf-8")
    start_head = _initialize_private_notes_git(notes)
    plan = _write_integration_plan(tmp_path, "a" * 40, (filename,))
    _disable_real_convert_network(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(mutations, "_commit_and_push", fail_commit)
    with pytest.raises(subprocess.CalledProcessError):
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


def test_cmd_convert_to_plan_displays_saved_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """変換コマンドが保存後に返された照合対象を表示する。"""
    details: dict[str, object | None] = {
        "target_repo": "github.com/example/foo",
        "target_commit": "a" * 40,
        "plan_file": "/tmp/plan.md",
        "depends_on": ["dependency.md"],
        "body_match": "一致",
        "saved_body": "保存本文\n",
    }
    monkeypatch.setattr(
        mutations,
        "convert_entries_to_plan",
        lambda *_args, **_kwargs: {"entries": [details], "commit": "b" * 40},
    )
    args = argparse.Namespace(
        filename="awi.md",
        plan_file="/tmp/plan.md",
        depends_on=["dependency.md"],
        target_repo="github.com/example/foo",
    )

    mutations._cmd_convert_to_plan(args, tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    output = capsys.readouterr().out
    assert "target_repo: github.com/example/foo" in output
    assert f"target_commit: {'a' * 40}" in output
    assert "plan_file: /tmp/plan.md" in output
    assert "depends_on: dependency.md" in output
    assert "変換commit:" not in output
    assert "push: 完了" not in output


class TestAdoptMultiple:
    """adoptサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のadoptで全件がadopted/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        _write_awi_file(notes, "fb-003.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md", "fb-002.md", "fb-003.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        assert not (inbox / "fb-003.md").exists()
        adopted = notes / "adopted"
        assert (adopted / "fb-001.md").exists()
        assert (adopted / "fb-002.md").exists()
        assert (adopted / "fb-003.md").exists()

        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 3 entries (adopted)" in commit_cmds[0]


class TestSkipPush:
    """adopt・rejectの中間操作でpushを省略し、最後に滞留commitを送信する。"""

    def test_adopt_skip_push_commits_without_push_and_reports_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """adoptの--skip-pushはcommitだけを実行し未push状態を案内する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md", "--skip-push"], home=tmp_path)

        assert exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert any(command[:2] == ["git", "add"] for command in commands)
        assert any(command[:2] == ["git", "commit"] for command in commands)
        assert ["git", "push"] not in commands
        captured = capsys.readouterr()
        assert "未pushのcommit" in captured.err
        assert "atk wi commit" in captured.err

    def test_reject_skip_push_commits_without_push_and_reports_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rejectの--skip-pushはcommitだけを実行し未push状態を案内する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "fb-001.md", "--skip-push"], home=tmp_path)

        assert exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert any(command[:2] == ["git", "add"] for command in commands)
        assert any(command[:2] == ["git", "commit"] for command in commands)
        assert ["git", "push"] not in commands
        captured = capsys.readouterr()
        assert "未pushのcommit" in captured.err
        assert "atk wi commit" in captured.err

    def test_default_adopt_pushes_without_skip_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--skip-pushを指定しないadoptは従来どおりpushし注記を出力しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert ["git", "push"] in commands
        assert "--skip-push" not in capsys.readouterr().err

    def test_pending_commits_are_pushed_by_following_normal_transition(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """skip-push後の通常遷移が先行分を含む滞留commitをまとめてpushする。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as first_exc_info:
            atk.main(["wi", "adopt", "fb-001.md", "--skip-push"], home=tmp_path)
        assert first_exc_info.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as second_exc_info:
            atk.main(["wi", "reject", "fb-002.md"], home=tmp_path)
        assert second_exc_info.value.code == 0
        commands = [call["cmd"] for call in git_calls]
        assert sum(command[:4] == ["git", "commit", "-m", "chore: process 1 entry (adopted)"] for command in commands) == 1
        assert sum(command[:4] == ["git", "commit", "-m", "chore: process 1 entry (rejected)"] for command in commands) == 1
        assert commands.count(["git", "push"]) == 2
        assert "--skip-push" not in capsys.readouterr().err

    def test_commit_pushes_clean_ahead_repository(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """キューがcleanでもaheadの滞留commitを`atk wi commit`でpushする。"""
        notes = _setup_notes(tmp_path)
        for state in ("processing", "adopted", "rejected"):
            (notes / state).mkdir()
        (notes / "adopted" / "base.md").write_text("base\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=notes, capture_output=True, text=True, check=True)
        remote = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        (notes / "adopted" / "pending.md").write_text("pending\n", encoding="utf-8")
        subprocess.run(["git", "add", "adopted/pending.md"], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "pending"], cwd=notes, capture_output=True, text=True, check=True)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "commit"], home=tmp_path)

        assert exc_info.value.code == 0
        local_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.strip()
        remote_head = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert local_head == remote_head
        assert "差分なし。滞留commitをpushしました。" in capsys.readouterr().out

    def test_real_git_transitions_push_and_recover_after_remote_advances(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """通常遷移は即時pushし、remote進行後もskip-pushの滞留commitを回復する。"""
        first_home = tmp_path / "first"
        first_home.mkdir()
        notes = _setup_notes(first_home)
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
        for state in ("processing", "adopted", "rejected"):
            (notes / state).mkdir()
        for filename in ("default-adopt.md", "default-reject.md", "skip.md", "following.md"):
            _write_awi_file(notes, filename)

        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        for key, value in (("user.name", "queue-test"), ("user.email", "queue-test@example.invalid")):
            subprocess.run(["git", "config", key, value], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "."], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=notes, capture_output=True, text=True, check=True)

        remote = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )

        second_home = tmp_path / "second"
        second_home.mkdir()
        second_notes = second_home / "private-notes"
        subprocess.run(
            ["git", "clone", "--branch", "main", str(remote), str(second_notes)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        for key, value in (("user.name", "queue-test"), ("user.email", "queue-test@example.invalid")):
            subprocess.run(["git", "config", key, value], cwd=second_notes, capture_output=True, text=True, check=True)

        def local_head() -> str:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
            ).stdout.strip()

        def remote_head() -> str:
            return subprocess.run(
                ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        with pytest.raises(SystemExit) as adopt_exit:
            atk.main(["wi", "adopt", "default-adopt.md"], home=first_home, now=_FIXED_DT)
        assert adopt_exit.value.code == 0
        assert local_head() == remote_head()

        with pytest.raises(SystemExit) as reject_exit:
            atk.main(["wi", "reject", "default-reject.md"], home=first_home, now=_FIXED_DT)
        assert reject_exit.value.code == 0
        assert local_head() == remote_head()

        subprocess.run(["git", "pull", "--ff-only"], cwd=second_notes, capture_output=True, text=True, check=True)
        (notes / "adopted" / "pending.md").write_text("pending\n", encoding="utf-8")
        subprocess.run(["git", "add", "adopted/pending.md"], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "pending"], cwd=notes, capture_output=True, text=True, check=True)
        pending_head = local_head()
        remote_before_skip = remote_head()

        with pytest.raises(SystemExit) as skip_exit:
            atk.main(["wi", "adopt", "skip.md", "--skip-push"], home=first_home, now=_FIXED_DT)
        assert skip_exit.value.code == 0
        assert remote_head() == remote_before_skip
        assert local_head() != remote_head()
        assert (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", pending_head, "HEAD"],
                cwd=notes,
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
        )
        assert subprocess.run(
            ["git", "log", "-2", "--format=%s"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.splitlines() == ["chore: process 1 entry (adopted)", "pending"]

        (second_notes / "remote-update.txt").write_text("remote update\n", encoding="utf-8")
        subprocess.run(["git", "add", "remote-update.txt"], cwd=second_notes, capture_output=True, text=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "remote update"],
            cwd=second_notes,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["git", "push"], cwd=second_notes, capture_output=True, text=True, check=True)

        with pytest.raises(SystemExit) as following_exit:
            atk.main(["wi", "reject", "following.md"], home=first_home, now=_FIXED_DT)
        assert following_exit.value.code == 0
        assert local_head() == remote_head()
        assert (notes / "adopted/skip.md").is_file()
        assert (notes / "rejected/following.md").is_file()

    def test_skip_push_without_remote_does_not_report_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """remote未設定の管理リポジトリでは--skip-pushの注記を出力しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        (notes / common._LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md", "--skip-push"], home=tmp_path)

        assert exc_info.value.code == 0
        assert ["git", "push"] not in [call["cmd"] for call in git_calls]
        assert "未pushのcommit" not in capsys.readouterr().err


def test_edit_entry_to_plan_rejects_inbox_name_conflict_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換先inboxの同名競合時はholdと競合先を変更しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_awi_file(notes, filename)
    held = notes / "hold" / filename
    source.replace(held)
    original = held.read_text(encoding="utf-8")
    conflict = notes / "inbox" / filename
    conflict.write_text("---\ntype: awi\n---\n\n競合本文\n", encoding="utf-8")
    conflict_original = conflict.read_text(encoding="utf-8")
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    with pytest.raises(mutations.WebInputError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    assert held.read_text(encoding="utf-8") == original
    assert conflict.read_text(encoding="utf-8") == conflict_original
    assert not (notes / "processing" / filename).exists()
    assert not commit_calls


def test_edit_plan_cli_uses_plan_base_commit_instead_of_current_head(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画後にHEADが進んでも計画メタ情報のベースコミットを保存する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_awi_file(notes, filename)
    source.replace(notes / "hold" / filename)
    plan_base = "a" * 40
    current_head = "b" * 40
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        f"# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- ベースコミット: `{plan_base}`\n\n## 提示素材\n\n- {filename}\n",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    worktree = tmp_path / "target-worktree"
    worktree.mkdir()
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", worktree),
    )
    monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")
    monkeypatch.setattr(mutations._add, "resolve_head_commit", lambda _path: current_head)  # pylint: disable=protected-access
    resolved: list[tuple[pathlib.Path, str]] = []

    def resolve_commit(path: pathlib.Path, revision: str) -> str:
        resolved.append((path, revision))
        return revision

    monkeypatch.setattr(mutations, "_resolve_commit", resolve_commit)

    with pytest.raises(SystemExit) as captured:
        atk.main(
            [
                "wi",
                "edit",
                filename,
                "統合本文",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 0
    output = notes / "inbox" / filename
    parsed = frontmatter_parser.parse_frontmatter(output.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["target_commit"] == plan_base
    assert parsed[0]["target_commit"] != current_head
    assert resolved == [(worktree, plan_base)]
    assert not (notes / "processing" / filename).exists()


class TestStartProcessingFailureBoundaries:
    """一括移動後のcommit・push失敗と限定復旧の境界を検証する。"""

    def test_commit_failure_leaves_only_requested_moves_and_commit_recovers_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """commit前失敗後は指定移動だけを`atk wi commit`で1回復旧できる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
        monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
        monkeypatch.setattr(mutations, "_pull", lambda _path: None)
        transition_calls: list[str] = []

        def fail_commit(*_args: object, **_kwargs: object) -> None:
            transition_calls.append("transition")
            raise subprocess.CalledProcessError(1, ["git", "commit"])

        monkeypatch.setattr(mutations, "_commit_and_push", fail_commit)
        with pytest.raises(subprocess.CalledProcessError):
            mutations.transition_entries(
                notes,
                action="start-processing",
                filenames=["fb-001.md", "fb-002.md"],
                target_repo="github.com/example/foo",
                now=_FIXED_DT,
            )

        assert transition_calls == ["transition"]
        assert sorted(path.name for path in (notes / "processing").iterdir()) == ["fb-001.md", "fb-002.md"]
        assert not list((notes / "inbox").iterdir())

        status_outputs = [" M processing/fb-001.md\n M processing/fb-002.md\n", ""]
        recovery_calls: list[tuple[object, ...]] = []
        push_calls: list[pathlib.Path] = []

        def fake_status(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            assert cmd[:3] == ["git", "status", "--porcelain"]
            return subprocess.CompletedProcess(cmd, 0, status_outputs.pop(0), "")

        def recover_commit(*args: object, **_kwargs: object) -> None:
            recovery_calls.append(args)

        monkeypatch.setattr(subprocess, "run", fake_status)
        monkeypatch.setattr(mutations, "_commit_and_push", recover_commit)
        monkeypatch.setattr(mutations, "_push_pending_commits", push_calls.append)
        assert mutations.commit_entries(notes) is True
        assert mutations.commit_entries(notes) is False
        assert len(recovery_calls) == 1
        assert push_calls == [notes, notes, notes]

    def test_push_failure_after_transition_commit_is_not_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """遷移commit後のpush失敗はcleanな未pushcommitを残し、完了扱いしない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        for state in ("processing", "adopted", "rejected"):
            (notes / state).mkdir()
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "add", "."], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=notes, capture_output=True, text=True, check=True)
        remote = tmp_path / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=notes, capture_output=True, text=True, check=True)
        subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=True,
        )
        monkeypatch.setattr(mutations, "_pull", lambda _path: None)
        before_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.strip()
        calls: list[str] = []

        def fail_push(_path: pathlib.Path) -> None:
            calls.append("push")
            raise subprocess.CalledProcessError(1, ["git", "push"])

        monkeypatch.setattr(common, "_push_pending_commits", fail_push)
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            mutations.transition_entries(
                notes,
                action="start-processing",
                filenames=["fb-001.md", "fb-002.md"],
                target_repo="github.com/example/foo",
                now=_FIXED_DT,
            )

        assert exc_info.value.cmd == ["git", "push"]
        assert calls == ["push"]
        assert sorted(path.name for path in (notes / "processing").iterdir()) == ["fb-001.md", "fb-002.md"]
        assert not list((notes / "inbox").iterdir())
        after_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=notes, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert after_head != before_head
        status = subprocess.run(["git", "status", "--porcelain"], cwd=notes, capture_output=True, text=True, check=True)
        assert status.stdout == ""
        upstream_check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", after_head, "@{u}"],
            cwd=notes,
            capture_output=True,
            text=True,
            check=False,
        )
        assert upstream_check.returncode != 0
