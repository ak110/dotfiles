# PYTHON_ARGCOMPLETE_OK
"""GitHub Actionsのrelease.yamlを安全に起動するreleaserコマンド。

引数として`patch`/`minor`/`major`を渡すと、未コミット検査・既定ブランチ確認・
push・CI完了待機を経てrelease.yamlをworkflow_dispatchで起動する。
引数無しの場合はヘルプと未リリースコミット一覧を表示する。
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)

# push直後はGitHub APIへのrun登録に数秒〜十数秒の遅延がある。
# 60秒待っても出現しない場合はブランチフィルター等で対象外と判断してスキップする。
_CI_RUN_APPEAR_TIMEOUT_SEC = 60
_CI_RUN_APPEAR_INTERVAL_SEC = 5
# 一般的なCIの実行時間を考慮し、完了まで最大30分待つ。
_CI_COMPLETE_TIMEOUT_SEC = 1800
_CI_COMPLETE_INTERVAL_SEC = 15
# workflow_dispatch起動後に新規runが確認できるまでの待機上限。
_DISPATCH_RUN_APPEAR_TIMEOUT_SEC = 120
_DISPATCH_RUN_APPEAR_INTERVAL_SEC = 5

_BUMP_CHOICES = ("patch", "minor", "major")
_RELEASE_WORKFLOW_FILENAME = "release.yaml"
_RELEASE_WORKFLOW_RELATIVE = Path(".github/workflows") / _RELEASE_WORKFLOW_FILENAME
# GitHub Actionsの実行中ステータス。これらが残っている間は完了扱いしない。
_IN_PROGRESS_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
# 成功扱いするconclusion。skipped/neutralも障害扱いにしない。
_ACCEPTABLE_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


class _ReleaserError(Exception):
    """releaserが扱う想定済みエラー。`main()`で捕捉し`sys.exit(1)`へ集約する。"""


def main() -> None:
    """GitHub Actionsのリリースワークフローを安全に起動するエントリポイント。"""
    setup_logging(fmt="%(levelname)s: %(message)s")
    parser = _build_parser()
    enable_completion(parser)
    args = parser.parse_args()
    try:
        if args.bump is None:
            _show_unreleased(parser)
        else:
            _run_release_flow(args.bump.upper())
    except _ReleaserError as e:
        logger.error("%s", e)
        sys.exit(1)
    sys.exit(0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="release.yamlワークフローを安全に起動する。",
    )
    parser.add_argument(
        "bump",
        nargs="?",
        choices=_BUMP_CHOICES,
        help="バージョン番号の更新タイプ。省略時はヘルプと未リリースコミット一覧を表示する。",
    )
    return parser


def _run_release_flow(bump: str) -> None:
    git_root = _get_git_root()
    _ensure_default_branch()
    _ensure_clean_working_tree()
    _push_to_remote()
    _wait_for_ci(git_root)

    workflow_path = git_root / _RELEASE_WORKFLOW_RELATIVE
    if not workflow_path.exists():
        logger.info("%sが見つからないためdispatch起動を省略する。", _RELEASE_WORKFLOW_FILENAME)
        return

    _check_release_workflow(workflow_path)
    last_run_id = _get_latest_release_run_id()
    _dispatch_release_workflow(bump)
    new_run_id = _wait_for_new_release_run(last_run_id)
    _watch_run(new_run_id)
    _sync_local_repo()


def _show_unreleased(parser: argparse.ArgumentParser) -> None:
    """ヘルプと未リリースコミット一覧を表示する（副作用なし）。"""
    parser.print_help()
    if not _is_git_repo():
        return
    print()
    tag = _get_latest_release_tag()
    if tag is None:
        print("直近のリリースタグが見つかりません（未リリースコミット一覧の表示をスキップ）。")
        return
    print(f"直近のリリースタグ: {tag}")
    print(f"未リリースコミット ({tag}..HEAD):")
    result = subprocess.run(
        ["git", "log", "--oneline", "--decorate", f"{tag}..HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout.rstrip("\n")
    if output:
        print(output)
    else:
        print("（未リリースコミット無し）")


def _is_git_repo() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _get_git_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _ReleaserError("gitリポジトリ内で実行してください。")
    return Path(result.stdout.strip())


def _get_latest_release_tag() -> str | None:
    """HEADから到達可能な最新の`v[0-9]*`タグを返す。無ければNone。"""
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match=v[0-9]*"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _ensure_default_branch() -> None:
    """既定ブランチ（origin/HEADが指すブランチ）で実行されていることを確認する。"""
    current = _get_current_branch()
    default = _get_default_branch()
    if current != default:
        raise _ReleaserError(f"既定ブランチ '{default}' で実行してください（現在: '{current}'）。")


def _get_current_branch() -> str:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    )
    branch = result.stdout.strip()
    if not branch:
        raise _ReleaserError("HEADが分離されています。既定ブランチへチェックアウトしてください。")
    return branch


def _get_default_branch() -> str:
    """origin/HEADが指す既定ブランチ名を返す（例: 'master'）。"""
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _ReleaserError("origin/HEADを解決できません。`git remote set-head origin --auto`を試してください。")
    name = result.stdout.strip()
    return name.removeprefix("origin/")


def _ensure_clean_working_tree() -> None:
    """作業ツリーがクリーンであることを確認する（追跡外も対象）。"""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    if result.stdout.strip():
        raise _ReleaserError("未コミットまたは追跡外の変更があります。コミット・stash・削除してから再実行してください。")


def _push_to_remote() -> None:
    """未プッシュコミットがあればpushする。"""
    result = subprocess.run(
        ["git", "rev-list", "@{u}..HEAD", "--count"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _ReleaserError("上流ブランチが設定されていません。`git push -u origin <branch>`で初回pushを行ってください。")
    count = int(result.stdout.strip() or "0")
    if count == 0:
        logger.info("未プッシュコミットはありません。")
        return
    logger.info("未プッシュコミット%d件をpushする。", count)
    subprocess.run(["git", "push"], check=True)


def _wait_for_ci(git_root: Path) -> None:
    """release.yaml以外のworkflow runが完了するまで待機する。"""
    if not _has_non_release_workflow(git_root):
        logger.info("release.yaml以外のworkflowが無いためCI待機をスキップする。")
        return
    sha = _get_head_sha()
    release_name = _get_release_workflow_name()

    runs = _wait_for_non_release_runs(sha, release_name)
    if runs is None:
        # ブランチフィルターやpath-filterで対象外の場合、push後にrunが登録されない。
        # CIなしと同等の扱いとし、警告のみで処理を継続する。
        logger.warning(
            "コミット %s に対するCI runが見つかりません。"
            "ブランチフィルター等の対象外の可能性があるためCI待機をスキップします。",
            sha[:7],
        )
        return

    final_runs = _wait_for_runs_complete(sha, release_name)
    _check_runs_success(final_runs)


def _has_non_release_workflow(git_root: Path) -> bool:
    """`.github/workflows/`にrelease.yaml以外のワークフローファイルが存在するか判定する。"""
    workflows_dir = git_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return False
    return any(
        p.is_file() and p.suffix in {".yaml", ".yml"} and p.name != _RELEASE_WORKFLOW_FILENAME for p in workflows_dir.iterdir()
    )


def _get_head_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _get_release_workflow_name() -> str:
    """`release.yaml`ワークフローの`name`をGitHub APIから取得する。

    ワークフローがGitHub側へ未登録の場合はファイル名で代替する
    （workflowName比較で`release.yaml`は使われないため副作用なし）。
    """
    result = subprocess.run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"/repos/:owner/:repo/actions/workflows/{_RELEASE_WORKFLOW_FILENAME}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return _RELEASE_WORKFLOW_FILENAME
    data = json.loads(result.stdout)
    return str(data.get("name") or _RELEASE_WORKFLOW_FILENAME)


def _list_runs_for_commit(sha: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            f"--commit={sha}",
            "--json=databaseId,status,conclusion,workflowName,name",
            "--limit=50",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _wait_for_non_release_runs(sha: str, release_name: str) -> list[dict[str, Any]] | None:
    """非release runが少なくとも1件出現するまでpollする。タイムアウトでNone。"""
    deadline = time.monotonic() + _CI_RUN_APPEAR_TIMEOUT_SEC
    while time.monotonic() < deadline:
        runs = _list_runs_for_commit(sha)
        non_release = [r for r in runs if r.get("workflowName") != release_name]
        if non_release:
            return non_release
        logger.info("CI runの登録待機中...")
        time.sleep(_CI_RUN_APPEAR_INTERVAL_SEC)
    return None


def _wait_for_runs_complete(sha: str, release_name: str) -> list[dict[str, Any]]:
    """非release runが全て完了するまで待機する。"""
    deadline = time.monotonic() + _CI_COMPLETE_TIMEOUT_SEC
    while time.monotonic() < deadline:
        runs = _list_runs_for_commit(sha)
        non_release = [r for r in runs if r.get("workflowName") != release_name]
        in_progress = [r for r in non_release if r.get("status") in _IN_PROGRESS_STATUSES]
        if not in_progress:
            return non_release
        logger.info("CI完了待機中（進行中%d件）...", len(in_progress))
        time.sleep(_CI_COMPLETE_INTERVAL_SEC)
    raise _ReleaserError("CI完了待機がタイムアウトしました。")


def _check_runs_success(runs: list[dict[str, Any]]) -> None:
    """workflowごとに最新runのconclusionを検査し、失敗があればエラーにする。"""
    latest_by_workflow: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = str(run.get("workflowName") or "")
        existing = latest_by_workflow.get(name)
        existing_id = int(existing.get("databaseId") or 0) if existing else -1
        run_id = int(run.get("databaseId") or 0)
        if run_id > existing_id:
            latest_by_workflow[name] = run
    failed = [
        (name, str(r.get("conclusion") or "unknown"))
        for name, r in latest_by_workflow.items()
        if r.get("conclusion") not in _ACCEPTABLE_CONCLUSIONS
    ]
    if failed:
        details = ", ".join(f"{n}: {c}" for n, c in failed)
        raise _ReleaserError(f"CIに失敗しています: {details}")
    logger.info("CIは全て成功しています。")


def _check_release_workflow(workflow_path: Path) -> None:
    text = workflow_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    _validate_release_workflow_dict(data)


def _validate_release_workflow_dict(data: Any) -> None:
    """`release.yaml`のパース済みデータを検証する。

    PyYAMLは`on:`キーをYAML 1.1仕様の真偽値`True`へ強制変換するため、
    `on`文字列キーと`True`キーの両方に対応する。
    """
    if not isinstance(data, dict):
        raise _ReleaserError("release.yamlのトップレベルがマップではありません。")
    on_section = data.get("on")
    if on_section is None:
        on_section = data.get(True)
    if not isinstance(on_section, dict):
        raise _ReleaserError("release.yamlに`on`セクションが見つかりません。")
    workflow_dispatch = on_section.get("workflow_dispatch")
    if not isinstance(workflow_dispatch, dict):
        raise _ReleaserError("release.yamlに`workflow_dispatch`がありません。")
    inputs = workflow_dispatch.get("inputs")
    if not isinstance(inputs, dict):
        raise _ReleaserError("release.yamlの`workflow_dispatch.inputs`がありません。")
    bump = inputs.get("bump")
    if not isinstance(bump, dict):
        raise _ReleaserError("release.yamlに`bump`入力がありません。")
    options = bump.get("options")
    if not isinstance(options, list):
        raise _ReleaserError("release.yamlの`bump.options`が不正です。")
    required = {"PATCH", "MINOR", "MAJOR"}
    actual = {str(o) for o in options}
    missing = required - actual
    if missing:
        raise _ReleaserError(f"release.yamlの`bump.options`に必要な値がありません: {sorted(missing)}")


def _list_release_runs() -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            f"--workflow={_RELEASE_WORKFLOW_FILENAME}",
            "--json=databaseId,status,createdAt",
            "--limit=5",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _get_latest_release_run_id() -> int | None:
    """dispatch前の状態確認用。`release.yaml`の最新run IDを返す。"""
    runs = _list_release_runs()
    if not runs:
        return None
    db_id = runs[0].get("databaseId")
    if db_id is None:
        return None
    return int(db_id)


def _dispatch_release_workflow(bump: str) -> None:
    logger.info("release.yamlをworkflow_dispatchで起動する（bump=%s）。", bump)
    subprocess.run(
        ["gh", "workflow", "run", _RELEASE_WORKFLOW_FILENAME, f"--field=bump={bump}"],
        check=True,
    )


def _wait_for_new_release_run(last_id: int | None) -> int:
    """前回IDより新しいrelease runが現れるまでpollする。"""
    deadline = time.monotonic() + _DISPATCH_RUN_APPEAR_TIMEOUT_SEC
    while time.monotonic() < deadline:
        for run in _list_release_runs():
            db_id = run.get("databaseId")
            if db_id is None:
                continue
            run_id = int(db_id)
            if last_id is None or run_id > last_id:
                logger.info("新規release run検出: id=%s", run_id)
                return run_id
        time.sleep(_DISPATCH_RUN_APPEAR_INTERVAL_SEC)
    raise _ReleaserError("dispatch後の新規runが検出できませんでした。")


def _watch_run(run_id: int) -> None:
    """`gh run watch --exit-status` でrunの完了を待機する。"""
    logger.info("release run %s の完了を待機する。", run_id)
    result = subprocess.run(
        ["gh", "run", "watch", str(run_id), "--exit-status"],
        check=False,
    )
    if result.returncode != 0:
        raise _ReleaserError(f"release run {run_id} が失敗しました。")
    logger.info("release run %s が成功しました。", run_id)


def _sync_local_repo() -> None:
    logger.info("ローカルリポジトリを最新化する。")
    subprocess.run(["git", "fetch", "--tags", "--prune"], check=True)
    subprocess.run(["git", "pull", "--ff-only"], check=True)


if __name__ == "__main__":
    main()
