"""`atk wi process-loop`への中断要求と追加指示の状態ファイル。"""

import pathlib
import sys
import time

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log

# 端末が連続するBELを1回へまとめないよう、鳴動の間に置く待機秒。
_ABORT_BELL_INTERVAL_SEC = 0.1


def _process_loop_abort_path() -> pathlib.Path:
    """process-loopの中断要求を保持する状態ファイルのパスを返す。

    パスの解決は`process_loop_log.abort_path`が担う。Stop hookも同じ関数を使う。
    """
    return _process_loop_log.abort_path()


def cmd_process_loop_abort() -> None:
    """process-loopへ現在のセッション終了後の中断を要求する。"""
    _process_loop_log.request_abort()
    _outcome.report_success("process-loopへ中断を要求した")


def cmd_process_loop_abort_cancel() -> None:
    """process-loopへの中断要求を解除する。"""
    path = _process_loop_abort_path()
    if not path.exists():
        _outcome.report_success("process-loopへの中断要求は設定されていないため、解除の変更は無い")
        return
    path.unlink(missing_ok=True)
    _outcome.report_success("process-loopへの中断要求を解除した")


def cmd_process_loop_status() -> None:
    """process-loopへの中断要求と保持中の追加指示を表示する。"""
    status = "あり" if _process_loop_abort_path().exists() else "なし"
    print(f"process-loopへの中断要求: {status}")
    instructions = _process_loop_log.read_instructions()
    print(f"保持中の追加指示: {len(instructions)}件")
    for index, body in enumerate(instructions, start=1):
        print(f"[{index}] {body}")


def cmd_process_loop_instruct(body: str) -> None:
    """次に起動する1セッションへ渡す追加指示を保持する。"""
    appended, summary = _process_loop_log.append_instruction(body)
    if not appended:
        if summary.startswith("保持中の合計") or summary == "本文が空である":
            next_action = (
                "本文を記入して再実行する"
                if summary == "本文が空である"
                else "`atk wi process-loop instruct-cancel`で保持中の指示を破棄するか、本文を短くして再実行する"
            )
            _outcome.report_failure(f"追加指示を保持しなかった: {summary}", next_action=next_action)
            raise SystemExit(1)
        _outcome.report_success(f"追加指示は{summary}ため、変更は無い")
        return
    _outcome.report_success(f"次のセッションへ渡す追加指示を保持した。{summary}")


def cmd_process_loop_instruct_cancel() -> None:
    """保持中の追加指示を全件破棄する。"""
    count = _process_loop_log.discard_instructions()
    if count == 0:
        _outcome.report_success("保持中の追加指示が無いため、変更は無い")
        return
    _outcome.report_success(f"保持中の追加指示を{count}件破棄した")


def consume_process_loop_abort() -> bool:
    """中断要求があればベルを3回鳴らして要求を消費し、process-loopを終了すべきかを返す。

    判定点は反復の境界と、呼び出し元へ戻らない再起動の直前の2箇所へ限定する。
    `_pl_update.restart_process_loop`は`os.execv`または`sys.exit`で呼び出し元へ戻らないため、
    その呼び出しの後段へ置いた判定は`--no-update`を省略して起動した場合には実行されない。
    同じ理由で`_pl_update.update_before_session`と`_pl_update.check_and_restart_on_update`の後段にも判定を置かず、
    反復ループの先頭でまとめて判定する。
    `atk wi process-loop abort`の公開契約は、現在のセッションが終わった時点で次の反復へ進まず
    終了することと、中断で終了した時点で要求も解除されることを定める。
    """
    abort_path = _process_loop_abort_path()
    if not abort_path.exists():
        return False
    _process_loop_log.append("abort_consumed", path=str(abort_path))
    for bell_index in range(3):
        print("\a", end="", file=sys.stderr, flush=True)
        if bell_index < 2:
            time.sleep(_ABORT_BELL_INTERVAL_SEC)
    abort_path.unlink(missing_ok=True)
    return True
