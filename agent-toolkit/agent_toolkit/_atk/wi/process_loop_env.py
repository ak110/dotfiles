"""`atk wi process-loop`が起動する子プロセスの環境、実行ファイルの解決とコンソールの復元。"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import typing

from agent_toolkit._atk import orchestrator as _orchestrator
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import wait_schedule as _wait_schedule

# ランチャーが作成する再起動要求の受け渡しファイルのパスを保持する環境変数。
# 実体プロセスが自身を`uv`へ置き換えると、置き換え前の`uv`が子の終了を待って残り、
# 再起動のたびにプロセス階層が1段深くなる。実体は次の起動対象を受け渡しファイルへ出力して終了し、
# ランチャーが同一プロセスで次の実体を起動することで階層を一定に保つ。
RESTART_SPEC_ENV = _orchestrator.RESTART_SPEC_ENV


# process-loopが起動した会話を環境印と会話IDで識別する。
PROCESS_LOOP_SESSION_ENV = _orchestrator.PROCESS_LOOP_SESSION_ENV


PROCESS_LOOP_SESSION_ID_ENV = _orchestrator.PROCESS_LOOP_SESSION_ID_ENV


# 次に起動する1セッションだけへ渡すユーザーの追加指示。SessionStart hookが本文を注入する。
PROCESS_LOOP_INSTRUCTION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION"


# Windows APIのCREATE_NEW_PROCESS_GROUP。POSIXでも純粋関数が契約どおりに動作するかを確かめられるよう値を固定する。
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def dialog_timeout_settings() -> str:
    """メイン会話の質問タイムアウトとRemote Control無効化の設定を返す。

    Remote Controlのbridgeが接続されると質問の自動送出が無効になるため、
    process-loopが起動するセッションでは起動時の接続を無効にする。
    """
    if _wait_schedule.get_prompt_cache_ttl("main") == "5m":
        return '{"askUserQuestionTimeout": "60s", "dialogExpiry": "60s", "remoteControlAtStartup": false}'
    return '{"askUserQuestionTimeout": "5m", "dialogExpiry": "5m", "remoteControlAtStartup": false}'


def child_env() -> dict[str, str]:
    """起動元ツールの仮想環境を除いた子プロセス用の環境変数を返す。

    対象は`atk`から起動する外部コマンド（claudeセッション・`update-dotfiles`）とする。
    `update-dotfiles`は`chezmoi apply`を経て対象リポジトリのuvベースのパッケージ操作へ至るため、
    claudeセッションと同じく起動元ツールの環境を引き継がせない。
    自己再起動を行う`_pl_update.restart_process_loop`は本関数の対象外とする。
    再起動先は`atk`自身であり、起動元と同じ実行環境で継続する必要があるためである。
    ランチャーとの再起動要求の受け渡しファイルは自プロセス専用のため、子孫プロセスへは引き継がない。
    引き継ぐと、子孫が同じファイルへ再起動対象を書き込みうる。
    """
    return _orchestrator.child_env(drop=(RESTART_SPEC_ENV,))


session_env = _orchestrator.session_env


def session_creation_flags(orchestrator: str, *, platform: str = os.name) -> int:
    """Windows Codexを親process-loopと別のコンソール制御グループで起動する。"""
    return _CREATE_NEW_PROCESS_GROUP if platform == "nt" and orchestrator == "codex" else 0


def reset_console(*, platform: str = os.name, stream: typing.TextIO | None = None) -> None:
    """POSIXのターミナルを初期化し、子セッションが残した表示状態を復旧する。"""
    if platform != "posix":
        return
    output = sys.stdout if stream is None else stream
    try:
        if not output.isatty():
            return
    except (AttributeError, ValueError):
        return
    executable = shutil.which("reset")
    if executable is not None:
        subprocess.run([executable], check=False)


def create_hook_debug_log(env: dict[str, str]) -> pathlib.Path:
    """Claude Codeのhook診断ログを所有者限定で事前作成する。"""
    config_dir = _host_homes.claude_config_dir(env)
    debug_dir = config_dir / "debug"
    debug_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="process-loop-", suffix=".log", dir=debug_dir)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    return pathlib.Path(name).resolve()


def resolve_executable(command: str) -> str | None:
    """実行可能ファイルを環境の探索規則で解決し、利用不能時は警告する。"""
    executable = shutil.which(command)
    if executable is None:
        _next_action.report(
            f"{command}コマンドを利用できないため処理を継続します。",
            next_action=f"対応不要（処理は継続した）。{command}を使う場合はPATHへ導入してからprocess-loopを再起動する",
        )
    return executable


def restore_process_loop_env(previous_values: dict[str, str | None]) -> None:
    """process-loop識別環境変数を呼び出し前の状態へ戻す。"""
    for key, previous_value in previous_values.items():
        if previous_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous_value
