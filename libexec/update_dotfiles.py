#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.15"
# dependencies = ["filelock>=3.30", "platformdirs>=4.0", "psutil"]
# ///
r"""dotfilesリポジトリを最新化するPEP 723スクリプト。

`chezmoi git pull --rebase` → Git管理のdotfilesプラグインを名前順にpull → `chezmoi init`（テンプレート再展開） →
`chezmoi status`（apply予定ファイルの表示） → `chezmoi diff --no-pager` →
`chezmoi apply --force`を、プロセス間排他ロック下で直列実行する。
画面には4段の進捗を示し、diffの詳細は永続ログへ記録する。
pull前にルート`mise.lock`の差分を破棄する。`mise.lock`はコミット済みの`mise.toml`から
再生成でき、更新処理のmise操作が書き戻した差分をユーザーの未コミット内容として保持しないためである。
それ以外の未コミット内容は退避してpull後に復元する。
pullまたは退避復元が競合した場合は、元HEADと未コミット内容を専用参照へ保存し、
設定済み上流へ作業branchを合わせて更新を継続する。

複数の`update-dotfiles`起動（`atk wi process-loop`の複数常駐・手動実行との重複等）が
同時に`git pull`・`chezmoi apply`を実行するとpullとファイル操作の競合を招くため、
`filelock`でプロセス間直列化する。ロック取得に失敗（タイムアウト）した場合は
exit code 1で終了し、他プロセスの完了を待って再実行するよう促す。

薄いランチャー`bin/update-dotfiles`・`bin/update-dotfiles.cmd`から
`uv run --no-project --script`形式で起動される。dotfilesルートは本ファイルの配置
（`libexec/update_dotfiles.py`）から`Path(__file__)`起点で解決する
（`Path.home()`起点は`$HOME`と実チェックアウト先の不一致を招くため使わない）。

各段は、後続の段の前提になる段と、失敗しても後続の段の前提を壊さない段に分ける。
前提になる段は、ロックの取得、git timeout設定の検証、dotfiles自身のGit更新（所有の確認・index管理・
退避・pull・競合回復）、`chezmoi init`、`chezmoi apply`であり、失敗したexit codeをそのまま伝播して以降の段を実行しない。
ソースの状態が不確定なまま`chezmoi init`・`chezmoi apply`へ進むと不確定な内容を配備するためである。
前提を壊さない段は、失敗を記録して後続の段を続け、全段の実行後に失敗した段と次の操作の一覧を標準エラーへ表示して終了コード1で終える。
該当する段は、過去の退避データの整理（旧形式の参照は残り、今回の退避は新しい参照へ保存される）、
`mise.lock`の差分の破棄（破棄できなかった差分は未コミット内容として退避・復元される）、
`plugins/`配下の各リポジトリのpull、agent-toolkitの共有依存環境の準備（後段のpost-applyのウォームアップが再試行する）、
`chezmoi status`（表示専用）、`chezmoi diff`（表示と記録専用）、
Codex管理daemonの一時停止と再起動、永続ログの開始（開始できない場合は標準出力・標準エラーだけで続ける）である。
段を追加するときは、失敗した状態で後続の段へ進むと不確定な内容を配備するかで分類する。
Gitが進捗を標準エラー出力へ書く場合も、Git更新段が正常終了した場合は
`update-dotfiles`の標準出力へ転送する。失敗時はGitの標準エラー出力を維持する。
各段のサブプロセスへ`MISE_AUTO_INSTALL=0`を渡し、miseのshimが呼び出したコマンドと
無関係なツールを自動導入して更新処理を停止させる経路を抑止する。親プロセスの
仮想環境は子へ引き継がず、各工程が自身の設定から環境を解決する。
取得したchezmoi出力は、プラットフォームのロケール設定が決める文字コードによらずUTF-8として厳格にデコードする。
git pull工程は`UPDATE_DOTFILES_GIT_TIMEOUT_SEC`秒で打ち切る。未設定時は600秒、
`0`は上限なしとし、負数または整数でない値は終了コード2で拒否する。

agent-toolkitの共有依存環境は、dotfiles自身のGit更新の直後に主checkoutの`agent-toolkit/scripts/launch.py --prepare`で準備する。
依存の固定内容（`agent-toolkit/uv.lock`）が変わる更新では、準備までの間に起動した主checkoutの`atk`が依存環境の未準備で
終了するため、コードを切り替える段の直後に準備して、その間隔を`chezmoi apply`の所要より短くする。

実行の開始時と終了時に、同期結果を`pytools/_internal/sync_report.py`が定める構造化ファイルへ記録する。
次に起動するコーディングエージェントが、失敗した段と標準エラーの末尾からAWIの処理を
完遂できるかを判定するための記録であり、失敗の内容を人間の目視に頼らず残す。
`chezmoi init`と`chezmoi apply`の段は、子が標準出力と標準エラーを継承する。
この2段の同期結果には終了コードを残し、post-applyの詳細はpost-apply自身の記録と永続ログで確認する。

Linuxでは適用差分があり、Codexの管理daemonだけが稼働し、利用セッションが無い場合に限り、`chezmoi apply`の直前に
公開CLI`codex app-server daemon stop`で管理daemonを一時停止する。
post-applyの診断ログ復元と不要pluginの除去が、利用セッションの無い管理daemonを理由に延期されないためである。
停止を試みた場合は`chezmoi apply`の成否にかかわらず`codex app-server daemon start`で起動状態を戻し、
停止または再起動の失敗を終了コードと同期結果へ反映する。利用セッションが残る場合は停止せず、
復帰直前にセッションが開始していた場合もdaemonの起動を見送る。
"""

import argparse
import dataclasses
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import filelock

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(SOURCE_ROOT / "agent-toolkit"))
sys.path.insert(0, str(SOURCE_ROOT / "libexec"))

import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._common import dotfiles_mode  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_process import run_git_pull  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_runtime import child_env  # noqa: E402  # pylint: disable=wrong-import-position

from pytools._internal import codex_processes, sync_report  # noqa: E402  # pylint: disable=wrong-import-position


def _run_step(
    step_no: int,
    total: int,
    title: str,
    argv: list[str],
    *,
    capture: bool = False,
    show_heading: bool = True,
    log_step_no: int | None = None,
) -> tuple[int, str]:
    """1段を実行し、画面と診断ログへそれぞれの段番号を記録する。

    `capture=False`の段は、子へ両ストリームを継承し、書き込み順と出力先を保つ。
    `capture=True`時は標準出力を文字列で返し、標準エラーは段の終了後に親の標準エラーへ転送する。
    """
    update_dotfiles_runtime.current_stage_title = title
    update_dotfiles_runtime.last_stderr_tail = None
    log_step_no = step_no if log_step_no is None else log_step_no
    if show_heading:
        # 子の出力より前に並ぶよう、見出しを子プロセスの起動前に書き込む。
        # 端末以外（サービスのjournal、ファイル）への出力はブロックバッファで、flushしないと見出しが後段の出力より後に並ぶ。
        print(f"=== [{step_no}/{total}] {title} ===", flush=True)
    update_dotfiles_runtime.logger.info("stage開始: %d/%d %s", log_step_no, update_dotfiles_runtime.LOG_STAGE_TOTAL, title)
    started_at = time.monotonic()
    try:
        if capture:
            result = subprocess.run(
                argv,
                cwd=update_dotfiles_runtime.DOTFILES_ROOT,
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=update_dotfiles_runtime.child_env(),
            )
            returncode, stdout, output_tail = result.returncode, result.stdout, result.stderr
            if result.stderr:
                sys.stderr.write(result.stderr)
        else:
            result = subprocess.run(
                argv, cwd=update_dotfiles_runtime.DOTFILES_ROOT, check=False, env=update_dotfiles_runtime.child_env()
            )
            returncode, output_tail = result.returncode, ""
            stdout = ""
    except OSError as error:
        update_dotfiles_runtime.logger.exception(
            "stage起動失敗: %d/%d %s", log_step_no, update_dotfiles_runtime.LOG_STAGE_TOTAL, title
        )
        print(
            f"{title}を開始できませんでした: {error}。"
            "chezmoiとGitがPATH上で起動できることを確認してupdate-dotfilesを再実行してください。",
            file=sys.stderr,
        )
        update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(str(error))
        return 1, ""
    update_dotfiles_runtime.logger.info(
        "stage終了: %d/%d %s exit=%d duration=%.3f",
        log_step_no,
        update_dotfiles_runtime.LOG_STAGE_TOTAL,
        title,
        returncode,
        time.monotonic() - started_at,
    )
    if output_tail:
        update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(output_tail)
    return returncode, stdout


@dataclasses.dataclass(frozen=True)
class _CodexDaemonPause:
    """`chezmoi apply`の前に停止を試みた管理daemonと、停止の失敗内容。"""

    codex: str
    stop_error: str | None


def _pause_codex_daemon() -> _CodexDaemonPause | None:
    """利用セッションが無く管理daemonだけが稼働する場合に停止し、再起動に必要な情報を返す。

    停止しない場合は`None`を返す。管理daemonが稼働していない場合（更新ループだけが残る場合を含む）は
    停止も再起動も要らない。実際の延期の案内は処置の要否を判定するpost-applyへ委ねる。
    """
    if sys.platform != "linux":
        return None
    processes = codex_processes.codex_processes()
    if not any(process.role == "managed-daemon" for process in processes):
        return None
    sessions = tuple(process.label for process in processes if process.role == "session")
    codex = shutil.which("codex", path=update_dotfiles_runtime.child_env()["PATH"])
    if sessions or codex is None:
        return None
    update_dotfiles_runtime.logger.info("Codex管理daemonを一時停止: %s", codex)
    print("Codex管理daemonを一時停止します（更新後に再起動します）。", flush=True)
    stop_error = _run_codex_daemon_command(codex, "stop", update_dotfiles_runtime.CODEX_DAEMON_STOP_TIMEOUT_SEC)
    if stop_error is not None:
        stop_error = (
            f"Codex管理daemonを停止できませんでした（{stop_error}）。"
            "診断ログ復元または不要pluginの除去が延期された場合は、update-dotfilesを再実行してください。"
        )
        update_dotfiles_runtime.logger.error(stop_error)
        print(stop_error, file=sys.stderr)
    return _CodexDaemonPause(codex=codex, stop_error=stop_error)


def _resume_codex_daemon(pause: _CodexDaemonPause) -> str | None:
    """停止を試みた管理daemonを起動し、失敗時は手動の復帰操作を含む文面を返す。"""
    if any(process.role == "session" for process in codex_processes.codex_processes()):
        update_dotfiles_runtime.logger.info("Codexセッションが開始したため管理daemonの起動を見送り")
        return None
    error = _run_codex_daemon_command(pause.codex, "start", update_dotfiles_runtime.CODEX_DAEMON_COMMAND_TIMEOUT_SEC)
    if error is None:
        update_dotfiles_runtime.logger.info("Codex管理daemonを再起動")
        print("Codex管理daemonを再起動しました。", flush=True)
        return None
    message = (
        f"Codex管理daemonを再起動できませんでした（{error}）。次のコマンドで起動してください: "
        f"{update_dotfiles_runtime.CODEX_DAEMON_START_COMMAND}"
    )
    update_dotfiles_runtime.logger.error(message)
    print(message, file=sys.stderr)
    return message


def _run_codex_daemon_command(codex: str, action: str, timeout: int) -> str | None:
    """`codex app-server daemon <action>`を実行し、失敗時だけ理由を返す。"""
    try:
        result = subprocess.run(
            [codex, "app-server", "daemon", action],
            cwd=update_dotfiles_runtime.DOTFILES_ROOT,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=update_dotfiles_runtime.child_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{timeout}秒以内に完了しなかった"
    except OSError as error:
        return f"起動できない: {error}"
    update_dotfiles_runtime.logger.info(
        "codex app-server daemon %s: exit=%d stdout=%s", action, result.returncode, result.stdout.strip()
    )
    if result.stderr:
        update_dotfiles_runtime.logger.info("codex app-server daemon %sの標準エラー:\n%s", action, result.stderr)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        return f"exit {result.returncode}" + (f": {detail}" if detail else "")
    return None


def _prepare_agent_toolkit_environment(total: int) -> None:
    """主checkoutのatkが使う共有依存環境を、Git更新の直後に準備する。失敗は記録して後続の段を続ける。"""
    title = "agent-toolkitの共有依存環境の準備"
    launcher = update_dotfiles_runtime.DOTFILES_ROOT / "agent-toolkit" / "scripts" / "launch.py"
    argv = ["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python", str(launcher)]
    returncode, _ = _run_step(1, total, title, [*argv, "--prepare", "agent_toolkit/atk.py"], capture=True, show_heading=False)
    if returncode != 0:
        update_dotfiles_runtime.continue_after_failure(
            title,
            "後段のpost-applyのウォームアップが同じ準備を再試行する。続けて失敗する場合は"
            f'`uv run --no-config --no-project --python 3.15 python "{launcher}" --prepare agent_toolkit/atk.py`'
            "を実行して診断を確かめてください。",
            update_dotfiles_runtime.last_stderr_tail,
        )


def _filter_apply_pending(status_output: str) -> list[str]:
    """`chezmoi status`出力から2列目（apply予定を表す列）が空白以外の行のみ抽出する。

    `chezmoi status`は2列構成。1列目=前回chezmoiが書いた状態vs現在のdestination実ファイル、
    2列目=現在の実ファイルvsターゲット状態（＝これからapplyで起きる変更）。
    2文字未満の行はスライスが空文字列を返すため常に対象外とする。
    """
    return [line for line in status_output.splitlines() if len(line) > 1 and line[1] != " "]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(description="dotfilesを取得し、chezmoiで反映する")
    parser.add_argument("command", nargs="?", choices=["logs"], help="logs: 直近1回の更新実行の保存ログを表示する")
    parser.add_argument("--mode", choices=dotfiles_mode.MODES, help="導入モードを保存して適用する")
    parser.add_argument("--prune", action="store_true", help="現在のモードで不要なツールとキャッシュを削除する")
    return parser.parse_args([] if argv is None else argv)


def _show_logs() -> int:
    """保存済みの直近の更新実行を、複数行レコードと世代境界を保って表示する。"""
    header = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} run=(\S+) \S+ ")
    records: list[tuple[str, str]] = []
    run_id = ""
    for generation in range(update_dotfiles_runtime.LOG_BACKUP_COUNT, -1, -1):
        path = (
            update_dotfiles_runtime.LOG_PATH.with_name(f"{update_dotfiles_runtime.LOG_PATH.name}.{generation}")
            if generation
            else update_dotfiles_runtime.LOG_PATH
        )
        try:
            with path.open(encoding="utf-8") as log_file:
                for line in log_file:
                    match = header.match(line)
                    if match is not None:
                        run_id = match[1]
                    records.append((run_id, line))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as error:
            print(
                f"保存ログを読み取れませんでした: {path}: {error}。"
                "このファイルの読取権限を付与し、文字コードの問題があれば元ファイルを退避してUTF-8で保存し直してから"
                "update-dotfiles logsを再実行してください。",
                file=sys.stderr,
            )
            return 1
    # 単独post-applyを除き、開始記録が既に削除された更新も残っている実行IDから選ぶ。
    update_ids = {identifier for identifier, _ in records if re.fullmatch(r"\d+-\d+", identifier)}
    if not update_ids:
        print("保存済みの更新ログはありません。")
        return 0
    latest_id = max(update_ids, key=lambda identifier: tuple(map(int, identifier.split("-"))))
    for identifier, line in records:
        if identifier == latest_id:
            sys.stdout.write(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    """更新処理を排他ロック下で直列実行し、最終exit codeを返す。"""
    args = _parse_args(argv)
    if args.command == "logs":
        return _show_logs()
    update_dotfiles_runtime.current_run_id = f"{time.time_ns()}-{os.getpid()}"
    update_dotfiles_runtime.current_stage_title = None
    update_dotfiles_runtime.last_stderr_tail = None
    update_dotfiles_runtime.continued_failures.clear()
    with update_dotfiles_runtime.configure_persistent_log(update_dotfiles_runtime.current_run_id) as log_handler:
        try:
            sync_report.write_start(update_dotfiles_runtime.current_run_id, sync_report.now_text())
            update_dotfiles_runtime.logger.info("update-dotfiles開始: root=%s", update_dotfiles_runtime.DOTFILES_ROOT)
            if log_handler is None:
                update_dotfiles_runtime.continue_after_failure(
                    "永続ログの開始",
                    f"{update_dotfiles_runtime.LOG_PATH.parent}の書込権限と空き容量を確認してupdate-dotfilesを再実行してください。",
                    "永続ログを開始できない",
                )
            try:
                git_timeout = update_dotfiles_process.git_timeout()
            except ValueError as error:
                update_dotfiles_runtime.logger.error("git timeout設定が不正: %s", error)
                print(error, file=sys.stderr)
                update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(str(error))
                return update_dotfiles_runtime.finish(2)
            total = 4
            lock_dir = update_dotfiles_runtime.LOCK_PATH.parent
            lock_dir.mkdir(parents=True, exist_ok=True)
            try:
                with filelock.FileLock(
                    str(update_dotfiles_runtime.LOCK_PATH), timeout=update_dotfiles_runtime.LOCK_TIMEOUT_SEC
                ):
                    dotfiles_mode.select(args.mode)
                    if args.prune:
                        os.environ["DOTFILES_PRUNE"] = "1"
                    returncode = update_dotfiles_git.update_git_with_recovery(1, total, timeout=git_timeout)
                    if returncode != 0:
                        return update_dotfiles_runtime.finish(returncode)

                    # 主checkoutの版が進んだ直後に準備し、plugin更新の待ちの間も未準備の区間を残さない。
                    _prepare_agent_toolkit_environment(total)

                    plugins_root = update_dotfiles_runtime.DOTFILES_ROOT / "plugins"
                    plugins = sorted(plugins_root.iterdir(), key=lambda path: path.name) if plugins_root.exists() else []
                    for plugin in plugins:
                        if plugin.is_dir() and (plugin / ".git").exists():
                            returncode = update_dotfiles_process.run_git_pull(1, total, timeout=git_timeout, plugin=plugin)
                            if returncode != 0:
                                update_dotfiles_runtime.continue_after_failure(
                                    update_dotfiles_runtime.current_stage_title
                                    or f"dotfilesプラグイン {plugin.name}: git pull",
                                    f"{plugin}で git status と git pull --rebase を実行して原因を確かめてください。",
                                    update_dotfiles_runtime.last_stderr_tail,
                                )

                    returncode, _ = _run_step(
                        2,
                        total,
                        "chezmoi init (テンプレート再展開)",
                        ["chezmoi", "init", f"--source={update_dotfiles_runtime.DOTFILES_ROOT}"],
                    )
                    if returncode != 0:
                        return update_dotfiles_runtime.finish(returncode)

                    returncode, status_output = _run_step(
                        3,
                        total,
                        "chezmoi status (apply予定のファイル)",
                        ["chezmoi", "status", "-x", "scripts"],
                        capture=True,
                    )
                    if returncode != 0:
                        update_dotfiles_runtime.continue_after_failure(
                            "chezmoi status (apply予定のファイル)",
                            "表示専用の段であり、chezmoi applyは続けます。"
                            "chezmoi statusを単独で実行して原因を確かめてください。",
                            update_dotfiles_runtime.last_stderr_tail,
                        )
                    for line in _filter_apply_pending(status_output):
                        print(line)

                    returncode, diff_output = _run_step(
                        4,
                        total,
                        "chezmoi diff (上書き前の差分)",
                        ["chezmoi", "diff", "--no-pager"],
                        capture=True,
                        show_heading=False,
                    )
                    update_dotfiles_runtime.logger.info("chezmoi diffの出力:\n%s", diff_output)
                    if returncode != 0:
                        update_dotfiles_runtime.continue_after_failure(
                            "chezmoi diff (上書き前の差分)",
                            "差分の記録だけの段であり、chezmoi applyは続けます。"
                            "chezmoi diffを単独で実行して原因を確かめてください。",
                            update_dotfiles_runtime.last_stderr_tail,
                        )

                    daemon_pause = (
                        _pause_codex_daemon()
                        if diff_output.strip() and not (dotfiles_mode.is_minimal() and args.prune)
                        else None
                    )
                    try:
                        returncode, _ = _run_step(
                            total,
                            total,
                            "chezmoi apply (post-apply実行)",
                            ["chezmoi", "apply", "--force"],
                            log_step_no=update_dotfiles_runtime.LOG_STAGE_TOTAL,
                        )
                    finally:
                        resume_error = None if daemon_pause is None else _resume_codex_daemon(daemon_pause)
                    if returncode != 0:
                        return update_dotfiles_runtime.finish(returncode)
                    stop_error = None if daemon_pause is None else daemon_pause.stop_error
                    daemon_errors = [error for error in (stop_error, resume_error) if error is not None]
                    if daemon_errors:
                        update_dotfiles_runtime.continue_after_failure(
                            update_dotfiles_runtime.CODEX_DAEMON_STAGE_TITLE,
                            "Codex管理daemonの状態を確認し、停止していれば codex app-server daemon start を実行してください。",
                            "\n".join(daemon_errors),
                        )
            except update_dotfiles_process.GitReclaimError as error:
                update_dotfiles_runtime.logger.exception("Git子プロセスの回収失敗")
                print(str(error), file=sys.stderr)
                update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(str(error))
                return update_dotfiles_runtime.finish(1)
            except filelock.Timeout:
                update_dotfiles_runtime.logger.exception("update-dotfilesロック取得失敗")
                lock_message = (
                    f"ロック取得に失敗しました（{update_dotfiles_runtime.LOCK_TIMEOUT_SEC:.0f}秒待機後もタイムアウト）。"
                    "他のupdate-dotfiles実行の完了を待って再実行してください。"
                )
                print(lock_message, file=sys.stderr)
                update_dotfiles_runtime.last_stderr_tail = sync_report.truncate_tail(lock_message)
                return update_dotfiles_runtime.finish(1)
            return update_dotfiles_runtime.finish(0)
        finally:
            update_dotfiles_runtime.current_run_id = None
            update_dotfiles_runtime.persistent_log_ready = False
            update_dotfiles_runtime.current_stage_title = None
            update_dotfiles_runtime.last_stderr_tail = None
            update_dotfiles_runtime.continued_failures.clear()


if __name__ == "__main__":
    for output_stream in (sys.stdout, sys.stderr):
        if isinstance(output_stream, io.TextIOWrapper):
            output_stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["child_env", "run_git_pull"]
