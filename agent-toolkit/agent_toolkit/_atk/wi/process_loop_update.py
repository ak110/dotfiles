"""`atk wi process-loop`の自己更新の確認と、更新を反映する再起動。"""

import hashlib
import os
import pathlib
import subprocess
import sys

from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._git import command as _git_command

_INTERNAL_MISE_REFRESHED_ARG = "--internal-mise-refreshed"


_INTERNAL_DOTFILES_UPDATED_ARG = "--internal-dotfiles-updated"


# ランチャーへ再起動を要求する終了コード。
_RESTART_EXIT_CODE = 75


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
        if arg == "--auto-resume":
            index += 1
            continue
        result.append(arg)
        index += 1
    return result


def _without_internal_mise_refreshed(argv: list[str]) -> list[str]:
    """一回限りのmise再評価済み指定を再起動引数から除去する。"""
    return [arg for arg in argv if arg != _INTERNAL_MISE_REFRESHED_ARG]


def _without_internal_dotfiles_updated(argv: list[str]) -> list[str]:
    """一回限りのdotfiles更新済み指定を再起動引数から除去する。"""
    return [arg for arg in argv if arg != _INTERNAL_DOTFILES_UPDATED_ARG]


def _build_restart_target(
    argv: list[str],
    dotfiles_root: pathlib.Path | None = None,
    *,
    resume_consumed: bool = False,
    mise_refreshed: bool = False,
    dotfiles_updated: bool = False,
) -> tuple[pathlib.Path, list[str]]:
    """再起動対象のスクリプトパスと引数列を返す。

    `dotfiles_root`を解決できた場合は再起動先をそのチェックアウト配下の`atk.py`へ切り替える。
    `atk`がプラグインキャッシュ配下のバージョン別コピーから起動された場合、`argv[0]`は
    更新前バージョンのディレクトリを指す。更新は新しいバージョンディレクトリへ展開されるため、
    `argv[0]`のまま再起動すると更新を検知するたびに旧コードを再実行し続ける。
    """
    script = pathlib.Path(argv[0]).resolve()
    if dotfiles_root is not None:
        canonical = dotfiles_root / "agent-toolkit" / "agent_toolkit" / "atk.py"
        if canonical.exists():
            script = canonical
    rest = _without_internal_dotfiles_updated(_without_internal_mise_refreshed(argv[1:]))
    if resume_consumed:
        rest = _without_resume_args(rest)
    if mise_refreshed:
        rest.append(_INTERNAL_MISE_REFRESHED_ARG)
    if dotfiles_updated:
        rest.append(_INTERNAL_DOTFILES_UPDATED_ARG)
    return script, rest


def restart_process_loop(
    argv: list[str],
    dotfiles_root: pathlib.Path | None = None,
    *,
    resume_consumed: bool = False,
    mise_refreshed: bool = False,
    dotfiles_updated: bool = False,
) -> None:
    """次に起動するスクリプトと引数をランチャーへ渡して再起動を要求する。

    セッション終了後と待機中の双方で呼ぶ共通ヘルパーとする。
    ランチャー経由で起動された場合は受け渡しファイルへ次の起動対象を書き、
    専用の終了コードで終了する。ランチャーは同一プロセスで次の実体を`uv run`で起動するため、
    plugin projectの依存解決が再実行され、かつプロセス階層が増えない。
    受け渡しファイルの指定が無い直接起動でも、同じprojectを指定して自プロセスを置き換える。
    """
    script, rest = _build_restart_target(
        argv,
        dotfiles_root,
        resume_consumed=resume_consumed,
        mise_refreshed=mise_refreshed,
        dotfiles_updated=dotfiles_updated,
    )
    spec_path = os.environ.get(_pl_env.RESTART_SPEC_ENV)
    if spec_path:
        try:
            pathlib.Path(spec_path).write_text("\n".join([str(script), *rest]) + "\n", encoding="utf-8")
        except OSError as error:
            _process_loop_log.append("restart_spec_write_failed", error=type(error).__name__, detail=str(error))
            raise
        _process_loop_log.append("restart_request", method="launcher", code=_RESTART_EXIT_CODE, script=str(script))
        sys.exit(_RESTART_EXIT_CODE)
    executable = _pl_env.resolve_executable("uv")
    if executable is None:
        _process_loop_log.append("restart_unavailable", reason="uv_missing")
        return
    _process_loop_log.append("restart_request", method="exec", script=str(script))
    restart_argv = [
        executable,
        "run",
        "--project",
        str(script.parent.parent),
        "--locked",
        "--no-default-groups",
        str(script),
        *rest,
    ]
    os.execv(executable, restart_argv)


def code_hash(scripts_dir: pathlib.Path) -> str:
    """`scripts_dir`配下を再帰走査した実装用`*.py`の内容から安定ハッシュを算出する。

    相対パスでソートして順序を固定し、相対パスと内容の各バイト列へ8byte長接頭辞を付けて
    境界を一意にしたうえでSHA-256を取る。
    常駐プロセスが起動時に読み込んだPythonコード群と現在のコード群の同一性判定に用いる。
    テストコードの変更では再起動を要さないため`*_test.py`と`__pycache__`配下は対象から除く。
    """
    digest = hashlib.sha256()
    for path in sorted(
        p
        for p in scripts_dir.rglob("*.py")
        if not p.name.endswith("_test.py") and "__pycache__" not in p.relative_to(scripts_dir).parts
    ):
        name_bytes = path.relative_to(scripts_dir).as_posix().encode("utf-8")
        content = path.read_bytes()
        for field in (name_bytes, content):
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
    return digest.hexdigest()


def resolve_dotfiles_root() -> pathlib.Path | None:
    """dotfiles本体チェックアウトの絶対パスを解決する。存在しなければ`None`を返す。

    `atk`コマンドは`~/.claude/plugins/cache/<marketplace>/agent-toolkit/<version>/`配下の
    バージョン別キャッシュコピーから実行される場合がある
    （`install-claude.sh`が生成する`~/.local/bin/atk`ラッパーが実行時に解決する参照先）。
    その場合`pathlib.Path(__file__)`はdotfilesチェックアウトの外側（キャッシュ配下のバージョンディレクトリ）を
    指すため、自己コード更新検知の基準には使用できない
    （キャッシュ配下は`agent-toolkit/`のみを含む部分ツリーで、`.git`もdotfiles全体の履歴も持たない）。
    OSアカウントごとに単一の`~/dotfiles`チェックアウトを持つ運用前提
    （`.bashrc`が`$HOME/dotfiles/bin`を直接PATHへ追加する既存運用と同じ前提。
    `atk wi process-loop`の対象リポジトリ（`--target-repo`）とは独立に、常に`~/dotfiles`を指す）に基づき、
    ホームディレクトリ直下の`dotfiles/`を直接の解決先とする。
    """
    candidate = pathlib.Path.home() / "dotfiles"
    return candidate if (candidate / ".git").exists() else None


def _has_upstream_diff(dotfiles_root: pathlib.Path) -> bool:
    """`dotfiles_root`のgit upstreamとの間に未取込コミットがあるかを判定する。

    同一の作業コピーを対象とする常駐インスタンスが複数並行するため、
    `git fetch`と`rev-list`を`_wi_sync.repo_lock(dotfiles_root)`保持下で実行する。
    ロックが無い状態ではgitの内部ロック競合により`fetch`がexit 128で失敗する。
    `git fetch`失敗・upstream未設定等でコマンドが失敗した場合は差分なし扱いとし、
    警告をstderrへ出力したうえで待機ループを継続させる（常駐を終了させない）。
    警告本文にはgitの標準エラー出力を含める。終了コードのみでは原因を特定できないためである。
    """
    try:
        with _wi_sync.repo_lock(dotfiles_root):
            _git_command.run(["-C", str(dotfiles_root), "fetch", "--quiet"], check=True, capture_output=True, text=True)
            _console_title.set_console_title("atk wi process-loop")
            result = _git_command.run(
                ["-C", str(dotfiles_root), "rev-list", "HEAD..@{upstream}", "--count"],
                check=True,
                capture_output=True,
                text=True,
            )
            _console_title.set_console_title("atk wi process-loop")
        return int(result.stdout.strip()) > 0
    except (subprocess.CalledProcessError, ValueError) as exc:
        stderr = getattr(exc, "stderr", None)
        detail = f": {stderr.strip()}" if isinstance(stderr, str) and stderr.strip() else ""
        _next_action.report(
            f"上流差分確認に失敗しました（待機ループを続行します）: {exc}{detail}",
            next_action=(f"対応不要（待機は継続した）。繰り返す場合は`git -C {dotfiles_root} fetch`で上流への到達を確認する"),
        )
        return False


def check_and_restart_on_update(
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
        executable = _pl_env.resolve_executable("update-dotfiles")
        if executable is not None:
            result = subprocess.run([executable], check=False, env=_pl_env.child_env())
            _console_title.set_console_title("atk wi process-loop")
            update_succeeded = result.returncode == 0
            if not update_succeeded:
                _next_action.report(
                    f"update-dotfilesに失敗しました（exit code {result.returncode}）。待機ループを続行します。",
                    next_action="対応不要（待機は継続した）。繰り返す場合は`update-dotfiles`を手作業で実行して原因を確認する",
                )
    current_hash = code_hash(dotfiles_root / "agent-toolkit" / "scripts")
    if current_hash != startup_hash:
        print("常駐コードの更新を検知したためprocess-loopを再起動します。")
        _process_loop_log.append("restart_on_wait_loop_update")
        restart_process_loop(
            argv,
            dotfiles_root,
            mise_refreshed=mark_mise_refreshed and update_succeeded,
            dotfiles_updated=update_succeeded,
        )
    return update_succeeded


def update_before_session(
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
    更新による再起動先には一回限りの指定を渡し、同じ上流状態への開始前更新を抑止する。

    同期が非0で終了した場合も子セッションを起動する。同期の終了コードは、失敗した段の種類、
    失敗の回復可能性およびAWIの内容のいずれも表さないため、消化を止める判定の根拠から外す。
    判定は、同期処理が残す構造化された記録を子セッション側のエージェントが読んで行う。
    同期処理そのものを起動できない場合だけは、判定材料となる記録も生じないため待機を続ける。
    """
    executable = _pl_env.resolve_executable("update-dotfiles")
    if executable is None:
        _next_action.report(
            "update-dotfilesを利用できないため、子セッションを起動せず待機します。",
            next_action="`update-dotfiles`をPATHへ導入してからprocess-loopを再起動する",
        )
        return False, False
    result = subprocess.run([executable], check=False, env=env)
    _console_title.set_console_title("atk wi process-loop")
    update_succeeded = result.returncode == 0
    if not update_succeeded:
        _next_action.report(
            f"update-dotfilesに失敗しました（exit code {result.returncode}）。",
            next_action="対応不要（子セッションの起動は続行した）。同期結果の記録は子セッションが判定する",
        )
    if dotfiles_root is not None and startup_hash is not None:
        current_hash = code_hash(dotfiles_root / "agent-toolkit" / "scripts")
        if current_hash != startup_hash:
            print("処理開始前に常駐コードの更新を検知したためprocess-loopを再起動します。")
            _process_loop_log.append("restart_before_session_update")
            restart_process_loop(
                argv,
                dotfiles_root,
                mise_refreshed=mark_mise_refreshed,
                dotfiles_updated=update_succeeded,
            )
    return _pl_watch.pull_private_notes(private_notes), update_succeeded
