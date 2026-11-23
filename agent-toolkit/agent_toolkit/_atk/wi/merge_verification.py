from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from agent_toolkit._atk import outcome, wait_ci_forge, wait_ci_polling
from agent_toolkit._atk.wi import frontmatter, sync
from agent_toolkit._atk.wi.mutations.targets import resolve_editable_targets
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command as git_command

_SHA = re.compile(r"[a-f0-9]{40}")
_TIMEOUT = 15.0
_RETRY = "対象ブランチと完全OIDのCIを確認し、必要なジョブが成功した後で同じ操作を再実行する"


def merge_input(text: str) -> tuple[object, object, object, object] | None:
    parsed = frontmatter.parse_frontmatter(text)
    if parsed is None:
        return None
    data = parsed[0]
    if "merge_branch" not in data and "merge_commit" not in data:
        return None
    return data.get("target_repo"), data.get("merge_branch"), data.get("merge_commit"), data.get("merge_local_verification")


def _check_local_evidence(reference: object, repository: str, sha: str) -> str:
    if not isinstance(reference, str) or not pathlib.Path(reference).is_absolute():
        raise ActionableError(
            "対象OIDで必要なCIを動かせない設定だが、同等の検証結果が無い",
            next_action="同じOIDで同等の検証を実行し、merge_local_verificationへ検証記録の絶対パスを指定する",
        )
    try:
        record = json.loads(pathlib.Path(reference).read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or record.get("target_repo") != repository
            or record.get("commit") != sha
            or type(record.get("exit_code")) is not int
            or record["exit_code"] != 0
            or not isinstance(argv := record.get("argv"), list)
            or not argv
            or not all(isinstance(value, str) and value for value in argv)
            or not isinstance(output := record.get("output"), str)
            or not pathlib.Path(output).is_absolute()
            or not pathlib.Path(output).is_file()
        ):
            raise ValueError("リポジトリ、OID、終了値、コマンド、出力のいずれかが一致しない")
    except (OSError, ValueError, TypeError) as error:
        raise ActionableError(f"同等の検証記録を受理できない: {error}", next_action=_RETRY) from error
    return f"CI非対象。対象OID {sha} の同等検証記録: {reference}"


def _configured_checks_executed(jobs: Sequence[Mapping[str, Any]]) -> bool:
    verification_jobs = [job for job in jobs if job.get("conclusion") == "success" and isinstance(job.get("name"), str)]
    if not verification_jobs:
        return False
    return not any("(non-owner)" in job["name"] for job in verification_jobs)


def _might_push(on: object, branch: str) -> bool:
    if isinstance(on, str):
        return on == "push"
    if isinstance(on, list):
        return "push" in on
    if not isinstance(on, dict) or not on:
        return True
    if "push" not in on:
        return False
    setting = on["push"]
    if not isinstance(setting, dict):
        return True
    if "branches" in setting and "branches-ignore" in setting:
        return True
    if "branches" in setting:
        patterns = setting["branches"]
        return (
            not isinstance(patterns, list)
            or not patterns
            or any(
                not isinstance(item, str) or re.fullmatch(r"[A-Za-z0-9_./-]+", item) is None or item == branch
                for item in patterns
            )
        )
    ignored = setting.get("branches-ignore")
    return not (isinstance(ignored, list) and branch in ignored)


def _declared_github_exclusion(sha: str, branch: str) -> bool:
    try:
        rows = (
            git_command.run(
                ["ls-tree", "-r", "-z", "--name-only", sha, "--", ".github/workflows"],
                check=True,
                capture_output=True,
                timeout=_TIMEOUT,
            )
            .stdout.decode("utf-8")
            .split("\0")
        )
        paths = [
            path
            for raw in rows
            if raw
            and (path := pathlib.PurePosixPath(raw)).parent.as_posix() == ".github/workflows"
            and path.suffix in {".yaml", ".yml"}
        ]
        if not paths:
            return False
        for file in paths:
            source = git_command.run(
                ["show", f"{sha}:{file.as_posix()}"], check=True, capture_output=True, timeout=_TIMEOUT
            ).stdout.decode("utf-8")
            workflow = yaml.load(source, Loader=yaml.BaseLoader)  # noqa: S506
            if not isinstance(workflow, dict) or _might_push(workflow.get("on"), branch):
                return False
    except OSError, subprocess.SubprocessError, UnicodeError, ValueError, yaml.YAMLError:
        return False
    return True


def check(text: str, *, expected_head: str | None = None) -> str:
    inputs = merge_input(text)
    if inputs is None:
        raise ActionableError("マージ用WIの情報が無い", next_action="対象ブランチと完全OIDを保存する")
    repository, branch, sha, local = inputs
    if (
        not isinstance(repository, str)
        or not isinstance(branch, str)
        or not branch
        or branch.startswith("-")
        or not isinstance(sha, str)
        or _SHA.fullmatch(sha) is None
    ):
        raise ActionableError("対象リポジトリ・ブランチ・完全OIDが必要", next_action=_RETRY)
    if expected_head is not None and expected_head != sha:
        raise ActionableError("取得した先端がマージ用WIの完全OIDと一致しない", next_action=_RETRY)
    try:
        forge, location = wait_ci_forge._resolve_forge("auto", repository)  # pylint: disable=protected-access
        fetch = wait_ci_forge._default_run_list_fn(forge, location, branch, _TIMEOUT)  # pylint: disable=protected-access
        fetched = fetch(sha)
        if len(fetched) >= 1000:
            raise ActionableError("CI一覧が取得上限1000件に達し、必要な実行を確定できない", next_action=_RETRY)
        runs = [run for run in fetched if run.get("headSha") == sha and run.get("event", "push") == "push"]
        wait_ci_polling._run_ids(runs)  # pylint: disable=protected-access
        superseded = wait_ci_polling._superseded_cancelled_ids(runs)  # pylint: disable=protected-access
        runs = [run for run in runs if run["databaseId"] not in superseded]
        if not runs:
            absent = (
                _declared_github_exclusion(sha, branch) or wait_ci_forge._github_ci_configured(location, sha, _TIMEOUT) is False  # pylint: disable=protected-access
                if forge == "github"
                else wait_ci_forge._gitlab_ci_exclusion(location, sha, branch, _TIMEOUT) is not None  # pylint: disable=protected-access
            )
            if absent:
                return _check_local_evidence(local, repository, sha)
            raise ActionableError(f"対象OID {sha} のpush CIが未確認", next_action=_RETRY)
        if any(run.get("status") != "completed" or run.get("conclusion") != "success" for run in runs):
            states = ", ".join(f"{run['databaseId']}:{run.get('status')}/{run.get('conclusion')}" for run in runs)
            raise ActionableError(f"対象OID {sha} のCIが成功していない: {states}", next_action=_RETRY)
        jobs = wait_ci_forge._gh_job_list if forge == "github" else wait_ci_forge._glab_job_list  # pylint: disable=protected-access
        if not all(_configured_checks_executed(jobs(location, run, _TIMEOUT)) for run in runs):
            raise ActionableError(f"対象OID {sha} の実ジョブの成功を確認できない", next_action=_RETRY)
    except wait_ci_forge.RunListError as error:
        raise ActionableError(f"対象OIDのCIを取得できない: {error}", next_action=_RETRY) from error
    return f"対象OID {sha} のpush CI成功: {', '.join(str(run['databaseId']) for run in runs)}"


def cmd_verify_merge(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    with sync.repo_lock(private_notes):
        (path,) = resolve_editable_targets([args.filename], private_notes)
        content = frontmatter.decode_entry_text(path.read_bytes())
    try:
        message = check(content, expected_head=args.head)
    except ActionableError as error:
        outcome.report_failure(str(error), next_action=error.next_action)
        raise SystemExit(1) from error
    outcome.report_success(message)
