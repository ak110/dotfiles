"""WIのmutations transitions: bulkのテスト。"""

import io
import pathlib
import sys

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import TRANSITION_EXPLICIT_STATES, bulk_source_states
from agent_toolkit._atk.wi.layout import entry_state_of
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git
from agent_toolkit._testing.wi_mutations_transitions_support import (
    _BulkTtyInput,
    _patch_bulk_git,
    _write_bulk_entry,
    _write_bulk_uwi,
)
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


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
    assert target.is_file()
    assert entry_state_of(target) == destination
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

    monkeypatch.setattr(_wi_sync, "pull", edit_after_confirmation)

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
