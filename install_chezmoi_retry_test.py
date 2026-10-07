"""chezmoiインストーラーの再試行契約を検証する。"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent
INSTALL_ACTION = REPO_ROOT / ".github" / "actions" / "install-chezmoi" / "action.yml"
INSTALL_SCRIPT = REPO_ROOT / "install.sh"

DOWNLOAD_ARGUMENTS = "curl -fsSL --connect-timeout 10 --max-time 30 get.chezmoi.io"
WINDOWS_DOWNLOAD_ARGUMENTS = "curl -fsSL --ssl-revoke-best-effort --connect-timeout 10 --max-time 30 get.chezmoi.io"
INSTALL_COMMAND = 'sh -c "$installer"'
ATTEMPT_LIMIT = 'if [ "$attempt" -ge 3 ]; then'
RETRY_DELAY = "sleep 2"


def _action_script() -> str:
    """CIが使うcomposite actionの導入スクリプトを返す。"""
    action = yaml.safe_load(INSTALL_ACTION.read_text(encoding="utf-8"))
    (step,) = action["runs"]["steps"]
    return step["run"]


def test_ci_action_and_install_sh_share_bounded_retry_contract() -> None:
    """CIのcomposite actionとエンドユーザー向けのinstall.shで同じ取得引数と再試行制限を維持する。"""
    action_script = _action_script()
    action_function = _single_install_function(action_script)
    install_function = _single_install_function(INSTALL_SCRIPT.read_text(encoding="utf-8"))

    for function in (action_function, install_function):
        assert function.count(INSTALL_COMMAND) == 1
        assert function.count(ATTEMPT_LIMIT) == 1
        assert function.count(RETRY_DELAY) == 1
    assert install_function.count(f"installer=$({DOWNLOAD_ARGUMENTS})") == 1
    assert action_script.count(DOWNLOAD_ARGUMENTS) == 1
    assert action_script.count(WINDOWS_DOWNLOAD_ARGUMENTS) == 1


@pytest.mark.parametrize(("first_script", "first_exit"), [("", 1), ("exit 1", 0)])
@pytest.mark.parametrize("revoke_best_effort", ["false", "true"])
def test_ci_action_retries_after_failure(tmp_path: Path, first_script: str, first_exit: int, revoke_best_effort: str) -> None:
    """composite actionは本文取得またはインストーラーの失敗後に処理全体を再実行し、導入先をPATHへ加える。"""
    github_path = tmp_path / "github_path"
    env = {"SSL_REVOKE_BEST_EFFORT": revoke_best_effort, "BIN_DIR": "", "GITHUB_PATH": str(github_path)}

    _assert_script_retries(tmp_path, _action_script(), first_script, first_exit, env)

    assert github_path.read_text(encoding="utf-8") == f"{tmp_path / '.local' / 'bin'}\n"


@pytest.mark.parametrize(("first_script", "first_exit"), [("", 1), ("exit 1", 0)])
def test_install_sh_retries_after_failure(tmp_path: Path, first_script: str, first_exit: int) -> None:
    """install.shの導入関数は本文取得またはインストーラーの失敗後に処理全体を再実行する。"""
    function = _single_install_function(INSTALL_SCRIPT.read_text(encoding="utf-8"))
    _assert_script_retries(tmp_path, f"{function}\ninstall_chezmoi", first_script, first_exit, {})


def _assert_script_retries(root: Path, script: str, first_script: str, first_exit: int, extra_env: dict[str, str]) -> None:
    fake_bin = root / "bin"
    fake_bin.mkdir(parents=True)
    counter = root / "attempts"
    _write_executable(
        fake_bin / "curl",
        """#!/bin/sh
attempt=0
if [ -f "$RETRY_COUNTER" ]; then
    attempt=$(cat "$RETRY_COUNTER")
fi
attempt=$((attempt + 1))
printf '%s' "$attempt" >"$RETRY_COUNTER"
if [ "$attempt" -eq 1 ]; then
    printf '%s\n' "$FIRST_SCRIPT"
    exit "$FIRST_EXIT"
else
    printf '%s\n' 'exit 0'
fi
""",
    )
    _write_executable(fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
    env = {
        "HOME": str(root),
        "PATH": f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "RETRY_COUNTER": str(counter),
        "FIRST_SCRIPT": first_script,
        "FIRST_EXIT": str(first_exit),
        **extra_env,
    }

    subprocess.run(["bash", "-c", script], check=True, env=env)

    assert counter.read_text(encoding="utf-8") == "2"


def _single_install_function(content: str) -> str:
    """導入関数がちょうど1つあることを確かめて返す。"""
    functions = _extract_install_functions(content)
    assert len(functions) == 1
    return functions[0]


def _extract_install_functions(content: str) -> list[str]:
    functions: list[str] = []
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if "install_chezmoi() {" not in line:
            continue
        depth = 0
        body: list[str] = []
        for function_line in lines[index:]:
            body.append(function_line)
            depth += function_line.count("{") - function_line.count("}")
            if depth == 0:
                break
        functions.append("\n".join(body))
    return functions


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
