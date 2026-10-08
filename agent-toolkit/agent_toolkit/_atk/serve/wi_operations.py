"""WI画面の一覧・詳細・変更をprivate-notesの同期ファイル操作として提供する`Operations`と、その表示用の整形。"""

import base64
import collections.abc
import datetime
import functools
import html
import json
import math
import pathlib
import re
import typing

import filelock
import markdown_it
import mdit_py_plugins.footnote

from agent_toolkit._atk.serve import entry_index
from agent_toolkit._atk.wi import add as awi_add
from agent_toolkit._atk.wi import batch as awi_batch
from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import filters as wi_filters
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi import headings as wi_headings
from agent_toolkit._atk.wi import mutations as awi_mutations
from agent_toolkit._atk.wi import repo as awi_repo
from agent_toolkit._atk.wi import sync as wi_sync
from agent_toolkit._atk.wi import user_comment as user_comment_mutations
from agent_toolkit._atk.wi import uwi as uwi_mutations
from agent_toolkit._atk.wi import uwi_scan as wi_uwi_scan
from agent_toolkit._atk.wi import web_input as wi_web_input
from agent_toolkit._common.markdown_headings import normalize_newlines, top_level_atx_headings
from agent_toolkit._git import remote as _git_remote

type JsonObject = dict[str, typing.Any]

_ENTRY_STATES = set(wi_constants.WI_STATES)


PERIOD_WEEKS = {"2w": 2, "4w": 4, "8w": 8}
"""一覧の期間の指定と週数。`all`は期間で限定しない。"""


_CREATED_AT_RE = re.compile(r"(\d{8}-\d{6})-")
"""WIファイル名の先頭にある作成日時。`atk wi add`がローカル時刻で付ける。"""


_WEB_LOCK_TIMEOUT = 30.0
"""画面の操作（変更・明示的な同期）がprivate-notesのロックを待つ上限秒数。

定期同期のfetch・pushや他の`atk`プロセスが保持する区間は、低速な環境では数秒から十数秒に及ぶ。
待機を短くすると、競合しただけの保存が「別の操作が進行中です」で失敗するため長めに取る。
"""


_BACKGROUND_SYNC_LOCK_TIMEOUT = 2.0
"""定期同期がロックを待つ上限秒数。取得できない周期は見送り、画面の操作を待たせない。"""


_RECENT_REPO_RETENTION = datetime.timedelta(days=7)
"""状態を省略した対象リポジトリの候補へ、処理済みの終端エントリを含める期間。"""


# 別のホストで記録した処理日時と、このホストのファイル更新時刻の時計のずれを吸収する余裕。
_PROCESSED_TIME_CLOCK_MARGIN = datetime.timedelta(days=1)


_PROCESSED_AT_KEY = "terminal_processing_time"


_LIST_ENTRY_KEY = "list_entry"


EDIT_CONFLICT_MESSAGE = "編集中に他プロセスが対象を変更しました"


# エンドユーザーが記述する注記記法を注記として描画する。
MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False}).use(mdit_py_plugins.footnote.footnote_plugin)


class WebApiInputError(ValueError):
    """`atk serve`のWeb APIだけが送出する入力エラー。

    受信側はブラウザー画面であり、HTTP 400の応答本文は理由の文字列だけとする。
    CLIとWeb APIが共有する処理の入力エラー（`common.WebInputError`）は次の操作を持つが、
    Web APIの応答本文へは同じく`str()`の理由だけを使う。
    """


def _terminal_processing_time(text: str) -> datetime.datetime | None:
    """最後の処理結果に保存された処理日時をUTCで返す。"""
    lines = normalize_newlines(text).split("\n")
    headings = top_level_atx_headings(text, 2)
    matching = [index for index, (_, title) in enumerate(headings) if title == "処理結果"]
    if not matching:
        return None
    position = matching[-1]
    token = headings[position][0]
    assert token.map is not None
    following = headings[position + 1][0] if position + 1 < len(headings) else None
    end = following.map[0] if following is not None and following.map is not None else len(lines)
    prefix = "- 処理日時: "
    section = lines[token.map[1] : end]
    timestamps = [line.removeprefix(prefix) for line in section if line.startswith(prefix)]
    if len(timestamps) != 1:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(timestamps[0])
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.UTC)


def _without_source_frontmatter(message: str) -> str:
    """通常Web登録の本文から投入元を除いて返す。"""
    parsed = frontmatter.parse_frontmatter(message)
    if parsed is None:
        return message
    metadata, body = parsed
    normalized_metadata = dict(metadata)
    normalized_metadata.pop("source", None)
    if not normalized_metadata:
        return body
    return frontmatter.serialize_frontmatter(normalized_metadata, body)


def _summary(text: str, kind: str) -> str:
    body = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
    lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("## ")]
    if kind == wi_constants.WI_TYPE_UWI:
        lines = [line for line in lines if not line.startswith("<!--")]
    return lines[0][:160] if lines else ""


def _created_at(filename: str) -> datetime.datetime | None:
    """WIファイル名の先頭の作成日時をローカル時刻として返す。読めない名前は`None`を返す。

    一覧の期間の起点に更新時刻を使わないのは、Gitの取得や同期で変わり、作成からの経過を表さないためである。
    """
    match = _CREATED_AT_RE.match(filename)
    if match is None:
        return None
    try:
        return datetime.datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def _source_kind(source: typing.Any) -> str:
    """保存された投入元を一覧フィルターの分類へ変換する。

    `agent-toolkit:wi-standards`の由来判定に従い、`source`の欠落だけを人間由来とし、
    値を持つ項目をすべてエージェント由来とする。
    既知値の列挙を持たないため、新しい`source`値の追加で本関数を更新しない。
    """
    return "human" if source is None else "agent"


def _entry(
    path: pathlib.Path,
    kind: str,
    state: str,
    text: str,
    metadata: dict[str, typing.Any],
    updated_at: str | None = None,
) -> dict[str, object]:
    answered = wi_uwi_scan.is_uwi_answered(text) if kind == wi_constants.WI_TYPE_UWI else None
    return {
        "kind": kind,
        "state": state,
        "filename": path.name,
        "answered": answered,
        "needs_verify": _needs_verify(kind, state, text),
        "plan": kind == wi_constants.WI_TYPE_AWI and isinstance(metadata.get("plan_file"), str),
        "target_repo": _json_compatible(metadata.get("target_repo")),
        "source": _json_compatible(metadata.get("source")),
        "summary": _summary(text, kind),
        "updated_at": updated_at
        if updated_at is not None
        else datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.UTC).isoformat(),
    }


def _needs_verify(kind: str, state: str, text: str) -> bool:
    """反映後の観測だけが残るため`inbox`へ戻されたawiかを返す。

    process-wiは観測できる時刻より前の項目を`inbox`へ戻すため、保存状態だけでは未着手の項目と区別できない。
    pickerと同じく、フェンス外のトップレベルH2の最後の再開記録の節が観測だけが残る区分を持つかで判定する。
    `processing`と`hold`は状態バッジが別の意味を示すため対象から外す。
    """
    if kind != wi_constants.WI_TYPE_AWI or state != wi_constants.WI_STATE_INBOX:
        return False
    lines = wi_headings.last_h2_section_lines(text, wi_constants.OBSERVATION_RESUME_HEADING)
    return lines is not None and any(line.strip() == wi_constants.OBSERVATION_ONLY_RESUME_LINE for line in lines)


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
        return MARKDOWN.render(text)
    metadata, body = parsed
    table = _render_frontmatter_table(metadata)
    return table + MARKDOWN.render(body)


@functools.lru_cache(maxsize=128)
def _render_body(text: str) -> str:
    """frontmatterを除いた本文だけをMarkdownとして整形する。"""
    parsed = frontmatter.parse_frontmatter(text)
    return MARKDOWN.render(text if parsed is None else parsed[1])


def _question_metadata(metadata: dict[str, typing.Any], kind: str) -> tuple[str, list[str]]:
    """UWIの回答形式と選択肢をWeb UI用の安定した形へ正規化する。"""
    if kind != wi_constants.WI_TYPE_UWI:
        return wi_constants.QUESTION_TYPE_FREE_FORM, []
    raw_type = metadata.get("question_type")
    question_type = (
        raw_type
        if isinstance(raw_type, str) and raw_type in wi_constants.STORED_QUESTION_TYPES
        else wi_constants.QUESTION_TYPE_FREE_FORM
    )
    if question_type == wi_constants.QUESTION_TYPE_POST_APPROVAL:
        return question_type, list(wi_constants.POST_APPROVAL_CHOICES)
    if question_type != wi_constants.QUESTION_TYPE_CHOICE:
        return question_type, []
    raw_choices = metadata.get("choices")
    if isinstance(raw_choices, str):
        return question_type, [choice.strip() for choice in raw_choices.split(",") if choice.strip()]
    if isinstance(raw_choices, list) and all(isinstance(choice, str) and choice.strip() for choice in raw_choices):
        return question_type, [choice.strip() for choice in raw_choices]
    return question_type, []


def _uwi_answer(text: str, kind: str) -> str | None:
    """UWI回答欄の既存回答を編集用文字列として返す。"""
    if kind != wi_constants.WI_TYPE_UWI or uwi_mutations.ANSWER_MARKER not in text:
        return None
    return text.rsplit(uwi_mutations.ANSWER_MARKER, maxsplit=1)[1].strip() or None


class Operations:
    """同期ファイル操作をWeb API向けに提供する。"""

    def __init__(self, private_notes: pathlib.Path) -> None:
        self.private_notes = private_notes
        self._entry_index = entry_index.EntryIndex(private_notes)

    def _entries(self, filters: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
        """条件に一致する一覧と、走査中に発生した読取り警告を返す。

        種別と回答状況を混在させてファイル名の降順とする。
        """
        result: list[dict[str, object]] = []
        kind_filter = filters.get("type", "all")
        status_filter = filters.get("status", "all")
        answered_filter = filters.get("answered", "all")
        plan_filter = filters.get("plan", "all")
        target_repo_filter = filters.get("target_repo")
        query_terms = [term for term in filters.get("q", "").casefold().split(" ") if term]
        period_weeks = PERIOD_WEEKS.get(filters.get("period", "all"))
        created_after = datetime.datetime.now() - datetime.timedelta(weeks=period_weeks) if period_weeks is not None else None
        states = wi_filters.resolve_states(status_filter)
        target_repo_matcher = wi_filters.TargetRepoMatcher(target_repo_filter)
        indexed_entries, warnings = self._entry_index.scan(states)
        for indexed in indexed_entries:
            if kind_filter not in ("all", indexed.kind):
                continue
            if created_after is not None:
                created_at = _created_at(indexed.path.name)
                # 作成日時を読めない名前の項目は、期間を判定できないため期間の指定にかかわらず返す。
                if created_at is not None and created_at < created_after:
                    continue
            metadata = indexed.metadata
            is_plan = indexed.kind == wi_constants.WI_TYPE_AWI and isinstance(metadata.get("plan_file"), str)
            if plan_filter != "all" and is_plan != (plan_filter == "plan"):
                continue
            if not wi_filters.answered_matches(indexed.kind, indexed.text, answered_filter):
                continue
            source = _json_compatible(metadata.get("source"))
            if filters.get("source_empty") == "true" and not (source is None or isinstance(source, str) and not source.strip()):
                continue
            item_target_repo = _json_compatible(metadata.get("target_repo"))
            if not target_repo_matcher.matches(item_target_repo):
                continue
            if filters.get("source") and not wi_filters.source_matches_any(
                wi_filters.normalized_source(source), filters["source"]
            ):
                continue
            if filters.get("source_kind") and _source_kind(source) != filters["source_kind"]:
                continue
            if query_terms:
                search_values = (
                    indexed.text_folded,
                    indexed.path.name.casefold(),
                    str(item_target_repo or "").casefold(),
                    str(source or "").casefold(),
                )
                if not all(any(term in value for value in search_values) for term in query_terms):
                    continue
            # 表示用項目は本文、状態、ファイル名および更新時刻だけで決まるため、解析結果とともに保持して
            # 変更のないファイルでは組み立て直さない。
            entry_key = (indexed.state, indexed.path.name, indexed.updated_at)
            cached_entry = indexed.derived.get(_LIST_ENTRY_KEY)
            if cached_entry is None or cached_entry[0] != entry_key:
                cached_entry = (
                    entry_key,
                    _entry(
                        indexed.path,
                        indexed.kind or "unknown",
                        indexed.state,
                        indexed.text,
                        metadata,
                        indexed.updated_at,
                    ),
                )
                indexed.derived[_LIST_ENTRY_KEY] = cached_entry
            result.append(dict(cached_entry[1]))
        return sorted(result, key=lambda item: str(item["filename"]), reverse=True), warnings

    def entries_with_warnings(
        self,
        filters: dict[str, str],
    ) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
        """一覧API向けにエントリと読取り警告を返す。"""
        return self._entries(filters)

    def detail(self, state: str, filename: str) -> dict[str, object]:
        if state not in _ENTRY_STATES:
            raise WebApiInputError("stateが不正です")
        path = wi_web_input.validate_filename(filename, self.private_notes / state)
        if not path.is_file():
            candidates = []
            for current_state in _ENTRY_STATES:
                candidate = wi_web_input.validate_filename(filename, self.private_notes / current_state)
                if candidate.is_file():
                    candidates.append((current_state, candidate))
            if len(candidates) == 1:
                state, path = candidates[0]
        try:
            text = path.read_text(encoding="utf-8")
            parsed = frontmatter.parse_frontmatter(text)
            metadata = parsed[0] if parsed is not None else {}
            kind = wi_entries.entry_type_from_metadata(path, metadata) if parsed is not None else None
            question_type, choices = _question_metadata(metadata, kind or "unknown")
            detail_entry = _entry(path, kind or "unknown", state, text, metadata)
            display_text = text
            if kind == wi_constants.WI_TYPE_UWI and uwi_mutations.ANSWER_MARKER in text:
                # 保存・回答抽出と同じ最後の区切りだけを、表示用本文から除く。
                before_answer, answer = text.rsplit(uwi_mutations.ANSWER_MARKER, 1)
                display_text = before_answer + answer
            try:
                extracted_comment = user_comment_mutations.extract_user_comment(text)
            except user_comment_mutations.UserCommentError:
                extracted_comment = None
                comment_editable = False
            else:
                comment_editable = (
                    state in {wi_constants.WI_STATE_INBOX, wi_constants.WI_STATE_HOLD}
                    and kind == wi_constants.WI_TYPE_AWI
                    and _source_kind(metadata.get("source")) == "agent"
                )
            return {
                **detail_entry,
                "content": text,
                "content_html": _render_content(display_text),
                "body_html": _render_body(display_text),
                "frontmatter_entries": (
                    _json_compatible_mapping_entries(metadata) if isinstance(metadata, collections.abc.Mapping) else []
                ),
                "question_type": question_type,
                "choices": choices,
                "answer": _uwi_answer(text, kind or "unknown"),
                "user_comment": extracted_comment,
                "user_comment_editable": comment_editable,
            }
        except FileNotFoundError as error:
            raise FileNotFoundError(filename) from error

    def sync(self) -> bool:
        """未送信commitをpushし、リポジトリを明示的に同期する。

        ユーザーの操作によって呼ばれるため、直近のpullからの経過時間によらず毎回実行する。
        """
        return wi_sync.synchronize(self.private_notes, lock_timeout=_WEB_LOCK_TIMEOUT)

    def background_sync(self) -> bool:
        """定期更新としてリポジトリを同期し、実際にpullしたかを返す。

        直近のpullから一定時間内であれば省略する。ロックを取得できない場合は
        その周期を見送り、次周期で再試行する。
        """
        try:
            return wi_sync.synchronize(self.private_notes, only_if_stale=True, lock_timeout=_BACKGROUND_SYNC_LOCK_TIMEOUT)
        except filelock.Timeout:
            return False

    def target_repos(self, status: str = "active", *, now: datetime.datetime | None = None) -> list[str]:
        """指定状態のエントリに現れる対象リポジトリを昇順で返す。

        新規登録フォームの補完候補とフィルターの選択肢に用いる。
        状態を省略した場合に使う`active`ではactiveエントリに加え、直近7日以内に処理した終端エントリを含める。
        他の状態を明示した場合はその状態の全エントリを返す。
        `git pull`は行わず、ローカルの保存済みエントリだけを走査する。
        """
        found: set[str] = set()
        resolver_cache: dict[str, str | None] = {}
        indexed_entries, _warnings = self._entry_index.scan(
            wi_constants.WI_STATES if status == "active" else wi_filters.resolve_states(status)
        )
        current_time = (now or datetime.datetime.now(datetime.UTC)).astimezone(datetime.UTC)
        cutoff = current_time - _RECENT_REPO_RETENTION
        for indexed in indexed_entries:
            if status == "active" and indexed.state not in wi_constants.WI_ACTIVE_STATES:
                # 処理日時は書込時に記録されるため、ファイルの更新時刻より後にならない。
                # 更新時刻が境界より十分前の終端項目は本文を解析せずに除く。
                if datetime.datetime.fromisoformat(indexed.updated_at) < cutoff - _PROCESSED_TIME_CLOCK_MARGIN:
                    continue
                if _PROCESSED_AT_KEY not in indexed.derived:
                    indexed.derived[_PROCESSED_AT_KEY] = _terminal_processing_time(indexed.text)
                processed_at = indexed.derived[_PROCESSED_AT_KEY]
                if processed_at is None or not cutoff <= processed_at <= current_time:
                    continue
            target_repo = indexed.metadata.get("target_repo")
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
            return awi_mutations.edit_entry_content(
                self.private_notes,
                state=state,
                filename=filename,
                content=content,
                lock_timeout=_WEB_LOCK_TIMEOUT,
                expected_content=expected_content,
                skip_remote_sync=True,
            )
        except SystemExit as error:
            raise WebApiInputError("指定したエントリを操作できません") from error

    def user_comment(self, state: str, filename: str, comment: str, expected_content: str) -> bool:
        """エージェント由来のinboxまたはhold項目へユーザーコメントを追記、置換または削除する。

        空白だけのコメントはユーザーコメント節の削除として扱う。
        """
        if state not in {wi_constants.WI_STATE_INBOX, wi_constants.WI_STATE_HOLD}:
            raise WebApiInputError("ユーザーコメントを編集できる状態はinboxまたはholdだけです")
        if not isinstance(comment, str):
            raise WebApiInputError("commentは文字列で指定してください")
        if not isinstance(expected_content, str) or not expected_content.strip():
            raise WebApiInputError("expected_contentは空でない文字列で指定してください")

        parsed = frontmatter.parse_frontmatter(expected_content)
        if parsed is None:
            raise WebApiInputError("frontmatterを解析できません")
        metadata, _body = parsed
        if wi_constants.normalized_wi_type(metadata.get("type")) != wi_constants.WI_TYPE_AWI:
            raise WebApiInputError(f"ユーザーコメントの対象は{wi_constants.WI_TYPE_AWI}だけです")
        if _source_kind(metadata.get("source")) != "agent":
            raise WebApiInputError(
                "ユーザーコメントの対象はエージェント由来のawiだけです。sourceが未設定の項目は対象になりません"
            )
        try:
            updated = user_comment_mutations.update_user_comment(expected_content, comment)
        except user_comment_mutations.UserCommentError as error:
            raise WebApiInputError(str(error)) from error
        try:
            return awi_mutations.edit_entry_content(
                self.private_notes,
                state=state,
                filename=filename,
                content=updated,
                lock_timeout=_WEB_LOCK_TIMEOUT,
                expected_content=expected_content,
                skip_remote_sync=True,
            )
        except SystemExit as error:
            raise WebApiInputError("指定したエントリを操作できません") from error

    def add(
        self,
        messages: list[str],
        *,
        entry_type: str,
        target_repo: str | None,
        scope: str | None = None,
        question_type: str | None = None,
        choices: list[str] | None = None,
    ) -> list[str]:
        """エントリを原子的に追加する。

        `target_repo`が`None`の場合は各メッセージのfrontmatterの`target_repo`を必須とし、
        入力の検証は、各呼び出し元が使う`add_entries`でまとめて行う。
        """
        if entry_type not in wi_constants.WI_TYPES:
            raise WebApiInputError("typeが不正です")
        if entry_type == wi_constants.WI_TYPE_AWI and (scope or question_type or choices):
            raise WebApiInputError(f"scope・question_type・choicesはtype={wi_constants.WI_TYPE_UWI}でのみ指定できます")
        if entry_type == wi_constants.WI_TYPE_UWI:
            if question_type not in wi_constants.NEW_QUESTION_TYPES:
                raise WebApiInputError(
                    f"UWIの回答形式が不正か未指定です: {question_type}。"
                    "選択肢形式（choice）、はい／いいえ（yes-no）、事後承認（post-approval）を指定してください"
                )
            if question_type == "choice" and (choices is None or len(choices) < 2):
                raise WebApiInputError("choice形式には2件以上のchoicesが必要です")
            if question_type != "choice" and choices is not None:
                raise WebApiInputError("choicesはchoice形式でのみ指定できます")
        resolved_target_repo: str | None = None
        if target_repo is not None:
            try:
                resolved_target_repo = awi_repo.resolve_repo_id(target_repo)
            except SystemExit as error:
                raise WebApiInputError("target_repoを解決できません") from error
        return awi_add.add_entries(
            self.private_notes,
            messages=[_without_source_frontmatter(message) for message in messages],
            target_repo=resolved_target_repo,
            source=None,
            now=datetime.datetime.now(),
            entry_type=entry_type,
            scope=scope,
            question_type=question_type,
            choices=",".join(choices) if choices else None,
            lock_timeout=_WEB_LOCK_TIMEOUT,
            skip_remote_sync=True,
        )

    def add_batch(self, text: str) -> dict[str, object]:
        """`atk wi show --all`の出力形式のテキストからエントリを一括で取り込む。

        原文保持の契約はCLIと共通の`batch.add_batch_entries`が担う。
        ファイル名と本文がともに既存項目と一致して取り込みを省いたエントリは`skipped`で返す。
        """
        mapping, skipped, warnings = awi_batch.add_batch_entries(
            self.private_notes,
            texts=[text],
            now=datetime.datetime.now(),
            lock_timeout=_WEB_LOCK_TIMEOUT,
            skip_remote_sync=True,
        )
        return {
            "filenames": [saved for _original, saved in mapping],
            "mapping": dict(mapping),
            "skipped": skipped,
            "warnings": warnings,
        }

    def answer_uwi(
        self,
        filename: str,
        answer: str,
        expected_content: str | None = None,
        state: str | None = None,
    ) -> bool:
        """UWI回答欄を置換する。"""
        return uwi_mutations.answer_uwi(
            self.private_notes,
            filename=filename,
            answer=answer,
            state=state,
            lock_timeout=_WEB_LOCK_TIMEOUT,
            expected_content=expected_content,
            skip_remote_sync=True,
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
        """複数エントリを全件検証後に移動または削除する。

        `force`は`action="remove"`の場合のみ意味を持ち、
        processing状態のファイルを通常の削除から守る処理（`atk wi rm`の`--force`と同義）を解除する。
        """
        try:
            return awi_mutations.transition_entries(
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
                skip_remote_sync=True,
            )
        except SystemExit as error:
            raise WebApiInputError("指定したエントリを操作できません") from error

    def commit(self) -> bool:
        """外部編集差分をcommitしてpushする。"""
        return awi_mutations.commit_entries(self.private_notes, lock_timeout=_WEB_LOCK_TIMEOUT).changed
