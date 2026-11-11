"""変更領域からRustとブラウザー検査を選ぶ。取得不能時は全体を実行する。"""

import json
import os
import subprocess


def main() -> None:
    """GitHub Actionsの環境入力から判定結果を出力する。"""
    rust = browser = True
    event = os.environ["EVENT_NAME"]
    reason = "判定できない入力のため全体を実行する。"
    if event == "push" and os.environ["REF_NAME"] in {"refs/heads/master", "refs/heads/develop"}:
        reason = "master・developへのpushは全体を実行する。"
    elif event == "push" and (not os.environ["BEFORE_SHA"] or set(os.environ["BEFORE_SHA"]) == {"0"}):
        reason = "初回pushは全体を実行する。"
    elif event in {"push", "pull_request"}:
        try:
            base = os.environ["PR_BASE_SHA"] if event == "pull_request" else "refs/remotes/origin/develop"
            if not base or not os.environ["CURRENT_SHA"]:
                raise ValueError("比較元または対象のSHAがない")
            common = subprocess.run(
                ["git", "merge-base", base, os.environ["CURRENT_SHA"]],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout.strip()
            changed = subprocess.run(
                ["git", "diff", "--name-only", "--no-renames", "-z", common, os.environ["CURRENT_SHA"], "--"],
                check=True,
                capture_output=True,
            ).stdout
            paths = [path.decode("utf-8", errors="surrogateescape") for path in changed.split(b"\0") if path]
            print("変更パス: " + json.dumps(paths, ensure_ascii=True))
            if not paths:
                raise ValueError("変更パスを取得できない")
            rust = browser = False
            categories = set()
            shared = {
                "Makefile",
                "mise.toml",
                "mise.lock",
                ".python-version",
                "Cargo.toml",
                "Cargo.lock",
                "rust-toolchain",
                "rust-toolchain.toml",
            }
            for path in paths:
                if path in shared or path.startswith((".github/", ".cargo/")) or path.endswith(("pyproject.toml", "uv.lock")):
                    rust = browser = True
                    categories.add("共有・実行設定")
                elif path.startswith("rust/"):
                    rust = True
                    categories.add("Rust")
                elif path.endswith((".md", ".md.tmpl")):
                    categories.add("文書")
                elif path.endswith(".py") or path.startswith("agent-toolkit/agent_toolkit/_atk/serve/"):
                    browser = True
                    categories.add("Python・serve")
                else:
                    rust = browser = True
                    categories.add("未知のパス")
            reason = "変更領域: " + "、".join(sorted(categories))
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            rust = browser = True
            detail = str(error)
            if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                stderr = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr
                detail += f": {stderr.strip()}"
            # GITHUB_OUTPUTの1行の値としても出すため、元の診断の改行を空白へ揃える。
            reason = "比較元・履歴・差分を確認できないため全体を実行する。理由: " + " ".join(detail.splitlines())
    print(f"Rust={str(rust).lower()}, browser={str(browser).lower()}: {reason}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"rust={str(rust).lower()}\nbrowser={str(browser).lower()}\nscope_reason={reason}\n")


if __name__ == "__main__":
    main()
