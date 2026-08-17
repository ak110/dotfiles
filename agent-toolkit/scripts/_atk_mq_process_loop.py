"""agent-toolkitプラグイン配下の`atk mq`コマンド用補助モジュール。

旧`pytools/dotfiles_fb/_process_loop.py`からの移設。PEP 723 entrypoint
`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import datetime
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import _atk_mq_alerts as _alerts
import _console_title
import _git_command
import _process_loop_log
import watchdog.events
import watchdog.observers
from _atk_mq_common import _count_pending_entries, _pull, _repo_lock
from _atk_mq_repo import _resolve_local_worktree, _resolve_repo_id

# 読み取り由来の`FileOpenedEvent`・`FileClosedNoWriteEvent`を除外した監視対象イベント型。
WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)

# Claude Codeがexit-sessionスキル経由でSIGTERMにより終了する場合のexit codeを含む正常終了集合。
# 0は正常exit、-15はLinuxでのSIGTERM受信、15はWindowsでのSIGTERM相当、
# 143はシェル経由でSIGTERM終了した場合の128+15を表す
# （プラットフォーム分岐なしの緩い判定で十分と判断）。
_CLAUDE_NORMAL_EXIT_CODES: frozenset[int] = frozenset({0, -15, 15, 143})

# `atk`は`uv run --no-project --script`で起動するため、PEP 723のエフェメラル環境を指す
# `VIRTUAL_ENV`が本プロセスの環境に設定される。この値を子セッションへ引き継ぐと、
# 作業対象リポジトリでのパッケージ操作が起動元ツールの環境を対象にする。
# 実測で子プロセスへ混入した仮想環境キーだけを除去対象とする。
_INHERITED_VENV_ENV_KEYS: tuple[str, ...] = ("VIRTUAL_ENV",)

# 仮想環境のコマンド格納ディレクトリ名（POSIXは`bin`、Windowsは`Scripts`）。
_VENV_BIN_DIR_NAMES: tuple[str, ...] = ("bin", "Scripts")

# 主待機のタイムアウト秒（他端末からのフィードバック投入を`remote`同期で拾う間隔）。
_POLL_INTERVAL_SEC = 600.0

# `latest`指定ツールを外部の登録簿に対して再評価する間隔と、導入処理の実行上限。
_MISE_REFRESH_INTERVAL_SEC = 24 * 60 * 60
_MISE_INSTALL_TIMEOUT_SEC = 600
_INTERNAL_MISE_REFRESHED_ARG = "--internal-mise-refreshed"

# 変更検知後、追加イベント発火が無くなるまでの畳み込み待機秒
# （1回のファイル操作で複数イベントが連続発火する実測を吸収する）。
_DEBOUNCE_SEC = 3.0

# ランチャーが作成する再起動要求の受け渡しファイルのパスを保持する環境変数。
# 実体プロセスが自身を`uv`へ置き換えると、置き換え前の`uv`が子の終了を待って残り、
# 再起動のたびにプロセス階層が1段深くなる。実体は次の起動対象を受け渡しファイルへ出力して終了し、
# ランチャーが同一プロセスで次の実体を起動することで階層を一定に保つ。
_RESTART_SPEC_ENV = "AGENT_TOOLKIT_RESTART_SPEC"

# ランチャーへ再起動を要求する終了コード。
_RESTART_EXIT_CODE = 75

# process-loopセッションを識別する正本と、更新中に旧Stop hookと併存するための移行互換名。
_PROCESS_LOOP_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_PROCESS_LOOP_SESSION_ENV = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

# Windows APIのCREATE_NEW_PROCESS_GROUP。POSIXでも純粋関数の契約を検査できるよう値を固定する。
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _strip_inherited_venv(env: dict[str, str]) -> None:
    """起動元ツールのエフェメラル仮想環境を子プロセス環境から除去する。

    `uv run`は`VIRTUAL_ENV`の設定に加えて当該環境のコマンド格納ディレクトリを`PATH`先頭へ挿入する。
    `VIRTUAL_ENV`だけを除去すると`PATH`側が残り、子セッション内の`python`・`pip`や
    コンソールスクリプトが引き続き起動元ツールの環境へ解決される。
    除去対象は`PATH`の全要素ではなく、除去する`VIRTUAL_ENV`の値から導いた
    コマンド格納ディレクトリと一致する要素だけとする。
    POSIXの`PATH`では空要素がカレントディレクトリを表すため、空要素は解決順序を保つよう残す。
    """
    venv_roots = [value for key in _INHERITED_VENV_ENV_KEYS if (value := env.get(key))]
    for key in _INHERITED_VENV_ENV_KEYS:
        env.pop(key, None)
    path_value = env.get("PATH")
    if not venv_roots or path_value is None:
        return
    venv_bin_dirs = {pathlib.Path(root) / name for root in venv_roots for name in _VENV_BIN_DIR_NAMES}
    remaining = [entry for entry in path_value.split(os.pathsep) if not entry or pathlib.Path(entry) not in venv_bin_dirs]
    env["PATH"] = os.pathsep.join(remaining)


def _child_env() -> dict[str, str]:
    """起動元ツールの仮想環境を除いた子プロセス用の環境変数を返す。

    対象は`atk`から起動する外部コマンド（claudeセッション・`update-dotfiles`）とする。
    `update-dotfiles`は`chezmoi apply`を経て作業対象リポジトリのuvベースのパッケージ操作へ至るため、
    claudeセッションと同じく起動元ツールの環境を引き継がせない。
    自己再起動経路（`_restart_process_loop`）は本関数の対象外とする。
    再起動先は`atk`自身であり、起動元と同じ実行環境で継続する必要があるためである。
    ランチャーとの再起動要求の受け渡しファイルは自プロセス専用のため、子孫プロセスへは引き継がない。
    引き継ぐと、子孫が同じファイルへ再起動対象を書き込みうる。
    """
    env = os.environ.copy()
    _strip_inherited_venv(env)
    env.pop(_RESTART_SPEC_ENV, None)
    return env


def _session_env(env: dict[str, str], orchestrator: str, *, platform: str = os.name) -> dict[str, str]:
    """セッション専用の環境を返し、Windows Codexだけにbash互換層を追加する。"""
    session_env = env.copy()
    if platform == "nt" and orchestrator == "codex":
        shim_dir = pathlib.Path(__file__).resolve().parent / "windows-shims"
        inherited_path = session_env.get("PATH", "")
        session_env["PATH"] = os.pathsep.join((str(shim_dir), inherited_path))
    return session_env


def _session_creation_flags(orchestrator: str, *, platform: str = os.name) -> int:
    """Windows Codexを親process-loopと別のコンソール制御グループで起動する。"""
    return _CREATE_NEW_PROCESS_GROUP if platform == "nt" and orchestrator == "codex" else 0


def _create_hook_debug_log(env: dict[str, str]) -> pathlib.Path:
    """Claude Codeのhook診断ログを所有者限定で事前作成する。"""
    config_dir = pathlib.Path(env.get("CLAUDE_CONFIG_DIR", pathlib.Path.home() / ".claude"))
    debug_dir = config_dir / "debug"
    debug_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="process-loop-", suffix=".log", dir=debug_dir)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    return pathlib.Path(name).resolve()


class _ChangeHandler(watchdog.events.FileSystemEventHandler):
    """inbox配下の`.md`変更検知時に`change_event`をsetするハンドラ。"""

    def __init__(self, change_event: threading.Event) -> None:
        super().__init__()
        self._change_event = change_event

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """監視対象イベント型・非ディレクトリ・`.md`拡張子の全条件を満たす場合にsetする。"""
        if not isinstance(event, WATCHED_EVENT_TYPES):
            return
        if event.is_directory:
            return
        if pathlib.Path(str(event.src_path)).suffix != ".md":
            return
        self._change_event.set()


# github.com/ak110/dotfiles編集時のみ、影響範囲の大きいホーム直下チェックアウトを避けるため
# git worktreeを作成してセッションのcwdにする。worktree名は反復ごとに固定値とし、常駐ループの再起動
# （`--no-update`未指定時の再起動）を経ても同一worktreeを継続利用させる。
_DOTFILES_REPO_ID = "github.com/ak110/dotfiles"
_DOTFILES_WORKTREE_NAME = "process-loop"
_DOTFILES_PUBLISH_DESTINATION = "origin/master"
# process-loopが作成するworktreeの配置先（対象リポジトリのroot相対）。
_WORKTREE_PARENT_REL = pathlib.PurePosixPath(".claude/worktrees")


def _resolve_executable(command: str) -> str | None:
    """実行可能ファイルを環境の探索規則で解決し、利用不能時は警告する。"""
    executable = shutil.which(command)
    if executable is None:
        print(f"{command}コマンドを利用できないため処理を継続します。", file=sys.stderr)
    return executable


def _mise_output_detail(output: str | bytes | None) -> str:
    """miseの標準出力又は標準エラー出力を警告用の一行へ整形する。"""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="backslashreplace")
    return output.strip() if isinstance(output, str) and output.strip() else "出力なし"


def _refresh_mise_tools(dotfiles_root: pathlib.Path) -> bool:
    """dotfilesのlatest指定ツールを非ログイン経路で再評価し、失敗後も呼び出し元を継続させる。"""
    executable = _resolve_executable("mise")
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, "install", "--quiet"],
            cwd=dotfiles_root,
            env=_child_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=_MISE_INSTALL_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _mise_output_detail(exc.stderr or exc.stdout)
        print(
            f"mise install --quietが{_MISE_INSTALL_TIMEOUT_SEC}秒でタイムアウトしました"
            f"（{detail}）。process-loopを継続します。",
            file=sys.stderr,
        )
        return False
    finally:
        _console_title.set_console_title("atk mq process-loop")
    if result.returncode != 0:
        detail = _mise_output_detail(result.stderr or result.stdout)
        print(
            f"mise install --quietに失敗しました（exit code {result.returncode}: {detail}）。process-loopを継続します。",
            file=sys.stderr,
        )
        return False
    return True


def _git_output(args: list[str], cwd: pathlib.Path) -> str:
    """gitコマンドの標準出力を返す。失敗時は空文字を返す。"""
    try:
        return _git_command.output(args, cwd)
    except subprocess.CalledProcessError:
        return ""
    finally:
        _console_title.set_console_title("atk mq process-loop")


def _worktree_is_clean(worktree_path: pathlib.Path) -> bool:
    """index・追跡済み差分・未追跡ファイルが全て空か判定する。"""
    checks = (
        ["git", "diff", "--quiet"],
        ["git", "diff", "--cached", "--quiet"],
    )
    if any(subprocess.run(command, cwd=worktree_path, check=False).returncode != 0 for command in checks):
        return False
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return untracked.returncode == 0 and not untracked.stdout.strip()


def _sync_worktree_with_upstream(local_path: pathlib.Path, worktree_name: str) -> pathlib.Path | None:
    """worktreeを準備して対象リポジトリの上流最新へ追随させる。

    worktree名は反復間で固定のため、前回反復のworktreeがそのまま再利用される。
    前回反復の成果がpush済みでも、その後に他の作業ツリーが上流へ進めた分は
    worktreeのブランチへ入らない。追随を経ないまま次の反復が始まると、
    上流に既にある変更を未実装と誤認して同一内容を二重に実装し、履歴が分岐する。

    worktree未作成の反復では上流最新から新規作成する。
    追随失敗またはdirty状態では`None`を返し、呼び出し元は実装セッションを起動しない。
    """
    worktree_path = local_path / _WORKTREE_PARENT_REL / worktree_name
    upstream_branch = _git_output(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=local_path)
    if not upstream_branch:
        print(f"上流ブランチを解決できないため実装セッションを起動しません: {worktree_path}", file=sys.stderr)
        return None
    created_worktree = False
    if not worktree_path.exists():
        fetch = subprocess.run(["git", "fetch", "origin"], cwd=local_path, capture_output=True, text=True, check=False)
        _console_title.set_console_title("atk mq process-loop")
        if fetch.returncode != 0:
            print(f"worktree作成前のfetchに失敗しました: {fetch.stderr.strip()}", file=sys.stderr)
            return None
        branch = f"worktree-{worktree_name}"
        branch_exists = (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                cwd=local_path,
                check=False,
            ).returncode
            == 0
        )
        command = ["git", "worktree", "add", str(worktree_path), branch]
        if not branch_exists:
            command = ["git", "worktree", "add", "-b", branch, str(worktree_path), upstream_branch]
        created = subprocess.run(command, cwd=local_path, capture_output=True, text=True, check=False)
        _console_title.set_console_title("atk mq process-loop")
        if created.returncode != 0:
            print(f"worktreeの作成に失敗しました: {created.stderr.strip()}", file=sys.stderr)
            return None
        created_worktree = True
    if not worktree_path.is_dir():
        print(f"worktreeの配置先がディレクトリではありません: {worktree_path}", file=sys.stderr)
        return None
    if not _worktree_is_clean(worktree_path):
        print(f"worktreeに未コミット変更があるため実装セッションを起動しません: {worktree_path}", file=sys.stderr)
        return None
    if not created_worktree:
        fetch = subprocess.run(["git", "fetch", "origin"], cwd=worktree_path, capture_output=True, text=True, check=False)
        _console_title.set_console_title("atk mq process-loop")
        if fetch.returncode != 0:
            print(f"worktreeのfetchに失敗しました: {fetch.stderr.strip()}", file=sys.stderr)
            return None
    rebase = subprocess.run(["git", "rebase", upstream_branch], cwd=worktree_path, capture_output=True, text=True, check=False)
    _console_title.set_console_title("atk mq process-loop")
    if rebase.returncode == 0:
        print(f"worktreeを{upstream_branch}へ追随させました: {worktree_path}")
        if _worktree_is_clean(worktree_path):
            return worktree_path
        print(f"追随後のworktreeがdirtyなため実装セッションを起動しません: {worktree_path}", file=sys.stderr)
        return None
    subprocess.run(["git", "rebase", "--abort"], cwd=worktree_path, capture_output=True, text=True, check=False)
    _console_title.set_console_title("atk mq process-loop")
    print(
        f"worktreeの{upstream_branch}への追随に失敗したため実装セッションを起動しません（{rebase.stderr.strip()}）。",
        file=sys.stderr,
    )
    return None


def _build_process_loop_prompt(local_path: pathlib.Path, target_repo_id: str, orchestrator: str) -> str:
    """対象リポジトリのフィードバック処理を依頼する短い目的文を構築する。"""
    prompt = (
        "/goal `agent-toolkit:process-feedbacks`を起動し、"
        f"`{local_path}`で対象リポジトリ`{target_repo_id}`の"
        "フィードバック処理を完遂してください。"
    )
    if target_repo_id == _DOTFILES_REPO_ID:
        prompt += f"公開時は現在のHEADを`{_DOTFILES_PUBLISH_DESTINATION}`へ反映してください。"
    if orchestrator == "codex":
        prompt += (
            "Codexオーケストレーターの連続処理として、開始後に追加されたready項目も同じセッションで順次処理し、"
            "ready項目がなくなった時点で既存の終了工程へ進んでください。"
        )
    return prompt


def _build_session_argv(
    args: argparse.Namespace,
    prompt: str,
    env: dict[str, str],
    *,
    resume_pending: bool,
) -> tuple[list[str], pathlib.Path | None]:
    """選択したオーケストレーターの対話セッション用argvを構築する。"""
    if args.orchestrator == "claude":
        hook_debug_log = _create_hook_debug_log(env)
        argv = ["claude", "--debug=hooks", "--debug-file", str(hook_debug_log)]
        if resume_pending:
            argv.append("--resume" if not args.resume else f"--resume={args.resume}")
        else:
            model = args.model or "opus"
            argv.extend(("--permission-mode=auto", "--model", model, "--autocompact", "1m", prompt))
        return argv, hook_debug_log

    argv = ["codex"]
    if resume_pending:
        argv.append("resume")
    if args.model is not None:
        argv.extend(("--model", args.model))
    if resume_pending:
        if args.resume:
            argv.append(args.resume)
    else:
        argv.append(prompt)
    return argv, None


def _is_normal_session_exit(orchestrator: str, returncode: int) -> bool:
    """オーケストレーター別の正常終了コードを判定する。"""
    if orchestrator == "claude":
        return returncode in _CLAUDE_NORMAL_EXIT_CODES
    return returncode == 0


def _wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> bool:
    """watchdogでinbox配下を監視し、変更検知またはタイムアウトまで待機する。

    変更検知時はデバウンス窓（3秒）で追加イベントを畳み込んでから返る
    （他端末書き込みは10分タイムアウト側のremote同期で拾うため、変更検知時は同期しない）。
    タイムアウト時は他端末投入を反映するため`_repo_lock`保持下で`_pull`する。
    他プロセスとの一時的な競合・ネットワーク断等で`_pull`が失敗した場合は例外を捕捉して
    stderrへ警告出力し、常駐ループの待機動作を続ける。
    戻り値は`True`=変更検知で復帰、`False`=タイムアウトで復帰を表す。
    呼び出し元は`False`復帰時のみ常駐コードの更新チェック（`_check_and_restart_on_update`）を行う。
    """
    del target_repo_id  # 現状の監視粒度ではrepo単位フィルタは行わない
    change_event = threading.Event()
    observer = watchdog.observers.Observer()
    handler = _ChangeHandler(change_event)
    _ensure_inbox_dirs(private_notes)
    observer.schedule(handler, str(private_notes / "processing"), recursive=False)
    observer.schedule(handler, str(private_notes / "inbox"), recursive=False)
    observer.start()
    try:
        if change_event.wait(timeout=_POLL_INTERVAL_SEC):
            while True:
                change_event.clear()
                if not change_event.wait(timeout=_DEBOUNCE_SEC):
                    break
            return True
        try:
            with _repo_lock(private_notes):
                _pull(private_notes)
        except subprocess.CalledProcessError as exc:
            print(f"remote同期に失敗（待機ループ続行）: {exc}", file=sys.stderr)
        return False
    finally:
        observer.stop()
        observer.join()


def _pull_private_notes(private_notes: pathlib.Path) -> bool:
    """private-notesをlock下で同期し、処理開始に利用できる状態かを返す。"""
    try:
        with _repo_lock(private_notes):
            _pull(private_notes)
    except subprocess.CalledProcessError as exc:
        print(f"remote同期に失敗（子セッションを起動せず待機します）: {exc}", file=sys.stderr)
        return False
    return True


def _ensure_inbox_dirs(private_notes: pathlib.Path) -> None:
    """watchdog監視対象のinboxディレクトリを事前作成する。"""
    (private_notes / "processing").mkdir(parents=True, exist_ok=True)
    (private_notes / "inbox").mkdir(parents=True, exist_ok=True)


def _without_resume_args(argv: list[str]) -> list[str]:
    """初回限定のresume指定と任意値をargvから除去する。"""
    result: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg.startswith("--resume="):
            index += 1
            continue
        if arg == "--resume":
            index += 1
            if index < len(argv) and not argv[index].startswith("-"):
                index += 1
            continue
        result.append(arg)
        index += 1
    return result


def _without_internal_mise_refreshed(argv: list[str]) -> list[str]:
    """一回限りのmise再評価済み指定を再起動引数から除去する。"""
    return [arg for arg in argv if arg != _INTERNAL_MISE_REFRESHED_ARG]


def _build_restart_target(
    argv: list[str],
    dotfiles_root: pathlib.Path | None = None,
    *,
    resume_consumed: bool = False,
    mise_refreshed: bool = False,
) -> tuple[pathlib.Path, list[str]]:
    """再起動対象のスクリプトパスと引数列を返す。

    `dotfiles_root`を解決できた場合は再起動先を当該チェックアウト配下の`atk.py`へ切り替える。
    `atk`がプラグインキャッシュ配下のバージョン別コピーから起動された場合、`argv[0]`は
    更新前バージョンのディレクトリを指す。更新は新しいバージョンディレクトリへ展開されるため、
    `argv[0]`のまま再起動すると更新を検知するたびに旧コードを再実行し続ける。
    """
    script = pathlib.Path(argv[0]).resolve()
    if dotfiles_root is not None:
        canonical = dotfiles_root / "agent-toolkit" / "scripts" / "atk.py"
        if canonical.exists():
            script = canonical
    rest = _without_internal_mise_refreshed(argv[1:])
    if resume_consumed:
        rest = _without_resume_args(rest)
    if mise_refreshed:
        rest.append(_INTERNAL_MISE_REFRESHED_ARG)
    return script, rest


def _restart_process_loop(
    argv: list[str],
    dotfiles_root: pathlib.Path | None = None,
    *,
    resume_consumed: bool = False,
    mise_refreshed: bool = False,
) -> None:
    """次に起動するスクリプトと引数をランチャーへ渡して再起動を要求する。

    セッション終了後経路・待機中経路の双方から呼ぶ共通ヘルパーとする。
    ランチャー経由で起動された場合は受け渡しファイルへ次の起動対象を書き、
    専用の終了コードで終了する。ランチャーは同一プロセスで次の実体を`uv run`で起動するため、
    PEP 723の依存解決が再実行され、かつプロセス階層が増えない。
    受け渡しファイルの指定が無い直接起動では、従来どおり自プロセスを置き換える。
    """
    script, rest = _build_restart_target(
        argv,
        dotfiles_root,
        resume_consumed=resume_consumed,
        mise_refreshed=mise_refreshed,
    )
    spec_path = os.environ.get(_RESTART_SPEC_ENV)
    if spec_path:
        pathlib.Path(spec_path).write_text("\n".join([str(script), *rest]) + "\n", encoding="utf-8")
        sys.exit(_RESTART_EXIT_CODE)
    executable = _resolve_executable("uv")
    if executable is None:
        return
    restart_argv = [executable, "run", "--no-project", "--script", str(script), *rest]
    os.execv(executable, restart_argv)


def _code_hash(scripts_dir: pathlib.Path) -> str:
    """`scripts_dir`配下の`*.py`（`*_test.py`除く）内容から安定ハッシュを算出する。

    ファイル名でソートしてから相対順序を固定し、ファイル名と内容の各バイト列へ8byte長接頭辞を付けて
    境界を一意にしたうえでSHA-256を取る。
    常駐プロセスが起動時に読み込んだPythonコード群と現在のコード群の同一性判定に用いる。
    テストコードの変更では再起動を要さないため`*_test.py`は対象から除く。
    """
    digest = hashlib.sha256()
    for path in sorted(p for p in scripts_dir.glob("*.py") if not p.name.endswith("_test.py")):
        name_bytes = path.name.encode("utf-8")
        content = path.read_bytes()
        for field in (name_bytes, content):
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
    return digest.hexdigest()


def _resolve_dotfiles_root() -> pathlib.Path | None:
    """dotfiles本体チェックアウトの絶対パスを解決する。存在しなければ`None`を返す。

    `atk`コマンドは`~/.claude/plugins/cache/<marketplace>/agent-toolkit/<version>/`配下の
    バージョン別キャッシュコピーから実行される場合がある
    （`install-claude.sh`が生成する`~/.local/bin/atk`ラッパーが実行時に解決する参照先）。
    その場合`pathlib.Path(__file__)`はdotfilesチェックアウトの外側（キャッシュ配下のバージョンディレクトリ）を
    指すため、自己コード更新検知の基準には使用できない
    （キャッシュ配下は`agent-toolkit/`のみを含む部分ツリーで、`.git`もdotfiles全体の履歴も持たない）。
    利用者ごとに単一の`~/dotfiles`チェックアウトを持つ運用前提
    （`.bashrc`が`$HOME/dotfiles/bin`を直接PATHへ追加する既存運用と同じ前提。
    `atk mq process-loop`の対象リポジトリ（`--target-repo`）とは独立に、常に`~/dotfiles`を指す）に基づき、
    ホームディレクトリ直下の`dotfiles/`を直接の解決先とする。
    """
    candidate = pathlib.Path.home() / "dotfiles"
    return candidate if (candidate / ".git").exists() else None


def _has_upstream_diff(dotfiles_root: pathlib.Path) -> bool:
    """`dotfiles_root`のgit upstreamとの間に未取込コミットがあるかを判定する。

    同一の作業コピーを対象とする常駐インスタンスが複数並行するため、
    `git fetch`と`rev-list`を`_repo_lock(dotfiles_root)`保持下で実行する。
    ロックが無い状態ではgitの内部ロック競合により`fetch`がexit 128で失敗する。
    `git fetch`失敗・upstream未設定等でコマンドが失敗した場合は差分なし扱いとし、
    警告をstderrへ出力したうえで待機ループを継続させる（常駐を終了させない）。
    警告本文にはgitの標準エラー出力を含める。終了コードのみでは原因を特定できないためである。
    """
    try:
        with _repo_lock(dotfiles_root):
            subprocess.run(
                ["git", "-C", str(dotfiles_root), "fetch", "--quiet"],
                check=True,
                capture_output=True,
                text=True,
            )
            _console_title.set_console_title("atk mq process-loop")
            result = subprocess.run(
                ["git", "-C", str(dotfiles_root), "rev-list", "HEAD..@{upstream}", "--count"],
                check=True,
                capture_output=True,
                text=True,
            )
            _console_title.set_console_title("atk mq process-loop")
        return int(result.stdout.strip()) > 0
    except (subprocess.CalledProcessError, ValueError) as exc:
        stderr = getattr(exc, "stderr", None)
        detail = f": {stderr.strip()}" if isinstance(stderr, str) and stderr.strip() else ""
        print(f"上流差分確認に失敗しました（待機ループを続行します）: {exc}{detail}", file=sys.stderr)
        return False


def _check_and_restart_on_update(
    dotfiles_root: pathlib.Path,
    startup_hash: str,
    argv: list[str],
    *,
    mark_mise_refreshed: bool = False,
) -> bool:
    """待機ループのタイムアウト復帰時に上流差分確認・`update-dotfiles`実行・再起動判定を行う。

    上流差分がある場合のみ`update-dotfiles`を実行し（無条件実行による無出力ノイズを避けるため）、
    その成否に関わらず常駐コードのハッシュを再計算して起動時ハッシュと比較する。
    ハッシュが変化した場合のみ再起動する（他プロセスが先に`update-dotfiles`を完了させ
    リポジトリが最新化済みのケース、ローカル手編集のケースの双方を検知できる）。
    出力は静音を基本とし、上流差分なし・ハッシュ不変の場合は無出力とする。
    戻り値は、この呼び出しで`update-dotfiles`が成功したかを表す。
    """
    update_succeeded = False
    if _has_upstream_diff(dotfiles_root):
        executable = _resolve_executable("update-dotfiles")
        if executable is not None:
            result = subprocess.run([executable, "--force"], check=False, env=_child_env())
            _console_title.set_console_title("atk mq process-loop")
            update_succeeded = result.returncode == 0
            if not update_succeeded:
                print(
                    f"update-dotfilesに失敗しました（exit code {result.returncode}）。待機ループを続行します。",
                    file=sys.stderr,
                )
    current_hash = _code_hash(dotfiles_root / "agent-toolkit" / "scripts")
    if current_hash != startup_hash:
        print("常駐コードの更新を検知したためprocess-loopを再起動します。")
        _process_loop_log.append("restart_on_wait_loop_update")
        _restart_process_loop(
            argv,
            dotfiles_root,
            mise_refreshed=mark_mise_refreshed and update_succeeded,
        )
    return update_succeeded


def _update_before_session(
    private_notes: pathlib.Path,
    dotfiles_root: pathlib.Path | None,
    startup_hash: str | None,
    argv: list[str],
    env: dict[str, str],
    *,
    mark_mise_refreshed: bool = False,
) -> tuple[bool, bool]:
    """ready項目の処理前にdotfilesとprivate-notesを同期する。

    戻り値は、子セッションを起動できるかと`update-dotfiles`が成功したかの組とする。
    """
    executable = _resolve_executable("update-dotfiles")
    if executable is None:
        print("update-dotfilesを利用できないため、子セッションを起動せず待機します。", file=sys.stderr)
        return False, False
    result = subprocess.run([executable, "--force"], check=False, env=env)
    _console_title.set_console_title("atk mq process-loop")
    if result.returncode != 0:
        print(
            f"update-dotfilesに失敗しました（exit code {result.returncode}）。子セッションを起動せず待機します。",
            file=sys.stderr,
        )
        return False, False
    if dotfiles_root is not None and startup_hash is not None:
        current_hash = _code_hash(dotfiles_root / "agent-toolkit" / "scripts")
        if current_hash != startup_hash:
            print("処理開始前に常駐コードの更新を検知したためprocess-loopを再起動します。")
            _process_loop_log.append("restart_before_session_update")
            _restart_process_loop(argv, dotfiles_root, mise_refreshed=mark_mise_refreshed)
    return _pull_private_notes(private_notes), True


def _prepare_session_target(
    local_path: pathlib.Path,
    target_repo_id: str,
    orchestrator: str,
    prompt: str,
    *,
    resume_pending: bool,
) -> tuple[pathlib.Path, str] | None:
    """新規dotfilesセッション用worktreeを用意して実行先とpromptを返す。"""
    if resume_pending or target_repo_id != _DOTFILES_REPO_ID:
        return local_path, prompt
    # `--worktree`は使わない。CLIのworktree隔離ガードが、gitへの言及を問わず
    # ANSI-Cクォート・制御構造・コマンド置換など18種のシェル構文を拒否するため。
    prepared = _sync_worktree_with_upstream(local_path, _DOTFILES_WORKTREE_NAME)
    if prepared is None:
        return None
    return prepared, _build_process_loop_prompt(prepared, target_repo_id, orchestrator)


def _run_process_session(
    args: argparse.Namespace,
    session_path: pathlib.Path,
    session_prompt: str,
    env: dict[str, str],
    *,
    resume_pending: bool,
    dotfiles_root: pathlib.Path | None,
    mise_refresh_enabled: bool,
) -> bool:
    """子セッションを1回実行し、update-dotfilesの成功有無を返す。"""
    session_argv, hook_debug_log = _build_session_argv(
        args,
        session_prompt,
        env,
        resume_pending=resume_pending,
    )
    if hook_debug_log is not None:
        print(f"Claude hook診断ログ: {hook_debug_log}")
    _process_loop_log.append("session_start")
    session_started_at = time.monotonic()
    result = subprocess.run(
        session_argv,
        check=False,
        env=_session_env(env, args.orchestrator),
        cwd=session_path,
        creationflags=_session_creation_flags(args.orchestrator),
    )
    _console_title.set_console_title("atk mq process-loop")
    _process_loop_log.append(
        "session_end",
        elapsed_sec=round(time.monotonic() - session_started_at, 3),
        returncode=result.returncode,
    )
    if not _is_normal_session_exit(args.orchestrator, result.returncode):
        print(f"{args.orchestrator}がexit code {result.returncode}で異常終了しました。", file=sys.stderr)
        sys.exit(result.returncode)
    if args.no_update:
        return False
    executable = _resolve_executable("update-dotfiles")
    if executable is None:
        return False
    print("update-dotfilesを実行してprocess-loopを再起動します。")
    update_result = subprocess.run([executable, "--force"], check=False, env=env)
    _console_title.set_console_title("atk mq process-loop")
    update_succeeded = update_result.returncode == 0
    _restart_process_loop(
        sys.argv,
        dotfiles_root,
        resume_consumed=True,
        mise_refreshed=mise_refresh_enabled and update_succeeded,
    )
    return update_succeeded


def _check_process_loop_alerts(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    target_repo_id: str,
    local_path: pathlib.Path,
    last_alert_check: float | None,
) -> tuple[float | None, int]:
    """確認間隔を満たす場合だけアラートを収集し、確認時刻と投入件数を返す。"""
    if args.no_alerts:
        return last_alert_check, 0
    monotonic_now = time.monotonic()
    if last_alert_check is not None and monotonic_now - last_alert_check < args.alert_interval:
        return last_alert_check, 0
    try:
        submitted = _alerts.check_and_submit_alerts(
            private_notes,
            target_repo_id,
            local_path,
            forge=args.alert_forge,
            now=datetime.datetime.now(),
        )
    except (_alerts.AlertCollectError, subprocess.CalledProcessError) as exc:
        print(f"警告: アラート確認処理に失敗しました: {exc}", file=sys.stderr)
        submitted = 0
    _process_loop_log.append("alert_check", submitted=submitted)
    return monotonic_now, submitted


def _restore_process_loop_env(previous_values: dict[str, str | None]) -> None:
    """process-loop識別環境変数を呼び出し前の状態へ戻す。"""
    for key, previous_value in previous_values.items():
        if previous_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous_value


def _cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """process-loopサブコマンド: 選択した対話セッションと待機ループを常駐で繰り返す。

    初回と0件待機からの復帰時はprivate-notesを同期し、ready項目があれば`update-dotfiles`と
    private-notesの再同期を終えてからセッションを起動する。同期失敗時は子を起動せず待機へ戻る。
    件数は選択したオーケストレーターのセッション起動要否だけに使う。
    分類結果の保存、依存判定、セッション上限、実行順、着手可否判定、バッチ選択は
    process-feedbacksが担う。
    初回再開時は選択したCLIのresume形式だけを渡し、再開後のプロンプト入力は利用者へ委ねる。
    新規起動は対象リポジトリでprocess-feedbacksを完遂する短い`/goal`条件を登録する。
    Claude Codeは既定modelの`opus`、権限mode、コンパクション設定を従来どおり使う。
    全Claude子セッションでhook限定debug logを有効化し、子環境の`CLAUDE_CONFIG_DIR/debug/`、
    未設定時はユーザーホーム配下`.claude/debug/`へ所有者限定の一意なログを保存する。
    Codexは対話CLIを使い、model未指定時はCodex設定の既定値を使う。
    Claude Codeは0・-15・15・143、Codexは0を正常終了とする。
    正常終了した場合、
    `--no-update`未指定なら`update-dotfiles`を実行してから
    `_restart_process_loop`でランチャーへ再起動を要求する。
    それ以外のexit codeで終了した場合は同じexit codeでCLI自体を終了する。
    件数0の間はアラート自動検出（既定有効、`--no-alerts`で無効化）を`--alert-interval`
    秒間隔で実行し、新規アラートを検知した場合はフィードバックへ投入して即座に次反復へ進む。
    `--alert-forge`は検出対象（github/gitlab/auto）を指定する。
    件数0の間はwatchdogによる変更検知と10分間隔のremote同期を含む待機ループへ進み、
    待機に入った旨を1度出力する。
    待機ループがタイムアウト（変更未検知）で復帰した場合、上流差分があれば`update-dotfiles`を実行したうえで、
    `~/dotfiles`チェックアウト内`agent-toolkit/scripts/`配下コードの起動時ハッシュと現在のハッシュを比較し、
    差異があれば同じく`_restart_process_loop`で再起動する。他プロセスが先に`update-dotfiles`を
    完了させていてもローカルコードの変更を独立して検知できる。`--no-update`指定時はこの待機中の
    更新反映・再起動チェックも抑止する。`~/dotfiles`チェックアウトが見つからない環境
    （`atk`がプラグインキャッシュ配下から実行され、かつ`~/dotfiles`が存在しない場合）ではこのチェック自体を行わない。
    Ctrl+Cで常駐ループを終了する。

    各反復で件数取得直後・セッション起動前後に`_process_loop_log.append`で観測イベント
    （`loop_iter_start`・`session_start`・`session_end`）を記録する
    （`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`未設定時はno-op）。
    待機ループ復帰時に自己コード更新を検知して再起動した場合は`restart_on_wait_loop_update`を記録する。
    dotfilesを対象とし、更新を有効にした起動ではmiseのlatest指定ツールを起動時と24時間ごとに再評価する。
    成功した`update-dotfiles`直後は再評価時刻を更新し、正常再起動先へ一回限りの内部指定を渡して重複を避ける。
    """
    local_path = _resolve_local_worktree(args.target_repo)
    target_repo_id = _resolve_repo_id(args.target_repo, cwd=local_path)
    prompt = _build_process_loop_prompt(local_path, target_repo_id, args.orchestrator)
    dotfiles_root = _resolve_dotfiles_root()
    startup_hash = _code_hash(dotfiles_root / "agent-toolkit" / "scripts") if dotfiles_root else None
    mise_refresh_root = dotfiles_root if target_repo_id == _DOTFILES_REPO_ID and not args.no_update else None
    mise_refreshed_at: float | None = None
    if mise_refresh_root is not None:
        if not args.internal_mise_refreshed:
            _refresh_mise_tools(mise_refresh_root)
        mise_refreshed_at = time.monotonic()
    print(f"atk mq process-loop 常駐モード開始（対象: {local_path}）。Ctrl+Cで終了。")
    last_alert_check: float | None = None
    # 自プロセスのos.environにも設定し、本関数内の_process_loop_log.append呼び出し
    # （自プロセス側の観測記録）を有効化する。claude起動時は明示的な`env=env`引数で継承する。
    # 関数終了時に元の値へ戻し、in-process呼び出し（テスト等）への環境変数漏洩を避ける。
    previous_env_values = {
        _PROCESS_LOOP_SESSION_ENV: os.environ.get(_PROCESS_LOOP_SESSION_ENV),
        _LEGACY_PROCESS_LOOP_SESSION_ENV: os.environ.get(_LEGACY_PROCESS_LOOP_SESSION_ENV),
    }
    os.environ[_PROCESS_LOOP_SESSION_ENV] = "1"
    os.environ[_LEGACY_PROCESS_LOOP_SESSION_ENV] = "1"
    env = _child_env()
    resume_pending = args.resume is not None
    refresh_before_session = True
    with _console_title.console_title("atk mq process-loop"):
        try:
            try:
                while True:
                    if not _pull_private_notes(private_notes):
                        print("同期を再試行するまで変更検知を待機します。")
                        _wait_for_changes(private_notes, target_repo_id)
                        refresh_before_session = True
                        continue
                    count = _count_pending_entries(private_notes, target_repo=target_repo_id)
                    if count > 0 and refresh_before_session and not args.no_update:
                        session_ready, update_succeeded = _update_before_session(
                            private_notes,
                            dotfiles_root,
                            startup_hash,
                            sys.argv,
                            env,
                            mark_mise_refreshed=mise_refresh_root is not None,
                        )
                        if update_succeeded and mise_refresh_root is not None:
                            mise_refreshed_at = time.monotonic()
                        if not session_ready:
                            print("同期を再試行するまで変更検知を待機します。")
                            _wait_for_changes(private_notes, target_repo_id)
                            refresh_before_session = True
                            continue
                        count = _count_pending_entries(private_notes, target_repo=target_repo_id)
                    _process_loop_log.append("loop_iter_start", count=count)
                    if count > 0:
                        refresh_before_session = False
                        print(f"{count}件のフィードバック/回答済みTBDを検知。{args.orchestrator}へ委譲します。")
                        current_resume_pending = resume_pending
                        if current_resume_pending:
                            resume_pending = False
                        prepared_target = _prepare_session_target(
                            local_path,
                            target_repo_id,
                            args.orchestrator,
                            prompt,
                            resume_pending=current_resume_pending,
                        )
                        if prepared_target is None:
                            print("worktree準備を再試行するまで変更検知を待機します。")
                            _wait_for_changes(private_notes, target_repo_id)
                            refresh_before_session = True
                            continue
                        session_path, session_prompt = prepared_target
                        update_succeeded = _run_process_session(
                            args,
                            session_path,
                            session_prompt,
                            env,
                            resume_pending=current_resume_pending,
                            dotfiles_root=dotfiles_root,
                            mise_refresh_enabled=mise_refresh_root is not None,
                        )
                        if update_succeeded and mise_refresh_root is not None:
                            mise_refreshed_at = time.monotonic()
                        continue
                    last_alert_check, submitted = _check_process_loop_alerts(
                        args,
                        private_notes,
                        target_repo_id,
                        local_path,
                        last_alert_check,
                    )
                    if submitted > 0:
                        print(f"アラート監視により{submitted}件のフィードバックを投入しました。")
                        refresh_before_session = True
                        continue
                    print("0件のため変更検知を待機します。")
                    changed = _wait_for_changes(private_notes, target_repo_id)
                    refresh_before_session = True
                    if (
                        mise_refresh_root is not None
                        and mise_refreshed_at is not None
                        and time.monotonic() - mise_refreshed_at >= _MISE_REFRESH_INTERVAL_SEC
                    ):
                        _refresh_mise_tools(mise_refresh_root)
                        mise_refreshed_at = time.monotonic()
                    if not changed and not args.no_update and dotfiles_root is not None and startup_hash is not None:
                        update_succeeded = _check_and_restart_on_update(
                            dotfiles_root,
                            startup_hash,
                            sys.argv,
                            mark_mise_refreshed=mise_refresh_root is not None,
                        )
                        if update_succeeded and mise_refresh_root is not None:
                            mise_refreshed_at = time.monotonic()
            except KeyboardInterrupt:
                print("Ctrl+Cを検知しました。常駐モードを終了します。")
        finally:
            _restore_process_loop_env(previous_env_values)
