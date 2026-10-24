"""保存済みlane統合記録と返却原文から、統合完了の機械的な受領条件を確認する。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import cli as wi_cli
from agent_toolkit._common import next_action, plugin_environment, process_tree
from agent_toolkit._git import command
from agent_toolkit._plan import commit_mapping, locations

_FIELDS = (
    "統合後のHEAD",
    "保存した計画ファイル",
    "adoptしたWI",
    "rejectしたWI",
    "adoptを延期したAWIとcommit",
    "変更したエージェント向け文書",
)
_RETRY = "表示した差異を同じレーン担当へ返し、原本を保持して修正後の返却へ同じ受領操作を再実行する"


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _value in pairs}) != len(pairs):
        raise ValueError("JSON objectのキーが重複している")
    return dict(pairs)


def _strings(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field}は重複の無い空でない文字列の配列で指定する")
    return value


def _reply(path: pathlib.Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if not lines or lines[0] != "統合完了":
        raise ValueError("返却は統合完了で開始する必要がある")
    result: dict[str, Any] = {}
    for line in lines[1:]:
        field, separator, value = line.partition(": ")
        if separator and field in {"想定外事象", "気付いた改善点"}:
            if set(result) != set(_FIELDS) or not value.strip():
                raise ValueError(f"返却の追記は必須項目の後へ本文を持つ行で指定する: {field}")
            continue
        if not separator or field not in _FIELDS or field in result:
            raise ValueError(f"返却の項目が不正または重複している: {field}")
        result[field] = value
    if set(result) != set(_FIELDS):
        raise ValueError(f"返却の必須項目が不足している: {sorted(set(_FIELDS) - set(result))}")
    for field in (_FIELDS[1], _FIELDS[4], _FIELDS[5]):
        result[field] = json.loads(result[field], object_pairs_hook=_object)
    for field in (_FIELDS[1], _FIELDS[5]):
        result[field] = _strings(result[field], field)
    for field in (_FIELDS[2], _FIELDS[3]):
        result[field] = [] if result[field] == "なし" else result[field].split(",")
        _strings(result[field], field)
        if result[field]:
            commit_mapping.validate_wis(result[field])
    rows = result[_FIELDS[4]]
    if not isinstance(rows, list):
        raise ValueError("adoptを延期したAWIとcommitはJSON配列で指定する")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"awi", "commit"}:
            raise ValueError("延期の各項目はawiとcommitだけを持つobjectで指定する")
        if not isinstance(row["awi"], str) or not isinstance(row["commit"], str):
            raise ValueError("延期のawiとcommitは文字列で指定する")
        commit_mapping.validate_wis([row["awi"]])
        pair = (row["awi"], row["commit"])
        if pair in seen:
            raise ValueError(f"延期のAWIとcommitの組が重複している: {row['awi']}")
        seen.add(pair)
    for name in result[_FIELDS[5]]:
        path_value = pathlib.PurePosixPath(name)
        if path_value.is_absolute() or ".." in path_value.parts or "\\" in name or path_value.as_posix() != name:
            raise ValueError(f"変更文書はリポジトリ相対パスで指定する: {name}")
    return result


def _oid(repo: pathlib.Path, value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
        raise ValueError(f"commitは7文字以上の一意なOIDで指定する: {value}")
    return commit_mapping.resolve_commit(repo, value)


def _dispositions(manifest: dict[str, Any], completed: list[str], leave_open: list[str]) -> dict[str, set[str]]:
    expected: dict[str, set[str]] = {"adopt": set(), "reject": set(), "defer": set(), "open": set(leave_open)}
    parser = argparse.ArgumentParser(prog="atk wi", exit_on_error=False)
    wi_cli.build_parser(parser)
    names = [row["name"] for row in manifest["wis"]]
    if len(set(names)) != len(names) or not set(leave_open) <= set(names):
        raise ValueError("manifestのWIが重複しているか、leave-openに対象外WIがある")
    for group in ("plans", "wis"):
        for row in manifest[group]:
            for index, _argv in enumerate(row["commands"]):
                if f"{group}:{row['name']}:{index}" not in completed:
                    raise ValueError(f"統合記録に未完了の最終化操作がある: {group}:{row['name']}:{index}")
    for row in manifest["wis"]:
        if not row["commands"]:
            if row["name"] not in expected["open"]:
                expected["defer"].add(row["name"])
            continue
        if row["name"] in expected["open"]:
            raise ValueError(f"leave-openに終端操作を持つWIがある: {row['name']}")
        dispositions: set[str] = set()
        for argv in row["commands"]:
            for index in range(1, len(argv) - 1):
                if argv[index] != "wi":
                    continue
                module_launch = index >= 2 and argv[index - 2 : index] == ["-m", "agent_toolkit.atk"]
                if pathlib.Path(argv[index - 1]).name not in {"atk", "atk.cmd", "atk.py"} and not module_launch:
                    continue
                if argv[index + 1] not in {"adopt", "reject"}:
                    continue
                try:
                    parsed = parser.parse_args(argv[index + 1 :])
                except (argparse.ArgumentError, SystemExit) as error:
                    raise ValueError(f"manifestのWI終端argvを公開parserで受理できない: {row['name']}") from error
                if parsed.all or set(parsed.filenames) != {row["name"]}:
                    raise ValueError(f"WI終端argvがその項目だけを対象にしていない: {row['name']}")
                dispositions.add(parsed.wi_subcommand)
        if len(dispositions) != 1:
            raise ValueError(f"manifestから公開atk wi adopt/rejectの終端区分を確定できない: {row['name']}")
        expected[dispositions.pop()].add(row["name"])
    return expected


def _plan_sources(manifest: dict[str, Any], supplied: list[pathlib.Path]) -> list[pathlib.Path]:
    explicit = {path.name: path for path in supplied}
    if len(explicit) != len(supplied) or set(explicit) - {row["name"] for row in manifest["plans"]}:
        raise ValueError("planに同名または対象外の計画がある")
    result = []
    for row in manifest["plans"]:
        path = explicit.get(row["name"])
        if path is None and row.get("saved"):
            path = pathlib.Path(row["saved"])
        if path is None:
            candidates = sorted(locations.new_plans_root().rglob(row["name"]))
            if not candidates:
                candidates = [locations.working_plans_root() / row["name"]]
            if len(candidates) != 1:
                raise ValueError(f"保存計画を一意に解決できない。--planで原本の所在を指定する: {row['name']}")
            path = candidates[0]
        if not path.is_absolute() or not path.is_file() or path.name != row["name"]:
            raise ValueError(f"計画原本が存在しない。--planで保存先の絶対パスを指定する: {row['name']}")
        result.append(path)
    if not result:
        handoff = pathlib.Path(manifest["handoff"])
        if not handoff.is_absolute() or not handoff.is_file():
            raise ValueError("manifestの引き継ぎ記録が存在しない")
        result.append(handoff)
    return result


def check(args: argparse.Namespace) -> dict[str, Any]:
    """原本から期待する集合を導出し、機械的に成立した条件と残る意味確認を返す。"""
    for path in (args.record, args.reply, *(args.plan or []), *(args.previous_reply or [])):
        if not path.is_absolute():
            raise ValueError(f"入力は絶対パスで指定する: {path}")
    record = json.loads(args.record.read_text(encoding="utf-8"), object_pairs_hook=_object)
    state = record["integration"]
    if state["phase"] != "finalized":
        raise ValueError("lane integrateの最終化が完了していない。同じ統合操作から再開する")
    if "selection-mark" not in state["completed"]:
        raise ValueError("統合記録に選定原本への統合完了記録がない")
    identity = state["input"]
    manifest = identity["manifest"]
    repo = pathlib.Path(identity["target"])
    branch = identity["branch"]
    if not repo.is_absolute() or branch != record["base_branch"]:
        raise ValueError("統合先が所有登録と一致しない")
    received = _reply(args.reply)
    head = command.output(["rev-parse", "--verify", "--end-of-options", f"refs/heads/{branch}^{{commit}}"], repo)
    if _oid(repo, received[_FIELDS[0]]) != head or state["integrated_head"] != head:
        raise ValueError("返却HEAD・統合記録・現在の統合先branchのHEADが一致しない")
    plans = [row["name"] for row in manifest["plans"]]
    if received[_FIELDS[1]] != plans or len(set(plans)) != len(plans):
        raise ValueError("保存した計画ファイルがmanifestの計画順序と一致しない")
    expected = _dispositions(manifest, state["completed"], args.leave_open or [])
    for kind, field in (("adopt", _FIELDS[2]), ("reject", _FIELDS[3])):
        if set(received[field]) != expected[kind]:
            raise ValueError(f"{field}の集合が統合前のmanifestと一致しない")
    if {row["awi"] for row in received[_FIELDS[4]]} != expected["defer"]:
        raise ValueError("延期AWIの集合がmanifestとleave-openの指定から得た集合と一致しない")
    sources = _plan_sources(manifest, args.plan or [])
    allowed = {row["name"] for row in manifest["wis"]}
    commits: dict[str, set[str]] = {wi: set() for wi in expected["defer"]}
    for source in sources:
        events = commit_mapping.read_events(source, source.read_text(encoding="utf-8"))
        for oid, wis in commit_mapping.read_mapping(repo, events, allowed).items():
            for wi in wis & expected["defer"]:
                commits[wi].add(commit_mapping.resolve_commit(repo, oid))
    actual: dict[str, set[str]] = {wi: set() for wi in expected["defer"]}
    empty: list[str] = []
    for row in received[_FIELDS[4]]:
        if row["commit"]:
            oid = _oid(repo, row["commit"])
            if oid in actual[row["awi"]]:
                raise ValueError(f"延期のAWIと解決後commitの組が重複している: {row['awi']}")
            actual[row["awi"]].add(oid)
        else:
            empty.append(row["awi"])
    if actual != commits or len(empty) != len(set(empty)) or set(empty) != {wi for wi, oids in commits.items() if not oids}:
        raise ValueError("延期AWIのcommitまたは空文字列が計画・引き継ぎ記録の現在のcommit対応と一致しない")
    changed: set[str] = set()
    if not identity["no_merge"]:
        invocation = [
            sys.executable,
            str(run_script.registered_script_path("agent-doc-changes")),
            "--repo",
            str(repo),
            state["target_head"],
            head,
        ]
        completed = process_tree.run_with_timeout(
            invocation,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
            env=plugin_environment.direct_environment(pathlib.Path(__file__).resolve().parents[3], os.environ),
        )
        if completed.returncode:
            raise ValueError(f"変更文書の取得が失敗した: {completed.stderr.strip()}")
        changed.update(_strings(json.loads(completed.stdout), _FIELDS[5]))
    for previous in args.previous_reply or []:
        changed.update(_reply(previous)[_FIELDS[5]])
    if set(received[_FIELDS[5]]) != changed:
        raise ValueError("変更したエージェント向け文書がGit差分と前回返却の和集合に一致しない")
    return {"status": "受領可能", "head": head, "plans": plans, "documents": sorted(changed), "empty_commit_wis": sorted(empty)}


def main(argv: list[str] | None = None) -> int:
    """既存lane recordと逐語返却を受け取り、合否と次の操作を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--record", type=pathlib.Path, metavar="PATH", required=True, help="lane create/integrateの既存record_path。絶対パス。"
    )
    parser.add_argument("--reply", type=pathlib.Path, metavar="PATH", required=True, help="逐語保存した統合返却。絶対パス。")
    parser.add_argument(
        "--plan",
        type=pathlib.Path,
        metavar="PATH",
        action="append",
        default=None,
        help="所在の指定が必要な計画原本。絶対パス。",
    )
    parser.add_argument("--leave-open", action="append", default=None, help="空commandsのうち延期対象でもない終端しないWI。")
    parser.add_argument(
        "--previous-reply",
        type=pathlib.Path,
        metavar="PATH",
        action="append",
        default=None,
        help="前回受理した返却原文。絶対パス。",
    )
    args = parser.parse_args(argv)
    try:
        result = check(args)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        next_action.report(f"統合返却を受領できない: {error}", next_action=_RETRY)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
