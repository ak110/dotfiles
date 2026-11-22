"""共通CIを所有するrunを判定する。依存導入前に標準Pythonだけで動く。"""

import json
import os
import subprocess
import typing
import urllib.error
import urllib.parse
import urllib.request

_DEVELOP_CHECKS = frozenset(
    {
        "common-ci-owner",
        "test-linux",
        "test-windows (tests)",
        "test-windows (install)",
        "test-windows",
        "update-dotfiles-upgrade (windows)",
        "browser-e2e",
        "rust-lint",
        "statusline-version",
        "plugin-version",
        "python-lint-other (3.15)",
        "python-lint (3.15)",
        "pylint (3.15)",
        "pytest (3.15)",
        "pytest-toolkit (3.15)",
        *(f"pytest-toolkit (3.15, {shard}/3)" for shard in range(1, 4)),
    }
)


def _paginated(path: str, key: str) -> typing.Iterator[dict[str, typing.Any]]:
    api = os.environ["GITHUB_API_URL"].rstrip("/")
    url = f"{api}/{path}"
    while url:
        if not url.startswith(f"{api}/"):
            raise ValueError("CIの次ページのURLがGitHub APIの外を指している")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            yield from json.load(response)[key]
            links = urllib.request.parse_http_list(response.headers.get("Link", ""))
        url = next((link.split(">", 1)[0].lstrip("<") for link in links if 'rel="next"' in link), "")


def _second_parent_for_reuse(sha: str) -> str | None:
    def git(*arguments: str) -> str:
        return subprocess.check_output(["git", *arguments], text=True, encoding="utf-8").strip()

    parents = git("rev-list", "--parents", "-n", "1", sha).split()
    if len(parents) != 3:
        return None
    _, first, second = parents
    relation = subprocess.run(["git", "merge-base", "--is-ancestor", first, second], check=False)
    if relation.returncode != 0:
        return None
    return second if git("rev-parse", f"{sha}^{{tree}}") == git("rev-parse", f"{second}^{{tree}}") else None


def _has_develop_checks(jobs: typing.Iterable[dict[str, typing.Any]]) -> bool:
    seen = set()
    selected_steps = {"browser-e2e": "ブラウザーE2Eの実行", "rust-lint": "Rust検証の実行"}
    for job in jobs:
        name = job["name"]
        if name in seen or name not in _DEVELOP_CHECKS or job["status"] != "completed" or job["conclusion"] != "success":
            return False
        if (step := selected_steps.get(name)) is not None and not any(
            item["name"] == step and item["conclusion"] == "success" for item in job["steps"]
        ):
            return False
        seen.add(name)
    return seen == _DEVELOP_CHECKS


def _matching_push_runs(repository: str, branch: str, sha: str) -> typing.Iterator[dict[str, typing.Any]]:
    query = urllib.parse.urlencode({"branch": branch, "event": "push", "head_sha": sha, "per_page": 100})
    yield from (
        run
        for run in _paginated(f"repos/{repository}/actions/workflows/ci.yaml/runs?{query}", "workflow_runs")
        if run["head_sha"] == sha
        and run["head_branch"] == branch
        and run["event"] == "push"
        and run["head_repository"]["full_name"] == repository
    )


def _reusable_develop_run(repository: str, sha: str) -> dict[str, typing.Any] | None:
    if (second := _second_parent_for_reuse(sha)) is None:
        return None
    for run in _matching_push_runs(repository, "develop", second):
        if run["status"] != "completed" or run["conclusion"] != "success":
            continue
        run_id, attempt = int(run["id"]), int(run["run_attempt"])
        jobs = _paginated(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "jobs")
        if _has_develop_checks(jobs):
            return run
    return None


def main() -> None:
    """GitHub Actionsの環境入力から判定結果を出力する。"""
    owns = True
    reason = "このrunが共通CIを所有します。"
    if os.environ["RELEASE_PULL_REQUEST"] == "true":
        owns = False
        reason = "release PR: develop pushが共通CIを所有します。develop branchのCI結果を確認してください。"
    elif os.environ["GITHUB_EVENT_NAME"] == "push":
        repository = os.environ["GITHUB_REPOSITORY"]
        sha = os.environ["GITHUB_SHA"]
        try:
            if os.environ["GITHUB_REF"] == "refs/heads/master":
                if (run := _reusable_develop_run(repository, sha)) is not None:
                    owns = False
                    reason = f"同じ内容を全体検証済み: developのCI {run['html_url']} を再利用する。"
            elif os.environ["GITHUB_REF"] == "refs/heads/develop":
                for run in _matching_push_runs(repository, "master", sha):
                    if run["status"] in {"queued", "in_progress", "waiting", "pending", "requested"} or (
                        run["status"] == "completed" and run["conclusion"] == "success"
                    ):
                        owns = False
                        reason = f"同期push: masterの所有run {run['html_url']} を確認してください。"
                        break
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            print(f"他のCIの成功を確認できないため、このrunで共通検証を実行する: {type(error).__name__}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"owns={str(owns).lower()}\nreason={reason}\n")


if __name__ == "__main__":
    main()
