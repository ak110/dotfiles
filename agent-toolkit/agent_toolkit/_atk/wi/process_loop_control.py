"""`atk wi process-loop`への中断要求と追加指示の状態ファイル。"""

import contextlib
import dataclasses
import json
import pathlib
import re
import sys
import time
import uuid
from collections.abc import Generator
from typing import Literal, cast

from filelock import FileLock

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log


@dataclasses.dataclass(frozen=True)
class ResumeRequest:
    """次の実起動だけに適用する予約。"""

    request_id: str
    mode: Literal["id", "auto", "picker"]
    session_id: str | None = None


class ResumeChanged(RuntimeError):
    """取得後に取消・置換・消費された予約の起動を拒否する。"""


def _resume_path() -> pathlib.Path:
    return _process_loop_abort_path().with_name("process-wi-resume.json")


@contextlib.contextmanager
def _resume_lock() -> Generator[None]:
    path = _resume_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    # 起動だけを保護し、停止した所有者を無期限に待たない。
    with FileLock(path, timeout=10):
        yield


def _valid_session_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", value) is not None


def read_resume_request() -> ResumeRequest | None:
    """原子的に保存された予約を読み、不正な状態は起動せずエラーにする。"""
    try:
        raw = json.loads(_resume_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"再開予約のJSONを読めない: {error}") from error
    if not isinstance(raw, dict) or set(raw) != {"request_id", "mode", "session_id"}:
        raise ValueError("再開予約の形式が不正である")
    token, mode, session_id = raw["request_id"], raw["mode"], raw["session_id"]
    try:
        if not isinstance(token, str) or str(uuid.UUID(token)) != token:
            raise ValueError("UUIDの正規形ではない")
    except ValueError as error:
        raise ValueError("再開予約のトークンが不正である") from error
    if mode not in ("id", "auto", "picker") or (not _valid_session_id(session_id) if mode == "id" else session_id is not None):
        raise ValueError("再開予約の形式またはIDが不正である")
    return ResumeRequest(token, cast(Literal["id", "auto", "picker"], mode), session_id)


@contextlib.contextmanager
def resume_launch(request_id: str | None) -> Generator[None]:
    """実Popenだけを排他し、中断と新しい予約を優先して成功した起動だけ消費する。"""
    with _resume_lock():
        if _process_loop_abort_path().exists():
            raise ResumeChanged("中断要求があるため起動を見送った。再開予約と追加指示は保持する")
        request = read_resume_request()
        if (request.request_id if request is not None else None) != request_id:
            raise ResumeChanged("再開予約は取得後に変更された")
        yield
        if request_id is not None:
            _resume_path().unlink()


def cmd_process_loop_resume(value: str | None) -> None:
    """次回の再開を予約し、後から受理した指定で置き換える。"""
    mode: Literal["id", "auto", "picker"] = "picker" if value is None else "auto" if value == "auto" else "id"
    if mode == "id" and not _valid_session_id(value):
        raise ValueError("再開IDは英数字で始まる200文字以内の英数字・ハイフン・下線とする")
    request = ResumeRequest(str(uuid.uuid4()), mode, value if mode == "id" else None)
    with _resume_lock():
        path = _resume_path()
        temporary = path.with_name(f"{path.name}.{request.request_id}.tmp")
        try:
            temporary.write_text(json.dumps(dataclasses.asdict(request)), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    _outcome.report_success("次回の再開を予約した")


def cmd_process_loop_resume_cancel() -> None:
    """まだ実起動していない予約を取り消す。"""
    with _resume_lock():
        _resume_path().unlink(missing_ok=True)
    _outcome.report_success("未消費の再開予約を取り消した")


# 端末が連続するBELを1回へまとめないよう、鳴動の間に置く待機秒。
_ABORT_BELL_INTERVAL_SEC = 0.1


def _process_loop_abort_path() -> pathlib.Path:
    """process-loopの中断要求を保持する状態ファイルのパスを返す。

    パスの解決は`process_loop_log.abort_path`が担う。
    """
    return _process_loop_log.abort_path()


def cmd_process_loop_abort() -> None:
    """process-loopへ現在のセッション終了後の中断を要求する。"""
    with _resume_lock():
        path = _process_loop_abort_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    _outcome.report_success("process-loopへ中断を要求した")


def cmd_process_loop_abort_cancel() -> None:
    """process-loopへの中断要求を解除する。"""
    with _resume_lock():
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
    request = read_resume_request()
    print("再開予約: 未予約" if request is None else f"再開予約: {request.mode} {request.session_id or ''}".rstrip())
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
        _outcome.report_success(f"{summary}ため、追加指示の変更は無い")
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
    with _resume_lock():
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
