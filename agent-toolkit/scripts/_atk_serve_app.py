"""`atk serve`のQuartアプリケーション。"""

import asyncio
import datetime
import pathlib
import re
import subprocess
import typing

import _atk_fb_add as feedback_add
import _atk_fb_common as common
import _atk_fb_mutations as feedback_mutations
import _atk_fb_repo as feedback_repo
import _atk_fb_tbd as tbd_mutations
import _atk_serve_assets as assets
import _atk_serve_config as serve_config
import _atk_serve_state as serve_state
import filelock
import quart
import werkzeug.exceptions

type JsonObject = dict[str, typing.Any]
_ENTRY_STATES = {
    "feedback": {"inbox", "processing", "adopted", "rejected"},
    "tbd": {"inbox", "adopted"},
}
_STATUS_FILTERS = {"all", "inbox", "processing", "adopted", "rejected", "answered", "unanswered"}
_WEB_LOCK_TIMEOUT = 2.0


async def _request_json() -> typing.Any:
    """不正なJSON本文をWeb API用入力エラーへ正規化する。"""
    try:
        return await quart.request.get_json()
    except werkzeug.exceptions.BadRequest as error:
        raise common.WebInputError("JSON本文の構文が不正です") from error


def _json_object(
    value: typing.Any,
    *,
    allowed: set[str],
    required: set[str] | None = None,
) -> JsonObject:
    if not isinstance(value, dict):
        raise common.WebInputError("JSON objectを指定してください")
    unknown = set(value) - allowed
    if unknown:
        raise common.WebInputError(f"未知のキーです: {', '.join(sorted(unknown))}")
    missing = (required or set()) - set(value)
    if missing:
        raise common.WebInputError(f"必須キーがありません: {', '.join(sorted(missing))}")
    return value


def _strings(value: typing.Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise common.WebInputError(f"{name}は空でない文字列の配列で指定してください")
    return value


def _optional_string(data: JsonObject, name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise common.WebInputError(f"{name}は空でない文字列で指定してください")
    return value


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[4:closing].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip()] = value.strip()
    return result


def _summary(text: str, kind: str) -> str:
    body = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
    lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("## ")]
    if kind == "tbd":
        lines = [line for line in lines if not line.startswith("<!--")]
    return lines[0][:160] if lines else ""


def _entry(path: pathlib.Path, kind: str, state: str, text: str) -> dict[str, object]:
    metadata = _frontmatter(text)
    category_match = re.search(r"^- カテゴリ:\s*(.+)$", text, re.MULTILINE)
    answered = common.is_tbd_answered(text) if kind == "tbd" else None
    return {
        "kind": kind,
        "state": state,
        "filename": path.name,
        "answered": answered,
        "target_repo": metadata.get("target_repo"),
        "source": metadata.get("source"),
        "category": category_match.group(1).strip() if category_match else None,
        "summary": _summary(text, kind),
        "updated_at": datetime.datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=datetime.UTC,
        ).isoformat(),
    }


class BoundedWorkers:
    """要求キャンセル後も同期処理完了まで同時実行枠を保持する。"""

    def __init__(self, limit: int) -> None:
        self._semaphore = asyncio.Semaphore(limit)

    async def run[**P, R](self, function: typing.Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        """同期関数を有界ワーカーで実行する。"""

        async def managed() -> R:
            async with self._semaphore:
                return await asyncio.to_thread(function, *args, **kwargs)

        return await asyncio.shield(asyncio.create_task(managed()))


class Operations:
    """同期ファイル操作をWeb API向けに提供する。"""

    def __init__(self, private_notes: pathlib.Path) -> None:
        self.private_notes = private_notes

    def status(self) -> bool:
        return common.flag_path(pathlib.Path.home()).exists()

    def set_enabled(self, enabled: bool) -> bool:
        path = common.flag_path(pathlib.Path.home())
        path.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            path.touch()
        else:
            path.unlink(missing_ok=True)
        return enabled

    def entries(self, filters: dict[str, str]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        with common.repo_lock(self.private_notes, timeout=_WEB_LOCK_TIMEOUT):
            common.pull(self.private_notes)
            kind_filter = filters.get("type", "all")
            status_filter = filters.get("status", "all")
            for kind, states in _ENTRY_STATES.items():
                if kind_filter not in ("all", kind):
                    continue
                for state in states:
                    if status_filter not in ("all", state, "answered", "unanswered"):
                        continue
                    directory = self.private_notes / kind / state
                    for path in sorted(directory.glob("*.md")):
                        text = path.read_text(encoding="utf-8")
                        item = _entry(path, kind, state, text)
                        if status_filter == "answered" and item["answered"] is not True:
                            continue
                        if status_filter == "unanswered" and item["answered"] is not False:
                            continue
                        if any(filters.get(key) and item[key] != filters[key] for key in ("target_repo", "source", "category")):
                            continue
                        result.append(item)
        return result

    def detail(self, kind: str, state: str, filename: str) -> dict[str, object]:
        if kind not in _ENTRY_STATES or state not in _ENTRY_STATES[kind]:
            raise common.WebInputError("kind又はstateが不正です")
        with common.repo_lock(self.private_notes, timeout=_WEB_LOCK_TIMEOUT):
            common.pull(self.private_notes)
            path = common.validate_filename(filename, self.private_notes / kind / state)
            if not path.is_file():
                raise FileNotFoundError(filename)
            text = path.read_text(encoding="utf-8")
            return {**_entry(path, kind, state, text), "content": text}

    def edit(self, kind: str, state: str, filename: str, content: str) -> bool:
        try:
            if kind == "feedback":
                return feedback_mutations.edit_feedback(
                    self.private_notes,
                    state=state,
                    filename=filename,
                    content=content,
                    lock_timeout=_WEB_LOCK_TIMEOUT,
                )
            if state != "inbox":
                raise common.WebInputError("このTBD状態は編集できません")
            return tbd_mutations.edit_tbd(
                self.private_notes,
                filename=filename,
                content=content,
                lock_timeout=_WEB_LOCK_TIMEOUT,
            )
        except SystemExit as error:
            raise common.WebInputError("指定したエントリを操作できません") from error

    def add_feedback(
        self,
        messages: list[str],
        source: str | None,
        target_repo: str | None,
    ) -> list[str]:
        """feedbackを原子的に追加する。"""
        try:
            resolved_target_repo = feedback_repo.resolve_repo_id(target_repo)
        except SystemExit as error:
            raise common.WebInputError("target_repoを解決できません") from error
        return feedback_add.add_feedback(
            self.private_notes,
            messages=messages,
            target_repo=resolved_target_repo,
            source=source,
            now=datetime.datetime.now(),
            lock_timeout=_WEB_LOCK_TIMEOUT,
        )

    def add_tbd(
        self,
        messages: list[str],
        *,
        scope: str,
        question_type: str,
        choices: list[str] | None,
        target_repo: str | None,
        source: str | None,
    ) -> list[str]:
        """TBDを原子的に追加する。"""
        if question_type not in {"choice", "yes-no", "free-form"}:
            raise common.WebInputError("question_typeが不正です")
        if question_type == "choice" and (choices is None or len(choices) < 2):
            raise common.WebInputError("choice形式には2件以上のchoicesが必要です")
        if question_type != "choice" and choices is not None:
            raise common.WebInputError("choicesはchoice形式でのみ指定できます")
        try:
            resolved_target_repo = feedback_repo.resolve_repo_id(target_repo)
        except SystemExit as error:
            raise common.WebInputError("target_repoを解決できません") from error
        return tbd_mutations.add_tbd(
            self.private_notes,
            messages=messages,
            target_repo=resolved_target_repo,
            scope=scope,
            source=source,
            question_type=question_type,
            choices=",".join(choices) if choices else None,
            now=datetime.datetime.now(),
            lock_timeout=_WEB_LOCK_TIMEOUT,
        )

    def answer_tbd(self, filename: str, answer: str) -> bool:
        """TBD回答欄を置換する。"""
        return tbd_mutations.answer_tbd(
            self.private_notes,
            filename=filename,
            answer=answer,
            lock_timeout=_WEB_LOCK_TIMEOUT,
        )

    def transition(
        self,
        kind: str,
        action: str,
        filenames: list[str],
        *,
        note: str | None = None,
        commit: str | None = None,
        category: str | None = None,
        target_repo: str | None = None,
    ) -> list[str]:
        """複数エントリを全件検証後に移動又は削除する。"""
        try:
            if kind == "feedback":
                return feedback_mutations.transition_feedback(
                    self.private_notes,
                    action=action,
                    filenames=filenames,
                    now=datetime.datetime.now(),
                    target_repo=target_repo,
                    note=note,
                    commit=commit,
                    category=category,
                    lock_timeout=_WEB_LOCK_TIMEOUT,
                )
            return tbd_mutations.transition_tbd(
                self.private_notes,
                action=action,
                filenames=filenames,
                now=datetime.datetime.now(),
                note=note,
                commit=commit,
                target_repo=target_repo,
                lock_timeout=_WEB_LOCK_TIMEOUT,
            )
        except SystemExit as error:
            raise common.WebInputError("指定したエントリを操作できません") from error

    def commit(self) -> bool:
        """外部編集差分をcommitしてpushする。"""
        return feedback_mutations.commit_feedback(self.private_notes, lock_timeout=_WEB_LOCK_TIMEOUT)


def create_app(
    private_notes: pathlib.Path,
    config: serve_config.ServeConfig,
    state: serve_state.ServeState,
    *,
    operations: Operations | None = None,
    worker_limit: int = 4,
) -> quart.Quart:
    """Quartアプリを生成する。"""
    del config
    app = quart.Quart(__name__)
    ops = operations or Operations(private_notes)
    workers = BoundedWorkers(worker_limit)

    @app.errorhandler(common.WebInputError)
    async def input_error(error: common.WebInputError) -> tuple[quart.Response, int]:
        return quart.jsonify(error=str(error)), 400

    @app.errorhandler(FileNotFoundError)
    async def not_found(error: FileNotFoundError) -> tuple[quart.Response, int]:
        return quart.jsonify(error=f"見つかりません: {error}"), 404

    @app.errorhandler(filelock.Timeout)
    async def lock_conflict(error: filelock.Timeout) -> tuple[quart.Response, int]:
        del error
        return quart.jsonify(error="別の操作が進行中です"), 409

    @app.errorhandler(subprocess.CalledProcessError)
    async def git_error(error: subprocess.CalledProcessError) -> tuple[quart.Response, int]:
        del error
        status = 503 if quart.request.method == "GET" else 500
        return quart.jsonify(error="Git同期に失敗しました"), status

    @app.get("/")
    async def index() -> quart.Response:
        return quart.Response(assets.HTML, content_type="text/html; charset=utf-8")

    @app.get("/static/app.css")
    async def css() -> quart.Response:
        return quart.Response(assets.CSS, content_type="text/css; charset=utf-8")

    @app.get("/static/app.js")
    async def javascript() -> quart.Response:
        return quart.Response(assets.JS, content_type="text/javascript; charset=utf-8")

    @app.get("/api/status")
    async def status() -> quart.Response:
        return quart.jsonify(enabled=await workers.run(ops.status))

    @app.get("/api/entries")
    async def entries() -> quart.Response:
        allowed = {"type", "status", "target_repo", "category", "source"}
        unknown = set(quart.request.args) - allowed
        if unknown:
            raise common.WebInputError(f"未知のqueryです: {', '.join(sorted(unknown))}")
        filters = dict(quart.request.args.items())
        if filters.get("type", "all") not in {"all", "feedback", "tbd"}:
            raise common.WebInputError("typeが不正です")
        if filters.get("status", "all") not in _STATUS_FILTERS:
            raise common.WebInputError("statusが不正です")
        for name in ("target_repo", "category", "source"):
            if name in filters and not filters[name].strip():
                raise common.WebInputError(f"{name}は空でない文字列で指定してください")
        return quart.jsonify(entries=await workers.run(ops.entries, filters))

    @app.get("/api/entries/<kind>/<state_name>/<filename>")
    async def detail(kind: str, state_name: str, filename: str) -> quart.Response:
        return quart.jsonify(entry=await workers.run(ops.detail, kind, state_name, filename))

    @app.put("/api/feedback/<state_name>/<filename>")
    async def edit_feedback(state_name: str, filename: str) -> quart.Response:
        data = _json_object(await _request_json(), allowed={"content"}, required={"content"})
        if not isinstance(data["content"], str) or not data["content"].strip():
            raise common.WebInputError("contentは空でない文字列で指定してください")
        return quart.jsonify(changed=await workers.run(ops.edit, "feedback", state_name, filename, data["content"]))

    @app.put("/api/tbd/<filename>")
    async def edit_tbd(filename: str) -> quart.Response:
        data = _json_object(await _request_json(), allowed={"content"}, required={"content"})
        if not isinstance(data["content"], str) or not data["content"].strip():
            raise common.WebInputError("contentは空でない文字列で指定してください")
        return quart.jsonify(changed=await workers.run(ops.edit, "tbd", "inbox", filename, data["content"]))

    @app.post("/api/feedback")
    async def add_feedback() -> tuple[quart.Response, int]:
        data = _json_object(
            await _request_json(),
            allowed={"messages", "source", "target_repo"},
            required={"messages", "target_repo"},
        )
        messages = _strings(data["messages"], "messages")
        for key in ("source", "target_repo"):
            if key in data and (not isinstance(data[key], str) or not data[key]):
                raise common.WebInputError(f"{key}は空でない文字列で指定してください")
        filenames = await workers.run(ops.add_feedback, messages, data.get("source"), data.get("target_repo"))
        return quart.jsonify(filenames=filenames), 201

    @app.post("/api/tbd")
    async def add_tbd() -> tuple[quart.Response, int]:
        data = _json_object(
            await _request_json(),
            allowed={"messages", "target_repo", "source", "scope", "question_type", "choices"},
            required={"messages", "scope", "question_type", "target_repo"},
        )
        messages = _strings(data["messages"], "messages")
        if not isinstance(data["scope"], str) or not data["scope"]:
            raise common.WebInputError("scopeは空でない文字列で指定してください")
        if not isinstance(data["question_type"], str):
            raise common.WebInputError("question_typeは文字列で指定してください")
        choices = _strings(data["choices"], "choices") if "choices" in data else None
        if not isinstance(data["target_repo"], str) or not data["target_repo"]:
            raise common.WebInputError("target_repoは空でない文字列で指定してください")
        target_repo = data["target_repo"]
        source = _optional_string(data, "source")
        filenames = await workers.run(
            ops.add_tbd,
            messages,
            scope=data["scope"],
            question_type=data["question_type"],
            choices=choices,
            target_repo=target_repo,
            source=source,
        )
        return quart.jsonify(filenames=filenames), 201

    @app.post("/api/tbd/<filename>/answer")
    async def answer_tbd(filename: str) -> quart.Response:
        data = _json_object(await _request_json(), allowed={"answer"}, required={"answer"})
        if not isinstance(data["answer"], str) or not data["answer"].strip():
            raise common.WebInputError("answerは空でない文字列で指定してください")
        return quart.jsonify(changed=await workers.run(ops.answer_tbd, filename, data["answer"]))

    async def transition(kind: str, action: str, allowed: set[str]) -> quart.Response:
        # target_repoは`_verify_frontmatter_target_repo`の任意検証にのみ使われ、常駐プロセスの
        # カレントディレクトリには依存しない（filenameで対象を一意に特定できるため）。
        # CWD依存回避のため必須化するのは新規追加系（add_feedback/add_tbd）のみでよい。
        data = _json_object(await _request_json(), allowed=allowed, required={"filenames"})
        filenames = _strings(data["filenames"], "filenames")
        optional = {
            name: _optional_string(data, name) for name in ("note", "commit", "category", "target_repo") if name in allowed
        }
        result = await workers.run(
            ops.transition,
            kind,
            action,
            filenames,
            note=optional.get("note"),
            commit=optional.get("commit"),
            category=optional.get("category"),
            target_repo=optional.get("target_repo"),
        )
        return quart.jsonify(filenames=result)

    @app.post("/api/feedback/start-processing")
    async def start_processing() -> quart.Response:
        return await transition("feedback", "start-processing", {"filenames", "target_repo"})

    @app.post("/api/feedback/adopt")
    async def adopt_feedback() -> quart.Response:
        return await transition(
            "feedback",
            "adopt",
            {"filenames", "note", "commit", "category", "target_repo"},
        )

    @app.post("/api/feedback/reject")
    async def reject_feedback() -> quart.Response:
        return await transition("feedback", "reject", {"filenames", "note", "commit", "target_repo"})

    @app.post("/api/feedback/remove")
    async def remove_feedback() -> quart.Response:
        return await transition("feedback", "remove", {"filenames", "target_repo"})

    @app.post("/api/tbd/adopt")
    async def adopt_tbd() -> quart.Response:
        return await transition("tbd", "adopt", {"filenames", "note", "commit", "target_repo"})

    @app.post("/api/tbd/remove")
    async def remove_tbd() -> quart.Response:
        return await transition("tbd", "remove", {"filenames", "note", "target_repo"})

    @app.post("/api/feedback/commit")
    async def commit_feedback() -> quart.Response:
        _json_object(await _request_json(), allowed=set())
        return quart.jsonify(changed=await workers.run(ops.commit))

    @app.post("/api/enable")
    async def enable() -> quart.Response:
        _json_object(await _request_json(), allowed=set())
        return quart.jsonify(enabled=await workers.run(ops.set_enabled, True))

    @app.post("/api/disable")
    async def disable() -> quart.Response:
        _json_object(await _request_json(), allowed=set())
        return quart.jsonify(enabled=await workers.run(ops.set_enabled, False))

    @app.get("/api/events")
    async def events() -> quart.Response:
        return quart.Response(state.events(), content_type="text/event-stream")

    return app
