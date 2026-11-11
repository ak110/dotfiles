"""plans listingの契約を検証する。"""

from __future__ import annotations

import datetime
import os
import pathlib
import types
import typing

import pytest

from agent_toolkit._atk import plans as _atk_plans
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import owner_records as _owner_records
from agent_toolkit._testing.plans_support import (
    _OWNER_SESSION,
    _PROGRESS_PLAN_TEMPLATE,
    _init_local_notes,
    _isolate_state_directory,
    _write_saved_progress_plan,
)

__all__ = ["_isolate_state_directory"]


def test_list_reports_every_working_plan_with_owner_and_update_time(tmp_path: pathlib.Path) -> None:
    """一覧は所有記録の有無にかかわらず全ての計画ファイル（メイン）を出力する。"""
    home = tmp_path / "home"
    working_root = _plan_file.working_plans_root(home)
    working_root.mkdir(parents=True)
    owned = working_root / "30-自分の計画-a1b2.md"
    owned.write_text("# owned\n", encoding="utf-8")
    owned_detail = working_root / "30-自分の計画-a1b2.detail.md"
    owned_detail.write_text("# detail\n", encoding="utf-8")
    _owner_records.record_plan_owner(owned)
    unowned = working_root / "30-記録なしの計画-c3d4.md"
    unowned.write_text("# unowned\n", encoding="utf-8")
    os.utime(owned, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    os.utime(owned_detail, ns=(1_700_000_500_000_000_000, 1_700_000_500_000_000_000))

    entries = _atk_plans.list_working_plans(home)

    assert [entry["path"] for entry in entries] == sorted((str(owned), str(unowned)))
    owner_by_path = {entry["path"]: entry["owner_session"] for entry in entries}
    assert owner_by_path == {str(owned): _OWNER_SESSION, str(unowned): None}
    updated_by_path = {entry["path"]: entry["updated_at"] for entry in entries}
    assert updated_by_path[str(owned)] == datetime.datetime.fromtimestamp(1_700_000_000).astimezone().isoformat()


def test_list_removes_legacy_sidecar_lock_even_when_table_exists(tmp_path: pathlib.Path) -> None:
    """一覧は本体の有無を問わず旧sidecarロックを回収し、共有ロックを残す。"""
    home = tmp_path / "home"
    working_root = _plan_file.working_plans_root(home)
    working_root.mkdir(parents=True)
    plan = working_root / "30-孤立ロック-a1b2.md"
    plan.write_text("# plan\n", encoding="utf-8")
    orphan_lock = working_root / "30-孤立ロック-a1b2.exec-review.tsv.lock"
    orphan_lock.touch()
    live_table = working_root / "30-孤立ロック-a1b2.plan-review.tsv"
    live_table.write_text("", encoding="utf-8")
    live_lock = live_table.with_name(live_table.name + ".lock")
    live_lock.touch()
    shared_lock = working_root / ".agent-toolkit-plan-create.lock"
    shared_lock.touch()

    entries = _atk_plans.list_working_plans(home)

    assert [entry["path"] for entry in entries] == [str(plan)]
    assert not orphan_lock.exists()
    assert not live_lock.exists()
    assert shared_lock.exists()


def test_list_succeeds_when_orphan_lock_cannot_be_removed(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """孤立ロックを削除できない場合も一覧の出力を続ける。"""
    home = tmp_path / "home"
    working_root = _plan_file.working_plans_root(home)
    working_root.mkdir(parents=True)
    plan = working_root / "30-削除不能-a1b2.md"
    plan.write_text("# plan\n", encoding="utf-8")
    orphan_lock = working_root / "30-削除不能-a1b2.exec-review.tsv.lock"
    orphan_lock.touch()

    def _unlink(self: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> None:
        del self, args, kwargs  # noqa
        raise OSError("ロックを削除できない")

    monkeypatch.setattr(pathlib.Path, "unlink", _unlink)

    entries = _atk_plans.list_working_plans(home)

    assert [entry["path"] for entry in entries] == [str(plan)]
    assert orphan_lock.exists()


def test_dispatch_list_prints_owner_and_update_time_per_plan(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一覧の各行はメイン計画の絶対パス、所有セッション、最終更新時刻をタブ区切りで並べる。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    working_root = _plan_file.working_plans_root(home)
    working_root.mkdir(parents=True)
    plan = working_root / "30-一覧出力-a1b2.md"
    plan.write_text("# plan\n", encoding="utf-8")
    args = types.SimpleNamespace(plans_subcommand="list")

    result = _atk_plans.dispatch(args, notes, home)

    assert result == 0
    entries = _atk_plans.list_working_plans(home)
    assert capsys.readouterr().out == f"{plan}\tなし\t{entries[0]['updated_at']}\n"


@pytest.mark.parametrize(
    ("owner", "shown"),
    [("mcp-0123456789abcdef0123456789abcdef", "なし"), ("conversation-session", "conversation-session")],
    ids=["process-root", "conversation"],
)
def test_dispatch_list_shows_only_conversation_owner(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    owner: str,
    shown: str,
) -> None:
    """`agents_server`のプロセス専用のrootの環境で作成した計画は所有者なしと表示し、実在する会話はその会話を表示する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", owner)
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    working_root = _plan_file.working_plans_root(home)
    working_root.mkdir(parents=True)
    plan = working_root / "30-所有者表示-a1b2.md"
    plan.write_text("# plan\n", encoding="utf-8")
    _owner_records.record_plan_owner(plan)

    assert _atk_plans.dispatch(types.SimpleNamespace(plans_subcommand="list"), notes, home) == 0

    assert capsys.readouterr().out.split("\t")[1] == shown


def test_dispatch_rejects_removed_progress_subcommand(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開parserを迂回した旧progress指定もdispatchで拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/09/09-進捗取得-a1b2.md")
    _write_saved_progress_plan(
        notes,
        relative,
        "| 2026-09-09 10:00 | 統合順1の実装 | 変更範囲の検証が終了コード0 |\n| 2026-09-09 11:00 | 統合順2の実装 | 警告0件 |\n",
    )
    args = types.SimpleNamespace(plans_subcommand="progress", plan_file=relative.as_posix())

    with pytest.raises(_wi_web_input.WebInputError, match="未知のplansサブコマンド: progress"):
        _atk_plans.dispatch(args, notes, home)
    assert capsys.readouterr().out == ""


def test_dispatch_rejects_removed_progress_subcommand_for_empty_plan(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """進捗行が無い場合も旧progressのdispatchを復活させない。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/09/09-進捗なし-a1b3.md")
    _write_saved_progress_plan(notes, relative, "")
    args = types.SimpleNamespace(plans_subcommand="progress", plan_file=relative.as_posix())

    with pytest.raises(_wi_web_input.WebInputError, match="未知のplansサブコマンド: progress"):
        _atk_plans.dispatch(args, notes, home)
    assert capsys.readouterr().out == ""


def test_progress_prefers_saved_plan_and_falls_back_to_working_copy(tmp_path: pathlib.Path) -> None:
    """`private-notes/plans/`相対の指定では保存側を先に読み、保存側が無い場合だけ同名の作業側を読む。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/09/09-優先順位-a1b4.md")
    _write_saved_progress_plan(notes, relative, "| 2026-09-09 10:00 | 保存側の工程 | 保存側 |\n")
    working_root = _plan_file.working_plans_root(home)
    working_root.mkdir(parents=True)
    working_main = working_root / relative.name
    working_main.write_text(
        _PROGRESS_PLAN_TEMPLATE.format(rows="| 2026-09-09 12:00 | 作業側の工程 | 作業側 |\n"),
        encoding="utf-8",
    )

    saved_rows = _atk_plans.plan_progress(notes, relative.as_posix(), home=home)
    (notes / "plans" / relative).unlink()
    working_rows = _atk_plans.plan_progress(notes, relative.as_posix(), home=home)

    assert [row["completed_step"] for row in saved_rows] == ["保存側の工程"]
    assert [row["completed_step"] for row in working_rows] == ["作業側の工程"]


def test_progress_rejects_missing_plan_file(tmp_path: pathlib.Path) -> None:
    """計画ファイルが実在しない指定を拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)

    with pytest.raises(_wi_web_input.WebInputError, match="指定したメイン計画が見つかりません") as error_info:
        _atk_plans.plan_progress(notes, "2026/09/09-不在-a1b5.md", home=home)

    assert "atk plans list" in error_info.value.next_action


@pytest.mark.parametrize(
    "content",
    [
        "# plan\n\n## 概要\n\n概要だけの計画。\n",
        "# plan\n\n## 進捗ログ（実行時）\n\n進捗表の無い本文。\n",
        "# plan\n\n## 進捗ログ（実行時）\n\n| 日時 | 完了した工程 |\n| --- | --- |\n| 2026-09-09 10:00 | 工程 |\n",
    ],
)
def test_progress_rejects_broken_progress_structure(tmp_path: pathlib.Path, content: str) -> None:
    """節、固定表および列構成のいずれかが成立しない計画を拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/09/09-構造不成立-a1b6.md")
    main = notes / "plans" / relative
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text(content, encoding="utf-8")

    with pytest.raises(_wi_web_input.WebInputError, match="進捗ログを読み取れません"):
        _atk_plans.plan_progress(notes, relative.as_posix(), home=home)
