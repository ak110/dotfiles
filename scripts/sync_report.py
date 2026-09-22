"""`update-dotfiles`の同期結果を構造化したJSONとして記録する。

`update-dotfiles`の失敗内容を、次に起動するコーディングエージェントが読んで
続行と中断を判定できる形で残す。書き手は`scripts/update_dotfiles.py`と
`pytools/post_apply.py`の2つであり、後者は`chezmoi apply`段の内側で動く。
両者は`scripts/update_dotfiles.py`が取得するプロセス間排他ロックの内側で
直列に動作するため、同時書き込みは起きない。

`scripts/update_dotfiles.py`はPEP 723スクリプトとして`--no-project`で起動され、
`sys.path[0]`が本ファイルのディレクトリになるため`import sync_report`で解決する。
`pytools/post_apply.py`はwheelへ同梱される`scripts`パッケージ経由で解決する。
両経路のいずれからも解決できる位置に置くため、本モジュールの依存は標準ライブラリと`platformdirs`に限る。
"""

import contextlib
import datetime
import json
import os
import pathlib
import tempfile
from typing import Any

import platformdirs

SCHEMA = 1
REPORT_PATH = pathlib.Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "sync-report.json"
STDERR_TAIL_MAX_LINES = 20
STDERR_TAIL_MAX_CHARS = 2000


def now_text() -> str:
    """記録用の現在時刻をISO 8601形式で返す。"""
    return datetime.datetime.now(datetime.UTC).isoformat()


def truncate_tail(text: str) -> str | None:
    """標準エラーの末尾を上限付きで返す。空文字列は`None`として扱う。"""
    stripped = text.strip()
    if not stripped:
        return None
    tail = "\n".join(stripped.splitlines()[-STDERR_TAIL_MAX_LINES:])
    if len(tail) > STDERR_TAIL_MAX_CHARS:
        tail = tail[-STDERR_TAIL_MAX_CHARS:]
    return tail


def read() -> dict[str, Any]:
    """保存済みの記録を返す。不在と破損はいずれも空のdictとして扱う。"""
    try:
        loaded = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write(payload: dict[str, Any]) -> None:
    """記録を置き換える。書き込めない環境でも呼び出し元の処理は継続させる。

    読み手が途中状態のJSONを受け取らないよう、同じディレクトリへ書いてから置き換える。
    """
    temporary_path: pathlib.Path | None = None
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=REPORT_PATH.parent,
            prefix=f"{REPORT_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary_path = pathlib.Path(handle.name)
        os.replace(temporary_path, REPORT_PATH)
    except OSError:
        if temporary_path is not None:
            with contextlib.suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def write_start(run_id: str, started_at: str) -> None:
    """実行の開始を記録し、前回の実行の内容を残さない。"""
    _write(
        {
            "schema": SCHEMA,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": None,
            "status": "running",
            "exit_code": None,
            "failed_stage": None,
            "stderr_tail": None,
            "post_apply": None,
        }
    )


def write_finish(
    run_id: str,
    *,
    status: str,
    exit_code: int,
    failed_stage: str | None,
    stderr_tail: str | None,
    finished_at: str,
) -> None:
    """実行の終了を記録する。同じ実行が記録したpost-apply段の結果は保持する。"""
    current = read()
    payload = current if current.get("run_id") == run_id else {"schema": SCHEMA, "run_id": run_id, "post_apply": None}
    payload.update(
        {
            "status": status,
            "exit_code": exit_code,
            "failed_stage": failed_stage,
            "stderr_tail": stderr_tail,
            "finished_at": finished_at,
        }
    )
    payload.setdefault("started_at", finished_at)
    _write(payload)


def write_post_apply(run_id: str, post_apply: dict[str, Any]) -> None:
    """post-apply段の結果を記録する。

    `chezmoi apply`を単独で実行した場合は`update-dotfiles`の記録が無いため、
    post-apply段の結果だけを持つ記録を新規に書く。書式版と実行識別子は、
    消費側が記録の形式と出所を判定するために併せて書く。
    """
    current = read()
    payload = current if current.get("run_id") == run_id else {"schema": SCHEMA, "run_id": run_id}
    payload["post_apply"] = post_apply
    _write(payload)
