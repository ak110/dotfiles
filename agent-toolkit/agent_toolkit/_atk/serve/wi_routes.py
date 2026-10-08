"""WI画面のAPI（一覧・詳細・同期・変更）のルートの登録と、要求の検証。"""

import json
import logging
import math
import re
import typing

import quart
import werkzeug.exceptions

from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import runtime as _runtime
from agent_toolkit._atk.serve import shell_routes as _shell_routes
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.serve import wi_operations as _wi_operations
from agent_toolkit._atk.wi import batch as awi_batch
from agent_toolkit._atk.wi import constants as wi_constants

logger = logging.getLogger(__name__)

_STATUS_FILTERS = {"all", "active", "processable", *wi_constants.WI_STATES}


_ANSWERED_FILTERS = {"all", "yes", "no"}


_PLAN_FILTERS = {"all", "normal", "plan"}
"""`plan`は、廃止した計画ファイル付きの型で保存された項目を見分ける読取互換の表示区分である。"""


_SOURCE_KIND_FILTERS = {"human", "agent"}


_PERIOD_FILTERS = {"all", *_wi_operations.PERIOD_WEEKS}


_ENTRY_PAGE_SIZE = 100


_DECIMAL_INTEGER_RE = re.compile(r"[0-9]+")


async def _request_json() -> typing.Any:
    """不正なJSON本文をWeb API用入力エラーへ正規化する。"""
    try:
        return await quart.request.get_json()
    except werkzeug.exceptions.BadRequest as error:
        raise _wi_operations.WebApiInputError("JSON本文の構文が不正です") from error


def _json_object(
    value: typing.Any,
    *,
    allowed: set[str],
    required: set[str] | None = None,
) -> _wi_operations.JsonObject:
    if not isinstance(value, dict):
        raise _wi_operations.WebApiInputError("JSON objectを指定してください")
    unknown = set(value) - allowed
    if unknown:
        raise _wi_operations.WebApiInputError(f"未知のキーです: {', '.join(sorted(unknown))}")
    missing = (required or set()) - set(value)
    if missing:
        raise _wi_operations.WebApiInputError(f"必須キーがありません: {', '.join(sorted(missing))}")
    return value


def _strings(value: typing.Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise _wi_operations.WebApiInputError(f"{name}は空でない文字列の配列で指定してください")
    return value


def _optional_string(data: _wi_operations.JsonObject, name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _wi_operations.WebApiInputError(f"{name}は空でない文字列で指定してください")
    return value


def _specified_string(data: _wi_operations.JsonObject, name: str) -> str | None:
    if name not in data:
        return None
    value = data[name]
    if not isinstance(value, str) or not value.strip():
        raise _wi_operations.WebApiInputError(f"{name}は空でない文字列で指定してください")
    return value


def _specified_text(data: _wi_operations.JsonObject, name: str) -> str | None:
    """指定済みの本文を空文字列も含めて受理する。"""
    if name not in data:
        return None
    value = data[name]
    if not isinstance(value, str):
        raise _wi_operations.WebApiInputError(f"{name}は文字列で指定してください")
    return value


def _ignored_single_fields(data: dict[str, typing.Any]) -> list[str]:
    """一括取り込みへ切り替えた登録で、使わなかった単件用の入力項目名を返す。"""
    ignored = [name for name in ("target_repo", "scope") if data.get(name)]
    question_type = data.get("question_type")
    # 新規追加ダイアログが初期値として送る回答形式は、ユーザーが入力した値として扱わない。
    # 初期値を選択肢の入力が要らない形式にし、種別を選び違えたshow形式の本文も一括登録へ切り替えられるようにする。
    if question_type is not None and question_type != wi_constants.QUESTION_TYPE_YES_NO:
        ignored.append("question_type")
    if data.get("choices"):
        ignored.append("choices")
    return ignored


def _entry_page(filters: dict[str, str]) -> int | None:
    """一覧APIの明示ページを正の10進整数として返す。"""
    raw_page = filters.get("page")
    if raw_page is None:
        return None
    if not _DECIMAL_INTEGER_RE.fullmatch(raw_page):
        raise _wi_operations.WebApiInputError("pageは正の10進整数で指定してください")
    try:
        page = int(raw_page)
    except ValueError as error:
        raise _wi_operations.WebApiInputError("pageは正の10進整数で指定してください") from error
    if page <= 0:
        raise _wi_operations.WebApiInputError("pageは正の10進整数で指定してください")
    return page


def _validate_entry_filters(filters: dict[str, str]) -> None:
    """一覧APIのquery組合せを検証する。"""
    if filters.get("type", "all") not in {"all", *wi_constants.WI_TYPES}:
        raise _wi_operations.WebApiInputError("typeが不正です")
    if filters.get("status", "all") not in _STATUS_FILTERS:
        raise _wi_operations.WebApiInputError("statusが不正です")
    if filters.get("answered", "all") not in _ANSWERED_FILTERS:
        raise _wi_operations.WebApiInputError("answeredが不正です")
    if filters.get("plan", "all") not in _PLAN_FILTERS:
        raise _wi_operations.WebApiInputError("planが不正です")
    if filters.get("period", "all") not in _PERIOD_FILTERS:
        raise _wi_operations.WebApiInputError("periodが不正です")
    _entry_page(filters)
    if "source_empty" in filters and filters["source_empty"] != "true":
        raise _wi_operations.WebApiInputError("source_emptyはtrueで指定してください")
    if "source" in filters and "source_empty" in filters:
        raise _wi_operations.WebApiInputError("sourceとsource_emptyは同時に指定できません")
    if "source_kind" in filters and filters["source_kind"] not in _SOURCE_KIND_FILTERS:
        raise _wi_operations.WebApiInputError("source_kindが不正です")
    if "source_kind" in filters and ("source" in filters or "source_empty" in filters):
        raise _wi_operations.WebApiInputError("source_kindとsource/source_emptyは同時に指定できません")
    for name in ("target_repo", "source", "q"):
        if name in filters and not filters[name].strip():
            raise _wi_operations.WebApiInputError(f"{name}は空でない文字列で指定してください")


def _register_query_routes(app: quart.Quart, runtime: _runtime.ServeRuntime) -> None:
    """同期・一覧・詳細・イベント購読ルートを登録する。"""
    ops, workers = runtime.operations, runtime.workers

    @app.post("/api/sync")
    async def sync() -> quart.Response:
        return quart.jsonify(synced=await runtime.synchronize())

    @app.get("/api/repos")
    async def repos() -> quart.Response:
        unknown = set(quart.request.args) - {"status"}
        if unknown:
            raise _wi_operations.WebApiInputError(f"未知のqueryです: {', '.join(sorted(unknown))}")
        status_filter = quart.request.args.get("status", "active")
        if status_filter not in _STATUS_FILTERS:
            raise _wi_operations.WebApiInputError("statusが不正です")
        return quart.jsonify(repos=await workers.run(ops.target_repos, status_filter))

    @app.get("/api/entries")
    async def entries() -> quart.Response:
        allowed = {
            "type",
            "status",
            "answered",
            "plan",
            "period",
            "page",
            "target_repo",
            "source",
            "source_empty",
            "source_kind",
            "q",
        }
        unknown = set(quart.request.args) - allowed
        if unknown:
            raise _wi_operations.WebApiInputError(f"未知のqueryです: {', '.join(sorted(unknown))}")
        filters = dict(quart.request.args.items())
        _validate_entry_filters(filters)
        result, warnings = await workers.run(ops.entries_with_warnings, filters)
        requested_page = _entry_page(filters)
        if requested_page is not None:
            total_count = len(result)
            page_count = max(1, math.ceil(total_count / _ENTRY_PAGE_SIZE))
            page = min(requested_page, page_count)
            start = (page - 1) * _ENTRY_PAGE_SIZE
            return quart.jsonify(
                entries=result[start : start + _ENTRY_PAGE_SIZE],
                warnings=warnings,
                pagination={
                    "page": page,
                    "page_size": _ENTRY_PAGE_SIZE,
                    "page_count": page_count,
                    "total_count": total_count,
                },
            )
        return quart.jsonify(entries=result, warnings=warnings)

    @app.get("/api/entries/<state_name>/<filename>")
    async def detail(state_name: str, filename: str) -> quart.Response:
        entry = await workers.run(ops.detail, state_name, filename)
        return quart.Response(
            json.dumps({"entry": entry}, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            content_type="application/json",
        )

    @app.get("/api/events")
    async def events() -> quart.Response:
        current_state: serve_state.ServeState = quart.current_app.config["SERVE_STATE"]
        return quart.Response(current_state.events(heartbeat=_runtime.SSE_HEARTBEAT_SEC), content_type="text/event-stream")


async def _transition_request(runtime: _runtime.ServeRuntime, action: str, allowed: set[str]) -> quart.Response:
    """状態遷移APIの共通payloadを解析して操作する。"""
    data = _json_object(await _request_json(), allowed=allowed, required={"filenames"})
    filenames = _strings(data["filenames"], "filenames")
    optional = {name: _optional_string(data, name) for name in ("note", "commit", "target_repo") if name in allowed}
    force = False
    if "force" in allowed and "force" in data:
        if not isinstance(data["force"], bool):
            raise _wi_operations.WebApiInputError("forceはbooleanで指定してください")
        force = data["force"]
    state_name = _optional_string(data, "state") if "state" in allowed else None
    if state_name is not None:
        valid_states = wi_constants.TRANSITION_EXPLICIT_STATES.get(action, ())
        if state_name not in valid_states:
            rendered_states = "、".join(valid_states) if valid_states else "なし"
            raise _wi_operations.WebApiInputError(
                f"操作{action}はstate={state_name}を受理しません。明示stateとして受理する状態: {rendered_states}"
            )
    expected_content = _specified_text(data, "expected_content") if "expected_content" in allowed else None
    if state_name is None and expected_content is None:
        result = await runtime.workers.run(
            runtime.operations.transition,
            action,
            filenames,
            note=optional.get("note"),
            commit=optional.get("commit"),
            target_repo=optional.get("target_repo"),
            force=force,
        )
    else:
        result = await runtime.workers.run(
            runtime.operations.transition,
            action,
            filenames,
            note=optional.get("note"),
            commit=optional.get("commit"),
            target_repo=optional.get("target_repo"),
            force=force,
            state=state_name,
            expected_content=expected_content,
        )
    return quart.jsonify(filenames=result)


def _register_mutation_routes(app: quart.Quart, runtime: _runtime.ServeRuntime) -> None:
    """編集・投入・ユーザーコメント・回答・状態遷移ルートを登録する。"""
    ops, workers = runtime.operations, runtime.workers

    @app.after_request
    async def log_wi_mutation(response: quart.Response) -> quart.Response:
        path = quart.request.path
        if quart.request.method not in {"POST", "PUT"} or not (path == "/api/entries" or path.startswith("/api/entries/")):
            return response
        operation = quart.request.endpoint or "unknown"
        request_data = await quart.request.get_json(silent=True)
        filename = (quart.request.view_args or {}).get("filename")
        if filename is None and isinstance(request_data, dict):
            filename = request_data.get("filename")
        request_targets = [filename] if isinstance(filename, str) else []
        if not request_targets and isinstance(request_data, dict) and isinstance(request_data.get("filenames"), list):
            request_targets = [name for name in request_data["filenames"] if isinstance(name, str)]
        if response.status_code >= 400:
            logger.warning(
                "WI更新失敗: 操作=%s 対象=%s HTTP=%s",
                operation,
                ", ".join(request_targets) or "-",
                response.status_code,
            )
            return response
        result = await response.get_json()
        filenames = result.get("filenames") if isinstance(result, dict) else None
        if filenames is None:
            filenames = request_targets
        outcome = result.get("changed", "完了") if isinstance(result, dict) else "完了"
        logger.info("WI更新: 操作=%s 対象=%s 結果=%s", operation, ", ".join(filenames) or "-", outcome)
        return response

    @app.put("/api/entries/<state_name>/<filename>")
    async def edit_entry(state_name: str, filename: str) -> quart.Response:
        data = _json_object(await _request_json(), allowed={"content", "expected_content"}, required={"content"})
        if not isinstance(data["content"], str) or not data["content"].strip():
            raise _wi_operations.WebApiInputError("contentは空でない文字列で指定してください")
        expected_content = _specified_string(data, "expected_content")
        return quart.jsonify(changed=await workers.run(ops.edit, state_name, filename, data["content"], expected_content))

    @app.post("/api/entries/user-comment")
    async def save_user_comment() -> quart.Response:
        data = _json_object(
            await _request_json(),
            allowed={"state", "filename", "comment", "expected_content"},
            required={"state", "filename", "comment", "expected_content"},
        )
        state_name = data["state"]
        filename = data["filename"]
        comment = data["comment"]
        expected_content = data["expected_content"]
        for name, value in (
            ("state", state_name),
            ("filename", filename),
            ("expected_content", expected_content),
        ):
            if not isinstance(value, str) or not value.strip():
                raise _wi_operations.WebApiInputError(f"{name}は空でない文字列で指定してください")
        # 空のコメントはユーザーコメント節の削除を表すため、文字列であることだけを確かめる。
        if not isinstance(comment, str):
            raise _wi_operations.WebApiInputError("commentは文字列で指定してください")
        return quart.jsonify(
            changed=await workers.run(
                ops.user_comment,
                state_name,
                filename,
                comment,
                expected_content,
            )
        )

    @app.post("/api/entries")
    async def add_entry() -> tuple[quart.Response, int]:
        data = _json_object(
            await _request_json(),
            allowed={"type", "messages", "target_repo", "scope", "question_type", "choices", "raw_text"},
            required={"type", "messages"},
        )
        if data["type"] not in wi_constants.WI_TYPES:
            raise _wi_operations.WebApiInputError("typeが不正です")
        raw_text = data.get("raw_text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise _wi_operations.WebApiInputError("raw_textは文字列で指定してください")
        # 種別の選び忘れで一括登録の本文を1件のWIとして保存しないよう、show形式の構造を持つ本文は一括取り込みへ切り替える。
        # ブラウザーの単件送信はtrim済みのため、原文保持に必要な未trimの入力（raw_text）で判定・取り込みする。
        if raw_text is not None and awi_batch.is_show_batch_format(raw_text):
            result = await workers.run(ops.add_batch, raw_text)
            return quart.jsonify(batch=True, ignored_fields=_ignored_single_fields(data), **result), 201
        messages = _strings(data["messages"], "messages")
        for key in ("target_repo",):
            if key in data and (not isinstance(data[key], str) or not data[key]):
                raise _wi_operations.WebApiInputError(f"{key}は空でない文字列で指定してください")
        question_type = data.get("question_type")
        filenames = await workers.run(
            ops.add,
            messages,
            entry_type=data["type"],
            target_repo=data.get("target_repo"),
            scope=data.get("scope"),
            question_type=question_type,
            choices=_strings(data["choices"], "choices") if "choices" in data else None,
        )
        return quart.jsonify(filenames=filenames), 201

    @app.post("/api/entries/batch")
    async def add_batch() -> tuple[quart.Response, int]:
        data = _json_object(await _request_json(), allowed={"text"}, required={"text"})
        if not isinstance(data["text"], str) or not data["text"].strip():
            raise _wi_operations.WebApiInputError("textは空でない文字列で指定してください")
        result = await workers.run(ops.add_batch, data["text"])
        return quart.jsonify(**result), 201

    @app.post("/api/entries/answer")
    async def answer_uwi() -> quart.Response:
        data = _json_object(
            await _request_json(),
            allowed={"filename", "state", "answer", "expected_content"},
            required={"filename", "answer"},
        )
        if not isinstance(data["answer"], str) or not data["answer"].strip():
            raise _wi_operations.WebApiInputError("answerは空でない文字列で指定してください")
        if not isinstance(data["filename"], str):
            raise _wi_operations.WebApiInputError("filenameは文字列で指定してください")
        expected_content = _specified_string(data, "expected_content")
        state_name = _optional_string(data, "state")
        if state_name is not None and state_name not in (*wi_constants.WI_PROCESSABLE_STATES, wi_constants.WI_STATE_HOLD):
            raise _wi_operations.WebApiInputError("stateはinbox、processingまたはholdで指定してください")
        if state_name is None:
            changed = await workers.run(ops.answer_uwi, data["filename"], data["answer"], expected_content)
        else:
            changed = await workers.run(
                ops.answer_uwi,
                data["filename"],
                data["answer"],
                expected_content,
                state_name,
            )
        saved = await workers.run(ops.detail, state_name or wi_constants.WI_STATE_INBOX, data["filename"])
        return quart.jsonify(changed=changed, state=saved["state"])

    transition_specs = {
        "start-processing": {"filenames", "target_repo", "state"},
        "return-to-inbox": {"filenames", "target_repo", "state"},
        "hold": {"filenames", "target_repo", "state"},
        "unhold": {"filenames", "target_repo"},
        "adopt": {"filenames", "note", "commit", "target_repo", "state"},
        "reject": {"filenames", "note", "commit", "target_repo", "state"},
        "remove": {"filenames", "note", "target_repo", "force", "state", "expected_content"},
    }

    def make_transition_handler(action: str, allowed: set[str]) -> typing.Callable[[], typing.Awaitable[quart.Response]]:
        async def transition_handler() -> quart.Response:
            return await _transition_request(runtime, action, allowed)

        return transition_handler

    for action, allowed in transition_specs.items():
        endpoint = action.replace("-", "_")
        app.add_url_rule(f"/api/entries/{action}", endpoint, make_transition_handler(action, allowed), methods=["POST"])

    @app.post("/api/entries/commit")
    async def commit_entries() -> quart.Response:
        _json_object(await _request_json(), allowed=set())
        return quart.jsonify(changed=await workers.run(ops.commit))


def register_wi_routes(
    app: quart.Quart,
    runtime: _runtime.ServeRuntime,
    plans_context: serve_plans.PlansContext,
) -> None:
    """WI画面の資産・一覧・更新のルートをまとめて登録する。"""
    _shell_routes.register_awi_asset_routes(app, plans_context)
    _register_query_routes(app, runtime)
    _register_mutation_routes(app, runtime)
