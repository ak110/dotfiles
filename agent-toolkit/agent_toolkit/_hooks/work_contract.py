"""現行の作業契約の本文と、回答後に元の工程へ戻る基準を通知する。"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import os
import pathlib
import re
from collections.abc import Iterator

from agent_toolkit._common import automated_prompt, delegated_session, runtime_inserted
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._common.external_source_review import EXTERNAL_SOURCE_ENTRY_KEY, source_urls
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks.notice import resolve_notice
lazy from agent_toolkit._atk import managed_temp
lazy from agent_toolkit._common import session_registry, startup_documents

RECORD_NAME = "work-contract.md"
DELEGATED_NOTICE_KEY = "whole_contract_notified"
"""委譲先の主体ごとに、更新を案内した全体作業契約の版（SHA-256）を持つセッション状態の項目。"""
DELEGATED_LAUNCH_KEY = "whole_contract_launch"
"""委譲先が起動時に受け取った全体作業契約の所在と版。登録簿の起動本文から1回だけ求めて保持する。"""

NOTICE_BODY_LIMIT = 4_000
"""通知へ載せる契約本文の上限（文字）。

Claude Codeは`additionalContext`を10,000文字で保存先とプレビューへ置き換えるため、
同じ入力で届く他の通知と合わせても上限へ収まる値にする。超える本文は所在だけを示す。
"""

RETURN_CRITERION = (
    "要求・制約・訂正を含む発話を受けたら、成果物を編集する前に作業契約の「現在有効な要求・制約」へ反映してください。"
    "質問、状況照会、継続だけなら更新しない。"
    "要求の統合・回答後の再開・完了判断は、`agent-toolkit/skills/atk-mode/references/work-contract.md`"
    "「追加入力の統合」「更新する時点」「工程境界での要求と成果の確認」に従ってください。"
)


SHORT_NOTICE = "現行の作業契約を取得してください: "
"""通知の上限に本文が収まらない場合に、一時領域の通知へ加える所在の1行。"""

_RECORD_REFERENCE = re.compile(r"`([^\n`]+):([1-9][0-9]{0,7})`")
_QUOTED_TERM = re.compile(r"「([^」\n]+)」|`([^`\n]+)`")
_EXCLUDED_ACTION = re.compile(r"(?:^|[。、： \s])([^。、\s]{2,12})は対象外")
_DECISION_NAME = r"(?:決定|方針|指示|合意|取り決め)"
_RELIANCE = r"(?:に従う|に従い|を根拠に(?:する|し)|を[^。\n]{0,24}適用(?:する|した))(?=[。\n、]|$)"
_DECISION_CUE = re.compile(rf"{_DECISION_NAME}(?={_RELIANCE})")
_DECLARED_SUBJECT = re.compile(r"(?:^|[。、： ]|^- )([^。、： ]{4,30})は(?=[^。]*(?:に限|のみ|だけ))")
_SCOPE_FIELD = re.compile(r"(?:適用対象|除外)[:：]\s*([^。\n]{4,48})")


EXTERNAL_SOURCE_WORKS = frozenset({"add-awi-by-user", "process-user-comments", "implement"})


def observe_external_source_entry(session_id: str, work: str, arguments: str) -> None:
    urls = sorted(set(source_urls(arguments))) if work in EXTERNAL_SOURCE_WORKS else []
    entry = {"work": work, "urls": urls}
    update_state(session_id, lambda state: {**state, EXTERNAL_SOURCE_ENTRY_KEY: entry})


def scope_review_before_send(session_id: str, raw_transcript: object, message: str, *, actor: str) -> str | None:
    path = record_path(session_id)
    if path is None or not message.strip() or not isinstance(raw_transcript, str) or not raw_transcript:
        return None
    transcript = pathlib.Path(raw_transcript)
    names = {raw_transcript, transcript.name, transcript.stem, session_id, f"claude:{session_id}", f"codex:{session_id}"}
    candidates: dict[int, dict[str, tuple[str, set[str]]]] = {}
    try:
        for decision in path.read_text(encoding="utf-8").splitlines():
            for loc in _RECORD_REFERENCE.finditer(decision):
                name, number = loc.groups()
                # 現ホストが渡す当該会話の記録だけを読む。契約に書かれた任意のpathは開かない。  # pyfltr: allow-comment
                if name not in names:
                    continue
                terms = {
                    term
                    for match in _QUOTED_TERM.finditer(_RECORD_REFERENCE.sub("", decision))
                    if len(term := match[1] or match[2]) >= 4
                }
                line = _RECORD_REFERENCE.sub("", decision)
                terms.update(_DECLARED_SUBJECT.findall(line))
                terms.update(_SCOPE_FIELD.findall(line))
                reference = re.escape(loc[0].strip("`"))
                cited = re.search(rf"{reference}`?\s*(?:の{_DECISION_NAME})?{_RELIANCE}", message)
                if cited or _applies_decision(message, decision, terms):
                    key = hashlib.sha256(f"{loc[0]}\n{decision.strip()}".encode()).hexdigest()
                    candidates.setdefault(int(number), {})[key] = (decision.strip(), terms)
    except OSError, UnicodeError:
        return None
    if not candidates:
        return None

    prior = read_state(session_id).get("scope_reviewed_before_send", {})
    checked = set(prior.get(actor, [])) if isinstance(prior, dict) else set()
    candidates = {
        line: remaining
        for line, decisions in candidates.items()
        if (remaining := {key: value for key, value in decisions.items() if key not in checked})
    }
    if not candidates:
        return None
    observed: dict[str, str] = {}
    try:
        with transcript.open(encoding="utf-8", errors="replace") as stream:
            for line_number, raw in enumerate(itertools.islice(stream, max(candidates)), 1):
                if line_number not in candidates:
                    continue
                if (utterance := _original_user_text(raw)) is not None:
                    for key, (decision, terms) in candidates[line_number].items():
                        observed[key] = _scope_review_entry(decision, utterance, terms)
    except OSError:
        return None
    if not observed:
        return None

    reminders: list[str] = []

    def mark(state: dict) -> dict | None:
        prior = state.get("scope_reviewed_before_send", {})
        first = dict(prior) if isinstance(prior, dict) else {}
        checked = set(first.get(actor, []))
        reminders.extend(value for key, value in observed.items() if key not in checked)
        if not reminders:
            return None
        first[actor] = sorted(checked | observed.keys())
        return {**state, "scope_reviewed_before_send": first}

    update_state(session_id, mark)
    return "\n\n".join(reminders) if reminders else None


def _applies_decision(message: str, decision: str, terms: set[str]) -> bool:
    excluded = _EXCLUDED_ACTION.findall(decision)
    return any(
        any(term in part for term in terms)
        and (
            _DECISION_CUE.search(part)
            or any(action + sense in part for action in excluded for sense in ("する", "予定", "に含め", "を許可"))
        )
        for part in re.split(r"[。\n]", message)
    )


def _scope_review_entry(decision: str, utterance: str, terms: set[str]) -> str:
    parts = [part for part in re.split(r"(?<=。)|\n", utterance) if any(term in part for term in terms)]
    excerpt = "".join(parts) or utterance
    original = (
        f"原文の抜粋: {json.dumps(redact_secrets(excerpt), ensure_ascii=False)}"
        if excerpt and len(excerpt) <= 800
        else "契約の参照位置で、原文の該当部分を確認してください。"
    )
    return f"契約: {redact_secrets(decision)}\n{original}"


def _original_user_text(raw: str, *, include_answers: bool = False) -> str | None:
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict):
        return None
    if entry.get("type") == "event_msg" and isinstance(payload := entry.get("payload"), dict):
        text = payload.get("message")
        return text if payload.get("type") == "user_message" and isinstance(text, str) else None
    if (
        entry.get("type") != "user"
        or runtime_inserted.is_runtime_generated(entry)
        or not isinstance(message := entry.get("message"), dict)
    ):
        return None
    if isinstance(result := entry.get("toolUseResult"), dict) and isinstance(result.get("answers"), dict):
        # 質問への回答も原本だが、選択肢以外の私的な自由記述を再配送しない。  # pyfltr: allow-comment
        return "\n".join(value for value in result["answers"].values() if isinstance(value, str)) if include_answers else ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )
    return None


def progress_update_warning(session_id: str) -> str | None:
    path = record_path(session_id)
    if path is None:
        return None
    key = "work_contract_progress_checked"
    try:
        stat = path.stat()
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
        if read_state(session_id).get(key) == stamp:
            return None
        lines = _visible_contract_lines(path.read_text(encoding="utf-8"))
        found = [
            number
            for number, line in lines
            if re.match(
                r"^ {0,3}(?:#{1,6} +|[-*] +)(?:\d{4}-\d{2}-\d{2}(?:T[\d:+Z-]+)? +)?"
                r"(?:現在の進捗|進捗(?:状況|ログ)?|現在の担当状況|担当の現在状態|"
                r"検収結果|実装結果|検証結果|経緯|新着WIの判定)(?:[:： ]|$)",
                line,
            )
        ]
    except OSError, UnicodeError:
        return None
    update_state(session_id, lambda state: {**state, key: stamp})
    if not found:
        return None
    numbers = "、".join(str(number) for number in found)
    return (
        f"`{path}`の{numbers}行目に進捗・検収の記録がある。"
        "担当の引き継ぎ記録、対応する計画かWI、または公開担当の引き継ぎ記録へ移し、"
        "全体作業契約には原本の所在だけを残してください。"
    )


def _visible_contract_lines(body: str) -> Iterator[tuple[int, str]]:
    fence = ""
    for number, line in enumerate(body.splitlines(), 1):
        if delimiters := re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line):
            if not fence:
                fence = delimiters[1]
            elif delimiters[1].startswith(fence) and not delimiters[2].strip():
                fence = ""
        elif not fence:
            yield number, line


def _additional_requirement_quotes(body: str) -> dict[int, str]:
    quotes: dict[int, str] = {}
    active = False
    for number, line in _visible_contract_lines(body):
        if line.startswith(("## ", "# ")):
            active = re.match(r"^## 追加要求(?:[ （(]|$)", line) is not None
        if active and (match := re.match(r"^[ \t]*(?:- )?原文[:：][ \t]*(.*)$", line)):
            value = match[1].strip()
            quotes[number] = value[1:-1] if value.startswith("「") and value.endswith("」") else value
    return quotes


def quote_update_warning(session_id: str, raw_transcript: object) -> str | None:
    if not isinstance(raw_transcript, str) or not raw_transcript:
        return None
    path = record_path(session_id)
    if path is None:
        return None
    key = "work_contract_quotes_checked"
    try:
        stat = path.stat()
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
        if read_state(session_id).get(key) == stamp:
            return None
        quotes = _additional_requirement_quotes(path.read_text(encoding="utf-8"))
        if quotes:
            with pathlib.Path(raw_transcript).open(encoding="utf-8") as stream:
                for row in stream:
                    source = _original_user_text(row, include_answers=True)
                    if (
                        source
                        and not automated_prompt.contains(source)
                        and not runtime_inserted.is_runtime_inserted_text(source)
                    ):
                        quotes = {number: quote for number, quote in quotes.items() if not quote or quote not in source}
                    if not quotes:
                        break
    except OSError, UnicodeError:
        return None
    update_state(session_id, lambda state: {**state, key: stamp})
    if not quotes:
        return None
    lines = "、".join(f"{number}行目" for number in quotes)
    return f"`{path}`の{lines}の「原文:」が実ユーザー発話と一致しない。会話から逐語で引用し直し、要約は原文欄から移す。"


def short_notice(path: pathlib.Path) -> str:
    """本文の代わりに、既知の契約原本を読む操作を示す。"""
    return SHORT_NOTICE + f"`{path}`"


def exists(session_id: str) -> bool:
    """メインの作業契約が実在するかを返す。"""
    path = record_path(session_id)
    return path is not None and path.is_file()


def record_path(session_id: str) -> pathlib.Path | None:
    directory = managed_temp.find_session_temp(session_id)
    return directory / RECORD_NAME if directory is not None else None


def observe_input(session_id: str) -> str | None:
    path = record_path(session_id)
    try:
        updated = path.stat().st_mtime if path is not None and path.is_file() else None
    except OSError:
        updated = None
    observed: dict = {}

    def count(state: dict) -> dict:
        state = {**state, "work_contract_input_sequence": state.get("work_contract_input_sequence", 0) + 1}
        if updated is None:
            return state
        previous = state.get("work_contract_observed_mtime")
        if previous is None:
            inputs, partial = 1, True
        elif previous != updated:
            inputs, partial = 1, False
        else:
            inputs, partial = (
                state.get("work_contract_inputs_since_update", 0) + 1,
                state.get("work_contract_count_partial", True),
            )
        observed.update(inputs=inputs, partial=partial)
        return {
            **state,
            "work_contract_observed_mtime": updated,
            "work_contract_inputs_since_update": inputs,
            "work_contract_count_partial": partial,
        }

    update_state(session_id, count)
    resolve_notice(session_id, "pretooluse", "main", subject="main")
    if path is None or updated is None:
        return None
    location = short_notice(path)
    if not observed:
        return location
    scope = "今回を含む。初回観測より前は不明" if observed["partial"] else "今回を含む"
    return (
        location + f"\n作業契約の最終更新後の実ユーザー発話: {observed['inputs']}件（{scope}）。"
        "件数は要求を再確認するための観測値であり、更新漏れの件数ではない。"
    )


def reset_notice(session_id: str) -> None:
    update_state(
        session_id,
        lambda state: {
            key: value
            for key, value in state.items()
            if key not in {"work_contract_notified_sequence", DELEGATED_NOTICE_KEY, "whole_contract_read"}
        },
    )
    resolve_notice(session_id, "pretooluse", "main", subject="main")


@dataclasses.dataclass(frozen=True)
class DelegatedLaunch:
    """委譲先の起動本文が渡した、全体作業契約と引き継ぎ記録の所在。"""

    whole_contract: pathlib.Path | None = None
    whole_contract_sha256: str | None = None
    """起動本文へ全文を入れた時点の全体作業契約の版。本文を入れていない起動では`None`とする。"""
    handoff: str | None = None


def delegated_launch(session_id: str) -> DelegatedLaunch | None:
    """`agents_server`の登録簿にある起動本文から、委譲先が受け取った全体作業契約と引き継ぎ記録の所在を返す。

    登録簿に無いsession（組み込みの委譲機能の子や、登録前の起動）は`None`とする。
    """
    if not re.fullmatch(r"[0-9A-Za-z_-]+", session_id):
        return None
    try:
        resolution = session_registry.resolve(session_id)
    except OSError, ValueError:
        return None
    info = resolution.resume_info or resolution.handoff_info
    if info is None:
        return None
    prompt = info.launch_info.prompt
    path = delegated_session.prompt_whole_contract_path(prompt)
    return DelegatedLaunch(
        whole_contract=path,
        whole_contract_sha256=startup_documents.delivered_versions(prompt).get(path) if path is not None else None,
        handoff=delegated_session.prompt_handoff_path(prompt),
    )


def _launched_contract(session_id: str) -> tuple[pathlib.Path, str] | None:
    """起動時に全文を受け取った全体作業契約の所在と版を、セッション状態の記録から返す。

    初回だけ登録簿を読み、結果を記録する。登録簿にまだ無いsessionは記録せず、次の操作で再び読む。
    """
    recorded = read_state(session_id).get(DELEGATED_LAUNCH_KEY)
    if not isinstance(recorded, dict):
        launch = delegated_launch(session_id)
        if launch is None:
            return None
        recorded = (
            {"path": str(launch.whole_contract), "sha256": launch.whole_contract_sha256}
            if launch.whole_contract is not None and launch.whole_contract_sha256 is not None
            else {}
        )
        update_state(session_id, lambda state: {**state, DELEGATED_LAUNCH_KEY: recorded})
    path, digest = recorded.get("path"), recorded.get("sha256")
    return (pathlib.Path(path), digest) if isinstance(path, str) and isinstance(digest, str) else None


def delegated_notice(session_id: str, *, agent: str = "main") -> tuple[str, str] | None:
    if not session_id or not delegated_session.is_delegated(os.environ):
        return None
    launched = _launched_contract(session_id)
    if launched is None:
        return None
    path, delivered = launched
    try:
        current = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return (
            f"全体作業契約を読めない。原本: `{path}`。",
            "委譲元へ原本の所在と読取可能性を確認してください。",
        )
    notified = read_state(session_id).get(DELEGATED_NOTICE_KEY, {})
    seen = notified.get(agent, []) if isinstance(notified, dict) else []
    if current == delivered or current in seen:
        return None
    text = f"全体作業契約が起動後に更新された。原本: `{path}`。"
    notice_module.stage_delivery(text, session_id, DELEGATED_NOTICE_KEY, agent, current)
    return text, "原本を全文読み、現行要求と「即時対応で確定した暫定規則」を確かめて続行へ適用してください。"


def notice(session_id: str, *, budget: int = NOTICE_BODY_LIMIT) -> str | None:
    """既存の作業契約の本文を参照資料として返す。要求の統合と達成はエージェントが判定する。

    本文を毎回届けるのは、所在だけの通知では質問へ回答した後に契約を読み直さず、元の工程へ戻らない事例があったためである。
    `budget`を超える場合と読取に失敗した場合は、本文の代わりに非遮断の案内を返す。
    """
    path = record_path(session_id)
    if path is None or not path.is_file():
        return None
    try:
        body = path.read_bytes().decode("utf-8").rstrip("\n")
    except (OSError, UnicodeDecodeError) as error:
        return (
            f"現行の作業契約を読めない（{type(error).__name__}: {error}）。\n"
            f"権限や文字コードの原因を解消した後、`{path}`を読み、現在の目的・要件を確かめてください。"
            "解消できなければ、会話の原文と計画・WIから現在の目的・要件を確かめて作業を続け、"
            "所在と診断を委譲元（委譲元が無ければユーザー）へ報告してください。"
            f"{RETURN_CRITERION}"
        )
    if len(body) > budget:
        return (
            f"現行の作業契約は通知の上限{budget}文字を超える。`{path}`を読み、現在の目的・要件を確かめてください。"
            "発話の転記、時刻、経緯、完了した要求が残っていれば、現在の目的・要件だけに書き直してください。"
            f"{RETURN_CRITERION}"
        )
    return f"現行の作業契約（機械生成の参照資料。原本: `{path}`）:\n{body}\n\n{RETURN_CRITERION}"
