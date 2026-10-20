"""複数領域の子プロセス停止を検査するため、リポジトリrootへ配置する。"""

import ast
import pathlib
import subprocess

# キーは追跡ファイルと関数。停止・時間上限を許可する理由を箇所ごとに残す。
_ALLOWED: dict[tuple[str, str], str] = {
    (
        "agent-toolkit/scripts/launch.py",
        "_stop_server_process",
    ): "stdlibだけのbootstrapで、独立sessionとして起動した子のprocess groupを有限時間で回収する",
    (
        "agent-toolkit/agent_toolkit/_atk/wi/process_loop_all_repos.py",
        "_stop_repository",
    ): "agents_serverのsession停止。子プロセスを直接止めず、終了の所有はkillの実装側にある",
    ("agent-toolkit/agent_toolkit/_common/process_tree.py", "reclaim_processes"): "子孫回収の共通実装",
    ("pytools/_internal/common.py", "stop_process_tree"): "pytoolsの子孫回収の共通実装",
    (
        "agent-toolkit/agent_toolkit/_agents_server/antigravity.py",
        "_terminate_process",
    ): "停止前に列挙して子孫も回収する既存実装",
    ("agent-toolkit/agent_toolkit/_agents_server/codex.py", "_close"): "停止前に列挙して子孫も回収する既存実装",
    (
        "agent-toolkit/agent_toolkit/_common/codex_models.py",
        "list_models_from_app_server",
    ): "asyncioの子を待ち、列挙済み子孫をスレッドで回収する",
    ("agent-toolkit/agent_toolkit/_agents_server/launch_requests.py", "_run_preflight_command"): "固定のuv --version照会",
    (
        "agent-toolkit/agent_toolkit/_atk/agents_exit_session.py",
        "request_termination",
    ): "登録済み所有セッションへの終了要求であり起動した子の打ち切りではない",
    ("agent-toolkit/agent_toolkit/_atk/serve/remote.py", "_terminate"): "ローカルのsshだけを停止し、remoteはEOFで終了する",
    (
        "agent-toolkit/agent_toolkit/_atk/serve/remote.py",
        "stop_resident_helper",
    ): "ローカルのsshだけを停止し、remoteはEOFで終了する",
    ("agent-toolkit/agent_toolkit/_common/claude_connection.py", "subscription_available"): "claude auth statusの単発照会",
    ("agent-toolkit/agent_toolkit/_common/wait_schedule.py", "_has_valid_subscription_status"): "claude auth statusの単発照会",
    ("agent-toolkit/agent_toolkit/_common/json_command.py", "run"): "gh・glabのAPI読取",
    ("agent-toolkit/agent_toolkit/_git/command.py", "run"): "ローカルgit操作",
    (
        "agent-toolkit/skills/refine-prompt/scripts/multi_turn_trials.py",
        "_grade",
    ): "固定の採点スクリプトで、提出コードが起動する子の残存は許容する",
    ("libexec/update_dotfiles.py", "_kill_process_tree"): "停止前に列挙して子孫も回収する既存実装",
    (
        "libexec/update_dotfiles.py",
        "_run_codex_daemon_command",
    ): "管理daemonの起動・停止要求。daemonは呼出元と独立した寿命を持つ",
    ("pytools/_internal/setup_media_remote.py", "_stop_running"): "実行ファイルで特定した常駐プロセスの停止",
    ("pytools/_internal/systemd_user_unit.py", "setup"): "固定のsystemd-catへ文字列を送る単一プロセス",
    ("pytools/releaser.py", "_run_gh_json_read"): "ghのAPI読取",
}


def _violations(path: pathlib.Path, source: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    modules = {"subprocess"}
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            imports.update(alias.asname or alias.name for alias in node.names)
    failures = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        subprocess_call = (
            isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) and function.value.id in modules
        ) or (isinstance(function, ast.Name) and function.id in imports)
        timeout = subprocess_call and any(keyword.arg == "timeout" for keyword in node.keywords)
        stop = (
            isinstance(function, ast.Attribute)
            and function.attr in {"kill", "terminate"}
            and ast.unparse(function.value) not in {"os", "_MANAGER"}
        )
        if not (timeout or stop):
            continue
        owner = parents.get(node)
        while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = parents.get(owner)
        name = owner.name if owner is not None else "<module>"
        if (path.as_posix(), name) not in _ALLOWED:
            failures.append(f"{path}:{node.lineno}: {name}: {ast.unparse(function)}")
    return failures


def test_timeouts_and_kills_use_process_tree_helpers():
    """追跡済み製品Pythonの停止処理が共通関数または理由付き許可箇所へ収まる。"""
    root = pathlib.Path(__file__).resolve().parent
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "agent-toolkit", "pytools", "libexec", "scripts"],
        check=True,
        capture_output=True,
    )
    paths = [pathlib.Path(value) for value in result.stdout.decode("utf-8").split("\0") if value]
    assert paths
    failures = []
    for path in paths:
        if path.suffix == ".py" and not path.name.endswith("_test.py") and "_testing" not in path.parts:
            failures.extend(_violations(path, (root / path).read_text(encoding="utf-8")))
    assert not failures, "共通関数を経由しない停止・時間上限:\n" + "\n".join(failures)


def test_bootstrap_stop_exception_is_limited_to_its_owned_helper():
    """bootstrap固有の許可は同じファイルの一般呼出側や別ファイルへ広がらない。"""
    source = "def _stop_server_process(process):\n    process.kill()\n"
    assert not _violations(pathlib.Path("agent-toolkit/scripts/launch.py"), source)
    assert _violations(pathlib.Path("agent-toolkit/agent_toolkit/atk.py"), source)
    assert _violations(pathlib.Path("agent-toolkit/scripts/launch.py"), source.replace("_stop_server_process", "main"))
