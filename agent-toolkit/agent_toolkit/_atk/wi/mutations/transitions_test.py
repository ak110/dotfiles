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
import io
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
from agent_toolkit._atk.wi import bulk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi.constants import (  # noqa: E402  # pylint: disable=wrong-import-position
    BULK_ACTION_LABELS,
    BULK_SOURCE_STATES,
    TRANSITION_EXPLICIT_STATES,
    bulk_source_states,
)
from agent_toolkit.atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
)  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = "失敗: " + user_comment.AGENT_USER_COMMENT_EDIT_ERROR


from agent_toolkit._atk.wi.mutations.test_support_test import *  # noqa: F403


def _write_body_file(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """CLIへ渡す本文ファイルを作成する。"""
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    return body_file


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


@pytest.mark.parametrize("terminal_state", ["adopted", "rejected"])
@pytest.mark.parametrize("action", ["return-to-inbox", "hold"])
def test_agent_reopens_terminal_entry_without_old_result(
    terminal_state: str,
    action: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """誤終端した項目をCLIから再開し、旧結果を次の処理へ持ち込まない。"""
    notes = _setup_notes(tmp_path)
    source = _write_awi_file(notes, "entry.md")
    source.write_text(source.read_text(encoding="utf-8") + "\n## 処理結果\n\n- 誤った終端\n", encoding="utf-8")
    terminal = notes / terminal_state / source.name
    terminal.parent.mkdir(exist_ok=True)
    source.replace(terminal)
    monkeypatch.setenv("AI_AGENT", "1")
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as first:
        atk.main(["wi", action, source.name, f"--state={terminal_state}"], home=tmp_path)
    assert first.value.code == 0
    destination = notes / ("hold" if action == "hold" else "inbox") / source.name
    assert destination.is_file()
    assert "## 処理結果" not in destination.read_text(encoding="utf-8")

    if action == "hold":
        with pytest.raises(SystemExit) as second:
            atk.main(["wi", "unhold", source.name], home=tmp_path)
        assert second.value.code == 0
        assert (notes / "inbox" / source.name).is_file()


def _place_awi(notes: pathlib.Path, filename: str, state: str, body: str = "テスト本文") -> pathlib.Path:
    """AWIを指定状態のディレクトリへ置き、そのパスを返す。"""
    source = _write_awi_file(notes, filename, body=body)
    target = notes / state / filename
    if source != target:
        target.parent.mkdir(exist_ok=True)
        source.replace(target)
    return target


_TERMINAL_DIRECTORIES = {"adopt": "adopted", "reject": "rejected"}


@pytest.mark.parametrize(
    ("action", "state"),
    [(action, state) for action in ("adopt", "reject") for state in BULK_SOURCE_STATES[action]],
)
def test_filename_terminal_transition_reaches_every_bulk_source_state(
    action: str,
    state: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ファイル名だけの`adopt`・`reject`が、`--all`の候補となる全状態から終端できる。

    個別指定と`--all`で到達できる遷移元が一致することを固定し、状態を片方にだけ加える変更を検出する。
    """
    notes = _setup_notes(tmp_path)
    source = _place_awi(notes, "entry.md", state)
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", action, "entry.md", "--note=終端する"], home=tmp_path)

    assert exc_info.value.code == 0
    destination = notes / _TERMINAL_DIRECTORIES[action] / "entry.md"
    assert destination.is_file()
    assert not source.exists()
    assert "## 処理結果" in destination.read_text(encoding="utf-8")


@pytest.mark.parametrize("action", ["adopt", "reject"])
def test_filename_terminal_transition_prefers_processing_over_hold(
    action: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名がprocessingとholdにある場合はprocessing側を終端し、hold側を残す。"""
    notes = _setup_notes(tmp_path)
    processing = _place_awi(notes, "entry.md", "processing", body="処理中の本文")
    held = _place_awi(notes, "entry.md", "hold", body="保留中の本文")
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", action, "entry.md"], home=tmp_path)

    assert exc_info.value.code == 0
    assert not processing.exists()
    assert held.is_file()
    destination = notes / _TERMINAL_DIRECTORIES[action] / "entry.md"
    assert "処理中の本文" in destination.read_text(encoding="utf-8")


def test_reject_if_inbox_keeps_all_targets_when_hold_is_included(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`reject --if-inbox`はholdの対象を含むと終了コード2で全対象を元の状態に残す。"""
    notes = _setup_notes(tmp_path)
    inbox_entry = _place_awi(notes, "inbox-entry.md", "inbox")
    held_entry = _place_awi(notes, "held-entry.md", "hold")
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "reject", "inbox-entry.md", "held-entry.md", "--if-inbox"], home=tmp_path)

    assert exc_info.value.code == 2
    assert inbox_entry.is_file()
    assert held_entry.is_file()
    assert not (notes / "rejected").exists() or not any((notes / "rejected").iterdir())


def test_hold_does_not_resolve_held_entry(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """holdにある項目への`atk wi hold`は、adopt・rejectの解決変更後も受理しない。"""
    notes = _setup_notes(tmp_path)
    held = _place_awi(notes, "entry.md", "hold")
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "entry.md"], home=tmp_path)

    assert exc_info.value.code == 2
    assert held.is_file()


@pytest.mark.parametrize("state", ["processing", "inbox", "hold", "adopted", "rejected"])
@pytest.mark.parametrize("explicit_state", [False, True], ids=["implicit", "explicit"])
def test_user_can_remove_individual_entry_from_every_state(
    state: str,
    explicit_state: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """人間環境の個別削除は暗黙・明示指定とも全状態を対象にする。"""
    notes = _setup_notes(tmp_path)
    source = _write_awi_file(notes, "entry.md")
    target = notes / state / source.name
    if source != target:
        target.parent.mkdir(exist_ok=True)
        source.replace(target)
    _disable_transition_git(monkeypatch)
    args = ["wi", "rm", source.name]
    if state == "processing":
        args.append("--force")
    if explicit_state:
        args.append(f"--state={state}")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(args, home=tmp_path)

    assert exc_info.value.code == 0
    assert not target.exists()


@pytest.mark.parametrize("state", ["inbox", "hold"])
def test_agent_can_remove_individual_entry_from_allowed_state(
    state: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """エージェント環境の個別削除はinboxとholdを対象にする。"""
    notes = _setup_notes(tmp_path)
    source = _write_awi_file(notes, "entry.md")
    target = notes / state / source.name
    if source != target:
        source.replace(target)
    monkeypatch.setenv("AI_AGENT", "1")
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "rm", source.name], home=tmp_path)

    assert exc_info.value.code == 0
    assert not target.exists()


@pytest.mark.parametrize("state", ["processing", "adopted", "rejected"])
@pytest.mark.parametrize("explicit_state", [False, True], ids=["implicit", "explicit"])
def test_agent_rejects_individual_entry_from_forbidden_state(
    state: str,
    explicit_state: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境では禁止状態を状態名付きで拒否し、対象を保持する。"""
    notes = _setup_notes(tmp_path)
    source = _write_awi_file(notes, "entry.md")
    target = notes / state / source.name
    target.parent.mkdir(exist_ok=True)
    source.replace(target)
    monkeypatch.setenv("AI_AGENT", "1")
    _disable_transition_git(monkeypatch)
    args = ["wi", "rm", source.name]
    if explicit_state:
        args.append(f"--state={state}")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(args, home=tmp_path)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert f"state={state}" in err
    # 削除できない主体へ、ユーザーへの依頼と実行できる代替（不採用）を示すこと。
    next_actions = [line for line in err.splitlines() if line.startswith("次の操作: ")]
    assert len(next_actions) == 1
    assert "ユーザーへ依頼する" in next_actions[0]
    assert "atk wi reject" in next_actions[0]
    assert target.is_file()


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


@pytest.mark.parametrize(
    "dependency_args",
    [(), ("--depends-on", "replacement", "--depends-on", "replacement.md")],
)
def test_convert_to_plan_cli_rejects_removed_command_without_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_args: tuple[str, ...],
) -> None:
    """削除済み変換CLIを依存引数の有無にかかわらず拒否する。"""
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

    assert captured.value.code == 2
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == ["predecessor.md"]
    assert "queue_schedule" in parsed[0]


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


def test_hold_does_not_record_processing_origin(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """processingからの保留でも保留前の状態をfrontmatterへ残さない。

    holdの項目はinboxと同じ条件で扱うため、保留前の状態を読む処理を持たない。
    """
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "processing.md")
    _disable_transition_git(monkeypatch)
    original = frontmatter_parser.parse_frontmatter((notes / "inbox/processing.md").read_text(encoding="utf-8"))

    mutations.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    mutations.transition_entries(notes, action="hold", filenames=["processing.md"], now=_FIXED_DT)

    held = frontmatter_parser.parse_frontmatter((notes / "hold/processing.md").read_text(encoding="utf-8"))
    assert original is not None and held is not None
    assert held[0] == original[0]


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
    assert "processingに存在しない" in captured.err


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
        assert "- 対応commit作成者日時: " not in adopted_text
        assert "- 対応commit件名: " not in adopted_text
        assert "- メモ: " not in adopted_text


@pytest.mark.parametrize(
    ("action", "destination", "summary"),
    (
        ("adopt", "adopted", "成功: 1件をadoptedへ移した"),
        ("reject", "rejected", "成功: 1件をrejectedへ移した"),
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
    """rejectのinbox状態前提を公開CLIを呼び出して検証する。"""

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


def test_agent_environment_rejects_removed_plan_edit_without_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """削除済み計画型編集を拒否し、ユーザーコメント節を含む項目を保持する。"""
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
    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_plan_args(tmp_path, filename, "編集後", plan), home=tmp_path)

    assert exc_info.value.code == 2
    assert held_path.read_text(encoding="utf-8").endswith("編集前\n\n## ユーザーコメント\n\n保持する\n")
    assert not (notes / "inbox" / filename).exists()


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
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "--body-file", str(body_file)], home=tmp_path)

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
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "--body-file", str(body_file)], home=tmp_path)

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
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "uwi-001.md", "--body-file", str(body_file)], home=tmp_path)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "UWIには追記できません" in err
        next_actions = [line for line in err.splitlines() if line.startswith("次の操作: ")]
        assert len(next_actions) == 1
        assert "--appendを外して" in next_actions[0]
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
        body_file = _write_body_file(tmp_path, "追記本文")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "--append", "fb-001.md", "--body-file", str(body_file)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映していない" in captured.err
        assert "追記本文".encode() not in path.read_bytes()


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

    def test_individual_remove_accepts_multiple_target_repositories(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """個別削除は各項目がいずれかの指定リポジトリに属する場合に一括実行する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "foo.md", target_repo="github.com/example/foo")
        _write_awi_file(notes, "bar.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "rm",
                    "foo.md",
                    "bar.md",
                    "--target-repo=github.com/example/foo",
                    "--target-repo=github.com/example/bar",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert not (notes / "inbox/foo.md").exists()
        assert not (notes / "inbox/bar.md").exists()

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
    """編集した本文を保存する処理中に本文が改変された場合、一致判定は不一致と最初の差異位置を示す。"""
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
    body_file = _write_body_file(tmp_path, "編集後")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "edit", filename, "--body-file", str(body_file)], home=tmp_path)

    assert exc_info.value.code == 1
    position = captured["expected"].index("編集後") + 1
    error = capsys.readouterr().err
    assert f"最初の差異: {position}文字目" in error
    assert "送信元本文:" in error
    assert "保存本文:" in error


class _BulkTtyInput(io.StringIO):
    """対話端末として応答するテスト用標準入力。"""

    def isatty(self) -> bool:
        """対話端末であることを返す。"""
        return True


def _write_bulk_entry(
    notes: pathlib.Path,
    state: str,
    filename: str,
    *,
    target_repo: str = "github.com/example/foo",
) -> pathlib.Path:
    """一括操作の検証に使うエントリを指定状態へ書き込む。"""
    directory = notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(f"---\ntarget_repo: {target_repo}\ntype: awi\n---\n\nテスト本文\n", encoding="utf-8")
    return path


def _patch_bulk_git(monkeypatch: pytest.MonkeyPatch, commit_calls: list[str]) -> None:
    """一括操作が呼び出す2モジュールのgit操作を抑止し、commitメッセージを記録する。"""
    _disable_transition_git(monkeypatch)
    monkeypatch.setattr(bulk, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(bulk, "_pull", lambda _path: None)
    monkeypatch.setattr(
        mutations,
        "_commit_and_push",
        lambda _private_notes, message, _paths, **_kwargs: commit_calls.append(message),
    )


@pytest.mark.parametrize(
    ("action", "source_state", "destination", "summary"),
    [
        ("start-processing", "inbox", "processing", "成功: 1件をprocessingへ移した"),
        ("hold", "inbox", "hold", "成功: 1件をholdへ移した"),
        ("unhold", "hold", "inbox", "成功: 1件をholdからinboxへ戻した"),
        ("return-to-inbox", "processing", "inbox", "成功: 1件をinboxへ差し戻した"),
        ("adopt", "inbox", "adopted", "成功: 1件をadoptedへ移した"),
        ("reject", "inbox", "rejected", "成功: 1件をrejectedへ移した"),
    ],
)
def test_bulk_transition_applies_to_filtered_candidates(
    action: str,
    source_state: str,
    destination: str,
    summary: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--all`は対象リポジトリの候補だけを1回のcommitで遷移させ、完了メッセージを書く。"""
    notes = _setup_notes(tmp_path)
    target = _write_bulk_entry(notes, source_state, "target.md")
    other = _write_bulk_entry(notes, source_state, "other.md", target_repo="github.com/example/bar")
    commits: list[str] = []
    _patch_bulk_git(monkeypatch, commits)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", action, "--all", "--target-repo=github.com/example/foo", "--status=all", "--yes"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    assert not target.exists()
    assert (notes / destination / "target.md").is_file()
    assert other.exists()
    assert len(commits) == 1
    assert summary in capsys.readouterr().out


@pytest.mark.parametrize(
    ("action", "state"),
    [("unhold", "inbox"), ("start-processing", "adopted"), ("return-to-inbox", "inbox")],
)
def test_bulk_transition_excludes_entries_outside_source_states(
    action: str,
    state: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """遷移元状態集合に属さない項目は候補にせず、状態を変えずに終える。"""
    notes = _setup_notes(tmp_path)
    kept = _write_bulk_entry(notes, state, "kept.md")
    commits: list[str] = []
    _patch_bulk_git(monkeypatch, commits)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", action, "--all", "--target-repo=github.com/example/foo", "--status=all", "--yes"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    assert kept.is_file()
    assert not commits
    # 対象0件は接頭辞付きの成功行で、変更が無いことを示す。
    output = capsys.readouterr().out
    assert output.startswith("成功: 対象0件のため")
    assert "（変更は無い）" in output


def test_bulk_transition_requires_yes_in_non_interactive_environment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """非対話環境で`--yes`を省略した一括操作は終了コード2で拒否する。"""
    notes = _setup_notes(tmp_path)
    kept = _write_bulk_entry(notes, "inbox", "kept.md")
    commits: list[str] = []
    _patch_bulk_git(monkeypatch, commits)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "--all", "--target-repo=github.com/example/foo"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert kept.is_file()
    assert not commits
    assert "非対話環境で一括保留するには--yesを指定する" in capsys.readouterr().err


def test_bulk_transition_keeps_entries_when_confirmation_is_declined(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """確認を否定した一括操作は状態を変えずに終える。"""
    notes = _setup_notes(tmp_path)
    kept = _write_bulk_entry(notes, "inbox", "kept.md")
    commits: list[str] = []
    _patch_bulk_git(monkeypatch, commits)
    monkeypatch.setattr(sys, "stdin", _BulkTtyInput("n\n"))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "--all", "--target-repo=github.com/example/foo"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    assert kept.is_file()
    assert not commits
    captured = capsys.readouterr().out
    assert "上記1件を保留します" in captured
    assert "成功: 確認で中止したため保留しなかった（変更は無い）" in captured


def test_bulk_transition_skips_entries_changed_after_confirmation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """確認後に本文が変わった項目は対象から外し、ファイル名を報告する。"""
    notes = _setup_notes(tmp_path)
    changed = _write_bulk_entry(notes, "inbox", "changed.md")
    commits: list[str] = []
    _patch_bulk_git(monkeypatch, commits)

    def edit_after_confirmation(_path: pathlib.Path) -> None:
        changed.write_text(changed.read_text(encoding="utf-8") + "\n追記\n", encoding="utf-8")

    monkeypatch.setattr(bulk, "_pull", edit_after_confirmation)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "hold", "--all", "--target-repo=github.com/example/foo", "--yes"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    assert changed.is_file()
    assert not commits
    captured = capsys.readouterr().out
    # 確認後に変わった項目は警告行で名指し、確認と再実行の手段を次の操作で示す。
    assert "警告: 確認後に変更されたため保留しない: changed.md\n次の操作: " in captured
    assert "`atk wi show <ファイル名>`" in captured


@pytest.mark.parametrize(
    ("argv_tail", "message"),
    [
        (["--all", "fb-001.md", "--target-repo=github.com/example/foo"], "FILENAMEと--allは同時に指定できません。"),
        (["--all"], "--allの対象リポジトリを確定できません。"),
        (["--yes", "fb-001.md"], "--yesは--allとともに指定してください。"),
        (["--skip-pull", "fb-001.md"], "--skip-pullは--allとともに指定してください。"),
        (["--status=all", "fb-001.md"], "--type・--status・--answered・--sourceは--allとともに指定してください。"),
        ([], "対象のFILENAME、または--allを指定してください。"),
    ],
)
def test_bulk_transition_rejects_invalid_combinations(
    argv_tail: list[str],
    message: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """個別指定と一括指定の併用制約を公開CLIで引数を受け取る際に拒否する。"""
    _setup_notes(tmp_path)
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", *argv_tail], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def _write_bulk_uwi(notes: pathlib.Path, state: str, filename: str) -> pathlib.Path:
    """一括操作の検証に使うUWIを指定状態へ作成する。"""
    directory = notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: uwi\n---\n\n## 質問\n\n確認事項\n\n## 回答\n",
        encoding="utf-8",
    )
    return path


def test_bulk_cooldown_rejects_non_awi_like_individual_route(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--all`と`--cooldown-days`の組み合わせは、個別指定と同じ結果で非AWIを拒否する。

    `--cooldown-days`はAWI専用であり、候補にUWIが含まれる実行では、どのファイルへも
    `cooldown_until`を書き込まずに拒否する。個別指定した場合と同じ終了コードで終える。
    """
    notes = _setup_notes(tmp_path)
    awi_entry = _write_bulk_entry(notes, "processing", "target.md")
    uwi_entry = _write_bulk_uwi(notes, "processing", "question.md")
    commits: list[str] = []
    _patch_bulk_git(monkeypatch, commits)

    with pytest.raises(SystemExit) as individual:
        atk.main(
            ["wi", "return-to-inbox", "question.md", "--cooldown-days=3"],
            home=tmp_path,
            now=_FIXED_DT,
        )
    capsys.readouterr()

    with pytest.raises(SystemExit) as bulk_run:
        atk.main(
            [
                "wi",
                "return-to-inbox",
                "--all",
                "--target-repo=github.com/example/foo",
                "--status=all",
                "--yes",
                "--cooldown-days=3",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert bulk_run.value.code == individual.value.code
    assert "cooldown-days" in capsys.readouterr().err
    assert not commits
    assert awi_entry.is_file()
    assert uwi_entry.is_file()
    assert "cooldown_until" not in awi_entry.read_text(encoding="utf-8")
    assert "cooldown_until" not in uwi_entry.read_text(encoding="utf-8")


def test_bulk_source_states_cover_transition_explicit_states() -> None:
    """明示`state`として受理する状態が、その操作の遷移元状態集合に含まれる。

    `remove`は呼出主体で値が変わるため、明示`state`の受理範囲と一致する非エージェント環境の値で比較する。
    """
    for action, explicit_states in TRANSITION_EXPLICIT_STATES.items():
        source_states = bulk_source_states(action, actor_is_agent=False)
        assert set(explicit_states) <= set(source_states), action


def _set_agent_environment(monkeypatch: pytest.MonkeyPatch, *, agent: bool) -> None:
    """エージェント環境の判定に使う環境変数をそろえる。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")


def test_agent_hold_rejects_processing_entry_without_explicit_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境の`atk wi hold`は、別セッションが処理中の項目を`--state`なしで保留しない。

    保留を経由した本文置換は処理中のセッションへ届かず、`unhold`で`inbox`へ戻るため、移さずに非0で終え、
    処理中の項目の扱いと自セッションの保留に使う`--state=processing`を案内する。
    """
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "processing.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    _set_agent_environment(monkeypatch, agent=True)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "processing.md"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code != 0
    assert (notes / "processing/processing.md").is_file()
    assert not (notes / "hold/processing.md").exists()
    err = capsys.readouterr().err
    assert "失敗: " in err
    assert "`atk wi edit --append`" in err
    assert "`agent-toolkit:wi-standards`「由来と承認」" in err
    assert "`--state=processing`" in err


def test_agent_hold_moves_processing_entry_with_explicit_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自セッションの処理中の項目は、エージェント環境でも`--state=processing`で保留できる。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "processing.md")
    _write_awi_file(notes, "inbox.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    _set_agent_environment(monkeypatch, agent=True)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "--state=processing", "processing.md"], home=tmp_path, now=_FIXED_DT)
    assert exc_info.value.code == 0
    assert (notes / "hold/processing.md").is_file()

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "inbox.md"], home=tmp_path, now=_FIXED_DT)
    assert exc_info.value.code == 0
    assert (notes / "hold/inbox.md").is_file()


def test_user_hold_keeps_implicit_processing_resolution(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """エージェント環境以外の`atk wi hold`は、従来どおり`processing`の項目を`--state`なしで保留する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "processing.md")
    _disable_transition_git(monkeypatch)
    mutations.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    _set_agent_environment(monkeypatch, agent=False)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "processing.md"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    assert (notes / "hold/processing.md").is_file()


def test_agent_bulk_hold_excludes_processing_entries(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """エージェント環境の`atk wi hold --all`は`processing`の項目を候補に含めず移さない。"""
    notes = _setup_notes(tmp_path)
    inbox_entry = _write_bulk_entry(notes, "inbox", "inbox.md")
    processing_entry = _write_bulk_entry(notes, "processing", "processing.md")
    commits: list[str] = []
    _patch_bulk_git(monkeypatch, commits)
    _set_agent_environment(monkeypatch, agent=True)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "hold", "--all", "--target-repo=github.com/example/foo", "--yes"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    assert not inbox_entry.exists()
    assert (notes / "hold/inbox.md").is_file()
    assert processing_entry.is_file()
    assert bulk_source_states("hold", actor_is_agent=True) == ("inbox", "rejected", "adopted")
    assert bulk_source_states("hold", actor_is_agent=False) == BULK_SOURCE_STATES["hold"]
