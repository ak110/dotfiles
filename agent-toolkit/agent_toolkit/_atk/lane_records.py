"""レーン資源の所有記録・選定状態と固有手順の実行。"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import typing
from collections.abc import Generator

lazy import psutil
lazy import yaml

from agent_toolkit._atk import managed_temp, run_command
from agent_toolkit._common import atomic_file, file_lock, state_paths
from agent_toolkit._git import command as git_command
from agent_toolkit._git import worktree_exclude
from agent_toolkit._git import worktrees as git_worktrees
from agent_toolkit._plan import selection

SESSION_ENV = "AGENT_TOOLKIT_LANE_SESSION_ID"

ID = re.compile(r"[A-Za-z0-9_-]+\Z")

LANE = re.compile(r"lane-[0-9]{2}\Z")

TIMEOUT = 120


def record_errors() -> tuple[type[BaseException], ...]:
    """所有登録の読取・判定・回収で保持対象へ変換する例外型を返す。"""
    return (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        psutil.Error,
        managed_temp.ManagedTempError,
    )


def current_session_id() -> str | None:
    """同じ実行の作成と終了操作が使う識別子を解決する。"""
    for name in (SESSION_ENV, "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        value = os.environ.get(name)
        if value and ID.fullmatch(value):
            return value
    return None


def session_directory(session_id: str) -> pathlib.Path:
    if not ID.fullmatch(session_id):
        raise ValueError("セッション識別子は英数字・ハイフン・アンダースコアで指定する")
    return state_paths.state_dir() / "lanes" / session_id


@contextlib.contextmanager
def locked(session_id: str) -> Generator[pathlib.Path]:
    directory = session_directory(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "operation.lock").open("a+", encoding="utf-8") as stream:
        file_lock.acquire_lock(stream)
        try:
            yield directory
        finally:
            file_lock.release_lock(stream)


@contextlib.contextmanager
def lane_locked(record_path: pathlib.Path, *, blocking: bool = True) -> Generator[None]:
    """同じレーンの所有登録を変える操作（統合・作成・回収）を互いに排他する。

    統合はrebase・検証・最終化の間この排他だけを保持し、所有session共通の`locked`を持ち続けない。
    他のレーンの統合と短い区間の操作を待たせないためである。
    取得順はレーン単位、統合先単位、所有session単位の順に固定し、逆順の取得を作らない。
    """
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.with_suffix(".lock").open("a+", encoding="utf-8") as stream:
        try:
            file_lock.acquire_lock(stream, blocking=blocking)
        except OSError as error:
            if blocking:
                raise
            raise ValueError(f"{record_path.stem}の統合が実行中。統合の終了後に同じ操作を再実行する") from error
        try:
            yield
        finally:
            file_lock.release_lock(stream)


@contextlib.contextmanager
def target_locked(record: dict[str, typing.Any], branch: str) -> Generator[None]:
    """統合先HEADの一致の確認と更新を、同じ統合先へ統合する全レーンで直列にする。

    所有sessionに依存させないのは、別sessionの統合が同じ統合先を更新する場合も直列にするためである。
    保持区間はGitの数操作に限り、検証と人の判断を含めない。
    置き場所を`lanes/`の外にするのは、`lanes/`直下をsessionのディレクトリとして走査する処理があるためである。
    """
    key = hashlib.sha256(f"{record['common_dir']}\0{branch}".encode()).hexdigest()[:32]
    directory = state_paths.state_dir() / "lane-targets"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{key}.lock").open("a+", encoding="utf-8") as stream:
        file_lock.acquire_lock(stream)
        try:
            yield
        finally:
            file_lock.release_lock(stream)


def save(path: pathlib.Path, record: dict[str, typing.Any]) -> None:
    atomic_file.atomic_write(path, json.dumps(record, ensure_ascii=False) + "\n")


def git(repo: pathlib.Path, *argv: str) -> str:
    result = git_command.run(list(argv), repo, capture_output=True, text=True, check=False, timeout=TIMEOUT)
    if result.returncode:
        raise ValueError(f"git {argv[0]}が終了{result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def common(repo: pathlib.Path) -> str:
    return git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")


def branches(repo: pathlib.Path) -> list[str]:
    return [
        ref.removeprefix("refs/heads/") for ref in git(repo, "for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
    ]


def worktrees(repo: pathlib.Path) -> dict[str, str]:
    """登録済みworktreeの解決済みパスからbranch名（branchを持たない登録は空文字）への対応を返す。"""
    return {str(entry.path.resolve()): entry.branch or "" for entry in git_worktrees.list_worktrees(repo, timeout=TIMEOUT)}


@dataclasses.dataclass(frozen=True)
class ResourceState:
    """存在する主repoから照会したレーン資源の状態。"""

    worktree_path: bool
    worktree_registered: bool
    branch: bool

    @property
    def absent(self) -> bool:
        """回収で守る実体が全て無いと確定したかを返す。"""
        return not (self.worktree_path or self.worktree_registered or self.branch)


def resource_state(record: dict[str, typing.Any]) -> ResourceState:
    """消え得るworktreeを起点にせず、資源の有無を一度に照会する。"""
    repo = pathlib.Path(record["repo"])
    if common(repo) != record["common_dir"]:
        raise ValueError("repoのGit共通ディレクトリが所有記録と異なる")
    worktree = pathlib.Path(record["worktree"])
    registered = str(worktree) in worktrees(repo)
    return ResourceState(os.path.lexists(worktree), registered, record["branch"] in branches(repo))


def branch_is_integrated(record: dict[str, typing.Any], repo: pathlib.Path) -> bool:
    """最終化時の入力HEADを保つbranchは、統合先の履歴書換え後も統合済みとして扱う。"""
    integration = record.get("integration")
    if isinstance(integration, dict) and integration.get("phase") == "finalized":
        new_head = integration.get("new_head")
        if isinstance(new_head, str) and git(repo, "rev-parse", f"refs/heads/{record['branch']}") == new_head:
            return True
    result = git_command.run(
        ["merge-base", "--is-ancestor", f"refs/heads/{record['branch']}", f"refs/heads/{record['base_branch']}"],
        repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
    )
    if result.returncode == 1:
        raise ValueError("レーンbranchに統合先branchへ含まれないcommitがある。成果を統合してから同じlane deleteを再実行する")
    if result.returncode:
        raise ValueError(f"git merge-baseが終了{result.returncode}: {result.stderr.strip()}")
    return True


def main_worktree(repo: pathlib.Path) -> pathlib.Path:
    """Linked worktreeから呼んでも、同じGitリポジトリの主作業ツリーへ解決する。"""
    main = git_worktrees.list_worktrees(repo, timeout=TIMEOUT)[0]
    if main.bare:
        raise ValueError("repoが主作業ツリーを持たない。--worktreeで作成先を指定する")
    return main.path.resolve()


def exclude_placement(main: pathlib.Path) -> None:
    """主作業ツリーの状態へ固定作成先が未追跡として現れないよう、info/excludeへ加える。"""
    try:
        worktree_exclude.ensure_excluded(main)
    except worktree_exclude.ExcludeError as error:
        raise ValueError(f"固定作成先の除外を設定できない（{error}）。`git status`と`info/exclude`を確認する") from error


def fixed_worktree(repo: pathlib.Path, lane: str) -> pathlib.Path:
    # --worktreeを省略した作成先は主作業ツリーからの相対で固定し、session・日時・呼出し元を識別へ含めない。
    return main_worktree(repo).joinpath(*worktree_exclude.WORKTREE_PARENT.parts, f"process-wi-{lane}")


def read_selection(record: dict[str, typing.Any]) -> dict[str, typing.Any] | None:
    """選定原本からこのレーンのWI集合と統合状態を読む。原本が存在しなければNoneを返す。"""
    try:
        text = pathlib.Path(record["selection_file"]).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    document = yaml.safe_load(text)
    return {
        "lane_wis": sorted(
            str(row[selection.WI_KEY])
            for row in selection.decisions(document) or []
            if isinstance(row, dict) and row.get(selection.LANE_KEY) == record["lane"] and selection.WI_KEY in row
        ),
        "integrated": record["lane"] in selection.integrated_lanes(document),
    }


def selection_state(record: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """選定原本が読めれば所有登録の保持値を更新して返し、失効していれば保持値を返す。

    選定原本はmanaged-tempの7日保持へ委ねられ、所有登録は資源の回収まで残る。
    原本の消失後も再開と回収を判定できるようにするため、読めた時点の内容を所有登録へ写して保持する。
    """
    current = read_selection(record)
    if current is not None:
        record["selection"] = current
        return current
    held = record.get("selection")
    if not isinstance(held, dict):
        raise ValueError(
            f"選定原本{record['selection_file']}が失効し、所有登録に選定情報が無い。"
            "成果のbranchを保持して作成先を解放するには"
            f"`atk lane recover --session-id {record['session_id']} --lane {record['lane']}`を実行する"
        )
    return held


def require_selection(record: dict[str, typing.Any]) -> None:
    """今回渡された選定原本から保持値を再生成する。再利用でも前回の選定を引き継がない。"""
    current = read_selection(record)
    if current is None:
        raise ValueError(f"選定YAMLが存在しない: {record['selection_file']}。選定結果の出力先を指定する")
    record["selection"] = current


def recovered(record: dict[str, typing.Any]) -> bool:
    recovery = record.get("recovery")
    return isinstance(recovery, dict) and recovery.get("phase") == "done"


def integration_directory(record: dict[str, typing.Any]) -> pathlib.Path:
    """統合の対応表と実行記録を、所有登録と同じ寿命の状態ディレクトリへ置く。"""
    return pathlib.Path(record["record_path"]).with_name(f"{record['lane']}-integration")


def persist_observed(record: dict[str, typing.Any], key: str, observed: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """実行記録と両出力を状態ディレクトリへ複製し、複製先を指す記録を返す。

    run_commandの保存先はmanaged-tempの7日保持であり、検収待ちの間に失効し得る。
    """
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", key) + "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    directory = integration_directory(record) / name
    directory.mkdir(parents=True, exist_ok=True)
    persisted = dict(observed)
    for field in ("record_path", "stdout_path", "stderr_path"):
        source = observed.get(field)
        if isinstance(source, str) and pathlib.Path(source).is_file():
            # 実行ごとの保存先の名前を保ち、再実行の記録を前回の記録と区別する。
            destination = directory / pathlib.Path(source).parent.name / pathlib.Path(source).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if pathlib.Path(source).resolve() != destination.resolve():
                shutil.copyfile(source, destination)
            persisted[field] = str(destination)
    if isinstance(persisted.get("record_path"), str):
        atomic_file.atomic_write(pathlib.Path(persisted["record_path"]), json.dumps(persisted, ensure_ascii=False) + "\n")
    return persisted


def materials_missing(state: dict[str, typing.Any]) -> bool:
    """検収待ちの対応表か検証記録が失われているかを返す。"""
    mapping = state.get("rewrite_map")
    if not isinstance(mapping, str) or not pathlib.Path(mapping).is_file():
        return True
    for key, observed in state.get("results", {}).items():
        if key.startswith("verification-") and isinstance(observed, dict):
            for field in ("record_path", "stdout_path", "stderr_path"):
                value = observed.get(field)
                if isinstance(value, str) and not pathlib.Path(value).is_file():
                    return True
    return False


def commands(path: pathlib.Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) - {"create", "prepare", "delete"}:
        raise ValueError("手順ファイルはcreate・prepare・deleteのargvを持つJSON objectで指定する")
    for argv in value.values():
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("各手順は空でない文字列のargv配列で指定する")
    return value


def procedure(record: dict[str, typing.Any], phase: str) -> None:
    argv = record["commands"].get(phase)
    if argv is None:
        return
    fields = {name: record[name] for name in ("repo", "worktree", "branch", "lane")}
    expanded = []
    for argument in argv:
        for name, value in fields.items():
            argument = argument.replace("{" + name + "}", value)
        expanded.append(argument)
    observed, code, failure = run_command.execute(expanded, pathlib.Path(record["repo"]), record["procedure_timeout"])
    record.setdefault("procedure_results", {})[phase] = observed
    save(pathlib.Path(record["record_path"]), record)
    if code:
        raise ValueError(f"{phase}手順が終了{code}: {failure or observed['record_path']}。手順を直して同じ操作を再実行する")


def hook_diagnostics(record: dict[str, typing.Any], event: str) -> pathlib.Path:
    """操作ごとの診断を期限付き領域へ置き、既存所有記録へ最新の所在を残す。"""
    directory = managed_temp.create_managed_temp("worktree-hook")
    record.setdefault("hook_diagnostics", {})[event] = str(directory)
    save(pathlib.Path(record["record_path"]), record)
    return directory


def positive_timeout(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("上限秒数は有限の正の値で指定する")
    return seconds


def list_resources(session_id: str | None = None) -> dict[str, typing.Any]:
    """前回の残存資源と回収結果を読む。新しい所有登録や削除を行わない。"""
    root = state_paths.state_dir() / "lanes"
    directories = [session_directory(session_id)] if session_id else sorted(root.iterdir()) if root.exists() else []
    records: list[dict[str, typing.Any]] = []
    results: list[dict[str, typing.Any]] = []
    errors: list[dict[str, typing.Any]] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("登録または回収結果はJSON objectである必要がある")
                if path.name == "cleanup-result.json" and "result_path" in value:
                    observed_path = pathlib.Path(value["result_path"])
                    if not observed_path.is_absolute():
                        raise ValueError("回収結果の保存先は絶対パスである必要がある")
                    if not observed_path.is_file():
                        results.append({"record_path": str(path), "record": value, "expired": True})
                        continue
                    value = json.loads(observed_path.read_text(encoding="utf-8"))
                target = results if path.name == "cleanup-result.json" else records
                target.append({"record_path": str(path), "record": value})
            except (OSError, UnicodeError, ValueError) as error:
                errors.append({"record_path": str(path), "reason": str(error)})
    return {"records": records, "results": results, "errors": errors}
