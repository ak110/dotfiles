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


def test_flat_awi_operations_are_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数遷移が戻り値とファイル移動を一貫して反映する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    filenames = mutations.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "processing/entry.md").is_file()


def test_transition_restores_missing_state_directories_before_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任意状態ディレクトリが不在でも全状態をgit addできる状態へ戻す。"""
    notes = _setup_notes(tmp_path)
    (notes / "hold").rmdir()
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)

    def assert_state_paths_exist(_notes: pathlib.Path, _message: str, paths: list[str], **_kwargs: object) -> None:
        assert all((_notes / path).is_dir() for path in paths)

    monkeypatch.setattr(mutations, "_commit_and_push", assert_state_paths_exist)

    mutations.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )

    assert (notes / "processing/entry.md").is_file()


def test_read_plan_input_filenames_uses_related_wi_and_legacy_materials(tmp_path: pathlib.Path) -> None:
    """新書式は関連WIを読み、旧書式は提示素材へフォールバックする。"""
    read_plan_input_filenames = vars(mutations)["_read_plan_input_filenames"]
    filenames = ("20260827-000000-002.md", "20260827-000000-001.md")
    plan = _write_integration_plan(tmp_path, "a" * 40, filenames)
    assert read_plan_input_filenames(plan) == (tuple(sorted(filenames)), "計画メタ情報の関連WI")

    plan.write_text("## 提示素材\n\n" + "".join(f"- {name}\n" for name in filenames), encoding="utf-8")
    assert read_plan_input_filenames(plan) == (tuple(sorted(filenames)), "計画の提示素材")


def test_read_plan_input_filenames_rejects_invalid_related_wi(tmp_path: pathlib.Path) -> None:
    """関連WIの要約欠落を提示素材へフォールバックせず拒否する。"""
    read_plan_input_filenames = vars(mutations)["_read_plan_input_filenames"]
    filename = "20260827-000000-001.md"
    plan = _write_integration_plan(tmp_path, "a" * 40, (filename,))
    plan.write_text(plan.read_text(encoding="utf-8").replace(": 変換対象の要求", ":"), encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="計画メタ情報の関連WIが不正"):
        read_plan_input_filenames(plan)


@pytest.mark.parametrize("state", ["inbox", "processing"])
def test_convert_to_plan_replaces_legacy_schedule_with_top_level_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """変換が状態を問わず計画・依存をトップレベルへ正規化する。"""
    notes = _setup_notes(tmp_path)
    schedule_mapping = (
        "queue_schedule:\n"
        "  body_sha256: stale\n"
        "  normalized_target_repo: github.com/example/foo\n"
        "  type: normal\n"
        "  dependency:\n"
        "    kind: none\n"
        "  target_files: []\n"
        "  carry_count: 2\n"
        "  carry_reasons: [limit-exceeded, conflict]\n"
        "  last_deferral_run_id: run-1\n"
        "  last_deferral_reason: conflict\n"
    )
    path = _write_convert_awi(notes, "awi.md", state=state, schedule_mapping=schedule_mapping)
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    details = mutations.convert_entry_to_plan(
        notes,
        filename="awi.md",
        plan_file=str(plan),
        depends_on=("dependency.md", "dependency", "dependency.md"),
        target_repo="github.com/example/foo",
    )

    text = path.read_text(encoding="utf-8")
    parsed = frontmatter_parser.parse_frontmatter(text)
    assert parsed is not None
    data, _body = parsed
    assert data["plan_file"] == str(plan)
    assert data["depends_on"] == ["dependency.md"]
    assert "queue_schedule" not in data
    assert details["target_commit"] == "a" * 40
    assert details["depends_on"] == ["dependency.md"]


@pytest.mark.parametrize(
    ("dependency_args", "expected_dependencies"),
    [
        ((), ["predecessor.md"]),
        (("--depends-on", "replacement", "--depends-on", "replacement.md"), ["replacement.md"]),
    ],
)
def test_convert_to_plan_cli_distinguishes_omitted_and_explicit_dependencies(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_args: tuple[str, ...],
    expected_dependencies: list[str],
) -> None:
    """変換CLIは依存の省略時に既存値を保持し、明示時だけ置換する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(
        notes,
        "awi.md",
        schedule_mapping=(
            "queue_schedule:\n  dependency:\n    kind: external-user\n    condition: 回答後\n    uwi_filename: answer.md\n"
        ),
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [predecessor.md]\n"),
        encoding="utf-8",
    )
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

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
                *dependency_args,
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert captured.value.code == 0
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == expected_dependencies
    assert "queue_schedule" not in parsed[0]


@pytest.mark.parametrize(
    "legacy_dependency",
    [
        "    kind: external-user\n    condition: 回答後\n    uwi_filename: answer.md\n",
        "    kind: entries\n    filenames: []\n",
    ],
)
def test_convert_to_plan_rejects_unrepresentable_legacy_dependency_when_omitted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_dependency: str,
) -> None:
    """トップレベルへ意味を保って移行できない旧依存は非破壊で拒否する。"""
    notes = _setup_notes(tmp_path)
    path = _write_convert_awi(
        notes,
        "awi.md",
        schedule_mapping=f"queue_schedule:\n  dependency:\n{legacy_dependency}",
    )
    original = path.read_text(encoding="utf-8")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)

    with pytest.raises(mutations.WebInputError, match="旧形式の依存"):
        mutations.convert_entry_to_plan(notes, filename="awi.md", plan_file=str(plan))

    assert path.read_text(encoding="utf-8") == original


def test_set_dependencies_uses_graph_refreshed_after_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pullで競合更新された依存を読んでから新しい閉路を拒否する。"""
    notes = _setup_notes(tmp_path)
    first = _write_convert_awi(notes, "first.md")
    _write_convert_awi(notes, "second.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)

    def pull_with_competing_update(_path: pathlib.Path) -> None:
        first.write_text(
            first.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [second.md]\n"),
            encoding="utf-8",
        )

    monkeypatch.setattr(mutations, "_pull", pull_with_competing_update)

    with pytest.raises(mutations.WebInputError, match="循環"):
        mutations.set_entry_dependencies(notes, filename="second.md", depends_on=("first.md",))


def test_convert_to_plan_pushes_pending_commit_before_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再実行時の未送信commitをff-only pullより先に同期する。"""
    notes = _setup_notes(tmp_path)
    _write_convert_awi(notes, "awi.md")
    plan = _write_convert_plan(tmp_path, "a" * 40)
    _disable_convert_git(monkeypatch)
    events: list[str] = []
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: events.append("push"))
    monkeypatch.setattr(mutations, "_pull", lambda _path: events.append("pull"))
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)

    mutations.convert_entry_to_plan(notes, filename="awi.md", plan_file=str(plan))

    assert events[:2] == ["push", "pull"]


def test_convert_held_entries_integrates_all_materials_into_oldest_inbox_item(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """holdの全awiを最古項目へ統合し、UWIと外部依存を保持する。"""
    notes = _setup_notes(tmp_path)
    awi_names = ("20260827-000000-001.md", "20260827-000000-002.md")
    paths = [_write_convert_awi(notes, name, state="hold") for name in awi_names]
    uwi_name = "20260826-235959-001.md"
    _write_uwi_entry(notes, uwi_name)
    paths[0].write_text(
        paths[0]
        .read_text(encoding="utf-8")
        .replace(
            "type: awi\n",
            f"type: awi\ndepends_on: [{uwi_name}, external-a.md]\n",
        ),
        encoding="utf-8",
    )
    paths[1].write_text(
        paths[1]
        .read_text(encoding="utf-8")
        .replace(
            "type: awi\n",
            "type: awi\nqueue_schedule:\n  dependency:\n    kind: entries\n    filenames: [external-b.md]\n",
        ),
        encoding="utf-8",
    )
    plan_relative = pathlib.PurePosixPath("plans/2026/08/plan.md")
    plan = _write_integration_plan(notes / plan_relative.parent, "a" * 40, (*awi_names, uwi_name))
    _disable_convert_git(monkeypatch)
    _patch_integration_target_resolution(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    result = mutations.convert_entries_to_plan(
        notes,
        filenames=(awi_names[1], awi_names[0]),
        plan_file=str(plan),
        message="統合した計画本文",
        target_repo="github.com/example/foo",
        local_worktree=tmp_path / "target-worktree",
        skip_push=True,
    )

    output_path = notes / "inbox" / awi_names[0]
    assert not (notes / "hold" / awi_names[0]).exists()
    assert not (notes / "hold" / awi_names[1]).exists()
    assert output_path.is_file()
    assert (notes / "inbox" / uwi_name).is_file()
    parsed = frontmatter_parser.parse_frontmatter(output_path.read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["source"] == "plan-and-add-awi"
    assert data["plan_file"] == f"$(atk config get private_notes)/{plan_relative}"
    assert result["plan_file"] == data["plan_file"]
    assert data["target_commit"] == "b" * 40
    assert data["depends_on"] == [uwi_name, "external-a.md", "external-b.md"]
    assert "統合した計画本文" in body
    entries = result["entries"]
    assert isinstance(entries, list) and len(entries) == 1
    assert result["integrated"] is True
    assert len(commit_calls) == 1
    assert commit_calls[0][2] == (
        "hold/20260827-000000-001.md",
        "hold/20260827-000000-002.md",
        "inbox/20260827-000000-001.md",
    )


def test_conversion_restore_preserves_unrelated_index_change(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """commit前の復元後も対象外のstage済み差分をindexへ保持する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_convert_awi(notes, filename, state="hold")
    original = source.read_text(encoding="utf-8")
    unrelated = notes / "unrelated.txt"
    unrelated.write_text("before\n", encoding="utf-8")
    _initialize_private_notes_git(notes)
    unrelated.write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(notes), "add", unrelated.name], check=True)
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
    staged = subprocess.run(
        ["git", "-C", str(notes), "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert staged.stdout == "unrelated.txt\n"


def test_cooldown_return_sets_one_utc_deadline_and_start_clears_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数AWIへ同じUTC期限を設定し、再開時に期限を除去する。"""
    notes = _setup_notes(tmp_path)
    first = _write_awi_file(notes, "first.md")
    second = _write_awi_file(notes, "second.md")
    _disable_transition_git(monkeypatch)
    now = datetime.datetime(2024, 1, 15, 10, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    mutations.transition_entries(notes, action="start-processing", filenames=["first.md", "second.md"], now=now)

    mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["first.md", "second.md"],
        now=now,
        cooldown_days=3,
    )

    for path in (first, second):
        assert "cooldown_until: '2024-01-18T01:30:00+00:00'" in path.read_text(encoding="utf-8")
    mutations.transition_entries(notes, action="start-processing", filenames=["first.md"], now=now)
    assert "cooldown_until" not in (notes / "processing/first.md").read_text(encoding="utf-8")


def test_return_to_inbox_missing_file_reports_processing_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """return-to-inboxで未存在ファイルを指定するとprocessing側の状態名で案内する。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "return-to-inbox", "nonexistent.md"], home=tmp_path)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "processingに存在しません" in captured.err


class TestAdoptZeroArgs:
    """adoptサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt"], home=tmp_path)

        assert exc_info.value.code == 2


class TestAdoptStampWithoutOptional:
    """adopt: --note・--commit省略時も必須項目のみ追記される。"""

    def test_stamp_written_with_required_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """引数省略時、`## 処理結果`節に採否・処理日時のみ追記され、対応commit・メモ行は含まれない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: " not in adopted_text
        assert "- メモ: " not in adopted_text


@pytest.mark.parametrize(
    ("action", "destination", "summary"),
    (
        ("adopt", "adopted", "1件採用処理:"),
        ("reject", "rejected", "1件不採用処理:"),
    ),
)
def test_terminal_transition_prints_destination_path(
    action: str,
    destination: str,
    summary: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """完了報告用に終端状態の件数と絶対パスを出力する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", action, "entry.md"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.splitlines() == [summary, str(notes / destination / "entry.md")]


class TestRejectIfInbox:
    """rejectのinbox状態前提を公開CLI経路で検証する。"""

    def test_rejects_inbox_entry_and_preserves_note(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """対象がinboxなら本文を保持して処理理由を記録する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "inbox.md", body="保持する本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "reject", "inbox.md", "--if-inbox", "--note=計画作成へ移管"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = (notes / "rejected/inbox.md").read_text(encoding="utf-8")
        assert "保持する本文" in content
        assert "- メモ: 計画作成へ移管" in content

    def test_processing_entry_stops_without_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """pull後にprocessingへ移った対象は終端しない。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        path = processing / "processing.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n処理中本文\n",
            encoding="utf-8",
        )
        original = path.read_text(encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "processing.md", "--if-inbox"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert path.read_text(encoding="utf-8") == original
        assert not (notes / "rejected/processing.md").exists()
        assert not any("commit" in call["cmd"] for call in git_calls)

    def test_mixed_states_stop_the_whole_batch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数対象の一部がprocessingならinbox対象も変更しない。"""
        notes = _setup_notes(tmp_path)
        inbox = _write_awi_file(notes, "inbox.md", body="inbox本文")
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True, exist_ok=True)
        processing = processing_dir / "processing.md"
        processing.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        inbox_original = inbox.read_text(encoding="utf-8")
        processing_original = processing.read_text(encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "reject", "inbox.md", "processing.md", "--if-inbox"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert inbox.read_text(encoding="utf-8") == inbox_original
        assert processing.read_text(encoding="utf-8") == processing_original
        assert not any((notes / "rejected").glob("*.md"))
        assert not any("commit" in call["cmd"] for call in git_calls)


def test_agent_environment_plan_edit_preserves_saved_user_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """計画型編集でも保存済みユーザーコメント節を保持する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    path = _write_awi_file(notes, filename, body="編集前\n\n## ユーザーコメント\n\n保持する")
    held_path = notes / "hold" / filename
    path.replace(held_path)
    plan = tmp_path / "plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "resolve_add_target",
        lambda _value: ("github.com/example/foo", worktree),
    )
    monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")
    monkeypatch.setattr(mutations, "_resolve_plan_base_commit", lambda *_args: "a" * 40)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "edit",
                filename,
                "編集後",
                "--plan-file",
                str(plan),
                "--target-repo",
                "github.com/example/foo",
            ],
            home=tmp_path,
        )

    assert exc_info.value.code == 0
    saved = notes / "inbox" / filename
    assert saved.read_text(encoding="utf-8").endswith("編集後\n\n## ユーザーコメント\n\n保持する\n")


class TestEditNoChanges:
    """editサブコマンド: 差分なしでcommitせず終了。"""

    def test_edit_no_changes_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集後にファイル差分がなければコミットされず案内のみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestAppendEdit:
    """`edit --append`のraw bytes保持・競合・UWI拒否を検証する。"""

    def test_append_preserves_lf_bytes_and_adds_utf8_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """LF本文の元bytesを変更せず、区切りとUTF-8本文を末尾へ追加する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="本文")
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_bytes() == original + b"\n\n" + "追記本文".encode()

    def test_append_preserves_crlf_bytes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """CRLF本文の改行を変換せず、元bytesへ追記する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_bytes(b"---\r\ntarget_repo: github.com/example/foo\r\ntype: awi\r\n---\r\n\r\n" + "本文\r\n".encode())
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_bytes() == original + b"\n\n" + "追記本文".encode()

    @pytest.mark.parametrize("answer", ["", "既存回答"])
    def test_append_rejects_uwi_before_writing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        answer: str,
    ) -> None:
        """未回答・回答済みを問わずUWIへの追記を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_uwi_entry(notes, "uwi-001.md", answer=answer)
        original = path.read_bytes()
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "uwi-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 1
        assert "UWIには追記できません" in capsys.readouterr().err
        assert path.read_bytes() == original

    def test_append_expected_bytes_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """snapshot後の競合時は追記本文を反映しない。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_file(notes, "fb-001.md", body="本文")
        original_append = mutations.append_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: bytes,
            target_repo: str | None = None,
            lock_timeout: float = -1,
            expected_content: bytes | None = None,
            finalized_content: dict[str, str] | None = None,
        ) -> bool:
            path.write_bytes(path.read_bytes() + "競合側の変更".encode())
            return original_append(
                private_notes,
                state=state,
                filename=filename,
                content=content,
                target_repo=target_repo,
                lock_timeout=lock_timeout,
                expected_content=expected_content,
                finalized_content=finalized_content,
            )

        monkeypatch.setattr(mutations, "append_entry_content", conflict)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "追記本文"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映されていません" in captured.err
        assert "追記本文".encode() not in path.read_bytes()


def test_edit_entry_to_plan_commit_failure_leaves_inbox_without_processing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変換commitの失敗後も計画型項目をprocessingへ公開しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_awi_file(notes, filename)
    held = notes / "hold" / filename
    source.replace(held)
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    _disable_transition_git(monkeypatch)

    def fail_commit(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(mutations, "_commit_and_push", fail_commit)
    with pytest.raises(subprocess.CalledProcessError):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    assert not held.exists()
    assert (notes / "inbox" / filename).is_file()
    assert not (notes / "processing" / filename).exists()


def test_edit_entry_to_plan_rejects_content_conflict_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hold本文の内容競合時は変換先を作成しない。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_awi_file(notes, filename)
    held = notes / "hold" / filename
    source.replace(held)
    original = held.read_text(encoding="utf-8")
    plan = tmp_path / "main-plan.md"
    plan.write_text(f"## 提示素材\n\n- {filename}\n", encoding="utf-8")
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))

    with pytest.raises(RuntimeError, match="編集中に他プロセスが対象を変更しました"):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
            expected_content=original + "外部変更",
        )

    assert held.read_text(encoding="utf-8") == original
    assert not (notes / "inbox" / filename).exists()
    assert not (notes / "processing" / filename).exists()
    assert not commit_calls


@pytest.mark.parametrize("case", ["invalid_type", "missing_requirement_table", "duplicate_queue_id"])
def test_edit_entry_to_plan_rejects_invalid_structured_materials_before_changes(
    case: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正規パーサーが拒否する旧表形式をキュー項目の保存前に拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    source = _write_awi_file(notes, filename)
    held = notes / "hold" / filename
    source.replace(held)
    material_type = "不正種別" if case == "invalid_type" else "AWI"
    second_row = f"| P-002 | フィードバック | {filename} | test | 本文全文 |\n" if case == "duplicate_queue_id" else ""
    requirement_table = (
        ""
        if case == "missing_requirement_table"
        else (
            "\n| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            f"| R-P-001-001 | {'P-001, P-002' if second_row else 'P-001'} | 統合する。 | "
            "採用 | 統合 | 非該当 | 計画へ反映するため。 |\n"
        )
    )
    plan = tmp_path / "main-plan.md"
    plan.write_text(
        "## 提示素材\n\n"
        "| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| P-001 | {material_type} | {filename} | test | 本文全文 |\n"
        f"{second_row}{requirement_table}",
        encoding="utf-8",
    )
    _disable_transition_git(monkeypatch)
    commit_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *args, **_kwargs: commit_calls.append(args))
    original = held.read_text(encoding="utf-8")

    with pytest.raises(mutations.WebInputError, match="提示素材"):
        mutations.edit_entry_to_plan(
            notes,
            filename=filename,
            content="統合本文",
            plan_file=str(plan),
            target_commit="a" * 40,
            target_repo="github.com/example/foo",
        )

    assert held.read_text(encoding="utf-8") == original
    assert not (notes / "processing" / filename).exists()
    assert not commit_calls


class TestStartProcessingMultiple:
    """start-processingサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_uwi_moves_to_processing_and_can_be_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """UWIをprocessingへ移し、続けてadoptで終端できること。"""
        notes = _setup_notes(tmp_path)
        filename = "uwi-001.md"
        (notes / "inbox" / filename).write_text(
            "---\ntype: uwi\ntarget_repo: github.com/example/foo\n---\n\n確認事項\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as start_result:
            atk.main(["wi", "start-processing", filename], home=tmp_path)

        assert start_result.value.code == 0
        assert (notes / "processing" / filename).exists()

        with pytest.raises(SystemExit) as adopt_result:
            atk.main(["wi", "adopt", filename], home=tmp_path)

        assert adopt_result.value.code == 0
        assert (notes / "adopted" / filename).exists()

    def test_multiple_files_moved_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のstart-processingで両方がprocessing/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "start-processing", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        processing = notes / "processing"
        assert (processing / "fb-001.md").exists()
        assert (processing / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: start processing 2 entries" in commit_cmds[0]

    def test_mixed_target_repo_rejects_entire_batch_before_moving(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """集合内の1件でも`target_repo`が異なる場合は一括移動を開始しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "start-processing",
                    "fb-001.md",
                    "fb-002.md",
                    "--target-repo",
                    "github.com/example/foo",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "inbox" / "fb-002.md").exists()
        assert not (notes / "processing" / "fb-001.md").exists()
        assert not (notes / "processing" / "fb-002.md").exists()

    def test_missing_member_rejects_entire_batch_before_moving(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """集合内の1件でもinboxに存在しない場合は適合項目も移動しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "start-processing", "fb-001.md", "missing.md", "--target-repo", "github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()
        assert not (notes / "processing" / "fb-001.md").exists()

    @pytest.mark.parametrize(
        ("invalid_frontmatter", "target_args"),
        [
            (
                "type: [\ntarget_repo: github.com/example/foo",
                ["--target-repo", "github.com/example/foo"],
            ),
            (
                "target_repo: github.com/example/foo",
                ["--target-repo", "github.com/example/foo"],
            ),
            ("type: awi", []),
            (
                "type: unknown\ntarget_repo: github.com/example/foo",
                ["--target-repo", "github.com/example/foo"],
            ),
        ],
        ids=["malformed", "missing-type", "missing-target-repo", "invalid-type"],
    )
    def test_invalid_frontmatter_rejects_entire_batch_before_moving(
        self,
        invalid_frontmatter: str,
        target_args: list[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """集合内の1件で必須frontmatterが不正な場合は適合項目も移動しない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        (notes / "inbox" / "fb-002.md").write_text(
            f"---\n{invalid_frontmatter}\n---\n\n本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "start-processing",
                    "fb-001.md",
                    "fb-002.md",
                    *target_args,
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "inbox" / "fb-002.md").exists()
        assert not (notes / "processing" / "fb-001.md").exists()
        assert not (notes / "processing" / "fb-002.md").exists()


def test_cli_edit_reports_body_mismatch_when_saved_body_is_altered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """編集の保存経路で本文が改変された場合、一致判定は不一致と最初の差異位置を示す。"""
    notes = _setup_notes(tmp_path)
    filename = "20260827-000000-001.md"
    _write_awi_file(notes, filename, body="編集前")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    original_read = mutations._add._read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001
    captured: dict[str, str] = {}

    def read_after_alteration(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
        captured["expected"] = expected_body
        path.write_text(expected_body.replace("編集後", "改変後", 1), encoding="utf-8")
        return original_read(path, expected_body=expected_body)

    monkeypatch.setattr(
        mutations._add,  # pylint: disable=protected-access
        "_read_saved_entry_details",
        read_after_alteration,
    )

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "edit", filename, "編集後"], home=tmp_path)

    assert exc_info.value.code == 1
    position = captured["expected"].index("編集後") + 1
    error = capsys.readouterr().err
    assert f"最初の差異: {position}文字目" in error
    assert "送信元本文:" in error
    assert "保存本文:" in error
