"""Claude Codeクラウド向けセットアップスクリプトの公開契約を検証する。

取得元はローカルのbareリポジトリ、`uv`は呼び出しを記録するstubに置き換え、隔離した取得先と依存領域で実行する。
"""

import os
import pathlib
import stat
import subprocess

from agent_toolkit._testing.git_repository import commit_all, init_bare_repository, init_repository, run_git

_SCRIPT = pathlib.Path(__file__).resolve().parent / "install-claude-cloud.sh"


def _origin(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """配布物を1commit持つbareリポジトリと、その更新に使う作業ツリーを用意する。"""
    bare = init_bare_repository(tmp_path / "origin.git", initial_branch="master")
    work = init_repository(
        tmp_path / "work",
        initial_branch="master",
        origin=str(bare),
        files={"agent-toolkit/scripts/launch.py": "# v1\n"},
        commit_message="初版",
    )
    run_git(work, "push", "--quiet", "origin", "master")
    return bare, work


def _uv_stub(tmp_path: pathlib.Path, *, exit_code: int = 0) -> pathlib.Path:
    """引数と`AGENT_TOOLKIT_RUNTIME_DATA`を記録して指定の終了コードで終わる`uv`を置いたディレクトリを返す。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "uv"
    stub.write_text(
        f'#!/bin/sh\nprintf \'%s|%s\\n\' "$AGENT_TOOLKIT_RUNTIME_DATA" "$*" >> "{tmp_path / "uv.log"}"\nexit {exit_code}\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run(
    tmp_path: pathlib.Path, bare: pathlib.Path, bin_dir: pathlib.Path, *, path: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": path if path is not None else f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "DOTFILES_REPO_URL": f"file://{bare}",
        "DOTFILES_BRANCH": "master",
        "AGENT_TOOLKIT_CLOUD_SRC": str(tmp_path / "src"),
        "AGENT_TOOLKIT_RUNTIME_DATA": str(tmp_path / "data"),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(["/bin/bash", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=120)


def test_first_run_clones_and_prepares(tmp_path: pathlib.Path) -> None:
    """初回は配布物を取得先へ取得し、依存領域を指定して依存準備を呼び、環境変数へ保存する2行を表示する。"""
    bare, _work = _origin(tmp_path)
    result = _run(tmp_path, bare, _uv_stub(tmp_path))

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "src" / "agent-toolkit" / "scripts" / "launch.py").read_text(encoding="utf-8") == "# v1\n"
    log = (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()
    launcher = tmp_path / "src" / "agent-toolkit" / "scripts" / "launch.py"
    assert log == [
        f"{tmp_path / 'data'}|run --no-config --no-project --python 3.15 python "
        f"{launcher} agent_toolkit/agents_server_mcp.py --check-dependencies"
    ]
    lines = result.stdout.splitlines()
    assert lines[-2:] == [
        f"CLAUDE_CODE_PLUGIN_DIRS={tmp_path / 'src' / 'agent-toolkit'}",
        f"AGENT_TOOLKIT_RUNTIME_DATA={tmp_path / 'data'}",
    ]


def test_rerun_updates_existing_checkout(tmp_path: pathlib.Path) -> None:
    """取得済みの取得先で再実行すると、取得元の最新の版へ更新して依存準備を再び行う。"""
    bare, work = _origin(tmp_path)
    bin_dir = _uv_stub(tmp_path)
    assert _run(tmp_path, bare, bin_dir).returncode == 0
    (work / "agent-toolkit" / "scripts" / "launch.py").write_text("# v2\n", encoding="utf-8")
    commit_all(work, "更新")
    run_git(work, "push", "--quiet", "origin", "master")

    result = _run(tmp_path, bare, bin_dir)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "src" / "agent-toolkit" / "scripts" / "launch.py").read_text(encoding="utf-8") == "# v2\n"
    assert len((tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()) == 2


def test_failures_report_stage(tmp_path: pathlib.Path) -> None:
    """gitの不在、取得の失敗、依存準備の失敗は、失敗した段と確認事項を標準エラーへ出力して非0で終わる。"""
    bare, _work = _origin(tmp_path)
    bin_dir = _uv_stub(tmp_path)

    missing_git = _run(tmp_path, bare, bin_dir, path=str(bin_dir))
    assert missing_git.returncode == 1
    assert "gitが見つかりません" in missing_git.stderr

    unreachable = _run(tmp_path, tmp_path / "missing.git", bin_dir)
    assert unreachable.returncode == 1
    assert "cloneできませんでした" in unreachable.stderr
    assert not (tmp_path / "uv.log").exists()

    failing_bin = _uv_stub(tmp_path / "failing", exit_code=3)
    dependency = _run(tmp_path, bare, failing_bin)
    assert dependency.returncode == 1
    assert "依存を準備できませんでした" in dependency.stderr
    assert "CLAUDE_CODE_PLUGIN_DIRS=" not in dependency.stdout


def test_guide_pipe_form_passes_custom_locations_to_script(tmp_path: pathlib.Path) -> None:
    """ガイドが案内するパイプの形（変数を右側の`bash`の直前へ置く）で、既定と異なる取得先と依存領域へ取得する。

    変数を行頭へ置くと左側のコマンドだけに渡り、スクリプトは省略時の取得先を使う。
    """
    bare, _work = _origin(tmp_path)
    bin_dir = _uv_stub(tmp_path)
    source = tmp_path / "custom-src"
    data = tmp_path / "custom-data"
    command = f'cat "$1" | AGENT_TOOLKIT_CLOUD_SRC="{source}" AGENT_TOOLKIT_RUNTIME_DATA="{data}" bash'
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "DOTFILES_REPO_URL": f"file://{bare}",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(
        ["/bin/bash", "-c", command, "pipe", str(_SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=120
    )

    assert result.returncode == 0, result.stderr
    assert (source / "agent-toolkit" / "scripts" / "launch.py").is_file()
    assert result.stdout.splitlines()[-2:] == [
        f"CLAUDE_CODE_PLUGIN_DIRS={source / 'agent-toolkit'}",
        f"AGENT_TOOLKIT_RUNTIME_DATA={data}",
    ]
    assert (tmp_path / "uv.log").read_text(encoding="utf-8").startswith(f"{data}|")
