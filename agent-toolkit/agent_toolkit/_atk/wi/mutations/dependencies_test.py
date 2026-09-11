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

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = user_comment.AGENT_USER_COMMENT_EDIT_ERROR + "\n"


from agent_toolkit._atk.wi.mutations.test_support_test import *  # noqa: F403


def test_hold_entries_accept_processing_terminal_removal_and_content_operations(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保留中項目を再開・採否・削除・本文変更の対象にする。"""
    notes = _setup_notes(tmp_path)
    filenames = ["process.md", "adopt.md", "reject.md", "remove.md", "edit.md", "append.md"]
    for filename in filenames:
        _write_awi_file(notes, filename)
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="hold", filenames=filenames, now=_FIXED_DT)

    mutations.transition_entries(notes, action="start-processing", filenames=["process.md"], state="hold", now=_FIXED_DT)
    mutations.transition_entries(notes, action="adopt", filenames=["adopt.md"], state="hold", now=_FIXED_DT)
    mutations.transition_entries(notes, action="reject", filenames=["reject.md"], state="hold", now=_FIXED_DT)
    mutations.transition_entries(notes, action="remove", filenames=["remove.md"], state="hold", now=_FIXED_DT)

    calls: list[tuple[str, pathlib.Path]] = []

    def record_edit(_notes: pathlib.Path, *, directory: pathlib.Path, **_kwargs: object) -> bool:
        calls.append(("edit", directory))
        return True

    def record_append(_notes: pathlib.Path, *, directory: pathlib.Path, **_kwargs: object) -> bool:
        calls.append(("append", directory))
        return True

    monkeypatch.setattr(mutations, "_edit_entry", record_edit)
    monkeypatch.setattr(mutations, "_append_entry", record_append)
    assert mutations.edit_entry_content(notes, state="hold", filename="edit.md", content="編集後")
    assert mutations.append_entry_content(
        notes,
        state="hold",
        filename="append.md",
        content="\n追記\n".encode(),
    )

    assert (notes / "processing/process.md").is_file()
    assert (notes / "adopted/adopt.md").is_file()
    assert (notes / "rejected/reject.md").is_file()
    assert not (notes / "hold/remove.md").exists()
    assert calls == [("edit", notes / "hold"), ("append", notes / "hold")]


@pytest.mark.parametrize(
    ("legacy", "source_description"),
    [(False, "計画メタ情報の関連WI"), (True, "計画の提示素材")],
)
@pytest.mark.parametrize(
    ("condition", "message_suffix"),
    [
        ("missing", "を一意に特定できません"),
        ("multiple", "を一意に特定できません"),
        ("broken-frontmatter", "のfrontmatterが破損しています"),
        ("awi-outside-hold", "の変換元awiがholdに存在しません"),
        ("already-planned", "が既に計画型です"),
        ("inactive-uwi", "のUWIがactive状態ではありません"),
        ("invalid-type", "のtypeが不正です"),
        ("no-awi", "に変換元awiがありません"),
    ],
)
def test_plan_awi_paths_identifies_plan_input_source_in_errors(
    tmp_path: pathlib.Path,
    legacy: bool,
    source_description: str,
    condition: str,
    message_suffix: str,
) -> None:
    """計画入力の通常検証エラーが新旧どちらの参照元かを示す。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    if condition == "multiple":
        _write_convert_awi(notes, filename, state="hold")
        _write_convert_awi(notes, filename, state="inbox")
    elif condition == "broken-frontmatter":
        path = notes / "hold" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("本文\n", encoding="utf-8")
    elif condition == "already-planned":
        path = _write_convert_awi(notes, filename, state="hold")
        path.write_text(path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\nplan_file: x\n"), encoding="utf-8")
    elif condition == "awi-outside-hold":
        _write_convert_awi(notes, filename, state="inbox")
    elif condition == "inactive-uwi":
        _write_convert_awi(notes, filename, entry_type="uwi", state="adopted")
    elif condition == "invalid-type":
        _write_convert_awi(notes, filename, entry_type="invalid", state="hold")
    elif condition == "no-awi":
        _write_convert_awi(notes, filename, entry_type="uwi", state="inbox")

    writer = _write_legacy_integration_plan if legacy else _write_integration_plan
    plan = writer(tmp_path, "a" * 40, (filename,))
    read_plan_input_filenames = vars(mutations)["_read_plan_input_filenames"]
    plan_awi_paths = vars(mutations)["_plan_awi_paths"]
    filenames, actual_source_description = read_plan_input_filenames(plan)

    assert actual_source_description == source_description
    with pytest.raises(mutations.WebInputError, match=re.escape(source_description + message_suffix)):
        plan_awi_paths(notes, filenames, actual_source_description)


def test_convert_to_plan_rejects_plan_file_only_in_working_root_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存前の作業計画を拒否し、変換元の状態を維持する。"""
    notes = _setup_notes(tmp_path)
    entry = _write_convert_awi(notes, "awi.md")
    original = entry.read_text(encoding="utf-8")
    plan = pathlib.Path.home() / ".claude/plans/30-working-plan-a1b2.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="atk plans commit"):
        mutations.convert_entries_to_plan(notes, filenames=("awi.md",), plan_file=str(plan))

    assert entry.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("resolved_worktree", [pathlib.Path("/worktree"), None])
def test_convert_to_plan_cli_ignores_mismatched_plan_base(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    resolved_worktree: pathlib.Path | None,
) -> None:
    """計画作成時点の参照値と`target_commit`を比較せず変換を成立させる。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md", target_commit="a" * 40)
    plan = _write_convert_plan(tmp_path, "b" * 40)
    _disable_convert_git(monkeypatch)
    resolved_targets: list[str | None] = []

    def resolve_target(value: str | None) -> tuple[str, pathlib.Path | None]:
        resolved_targets.append(value)
        return "github.com/example/foo", resolved_worktree

    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        resolve_target,
    )

    with pytest.raises(SystemExit) as captured:
        atk.main(
            [
                "wi",
                "convert-to-plan",
                "awi.md",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 0
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["plan_file"] == str(plan)
    assert resolved_targets == ["github.com/example/foo"]


def test_convert_to_plan_rejects_explicit_dependency_cycle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換時に依存を明示した場合は既存グラフへ閉路を形成しない。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_awi(notes, "first.md")
    _write_convert_awi(notes, "second.md")
    first.write_text(
        first.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [second.md]\n"),
        encoding="utf-8",
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="循環"):
        mutations.convert_entry_to_plan(
            notes,
            filename="second.md",
            plan_file=str(plan),
            depends_on=("first.md",),
        )


@pytest.mark.parametrize(
    ("existing", "filename", "dependencies"),
    [
        (("first.md", "second.md"), "second.md", ("first.md",)),
        (("first.md", "second.md", "third.md"), "third.md", ("first.md",)),
    ],
)
def test_set_dependencies_rejects_mutual_and_existing_chain_cycles(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: tuple[str, ...],
    filename: str,
    dependencies: tuple[str, ...],
) -> None:
    """相互依存と既存長鎖へ閉路を形成する更新をlock内で拒否する。"""
    notes = _setup_notes(tmp_path)
    for name in existing:
        path = _write_convert_awi(notes, name)
        if name == "first.md":
            path.write_text(
                path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [second.md]\n"),
                encoding="utf-8",
            )
        elif name == "second.md" and len(existing) == 3:
            path.write_text(
                path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [third.md]\n"),
                encoding="utf-8",
            )
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="循環"):
        mutations.set_entry_dependencies(notes, filename=filename, depends_on=dependencies)


def test_set_dependencies_updates_held_entry_and_keeps_hold_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hold状態の項目の依存を更新し、保存状態をholdのまま維持する。"""
    notes = _setup_notes(tmp_path)
    held = _write_convert_awi(notes, "held.md", state="hold")
    _write_convert_awi(notes, "first.md")
    _disable_convert_git(monkeypatch)

    mutations.set_entry_dependencies(notes, filename="held.md", depends_on=("first.md",))

    assert held.exists()
    assert not (notes / "inbox" / "held.md").exists()
    assert not (notes / "processing" / "held.md").exists()
    parsed = frontmatter_parser.parse_frontmatter(held.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == ["first.md"]


def test_set_dependencies_detects_cycle_through_held_entry(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """循環検出の母集団へhold状態の項目を含める。"""
    notes = _setup_notes(tmp_path)
    held = _write_convert_awi(notes, "held.md", state="hold")
    held.write_text(
        held.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [first.md]\n"),
        encoding="utf-8",
    )
    _write_convert_awi(notes, "first.md")
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="循環"):
        mutations.set_entry_dependencies(notes, filename="first.md", depends_on=("held.md",))


def test_convert_multiple_entries_uses_one_commit_in_input_order(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数入力を指定順に更新し、1回のcommitへまとめる。"""
    notes = _setup_notes(tmp_path)
    paths = [_write_convert_awi(notes, name) for name in ("second.md", "first.md")]
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    result = mutations.convert_entries_to_plan(
        notes,
        filenames=("second.md", "first.md"),
        plan_file=str(plan),
        skip_push=True,
    )

    assert commit_calls == [(notes, "chore: convert awi items to plans", ("inbox/second.md", "inbox/first.md"))]
    entries = result["entries"]
    assert isinstance(entries, list)
    assert len(entries) == 2
    for path in paths:
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["plan_file"] == str(plan)


@pytest.mark.parametrize(
    ("legacy", "source_description"),
    [(False, "計画メタ情報の関連WI"), (True, "計画の提示素材")],
)
def test_convert_held_entries_rejects_material_mismatch_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
    source_description: str,
) -> None:
    """CLI入力と新旧計画入力の集合が異なる場合は書込み前に拒否する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_awi(notes, "20260827-000000-001.md", state="hold")
    second = _write_convert_awi(notes, "20260827-000000-002.md", state="hold")
    originals = {path.name: path.read_text(encoding="utf-8") for path in (first, second)}
    writer = _write_legacy_integration_plan if legacy else _write_integration_plan
    plan = writer(tmp_path, "a" * 40, (first.name,))
    _disable_convert_git(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)

    expected = f"convert-to-planの入力と{source_description}が一致しません"
    with pytest.raises(mutations.WebInputError, match=re.escape(expected)):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(first.name, second.name),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    assert first.read_text(encoding="utf-8") == originals[first.name]
    assert second.read_text(encoding="utf-8") == originals[second.name]
    assert not (notes / "inbox" / first.name).exists()


def test_convert_to_plan_rejects_dirty_target_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換対象に未コミット差分がある場合は書込み前に拒否する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(notes, "awi.md")
    _initialize_private_notes_git(notes)
    original = path.read_text(encoding="utf-8") + "未コミット変更\n"
    path.write_text(original, encoding="utf-8")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_real_convert_network(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="変換対象と保存先"):
        mutations.convert_entry_to_plan(notes, filename=path.name, plan_file=str(plan))

    assert path.read_text(encoding="utf-8") == original


def test_convert_held_entries_keeps_clean_local_commit_after_push_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """push失敗時は変換済みのcleanなローカルcommitを保持する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    _write_convert_awi(notes, filename, state="hold")
    start_head = _initialize_private_notes_git(notes)
    plan = _write_integration_plan(tmp_path, "a" * 40, (filename,))
    _disable_real_convert_network(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)

    def commit_then_fail_push(
        private_notes: pathlib.Path,
        commit_message: str,
        relative_paths: tuple[str, ...],
        **_kwargs: object,
    ) -> None:
        subprocess.run(
            ["git", "-C", str(private_notes), "add", "--", *relative_paths],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(private_notes),
                "-c",
                "user.email=agent-toolkit@test.invalid",
                "-c",
                "user.name=agent-toolkit-test",
                "commit",
                "-m",
                commit_message,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        raise subprocess.CalledProcessError(1, ["git", "push"])

    monkeypatch.setattr(mutations, "_commit_and_push", commit_then_fail_push)
    with pytest.raises(subprocess.CalledProcessError):
        mutations.convert_entries_to_plan(
            notes,
            filenames=(filename,),
            plan_file=str(plan),
            message="統合本文",
            local_worktree=tmp_path / "target-worktree",
        )

    head = subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head != start_head
    assert (notes / "inbox" / filename).is_file()
    assert not (notes / "hold" / filename).exists()
    assert (
        subprocess.run(["git", "-C", str(notes), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        == ""
    )


def test_cooldown_return_rejects_uwi_without_frontmatter_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UWI単独指定も移動とfrontmatter更新の前に拒否する。"""
    notes = _setup_notes(tmp_path)
    _write_uwi_entry(notes, "uwi.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["uwi.md"], now=_FIXED_DT)
    path = notes / "processing/uwi.md"
    original = path.read_text(encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="AWI専用"):
        mutations.transition_entries(
            notes,
            action="return-to-inbox",
            filenames=["uwi.md"],
            now=_FIXED_DT,
            cooldown_days=3,
        )

    assert path.read_text(encoding="utf-8") == original
    assert not (notes / "inbox/uwi.md").exists()


class TestAdoptStampWithNoteAndCommit:
    """adopt: --note・--commit指定時に`## 処理結果`節へ全項目が追記される。"""

    def test_stamp_written_with_all_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note・--commit指定時、adopted/配下のファイル末尾に採否・処理日時・対応commit・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="元本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", "fb-001.md", "--note", "採用理由サマリー", "--commit", "abc1234"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: abc1234" in adopted_text
        assert "- メモ: 採用理由サマリー" in adopted_text


class TestRejectMultiple:
    """rejectサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_rejected_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrejectで両方がrejected/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        rejected = notes / "rejected"
        assert (rejected / "fb-001.md").exists()
        assert (rejected / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 2 entries (rejected)" in commit_cmds[0]


@pytest.mark.parametrize("environment_name", _AGENT_ENVIRONMENT_VARIABLES)
@pytest.mark.parametrize("route", ("message", "editor", "plan", "append"))
def test_agent_environment_rejects_user_comment_change_in_each_cli_route(
    route: str,
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """各エージェント環境と編集経路でユーザーコメント変更を書き込み前に拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_awi_file(notes, filename, body="本文\n\n## ユーザーコメント\n\n保持する")
    original = path.read_bytes()
    body_file = tmp_path / "body.md"
    body_file.write_text("変更後\n\n## ユーザーコメント\n\n変更する", encoding="utf-8")
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(environment_name, "1")

    argv = ["wi", "edit", filename, "--body-file", str(body_file)]
    if route == "editor":
        monkeypatch.setenv("EDITOR", "fake-editor")

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(
                    editor_path.read_text(encoding="utf-8").replace("保持する", "変更する"),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(cmd, returncode=0)
            return _make_subprocess_fake([])(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        argv = ["wi", "edit", filename]
    else:
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    if route == "append":
        argv = ["wi", "edit", "--append", filename, "--body-file", str(body_file)]
    elif route == "plan":
        held_path = notes / "hold" / filename
        path.replace(held_path)
        path = held_path
        plan = tmp_path / "plan.md"
        plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        monkeypatch.setattr(
            mutations._add,  # pylint: disable=protected-access
            "resolve_add_target",
            lambda _value: ("github.com/example/foo", worktree),
        )
        monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")
        monkeypatch.setattr(mutations, "_resolve_plan_base_commit", lambda *_args: "a" * 40)
        argv.extend(("--plan-file", str(plan), "--target-repo", "github.com/example/foo"))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path)

    assert exc_info.value.code == 1
    assert path.read_bytes() == original
    assert capsys.readouterr().err == _USER_COMMENT_ERROR


def test_agent_environment_rejects_add_with_user_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """新規投入のaddもユーザーコメント節を含む本文を拒否する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    body_file = tmp_path / "body.md"
    body_file.write_text("本文\n\n## ユーザーコメント\n\n移管するコメント", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "add",
                "--target-repo",
                "github.com/example/foo",
                "--body-file",
                str(body_file),
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 1
    assert not list((notes / "inbox").glob("*.md"))


@pytest.mark.parametrize("route", ("message", "editor", "plan", "append"))
def test_cli_edit_outputs_match_without_saved_body_for_each_write_route(
    route: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """各編集経路が一致判定だけを出力し、保存本文を再掲しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_awi_file(notes, filename, body="編集前")
    message = '編集後。"引用"を含む。\n\n## 見出し\n\n複数行。'
    body_file = tmp_path / "body.md"
    body_file.write_text(message, encoding="utf-8")
    argv = ["wi", "edit", filename, "--body-file", str(body_file)]

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        if cmd[0] == "fake-editor":
            editor_path = pathlib.Path(cmd[1])
            original = editor_path.read_text(encoding="utf-8")
            editor_path.write_text(original.replace("編集前", message), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode=0)
        return _make_subprocess_fake([])(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    if route == "editor":
        monkeypatch.setenv("EDITOR", "fake-editor")
        argv = ["wi", "edit", filename]
    elif route == "append":
        argv = ["wi", "edit", "--append", filename, "--body-file", str(body_file)]
    elif route == "plan":
        held_path = notes / "hold" / filename
        path.replace(held_path)
        plan = _write_integration_plan(tmp_path, "a" * 40, (filename,))
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        monkeypatch.setattr(
            mutations._add,  # pylint: disable=protected-access
            "resolve_add_target",
            lambda _value: ("github.com/example/foo", worktree),
        )
        _patch_integration_target_resolution(monkeypatch, "a" * 40)
        argv.extend(("--plan-file", str(plan), "--target-repo", "github.com/example/foo"))
        path = notes / "inbox" / filename

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert output.count("    body_match: 一致\n") == 1
    assert "saved_body" not in output
    assert message not in output


def test_set_dependencies_reports_body_mismatch_and_omits_body_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """依存更新は正常出力へ本文を含めず、保存後の本文改変を診断して失敗する。"""
    notes = _setup_notes(tmp_path)
    _write_convert_awi(notes, "dependency.md")
    _write_convert_awi(notes, "success.md")
    target = _write_convert_awi(notes, "target.md")
    _disable_convert_git(monkeypatch)

    with pytest.raises(SystemExit) as success:
        atk.main(
            [
                "wi",
                "set-dependencies",
                "success.md",
                "--depends-on",
                "dependency.md",
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
        )

    assert success.value.code == 0
    assert "本文" not in capsys.readouterr().out
    original_read = mutations._add._read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001
    captured: dict[str, str] = {}

    def read_after_alteration(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
        captured["expected"] = expected_body
        path.write_text(expected_body.replace("本文", "改文", 1), encoding="utf-8")
        return original_read(path, expected_body=expected_body)

    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "_read_saved_entry_details",
        read_after_alteration,
    )

    with pytest.raises(SystemExit) as mismatch:
        atk.main(
            [
                "wi",
                "set-dependencies",
                target.name,
                "--depends-on",
                "dependency.md",
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
        )

    assert mismatch.value.code == 1
    position = captured["expected"].index("本文") + 1
    error = capsys.readouterr().err
    assert f"最初の差異: {position}文字目" in error
    assert "送信元本文:" in error
    assert "保存本文:" in error


class TestEditNoArg:
    """editサブコマンド: 無引数時はinbox配下のファイル名順最大値（最終追加分）を対象とする。"""

    def test_edit_no_arg_selects_max_filename(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル存在時はファイル名順の最大値（最終追加分）が編集対象になる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "20240101-100000-001.md", body="旧")
        latest = _write_awi_file(notes, "20240201-100000-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                # 対象選択（最終追加分）の検証はcommit対象の相対パスで行う。
                editor_path = pathlib.Path(cmd[1])
                content = editor_path.read_text(encoding="utf-8")
                editor_path.write_text(content.replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit wi item" in commit_cmd
        add_cmd = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "add"]][0]
        assert str(latest.relative_to(notes)) in add_cmd

    def test_edit_no_arg_exits_on_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空の場合はexit 2でstderr案内を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox" in captured.err


class TestStartProcessingSingle:
    """start-processingサブコマンド: 1件指定でinboxからprocessing/へ移動しコミットする。"""

    def test_single_file_moved_to_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のstart-processing実行でinboxから移動されprocessing/に置かれコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "start-processing", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "processing" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: start processing 1 entry" in commit_cmd


def test_edit_entry_to_plan_reads_table_materials_and_moves_atomically(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画の提示素材表から依存を統合し、本文・metadata・状態移動を一度に確定する。"""
    notes = _setup_notes(tmp_path)
    first = _write_awi_file(notes, "20260827-000000-001.md")
    second = _write_awi_file(notes, "20260827-000000-002.md")
    first_destination = notes / "hold" / first.name
    second_destination = notes / "hold" / second.name
    first.replace(first_destination)
    second.replace(second_destination)
    first = first_destination
    second = second_destination
    first.write_text(
        first.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [external-a.md]\n"),
        encoding="utf-8",
    )
    second.write_text(
        second.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [external-b.md]\n"),
        encoding="utf-8",
    )
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        "## 提示素材\n\n"
        "| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| P-001 | フィードバック | 20260827-000000-001.md | test | 本文全文 |\n"
        "| P-002 | フィードバック | 20260827-000000-002.md | test | 本文全文 |\n\n"
        "| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| R-P-001-001 | P-001, P-002 | 統合する。 | 採用 | 統合 | 非該当 | 計画へ反映するため。 |\n",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    details = mutations.edit_entry_to_plan(
        notes,
        filename="20260827-000000-001.md",
        content="---\nsummary: 統合本文\n---\n\n統合本文\n",
        plan_file=str(plan),
        target_commit="a" * 40,
        target_repo="github.com/example/foo",
    )

    output_path = notes / "inbox" / first.name
    assert not first.exists()
    assert not (notes / "hold" / first.name).exists()
    assert not (notes / "processing" / first.name).exists()
    assert second.exists()
    assert output_path.is_file()
    parsed = frontmatter_parser.parse_frontmatter(output_path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["source"] == "plan-and-add-awi"
    assert data["plan_file"] == str(plan)
    assert data["target_commit"] == "a" * 40
    assert data["depends_on"] == ["external-a.md", "external-b.md"]
    assert "統合本文" in body
    assert details["plan_file"] == str(plan)
    assert len(commit_calls) == 1
    assert commit_calls[0][2] == [
        "hold/20260827-000000-001.md",
        "inbox/20260827-000000-001.md",
    ]


@pytest.mark.parametrize(
    ("uwi_name", "uwi_state"),
    [
        ("20260826-235959-001.md", "processing"),
        ("20260827-000001-001.md", "inbox"),
    ],
)
def test_edit_entry_to_plan_allows_active_uwi_materials_outside_awi_set(
    uwi_name: str,
    uwi_state: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前後にあるactive UWIを検証し、最古選択と依存統合から除外する。"""
    notes = _setup_notes(tmp_path)
    awi_names = ("20260827-000000-001.md", "20260827-000000-002.md")
    for index, name in enumerate(awi_names):
        source = _write_awi_file(notes, name)
        source.replace(notes / "hold" / name)
        path = notes / "hold" / name
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: awi\n",
                f"type: awi\ndepends_on: [{uwi_name}, external-{index}.md]\n",
            ),
            encoding="utf-8",
        )
    uwi_path = _write_uwi_entry(notes, uwi_name)
    if uwi_state == "processing":
        (notes / "processing").mkdir(exist_ok=True)
        uwi_path.replace(notes / "processing" / uwi_name)
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        "## 提示素材\n\n" + "".join(f"- {name}\n" for name in (*awi_names, uwi_name)),
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)

    mutations.edit_entry_to_plan(
        notes,
        filename=awi_names[0],
        content="統合本文",
        plan_file=str(plan),
        target_commit="a" * 40,
        target_repo="github.com/example/foo",
    )

    output = notes / "inbox" / awi_names[0]
    parsed = frontmatter_parser.parse_frontmatter(output.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == [uwi_name, "external-0.md", "external-1.md"]
    assert (notes / uwi_state / uwi_name).is_file()
    assert not (notes / "processing" / awi_names[0]).exists()


@pytest.mark.parametrize("case", ["empty", "outside", "not_oldest", "mixed_state"])
def test_edit_entry_to_plan_rejects_invalid_material_set_without_changes(
    case: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提示素材の欠落、対象外、非最古及び状態混在は変換前に一括拒否する。"""
    notes = _setup_notes(tmp_path)
    (notes / "processing").mkdir()
    names = ("20260827-000000-001.md", "20260827-000000-002.md")
    for name in names:
        source = _write_awi_file(notes, name)
        source.replace(notes / "hold" / name)
    materials = names
    filename = names[0]
    if case == "empty":
        materials = ()
    elif case == "outside":
        materials = (names[1],)
    elif case == "not_oldest":
        filename = names[1]
    elif case == "mixed_state":
        (notes / "hold" / names[1]).replace(notes / "processing" / names[1])
    plan = tmp_path / "main-plan.md"
    rows = "".join(f"- {name}\n" for name in materials)
    plan.write_text(f"## 提示素材\n\n{rows}", encoding="utf-8")
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))
    before = {
        path.relative_to(notes): path.read_text(encoding="utf-8")
        for state in ("hold", "processing")
        for path in (notes / state).iterdir()
    }

    with pytest.raises(mutations.WebInputError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    after = {
        path.relative_to(notes): path.read_text(encoding="utf-8")
        for state in ("hold", "processing")
        for path in (notes / state).iterdir()
    }
    assert after == before
    assert not commit_calls


class TestStartProcessingMissing:
    """start-processingサブコマンド: 存在しないファイル指定でexit 2となる。"""

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
            atk.main(["wi", "start-processing", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inboxに存在しません" in captured.err


class TestRejectFromProcessing:
    """reject: processing配下のファイルもrejected/へ移動できる。"""

    def test_reject_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがreject対象に含まれrejected/へ移動する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "rejected" / "fb-p.md").exists()


def _replace_edit_message_with_body_file(command: list[str], tmp_path: pathlib.Path) -> list[str]:
    """editの位置引数本文を現行の本文ファイル入力へ置き換える。"""
    if command[1] != "edit":
        return command
    body_file = tmp_path / "body.md"
    body_file.write_text(command[3], encoding="utf-8")
    return [*command[:3], "--body-file", str(body_file)]


class TestTargetRepoVerification:
    """mutation系サブコマンド: `--target-repo`指定時のfrontmatter一致検証を検証する。

    既定のfrontmatter`target_repo`は`github.com/example/foo`（`_write_awi_file`既定値）。
    """

    @pytest.mark.parametrize(
        "command",
        [
            ["wi", "edit", "fb-001.md", "編集後"],
            ["wi", "adopt", "fb-001.md"],
            ["wi", "reject", "fb-001.md"],
            ["wi", "start-processing", "fb-001.md"],
            ["wi", "rm", "fb-001.md"],
        ],
        ids=["edit", "adopt", "reject", "start-processing", "remove"],
    )
    @pytest.mark.parametrize("target_repo_kind", ["legacy-path", "canonical-url"])
    def test_active_entry_with_legacy_target_repo_accepts_equivalent_target(
        self,
        command: list[str],
        target_repo_kind: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """旧パス形の保存値は対応するパス形・URL形のどちらとも一致する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        _write_awi_file(notes, "fb-001.md", target_repo=str(local_repo), body="編集前")
        target_repo = {
            "legacy-path": str(local_repo),
            "canonical-url": "github.com/example/repo",
        }[target_repo_kind]
        git_calls: list[_GitCall] = []
        fallback = _make_subprocess_fake(git_calls)

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        command = _replace_edit_message_with_body_file(command, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            atk.main([*command, "--target-repo", target_repo], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0

    @pytest.mark.parametrize("target_repo_kind", ["legacy-path", "canonical-url"])
    def test_processing_entry_with_legacy_target_repo_accepts_equivalent_target_on_return(
        self,
        target_repo_kind: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """return-to-inboxも旧パス形の保存値をパス形・URL形で検証できる。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        path = _write_awi_file(notes, "fb-001.md", target_repo=str(local_repo))
        processing = notes / "processing"
        processing.mkdir()
        path.replace(processing / path.name)
        target_repo = {
            "legacy-path": str(local_repo),
            "canonical-url": "github.com/example/repo",
        }[target_repo_kind]
        fallback = _make_subprocess_fake([])

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "return-to-inbox", "fb-001.md", "--target-repo", target_repo],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0

    @pytest.mark.parametrize(
        "command",
        [
            ["wi", "edit", "fb-001.md", "編集後"],
            ["wi", "adopt", "fb-001.md"],
            ["wi", "reject", "fb-001.md"],
            ["wi", "start-processing", "fb-001.md"],
            ["wi", "rm", "fb-001.md"],
        ],
        ids=["edit", "adopt", "reject", "start-processing", "remove"],
    )
    def test_active_entry_with_legacy_target_repo_rejects_different_target(
        self,
        command: list[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """旧パス形の保存値は別リポジトリのURL形と一致しない。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        _write_awi_file(notes, "fb-001.md", target_repo=str(local_repo), body="編集前")
        fallback = _make_subprocess_fake([])

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        command = _replace_edit_message_with_body_file(command, tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            atk.main([*command, "--target-repo", "github.com/example/other"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err

    def test_processing_entry_with_legacy_target_repo_rejects_different_target_on_return(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """return-to-inboxも旧パス形の保存値を別リポジトリとは判定しない。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "target-repo"
        local_repo.mkdir()
        path = _write_awi_file(notes, "fb-001.md", target_repo=str(local_repo))
        processing = notes / "processing"
        processing.mkdir()
        path.replace(processing / path.name)
        fallback = _make_subprocess_fake([])

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/repo.git\n", "")
            return fallback(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "return-to-inbox", "fb-001.md", "--target-repo", "github.com/example/other"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err

    def test_unresolvable_saved_target_repo_exits_2_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """解決不能な保存値は不一致としてTraceback無しのexit 2で拒否する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo=str(tmp_path / "missing-repo"))
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", "fb-001.md", "--target-repo", "github.com/example/repo"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "target_repo不一致" in captured.err
        assert "Traceback" not in captured.err

    @pytest.mark.parametrize(
        "command",
        [
            ["wi", "adopt", "fb-dup.md"],
            ["wi", "reject", "fb-dup.md"],
            ["wi", "rm", "--force", "fb-dup.md"],
        ],
        ids=["adopt", "reject", "remove"],
    )
    def test_resolved_processing_entry_is_verified_when_same_name_exists_in_inbox(
        self,
        command: list[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同名併存時は実際の操作対象であるprocessing側を検証する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-dup.md", target_repo="github.com/example/foo")
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntarget_repo: github.com/example/other\ntype: awi\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main([*command, "--target-repo", "github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err
        assert processing_path.exists()

    def test_edit_verifies_resolved_processing_entry_when_same_name_exists_in_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """editの事前検査も解決済みprocessing実体へ適用する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-dup.md", target_repo="github.com/example/foo")
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntarget_repo: github.com/example/other\ntype: awi\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "edit", "fb-dup.md", "--target-repo", "github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert "target_repo不一致" in capsys.readouterr().err
        assert not editor_calls

    def test_adopt_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """adopt: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "target_repo不一致" in captured.err
        assert (notes / "inbox" / "fb-001.md").exists()
        assert not (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: `--target-repo`一致時は通常通りadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md", "--target-repo", "github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_reject_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """reject: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_start_processing_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "start-processing", "fb-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_unspecified_target_repo_is_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--target-repo`未指定時は検証されず既存挙動のまま処理が進む。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_bare_stem_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: 拡張子.md省略入力でも`--target-repo`不一致時に検証が回避されない（回帰確認）。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001", "--target-repo", "github.com/other/repo"], home=tmp_path)
        assert exc_info.value.code == 2
        assert not (notes / "adopted" / "fb-001.md").exists()
