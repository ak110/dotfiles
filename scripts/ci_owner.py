"""共通CIを所有するrunを判定する。依存導入前に標準Pythonだけで動く。"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


def main() -> None:
    """GitHub Actionsの環境入力から判定結果を出力する。"""
    owns = True
    reason = "このrunが共通CIを所有します。"
    if os.environ["RELEASE_PULL_REQUEST"] == "true":
        owns = False
        reason = "release PR: develop pushが共通CIを所有します。develop branchのCI結果を確認してください。"
    elif os.environ["GITHUB_EVENT_NAME"] == "push" and os.environ["GITHUB_REF"] == "refs/heads/develop":
        repository = os.environ["GITHUB_REPOSITORY"]
        sha = os.environ["GITHUB_SHA"]
        query = urllib.parse.urlencode({"branch": "master", "event": "push", "head_sha": sha, "per_page": 100})
        url = f"{os.environ['GITHUB_API_URL']}/repos/{repository}/actions/workflows/ci.yaml/runs?{query}"
        try:
            while url and owns:
                request = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    runs = json.load(response)["workflow_runs"]
                    links = urllib.request.parse_http_list(response.headers.get("Link", ""))
                for run in runs:
                    if (
                        run["head_sha"] == sha
                        and run["head_branch"] == "master"
                        and run["event"] == "push"
                        and run["head_repository"]["full_name"] == repository
                        and (
                            run["status"] in {"queued", "in_progress", "waiting", "pending", "requested"}
                            or (run["status"] == "completed" and run["conclusion"] == "success")
                        )
                    ):
                        owns = False
                        reason = f"同期push: masterの所有run {run['html_url']} を確認してください。"
                        break
                url = next((link.split(">", 1)[0].lstrip("<") for link in links if 'rel="next"' in link), "")
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"::warning::master CIを照会できないため、このrunで共通検証を実行します: {type(error).__name__}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"owns={str(owns).lower()}\nreason={reason}\n")


if __name__ == "__main__":
    main()
