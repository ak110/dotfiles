"""agents_serverが保存した終端結果又は通知を待つ。"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import sys
import time
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import logging_config, session_registry, state, status_file
from agent_toolkit._common.file_lock import acquire_lock, release_lock

_LOG = logging.getLogger("agent-toolkit.agents-server.wait")


def _fail(message: str, code: int, *, session_id: str | None = None) -> int:
    """標準エラーへ理由を出力してから非0の終了コードで異常終了する。

    理由を伴わない異常終了をこの経路では表現できないよう、`message`を必須の引数とする。
    """
    _LOG.info("wait_return reason=abnormal code=%d session_id=%s", code, session_id or "none")
    print(message, file=sys.stderr)
    return code


def _target_origins(
    own_status_path: pathlib.Path,
    result_directory: pathlib.Path,
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None,
) -> tuple[dict[str, set[str]], tuple[str, int] | None]:
    """現行の待機対象と由来を返し、解釈不能な入力は診断へ変換する。"""
    listed = _read_sessions(own_status_path)
    invalid = [session["session_id"] for session in listed or () if not status_file.valid_session_id(session["session_id"])]
    if invalid:
        return {}, (f"session_idの形式が不正です: {invalid[0]}", 5)
    listed_ids = {session["session_id"] for session in listed or ()}
    result_ids = _retained_result_session_ids(result_directory, owner_status_file=owner_status_file)
    registered_ids, registry_error = status_file.read_wait_targets(root_session_id, owner_status_file, state_root)
    if registry_error is not None:
        return {}, (f"待機対象登録簿を読めません: {registry_error}", 9)
    for session_id in set(registered_ids) - listed_ids - result_ids:
        if session_registry.resolve(session_id, state_root=state_root).state is session_registry.Resolution.MISSING:
            status_file.release_wait_target(root_session_id, owner_status_file, session_id, state_root)
            registered_ids.remove(session_id)
    origins: dict[str, set[str]] = {}
    for origin, identifiers in (
        ("status", listed_ids),
        ("result", result_ids),
        ("registered", registered_ids),
    ):
        for session_id in identifiers:
            origins.setdefault(session_id, set()).add(origin)
    return origins, None


def wait_for_result(
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """自身が保持するsessionから、1回の巡回で回収できた終端結果と通知を全件返す。

    回収できたものは1件1行のJSON Linesで標準出力へ書く。1件ずつ返す形では、未回収の終端結果が
    残っている間は呼び出し元が当該結果を消化する回数だけ起動を繰り返さないと、稼働中のsessionへ到達できない。
    回収の途中で終端結果の読取に失敗した場合は、同じ巡回で回収済みの本文を先に配送してから終わる。
    回収は結果ファイルと通知ファイルの削除を伴うため、当該失敗を理由に配送を取りやめると回収済みの本文が失われる。
    読取の失敗は次の起動でも同じ状態で現れるため、当該起動の診断を1回遅らせても失われない。

    対象は、自身の書込主体の状態ファイルへ載るsessionと、終端結果ファイルが残るsessionの
    双方とする。後者を含めるのは、保持期限で一覧から外れたsessionの結果本文も回収するためである。
    対象集合は最初の待機の発行時点で登録簿へ保存し、再発行時も同じ集合を引き継ぐ。
    待機中に開始又は再稼働したsessionも、巡回ごとに取得して対象へ追加する。
    状態ファイルは投影であり、対象の不在から権威あるsessionの喪失を判定できない。
    終端結果と通知が無い場合は、投影が消失しても待機上限まで非終端として扱う。
    対象を1件も取得できない状態が続く場合だけは、起動そのものが失敗した可能性を呼び出し元へ返すため、
    通常の待機上限とは別の短い上限で理由を標準エラーへ書いて非0で終わる。
    """
    logging_config.configure_logging()
    env = os.environ if environment is None else environment
    root_session_id = status_file.resolve_conversation_root_session_id(env, state_root)
    identity = status_file.resolve_status_file_identity(env)
    if root_session_id is None or identity is None:
        message = (
            "agents_serverの状態ディレクトリを解決できません。"
            "同じsessionで`atk agents list`と`atk agents wait`を実行してください。"
        )
        return _fail(message, 4)
    own_status_path = status_file.status_directory(root_session_id, state_root) / identity.file_name
    result_directory = status_file.results_directory(root_session_id, state_root)
    origins, target_error = _target_origins(
        own_status_path,
        result_directory,
        root_session_id,
        identity.file_name,
        state_root,
    )
    if target_error is not None:
        return _fail(*target_error)
    ordered_ids = sorted(origins)
    _LOG.info(
        "wait_start targets=%s origins=%s",
        ",".join(ordered_ids) or "none",
        ";".join(f"{session_id}:{','.join(sorted(origins[session_id]))}" for session_id in ordered_ids) or "none",
    )
    lock_directory = status_file.status_directory(root_session_id, state_root) / "wait-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / f"{identity.file_name}.lock"
    lock_file = lock_path.open("a+b")
    try:
        try:
            acquire_lock(lock_file, blocking=False)
        except OSError:
            _LOG.info("wait_lock_failed owner=%s targets=%s", identity.file_name, ",".join(ordered_ids) or "none")
            return _fail(
                "同じ書込主体の待機を別の実行が保持しています。先行する`atk agents wait`の終了を待ってから再実行してください: "
                f"owner={identity.file_name}, targets={','.join(ordered_ids) or 'none'}",
                8,
            )
        status_file.retain_wait_targets(root_session_id, identity.file_name, ordered_ids, state_root)

        started_at = time.monotonic()
        deadline = started_at + state.WAIT_TIMEOUT_SECONDS
        # 対象を1件でも取得した時点で不在の上限は失効し、以後は通常の待機上限だけが働く。
        empty_deadline: float | None = None if ordered_ids else started_at + state.EMPTY_WAIT_TIMEOUT_SECONDS
        while True:
            current_origins, target_error = _target_origins(
                own_status_path,
                result_directory,
                root_session_id,
                identity.file_name,
                state_root,
            )
            if target_error is not None:
                return _fail(*target_error)
            for session_id in sorted(set(current_origins) - set(ordered_ids)):
                ordered_ids.append(session_id)
                ordered_ids.sort()
                status_file.retain_wait_targets(root_session_id, identity.file_name, [session_id], state_root)
                _LOG.info(
                    "wait_target_added session_id=%s origins=%s",
                    session_id,
                    ",".join(sorted(current_origins[session_id])),
                )
            status_paths = status_file.list_status_files(root_session_id, state_root)
            collected: list[dict[str, Any]] = []
            read_failure: tuple[str, int] | None = None
            failed_session_id: str | None = None
            for session_id in ordered_ids:
                result_path = result_directory / f"{session_id}.json"
                result, read_error = status_file.take_result(
                    root_session_id,
                    session_id,
                    identity.file_name,
                    collector="atk-agents-wait",
                    state_root=state_root,
                )
                if read_error is not None:
                    read_failure = (f"終端結果ファイルを読めません: {result_path}: {read_error}", 6)
                    failed_session_id = session_id
                    break
                notices = status_file.take_notices(root_session_id, session_id, state_root)
                if result is not None:
                    result["session_id"] = session_id
                    if notices:
                        result["notices"] = notices
                    collected.append(result)
                    status_file.release_wait_target(root_session_id, identity.file_name, session_id, state_root)
                    continue
                if notices:
                    response = _running_response(session_id, _session_output_activity(status_paths, session_id))
                    response["notices"] = notices
                    collected.append(response)
            if collected:
                print("\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in collected))
                _LOG.info(
                    "wait_return reason=collected count=%d session_ids=%s",
                    len(collected),
                    ",".join(str(item["session_id"]) for item in collected),
                )
                return 0
            if read_failure is not None:
                return _fail(*read_failure, session_id=failed_session_id)

            now = time.monotonic()
            if ordered_ids:
                empty_deadline = None
            elif empty_deadline is not None and now >= empty_deadline:
                return _fail(
                    "待機対象が1件も登録されないまま上限へ達しました。"
                    "委譲先の起動に失敗した可能性があるため、`atk agents list`と起動側の応答を確認してください: "
                    f"owner={identity.file_name}, "
                    f"elapsed={now - started_at:.1f}s, "
                    f"limit={state.EMPTY_WAIT_TIMEOUT_SECONDS:.0f}s",
                    10,
                )

            retained = {session_id: _session_is_retained(status_paths, session_id) for session_id in ordered_ids}
            selected = next((session_id for session_id in ordered_ids if retained[session_id] is not False), None)
            if selected is None and ordered_ids:
                selected = ordered_ids[0]
            response = _running_response(
                selected,
                {} if selected is None else _session_output_activity(status_paths, selected),
            )
            remaining = deadline - now
            if remaining <= 0:
                print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                _LOG.info("wait_return reason=timeout session_id=%s", selected or "none")
                return 3
            time.sleep(min(1.0, remaining))
    finally:
        if not lock_file.closed:
            release_lock(lock_file)
            lock_file.close()


def _retained_result_session_ids(result_directory: pathlib.Path, *, owner_status_file: str) -> set[str]:
    """自身の書込主体が公開した終端結果のsession識別子を返す。"""
    try:
        paths = tuple(result_directory.iterdir())
    except OSError:
        return set()
    retained: set[str] = set()
    for path in paths:
        if path.suffix != ".json" or not path.is_file() or not status_file.valid_session_id(path.stem):
            continue
        result, error = _read_result(path)
        if error is not None:
            if owner_status_file == "root.json":
                retained.add(path.stem)
            continue
        if result is None:
            continue
        recorded_owner = result.get("owner_status_file")
        if recorded_owner == owner_status_file or recorded_owner is None and owner_status_file == "root.json":
            retained.add(path.stem)
    return retained


def _read_result(path: pathlib.Path) -> tuple[dict[str, Any] | None, str | None]:
    """終端結果を読み、不在と破損を区別して返す。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "最上位が辞書ではありません"
    return value, None


def _session_is_retained(paths: list[pathlib.Path], session_id: str) -> bool | None:
    """状態ファイル群からsessionの保持有無を読み、全て解釈不能なら`None`を返す。"""
    parsed = False
    for path in paths:
        sessions = _read_sessions(path)
        if sessions is None:
            continue
        parsed = True
        if any(session["session_id"] == session_id for session in sessions):
            return True
    return False if parsed else None


def _session_output_activity(paths: list[pathlib.Path], session_id: str) -> dict[str, Any]:
    """状態ファイルから保持中sessionの最新テキスト出力活動を返す。"""
    for path in paths:
        sessions = _read_sessions(path)
        if sessions is None:
            continue
        for session in sessions:
            if session["session_id"] != session_id:
                continue
            output_updated_at = session.get("output_updated_at")
            reference = output_updated_at if isinstance(output_updated_at, str) else session.get("started_at")
            if not isinstance(reference, str):
                return {}
            try:
                timestamp = datetime.datetime.fromisoformat(reference)
            except ValueError:
                return {}
            if timestamp.utcoffset() is None:
                return {}
            elapsed = max(0, int((datetime.datetime.now(datetime.UTC) - timestamp).total_seconds()))
            activity: dict[str, Any] = {
                "output_updated_at": output_updated_at if isinstance(output_updated_at, str) else None,
                "seconds_since_output": elapsed,
            }
            if elapsed >= state.STALL_NOTICE_SECONDS:
                activity["stalled"] = True
            return activity
    return {}


def _read_sessions(path: pathlib.Path) -> list[dict[str, Any]] | None:
    """状態ファイルを解釈し、session一覧又は`None`を返す。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    sessions = value.get("sessions") if isinstance(value, dict) and value.get("version") == 1 else None
    if not isinstance(sessions, list):
        return None
    if any(not isinstance(session, dict) or not isinstance(session.get("session_id"), str) for session in sessions):
        return None
    return sessions


def _running_response(session_id: str | None, output_activity: Mapping[str, Any]) -> dict[str, Any]:
    """非終端の待機応答へ最新テキスト出力活動の観測値を加える。

    対象を1件も解決できない場合は`session_id`を省き、`status`だけを返す。
    """
    response: dict[str, Any] = {"status": "running"}
    if session_id is not None:
        response = {"session_id": session_id, "status": "running"}
    response.update(output_activity)
    return response
