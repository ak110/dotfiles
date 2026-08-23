"""`atk serve`のQuartアプリケーション。"""

import asyncio
import base64
import collections.abc
import contextlib
import datetime
import functools
import html
import json
import math
import pathlib
import re
import subprocess
import typing

import _atk_mq_add as feedback_add
import _atk_mq_batch as feedback_batch
import _atk_mq_common as common
import _atk_mq_frontmatter as frontmatter
import _atk_mq_mutations as feedback_mutations
import _atk_mq_repo as feedback_repo
import _atk_mq_tbd as tbd_mutations
import _atk_serve_assets as assets
import _atk_serve_config as serve_config
import _atk_serve_state as serve_state
import _git_remote
import filelock
import markdown_it
import pytilpack.quart
import quart
import werkzeug.exceptions

type JsonObject = dict[str, typing.Any]
_ENTRY_STATES = set(common.MQ_STATES)
_STATUS_FILTERS = {"all", "active", *common.MQ_STATES}
_ANSWERED_FILTERS = {"all", "yes", "no"}
_WEB_LOCK_TIMEOUT = 2.0
_BACKGROUND_SYNC_INTERVAL_SECONDS = 60.0
"""定期バックグラウンド更新の間隔。

`atk mq process-loop`が10分間隔で更新する先例に対し、
Web UIは利用者が画面を閲覧する前提のため短く取る。
"""
_EDIT_CONFLICT_MESSAGE = "編集中に他プロセスが対象を変更しました"
_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})

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


def _specified_text(data: JsonObject, name: str) -> str | None:
    """指定済みの本文を空文字列も含めて受理する。"""
    if name not in data:
        return None
    value = data[name]
    if not isinstance(value, str):
        raise common.WebInputError(f"{name}は文字列で指定してください")
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
    answered = common.is_tbd_answered(text) if kind == "tbd" else None
    return {
        "kind": kind,
        "state": state,
        "filename": path.name,
        "answered": answered,
        "target_repo": _json_compatible(metadata.get("target_repo")),
        "source": _json_compatible(metadata.get("source")),
        "summary": _summary(text, kind),
        "updated_at": datetime.datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=datetime.UTC,
        ).isoformat(),
    }


def _json_sort_key(value: typing.Any) -> str:
    """正規化済み値をJSON表現の辞書順で並べるためのキーを返す。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_compatible(value: typing.Any, active_ids: set[int] | None = None) -> typing.Any:
    """YAML値を厳格なJSONが受理する表示用の値へ再帰的に変換する。"""
    active = set() if active_ids is None else active_ids
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, collections.abc.Mapping):
        value_id = id(value)
        if value_id in active:
            return "[Circular]"
        active.add(value_id)
        try:
            if all(isinstance(key, str) for key in value):
                return {key: _json_compatible(item, active) for key, item in value.items()}
            return {
                "__mapping__": [
                    {
                        "key": {
                            "type": type(key).__name__,
                            "value": _json_compatible(key, active),
                        },
                        "value": _json_compatible(item, active),
                    }
                    for key, item in value.items()
                ]
            }
        finally:
            active.remove(value_id)
    if isinstance(value, (list, tuple)):
        value_id = id(value)
        if value_id in active:
            return "[Circular]"
        active.add(value_id)
        try:
            return [_json_compatible(item, active) for item in value]
        finally:
            active.remove(value_id)
    if isinstance(value, (set, frozenset)):
        value_id = id(value)
        if value_id in active:
            return "[Circular]"
        active.add(value_id)
        try:
            normalized = [_json_compatible(item, active) for item in value]
        finally:
            active.remove(value_id)
        return sorted(normalized, key=_json_sort_key)
    return str(value)


def _json_compatible_mapping_entries(
    value: collections.abc.Mapping[typing.Any, typing.Any],
) -> list[dict[str, typing.Any]]:
    """マッピングの挿入順とキー型を保持したJSON互換のentry列へ変換する。"""
    active = {id(value)}
    try:
        return [
            {
                "key": {
                    "type": type(key).__name__,
                    "value": _json_compatible(key, active),
                },
                "value": _json_compatible(item, active),
            }
            for key, item in value.items()
        ]
    finally:
        active.remove(id(value))


def _render_frontmatter_table(metadata: dict[str, typing.Any]) -> str:
    """解析済みfrontmatterをキーと値の2列表のHTMLへ整形する。

    Markdownとして整形すると区切り行が水平線と見出しへ解釈され、インデントも失われるため、
    本文と分離して表として描画する。解析は既存の`parse_frontmatter()`へ一元化する。
    """
    if not metadata:
        return ""
    rows: list[str] = []
    for key, value in metadata.items():
        normalized = _json_compatible(value)
        if isinstance(normalized, (dict, list)):
            formatted = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False)
            cell = f"<pre>{html.escape(formatted)}</pre>"
        else:
            cell = html.escape("" if normalized is None else str(normalized))
        rows.append(f"<tr><th>{html.escape(str(key))}</th><td>{cell}</td></tr>")
    return '<table class="frontmatter">' + "".join(rows) + "</table>"


@functools.lru_cache(maxsize=128)
def _render_content(text: str) -> str:
    """frontmatterを表として、本文をMarkdownとして整形したHTMLを保持する。

    整形結果は本文だけで一意に定まるため、更新時刻はキーに含めない。
    含めると同一本文の再保存でヒットしなくなるだけで、無効化には寄与しない。
    """
    parsed = frontmatter.parse_frontmatter(text)
    if parsed is None:
        return _MARKDOWN.render(text)
    metadata, body = parsed
    table = _render_frontmatter_table(metadata)
    return table + _MARKDOWN.render(body)


@functools.lru_cache(maxsize=128)
def _render_body(text: str) -> str:
    """frontmatterを除いた本文だけをMarkdownとして整形する。"""
    parsed = frontmatter.parse_frontmatter(text)
    return _MARKDOWN.render(text if parsed is None else parsed[1])


def _question_metadata(metadata: dict[str, typing.Any], kind: str) -> tuple[str, list[str]]:
    """TBDの回答形式と選択肢をWeb UI用の安定した形へ正規化する。"""
    if kind != common.MQ_TYPE_TBD:
        return "free-form", []
    raw_type = metadata.get("question_type")
    question_type = raw_type if isinstance(raw_type, str) and raw_type in {"choice", "yes-no", "free-form"} else "free-form"
    if question_type != "choice":
        return question_type, []
    raw_choices = metadata.get("choices")
    if isinstance(raw_choices, str):
        return question_type, [choice.strip() for choice in raw_choices.split(",") if choice.strip()]
    if isinstance(raw_choices, list) and all(isinstance(choice, str) and choice.strip() for choice in raw_choices):
        return question_type, [choice.strip() for choice in raw_choices]
    return question_type, []


def _tbd_answer(text: str, kind: str) -> str | None:
    """TBD回答欄の既存回答を編集用文字列として返す。"""
    if kind != common.MQ_TYPE_TBD or tbd_mutations.ANSWER_MARKER not in text:
        return None
    return text.rsplit(tbd_mutations.ANSWER_MARKER, maxsplit=1)[1].strip() or None


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

    def _iter_entry_files(
        self,
        states: typing.Iterable[str],
        warnings: list[dict[str, str]] | None = None,
    ) -> typing.Iterator[tuple[str, pathlib.Path, str]]:
        """指定状態のエントリを`(状態名, パス, 本文)`として順に返す。

        一覧表示と対象リポジトリの候補収集で同じ走査条件を用いるための共通経路とする。
        読み取れないファイルは除外し、一覧APIの走査では警告へ記録する。
        """
        for state in states:
            try:
                paths = sorted((self.private_notes / state).iterdir())
            except FileNotFoundError:
                continue
            for path in paths:
                if path.suffix != ".md":
                    continue
                try:
                    yield state, path, path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    if warnings is not None:
                        warnings.append({"filename": path.name, "reason": "UTF-8として読み取れません"})
                except OSError:
                    if warnings is not None:
                        warnings.append({"filename": path.name, "reason": "ファイルを読み取れません"})
                    continue

    def _entries(self, filters: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
        """条件に一致する一覧と、走査中に発生した読取り警告を返す。

        未回答TBD、その他のTBD、フィードバック、種別不明の順に分け、
        各群ではファイル名の降順とする。
        """
        result: list[dict[str, object]] = []
        warnings: list[dict[str, str]] = []
        kind_filter = filters.get("type", "all")
        status_filter = filters.get("status", "all")
        answered_filter = filters.get("answered", "all")
        target_repo_filter = filters.get("target_repo")
        query = filters.get("q", "").casefold()
        states = _resolve_states(status_filter)
        resolver_cache: dict[str, str | None] = {}
        canonical_target_repo = (
            _git_remote.canonical_repo(target_repo_filter, resolver_cache) if target_repo_filter is not None else None
        )
        for state, path, text in self._iter_entry_files(states, warnings):
            try:
                kind = common.entry_type_of(path, text)
                if kind_filter not in ("all", kind):
                    continue
                item = _entry(path, kind or "unknown", state, text)
            except FileNotFoundError:
                continue
            except OSError:
                warnings.append({"filename": path.name, "reason": "ファイル情報を読み取れません"})
                continue
            if answered_filter == "yes" and item["answered"] is not True:
                continue
            if answered_filter == "no" and item["answered"] is not False:
                continue
            if filters.get("source_empty") == "true":
                source = item["source"]
                if not (source is None or isinstance(source, str) and not source.strip()):
                    continue
            if target_repo_filter is not None:
                item_target_repo = item["target_repo"]
                if not isinstance(item_target_repo, str):
                    continue
                # 正規化は旧パス形とURL形の統合にだけ用い、解決不能な保存値は原値で照合する。
                if canonical_target_repo is None:
                    if item_target_repo != target_repo_filter:
                        continue
                elif _git_remote.canonical_repo(item_target_repo, resolver_cache) != canonical_target_repo:
                    continue
            if filters.get("source") and item["source"] != filters["source"]:
                continue
            searchable = (text, path.name, item["target_repo"], item["source"])
            if query and not any(query in str(value or "").casefold() for value in searchable):
                continue
            result.append(item)
        unanswered_tbd_items = sorted(
            [item for item in result if item["kind"] == "tbd" and item["answered"] is False],
            key=lambda item: str(item["filename"]),
            reverse=True,
        )
        other_tbd_items = sorted(
            [item for item in result if item["kind"] == "tbd" and item["answered"] is not False],
            key=lambda item: str(item["filename"]),
            reverse=True,
        )
        feedback_items = sorted(
            [item for item in result if item["kind"] == "feedback"], key=lambda item: str(item["filename"]), reverse=True
        )
        other_items = sorted(
            [item for item in result if item["kind"] not in ("tbd", "feedback")],
            key=lambda item: str(item["filename"]),
            reverse=True,
        )
        return unanswered_tbd_items + other_tbd_items + feedback_items + other_items, warnings

    def entries_with_warnings(
        self,
        filters: dict[str, str],
    ) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
        """一覧API向けにエントリと読取り警告を返す。"""
        return self._entries(filters)

    def detail(self, state: str, filename: str) -> dict[str, object]:
        if state not in _ENTRY_STATES:
            raise common.WebInputError("stateが不正です")
        path = common.validate_filename(filename, self.private_notes / state)
        try:
            text = path.read_text(encoding="utf-8")
            kind = common.entry_type_of(path, text)
            parsed = frontmatter.parse_frontmatter(text)
            metadata = parsed[0] if parsed is not None else {}
            question_type, choices = _question_metadata(metadata, kind or "unknown")
            detail_entry = _entry(path, kind or "unknown", state, text)
            return {
                **detail_entry,
                "content": text,
                "content_html": _render_content(text),
                "body_html": _render_body(text),
                "frontmatter_entries": (
                    _json_compatible_mapping_entries(metadata) if isinstance(metadata, collections.abc.Mapping) else []
                ),
                "question_type": question_type,
                "choices": choices,
                "answer": _tbd_answer(text, kind or "unknown"),
            }
        except FileNotFoundError as error:
            raise FileNotFoundError(filename) from error

    def sync(self) -> bool:
        """リポジトリを明示的に同期する。

        利用者の操作に対応する経路であるため、直近のpullからの経過時間によらず毎回実行する。
        """
        with common.repo_lock(self.private_notes, timeout=_WEB_LOCK_TIMEOUT):
            common.pull(self.private_notes)
        return True

    def background_sync(self) -> bool:
        """定期更新としてリポジトリを同期し、実際にpullしたかを返す。

        直近のpullから一定時間内であれば省略する。ロックを取得できない場合は
        当該周期を見送り、次周期で再試行する。
        """
        try:
            with common.repo_lock(self.private_notes, timeout=_WEB_LOCK_TIMEOUT):
                return common.pull_if_stale(self.private_notes)
        except filelock.Timeout:
            return False

    def target_repos(self, status: str = "active") -> list[str]:
        """指定状態のエントリに現れる対象リポジトリを昇順で返す。

        新規登録フォームの補完候補とフィルターの選択肢に用いる。
        既定値は一覧の状態フィルターの初期値と同じ`active`とする。
        `git pull`は行わず、ローカルの保存済みエントリだけを走査する。
        """
        found: set[str] = set()
        resolver_cache: dict[str, str | None] = {}
        for _state, _path, text in self._iter_entry_files(_resolve_states(status)):
            parsed = frontmatter.parse_frontmatter(text)
            if parsed is None:
                continue
            target_repo = parsed[0].get("target_repo")
            if isinstance(target_repo, str) and target_repo:
                canonical_target_repo = _git_remote.canonical_repo(target_repo, resolver_cache)
                # 正規化は同一リポジトリの候補統合にだけ用い、解決不能な保存値は原値を保持する。
                found.add(canonical_target_repo if canonical_target_repo is not None else target_repo)
        return sorted(found)

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
        """エントリを原子的に追加する。

        `target_repo`が`None`の場合は各メッセージのfrontmatterの`target_repo`を必須とし、
        検証は`add_entries`の共通経路へ委ねる。
        """
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
        resolved_target_repo: str | None = None
        if target_repo is not None:
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

    def add_batch(self, text: str) -> dict[str, object]:
        """`atk mq show --all`の出力形式のテキストからエントリを一括で取り込む。

        原文保持の契約はCLIと共通の`_atk_mq_batch.add_batch_entries`が担う。
        """
        mapping, warnings = feedback_batch.add_batch_entries(
            self.private_notes,
            texts=[text],
            now=datetime.datetime.now(),
            lock_timeout=_WEB_LOCK_TIMEOUT,
        )
        return {
            "filenames": [saved for _original, saved in mapping],
            "mapping": dict(mapping),
            "warnings": warnings,
        }

    def answer_tbd(
        self,
        filename: str,
        answer: str,
        expected_content: str | None = None,
        state: str | None = None,
    ) -> bool:
        """TBD回答欄を置換する。"""
        return tbd_mutations.answer_tbd(
            self.private_notes,
            filename=filename,
            answer=answer,
            state=state,
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
        target_repo: str | None = None,
        force: bool = False,
        state: str | None = None,
        expected_content: str | None = None,
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
                lock_timeout=_WEB_LOCK_TIMEOUT,
                force=force,
                state=state,
                expected_content=expected_content,
            )
        except SystemExit as error:
            raise common.WebInputError("指定したエントリを操作できません") from error

    def commit(self) -> bool:
        """外部編集差分をcommitしてpushする。"""
        return feedback_mutations.commit_entries(self.private_notes, lock_timeout=_WEB_LOCK_TIMEOUT)


class _ServeRuntime:
    """Webハンドラ間で共有する操作・ワーカー・同期タスクを保持する。"""

    def __init__(self, operations: Operations, workers: BoundedWorkers) -> None:
        self.operations = operations
        self.workers = workers
        self.sync_task: asyncio.Task[bool] | None = None
        self.background_task: asyncio.Task[None] | None = None

    async def synchronize(self) -> bool:
        """同時に届いた同期要求へ同じ実行結果を返す。"""
        if self.sync_task is None or self.sync_task.done():
            self.sync_task = asyncio.create_task(self.workers.run(self.operations.sync))
        current = self.sync_task
        try:
            return await asyncio.shield(current)
        finally:
            if current.done() and self.sync_task is current:
                self.sync_task = None

    async def background_sync_loop(self) -> None:
        """一定間隔でリポジトリを更新し、失敗時は次周期で再試行する。"""
        while True:
            await asyncio.sleep(_BACKGROUND_SYNC_INTERVAL_SECONDS)
            try:
                await self.workers.run(self.operations.background_sync)
            except Exception:  # pylint: disable=broad-exception-caught
                continue


def _register_error_handlers(app: quart.Quart) -> None:
    """Web API共通の例外応答を登録する。"""

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


def _register_asset_routes(app: quart.Quart) -> None:
    """HTML・静的資産・PWAメタデータのルートを登録する。"""

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
        return quart.Response(
            assets.JS.replace("__BASE_PATH_JS__", json.dumps(base_path)),
            content_type="text/javascript; charset=utf-8",
        )

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
                    "src": f"{base_path}/favicon.svg",
                    "sizes": "192x192 512x512 any",
                    "type": "image/svg+xml",
                    "purpose": "any",
                }
            ],
        }
        response = quart.Response(json.dumps(body, ensure_ascii=False), content_type="application/manifest+json")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/favicon.svg")
    async def favicon_svg() -> quart.Response:
        return quart.Response(
            assets.FAVICON_SVG,
            content_type="image/svg+xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    def png_response(content: bytes) -> quart.Response:
        response = quart.Response(content, content_type="image/png")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/static/icon-192.png")
    async def icon_192() -> quart.Response:
        return png_response(assets.ICON_192_PNG)

    @app.get("/static/icon-512.png")
    async def icon_512() -> quart.Response:
        return png_response(assets.ICON_512_PNG)


def _register_lifecycle(app: quart.Quart, runtime: _ServeRuntime) -> None:
    """バックグラウンド同期の開始・終了処理を登録する。"""

    @app.before_serving
    async def start_background_sync() -> None:
        runtime.background_task = asyncio.create_task(runtime.background_sync_loop())

    @app.after_serving
    async def stop_background_sync() -> None:
        if runtime.background_task is None:
            return
        runtime.background_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.background_task
        runtime.background_task = None


def _validate_entry_filters(filters: dict[str, str]) -> None:
    """一覧APIのquery組合せを検証する。"""
    if filters.get("type", "all") not in {"all", "feedback", "tbd"}:
        raise common.WebInputError("typeが不正です")
    if filters.get("status", "all") not in _STATUS_FILTERS:
        raise common.WebInputError("statusが不正です")
    if filters.get("answered", "all") not in _ANSWERED_FILTERS:
        raise common.WebInputError("answeredが不正です")
    if "source_empty" in filters and filters["source_empty"] != "true":
        raise common.WebInputError("source_emptyはtrueで指定してください")
    if "source" in filters and "source_empty" in filters:
        raise common.WebInputError("sourceとsource_emptyは同時に指定できません")
    for name in ("target_repo", "source", "q"):
        if name in filters and not filters[name].strip():
            raise common.WebInputError(f"{name}は空でない文字列で指定してください")


def _register_query_routes(app: quart.Quart, runtime: _ServeRuntime) -> None:
    """同期・一覧・詳細・イベント購読ルートを登録する。"""
    ops, workers = runtime.operations, runtime.workers

    @app.post("/api/sync")
    async def sync() -> quart.Response:
        return quart.jsonify(synced=await runtime.synchronize())

    @app.get("/api/repos")
    async def repos() -> quart.Response:
        unknown = set(quart.request.args) - {"status"}
        if unknown:
            raise common.WebInputError(f"未知のqueryです: {', '.join(sorted(unknown))}")
        status_filter = quart.request.args.get("status", "active")
        if status_filter not in _STATUS_FILTERS:
            raise common.WebInputError("statusが不正です")
        return quart.jsonify(repos=await workers.run(ops.target_repos, status_filter))

    @app.get("/api/entries")
    async def entries() -> quart.Response:
        allowed = {"type", "status", "answered", "target_repo", "source", "source_empty", "q"}
        unknown = set(quart.request.args) - allowed
        if unknown:
            raise common.WebInputError(f"未知のqueryです: {', '.join(sorted(unknown))}")
        filters = dict(quart.request.args.items())
        _validate_entry_filters(filters)
        result, warnings = await workers.run(ops.entries_with_warnings, filters)
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
        return quart.Response(current_state.events(), content_type="text/event-stream")


async def _transition_request(runtime: _ServeRuntime, action: str, allowed: set[str]) -> quart.Response:
    """状態遷移APIの共通payloadを解析して操作する。"""
    data = _json_object(await _request_json(), allowed=allowed, required={"filenames"})
    filenames = _strings(data["filenames"], "filenames")
    optional = {name: _optional_string(data, name) for name in ("note", "commit", "target_repo") if name in allowed}
    force = False
    if "force" in allowed and "force" in data:
        if not isinstance(data["force"], bool):
            raise common.WebInputError("forceはbooleanで指定してください")
        force = data["force"]
    state_name = _optional_string(data, "state") if "state" in allowed else None
    if state_name is not None and state_name not in common.MQ_ACTIVE_STATES:
        raise common.WebInputError("stateはinbox又はprocessingで指定してください")
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


def _register_mutation_routes(app: quart.Quart, runtime: _ServeRuntime) -> None:
    """編集・投入・回答・状態遷移ルートを登録する。"""
    ops, workers = runtime.operations, runtime.workers

    @app.put("/api/entries/<state_name>/<filename>")
    async def edit_entry(state_name: str, filename: str) -> quart.Response:
        data = _json_object(await _request_json(), allowed={"content", "expected_content"}, required={"content"})
        if not isinstance(data["content"], str) or not data["content"].strip():
            raise common.WebInputError("contentは空でない文字列で指定してください")
        expected_content = _specified_string(data, "expected_content")
        return quart.jsonify(changed=await workers.run(ops.edit, state_name, filename, data["content"], expected_content))

    @app.post("/api/entries")
    async def add_entry() -> tuple[quart.Response, int]:
        data = _json_object(
            await _request_json(),
            allowed={"type", "messages", "source", "target_repo", "scope", "question_type", "choices"},
            required={"type", "messages"},
        )
        if data["type"] not in common.MQ_TYPES:
            raise common.WebInputError("typeが不正です")
        messages = _strings(data["messages"], "messages")
        for key in ("source", "target_repo"):
            if key in data and (not isinstance(data[key], str) or not data[key]):
                raise common.WebInputError(f"{key}は空でない文字列で指定してください")
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

    @app.post("/api/entries/batch")
    async def add_batch() -> tuple[quart.Response, int]:
        data = _json_object(await _request_json(), allowed={"text"}, required={"text"})
        if not isinstance(data["text"], str) or not data["text"].strip():
            raise common.WebInputError("textは空でない文字列で指定してください")
        result = await workers.run(ops.add_batch, data["text"])
        return quart.jsonify(**result), 201

    @app.post("/api/entries/answer")
    async def answer_tbd() -> quart.Response:
        data = _json_object(
            await _request_json(),
            allowed={"filename", "state", "answer", "expected_content"},
            required={"filename", "answer"},
        )
        if not isinstance(data["answer"], str) or not data["answer"].strip():
            raise common.WebInputError("answerは空でない文字列で指定してください")
        if not isinstance(data["filename"], str):
            raise common.WebInputError("filenameは文字列で指定してください")
        expected_content = _specified_string(data, "expected_content")
        state_name = _optional_string(data, "state")
        if state_name is not None and state_name not in common.MQ_ACTIVE_STATES:
            raise common.WebInputError("stateはinbox又はprocessingで指定してください")
        if state_name is None:
            changed = await workers.run(ops.answer_tbd, data["filename"], data["answer"], expected_content)
        else:
            changed = await workers.run(
                ops.answer_tbd,
                data["filename"],
                data["answer"],
                expected_content,
                state_name,
            )
        return quart.jsonify(changed=changed)

    transition_specs = {
        "start-processing": {"filenames", "target_repo"},
        "adopt": {"filenames", "note", "commit", "target_repo"},
        "reject": {"filenames", "note", "commit", "target_repo"},
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
    app.config["SERVE_CONFIG"] = config
    app.config["SERVE_STATE"] = state
    runtime = _ServeRuntime(operations or Operations(private_notes), BoundedWorkers(worker_limit))
    _register_error_handlers(app)
    _register_asset_routes(app)
    _register_lifecycle(app, runtime)
    _register_query_routes(app, runtime)
    _register_mutation_routes(app, runtime)
    app.asgi_app = pytilpack.quart.ProxyFix(app)  # type: ignore[method-assign,assignment]  # ty: ignore[invalid-assignment]
    return app
