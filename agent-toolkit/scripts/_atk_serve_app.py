"""`atk serve`のQuartアプリケーション。"""

import asyncio
import datetime
import html
import json
import pathlib
import re
import subprocess
import typing

import _atk_mq_add as feedback_add
import _atk_mq_common as common
import _atk_mq_frontmatter as frontmatter
import _atk_mq_mutations as feedback_mutations
import _atk_mq_repo as feedback_repo
import _atk_mq_tbd as tbd_mutations
import _atk_serve_assets as assets
import _atk_serve_config as serve_config
import _atk_serve_state as serve_state
import filelock
import pytilpack.quart
import quart
import werkzeug.exceptions

type JsonObject = dict[str, typing.Any]
_ENTRY_STATES = set(common.MQ_STATES)
_STATUS_FILTERS = {"all", "active", *common.MQ_STATES}
_ANSWERED_FILTERS = {"all", "yes", "no"}
_WEB_LOCK_TIMEOUT = 2.0
_EDIT_CONFLICT_MESSAGE = "編集中に他プロセスが対象を変更しました"

# pylint: disable=duplicate-code  # 配布物独立性を保つため同等機能を独立実装する。

# 安全なbase_pathの照合パターン。先頭スラッシュ必須、英数字と`._~/-`のみ許可し、
# 連続スラッシュ（スキーム相対URL扱いになり外部オリジン誘導の口になる）は別途禁止する。
# 配布物独立性の制約（agent-toolkit配下は他ディレクトリの実装を参照しない）により、
# 同種の検証を行う実装は本ファイル内で完結させる。
_BASE_PATH_ALLOWED_RE = re.compile(r"^/[A-Za-z0-9._~-][A-Za-z0-9._~/-]*$")


def _safe_base_path(raw: str) -> str:
    """`request.root_path`を信頼境界として正規化する。

    不正値・空値は空文字列として返す。呼び出し元はそのままURL前置として扱える。
    """
    if not raw:
        return ""
    candidate = raw.rstrip("/")
    if not candidate:
        return ""
    if "//" in candidate:
        return ""
    if not _BASE_PATH_ALLOWED_RE.fullmatch(candidate):
        return ""
    return candidate


def _resolve_states(status: str) -> tuple[str, ...]:
    """`status` queryの指定値を走査対象の状態フォルダ列へ変換する。"""
    if status == "active":
        return common.MQ_ACTIVE_STATES
    if status == "all":
        return common.MQ_STATES
    return (status,)


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


def _specified_string(data: JsonObject, name: str) -> str | None:
    if name not in data:
        return None
    value = data[name]
    if not isinstance(value, str) or not value.strip():
        raise common.WebInputError(f"{name}は空でない文字列で指定してください")
    return value


def _summary(text: str, kind: str) -> str:
    body = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
    lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("## ")]
    if kind == "tbd":
        lines = [line for line in lines if not line.startswith("<!--")]
    return lines[0][:160] if lines else ""


def _entry(path: pathlib.Path, kind: str, state: str, text: str) -> dict[str, object]:
    parsed = frontmatter.parse_frontmatter(text)
    metadata = parsed[0] if parsed is not None else {}
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

    def entries(self, filters: dict[str, str]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        kind_filter = filters.get("type", "all")
        status_filter = filters.get("status", "all")
        answered_filter = filters.get("answered", "all")
        states = _resolve_states(status_filter)
        for state in states:
            try:
                paths = sorted((self.private_notes / state).iterdir())
            except FileNotFoundError:
                continue
            for path in paths:
                if path.suffix != ".md":
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                    kind = common.entry_type_of(path, text)
                    if kind_filter not in ("all", kind):
                        continue
                    item = _entry(path, kind or "unknown", state, text)
                except FileNotFoundError:
                    continue
                if answered_filter == "yes" and item["answered"] is not True:
                    continue
                if answered_filter == "no" and item["answered"] is not False:
                    continue
                if any(filters.get(key) and item[key] != filters[key] for key in ("target_repo", "source", "category")):
                    continue
                result.append(item)
        return result

    def detail(self, state: str, filename: str) -> dict[str, object]:
        if state not in _ENTRY_STATES:
            raise common.WebInputError("stateが不正です")
        path = common.validate_filename(filename, self.private_notes / state)
        try:
            text = path.read_text(encoding="utf-8")
            kind = common.entry_type_of(path, text)
            return {**_entry(path, kind or "unknown", state, text), "content": text}
        except FileNotFoundError as error:
            raise FileNotFoundError(filename) from error

    def sync(self) -> bool:
        """リポジトリを明示的に同期する。"""
        with common.repo_lock(self.private_notes, timeout=_WEB_LOCK_TIMEOUT):
            common.pull(self.private_notes)
        return True

    def edit(
        self,
        state: str,
        filename: str,
        content: str,
        expected_content: str | None = None,
    ) -> bool:
        try:
            return feedback_mutations.edit_entry_content(
                self.private_notes,
                state=state,
                filename=filename,
                content=content,
                lock_timeout=_WEB_LOCK_TIMEOUT,
                expected_content=expected_content,
            )
        except SystemExit as error:
            raise common.WebInputError("指定したエントリを操作できません") from error

    def add(
        self,
        messages: list[str],
        *,
        entry_type: str,
        target_repo: str | None,
        source: str | None,
        scope: str | None = None,
        question_type: str | None = None,
        choices: list[str] | None = None,
    ) -> list[str]:
        """エントリを原子的に追加する。"""
        if entry_type not in common.MQ_TYPES:
            raise common.WebInputError("typeが不正です")
        if entry_type == common.MQ_TYPE_FEEDBACK and (scope or question_type or choices):
            raise common.WebInputError("scope・question_type・choicesはtype=tbdでのみ指定できます")
        if entry_type == common.MQ_TYPE_TBD:
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
        return feedback_add.add_entries(
            self.private_notes,
            messages=messages,
            target_repo=resolved_target_repo,
            source=source,
            now=datetime.datetime.now(),
            entry_type=entry_type,
            scope=scope,
            question_type=question_type,
            choices=",".join(choices) if choices else None,
            lock_timeout=_WEB_LOCK_TIMEOUT,
        )

    def answer_tbd(
        self,
        filename: str,
        answer: str,
        expected_content: str | None = None,
    ) -> bool:
        """TBD回答欄を置換する。"""
        return tbd_mutations.answer_tbd(
            self.private_notes,
            filename=filename,
            answer=answer,
            lock_timeout=_WEB_LOCK_TIMEOUT,
            expected_content=expected_content,
        )

    def transition(
        self,
        action: str,
        filenames: list[str],
        *,
        note: str | None = None,
        commit: str | None = None,
        category: str | None = None,
        target_repo: str | None = None,
        force: bool = False,
    ) -> list[str]:
        """複数エントリを全件検証後に移動又は削除する。

        `force`は`action="remove"`の場合のみ意味を持ち、
        processing状態のファイルへの既定保護（`atk mq rm`の`--force`と同義）を解除する。
        """
        try:
            return feedback_mutations.transition_entries(
                self.private_notes,
                action=action,
                filenames=filenames,
                now=datetime.datetime.now(),
                note=note,
                commit=commit,
                target_repo=target_repo,
                category=category,
                lock_timeout=_WEB_LOCK_TIMEOUT,
                force=force,
            )
        except SystemExit as error:
            raise common.WebInputError("指定したエントリを操作できません") from error

    def commit(self) -> bool:
        """外部編集差分をcommitしてpushする。"""
        return feedback_mutations.commit_entries(self.private_notes, lock_timeout=_WEB_LOCK_TIMEOUT)


def create_app(
    private_notes: pathlib.Path,
    config: serve_config.ServeConfig,
    state: serve_state.ServeState,
    *,
    operations: Operations | None = None,
    worker_limit: int = 4,
) -> quart.Quart:
    """Quartアプリを生成する。"""
    app = quart.Quart(__name__)
    # app.configへ格納してモジュールレベルの可変状態を避ける。
    # ハンドラからは`quart.current_app.config`経由で参照する。
    app.config["SERVE_CONFIG"] = config
    app.config["SERVE_STATE"] = state
    ops = operations or Operations(private_notes)
    workers = BoundedWorkers(worker_limit)
    sync_task: asyncio.Task[bool] | None = None

    async def synchronize() -> bool:
        """同時に届いた同期要求へ同じ実行結果を返す。"""
        nonlocal sync_task
        if sync_task is None or sync_task.done():
            sync_task = asyncio.create_task(workers.run(ops.sync))
        current = sync_task
        try:
            return await asyncio.shield(current)
        finally:
            if current.done() and sync_task is current:
                sync_task = None

    @app.errorhandler(common.WebInputError)
    async def input_error(error: common.WebInputError) -> tuple[quart.Response, int]:
        return quart.jsonify(error=str(error)), 400

    @app.errorhandler(FileNotFoundError)
    async def not_found(error: FileNotFoundError) -> tuple[quart.Response, int]:
        return quart.jsonify(error=f"見つかりません: {error}"), 404

    @app.errorhandler(filelock.Timeout)
    async def lock_conflict(error: filelock.Timeout) -> tuple[quart.Response, int]:
        del error
        return quart.jsonify(error="別の操作が進行中です", code="lock_conflict"), 409

    @app.errorhandler(RuntimeError)
    async def edit_conflict(error: RuntimeError) -> tuple[quart.Response, int]:
        if str(error) != _EDIT_CONFLICT_MESSAGE:
            raise error
        return quart.jsonify(error=str(error), code="edit_conflict"), 409

    @app.errorhandler(subprocess.CalledProcessError)
    async def git_error(error: subprocess.CalledProcessError) -> tuple[quart.Response, int]:
        del error
        status = 503 if quart.request.method == "GET" else 500
        return quart.jsonify(error="Git同期に失敗しました"), status

    @app.get("/")
    async def index() -> quart.Response:
        base_path = _safe_base_path(quart.request.root_path)
        body = assets.HTML.replace("__BASE_PATH_HTML__", html.escape(base_path, quote=True))
        return quart.Response(body, content_type="text/html; charset=utf-8")

    @app.get("/static/app.css")
    async def css() -> quart.Response:
        return quart.Response(assets.CSS, content_type="text/css; charset=utf-8")

    @app.get("/static/app.js")
    async def javascript() -> quart.Response:
        base_path = _safe_base_path(quart.request.root_path)
        body = assets.JS.replace("__BASE_PATH_JS__", json.dumps(base_path))
        return quart.Response(body, content_type="text/javascript; charset=utf-8")

    @app.get("/manifest.webmanifest")
    async def manifest() -> quart.Response:
        base_path = _safe_base_path(quart.request.root_path)
        root_url = f"{base_path}/"
        body = {
            "name": "フィードバック管理",
            "short_name": "atk serve",
            "start_url": root_url,
            "scope": root_url,
            "display": "standalone",
            "theme_color": assets.THEME_COLOR,
            "background_color": assets.THEME_COLOR,
            "icons": [
                {
                    "src": f"{base_path}/static/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                },
                {
                    "src": f"{base_path}/static/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                },
            ],
        }
        response = quart.Response(
            json.dumps(body, ensure_ascii=False),
            content_type="application/manifest+json",
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/static/icon-192.png")
    async def icon_192() -> quart.Response:
        response = quart.Response(assets.ICON_192_PNG, content_type="image/png")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/static/icon-512.png")
    async def icon_512() -> quart.Response:
        response = quart.Response(assets.ICON_512_PNG, content_type="image/png")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.post("/api/sync")
    async def sync() -> quart.Response:
        return quart.jsonify(synced=await synchronize())

    @app.get("/api/entries")
    async def entries() -> quart.Response:
        allowed = {"type", "status", "answered", "target_repo", "category", "source"}
        unknown = set(quart.request.args) - allowed
        if unknown:
            raise common.WebInputError(f"未知のqueryです: {', '.join(sorted(unknown))}")
        filters = dict(quart.request.args.items())
        if filters.get("type", "all") not in {"all", "feedback", "tbd"}:
            raise common.WebInputError("typeが不正です")
        if filters.get("status", "all") not in _STATUS_FILTERS:
            raise common.WebInputError("statusが不正です")
        if filters.get("answered", "all") not in _ANSWERED_FILTERS:
            raise common.WebInputError("answeredが不正です")
        for name in ("target_repo", "category", "source"):
            if name in filters and not filters[name].strip():
                raise common.WebInputError(f"{name}は空でない文字列で指定してください")
        return quart.jsonify(entries=await workers.run(ops.entries, filters))

    @app.get("/api/entries/<state_name>/<filename>")
    async def detail(state_name: str, filename: str) -> quart.Response:
        return quart.jsonify(entry=await workers.run(ops.detail, state_name, filename))

    @app.put("/api/entries/<state_name>/<filename>")
    async def edit_entry(state_name: str, filename: str) -> quart.Response:
        data = _json_object(
            await _request_json(),
            allowed={"content", "expected_content"},
            required={"content"},
        )
        if not isinstance(data["content"], str) or not data["content"].strip():
            raise common.WebInputError("contentは空でない文字列で指定してください")
        expected_content = _specified_string(data, "expected_content")
        return quart.jsonify(
            changed=await workers.run(
                ops.edit,
                state_name,
                filename,
                data["content"],
                expected_content,
            )
        )

    @app.post("/api/entries")
    async def add_entry() -> tuple[quart.Response, int]:
        data = _json_object(
            await _request_json(),
            allowed={"type", "messages", "source", "target_repo", "scope", "question_type", "choices"},
            required={"type", "messages", "target_repo"},
        )
        if data["type"] not in common.MQ_TYPES:
            raise common.WebInputError("typeが不正です")
        messages = _strings(data["messages"], "messages")
        for key in ("source", "target_repo"):
            if key in data and (not isinstance(data[key], str) or not data[key]):
                raise common.WebInputError(f"{key}は空でない文字列で指定してください")
        # question_typeの既定値補完はTBDのみに適用する。feedbackへ補完すると
        # `ops.add`のTBD専用オプション併用検査が誤って発火するため。
        question_type = data.get("question_type")
        if data["type"] == common.MQ_TYPE_TBD and question_type is None:
            question_type = "free-form"
        filenames = await workers.run(
            ops.add,
            messages,
            entry_type=data["type"],
            target_repo=data.get("target_repo"),
            source=_optional_string(data, "source"),
            scope=data.get("scope"),
            question_type=question_type,
            choices=_strings(data["choices"], "choices") if "choices" in data else None,
        )
        return quart.jsonify(filenames=filenames), 201

    @app.post("/api/entries/answer")
    async def answer_tbd() -> quart.Response:
        data = _json_object(
            await _request_json(),
            allowed={"filename", "answer", "expected_content"},
            required={"filename", "answer"},
        )
        if not isinstance(data["answer"], str) or not data["answer"].strip():
            raise common.WebInputError("answerは空でない文字列で指定してください")
        if not isinstance(data["filename"], str):
            raise common.WebInputError("filenameは文字列で指定してください")
        expected_content = _specified_string(data, "expected_content")
        return quart.jsonify(
            changed=await workers.run(
                ops.answer_tbd,
                data["filename"],
                data["answer"],
                expected_content,
            )
        )

    async def transition(action: str, allowed: set[str]) -> quart.Response:
        # target_repoは`_verify_frontmatter_target_repo`の任意検証にのみ使われ、常駐プロセスの
        # カレントディレクトリには依存しない（filenameで対象を一意に特定できるため）。
        # CWD依存回避のため必須化するのは新規追加系（add）のみでよい。
        data = _json_object(await _request_json(), allowed=allowed, required={"filenames"})
        filenames = _strings(data["filenames"], "filenames")
        optional = {
            name: _optional_string(data, name) for name in ("note", "commit", "category", "target_repo") if name in allowed
        }
        force = False
        if "force" in allowed and "force" in data:
            if not isinstance(data["force"], bool):
                raise common.WebInputError("forceはbooleanで指定してください")
            force = data["force"]
        result = await workers.run(
            ops.transition,
            action,
            filenames,
            note=optional.get("note"),
            commit=optional.get("commit"),
            category=optional.get("category"),
            target_repo=optional.get("target_repo"),
            force=force,
        )
        return quart.jsonify(filenames=result)

    @app.post("/api/entries/start-processing")
    async def start_processing() -> quart.Response:
        return await transition("start-processing", {"filenames", "target_repo"})

    @app.post("/api/entries/adopt")
    async def adopt_feedback() -> quart.Response:
        return await transition(
            "adopt",
            {"filenames", "note", "commit", "category", "target_repo"},
        )

    @app.post("/api/entries/reject")
    async def reject_feedback() -> quart.Response:
        return await transition("reject", {"filenames", "note", "commit", "target_repo"})

    @app.post("/api/entries/remove")
    async def remove_feedback() -> quart.Response:
        return await transition("remove", {"filenames", "note", "target_repo", "force"})

    @app.post("/api/entries/commit")
    async def commit_entries() -> quart.Response:
        _json_object(await _request_json(), allowed=set())
        return quart.jsonify(changed=await workers.run(ops.commit))

    @app.get("/api/events")
    async def events() -> quart.Response:
        current_state: serve_state.ServeState = quart.current_app.config["SERVE_STATE"]
        return quart.Response(current_state.events(), content_type="text/event-stream")

    # X-Forwarded-Prefix/ProtoをASGI scopeへ反映するミドルウェアを介在させる。
    # `app.asgi_app`（バウンドメソッド）を入れ替えるQuartの公式パターンにより、
    # `app.config`等のハンドラ参照は維持しつつASGIディスパッチだけを上流に通す。
    # 前提: リバースプロキシ前段が`X-Forwarded-Prefix`を保持して転送する構成
    # （`/etc/apache2/sites-enabled/mysettings-443.conf`の`/atk/`設定と同様、
    # prefixを除去しない構成）であること。プレフィクスを除去する構成では
    # Quartが`scope.root_path`をパス冒頭から除去する前提が崩れ404になる。
    # method-assignとASGIプロトコル不一致は意図的なため型チェッカーは抑制する。
    app.asgi_app = pytilpack.quart.ProxyFix(app)  # type: ignore[method-assign,assignment]  # ty: ignore[invalid-assignment]
    return app
