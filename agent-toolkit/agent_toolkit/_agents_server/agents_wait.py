"""agents_serverが保存した終端結果または通知を待つ。"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import shutil
import sys
import time
import uuid
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import logging_config, session_registry, state, status_file
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.next_action import ActionableError, report, with_next_action
from agent_toolkit._common.wait_schedule import get_wait_timeout

_LOG = logging.getLogger("agent-toolkit.agents-server.wait")

# 次の操作の文面。待機の結果を受け取った主体が、再待機・調査・巻き取りのどれへ進むかを応答だけで決められるようにする。
_RERUN_WAIT_NEXT_ACTION = "`atk agents wait`を再実行して待機を続ける"
_NOTICE_NEXT_ACTION = "`notices`の通知を読んで対処し、終端を待つ場合は`atk agents wait`を再実行する"
_CONSUMED_NEXT_ACTION = (
    "結果は別の`atk agents wait`が受領済みのため、その出力を参照する。後続の結果を待つ場合は`atk agents wait`を再実行する"
)
_PRECEDING_WAIT_NEXT_ACTION = (
    "先行する`atk agents wait`の終了を待って再実行する。"
    "先行する待機が応答しないまま残っている場合は、そのプロセスを停止してから再実行する"
)
_BROKEN_STATE_NEXT_ACTION = (
    "agents_serverの状態が壊れているため、MCPの`list`で保持状態を確かめ、agents_serverの不具合としてユーザーへ報告する"
)
_READ_FAILURE_NEXT_ACTION = (
    "回収済みの結果は出力済みのため再取得しない。"
    "MCPの`show`で対象sessionの状態を確かめ、読めない状態が続く場合はagents_serverの不具合としてユーザーへ報告する"
)
_ABSENT_TARGETS_NEXT_ACTION = "委譲先を起動してから`atk agents wait`を実行する"
_IDENTITY_NEXT_ACTION = "同じsessionで`atk agents list`と`atk agents wait`を実行する"
_FAILED_RESULT_NEXT_ACTION = (
    "`agent_message`と`error`で失敗の種類を確かめる。作業を続けられる失敗なら同じsessionへ`send_message`で継続し、"
    "利用上限・認証・過負荷など候補の可用性による失敗なら別の`model_type`で起動し直し、いずれも成立しなければ作業を巻き取る"
)
_INTERRUPTED_RESULT_NEXT_ACTION = "中断を要求していない場合は同じsessionへ`send_message`で継続するか、作業を巻き取る"
_BODY_FILE_NEXT_ACTION = (
    "回収した結果は退避して保持しているため、管理対象一時領域へ書き込めない原因（容量や権限）を解消してから"
    "`atk agents wait`を再実行し、同じ結果を受け取る"
)
_BODY_TEMP_PREFIX = "agents-wait"
"""結果本文のファイルを置く管理対象一時領域の接頭辞。呼び出しごとに新しい領域を作成し、7日後の自動削除へ委ねる。"""


class _BodyFileWriter:
    """終端結果の`agent_message`をエスケープを含まないMarkdownファイルへ書き、その絶対パスを返す。

    呼び出し元がJSON文字列のエスケープを解く処理を持たずに結果本文を読めるようにするためである。
    書込先の管理対象一時領域は最初の書込時に1回だけ作成する。
    """

    def __init__(self) -> None:
        self._directory: pathlib.Path | None = None

    def write(self, session_id: str, result: Mapping[str, Any]) -> pathlib.Path:
        """本文を書いたファイルの絶対パスを返す。書けない場合は`OSError`か`ManagedTempError`を送出する。"""
        if self._directory is None:
            self._directory = _managed_temp.create_managed_temp(_BODY_TEMP_PREFIX)
        turn_seq = result.get("turn_seq")
        # labelは利用者の自由な文字列を含み得るため、ファイル名はsession識別子とturn番号だけで組み立てる。
        suffix = f"-turn{turn_seq}" if isinstance(turn_seq, int) and not isinstance(turn_seq, bool) else ""
        path = self._directory / f"{session_id}{suffix}.md"
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(str(result["agent_message"]))
        return path


def _wait_run_directory(root_session_id: str, owner: str, state_root: pathlib.Path | None) -> pathlib.Path:
    return status_file.status_directory(root_session_id, state_root) / "wait-results" / owner


def _write_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", fsync=True)


def _read_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _matching_current_wait_run(run_directory: pathlib.Path, targets: list[str]) -> pathlib.Path | None:
    current = _read_json(run_directory / "current.json")
    run_id = current.get("run_id") if current is not None else None
    if not isinstance(run_id, str):
        return None
    run_path = run_directory / f"{run_id}.json"
    run = _read_json(run_path)
    if run is not None and run.get("status") in {"running", "published"} and not isinstance(run.get("targets"), list):
        return None
    if run is None or run.get("targets") != targets:
        return None
    if run.get("status") in {"foreground-delivered", "consumed"} or (
        run.get("status") == "published" and run.get("continuable") is True
    ):
        return None
    return run_path if run.get("status") in {"running", "published"} else None


def _stashed_wait_targets(run_path: pathlib.Path) -> set[str]:
    """回収途中に原本から移した結果と通知のsessionを返す。"""
    stash = run_path.with_suffix("")
    result_ids = {path.stem for path in (stash / "results").glob("*.json")}
    notice_ids = {path.name.split(".", 1)[0] for path in (stash / "notices").glob("*.json")}
    return {session_id for session_id in result_ids | notice_ids if status_file.valid_session_id(session_id)}


def _publish_wait_result(
    run_path: pathlib.Path,
    output: str,
    code: int,
    *,
    stream: str = "stdout",
    continuable: bool = False,
) -> int:
    value = _read_json(run_path) or {}
    value.update(
        {
            "status": "published",
            "output": output,
            "exit_code": code,
            "stream": stream,
            "continuable": False,
        }
    )
    _write_json(run_path, value)
    print(output, file=sys.stderr if stream == "stderr" else sys.stdout, flush=True)
    value["status"] = "foreground-delivered"
    value["continuable"] = continuable
    _write_json(run_path, value)
    _remove_wait_stash(run_path)
    return code


def _remove_wait_stash(run_path: pathlib.Path) -> None:
    """公開済みrunの回復用退避物を回収する。"""
    try:
        shutil.rmtree(run_path.with_suffix(""))
    except FileNotFoundError:
        pass
    except OSError as exc:
        _LOG.warning("wait退避物を回収できませんでした: path=%s error=%s", run_path.with_suffix(""), exc)


def _consume_wait_result(run_path: pathlib.Path) -> int:
    value = _read_json(run_path)
    if value is None:
        return _fail(f"先行する待機の結果記録を読めません: {run_path}", 8, next_action=_PRECEDING_WAIT_NEXT_ACTION)
    if value.get("status") == "consumed":
        consumed = {"status": "consumed", "run_id": value.get("run_id"), "next_action": _CONSUMED_NEXT_ACTION}
        print(json.dumps(consumed, ensure_ascii=False, separators=(",", ":")))
        return 8
    output = value.get("output")
    code = value.get("exit_code")
    stream = value.get("stream")
    if (
        value.get("status") not in {"published", "foreground-delivered"}
        or not isinstance(output, str)
        or not isinstance(code, int)
    ):
        return _fail(f"先行する待機の終端結果が公開されていません: {run_path}", 8, next_action=_PRECEDING_WAIT_NEXT_ACTION)
    print(output, file=sys.stderr if stream == "stderr" else sys.stdout, flush=True)
    value["status"] = "consumed"
    _write_json(run_path, value)
    _remove_wait_stash(run_path)
    return code


def _absent_targets_message(owner_status_file: str) -> str:
    """待機対象の不在を理由とする終了の理由を返す。"""
    return f"待機対象の登録が0件で、保持中のsessionも0件です: owner={owner_status_file}"


def _fail(message: str, code: int, *, next_action: str, session_id: str | None = None) -> int:
    """標準エラーへ理由と次の操作を出力してから非0の終了コードで異常終了する。

    異常終了を返すときに理由と次の操作を必ず示すため、`message`と`next_action`を必須の引数とする。
    """
    _LOG.info("wait_return reason=abnormal code=%d session_id=%s", code, session_id or "none")
    report(message, next_action=next_action)
    return code


def _with_result_next_action(result: dict[str, Any]) -> dict[str, Any]:
    """委譲先が失敗または中断で終端した結果へ、受領した主体の次の操作を加える。"""
    status = result.get("status")
    if status == "failed":
        result["next_action"] = _FAILED_RESULT_NEXT_ACTION
    elif status == "interrupted":
        result["next_action"] = _INTERRUPTED_RESULT_NEXT_ACTION
    return result


def _target_origins(
    own_status_path: pathlib.Path,
    result_directory: pathlib.Path,
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None,
) -> tuple[dict[str, set[str]], tuple[str, int, str] | None]:
    """現行の待機対象と由来を返し、解釈不能な入力は理由・終了コード・次の操作の診断へ変換する。"""
    listed = _read_sessions(own_status_path)
    invalid = [session["session_id"] for session in listed or () if not status_file.valid_session_id(session["session_id"])]
    if invalid:
        return {}, (f"session_idの形式が不正です: {invalid[0]}", 5, _BROKEN_STATE_NEXT_ACTION)
    listed_ids = {session["session_id"] for session in listed or ()}
    result_ids = _retained_result_session_ids(result_directory, owner_status_file=owner_status_file)
    registered_ids, registry_error = status_file.read_wait_targets(root_session_id, owner_status_file, state_root)
    if registry_error is not None:
        return {}, (f"待機対象登録簿を読めません: {registry_error}", 9, _BROKEN_STATE_NEXT_ACTION)
    # 状態ファイルにも結果ファイルにも無い登録は、登録簿が喪失か終端を示す場合に結果が生じないため解放する。
    # 終端の公開から結果ファイルの書込までの間は状態ファイルに行が残るため、回収前の対象を解放しない。
    releasable = {
        session_registry.Resolution.MISSING,
        session_registry.Resolution.RELEASED,
        session_registry.Resolution.TERMINAL,
    }
    for session_id in set(registered_ids) - listed_ids - result_ids:
        if session_registry.resolve(session_id, state_root=state_root).state in releasable:
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
    root_session_id: str | None = None,
) -> int:
    """自身が保持するsessionから、1回の巡回で回収できた終端結果と通知を全件返す。

    回収できたものは1件1行のJSON Linesで標準出力へ書く。1件ずつ返す形では、未回収の終端結果が
    残っている間は委譲元がその結果を消化する回数だけ起動を繰り返さないと、稼働中のsessionへ到達できない。
    回収の途中で終端結果の読取に失敗した場合は、同じ巡回で回収済みの本文を先に配送してから終わる。
    回収は結果ファイルと通知ファイルの削除を伴うため、その失敗を理由に配送を取りやめると回収済みの本文が失われる。
    読取の失敗は次の起動でも同じ状態で現れるため、その回の診断を1回遅らせても失われない。

    対象は、自身の書込主体の状態ファイルへ載るsessionと、終端結果ファイルが残るsessionの
    双方とする。後者を含めるのは、保持期限で一覧から外れたsessionの結果本文も回収するためである。
    対象集合は最初の待機の発行時点で登録簿へ保存し、再発行時も同じ集合を引き継ぐ。
    待機中に開始または再稼働したsessionも、巡回ごとに取得して対象へ追加する。
    状態ファイルは投影であり、対象の不在から権威あるsessionの喪失を判定できない。
    終端結果と通知が無い場合は、投影が消失しても待機上限まで非終端として扱う。
    待機対象の登録も、`starting`を含む保持中sessionも0件の場合は、待機しても回収対象が生じないため、
    両方の不在を理由として標準エラーへ書き、即座に非0で終わる。
    待機中に全対象が登録簿で終端を示したまま結果も状態も残さなくなった場合も、回収途中の退避物が無ければ同じ理由で終わる。
    """
    logging_config.configure_logging()
    env = os.environ if environment is None else environment
    root_resolution = None if root_session_id is not None else status_file.resolve_conversation_root(env, state_root)
    try:
        identity = status_file.resolve_wait_identity(env, root_session_id, state_root)
    except ValueError as error:
        return _fail(
            f"agents_serverの待機ルートまたは状態書込主体を解決できません: {error}",
            4,
            next_action=error.next_action if isinstance(error, ActionableError) else _IDENTITY_NEXT_ACTION,
        )
    if identity is None:
        return _fail("agents_serverの状態ディレクトリを解決できません。", 4, next_action=_IDENTITY_NEXT_ACTION)
    root_session_id = identity.root_session_id
    own_status_path = status_file.status_directory(root_session_id, state_root) / identity.file_name
    result_directory = status_file.results_directory(root_session_id, state_root)
    run_directory = _wait_run_directory(root_session_id, identity.file_name, state_root)
    origins, target_error = _target_origins(
        own_status_path,
        result_directory,
        root_session_id,
        identity.file_name,
        state_root,
    )
    if target_error is not None:
        message, code, next_action = target_error
        return _fail(message, code, next_action=next_action)
    current = _read_json(run_directory / "current.json")
    current_id = current.get("run_id") if current is not None else None
    current_run_path = run_directory / f"{current_id}.json" if isinstance(current_id, str) else None
    current_run = _read_json(current_run_path) if current_run_path is not None else None
    if current_run is not None and current_run.get("status") in {"running", "published"}:
        recorded_targets = current_run.get("targets")
        if (
            isinstance(recorded_targets, list)
            and current_run_path is not None
            and all(isinstance(session_id, str) and status_file.valid_session_id(session_id) for session_id in recorded_targets)
        ):
            stashed = _stashed_wait_targets(current_run_path)
            if set(origins) | stashed == set(recorded_targets):
                for session_id in stashed:
                    origins.setdefault(session_id, set()).add("run")
    ordered_ids = sorted(origins)
    own_sessions = _read_sessions(own_status_path)
    if not ordered_ids and (not own_status_path.exists() or own_sessions is not None):
        if root_resolution is not None and not root_resolution.mapping_confirmed:
            reason, next_action = status_file.unconfirmed_root_recovery(root_resolution, "atk agents wait")
            return _fail(reason, 4, next_action=next_action)
        return _fail(_absent_targets_message(identity.file_name), 10, next_action=_ABSENT_TARGETS_NEXT_ACTION)
    _LOG.info(
        "wait_start targets=%s origins=%s",
        ",".join(ordered_ids) or "none",
        ";".join(f"{session_id}:{','.join(sorted(origins[session_id]))}" for session_id in ordered_ids) or "none",
    )
    lock_directory = status_file.status_directory(root_session_id, state_root) / "wait-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / f"{identity.file_name}.lock"
    lock_file = lock_path.open("a+b")
    owns_lock = False
    run_path: pathlib.Path | None = None
    try:
        try:
            acquire_lock(lock_file, blocking=False)
            owns_lock = True
        except OSError:
            _LOG.info("wait_lock_failed owner=%s targets=%s", identity.file_name, ",".join(ordered_ids) or "none")
            run_directory = _wait_run_directory(root_session_id, identity.file_name, state_root)
            current = _read_json(run_directory / "current.json")
            run_id = current.get("run_id") if current is not None else None
            if not isinstance(run_id, str):
                return _fail(
                    "同じ書込主体の旧形式の待機がlockを保持しています: "
                    f"owner={identity.file_name}, targets={','.join(ordered_ids) or 'none'}",
                    8,
                    next_action=_PRECEDING_WAIT_NEXT_ACTION,
                )
            run_path = run_directory / f"{run_id}.json"
            acquire_lock(lock_file, blocking=True)
            owns_lock = True
        current_run_path = run_path if run_path is not None else _matching_current_wait_run(run_directory, ordered_ids)
        if current_run_path is not None:
            recorded = _read_json(current_run_path)
            if recorded is not None and recorded.get("status") == "running":
                if run_path is not None:
                    recorded_targets = recorded.get("targets")
                    if not isinstance(recorded_targets, list) or any(
                        not isinstance(session_id, str) or not status_file.valid_session_id(session_id)
                        for session_id in recorded_targets
                    ):
                        return _consume_wait_result(current_run_path)
                    ordered_ids = sorted(set(ordered_ids) | set(recorded_targets) | _stashed_wait_targets(current_run_path))
                run_path = current_run_path
            else:
                return _consume_wait_result(current_run_path)
        else:
            run_id = uuid.uuid4().hex
            run_path = run_directory / f"{run_id}.json"
            _write_json(
                run_path,
                {
                    "run_id": run_id,
                    "status": "running",
                    "targets": ordered_ids,
                    "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
                },
            )
            _write_json(run_directory / "current.json", {"run_id": run_id})
        status_file.retain_wait_targets(root_session_id, identity.file_name, ordered_ids, state_root)

        # 上限はMCPの`wait`と同じくプロンプトキャッシュの保持期間から導出し、CLIとMCPで同じ値を使う。
        # 委譲元がメイン会話かサブエージェントかを判定できないため、MCPと同じくmainのbucketを用いる。
        wait_timeout = get_wait_timeout("main")
        body_writer = _BodyFileWriter()
        started_at = time.monotonic()
        deadline = started_at + wait_timeout
        while True:
            current_origins, target_error = _target_origins(
                own_status_path,
                result_directory,
                root_session_id,
                identity.file_name,
                state_root,
            )
            if target_error is not None:
                message, code, next_action = target_error
                return _publish_wait_result(run_path, with_next_action(message, next_action), code, stream="stderr")
            # 待機中の対象不在は、全対象の登録簿が終端を示す場合だけ確定する。
            # 状態投影の消失と登録簿の不在は待機開始後の喪失の根拠にしないため、その場合は待機上限まで待つ。
            if (
                ordered_ids
                and not current_origins
                and not _stashed_wait_targets(run_path)
                and all(
                    session_registry.resolve(session_id, state_root=state_root).state is session_registry.Resolution.TERMINAL
                    for session_id in ordered_ids
                )
            ):
                _LOG.info("wait_return reason=absent owner=%s", identity.file_name)
                return _publish_wait_result(
                    run_path,
                    with_next_action(_absent_targets_message(identity.file_name), _ABSENT_TARGETS_NEXT_ACTION),
                    10,
                    stream="stderr",
                )
            for session_id in sorted(set(current_origins) - set(ordered_ids)):
                ordered_ids.append(session_id)
                ordered_ids.sort()
                status_file.retain_wait_targets(root_session_id, identity.file_name, [session_id], state_root)
                recorded = _read_json(run_path) or {}
                recorded["targets"] = ordered_ids
                _write_json(run_path, recorded)
                _LOG.info(
                    "wait_target_added session_id=%s origins=%s",
                    session_id,
                    ",".join(sorted(current_origins[session_id])),
                )
            status_paths = status_file.list_status_files(root_session_id, state_root)
            collected: list[dict[str, Any]] = []
            read_failure: str | None = None
            for session_id in ordered_ids:
                result_path = result_directory / f"{session_id}.json"
                result, read_error = status_file.take_result(
                    root_session_id,
                    session_id,
                    identity.file_name,
                    collector="atk-agents-wait",
                    state_root=state_root,
                    stash_path=run_path.with_suffix("") / "results" / f"{session_id}.json",
                )
                if read_error is not None:
                    read_failure = f"終端結果ファイルを読めません: {result_path}: {read_error}"
                    break
                notices = status_file.take_notices(
                    root_session_id,
                    session_id,
                    state_root,
                    stash_directory=run_path.with_suffix("") / "notices",
                )
                if result is not None:
                    result["session_id"] = session_id
                    if isinstance(result.get("agent_message"), str):
                        # 書込に失敗した場合は配送前に終え、run別の退避物と待機対象の登録を残して次の待機で再配送する。
                        try:
                            result["agent_message_path"] = str(body_writer.write(session_id, result))
                        except (_managed_temp.ManagedTempError, OSError) as error:
                            return _fail(
                                f"結果本文のファイルを書けません: session_id={session_id}: {error}",
                                11,
                                next_action=_BODY_FILE_NEXT_ACTION,
                                session_id=session_id,
                            )
                    if notices:
                        result["notices"] = notices
                    collected.append(_with_result_next_action(result))
                    status_file.release_wait_target(root_session_id, identity.file_name, session_id, state_root)
                    continue
                if notices:
                    response = _running_response(session_id, _session_output_activity(status_paths, session_id))
                    _add_session_label(response, status_paths, session_id)
                    response["notices"] = notices
                    response["next_action"] = _NOTICE_NEXT_ACTION
                    collected.append(response)
            if collected:
                output = "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in collected)
                _LOG.info(
                    "wait_return reason=collected count=%d session_ids=%s",
                    len(collected),
                    ",".join(str(item["session_id"]) for item in collected),
                )
                continuable = all(item.get("status") == "running" for item in collected)
                return _publish_wait_result(run_path, output, 0, continuable=continuable)
            if read_failure is not None:
                return _publish_wait_result(
                    run_path, with_next_action(read_failure, _READ_FAILURE_NEXT_ACTION), 6, stream="stderr"
                )

            now = time.monotonic()
            retained = {session_id: _session_is_retained(status_paths, session_id) for session_id in ordered_ids}
            selected = next((session_id for session_id in ordered_ids if retained[session_id] is not False), None)
            if selected is None and ordered_ids:
                selected = ordered_ids[0]
            response = _running_response(
                selected,
                {} if selected is None else _session_output_activity(status_paths, selected),
            )
            if selected is not None:
                _add_session_label(response, status_paths, selected)
            remaining = deadline - now
            if remaining <= 0:
                response["next_action"] = _RERUN_WAIT_NEXT_ACTION
                output = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
                _LOG.info("wait_return reason=timeout session_id=%s", selected or "none")
                return _publish_wait_result(run_path, output, 3, continuable=True)
            time.sleep(min(1.0, remaining))
    finally:
        if owns_lock and not lock_file.closed:
            release_lock(lock_file)
        if not lock_file.closed:
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
    """状態ファイルから保持中sessionの活動とテキスト出力の観測値を返す。"""
    for path in paths:
        sessions = _read_sessions(path)
        if sessions is None:
            continue
        for session in sessions:
            if session["session_id"] != session_id:
                continue
            updated_at = session.get("updated_at")
            output_updated_at = session.get("output_updated_at")
            started_at = session.get("started_at")
            return state.activity_projection(
                updated_at=updated_at if isinstance(updated_at, str) else None,
                output_updated_at=output_updated_at if isinstance(output_updated_at, str) else None,
                started_at=started_at if isinstance(started_at, str) else None,
                api_error=session.get("api_error") if isinstance(session.get("api_error"), dict) else None,
            )
    return {}


def _add_session_label(response: dict[str, Any], paths: list[pathlib.Path], session_id: str) -> None:
    """状態ファイルのsession一覧が持つ起動時の`label`を、非終端の待機応答へ加える。"""
    for path in paths:
        for session in _read_sessions(path) or ():
            if session["session_id"] == session_id:
                label = session.get("label")
                if isinstance(label, str) and label:
                    response["label"] = label
                return


def _read_sessions(path: pathlib.Path) -> list[dict[str, Any]] | None:
    """状態ファイルを解釈し、session一覧または`None`を返す。"""
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
    """非終端の待機応答へ活動とテキスト出力の観測値を加える。

    対象を1件も解決できない場合は`session_id`を省き、`status`だけを返す。
    """
    response: dict[str, Any] = {"status": "running"}
    if session_id is not None:
        response = {"session_id": session_id, "status": "running"}
    response.update(output_activity)
    return response
