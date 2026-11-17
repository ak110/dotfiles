"""process-loopの完了検収。報告前のstatusと常駐の終了処理が同じ処理範囲と残件の判定を使う。"""

import json
import pathlib
from typing import Any

import pydantic

from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._atk.wi import headings as wi_headings
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_launch_gate as _gate
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._git import command as _git_command


class _CompletionTarget(pydantic.BaseModel):
    """報告前の確認で使う起動原本と所有の識別。"""

    target_repo: str
    ownership_record: pathlib.Path
    ownership_token: str
    candidate_file: pathlib.Path
    lane_session_id: str


class _CompletionManifest(pydantic.BaseModel):
    """既存の起動manifestから対象と所有情報を検証して取り出す。"""

    repositories: list[_CompletionTarget] = pydantic.Field(min_length=1)


def completion_status(
    manifest: pathlib.Path, private_notes: pathlib.Path, additional_wis: list[str] | None = None
) -> dict[str, Any]:
    """報告前にも常駐と同じ現キュー・最終化検収を読み取り専用で使う。"""
    request = _CompletionManifest.model_validate_json(manifest.read_text(encoding="utf-8"))
    snapshots = {item.repository.repo_id: item for item in _listing.configured_repo_snapshot(private_notes)}
    results: dict[str, Any] = {}
    unmatched = set(additional_wis or [])
    for target in request.repositories:
        if target.target_repo in results or target.target_repo not in snapshots:
            raise ValueError("完了確認の対象が重複しているか、現在の登録先に存在しない")
        record = json.loads(target.ownership_record.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or record.get("token") != target.ownership_token
            or record.get("repo_id") != target.target_repo
        ):
            raise ValueError("起動時と現在の所有tokenまたは対象が一致しない")
        owner = _ownership.Ownership(
            target.target_repo,
            target.ownership_token,
            target.ownership_record,
            record.get("session_id"),
            record.get("previous"),
        )
        if owner.lane_session_id != target.lane_session_id:
            raise ValueError("起動時と現在のレーン所有が一致しない")
        present = {entry[0].name for entry in snapshots[target.target_repo].entries}
        additional = set(additional_wis or []) & present
        unmatched -= additional
        remaining, excluded = remaining_work(snapshots[target.target_repo], owner, additional, target.candidate_file)
        results[target.target_repo] = {
            "status": "incomplete" if any(remaining.values()) else "ready",
            "observed_remaining": remaining,
            **excluded,
        }
    if unmatched:
        raise ValueError(f"追加WIをmanifestの対象リポジトリから取得できない: {sorted(unmatched)}")
    return {"repositories": results}


def remaining_work(
    snapshot: _listing.RepositorySnapshot,
    ownership: _ownership.Ownership,
    additional: set[str],
    candidate_file: pathlib.Path | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """今回の処理範囲の残件と、残件から除いた削除済み・先送りのWIを返す。

    処理範囲は開始時の候補全件ではなく、レーンの選定原本で選んだWIと巡回中に追加したWIとする。
    1回の巡回は候補から一部だけを選ぶため、選定外の候補が残るのは正常である。
    `candidate_file`を渡すと、巡回中に削除された選定外の候補も削除済みとして示す（残件には含めない）。
    """
    current = _listing.configured_repo_snapshot(snapshot.private_notes, (snapshot.repository,))[0]
    resources = _lane.list_resources(ownership.lane_session_id)
    if resources["errors"]:
        raise ValueError(f"レーン登録を照会できない: {resources['errors']}")
    lanes: set[str] = set()
    scope = set(additional)
    for entry in resources["records"]:
        record = entry["record"]
        integrated = record.get("integration", {}).get("phase") == "finalized"
        if not integrated:
            lanes.add(record["lane"])
        if "selection_file" in record:
            # 選定原本がmanaged-tempの保持期限で消えても、所有登録へ写した選定を使う。
            scope.update(_lane_records.selection_state(record)["lane_wis"])
    entries = {path.name: (state, text) for path, _repo, text, state, _kind in current.entries}
    removed = sorted(name for name in scope - entries.keys() if _deleted_in_history(snapshot.private_notes, name))
    if candidate_file is not None:
        # 選定外の候補は残件に含めないが、巡回中に削除された候補は検収と報告で確かめられるよう列挙する。
        candidates = json.loads(candidate_file.read_text(encoding="utf-8"))
        if not isinstance(candidates, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("filename"), str) for item in candidates
        ):
            raise ValueError("開始時の候補原本の形式が不正")
        unselected = {item["filename"] for item in candidates} - scope - entries.keys()
        try:
            deleted = {name for name in unselected if _deleted_in_history(snapshot.private_notes, name)}
        except ValueError:
            # 選定外の候補は完了の判定に使わない報告用の情報であり、履歴を照会できなくても検収を止めない。
            # 選定WIの欠落は上の判定で従来どおり失敗にする。
            deleted = set()
        removed = sorted({*removed, *deleted})
    if missing := sorted(scope - entries.keys() - set(removed)):
        # 削除の記録が無い欠落は、読取失敗や取り違えを完了扱いしないため失敗にする。
        raise ValueError(f"処理対象のWIを現キューから取得できない: {missing}")
    # 再開記録を保存してinboxへ戻したWIは正当な先送りであり、未処理の残件と分けて示す。
    deferred = sorted(
        name
        for name in scope & entries.keys()
        if entries[name][0] == wi_constants.WI_STATE_INBOX
        and wi_headings.last_h2_section_lines(entries[name][1], wi_constants.OBSERVATION_RESUME_HEADING) is not None
    )
    # 開始側と同じ候補・修復診断を使い、冷却・外部依存で延期したinboxを残件へ戻さない。
    items = {name: kind.rsplit(":", 1)[-1] for name, _digest, kind in _gate.repository_input(current, None).items}
    # 未回答UWIなど開始候補から外れる項目も、processingのままなら処理終了を待つ。
    items.update((name, "processing") for name, (state, _text) in entries.items() if state == "processing")
    remaining = {
        "wi": sorted(
            name
            for name, kind in items.items()
            if (name in scope and name not in deferred) or kind not in {"ready", "internal-dependency-wait"}
        ),
        "lanes": sorted(lanes),
    }
    return remaining, {"removed_wi": removed, "deferred_wi": deferred}


def _deleted_in_history(private_notes: pathlib.Path, name: str) -> bool:
    """private-notesの履歴に、そのWIファイルを削除したcommitがあるかを返す。"""
    result = _git_command.run(
        ["-C", str(private_notes), "log", "--diff-filter=D", "--format=%H", "-1", "--", f"*/{name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"private-notesの削除履歴を照会できない: {result.stderr.strip()}")
    return bool(result.stdout.strip())
