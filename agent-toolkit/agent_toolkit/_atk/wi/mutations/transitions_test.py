"""WIのmutations transitions: baseのテスト。"""

import contextlib
import datetime
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._atk.wi.constants import BULK_SOURCE_STATES
from agent_toolkit._atk.wi.layout import entry_state_of
from agent_toolkit._atk.wi.mutations import dependencies as mutation_dependencies
from agent_toolkit._atk.wi.mutations import transitions as mutation_transitions
from agent_toolkit._testing.wi_entry_files import entry_path as _test_entry_path
from agent_toolkit._testing.wi_mutations_support import (
    _disable_convert_git,
    _disable_transition_git,
    _write_convert_awi,
    _write_convert_plan,
)
from agent_toolkit._testing.wi_mutations_transitions_support import _TERMINAL_DIRECTORIES, _place_awi, _set_state
from agent_toolkit.atk_test import _FIXED_DT, _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file


def test_flat_awi_operations_are_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数遷移が固定パスを保ち、戻り値と保存状態を一貫して反映する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    filenames = mutation_transitions.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert _test_entry_path(notes, "entry.md").is_file()
    assert entry_state_of(_test_entry_path(notes, "entry.md")) == "processing"


@pytest.mark.parametrize("terminal_state", ["adopted", "rejected"])
@pytest.mark.parametrize("action", ["return-to-inbox", "hold"])
def test_agent_reopens_terminal_entry_without_old_result(
    terminal_state: str,
    action: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """誤終了した項目をCLIから再開し、旧結果を次の処理へ持ち込まない。"""
    notes = _setup_notes(tmp_path)
    source = _write_awi_file(notes, "entry.md")
    source.write_text(source.read_text(encoding="utf-8") + "\n## 処理結果\n\n- 誤った終了\n", encoding="utf-8")
    terminal = _set_state(source, terminal_state)
    monkeypatch.setenv("AI_AGENT", "1")
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as first:
        atk.main(["wi", action, source.name, f"--state={terminal_state}"], home=tmp_path)
    assert first.value.code == 0
    destination = _test_entry_path(notes, source.name)
    assert destination.is_file()
    assert destination == source == terminal
    assert entry_state_of(destination) == ("hold" if action == "hold" else "inbox")
    assert "## 処理結果" not in destination.read_text(encoding="utf-8")

    if action == "hold":
        with pytest.raises(SystemExit) as second:
            atk.main(["wi", "unhold", source.name], home=tmp_path)
        assert second.value.code == 0
        assert _test_entry_path(notes, source.name).is_file()
        assert entry_state_of(source) == "inbox"


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
    """ファイル名だけの`adopt`・`reject`が、`--all`の候補となる全状態から終了できる。

    個別指定と`--all`で到達できる遷移元が一致することを固定し、状態を片方にだけ加える変更を検出する。
    """
    notes = _setup_notes(tmp_path)
    source = _place_awi(notes, "entry.md", state)
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", action, "entry.md", "--note=終了する"], home=tmp_path)

    assert exc_info.value.code == 0
    destination = _test_entry_path(notes, "entry.md")
    assert destination.is_file()
    assert source == destination
    assert entry_state_of(destination) == _TERMINAL_DIRECTORIES[action]
    assert "## 処理結果" in destination.read_text(encoding="utf-8")


@pytest.mark.parametrize("action", ["adopt", "reject"])
def test_legacy_duplicate_names_stop_before_terminal_transition(
    action: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧配置の同名項目が固定パスへ衝突する場合は両方の本文と状態を保持する。"""
    notes = _setup_notes(tmp_path)
    processing = _place_awi(notes, "entry.md", "processing", body="処理中の本文", legacy_layout=True)
    held = _place_awi(notes, "entry.md", "hold", body="保留中の本文", legacy_layout=True)
    _disable_transition_git(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", action, "entry.md"], home=tmp_path)

    assert exc_info.value.code == 2
    assert processing.is_file()
    assert held.is_file()
    assert entry_state_of(processing) == "processing"
    assert entry_state_of(held) == "hold"
    assert "処理中の本文" in processing.read_text(encoding="utf-8")
    assert "保留中の本文" in held.read_text(encoding="utf-8")


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
    assert entry_state_of(inbox_entry) == "inbox"
    assert entry_state_of(held_entry) == "hold"


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
    target = _set_state(source, state)
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
    target = _set_state(source, state)
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
    target = _set_state(source, state)
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


def test_transition_commits_fixed_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態遷移は変更した固定パスだけをcommit対象にする。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)

    def assert_state_paths_exist(_notes: pathlib.Path, _message: str, paths: list[str], **_kwargs: object) -> None:
        assert paths == ["2026/10/entry.md"]
        assert all((_notes / path).is_file() for path in paths)

    monkeypatch.setattr(_wi_sync, "commit_and_push", assert_state_paths_exist)

    mutation_transitions.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )

    assert _test_entry_path(notes, "entry.md").is_file()


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
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    def pull_with_competing_update(_path: pathlib.Path) -> None:
        first.write_text(
            first.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [second.md]\n"),
            encoding="utf-8",
        )

    monkeypatch.setattr(_wi_sync, "pull", pull_with_competing_update)

    with pytest.raises(_wi_web_input.WebInputError, match="循環"):
        mutation_dependencies.set_entry_dependencies(notes, filename="second.md", depends_on=("first.md",))


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
    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["first.md", "second.md"], now=now)

    mutation_transitions.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["first.md", "second.md"],
        now=now,
        cooldown_days=3,
    )

    for path in (first, second):
        assert "cooldown_until: '2024-01-18T01:30:00+00:00'" in path.read_text(encoding="utf-8")
    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["first.md"], now=now)
    assert "cooldown_until" not in _test_entry_path(notes, "first.md").read_text(encoding="utf-8")


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
    original = frontmatter_parser.parse_frontmatter(_test_entry_path(notes, "processing.md").read_text(encoding="utf-8"))

    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    mutation_transitions.transition_entries(notes, action="hold", filenames=["processing.md"], now=_FIXED_DT)

    held = frontmatter_parser.parse_frontmatter(_test_entry_path(notes, "processing.md").read_text(encoding="utf-8"))
    assert original is not None and held is not None
    assert held[0] == {**original[0], "state": "hold"}


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
        adopted_text = _test_entry_path(notes, "fb-001.md").read_text(encoding="utf-8")
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
    """完了報告用に終了状態の件数と絶対パスを出力する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "entry.md")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", action, "entry.md"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.splitlines() == [summary, str(_test_entry_path(notes, "entry.md"))]
    assert entry_state_of(_test_entry_path(notes, "entry.md")) == destination


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
        content = _test_entry_path(notes, "inbox.md").read_text(encoding="utf-8")
        assert "保持する本文" in content
        assert "- メモ: 計画作成へ移管" in content

    def test_processing_entry_stops_without_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """pull後にprocessingへ移った対象は終了しない。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "2026" / "10"
        processing.mkdir(parents=True, exist_ok=True)
        path = processing / "processing.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\nstate: processing\n---\n\n処理中本文\n",
            encoding="utf-8",
        )
        original = path.read_text(encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "reject", "processing.md", "--if-inbox"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        assert path.read_text(encoding="utf-8") == original
        assert entry_state_of(_test_entry_path(notes, "processing.md")) == "processing"
        assert not any("commit" in call["cmd"] for call in git_calls)

    def test_mixed_states_stop_the_whole_batch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数対象の一部がprocessingならinbox対象も変更しない。"""
        notes = _setup_notes(tmp_path)
        inbox = _write_awi_file(notes, "inbox.md", body="inbox本文")
        processing_dir = notes / "2026" / "10"
        processing_dir.mkdir(parents=True, exist_ok=True)
        processing = processing_dir / "processing.md"
        processing.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: awi\nstate: processing\n---\n\nprocessing本文\n",
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
        assert entry_state_of(inbox) == "inbox"
        assert entry_state_of(processing) == "processing"
        assert not any("commit" in call["cmd"] for call in git_calls)
