"""`agent_toolkit/`配下のテストへ開発機の状態からの隔離が自動で適用されることを確かめる。"""

import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from agent_toolkit._testing import isolation


def test_development_state_isolated_by_default(tmp_path: pathlib.Path) -> None:
    """この起動範囲のテストは、何も設定しなくても開発機の状態から隔離される。"""
    isolation.assert_development_state_isolated(tmp_path)


def test_commit_hook_context_does_not_escape_nested_test_repository(tmp_path: pathlib.Path) -> None:
    """実際のlinked worktreeのhookからpytestを起動しても、模擬repoのinit・addが元repoへ作用しない。"""
    environment = dict(os.environ)
    names = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.splitlines()
    for name in names:
        environment.pop(name, None)

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    git("init", "-q", "--initial-branch=main", str(main))
    (main / "kept.txt").write_text("元repoの内容\n", encoding="utf-8")
    git("-C", str(main), "add", ".")
    git("-C", str(main), "commit", "-q", "-m", "base")
    git("-C", str(main), "worktree", "add", "-q", "--detach", str(worktree), "HEAD")
    original_config = (main / ".git/config").read_bytes()
    original_tree = git("-C", str(worktree), "write-tree").stdout.strip()
    test_file = tmp_path / "nested_test.py"
    test_file.write_text(
        "import pathlib, subprocess\n"
        "def test_nested_repository(tmp_path):\n"
        "    inner = tmp_path / 'inner'\n"
        "    subprocess.run(['git', 'init', '-q', str(inner)], check=True)\n"
        "    (inner / 'probe.txt').write_text('nested\\n', encoding='utf-8')\n"
        "    subprocess.run(['git', '-C', str(inner), 'add', '.'], check=True)\n"
        "    result = subprocess.run(['git', '-C', str(inner), 'ls-files'], capture_output=True, text=True, check=True)\n"
        "    assert result.stdout.strip() == 'probe.txt'\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "agent_toolkit._testing.isolation",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:xdist",
        "-q",
        str(test_file),
    ]
    hook = main / ".git/hooks/pre-commit"
    result_file = tmp_path / "nested-result.json"
    runner = tmp_path / "hook_runner.py"
    runner.write_text(
        "import json, pathlib, subprocess\n"
        f"result = subprocess.run({command!r}, capture_output=True, text=True, "
        "encoding='utf-8', errors='replace', timeout=30)\n"
        f"pathlib.Path({str(result_file)!r}).write_text(json.dumps("
        "{'rc': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}), encoding='utf-8')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    hook.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(runner)]) + "\n", encoding="utf-8")
    hook.chmod(0o700)
    aborted = subprocess.run(
        ["git", "-C", str(worktree), "commit", "--allow-empty", "-m", "hook probe"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    assert aborted.returncode == 1, aborted.stderr
    nested = json.loads(result_file.read_text(encoding="utf-8"))
    assert nested["rc"] == 0, nested
    assert (main / ".git/config").read_bytes() == original_config
    assert git("-C", str(worktree), "write-tree").stdout.strip() == original_tree


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        pytest.param(
            "linux",
            {"HOME": "/h"},
            {
                "npm_config_cache_dir": "/h/.cache/pnpm",
                "npm_config_store_dir": "/h/.local/share/pnpm/store",
                "COREPACK_HOME": "/h/.cache/node/corepack",
            },
            id="linux-home-only",
        ),
        pytest.param(
            "linux",
            {"HOME": "/h", "XDG_CACHE_HOME": "/c", "XDG_DATA_HOME": "/d"},
            {
                "npm_config_cache_dir": "/c/pnpm",
                "npm_config_store_dir": "/d/pnpm/store",
                "COREPACK_HOME": "/c/node/corepack",
            },
            id="linux-xdg",
        ),
        pytest.param(
            "linux",
            {"HOME": "/h", "PNPM_HOME": "/p", "XDG_DATA_HOME": "/d"},
            {
                "npm_config_cache_dir": "/h/.cache/pnpm",
                "npm_config_store_dir": "/p/store",
                "COREPACK_HOME": "/h/.cache/node/corepack",
            },
            id="linux-pnpm-home",
        ),
        pytest.param(
            "linux",
            {"HOME": "/h", "npm_config_cache_dir": "/x", "npm_config_store_dir": "/y", "COREPACK_HOME": "/z"},
            {"npm_config_cache_dir": "/x", "npm_config_store_dir": "/y", "COREPACK_HOME": "/z"},
            id="explicit-values",
        ),
        pytest.param(
            "darwin",
            {"HOME": "/Users/u"},
            {
                "npm_config_cache_dir": "/Users/u/Library/Caches/pnpm",
                "npm_config_store_dir": "/Users/u/Library/pnpm/store",
                "COREPACK_HOME": "/Users/u/.cache/node/corepack",
            },
            id="darwin",
        ),
        pytest.param(
            "win32",
            {"USERPROFILE": r"C:\Users\u", "LOCALAPPDATA": r"C:\Users\u\AppData\Local"},
            {
                "npm_config_cache_dir": r"C:\Users\u\AppData\Local\pnpm-cache",
                "npm_config_store_dir": r"C:\Users\u\AppData\Local\pnpm\store",
                "COREPACK_HOME": r"C:\Users\u\AppData\Local\node\corepack",
            },
            id="win32",
        ),
    ],
)
def test_package_cache_environ_follows_tool_rules(platform: str, environ: dict[str, str], expected: dict[str, str]) -> None:
    """pnpmの公式資料とcorepackの実装が定める保存先の規則どおりに、ホストの位置を求める。"""
    assert isolation.package_cache_environ(environ, platform) == expected
