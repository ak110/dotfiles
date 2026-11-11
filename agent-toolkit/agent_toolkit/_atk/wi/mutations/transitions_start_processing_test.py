"""WIのmutations transitions: start_processingのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi.constants import BULK_SOURCE_STATES, bulk_source_states
from agent_toolkit._atk.wi.layout import entry_state_of
from agent_toolkit._atk.wi.mutations import transitions as mutation_transitions
from agent_toolkit._testing.wi_entry_files import entry_path as _test_entry_path
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git
from agent_toolkit._testing.wi_mutations_transitions_support import _patch_bulk_git, _set_agent_environment, _write_bulk_entry
from agent_toolkit.atk_test import _FIXED_DT, _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file


class TestStartProcessingMultiple:
    """start-processingサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_uwi_moves_to_processing_and_can_be_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """UWIをprocessingへ移し、続けてadoptで終了できること。"""
        notes = _setup_notes(tmp_path)
        filename = "uwi-001.md"
        _test_entry_path(notes, filename).write_text(
            "---\ntype: uwi\nstate: inbox\ntarget_repo: github.com/example/foo\n---\n\n確認事項\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as start_result:
            atk.main(["wi", "start-processing", filename], home=tmp_path)

        assert start_result.value.code == 0
        assert _test_entry_path(notes, filename).exists()
        assert entry_state_of(_test_entry_path(notes, filename)) == "processing"

        with pytest.raises(SystemExit) as adopt_result:
            atk.main(["wi", "adopt", filename], home=tmp_path)

        assert adopt_result.value.code == 0
        assert _test_entry_path(notes, filename).exists()
        assert entry_state_of(_test_entry_path(notes, filename)) == "adopted"

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
        processing = notes / "2026" / "10"
        assert (processing / "fb-001.md").exists()
        assert (processing / "fb-002.md").exists()
        assert entry_state_of(processing / "fb-001.md") == "processing"
        assert entry_state_of(processing / "fb-002.md") == "processing"
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
        assert _test_entry_path(notes, "fb-001.md").exists()
        assert _test_entry_path(notes, "fb-002.md").exists()
        assert entry_state_of(_test_entry_path(notes, "fb-001.md")) == "inbox"
        assert entry_state_of(_test_entry_path(notes, "fb-002.md")) == "inbox"

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
        assert not _test_entry_path(notes, "foo.md").exists()
        assert not _test_entry_path(notes, "bar.md").exists()

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
        assert _test_entry_path(notes, "fb-001.md").exists()
        assert entry_state_of(_test_entry_path(notes, "fb-001.md")) == "inbox"

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
        _test_entry_path(notes, "fb-002.md").write_text(
            f"---\nstate: inbox\n{invalid_frontmatter}\n---\n\n本文\n",
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
        assert _test_entry_path(notes, "fb-001.md").exists()
        assert _test_entry_path(notes, "fb-002.md").exists()
        assert entry_state_of(_test_entry_path(notes, "fb-001.md")) == "inbox"
        assert _test_entry_path(notes, "fb-002.md").read_text(encoding="utf-8") == (
            f"---\nstate: inbox\n{invalid_frontmatter}\n---\n\n本文\n"
        )


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
    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    _set_agent_environment(monkeypatch, agent=True)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "processing.md"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code != 0
    assert _test_entry_path(notes, "processing.md").is_file()
    assert entry_state_of(_test_entry_path(notes, "processing.md")) == "processing"
    err = capsys.readouterr().err
    assert "失敗: " in err
    assert "`atk wi edit --append`" in err
    assert "`agent-toolkit:wi-standards`「状態と依存」" in err
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
    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    _set_agent_environment(monkeypatch, agent=True)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "--state=processing", "processing.md"], home=tmp_path, now=_FIXED_DT)
    assert exc_info.value.code == 0
    assert _test_entry_path(notes, "processing.md").is_file()
    assert entry_state_of(_test_entry_path(notes, "processing.md")) == "hold"

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "inbox.md"], home=tmp_path, now=_FIXED_DT)
    assert exc_info.value.code == 0
    assert _test_entry_path(notes, "inbox.md").is_file()
    assert entry_state_of(_test_entry_path(notes, "inbox.md")) == "hold"


def test_user_hold_keeps_implicit_processing_resolution(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """エージェント環境以外の`atk wi hold`は、従来どおり`processing`の項目を`--state`なしで保留する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "processing.md")
    _disable_transition_git(monkeypatch)
    mutation_transitions.transition_entries(notes, action="start-processing", filenames=["processing.md"], now=_FIXED_DT)
    _set_agent_environment(monkeypatch, agent=False)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "hold", "processing.md"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    assert _test_entry_path(notes, "processing.md").is_file()
    assert entry_state_of(_test_entry_path(notes, "processing.md")) == "hold"


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
    assert entry_state_of(inbox_entry) == "hold"
    assert _test_entry_path(notes, "inbox.md").is_file()
    assert processing_entry.is_file()
    assert entry_state_of(processing_entry) == "processing"
    assert bulk_source_states("hold", actor_is_agent=True) == ("inbox", "rejected", "adopted")
    assert bulk_source_states("hold", actor_is_agent=False) == BULK_SOURCE_STATES["hold"]
