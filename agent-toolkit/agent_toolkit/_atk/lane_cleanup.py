"""レーン資源の安全な回収と旧所有登録の回復。"""

from __future__ import annotations

import contextlib
import json
import pathlib
import shutil
import subprocess
import typing

from agent_toolkit._atk import lane_activity as _lane_activity
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import permissions as temp_permissions
from agent_toolkit._common import worktree_hooks


def eligible(record: dict[str, typing.Any], *, wait_release: bool = True) -> str | None:
    repo = pathlib.Path(record["repo"])
    worktree = pathlib.Path(record["worktree"])
    state = _lane_records.resource_state(record)
    if state.absent:
        return None
    if not record.get("ready"):
        return "作成または環境準備が未完了"
    if not _lane_records.selection_state(record)["integrated"]:
        return "選定の統合状態が未統合"
    if state.worktree_path:
        activity = _lane_activity.worktree_activity(worktree, wait_release=wait_release)
        if activity:
            return activity
    trees = _lane_records.worktrees(repo)
    if state.worktree_registered:
        if trees.get(str(worktree)) != record["branch"]:
            return "worktreeの登録が所有記録と異なる"
        if state.worktree_path and _lane_records.common(worktree) != record["common_dir"]:
            return "worktreeのGit共通ディレクトリが所有記録と異なる"
        if state.worktree_path and _lane_activity.has_unignored_changes(worktree):
            return "未commitまたは未追跡の変更がある"
    elif state.worktree_path:
        return "登録の無いworktreeが残っている"
    if state.branch:
        _lane_records.branch_is_integrated(record, repo)
    return None


def retained_resource(path: pathlib.Path, error: BaseException) -> dict[str, str]:
    retained = {"record_path": str(path), "reason": str(error)}
    if isinstance(error, managed_temp.ManagedTempError):
        retained["next_action"] = error.next_action
    return retained


def cleanup_session(
    session_id: str | None, *, only: pathlib.Path | None = None, wait_release: bool = True
) -> dict[str, typing.Any]:
    """全登録を先に判定し、安全な対象だけを回収する。結果は削除対象の外へ保持する。

    onlyを指定すると、その登録だけを同じ条件で判定・回収する。
    自動終了ではwait_release=Falseを渡し、終了資源の解放が未確認なら理由付きで保持する。
    """
    result: dict[str, typing.Any] = {"session_id": session_id, "removed": [], "retained": [], "recovered": [], "missing": []}
    if session_id is None or not _lane_records.session_directory(session_id).exists():
        return result
    with _lane_records.locked(session_id) as directory, contextlib.ExitStack() as lane_locks:
        records: list[tuple[pathlib.Path, dict[str, typing.Any]]] = []
        for path in sorted(directory.glob("lane-*.json")):
            if only is not None and path != only:
                continue
            try:
                # 統合中のレーンは待たずに保持する。統合はレーン単位の排他を持ったまま所有session単位を短く取るため、
                # ここで待つと取得順が逆になる。
                lane_locks.enter_context(_lane_records.lane_locked(path, blocking=False))
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["session_id"] != session_id or path.stem != record["lane"]:
                    raise ValueError("登録の所有sessionまたはlaneが異なる")
                state = _lane_records.resource_state(record)
                if _lane_records.recovered(record) and not state.absent:
                    # 回復で作業ツリーを解放し、成果のbranchを残した登録。所在を結果へ示し、回収しない。
                    result["recovered"].append({"record_path": str(path), **record["recovery"]})
                    continue
                held = record.get("selection")
                reason = eligible(record, wait_release=wait_release)
                if record.get("selection") != held:
                    _lane_records.save(path, record)
                if reason:
                    result["retained"].append({"record_path": str(path), "reason": reason})
                else:
                    if not state.worktree_path:
                        integration = record.get("integration")
                        recovery = record.get("recovery")
                        last_head = (
                            integration.get("new_head")
                            if isinstance(integration, dict) and isinstance(integration.get("new_head"), str)
                            else recovery.get("head")
                            if isinstance(recovery, dict) and isinstance(recovery.get("head"), str)
                            else record.get("initial_head")
                        )
                        result["missing"].append(
                            {
                                "lane": record["lane"],
                                "worktree": record["worktree"],
                                "branch": record["branch"],
                                "last_head": last_head,
                                "skipped_procedure": "delete" if record["commands"].get("delete") else None,
                            }
                        )
                    records.append((path, record))
            except _lane_records.record_errors() as error:
                result["retained"].append(retained_resource(path, error))
        result_index = directory / "cleanup-result.json"
        result_path = managed_temp.create_managed_temp("lane-cleanup") / "cleanup-result.json"
        result["result_path"] = str(result_path)
        result["pending"] = [record["lane"] for _path, record in records]
        # 保存先を確保して全登録の判定を残してから削除する。保存失敗では資源を保持する。
        _lane_records.save(result_path, result)
        _lane_records.save(result_index, {"session_id": session_id, "result_path": str(result_path)})
        for path, record in records:
            try:
                # 判定後に状態が変わった対象も、削除の直前に再判定する。
                reason = eligible(record, wait_release=wait_release)
                if reason:
                    raise ValueError(reason)
                state = _lane_records.resource_state(record)
                if not record["teardown_done"] and state.worktree_path:
                    _lane_records.procedure(record, "delete")
                    record["teardown_done"] = True
                    _lane_records.save(path, record)
                reason = eligible(record, wait_release=wait_release)
                if reason:
                    raise ValueError(reason)
                repo = pathlib.Path(record["repo"])
                state = _lane_records.resource_state(record)
                if (
                    state.worktree_registered
                    and record.get("project_hook")
                    and state.worktree_path
                    and worktree_hooks.configured(repo, "WorktreeRemove")
                ):
                    worktree_hooks.remove(
                        repo,
                        pathlib.Path(record["worktree"]),
                        session_id,
                        record["procedure_timeout"],
                        diagnostics_directory=_lane_records.hook_diagnostics(record, "WorktreeRemove"),
                    )
                    remaining = _lane_records.resource_state(record)
                    if remaining.worktree_path or remaining.worktree_registered:
                        raise ValueError("WorktreeRemove hookの後にworktreeが残っている。hookを直して同じ回収操作を再実行する")
                elif state.worktree_registered:
                    remove_args = ("--force",) if not state.worktree_path else ()
                    if state.worktree_path:
                        # Gitの保護判定は既に完了しているため、ここでだけ全木を準備する。
                        # 固有手順が別所有者の生成物を撤去できなければ、所有記録を残して失敗する。
                        temp_permissions.prepare_tree(pathlib.Path(record["worktree"]))
                    _lane_records.git(repo, "worktree", "remove", *remove_args, record["worktree"])
                if _lane_records.resource_state(record).branch:
                    _lane_records.branch_is_integrated(record, repo)
                    _lane_records.git(repo, "branch", "-D", record["branch"])
                if record["managed_temp"]:
                    managed_temp.cleanup_managed_temp(pathlib.Path(record["managed_temp"]))
                integration_directory = _lane_records.integration_directory(record)
                if integration_directory.exists():
                    # 統合の対応表と検証記録は証拠として回収結果と同じ期限付き領域へ移し、状態ディレクトリから外す。
                    moved = result_path.parent / integration_directory.name
                    shutil.move(integration_directory, moved)
                    result.setdefault("integration_materials", {})[record["lane"]] = str(moved)
                path.unlink()
                result["removed"].append(record["lane"])
            except (OSError, ValueError, KeyError, subprocess.SubprocessError, managed_temp.ManagedTempError) as error:
                result["retained"].append(retained_resource(path, error))
            result["pending"].remove(record["lane"])
            _lane_records.save(result_path, result)
        for retained in result["retained"]:
            retained.setdefault(
                "next_action", "理由を解消し、所有登録の入力で作成を再開するか同じlane deleteで残る回収を実行する"
            )
        _lane_records.save(result_path, result)
    return result


def recover_record(record: dict[str, typing.Any], path: pathlib.Path) -> None:
    """成果のbranchを残して作業ツリーの占有を解放し、到達点を所有登録へ記録する。"""
    if record.get("recovery") is None and (
        _lane_records.read_selection(record) is not None or record.get("selection") is not None
    ):
        raise ValueError("選定情報が残る登録は回復の対象外。統合と`atk lane delete`か、--reuse-recordによる再利用で扱う")
    repo = pathlib.Path(record["repo"])
    worktree = pathlib.Path(record["worktree"])
    if _lane_records.common(repo) != record["common_dir"]:
        raise ValueError("repoのGit共通ディレクトリが所有記録と異なる")
    trees = _lane_records.worktrees(repo)
    registered = str(worktree) in trees
    if registered:
        if trees[str(worktree)] != record["branch"]:
            raise ValueError("worktreeの登録が所有記録と異なる")
        if worktree.exists():
            if _lane_records.common(worktree) != record["common_dir"]:
                raise ValueError("worktreeのGit共通ディレクトリが所有記録と異なる")
            activity = _lane_activity.worktree_activity(worktree)
            if activity:
                raise ValueError(activity)
            if _lane_activity.has_unignored_changes(worktree):
                raise ValueError("未commitまたは未追跡の変更がある。成果をcommitしてから再実行する")
    elif worktree.exists():
        raise ValueError("登録の無いworktreeが残っている。内容とGit登録を確認する")
    if record["branch"] not in _lane_records.branches(repo):
        raise ValueError(f"元のbranchが無い: {record['branch']}。成果の所在を確認する")
    head = _lane_records.git(repo, "rev-parse", "--verify", f"refs/heads/{record['branch']}")
    recovery = record.setdefault("recovery", {"phase": "started", "branch": record["branch"], "head": head})
    if recovery["head"] != head:
        raise ValueError("回復の開始後に元branchのHEADが変わった。成果を確認して同じ操作を再実行する")
    _lane_records.save(path, record)
    if registered:
        if worktree.exists() and record.get("project_hook") and worktree_hooks.configured(repo, "WorktreeRemove"):
            worktree_hooks.remove(
                repo,
                worktree,
                record["session_id"],
                record["procedure_timeout"],
                diagnostics_directory=_lane_records.hook_diagnostics(record, "WorktreeRemove"),
            )
            remaining = _lane_records.resource_state(record)
            if remaining.worktree_path or remaining.worktree_registered:
                raise ValueError("WorktreeRemove hookの後にworktreeが残っている。hookを直して同じ回復操作を再実行する")
        elif worktree.exists():
            # Gitのclean判定後にだけ全木を準備し、残る別所有者の内容は保持する。
            temp_permissions.prepare_tree(worktree)
            _lane_records.git(repo, "worktree", "remove", str(worktree))
        else:
            # 実体の無い登録は、その作業ツリーだけをGit登録から外す。
            _lane_records.git(repo, "worktree", "remove", "--force", str(worktree))
    recovery["phase"] = "done"
    _lane_records.save(path, record)


def recover(session_id: str, lanes: list[str] | None = None) -> dict[str, typing.Any]:
    """選定情報を失った旧登録の作業ツリーを、成果のbranchを残して解放する。"""
    for lane_id in lanes or []:
        if not _lane_records.LANE.fullmatch(lane_id):
            raise ValueError("レーンはlane-NNで指定する")
    directory = _lane_records.session_directory(session_id)
    if not directory.is_dir():
        raise ValueError(f"所有sessionの登録が無い: {session_id}。`atk lane delete --list`で所有sessionを確認する")
    result: dict[str, typing.Any] = {"session_id": session_id, "recovered": [], "removed": [], "retained": []}
    with _lane_records.locked(session_id):
        paths = sorted(directory.glob("lane-*.json"))
        if lanes:
            missing = sorted(set(lanes) - {path.stem for path in paths})
            if missing:
                raise ValueError(f"指定したレーンの登録が無い: {', '.join(missing)}")
            paths = [path for path in paths if path.stem in lanes]
        for path in paths:
            # 統合中のレーンは待たずに保持する（取得順の理由は`cleanup_session`と同じ）。
            try:
                with _lane_records.lane_locked(path, blocking=False):
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if record["session_id"] != session_id or path.stem != record["lane"]:
                        raise ValueError("登録の所有sessionまたはlaneが異なる")
                    if _lane_records.resource_state(record).absent:
                        path.unlink()
                        result["removed"].append(record["lane"])
                        continue
                    if not _lane_records.recovered(record):
                        recover_record(record, path)
                    result["recovered"].append({"record_path": str(path), "worktree": record["worktree"], **record["recovery"]})
            except _lane_records.record_errors() as error:
                result["retained"].append(retained_resource(path, error))
    return result
