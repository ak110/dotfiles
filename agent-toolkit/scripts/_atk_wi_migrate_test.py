"""`_atk_wi_migrate`の契約テスト。"""

import pathlib
import subprocess

import _atk_wi_common as _common
import _atk_wi_migrate
import pytest

_OWNER_SESSION = "wi-migrate-test-session"
"""テスト中に所有記録へ書かれるセッション識別子。"""


@pytest.fixture(autouse=True)
def _isolate_state_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIXとWindowsの状態ディレクトリ解決をテスト固有の場所へ隔離する。"""
    state_root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("LOCALAPPDATA", str(state_root))
    monkeypatch.setenv("APPDATA", str(state_root))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", _OWNER_SESSION)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


def _git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    """テスト用Gitコマンドを実行する。"""
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _init_notes(root: pathlib.Path, remote: pathlib.Path) -> None:
    """upstream付きprivate-notesを、廃止した状態のディレクトリも含めて初期化する。"""
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "wi-migrate-test")
    _git(root, "config", "user.email", "wi-migrate-test@example.invalid")
    _git(root, "config", "core.quotePath", "false")
    for state in ("inbox", "processing", "hold", "adopted", "rejected", "planning", "editing"):
        (root / state).mkdir()
        (root / state / ".gitkeep").touch()
    (root / "plans").mkdir()
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    _git(remote.parent, "init", "--bare", "--initial-branch=main", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "--set-upstream", "origin", "main")


def _commit_all(root: pathlib.Path) -> None:
    """作業ツリーの全変更をcommitしてpushする。"""
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "fixture")
    _git(root, "push")


def _legacy_entry(entry_type: str, body: str) -> str:
    """旧体系のキュー項目本文を組み立てる。"""
    return f"---\ntarget_repo: github.com/example/repo\ntype: {entry_type}\n---\n\n{body}"


def test_migrate_converts_type_values_and_moves_withdrawn_states(tmp_path: pathlib.Path) -> None:
    """旧体系のキュー項目を新体系のtype値へ変換し、廃止した状態の項目をholdへ移す。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "inbox" / "a.md").write_text(
        _legacy_entry("feedback", "atk mqのフィードバックをagent-toolkit:process-feedbacksで処理する。\n"),
        encoding="utf-8",
    )
    (notes / "planning" / "b.md").write_text(_legacy_entry("feedback", "計画型フィードバック。\n"), encoding="utf-8")
    (notes / "editing" / "c.md").write_text(_legacy_entry("tbd", "TBDの本文。\n"), encoding="utf-8")
    _commit_all(notes)

    result = _atk_wi_migrate.migrate_queue(notes)

    assert result["converted"] == 3
    assert result["moved"] == 2
    assert "type: awi" in (notes / "inbox" / "a.md").read_text(encoding="utf-8")
    assert "atk wiのAWIをagent-toolkit:process-wiで処理する。" in (notes / "inbox" / "a.md").read_text(encoding="utf-8")
    assert not (notes / "planning" / "b.md").exists()
    assert not (notes / "editing" / "c.md").exists()
    assert "計画型AWI。" in (notes / "hold" / "b.md").read_text(encoding="utf-8")
    assert "type: uwi" in (notes / "hold" / "c.md").read_text(encoding="utf-8")
    assert "UWIの本文。" in (notes / "hold" / "c.md").read_text(encoding="utf-8")
    assert _git(notes, "status", "--porcelain").stdout == ""


def test_migrate_preserves_user_sections_protected_tokens_and_code_blocks(tmp_path: pathlib.Path) -> None:
    """ユーザー記入欄・計画書式の判定文字列・逐語コードブロックを置換しない。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "inbox" / "a.md").write_text(
        _legacy_entry(
            "feedback",
            "本文のフィードバック。\n\n"
            "```text\nコードブロック内のフィードバックとTBD\n```\n\n"
            "## ユーザーコメント\n\nユーザー記入のフィードバックとTBD\n\n"
            "## 回答\n\n回答欄のTBD\n\n"
            "## 次の節\n\n再びフィードバック。\n",
        ),
        encoding="utf-8",
    )
    (notes / "plans").mkdir(exist_ok=True)
    (notes / "plans" / "01-計画-1a2b.md").write_text(
        "| 関連フィードバック | なし |\n| 由来 | 人間由来のフィードバック |\n\n本文のフィードバック。\n",
        encoding="utf-8",
    )
    _commit_all(notes)

    _atk_wi_migrate.migrate_queue(notes)

    entry = (notes / "inbox" / "a.md").read_text(encoding="utf-8")
    assert "本文のAWI。" in entry
    assert "再びAWI。" in entry
    assert "コードブロック内のフィードバックとTBD" in entry
    assert "ユーザー記入のフィードバックとTBD" in entry
    assert "回答欄のTBD" in entry
    plan = (notes / "plans" / "01-計画-1a2b.md").read_text(encoding="utf-8")
    assert "| 関連フィードバック | なし |" in plan
    assert "| 由来 | 人間由来のフィードバック |" in plan
    assert "本文のAWI。" in plan


def test_migrate_is_idempotent(tmp_path: pathlib.Path) -> None:
    """変換済みのprivate-notesへ再実行しても何も変更しない。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "inbox" / "a.md").write_text(_legacy_entry("feedback", "フィードバック本文。\n"), encoding="utf-8")
    _commit_all(notes)
    _atk_wi_migrate.migrate_queue(notes)
    head = _git(notes, "rev-parse", "HEAD").stdout.strip()

    result = _atk_wi_migrate.migrate_queue(notes)

    assert result == {"converted": 0, "moved": 0, "commit": None}
    assert _git(notes, "rev-parse", "HEAD").stdout.strip() == head
    assert _git(notes, "status", "--porcelain").stdout == ""


def test_migrate_fails_without_changes_when_destination_name_exists(tmp_path: pathlib.Path) -> None:
    """移動先に同名の項目がある場合は何も変更せずに失敗する。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "planning" / "a.md").write_text(_legacy_entry("feedback", "移動元。\n"), encoding="utf-8")
    (notes / "hold" / "a.md").write_text(_legacy_entry("feedback", "移動先。\n"), encoding="utf-8")
    _commit_all(notes)
    head = _git(notes, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(_common.WebInputError, match="移動先に同名の項目が存在します"):
        _atk_wi_migrate.migrate_queue(notes)

    assert (notes / "planning" / "a.md").exists()
    assert "移動先。" in (notes / "hold" / "a.md").read_text(encoding="utf-8")
    assert _git(notes, "rev-parse", "HEAD").stdout.strip() == head
    assert _git(notes, "status", "--porcelain").stdout == ""


def test_migrate_converts_current_names_and_keeps_withdrawn_identifiers(tmp_path: pathlib.Path) -> None:
    """現行の実体を持つ識別子だけを変換し、廃止済みの複合識別子は当時の名称で残す。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "inbox" / "a.md").write_text(
        _legacy_entry(
            "feedback",
            "process-feedbacksでagent-toolkit:add-feedbackを起動する。\n"
            "feedbacks-plannerとapply-feedbackは廃止済み。\n"
            "計画ファイルはfeedback-batch-20260818-9c4e.mdを参照する。\n"
            "⎿  Stop hook feedback: uncommitted changes detected。\n",
        ),
        encoding="utf-8",
    )
    _commit_all(notes)

    _atk_wi_migrate.migrate_queue(notes)

    entry = (notes / "inbox" / "a.md").read_text(encoding="utf-8")
    assert "process-wiでagent-toolkit:add-awiを起動する。" in entry
    assert "feedbacks-plannerとapply-feedbackは廃止済み。" in entry
    assert "feedback-batch-20260818-9c4e.mdを参照する。" in entry
    assert "⎿  Stop hook feedback: uncommitted changes detected。" in entry


def test_migrate_repairs_identifiers_broken_by_word_level_conversion(tmp_path: pathlib.Path) -> None:
    """概念語として複合識別子を置き換えた時期の変換が残した誤った識別子を直す。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "inbox" / "a.md").write_text(
        "---\ntarget_repo: github.com/example/repo\ntype: awi\nsource: process-awis\n---\n\n"
        "process-awisのレーンをprocess-awis-loopが起動し、awis-plannerが計画する。\n"
        "作業ツリーは/tmp/merge-awis-fxg0fhvb/wtへ作成する。\n"
        "計画ファイルは/plans/2026/08/23-awis-batch-20260823-h3vq.mdを参照する。\n"
        "agent-toolkit:add-awiとawi_pathは現行の識別子なので変えない。\n"
        "⎿  Stop hook awi: uncommitted changes detected.\n",
        encoding="utf-8",
    )
    _commit_all(notes)

    _atk_wi_migrate.migrate_queue(notes)

    entry = (notes / "inbox" / "a.md").read_text(encoding="utf-8")
    assert "source: process-wi\n" in entry
    assert "process-wiのレーンをprocess-feedbacks-loopが起動し、feedbacks-plannerが計画する。" in entry
    assert "/tmp/merge-feedbacks-fxg0fhvb/wt" in entry
    assert "/plans/2026/08/23-feedbacks-batch-20260823-h3vq.md" in entry
    assert "agent-toolkit:add-awiとawi_pathは現行の識別子なので変えない。" in entry
    assert "⎿  Stop hook feedback: uncommitted changes detected." in entry


def test_migrate_repairs_underscore_identifiers_without_changing_current_identifiers(tmp_path: pathlib.Path) -> None:
    """アンダースコア識別子を完全一致で修復し、現行の識別子を維持する。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "inbox" / "a.md").write_text(
        "---\ntarget_repo: github.com/example/repo\ntype: awi\n---\n\n"
        "pick_awis\n"
        "process_awis\n"
        "process_awis_invoked\n"
        "test_awis_planner_contract_separates_coordination_from_writes\n"
        "test_awis_planner_uses_sender_selected_plan_path_and_uwi_boundary\n"
        "test_integrated_plan_overview_lists_post_exclusion_awis\n"
        "test_process_awis_preserves_codex_queue_and_process_loop_contracts\n"
        "test_prompt_references_process_awis\n"
        "_PROCESS_AWIS\n"
        "awi_path _write_awi_file answer_uwi _uwi_scan _PROCESS_WI_SKILL_NAMES\n",
        encoding="utf-8",
    )
    _commit_all(notes)

    _atk_wi_migrate.migrate_queue(notes)

    entry = (notes / "inbox" / "a.md").read_text(encoding="utf-8")
    for expected in (
        "pick_wi",
        "process_wi",
        "process_feedbacks_invoked",
        "test_feedbacks_planner_contract_separates_coordination_from_writes",
        "test_feedbacks_planner_uses_sender_selected_plan_path_and_tbd_boundary",
        "test_integrated_plan_overview_lists_post_exclusion_feedbacks",
        "test_process_feedbacks_preserves_codex_queue_and_process_loop_contracts",
        "test_prompt_references_process_wi",
        "_PROCESS_FEEDBACKS",
        "awi_path _write_awi_file answer_uwi _uwi_scan _PROCESS_WI_SKILL_NAMES",
    ):
        assert expected in entry

    head = _git(notes, "rev-parse", "HEAD").stdout.strip()
    result = _atk_wi_migrate.migrate_queue(notes)
    assert result == {"converted": 0, "moved": 0, "commit": None}
    assert _git(notes, "rev-parse", "HEAD").stdout.strip() == head


def test_migrate_is_idempotent_after_repairing_identifiers(tmp_path: pathlib.Path) -> None:
    """誤った識別子を直したprivate-notesへ再実行しても何も変更しない。"""
    notes = tmp_path / "private-notes"
    _init_notes(notes, tmp_path / "origin.git")
    (notes / "inbox" / "a.md").write_text(
        _legacy_entry("feedback", "process-awisとawis-plannerとawis-normal-wave-m3q9.mdのAWI。\n"),
        encoding="utf-8",
    )
    _commit_all(notes)
    _atk_wi_migrate.migrate_queue(notes)
    head = _git(notes, "rev-parse", "HEAD").stdout.strip()

    result = _atk_wi_migrate.migrate_queue(notes)

    assert result == {"converted": 0, "moved": 0, "commit": None}
    assert _git(notes, "rev-parse", "HEAD").stdout.strip() == head
    assert _git(notes, "status", "--porcelain").stdout == ""
