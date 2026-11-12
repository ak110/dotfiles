"""レーン統合の入力検証・コマンド実行と履歴比較。"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import pathlib
import re
import sys
import typing

from agent_toolkit._atk import help_text, run_command
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._common import atomic_file
from agent_toolkit._plan import commit_mapping
lazy from agent_toolkit._atk.wi import arguments as wi_arguments
lazy from agent_toolkit._atk.wi import filenames as wi_filenames


def integration_input(args: argparse.Namespace, record: dict[str, typing.Any]) -> dict[str, typing.Any]:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) - {
        "plans",
        "wis",
        "verification",
        "verification_records",
        "verification_timeout",
        "handoff",
        "lane_started_at",
    }:
        raise ValueError(
            "manifestはplans・wis・verification・verification_records・verification_timeout・handoff・lane_started_atを持つobjectで指定する"
        )
    verification_timeout = manifest.get("verification_timeout")
    if verification_timeout is not None and (
        isinstance(verification_timeout, bool)
        or not isinstance(verification_timeout, int | float)
        or verification_timeout <= 0
        or verification_timeout > sys.float_info.max
        or not math.isfinite(verification_timeout)
    ):
        raise ValueError("verification_timeoutは正の有限秒数かnullで指定する")
    reuse = manifest.get("verification_records", [])
    if not isinstance(reuse, list) or any(not isinstance(path, str) or not pathlib.Path(path).is_absolute() for path in reuse):
        raise ValueError("verification_recordsは保存済み実行記録JSONの絶対パスの配列で指定する")
    for name in ("plans", "wis"):
        rows = manifest.get(name)
        if not isinstance(rows, list):
            raise ValueError(f"manifestの{name}は順序付き配列で指定する")
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or not {"name", "commands"} <= set(row)
                or set(row) - {"name", "commands", "saved", "no_merge", "commit_record"}
                or not isinstance(row["name"], str)
                or ("no_merge" in row and not isinstance(row["no_merge"], bool))
            ):
                raise ValueError(f"{name}の各項目はnameとcommandsを持つobjectで指定する")
            if row["name"] in seen:
                raise ValueError(f"{name}の名前が重複している: {row['name']}")
            seen.add(row["name"])
            if "commit_record" in row:
                source = row["commit_record"]
                # 最終化の計画保存で移動するため、実在の確認はGit統合直前に行う。
                if (
                    name != "wis"
                    or not isinstance(source, str)
                    or not pathlib.Path(source).is_absolute()
                    or pathlib.Path(source).suffix != ".md"
                ):
                    raise ValueError(f"commit_recordは計画か引き継ぎ記録Markdownの絶対パスで指定する: {row['name']}")
            if name == "plans" and not row["commands"]:
                saved = pathlib.Path(row.get("saved", ""))
                if not saved.is_absolute() or not saved.is_file() or saved.name != row["name"]:
                    raise ValueError(f"保存済み計画の所在をsavedへ絶対パスで指定する: {row['name']}")
    commands = [*manifest.get("verification", [])]
    for name in ("plans", "wis"):
        for row in manifest[name]:
            commands.extend(row["commands"])
    for argv in commands:
        if not isinstance(argv, list) or not argv or not all(isinstance(value, str) for value in argv):
            raise ValueError("各操作は空でない文字列のargv配列で指定する")
    if not manifest["plans"]:
        handoff = pathlib.Path(manifest.get("handoff", ""))
        if not handoff.is_absolute() or not handoff.is_file():
            raise ValueError("計画なしでは既存の引き継ぎ記録をhandoffへ絶対パスで指定する")
    expected = set(_lane_records.selection_state(record)["lane_wis"])
    if expected and expected != {row["name"] for row in manifest["wis"]}:
        raise ValueError("manifestのWI集合が選定の今回のレーン集合と一致しない")
    if "lane_started_at" in manifest:
        # Git統合後に最終化だけが入力の誤りで止まらないよう、完了時刻より後の値は統合の前に拒否する。
        check_lane_started_before_now(lane_started_at(manifest["lane_started_at"]))
    finalization = [value for name in ("plans", "wis") for row in manifest[name] for argv in row["commands"] for value in argv]
    referenced = False
    for value in finalization:
        for reference in REFERENCE.findall(value):
            referenced = True
            name, separator, file = reference.partition(":")
            if separator and name == "expand-file":
                template = pathlib.Path(file)
                if not template.is_absolute() or not template.is_file():
                    raise ValueError(f"展開するテンプレートは実在する絶対パスで指定する: {file}")
            elif separator or reference not in VALUE_REFERENCES:
                raise ValueError(
                    f"最終化argvの参照が不明: {{{{{reference}}}}}。受理する参照は"
                    "{{integrated_head_short}}・{{completed_at}}・{{lane_seconds}}・{{expand-file:<絶対パス>}}で、"
                    "manifestのargvをこのいずれかへ直して同じlane integrateを再実行する"
                )
    if referenced and "lane_started_at" not in manifest:
        raise ValueError("最終化argvが確定値を参照する場合はmanifestのlane_started_atを指定する")
    return manifest


# 最終化argvが参照する確定値。値は最終化の最初の操作の前に1回だけ生成し、再開では保存値を使う。
REFERENCE = re.compile(r"\{\{([^{}]*)\}\}")

VALUE_REFERENCES = frozenset({"integrated_head_short", "completed_at", "lane_seconds"})


def lane_started_at(value: object) -> datetime.datetime:
    """manifestのレーン開始時刻を時差付きの日時として解釈する。"""
    try:
        started = datetime.datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        started = None
    if started is None or started.tzinfo is None:
        raise ValueError(f"lane_started_atは時差付きISO 8601で指定する: {value}")
    return started


def check_lane_started_before_now(started: datetime.datetime) -> datetime.datetime:
    """レーン開始時刻が現在時刻より後なら拒否し、現在時刻を返す。"""
    now = datetime.datetime.now().astimezone()
    if started > now:
        raise ValueError(
            f"lane_started_atが現在時刻より後にある: 受け取った値={started.isoformat()}, "
            f"現在時刻={now.isoformat(timespec='seconds')}。"
            "最初のレーン担当の起動時刻を時差付きISO 8601（例: 2026-10-19T09:00:00+09:00）で指定し直して再実行する"
        )
    return now


def finalization_values(state: dict[str, typing.Any], started: datetime.datetime) -> dict[str, str]:
    """統合後HEAD・完了時刻・レーン稼働秒数を生成する。計測の終点は最終化の最初の操作の前とする。"""
    completed = check_lane_started_before_now(started)
    seconds = math.floor((completed - started).total_seconds())
    target = pathlib.Path(state["input"]["target"])
    return {
        "integrated_head_short": _lane_records.git(target, "rev-parse", "--short=7", state["integrated_head"]),
        "completed_at": completed.isoformat(timespec="seconds"),
        "lane_seconds": str(seconds),
    }


def expand(record: dict[str, typing.Any], values: dict[str, str] | None, argv: list[str]) -> list[str]:
    """最終化argvの確定値の参照をshellを介さず展開する。テンプレートは展開した複製のパスへ置き換える。

    テンプレート本文では確定値の参照だけを置き換え、他の`{{...}}`はそのまま残す。
    """
    if not values:
        return argv

    def replace(text: str) -> str:
        return REFERENCE.sub(lambda match: values.get(match.group(1), match.group(0)), text)

    def resolve(match: re.Match[str]) -> str:
        name, separator, file = match.group(1).partition(":")
        if not separator or name != "expand-file":
            return replace(match.group(0))
        template = pathlib.Path(file)
        digest = hashlib.sha256(file.encode("utf-8")).hexdigest()[:8]
        expanded = _lane_records.integration_directory(record) / "expanded" / f"{digest}-{template.name}"
        expanded.parent.mkdir(parents=True, exist_ok=True)
        atomic_file.atomic_write(expanded, replace(template.read_text(encoding="utf-8")))
        return str(expanded)

    return [REFERENCE.sub(resolve, value) for value in argv]


def integration_command(
    record: dict[str, typing.Any],
    state: dict[str, typing.Any],
    key: str,
    argv: list[str],
    cwd: pathlib.Path,
    *,
    timeout: float | None,
) -> None:
    if key in state["completed"]:
        return
    observed, code, failure = run_command.execute(argv, cwd, timeout, verification=key.startswith("verification-"))
    observed = _lane_records.persist_observed(record, key, observed)
    state.setdefault("results", {})[key] = observed
    if code == 0:
        state["completed"].append(key)
    _lane_records.save(pathlib.Path(record["record_path"]), record)
    if code:
        raise ValueError(f"統合の{key}が終了{code}: {failure or observed['record_path']}。同じlane integrateで再開する")


def verify(record: dict[str, typing.Any], state: dict[str, typing.Any], argvs: list[list[str]], cwd: pathlib.Path) -> None:
    # 自身に上限が無い検証だけ、manifestの明示値を内部実行へ渡す。
    # 固有手順向けのprocedure_timeoutは重ねず、実行枠の待ちも実処理の上限から外す。
    for index, argv in enumerate(argvs):
        key = f"verification-{index}"
        sources = [*state["input"]["manifest"].get("verification_records", [])]
        previous = state.get("results", {}).get(key, {}).get("record_path")
        if previous:
            sources.append(previous)
        for source in sources:
            observed = run_command.reusable_success(pathlib.Path(source), argv, cwd)
            if observed is not None:
                state.setdefault("results", {})[key] = _lane_records.persist_observed(
                    record, key, {**observed, "reused_from": source}
                )
                if key not in state["completed"]:
                    state["completed"].append(key)
                _lane_records.save(pathlib.Path(record["record_path"]), record)
                break
        else:
            state["completed"] = [value for value in state["completed"] if value != key]
            integration_command(record, state, key, argv, cwd, timeout=state["input"]["manifest"].get("verification_timeout"))


def rebase_unpublished(worktree: pathlib.Path, base: str, record: dict[str, typing.Any], state: dict[str, typing.Any]) -> None:
    current = _lane_records.git(worktree, "rev-parse", "HEAD")
    old_base = _lane_records.git(worktree, "merge-base", base, current)
    commits = _lane_records.git(worktree, "rev-list", f"{old_base}..{current}").splitlines()
    _lane_records.git(worktree, "fetch", "--all", "--no-tags")
    for commit in commits:
        if _lane_records.git(worktree, "for-each-ref", "--format=%(refname)", f"--contains={commit}", "refs/remotes"):
            raise ValueError(f"push済みcommitの書換えを拒否した: {commit}")
    state.update(phase="rebasing", new_base=base)
    record["integration"] = state
    _lane_records.save(pathlib.Path(record["record_path"]), record)
    _lane_records.git(worktree, "-c", "submodule.recurse=false", "rebase", base)


def atk_script(*arguments: str) -> list[str]:
    # 統合先cwdやPATH上の旧プラグインへ版を切り替えず、現在のCLIと同じ実体・依存環境を使う。
    root = pathlib.Path(__file__).resolve().parents[2]
    source = (
        f"import sys; sys.path.insert(0, {str(root)!r}); from agent_toolkit import atk; "
        "raise SystemExit(atk.main(sys.argv[1:]))"
    )
    return [sys.executable, "-c", source, *arguments]


def amend_finalization(state: dict[str, typing.Any], identity: dict[str, typing.Any], digest: str) -> None:
    previous = state["input"]
    if state["phase"] not in {"awaiting-validation", "git-integrated"}:
        raise ValueError(
            "最終化入力の補完はrebase後の検収待ちかGit統合済みの工程で行う。"
            f"現在の工程は{state['phase']}のため、--amend-finalizationを外して同じ入力で再実行する"
        )
    if any(previous[key] != identity[key] for key in ("reviewed_head", "target", "branch", "no_merge")):
        raise ValueError(
            "最終化入力の補完ではレビューHEADと統合先を変更しない。変更する場合はGit統合前に--restart-reviewで再開し、"
            "Git統合後は前回と同じレビューHEADと統合先を指定する"
        )
    old, new = previous["manifest"], identity["manifest"]
    if any(old.get(key) != new.get(key) for key in ("verification", "verification_timeout", "handoff", "lane_started_at")):
        raise ValueError(
            "最終化入力の補完では検証・引き継ぎ記録・レーン開始時刻を変更しない。"
            "Git統合前なら--amend-finalizationを外して--restart-reviewで是正して再開し、"
            "Git統合後は前回と同じ値のまま未完了の最終化argvだけを補完する"
        )
    for group in ("plans", "wis"):
        if [row["name"] for row in old[group]] != [row["name"] for row in new[group]]:
            raise ValueError(
                "最終化入力の補完では全計画・WIの集合と順序を変更しない。前回と同じ名前と順序のmanifestへ直して再実行する"
            )
        for before, after in zip(old[group], new[group], strict=True):
            if before.get("no_merge", False) != after.get("no_merge", False):
                raise ValueError("最終化入力の補完では実装有無の区分を変更しない。前回と同じno_mergeの値で再実行する")
            for index, argv in enumerate(before["commands"]):
                key = f"{group}:{before['name']}:{index}"
                if key in state["completed"] and (index >= len(after["commands"]) or after["commands"][index] != argv):
                    raise ValueError(
                        f"成功済みの最終化操作を変更できない: {key}。このargvは前回と同じ値へ戻し、未完了の操作だけを補完する"
                    )
    state.update(previous_manifest=old, input=identity, input_digest=digest)


def assert_integration_tree(
    record: dict[str, typing.Any], target: pathlib.Path, branch: str, *, target_state: bool = True
) -> pathlib.Path:
    """レーンと統合先の登録と、作業ツリー・中断状態を確かめる。

    `target_state`をFalseにすると統合先の作業ツリーと中断状態を確かめない。
    統合先は他レーンの短い更新が統合先単位の排他の中で一時的にロックファイルを作るため、
    その排他の外（統合の開始時）では登録だけを確かめ、作業ツリーと中断状態は排他の中で確かめる。
    """
    worktree = pathlib.Path(record["worktree"])
    if not record.get("ready") or _lane_records.common(target) != record["common_dir"]:
        raise ValueError("準備状態または統合先のGit共通ディレクトリが所有記録と異なる")
    for tree, expected in ((worktree, record["branch"]), (target, branch)):
        if _lane_records.worktrees(tree).get(str(tree)) != expected or _lane_records.common(tree) != record["common_dir"]:
            raise ValueError(f"worktreeとbranchの登録が所有記録と異なる: {tree}")
        if tree == target and not target_state:
            continue
        if _lane_records.git(tree, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError(f"統合作業ツリーに未commitまたは未追跡の変更がある: {tree}")
        for name in (
            "rebase-merge",
            "rebase-apply",
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "index.lock",
            "HEAD.lock",
            "packed-refs.lock",
            f"refs/heads/{expected}.lock",
        ):
            if pathlib.Path(_lane_records.git(tree, "rev-parse", "--path-format=absolute", "--git-path", name)).exists():
                raise ValueError(f"Gitの中断状態がある: {tree}: {name}")
    return worktree


def compare_integration_history(
    record: dict[str, typing.Any],
    state: dict[str, typing.Any],
    worktree: pathlib.Path,
    resolved: list[str],
    excluded: list[list[str]],
) -> None:
    directory = _lane_records.integration_directory(record)
    directory.mkdir(parents=True, exist_ok=True)
    mapping = directory / "rewrite-map.json"
    comparison = state.get("comparison", state)
    output = directory / "comparison-map.json" if state.get("rewrite_prefix") else mapping
    if state.get("rewrite_map") != str(mapping) or not output.is_file():
        state["completed"] = [key for key in state["completed"] if key != "history-compare"]
    integration_command(
        record,
        state,
        "history-compare",
        atk_script(
            "run-script",
            "history-compare",
            "--",
            "--operation",
            "rebase",
            "--work-dir",
            str(worktree),
            "--old-base",
            comparison["old_base"],
            "--old-head",
            comparison["old_head"],
            "--new-base",
            comparison["new_base"],
            "--new-head",
            comparison.get("new_head", state["new_head"]),
            "--output",
            str(output),
            *(argument for oid in resolved for argument in ("--resolved-conflict", oid)),
            *(argument for oid, path in excluded for argument in ("--exclude-path", oid, path)),
        ),
        worktree,
        timeout=record["procedure_timeout"],
    )
    # 省略時に再利用する入力は、消費する工程の成功後に保存する。
    # 拒否された指定を先に保存すると、次回に指定を外しても公開入力だけでは復帰できなくなる。
    state["resolved_conflicts"] = resolved
    state["excluded_paths"] = excluded
    state["comparison"] = {
        **{key: comparison.get(key, state[key]) for key in ("old_base", "old_head", "new_base", "new_head")},
        "resolved_conflicts": resolved,
        "excluded_paths": excluded,
    }
    if prefix := state.get("rewrite_prefix"):
        current = commit_mapping.load_rewrite_map(output)
        prefix_path, continuation = directory / "rewrite-prefix.json", directory / "rewrite-continuation.json"
        _lane_records.save(prefix_path, prefix)
        # 最初の旧系列の対応表へ、後から追加したcommitを混ぜずに合成する。
        endpoints = {oid for value in prefix.values() for oid, _names in commit_mapping.rewrite_destinations(value)}
        following = {old: new for old, new in current.items() if old in endpoints}
        _lane_records.save(continuation, following)
        composed = commit_mapping.load_range_rewrite(
            worktree, [prefix_path, *([continuation] if following else [])], state["old_head"]
        )
        _lane_records.save(mapping, composed)
        state["additional_rewrites"] = {old: current.get(new, new) for old, new in state.get("additional_rewrites", {}).items()}
    state["additional_commits"] = list(dict.fromkeys(state.get("additional_rewrites", {}).values()))
    state["rewrite_map"] = str(mapping)
    _lane_records.save(pathlib.Path(record["record_path"]), record)


def validate_adopt_commits(manifest: dict[str, typing.Any], worktree: pathlib.Path) -> None:
    parser = None
    for row in manifest["wis"]:
        for argv in row["commands"]:
            position = next((index for index in range(len(argv) - 1) if argv[index : index + 2] == ["wi", "adopt"]), None)
            if position is None:
                continue
            if parser is None:
                parser = argparse.ArgumentParser(prog="atk wi", allow_abbrev=False)
                sub = help_text.add_subcommands(parser, dest="wi_subcommand", required=False, show_help_when_missing=True)
                wi_arguments.add_adopt_parser(sub)
            # パスの型検査にはテンプレート元を使い、複製と本文展開はGit統合後の_expandで行う。
            validation_argv = [
                REFERENCE.sub(
                    lambda match: (
                        match.group(1).removeprefix("expand-file:")
                        if match.group(1).startswith("expand-file:")
                        else match.group(0)
                    ),
                    value,
                )
                for value in argv[position + 1 :]
            ]
            try:
                adopted = parser.parse_args(validation_argv)
            except SystemExit as error:
                raise ValueError(f"adoptのargvが不正: {row['name']}。atk wi adoptの公開入力へ直す") from error
            specified = adopted.commit or []
            source = pathlib.Path(row.get("commit_record", ""))
            action = (
                f"commit_recordのMarkdownを対象に、atk run-script plan-commitsの--worktreeへ{worktree}を指定して"
                "現在の一覧を取得しmanifestを作り直す。引き継ぎ記録には--handoffと対象AWI全件の--allowed-awiも指定する"
            )
            if not source.is_absolute() or not source.is_file():
                raise ValueError(f"{row['name']}: commit_recordへ計画か引き継ぎ記録Markdownの絶対パスを指定し、{action}")
            try:
                content = source.read_text(encoding="utf-8")
                events = commit_mapping.read_events(source, content)
                allowed = {row["name"]}
                if source.name in {plan["name"] for plan in manifest["plans"]}:
                    allowed = commit_mapping.plan_allowed_wis(content, source)
                else:
                    for event in events:
                        names = event.get("awi")
                        if isinstance(names, list):
                            allowed.update(name for name in names if isinstance(name, str))
                commit_mapping.validate_targets([row["name"]], allowed)
                mapping = commit_mapping.read_mapping(worktree, events, allowed, allow_missing=True)
                expected = {
                    commit_mapping.resolve_commit(worktree, oid) for oid, names in mapping.items() if row["name"] in names
                }
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"{row['name']}: 指定値={specified}、期待集合を取得できない: {source}。{error}。{action}"
                ) from error
            effective_commits = specified
            if any("{{integrated_head_short}}" in value for value in specified):
                # fast-forwardするHEADの短縮OIDは、最終化の_expandへ渡す値と同じになる。
                short = _lane_records.git(worktree, "rev-parse", "--short=7", "HEAD")
                effective_commits = [value.replace("{{integrated_head_short}}", short) for value in specified]
            try:
                commits = {commit_mapping.resolve_commit(worktree, value) for value in effective_commits}
            except commit_mapping.CommitMappingError as error:
                raise ValueError(
                    f"{row['name']}: 指定値={specified}、期待集合={sorted(expected)}。{error}。{action}"
                ) from error
            names = {wi_filenames.normalize_md_filename(name) for name in adopted.filenames}
            if names != {row["name"]} or commits != expected or (row.get("no_merge") and commits):
                raise ValueError(f"{row['name']}: 指定値={specified}、期待集合={sorted(expected)}。{action}")
