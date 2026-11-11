"""レーンのGit統合と最終化の再開可能な工程。"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import typing

from agent_toolkit._atk import lane_activity as _lane_activity
from agent_toolkit._atk import lane_integration_input as _lane_integration_input
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._git import command as git_command


def _update_target(
    record: dict[str, typing.Any],
    state: dict[str, typing.Any],
    manifest: dict[str, typing.Any],
    args: argparse.Namespace,
    target: pathlib.Path,
    worktree: pathlib.Path,
) -> dict[str, str] | None:
    """統合先を更新する短い区間。統合先が準備の前提から進んでいれば更新せず、前提と現在のHEADを返す。

    検証と人の判断は区間の外で終えておき、統合先単位の排他は統合先HEADの確認と更新の間だけ保持する。
    前進を検出した場合は工程を変えずに返し、既存の`--restart-review`による再rebase・比較・検収へ戻す。
    """
    # --no-mergeも統合先を書かないが同じ排他を取る。他レーンのfast-forwardが一時的に作るロックファイルを
    # 中断状態と誤判定せず、その更新の終了を待ってから統合先の作業ツリーと中断状態を確かめるためである。
    with _lane_records.target_locked(record, args.target_branch):
        _lane_integration_input.assert_integration_tree(record, target, args.target_branch)
        if not args.no_merge:
            expected = state.get("new_base") or state.get("verified_base")
            current = _lane_records.git(target, "rev-parse", "HEAD")
            if current != expected:
                return {"expected": expected or "", "actual": current}
        _lane_integration_input.validate_adopt_commits(manifest, worktree)
        if not args.no_merge:
            if _lane_records.git(worktree, "rev-parse", "HEAD") != state["new_head"]:
                raise ValueError("検証済みHEADが変わっている")
            _lane_records.git(target, "merge", "--ff-only", state["new_head"])
        state.update(phase="git-integrated", integrated_head=_lane_records.git(target, "rev-parse", "HEAD"))
        _lane_records.save(pathlib.Path(record["record_path"]), record)
    return None


def integrate(args: argparse.Namespace, session_id: str) -> dict[str, typing.Any]:
    """所有登録からGit統合と最終化を再開し、全操作の成功後に選定を統合済みにする。"""
    for path in (args.record, args.manifest, args.target_worktree):
        if not path.is_absolute():
            raise ValueError("record・manifest・target-worktreeは絶対パスで指定する")
    path = args.record.resolve()
    if path.parent != _lane_records.session_directory(session_id) or not _lane_records.LANE.fullmatch(path.stem):
        raise ValueError("recordは現在の所有sessionの登録済みlane recordを指定する")
    # 全区間でこのレーンの排他だけを保持し、統合先HEADの更新と選定の更新だけを短い排他で直列にする。
    with _lane_records.lane_locked(path):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["session_id"] != session_id or record["record_path"] != str(path):
            raise ValueError("統合するレーンの書込所有を確認できない")
        target = args.target_worktree.resolve()
        if args.target_branch != record["base_branch"]:
            raise ValueError("統合先branchが所有登録と異なる")
        worktree = _lane_integration_input.assert_integration_tree(record, target, args.target_branch, target_state=False)
        excluded: frozenset[str] = frozenset([session_id, *(args.writer_session or []), *_lane_activity.caller_chain()])
        writers = _lane_activity.unowned_writers((worktree, target), excluded)
        if writers:
            raise ValueError(
                f"現在の書込所有に含まれない担当が稼働中: {len(writers)}件。"
                + "; ".join(writer.describe() for writer in writers)
                + "。各担当の担当範囲と統合への合意を確かめ、合意した担当は--writer-sessionへ反復指定し、"
                "合意しない担当は終了を待ってから同じlane integrateを再実行する。"
                "統合先へ書き込まない読み取りだけの補助の担当は、作業ディレクトリを両worktreeの外にして起動し直してもよい"
            )
        manifest = _lane_integration_input.integration_input(args, record)
        if (
            "lane_started_at" in manifest
            and "lane_started_at" in record
            and _lane_integration_input.lane_started_at(manifest["lane_started_at"])
            != _lane_integration_input.lane_started_at(record["lane_started_at"])
        ):
            raise ValueError(
                f"lane_started_atが所有登録の最初のレーン開始時刻と異なる: {record['lane_started_at']}。"
                "担当の置換や追加統合でも最初の起点を渡す"
            )
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", args.reviewed_head):
            raise ValueError("reviewed-headは7文字以上の一意なcommit OIDで指定する")
        reviewed = _lane_records.git(worktree, "rev-parse", "--verify", f"{args.reviewed_head}^{{commit}}")
        identity = {
            "reviewed_head": reviewed,
            "target": str(target),
            "branch": args.target_branch,
            "manifest": manifest,
            "no_merge": args.no_merge,
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        state: dict[str, typing.Any] | None = record.get("integration")
        if state is not None and state["phase"] == "finalized" and not _lane_records.selection_state(record)["integrated"]:
            # 観測だけの再開ではHEADとmanifestが同じでも、選定の未統合を処置する必要がある。
            record["previous_integration"] = state
            state = None
        if state is not None and args.restart_review:
            if state["phase"] in {"git-integrated", "finalized"}:
                raise ValueError("Git統合後は--restart-reviewで再開せず、同じ入力で残る最終化を実行する")
            current = _lane_records.git(worktree, "rev-parse", "HEAD")
            record["previous_integration"] = state
            if state["phase"] in {"rebased", "awaiting-validation", "verified"} and all(
                state.get(name) for name in ("old_base", "old_head", "new_base", "new_head")
            ):
                ancestor = git_command.run(
                    ["merge-base", "--is-ancestor", state["new_head"], current], worktree, check=False
                ).returncode
                if ancestor not in {0, 1}:
                    raise ValueError("保存したnew_headと現在HEADの祖先判定が失敗した。Git実体と所有記録を確認して再実行する")
                if ancestor == 1:
                    raise ValueError(
                        f"現在HEAD {current}は記録のnew_head {state['new_head']}の子孫ではない。"
                        "保存したrebase系列を復元して再レビューし、--restart-reviewで再開する"
                    )
                if reviewed not in ({state["old_head"], current} if current == state["new_head"] else {current}):
                    raise ValueError(
                        "再開のレビュー対象HEADは、HEADが変わった場合は現在HEAD、同じ場合はold_headかnew_headを指定する"
                    )
                previous_head = state["new_head"]
                state = {
                    **state,
                    "input_digest": digest,
                    "input": identity,
                    "phase": "rebased",
                    "completed": [key for key in state["completed"] if not key.startswith("verification-")],
                    "results": dict(state.get("results", {})),
                    "additional_rewrites": dict(state.get("additional_rewrites", {})),
                }
                record["integration"] = state
                if current != previous_head:
                    state.setdefault(
                        "comparison", {key: state[key] for key in ("old_base", "old_head", "new_base", "new_head")}
                    )
                    additions = _lane_records.git(worktree, "rev-list", "--reverse", f"{previous_head}..{current}").splitlines()
                    state["additional_rewrites"].update({oid: oid for oid in additions})
                    state["new_head"] = current
                base = _lane_records.git(target, "rev-parse", "HEAD")
                if base != state["new_base"]:
                    if state["additional_rewrites"]:
                        _lane_integration_input.compare_integration_history(
                            record,
                            state,
                            worktree,
                            state.get("comparison", {}).get("resolved_conflicts", state.get("resolved_conflicts", [])),
                        )
                        state["rewrite_prefix"] = json.loads(pathlib.Path(state["rewrite_map"]).read_text(encoding="utf-8"))
                        # 再rebaseの旧系列には前回の競合解消が織り込まれている。指定の再利用も比較する系列ごとに保つ。
                        state["comparison"] = {
                            "old_base": state["new_base"],
                            "old_head": current,
                            "new_base": base,
                            "resolved_conflicts": [],
                        }
                    else:
                        state.pop("comparison", None)
                    state["completed"] = [key for key in state["completed"] if key != "history-compare"]
                    _lane_integration_input.rebase_unpublished(worktree, base, record, state)
                    state.update(phase="rebased", new_head=_lane_records.git(worktree, "rev-parse", "HEAD"))
                record["integration"] = state
                _lane_records.save(path, record)
            else:
                if current != reviewed:
                    raise ValueError("再レビューしたHEADが現在HEADと一致しない")
                state = None
        if state is not None and state["input_digest"] != digest:
            if args.amend_finalization:
                _lane_integration_input.amend_finalization(state, identity, digest)
                _lane_records.save(path, record)
            elif state["phase"] != "finalized":
                raise ValueError(
                    "統合途中の入力が異なる。Git統合前の検証argv・最終化argv・handoffの是正は--restart-reviewで再開する。"
                    "HEADが記録のnew_headのままならold_headかnew_headを--reviewed-headへ渡し、rebase結果を再検証して検収待ちへ戻る。"
                    "未完了の最終化argvだけの補完は--amend-finalizationを指定する"
                )
            else:
                state = None
        if state is None:
            if _lane_records.git(worktree, "rev-parse", "HEAD") != reviewed:
                raise ValueError("現在HEADがレビュー対象HEADと一致しない")
            state = {
                "input_digest": digest,
                "input": identity,
                "phase": "prepared",
                "completed": [],
                "old_head": reviewed,
                "target_head": _lane_records.git(target, "rev-parse", "HEAD"),
            }
            record["integration"] = state
            _lane_records.save(path, record)
        if state["phase"] == "finalized" and _lane_records.git(worktree, "rev-parse", "HEAD") != state.get(
            "new_head", state["old_head"]
        ):
            raise ValueError("前回最終化後に現在HEADが変わっている。今回のレビュー対象HEADとmanifestを渡す")
        if state["phase"] == "prepared" and not args.no_merge:
            current = _lane_records.git(worktree, "rev-parse", "HEAD")
            if current != reviewed:
                raise ValueError("現在HEADがレビュー対象HEADと一致しない")
            base = _lane_records.git(target, "rev-parse", "HEAD")
            ancestor = git_command.run(["merge-base", "--is-ancestor", base, current], worktree, check=False).returncode
            if ancestor not in {0, 1}:
                raise ValueError("統合先との祖先判定が失敗した")
            if ancestor == 1:
                old_base = _lane_records.git(worktree, "merge-base", base, current)
                if not manifest.get("verification"):
                    raise ValueError("rebase後の検証argvがmanifestに無い")
                state["old_base"] = old_base
                _lane_integration_input.rebase_unpublished(worktree, base, record, state)
                state.update(phase="rebased", new_head=_lane_records.git(worktree, "rev-parse", "HEAD"))
                _lane_records.save(path, record)
            else:
                # 短い更新で統合先がこの時点から進んでいないことを確かめるため、前提のHEADを保持する。
                state.update(phase="verified", new_head=current, verified_base=base)
                _lane_records.save(path, record)
        if state["phase"] == "rebasing":
            state.update(phase="rebased", new_head=_lane_records.git(worktree, "rev-parse", "HEAD"))
            _lane_records.save(path, record)
        if state["phase"] == "awaiting-validation" and _lane_records.materials_missing(state):
            # 旧状態の対応表・検証記録が期限付き領域から失われた。保存済みの両系列から再生成して検収待ちへ戻る。
            state.update(
                phase="rebased",
                completed=[
                    key for key in state["completed"] if key != "history-compare" and not key.startswith("verification-")
                ],
            )
            _lane_records.save(path, record)
        saved_resolved = state.get("comparison", {}).get("resolved_conflicts", state.get("resolved_conflicts", []))
        resolved = saved_resolved
        if args.resolved_conflict is not None and state["phase"] in {"rebased", "awaiting-validation"}:
            # 競合解消の指定はhistory-compareへ渡す入力で、同じ統合入力の再開に付けるためinput_digestへ含めない。
            # 指定を省いた再開（検収待ちの受理など）は保存済みの集合を使い、集合が変わった場合だけhistory-compareをやり直す。
            resolved = sorted(
                {
                    _lane_records.git(worktree, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}")
                    for value in args.resolved_conflict
                }
            )
            if saved_resolved != resolved:
                state.update(
                    phase="rebased",
                    completed=[key for key in state["completed"] if key != "history-compare"],
                )
                _lane_records.save(path, record)
        if state["phase"] == "rebased":
            _lane_integration_input.compare_integration_history(record, state, worktree, resolved)
            _lane_integration_input.verify(record, state, manifest["verification"], worktree)
            state["phase"] = "awaiting-validation"
            _lane_records.save(path, record)
        if state["phase"] == "awaiting-validation":
            if args.restart_review or args.accept_rebased_head != state["new_head"]:
                return dict(
                    state,
                    record_path=str(path),
                    next_action=(
                        "保存済み対応表・検証診断とcommit対応を検収し、"
                        f"同じ操作に--accept-rebased-head {state['new_head']}を指定する"
                    ),
                )
            state["phase"] = "verified"
            _lane_records.save(path, record)
        if state["phase"] == "verified" or (state["phase"] == "prepared" and args.no_merge):
            _lane_integration_input.verify(record, state, manifest.get("verification", []), worktree)
            advanced = _update_target(record, state, manifest, args, target, worktree)
            if advanced is not None:
                return dict(
                    state,
                    record_path=str(path),
                    target_advanced=advanced,
                    next_action=(
                        "統合先が準備の時点から進んだため統合先を更新していない。"
                        "同じ入力に--restart-reviewを付け、現在HEADを--reviewed-headへ渡して最新の統合先へ追随する"
                    ),
                )
        if state["phase"] == "git-integrated":
            _lane_records.git(target, "merge-base", "--is-ancestor", state["integrated_head"], "HEAD")
            if "lane_started_at" in manifest and "finalization_values" not in state:
                # 再開と--amend-finalizationでは保存した確定値を使い、全消費先へ同じ値を渡す。
                state["finalization_values"] = _lane_integration_input.finalization_values(
                    state, _lane_integration_input.lane_started_at(manifest["lane_started_at"])
                )
                record["lane_started_at"] = manifest["lane_started_at"]
                _lane_records.save(path, record)
            for group in ("plans", "wis"):
                for row in manifest[group]:
                    for index, argv in enumerate(row["commands"]):
                        _lane_integration_input.integration_command(
                            record,
                            state,
                            f"{group}:{row['name']}:{index}",
                            _lane_integration_input.expand(record, state.get("finalization_values"), argv),
                            worktree if group == "plans" else target,
                            timeout=record["procedure_timeout"],
                        )
            _lane_integration_input.integration_input(args, record)
            selected = pathlib.Path(record["selection_file"])
            if not selected.exists() and "selection-mark" not in state["completed"]:
                # 選定原本は失効した。統合済みの事実を所有登録の選定情報へ記録して回収判定へ渡す。
                state["completed"].append("selection-mark")
                state.setdefault("results", {})["selection-mark"] = {"skipped": "選定原本が失効したため所有登録へ記録した"}
            # 選定YAMLは同じ所有sessionの全レーンが共有するため、更新だけを所有session単位の排他で直列にする。
            with _lane_records.locked(session_id):
                _lane_integration_input.integration_command(
                    record,
                    state,
                    "selection-mark",
                    _lane_integration_input.atk_script(
                        "run-script",
                        "pick-wi-check",
                        "--",
                        str(selected),
                        "--mark-integrated",
                        record["lane"],
                        "--output",
                        str(selected),
                        "--work-dir",
                        str(target),
                    ),
                    target,
                    timeout=record["procedure_timeout"],
                )
            record["selection"] = {**_lane_records.selection_state(record), "integrated": True}
            state["phase"] = "finalized"
            _lane_records.save(path, record)
        return dict(state, record_path=str(path))
