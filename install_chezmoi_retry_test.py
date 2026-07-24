"""chezmoiインストーラーの再試行契約を検証する。"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yaml"
INSTALL_SCRIPT = REPO_ROOT / "install.sh"

DOWNLOAD_COMMAND = "installer=$(curl -fsSL --connect-timeout 10 --max-time 30 get.chezmoi.io)"
INSTALL_COMMAND = 'sh -c "$installer"'
ATTEMPT_LIMIT = 'if [ "$attempt" -ge 3 ]; then'
RETRY_DELAY = "sleep 2"


def test_all_chezmoi_install_paths_share_bounded_retry_contract() -> None:
    """CIと利用者向け導入処理で同じ再試行制限を維持する。"""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    install_script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    workflow_functions = _extract_install_functions(workflow)
    install_functions = _extract_install_functions(install_script)

    assert len(workflow_functions) == 2
    assert len(install_functions) == 1
    for function in [*workflow_functions, *install_functions]:
        assert function.count(DOWNLOAD_COMMAND) == 1
        assert function.count(INSTALL_COMMAND) == 1
        assert function.count(ATTEMPT_LIMIT) == 1
        assert function.count(RETRY_DELAY) == 1


@pytest.mark.parametrize(("first_script", "first_exit"), [("", 1), ("exit 1", 0)])
def test_installer_retries_after_failure(tmp_path: Path, first_script: str, first_exit: int) -> None:
    """本文取得またはインストーラーの失敗後に処理全体を再実行する。"""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    install_script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    functions = [*_extract_install_functions(workflow), *_extract_install_functions(install_script)]

    for index, function in enumerate(functions):
        _assert_function_retries(tmp_path / str(index), function, first_script, first_exit)


def _assert_function_retries(root: Path, function: str, first_script: str, first_exit: int) -> None:
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
    }

    subprocess.run(["bash", "-c", f"{function}\ninstall_chezmoi"], check=True, env=env)

    assert counter.read_text(encoding="utf-8") == "2"


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
