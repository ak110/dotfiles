"""証拠抽出の問題候補の抽出。`--bundle`が記録から失敗・警告・介入などの候補と個別の証拠を組み立てる。"""

from __future__ import annotations

import collections
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, NamedTuple

from session_evidence_detail import (
    _clip_structure,
    _entry_detail_events,
    _entry_timestamp,
)
from session_evidence_extract import (
    _CANDIDATE_VARIABLE,
    _CANDIDATE_VARIABLE_PLACEHOLDER,
    _ENV_ASSIGNMENT,
    _HOOK_FAILURE_PREFIX,
    _IMPROVEMENT_MARKER,
    _OMISSION_MARK,
    _SHELL_NAMES,
    _basename,
    _clip,
    _CollectedRecord,
    _DetailBudget,
    _generated_user_event,
    _json_object,
    _result_call_ids,
    _shell_tokens,
)
from session_evidence_hook_notices import (
    _hook_notice_keys,
    _hook_notice_kind_text,
    _hook_records,
    _normalize_candidate_kind_text,
    _normalize_hook_candidate_text,
)
from session_evidence_tool_calls import (
    _record_tool_calls,
)

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import constants as _wi_constants
from agent_toolkit._atk.wi import sections as _wi_sections
from agent_toolkit._atk.wi import style_diagnostics as _style_diagnostics
from agent_toolkit._atk.wi import uwi_scan as _uwi_scan
from agent_toolkit._atk.wi.constants import PROCESS_WI_GOAL_BODY as _PROCESS_WI_GOAL_BODY
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter as _parse_wi_frontmatter
from agent_toolkit._common import response_language_check as _response_language_check
from agent_toolkit._common.shell_quoting import QuotingScanner as _QuotingScanner
from agent_toolkit._common.shell_segments import extract_execution_segments as _extract_execution_segments

_PERMISSION_DENIAL_MARKER = "denied by the Claude Code auto mode classifier"
"""auto mode classifierの拒否本文に現れる定型句。実行環境が返す本文をそのまま用いる。"""


_BUNDLE_BODY_LENGTH = 200


_HOOK_NOTICE_VARIANT_LIMIT = 5


# 委譲先の返却形式の欄名。日本語名へ改める前の英字の欄名（後者）で返す版の委譲先も同じ意味で読む。
_RETURN_STATUS_PREFIXES = ("状態:", "status:")


# 委譲先は続行できないことを`続行できない理由:`の行で表す。値が空の行（後続行へ列挙する形式の見出し）と、
# 省略時の値「なし」を書いた旧形式の行は続行不能を示さない。
_ESCALATION_REASON_PREFIX = "続行できない理由:"


_NO_ESCALATION_REASONS = frozenset({"", "なし"})


# 続行不能を状態値で示した旧形式の返却。過去のセッション記録に残るため読み取り互換として判定する。
_ESCALATION_RETURN_STATUS = "needs_escalation"


_COMPLETED_RETURN_LINES = frozenset(f"{prefix} completed" for prefix in _RETURN_STATUS_PREFIXES)


_UNRESOLVED_RETURN_PREFIXES = ("未解決の指摘数:", "unresolved:")


_ESCALATION_REASON_PREFIXES = (_ESCALATION_REASON_PREFIX, "reason:")


_CANDIDATE_EVIDENCE_LENGTH = 2000


UNTRUNCATED_EVIDENCE_KINDS = frozenset(
    {
        "user-intervention",
        "confirmation-request",
        "wi-user-response",
        "command-failure",
        "tool-failure",
        "delegate-return",
        "escalation",
    }
)
"""個別証拠の本文を切り詰めない候補種別。

これらの本文は振り返りの分析主体が原因を判断するための一次資料であり、切り詰めると
裏取りのために元記録を読み直す工程が生じる。hook通知など定型本文の種別だけに上限を残す。
"""


_TOOL_USE_EVIDENCE_KINDS = frozenset({"hook-notice", "command-failure", "tool-failure", "permission-denial"})
"""個別証拠へ対象のツール呼び出しの入力を加える候補種別。"""


_HOOK_NOTICE_CANDIDATE_TAGS = frozenset({"block", "warn"})


_HOOK_ORIGIN_MIN_MATCH_LENGTH = 20
"""hook通知の本文どうしを前方一致で比較するときに一致を求める最小の文字数。短い定型句だけの一致で別の通知を同一視しないための下限とする。"""


_DELEGATE_COMPLETION_VALUES = frozenset(
    {
        "計画作成完了",
        "計画なし準備完了",
        "計画検査完了",
        "実装完了",
        "対応完了",
        "対応完了（再レビュー不要）",
        "統合完了",
        "終端完了",
    }
)
"""`<役割名>.subagent.md`が成功の返却値として定める固定の先頭行。各値が`agent-toolkit/share/`の`<役割名>.subagent.md`に現れることをテストが確かめる。"""


_UNEXPECTED_EVENT_PREFIXES = ("想定外事象:", "想定外事象：")


_VERDICT_LINE = re.compile(r"^(?:#+\s*)?(?:\*\*)?\s*判定[^:：]{0,30}[:：]\s*(?:\*\*)?\s*(?P<value>\S.*)$")


_SHELL_OPERATOR_CHARS = frozenset(";&|<>()\n")


_SHELL_DELEGATION_MARKER = "次のコマンドを実行し、結果を報告せよ。"
"""`agents_server`の`start`のshellが委譲先へ渡す指示本文の冒頭の文。値が同サーバーの指示本文と一致することをテストが確かめる。"""


_REPORTED_EXIT_CODE = re.compile(r"(?:終了コード|exit(?:[_ ]?code)?|(?<![A-Za-z])rc)[^0-9\n]{0,15}?(\d+)", re.IGNORECASE)


_REPORTED_NONZERO_COUNT = re.compile(
    r"(?:(?:failed|warnings?|diagnostics)[\"'`]*\s*[:=]\s*[1-9])|(?:(?:失敗|警告|診断)[^0-9\n]{0,10}?[1-9][0-9]*\s*件)",
    re.IGNORECASE,
)


_TRANSIENT_CLASSIFIER_ERROR = "The server-side auto mode classifier gave no verdict (error)"


_FAILURE_PATH = re.compile(r"(?<!\w)(?:~?/|[A-Za-z]:[\\/])[^\s'\"`]+")


_FAILURE_RECORD_ID = re.compile(r"\b(?:codex|claude|agy):[A-Za-z0-9_-]+(?::\d+)?")
_FAILURE_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


_CHECK_COMMANDS = frozenset(
    {
        ("pyfltr", "run"),
        ("pytest", ""),
        ("make", "test"),
        ("atk", "plan-check"),
        ("atk", "exec-review-evidence-check"),
        ("atk", "validate"),
        ("gh", "watch"),
        ("wait_ci.py", ""),
        ("wait-ci", ""),
    }
)


_ADHOC_SHELL_TOOLS = frozenset({"bash", "exec", "exec_command", "shell", "local_shell", "shell_command"})
"""シェルのコマンドを実行するツール名の末尾部分。Codexの`exec`はJavaScript本文から`exec_command`を呼ぶ形でコマンドを渡す。"""


_ADHOC_PYTHON = r"(?<![\w.-])python(?:\d+(?:\.\d+)*)?"


_ADHOC_INLINE_CODE = re.compile(
    _ADHOC_PYTHON + r"(?:\s+-[A-Za-z]+)*?\s+-c(?![\w-])"
    r"|" + _ADHOC_PYTHON + r"(?:\s+-[A-Za-z]+)*(?:\s+-)?\s*<<"
    r"|(?<![\w.-])(?:python(?:\d+(?:\.\d+)*)?|uv\s+run)(?:\s+-\S+)*\s+\S*managed-temp/\S+\.py(?!\w)"
    r"|(?<![\w.-])node(?:\s+-\S+)*?\s+(?:-e|--eval)(?![\w-])"
)
"""インタプリタへその場で書いたコードを渡す実行（`-c`、ヒアドキュメントの標準入力、managed-tempのスクリプト、`node -e`）。"""


_ADHOC_SAVED_OUTPUT = re.compile(r"atk-output-|agents-wait-|/tool-results/|/tasks/[^\s'\"]*\.output(?!\w)")
"""`atk`が標準出力の代わりに示す保存先、`atk agents wait`の結果ファイル、
ホストが退避したツール結果とバックグラウンドタスクの出力。
"""


_ADHOC_OUTPUT_PROCESSOR = re.compile(r"(?<![\w.-])(?:jq|awk|gawk|cut|sed|python(?:\d+(?:\.\d+)*)?)(?![\w.-])")
"""保存された出力の後加工に使うコマンド。読むだけの`cat`・`rg`・`head`は含めない。"""


_CANDIDATE_CONTEXT_FIELDS = ("assistant_context", "user_response", "wi_file", "wi_state", "wi_section")
"""候補へ写す判断材料の項目。確認の質問・選択肢・回答と、記入元のWIの所在を候補一覧へ載せるために使う。"""


_REPORT_UWI_QUESTION = re.compile(r"の作業結果と振り返りについて、この対応で問題ありませんか？$")
"""`agent-toolkit:completion-report`が投入する報告用UWIの冒頭の問い。報告用UWIは確認ではないため候補から除く。"""


_WI_PROCESSING_OPERATIONS = frozenset({"start-processing", "adopt", "reject", "unhold"})
"""対象セッションが処理したWIとみなす`atk wi`のサブコマンド。`show`などで読んだだけのWIは含めない。"""


_WI_FILENAME = re.compile(r"\d{8}-\d{6}-\d{3}\.md")


_YES_NO_CHOICES = ("はい", "いいえ")
"""`question_type: yes-no`のUWIで回答画面が示す選択肢。"""


def _processed_wi_filenames(collected: list[_CollectedRecord]) -> set[str]:
    """メインと全ての委譲先が実行したシェルコマンドから、処理したWIのファイル名を返す。

    コマンドの実行位置にある`atk wi <サブコマンド>`だけを対象とし、引用や説明文の中の文字列は対象から外す。
    """
    names: set[str] = set()
    for item in collected:
        for call in _record_tool_calls(item.records):
            if call.tool.casefold().rsplit("__", maxsplit=1)[-1].rsplit(".", maxsplit=1)[-1] not in _ADHOC_SHELL_TOOLS:
                continue
            for segment in _extract_execution_segments(call.text):
                tokens = list(segment.tokens)
                if not segment.resolved or len(tokens) < 3:
                    continue
                if Path(tokens[0].replace("\\", "/")).name not in {"atk", "atk.cmd", "atk.py"} or tokens[1] != "wi":
                    continue
                if tokens[2] in _WI_PROCESSING_OPERATIONS:
                    names.update(Path(token).name for token in tokens[3:] if _WI_FILENAME.fullmatch(Path(token).name))
    return names


def _wi_entry(text: str, section: str) -> str:
    """WIの指定した節から、回答欄の注記行（HTMLコメントだけの行）を除いた記入を返す。

    `## ユーザーコメント`は処理結果の節が後ろに続く終端済みの項目もあるため、節の位置を問わずに読む。
    """
    lines = [
        line
        for line in _wi_sections.h2_sections(text).get(section, "").splitlines()
        if not (line.strip().startswith("<!--") and line.strip().endswith("-->"))
    ]
    return "\n".join(lines).strip()


def _wi_choices(metadata: dict[str, Any]) -> tuple[str, ...]:
    """UWIの回答画面が示す選択肢を返す。"""
    if metadata.get("question_type") == _wi_constants.QUESTION_TYPE_YES_NO:
        return _YES_NO_CHOICES
    choices = metadata.get("choices")
    if isinstance(choices, str):
        return tuple(choice.strip() for choice in choices.split(",") if choice.strip())
    if isinstance(choices, list):
        return tuple(str(choice).strip() for choice in choices)
    return ()


def _wi_candidate_events(collected: list[_CollectedRecord], session_id: str) -> list[dict[str, Any]]:
    """private-notesのWIから、対象セッションが投入したUWIと、WIの記入欄で行われた是正を返す。

    `submitted-uwi`は`submitter_session`が対象セッションと一致するUWIで、報告用UWIを除く。
    確認をUWIへ送った判断の要否を振り返るため、回答の有無によらず載せる。
    `wi-user-response`は、対象セッションが投入したUWIと`_processed_wi_filenames`が返すWIのうち、
    準備の時点で記入を持つものとする。UWIの`## 回答`は選択肢のいずれかと完全に一致する記入を除き、
    AWIの`## ユーザーコメント`は空でなければ載せる。記録位置を持たないため、`record`へWIのファイル名を示す値を置く。
    private-notesを解決できない環境では空を返す。
    """
    root = _uwi_scan.private_notes_root()
    if root is None:
        return []
    processed = _processed_wi_filenames(collected)
    events: list[dict[str, Any]] = []
    for state in _wi_constants.WI_STATES:
        directory = root / state
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            parsed = _parse_wi_frontmatter(text)
            metadata = parsed[0] if parsed is not None else {}
            is_uwi = _wi_constants.normalized_wi_type(metadata.get("type")) == _wi_constants.WI_TYPE_UWI
            submitted = is_uwi and metadata.get("submitter_session") == session_id
            question = _wi_sections.title(text) if is_uwi else ""
            if submitted and _REPORT_UWI_QUESTION.search(question):
                continue
            location = {"record": f"wi:{path.name}", "line": 0, "wi_file": path.name, "wi_state": state}
            if submitted:
                events.append({"kind": "submitted-uwi", **location, "tag": "submitted-uwi", "text": question})
            if not submitted and path.name not in processed:
                continue
            section = "回答" if is_uwi else "ユーザーコメント"
            entry = _wi_entry(text, section)
            if entry and not (is_uwi and entry in _wi_choices(metadata)):
                events.append({"kind": "wi-user-response", **location, "tag": section, "wi_section": section, "text": entry})
    return events


def _adhoc_processing_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """メイン記録と全ての委譲先の記録から、その場のコードによる加工に当たるシェル呼び出しを返す。

    会話の流れはメイン記録だけを対象とするため、委譲先の成功した呼び出しは候補の側でしか振り返りの入力に届かない。
    代表入力は会話の流れと同じく空白を詰めた1行を`_BUNDLE_BODY_LENGTH`字までとする。
    """
    events: list[dict[str, Any]] = []
    for item in collected:
        for call in _record_tool_calls(item.records):
            if not _is_adhoc_processing(call.tool, call.text):
                continue
            events.append(
                {
                    "kind": "adhoc-processing",
                    "record": item.record_id,
                    "line": call.line,
                    "timestamp": call.timestamp,
                    "tool": call.tool,
                    "text": _clip(" ".join(call.text.split()), _BUNDLE_BODY_LENGTH),
                }
            )
    return events


def _is_adhoc_processing(tool: str, command: str) -> bool:
    """シェル呼び出しが、その場のコードによる加工に当たるかを判定する。

    対象はシェルのコマンドを実行するツール（Claude Codeの`Bash`、Codexのコマンド実行）の呼び出しとし、
    終了コードによらず成功した呼び出しも含む。次のいずれかに当たるコマンドを加工とする。

    - インタプリタへその場で書いたコードを渡す実行: `python`・`python3`・`uv run … python`への`-c`の引数または
      ヒアドキュメントの標準入力、managed-tempへ書いたスクリプトファイルの`python`・`uv run`による実行、`node -e`
    - 保存されたツール出力の後加工: `atk`が標準出力の代わりに示す保存先（`atk-output-`を含むパス）、
      `atk agents wait`の結果ファイル（`agents-wait-`を含むパス）、ホストが退避したツール結果（`/tool-results/`）と
      バックグラウンドタスクの出力（`/tasks/`配下の`.output`）を、`jq`・`awk`・`cut`・`sed`・Pythonで加工する呼び出し

    リポジトリのファイルや保存出力を`cat`・`rg`で読むだけの呼び出しと、保存出力を含まないパイプラインの
    `cut`・`sed`（検索結果の整形など）は含めない。
    """
    leaf = tool.casefold().rsplit("__", maxsplit=1)[-1].rsplit(".", maxsplit=1)[-1]
    if leaf not in _ADHOC_SHELL_TOOLS:
        return False
    if _ADHOC_INLINE_CODE.search(command):
        return True
    return bool(_ADHOC_SAVED_OUTPUT.search(command) and _ADHOC_OUTPUT_PROCESSOR.search(command))


def _candidate_events(
    timeline: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    hook_notices: list[dict[str, Any]],
    *,
    main_record_id: str = "main",
    adhoc_processing: list[dict[str, Any]] | None = None,
    wi_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """決定的に除外できる入力を省き、同種の候補を全位置付きで集約する。

    母集団はhook通知、ユーザー介入、ユーザー確認、WIの記入欄の是正、失敗したツール実行、警告、工程の返却値
    およびその場のコードによる加工とする。
    ユーザー確認（`confirmation-request`）は、提示した選択肢をそのまま選んだ確認への回答と、
    対象セッションが投入したUWI（`wi_events`のうち`submitted-uwi`）とする。確認が必要だったかは振り返りが判定するため、
    回答の形からは除外しない。WIの記入欄の是正（`wi-user-response`）は`_wi_candidate_events`が定める。
    返却値を含めるのは、本文に誤りがある委譲結果が他の事象には現れず、本文の判定前に候補集合に含まれなくなるためである。
    正常な完了だけを示し、想定外事象を持たない返却は、判定すべき本文を持たないため除外する。
    その場のコードによる加工（`adhoc-processing`）は成功した呼び出しも含み、記録ごとに1件の候補へまとめ、
    呼び出しごとの記録位置と代表入力を`calls`へ保持する。判定条件は`_is_adhoc_processing`が定める。

    同じ位置の同一hook発火は構造化されたhook通知を代表とする。それ以外は、同じ位置でも候補種別またはhookタグが異なる事象を別候補として保持する。同じ位置、候補種別およびhookタグの
    組だけを重複として除外する。`permission-denial`は`failed-tool`の一部でもあるため、同じ位置の
    `tool-failure`も保持し、許可ルールと実行失敗の双方の見直しへ対応付ける。

    候補件数の削減は、正規化した本文での集約と、恒久対策の要否が記録の構造から定まる事象の除外だけで行う。
    除外するのは、ユーザー介入ではない入力、正常な完了だけの委譲返却、検索の一致0件などの正常な否定結果、
    および起草者が保存前に処置するWI本文の表記診断の警告である。
    hookの標識を持つツール失敗と、hook通知と同じ本文の警告は、hook通知として発生源別の上限の対象にする。
    上限は発生源と区分の組ごとに適用し、フック名のツール部分ごとに最多の種類を残して、件数の少ないツールの通知も候補に残す。
    それ以外に件数上限を設けない。振り返りの契約は、候補が保持する位置の集合と
    判定表の位置の集合の一致を求めるため、位置を失う削減はこの判定と両立しない。
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    seen: set[tuple[str, int, str, str]] = set()
    excluded: collections.Counter[str] = collections.Counter()
    notices_by_locator: dict[tuple[str, int], list[dict[str, Any]]] = collections.defaultdict(list)
    for notice in hook_notices:
        if (
            notice.get("kind") == "hook-notice"
            and isinstance(notice.get("record"), str)
            and isinstance(notice.get("line"), int)
        ):
            notices_by_locator[(notice["record"], notice["line"])].append(notice)
    hook_notice_origins = [
        notice
        for notice in hook_notices
        if notice.get("kind") == "hook-notice" and notice.get("tag") in _HOOK_NOTICE_CANDIDATE_TAGS
    ]
    initial_skill_request, initial_skill_body = _initial_skill_input_locators(timeline, main_record_id=main_record_id)
    shell_records, resumed_records = _delegation_record_kinds(timeline, main_record_id=main_record_id)
    first_main_user = _initial_request_locator(timeline, main_record_id=main_record_id)
    sources = (
        ("hook-notice", (event for event in hook_notices if event.get("kind") == "hook-notice")),
        ("user-intervention", (event for event in timeline if event.get("kind") == "user")),
        ("permission-denial", (event for event in timeline if _is_permission_denial(event))),
        (
            "command-failure",
            (event for event in timeline if event.get("kind") == "failed-tool" and event.get("tool") == "CommandExecution"),
        ),
        (
            "tool-failure",
            (event for event in timeline if event.get("kind") == "failed-tool" and event.get("tool") != "CommandExecution"),
        ),
        ("warning", (event for event in warnings if event.get("kind") == "warning")),
        ("escalation", (event for event in timeline if _is_escalation_return(event))),
        (
            "delegate-return",
            (event for event in timeline if _is_delegate_return(event, main_record_id=main_record_id)),
        ),
        ("adhoc-processing", iter(adhoc_processing or ())),
        ("confirmation-request", (event for event in wi_events or () if event.get("kind") == "submitted-uwi")),
        ("wi-user-response", (event for event in wi_events or () if event.get("kind") == "wi-user-response")),
    )
    for candidate_kind, events in sources:
        for event in events:
            event = dict(event)
            record = event.get("record")
            line = event.get("line")
            if not isinstance(record, str) or not isinstance(line, int):
                continue
            identity = (record, line, candidate_kind, str(event.get("tag", "")))
            if identity in seen:
                excluded["duplicate-candidate"] += 1
                continue
            seen.add(identity)
            text = event.get("text")
            normalized_text = " ".join(text.split()) if isinstance(text, str) else ""
            if candidate_kind in {"warning", "tool-failure"} and any(
                _same_hook_event(candidate_kind, event, notice) for notice in notices_by_locator.get((record, line), [])
            ):
                excluded["hook-notice-represented"] += 1
                continue
            # 除外の判定は終了コードを帰属させるコマンドで行い、候補の署名と表示は記録どおりのコマンドで行う。
            attributed = _event_without_cd_prefix(event) if candidate_kind in {"command-failure", "tool-failure"} else event
            if candidate_kind == "tool-failure" and _is_normal_negative_tool_failure(attributed):
                excluded["normal-negative-result"] += 1
                continue
            if candidate_kind in {"command-failure", "tool-failure"} and _is_normal_atk_no_match(attributed):
                excluded["normal-negative-result"] += 1
                continue
            if candidate_kind in {"command-failure", "tool-failure"} and _is_normal_nonterminal_result(attributed):
                excluded["normal-nonterminal-result"] += 1
                continue
            if candidate_kind == "delegate-return" and _is_normal_delegate_return(
                event, shell=record in shell_records, resumed=record in resumed_records
            ):
                excluded["normal-delegate-return"] += 1
                continue
            if candidate_kind == "warning" and _style_diagnostics.is_body_style_diagnostic_warning(normalized_text):
                excluded["wi-style-diagnostic"] += 1
                continue
            event_kind = candidate_kind
            if candidate_kind in {"warning", "tool-failure"}:
                hook_event = _hook_originated_event(candidate_kind, event, hook_notice_origins)
                if hook_event is not None:
                    event_kind, event = "hook-notice", hook_event
                    normalized_text = " ".join(str(event["text"]).split())
            if candidate_kind == "user-intervention":
                exclusion = _user_candidate_exclusion(
                    event,
                    record,
                    line,
                    first_main_user,
                    initial_skill_request=initial_skill_request,
                    initial_skill_body=initial_skill_body,
                    main_record_id=main_record_id,
                )
                if exclusion == "question-answer":
                    event_kind = "confirmation-request"
                elif exclusion is not None:
                    excluded[exclusion] += 1
                    continue
            if candidate_kind == "command-failure" and _is_help_command_failure(event):
                excluded["command-help"] += 1
                continue
            if candidate_kind == "command-failure" and _is_normal_negative_result(attributed):
                excluded["normal-negative-result"] += 1
                continue
            if event_kind == "tool-failure" and _TRANSIENT_CLASSIFIER_ERROR in normalized_text:
                excluded["runtime-transient"] += 1
                continue
            if event_kind in {"command-failure", "tool-failure"} and _is_check_detected(attributed):
                excluded["check-detected"] += 1
                continue
            if event_kind == "hook-notice":
                exclusion = _hook_notice_candidate_exclusion(event.get("tag"))
                if exclusion is not None:
                    excluded[exclusion] += 1
                    continue
                if _is_response_language_notice(event):
                    excluded["response-language-notice"] += 1
                    continue
            key = _candidate_key(event_kind, event, normalized_text)
            if event_kind in {"command-failure", "tool-failure"}:
                event["failure_signature"], event["failure_summary"] = _failure_signature(event_kind, event)
            groups.setdefault(key, []).append(event)

    selected_groups: list[tuple[tuple[str, ...], list[dict[str, Any]], int, int]] = []
    bounded_hook_groups: dict[tuple[str, ...], list[tuple[tuple[str, ...], list[dict[str, Any]]]]] = {}
    for key, events in groups.items():
        if _is_bounded_hook_group(key):
            bounded_hook_groups.setdefault((key[1], key[3]), []).append((key, events))
        else:
            selected_groups.append((key, events, len(events), 0))
    for variants in bounded_hook_groups.values():
        ranked = sorted(variants, key=lambda item: (-len(item[1]), item[0]))
        kept = _bounded_hook_variant_indexes(ranked)
        for index, (key, events) in enumerate(ranked):
            if index not in kept:
                excluded["hook-notice-detail-budget"] += len(events)
                continue
            representative = min(events, key=lambda event: (str(event["record"]), int(event["line"])))
            omitted_count = len(events) - 1
            # Counterは0の加算でもキーを生成するため、省略が無い種類では加算せず、値0の区分を除外件数へ残さない。
            if omitted_count:
                excluded["hook-notice-detail-budget"] += omitted_count
            selected_groups.append((key, [representative], len(events), omitted_count))

    candidates: list[dict[str, Any]] = []
    included_locators: list[dict[str, Any]] = []
    included_locator_keys: set[tuple[str, int]] = set()
    for index, (key, events, occurrence_count, omitted_locator_count) in enumerate(
        sorted(selected_groups, key=lambda item: item[0]),
        start=1,
    ):
        locators: list[dict[str, Any]] = [{"record": str(event["record"]), "line": int(event["line"])} for event in events]
        locators.sort(key=lambda locator: (str(locator["record"]), int(locator["line"])))
        for locator in locators:
            locator_key = (str(locator["record"]), int(locator["line"]))
            if locator_key not in included_locator_keys:
                included_locator_keys.add(locator_key)
                included_locators.append(locator)
        candidate: dict[str, Any] = {
            "kind": "candidate",
            "candidate_id": f"c{index:04d}",
            "candidate_kind": key[0],
            "analysis_group_hint": list(
                key[1:] if key[0] in {"escalation", "hook-notice", "tool-failure"} else key[1:-1] if len(key) > 2 else key[1:]
            ),
            "event_key": list(key[1:]),
            "count": len(locators),
            "locators": locators,
        }
        text = events[0].get("text")
        if isinstance(text, str):
            candidate["text"] = text
        for field in _CANDIDATE_CONTEXT_FIELDS:
            if field in events[0]:
                candidate[field] = events[0][field]
        if key and key[0] in {"command-failure", "tool-failure"}:
            candidate["failure_signature"] = events[0]["failure_signature"]
            candidate["failure_summary"] = events[0]["failure_summary"]
        if _is_bounded_hook_group(key):
            candidate["occurrence_count"] = occurrence_count
            candidate["omitted_locator_count"] = omitted_locator_count
        if key and key[0] == "adhoc-processing":
            ordered = sorted(events, key=lambda event: (str(event["record"]), int(event["line"])))
            candidate["calls"] = [
                {"record": str(event["record"]), "line": int(event["line"]), "text": str(event.get("text", ""))}
                for event in ordered
            ]
        candidates.append(candidate)
    included_locators.sort(key=lambda locator: (locator["record"], locator["line"]))
    return [
        *candidates,
        {
            "kind": "candidate-summary",
            "count": len(candidates),
            "included_locator_count": len(included_locators),
            "included_locators": included_locators,
            "excluded": dict(sorted(excluded.items())),
        },
    ]


_CANDIDATE_EVIDENCE_DIRNAME = "candidate-evidence"
"""候補ごとの証拠を保存するbundle直下のディレクトリ名。"""


def _write_candidate_evidence_files(directory: Path, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """候補ごとの証拠を個別ファイルへ保存し、索引の行を返す。

    完全分析は候補単位で行うため、証拠の保存単位も候補単位とする。
    1ファイルへ集約すると、消費側は全候補の長文証拠を読み込むか、
    出力上限に達した後で範囲を指定して取得し直すことになる。
    索引は候補ID、証拠件数、個別ファイルの相対パスおよび総文字数を持ち、
    一次選別で除外した候補の本文を読まずに完全分析の対象を選べるようにする。
    """
    evidence_dir = directory / _CANDIDATE_EVIDENCE_DIRNAME
    evidence_dir.mkdir(exist_ok=True)
    index: list[dict[str, Any]] = []
    for item in evidence:
        candidate_id = str(item["candidate_id"])
        body = json.dumps(item, ensure_ascii=False)
        (evidence_dir / f"{candidate_id}.json").write_text(f"{body}\n", encoding="utf-8")
        index.append(
            {
                "kind": "candidate-evidence-index",
                "candidate_id": candidate_id,
                "analysis_group_hint": item["analysis_group_hint"],
                "locators": item["locators"],
                "evidence_count": len(item["events"]),
                "path": f"{_CANDIDATE_EVIDENCE_DIRNAME}/{candidate_id}.json",
                "total_chars": len(body),
            }
        )
    return index


def _candidate_evidence_events(
    collected: list[_CollectedRecord],
    candidates: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    hook_notices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """候補ごとに完全分析へ必要な位置付き証拠を有界な本文で束ねる。"""
    raw_entries = {(record.record_id, entry.line): entry.entry for record in collected for entry in record.records}
    indexed: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for event in (*timeline, *warnings, *hook_notices):
        record, line = event.get("record"), event.get("line")
        if isinstance(record, str) and isinstance(line, int):
            indexed.setdefault((record, line), []).append(event)
    tool_uses = _tool_use_index(collected)
    evidence: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("kind") != "candidate":
            continue
        untruncated = candidate["candidate_kind"] in UNTRUNCATED_EVIDENCE_KINDS
        text_limit = sys.maxsize if untruncated else _CANDIDATE_EVIDENCE_LENGTH
        budget = _DetailBudget(text_limit)
        details: list[dict[str, Any]] = []
        source_chars = 0
        for locator in candidate["locators"]:
            key = (str(locator["record"]), int(locator["line"]))
            raw_entry = raw_entries.get(key)
            if raw_entry is not None:
                source_chars += len(json.dumps(raw_entry, ensure_ascii=False))
                details.extend(
                    _clip_structure({"record": key[0], **event}, budget)
                    for event in _entry_detail_events(key[1], raw_entry, limit=text_limit)
                )
                if candidate["candidate_kind"] in _TOOL_USE_EVIDENCE_KINDS:
                    for call_id in _evidence_call_ids(raw_entry):
                        tool_use = tool_uses.get((key[0], call_id))
                        if tool_use is not None:
                            details.append(_clip_structure({"record": key[0], **tool_use}, budget))
            else:
                for event in indexed.get(key, ()):  # 同一位置の別走査結果も保持する。
                    detail = {"kind": str(event.get("kind", "")), "record": key[0], "line": key[1]}
                    for field in ("tool", "hook", "hook_name", "tag"):
                        if field in event:
                            detail[field] = event[field]
                    if isinstance(event.get("text"), str):
                        source_chars += len(event["text"])
                        detail["text"] = budget.clip(event["text"])
                    details.append(detail)
        if not details:
            details.append(
                {
                    "kind": str(candidate["candidate_kind"]),
                    "record": str(candidate["locators"][0]["record"]),
                    "line": int(candidate["locators"][0]["line"]),
                    "text": budget.clip(str(candidate.get("text", ""))),
                }
            )
        item = {
            "kind": "candidate-evidence",
            "candidate_id": candidate["candidate_id"],
            "analysis_group_hint": candidate["analysis_group_hint"],
            "locators": candidate["locators"],
            "source_chars": source_chars,
            "text_limit": None if untruncated else _CANDIDATE_EVIDENCE_LENGTH,
            "events": details,
        }
        if budget.omitted:
            item["omitted"] = True
        evidence.append(item)
    return evidence


def _tool_use_index(collected: list[_CollectedRecord]) -> dict[tuple[str, str], dict[str, Any]]:
    """記録ごとに、呼び出し識別子からツール呼び出しの入力と記録位置を引ける索引を作成する。

    hook通知と失敗したツール結果は呼び出し識別子だけを持ち、対象のコマンドは別の行にある。
    Claude Codeは`tool_use`ブロックの`id`、Codexは`function_call`等の`call_id`で対応付ける。
    """
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in collected:
        for record in item.records:
            message = record.entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                        index[(item.record_id, block["id"])] = {
                            "kind": "tool-use",
                            "line": record.line,
                            "timestamp": _entry_timestamp(record.entry),
                            "name": str(block.get("name", "")),
                            "input": block.get("input"),
                        }
            payload = record.entry.get("payload")
            if (
                isinstance(payload, dict)
                and payload.get("type") in {"function_call", "custom_tool_call"}
                and isinstance(payload.get("call_id"), str)
            ):
                index[(item.record_id, payload["call_id"])] = {
                    "kind": "tool-use",
                    "line": record.line,
                    "timestamp": _entry_timestamp(record.entry),
                    "name": str(payload.get("name", "")),
                    "input": payload.get("arguments", payload.get("input")),
                }
    return index


def _evidence_call_ids(entry: dict[str, Any]) -> list[str]:
    """候補の記録行が参照する呼び出し識別子を出現順に重複なく返す。"""
    call_ids: list[str] = []
    for hook_record in _hook_records(entry):
        tool_use_id = hook_record.get("toolUseID")
        if isinstance(tool_use_id, str) and tool_use_id not in call_ids:
            call_ids.append(tool_use_id)
    for call_id in sorted(_result_call_ids(entry)):
        if call_id not in call_ids:
            call_ids.append(call_id)
    return call_ids


def _is_permission_denial(event: dict[str, Any]) -> bool:
    """Auto mode classifierの拒否で終わったツール実行であるかを返す。

    拒否は`is_error`のtool_resultとして記録されるため`failed-tool`の一部に当たる。
    許可ルールの見直しという他の失敗とは別の是正へ結び付くため、独立した候補種別として扱う。
    """
    if event.get("kind") != "failed-tool":
        return False
    text = event.get("text")
    return isinstance(text, str) and _PERMISSION_DENIAL_MARKER in text


def _is_delegate_return(event: dict[str, Any], *, main_record_id: str = "main") -> bool:
    """委譲先の空でない最終返却のうち、明示的なエスカレーション以外を返す。

    `final-result`は記録ごとの最後の非commentaryのアシスタントイベントであり、
    委譲先の記録ではその委譲先が委譲元へ返した返却値に対応する。
    成功の定型形式だけの返却の除外は、`_is_normal_delegate_return`が候補の集約時に行う。
    メイン記録の最終出力は委譲返却ではない。明示的なエスカレーションは独立した候補へ送る。
    """
    if event.get("kind") != "final-result" or event.get("record") == main_record_id:
        return False
    text = event.get("text")
    return isinstance(text, str) and bool(text.strip()) and not _is_escalation_return(event)


def _is_escalation_return(event: dict[str, Any]) -> bool:
    """上位判断を要求する明示的な最終返却であるかを返す。

    値を持つ`続行できない理由:`の行を持つ返却と、旧形式の状態値で続行不能を示した返却を対象とする。
    """
    if event.get("kind") != "final-result":
        return False
    text = event.get("text")
    if not isinstance(text, str):
        return False
    lines = [line.strip() for line in text.splitlines()]
    if any(
        line.startswith(_ESCALATION_REASON_PREFIX)
        and line.removeprefix(_ESCALATION_REASON_PREFIX).strip() not in _NO_ESCALATION_REASONS
        for line in lines
    ):
        return True
    return any(
        line.removeprefix(prefix).strip() == _ESCALATION_RETURN_STATUS
        for line in lines
        for prefix in _RETURN_STATUS_PREFIXES
        if line.startswith(prefix)
    )


def _hook_notice_candidate_exclusion(tag: Any) -> str | None:
    """是正を求めない区分のhook通知を問題候補から除く場合に、除外の種別名を返す。

    この判定は、是正を求める通知が`block`または`warn`の区分を必ず持つという前提へ依存する。
    残す区分を列挙するのは、規範の注入や配送の種別のように是正の要否と無関係な値を`kind`へ持つ出力が
    今後追加されても、既知値の列挙に含まれず是正要求として扱われないことを防ぐためである。
    区分を持たない通知は、区分を示す必要が無い通知として発行されるため情報提示と同じ扱いとする。
    """
    if tag in _HOOK_NOTICE_CANDIDATE_TAGS:
        return None
    if tag in {"info", "notice"}:
        return "hook-notice-informational"
    if tag is None:
        return "hook-notice-untagged"
    return "hook-notice-context"


def _is_response_language_notice(event: dict[str, Any]) -> bool:
    """応答言語hookの警告を表すhook通知かを返す。

    応答言語hookは直前の応答を検出後に通知するだけの遮断後に対処する型であり、
    振り返りのたびに同じ見送り判定になるため、問題候補から除いて件数だけを数える。
    同じ`pretooluse`の`warn`通知には他の判定処理が返す通知も含まれるため、発生源と区分に加えて本文の先頭を
    hook側の本文定数と比べる。定数を参照するのは、hook側の文言を変えたときに判定を追随させるためである。
    """
    if event.get("hook") != "pretooluse" or event.get("tag") != "warn":
        return False
    text = event.get("text")
    if not isinstance(text, str) or not text:
        return False
    for body in (_response_language_check.WARNING_BODY, _response_language_check.BLOCK_BODY):
        expected = _hook_notice_kind_text(body)
        length = min(len(text), len(expected))
        if text[:length] == expected[:length]:
            return True
    return False


def _initial_request_locator(timeline: list[dict[str, Any]], *, main_record_id: str = "main") -> tuple[str, int] | None:
    """メイン記録の初期要求の位置を返す。

    人間の依頼で始まるセッションでは、最初の人間の発話を初期要求とする。
    `atk wi process-loop`が自動起動したセッションでは、起動の目的文を持つ入力を初期要求とし、
    その後の人間の発話は作業中の介入として候補に残す。起動の目的文は記録で前置きが付くため、包含で判定する。
    """
    for event in timeline:
        line = event.get("line")
        text = event.get("text")
        if event.get("kind") != "user" or event.get("record") != main_record_id or not isinstance(line, int):
            continue
        if not isinstance(text, str):
            continue
        if _PROCESS_WI_GOAL_BODY in text:
            return (main_record_id, line)
        if _user_candidate_exclusion(event, main_record_id, line, None, main_record_id=main_record_id) is None:
            return (main_record_id, line)
    return None


def _user_candidate_exclusion(
    event: dict[str, Any],
    record: str,
    line: int,
    first_main_user: tuple[str, int] | None,
    *,
    initial_skill_request: tuple[str, int] | None = None,
    initial_skill_body: tuple[str, int] | None = None,
    main_record_id: str = "main",
) -> str | None:
    """構造と固定接頭辞だけでユーザー介入ではない入力を分類する。

    接頭辞は、実行環境がユーザーのメッセージへ挿入する本文、process-loopの通知、および定時promptの
    先頭に現れる固定文字列を実記録から採取したものとする。これらはユーザーの発話ではないため、
    残すとユーザー介入の候補が実際の介入件数を超える。
    接頭辞を持たない実行環境の生成は本文の形からは判別できないため、`_is_runtime_generated`が
    付けた標識で分類する。
    確認への回答は、生成側が付ける`user_response`の構造で判定し、表示用の本文の書式からは推定しない。
    本文の書式で判定すると、書式の変更で選択肢どおりの回答が候補に残り、同じ書式の通常の発話が回答として除外される。
    回答のうち`answer_intervention`を持つものは、選択肢をそのまま選んだ回答ではなく
    従来の判断を是正した介入であるため、除外せず候補として残す。
    選択肢をそのまま選んだ回答へ返す`question-answer`はユーザー介入ではない区分であり、
    呼び出し元はその回答を除外せず、確認の要否を振り返る`confirmation-request`の候補とする。
    """
    if record != main_record_id:
        return "delegated-record"
    if event.get("runtime_generated") is True:
        return "runtime-meta"
    if initial_skill_request == (record, line):
        return "initial-skill-request"
    if initial_skill_body == (record, line):
        return "initial-skill-body"
    if _generated_user_event(event):
        return "runtime-inserted"
    if isinstance(event.get("user_response"), list):
        return None if event.get("answer_intervention") is True else "question-answer"
    if first_main_user == (record, line):
        return "initial-request"
    return None


def _initial_skill_input_locators(
    timeline: list[dict[str, Any]],
    *,
    main_record_id: str = "main",
) -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
    """Codexの先頭スキル要求と、直後に挿入された対応本文の位置を返す。"""
    main_users = [
        event
        for event in timeline
        if event.get("kind") == "user"
        and event.get("record") == main_record_id
        and isinstance(event.get("line"), int)
        and isinstance(event.get("text"), str)
        and event.get("runtime_generated") is not True
    ]
    request_index = next(
        (
            index
            for index, event in enumerate(main_users)
            if _user_candidate_exclusion(event, main_record_id, int(event["line"]), None, main_record_id=main_record_id) is None
        ),
        None,
    )
    if request_index is None:
        return None, None
    request = main_users[request_index]
    request_text = str(request["text"]).strip()
    if not request_text.startswith("$") or any(character.isspace() for character in request_text):
        return None, None
    skill_name = request_text.removeprefix("$")
    if not skill_name or any(not (character.isalnum() or character in {"-", "_", ":"}) for character in skill_name):
        return None, None
    if request_index + 1 >= len(main_users):
        return None, None
    body = main_users[request_index + 1]
    body_text = str(body["text"]).lstrip()
    if not body_text.startswith("<skill>") or f"<name>{skill_name}</name>" not in body_text:
        return None, None
    return (main_record_id, int(request["line"])), (main_record_id, int(body["line"]))


def _is_help_command_failure(event: dict[str, Any]) -> bool:
    """変更処理へ到達せずusageを返した明示的なCLIヘルプ取得であるかを返す。"""
    command_value = event.get("command")
    diagnostic = event.get("diagnostic")
    if not isinstance(command_value, str) or not isinstance(diagnostic, str):
        return False
    try:
        command = json.loads(command_value)
    except json.JSONDecodeError:
        return False
    if not isinstance(command, list) or not all(isinstance(argument, str) for argument in command):
        return False
    if not any(argument in {"-h", "--help"} for argument in command[1:]):
        return False
    normalized = diagnostic.casefold()
    return "usage:" in normalized or "options:" in normalized


_CD_PREFIX_SEPARATORS = frozenset({";", "&&"})


def _event_without_cd_prefix(event: dict[str, Any]) -> dict[str, Any]:
    """終了コードを帰属させるコマンドを求めるため、先頭の`cd <パス>;`か`cd <パス> &&`を外したイベントを返す。

    作業ディレクトリを移すだけの前置は、後続の単独のコマンドが返す終了コードと出力を変えない。
    除外判定（`_is_normal_atk_no_match`・`_is_normal_nonterminal_result`・`_is_normal_negative_result`・
    `_is_normal_negative_tool_failure`・`_is_check_detected`）は、このイベントを前置の無い単独のコマンドとして判定する。
    外すのは引数1語の`cd`の前置1つだけとし、オプション付きの`cd`、引数の無い`cd`、`|`や改行による前置、
    2つ目以降の前置は外さない。出力（Bashは`text`、`CommandExecution`は`diagnostic`）に`cd:`を含む行がある場合は、
    `cd`が失敗して後続のコマンドが意図した場所で動いていないため、前置を外さず記録どおりのイベントを返す。
    """
    output = event.get("diagnostic") if event.get("tool") == "CommandExecution" else event.get("text")
    if isinstance(output, str) and any("cd:" in line or "Set-Location" in line for line in output.splitlines()):
        return event
    event = _event_without_powershell_wrapper(event)
    result = dict(event)
    operation = _json_object(str(event.get("operation", "")))
    if operation is not None and isinstance(operation.get("command"), str):
        stripped = _command_without_cd_prefix(operation["command"])
        if stripped is not None:
            result["operation"] = json.dumps({**operation, "command": stripped}, ensure_ascii=False, sort_keys=True)
    for key in ("command", "command_full"):
        try:
            args = json.loads(str(event.get(key, "")))
        except json.JSONDecodeError:
            continue
        if (
            not isinstance(args, list)
            or len(args) < 3
            or not all(isinstance(arg, str) for arg in args)
            or _basename(args[0]) not in _SHELL_NAMES
            or args[1] not in {"-c", "-lc"}
        ):
            continue
        stripped = _command_without_cd_prefix(args[2])
        if stripped is not None:
            result[key] = json.dumps([*args[:2], stripped, *args[3:]], ensure_ascii=False)
    return _event_without_run_command_wrapper(result)


def _event_with_command(event: dict[str, Any], args: list[str]) -> dict[str, Any]:
    """判定用の引数だけを差し替え、元イベントは呼び出し側に保持する。"""
    result = {**event, "command": json.dumps(args, ensure_ascii=False), "command_full": json.dumps(args, ensure_ascii=False)}
    operation = _json_object(str(event.get("operation", "")))
    if operation is not None:
        result["operation"] = json.dumps({**operation, "command": shlex.join(args)}, ensure_ascii=False)
    return result


def _event_without_powershell_wrapper(event: dict[str, Any]) -> dict[str, Any]:
    """確定できる単独PowerShellコマンドだけを既存分類へ渡す。"""
    args = _event_command_args(event)
    if args and _basename(args[0]) in _SHELL_NAMES and len(args) == 3 and args[1] in {"-c", "-lc"}:
        tokens = _shell_command_tokens(args[2])
        args = [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else []
    inner = _powershell_inner_args(args)
    return _event_with_command(event, inner) if inner else event


def _event_command_args(event: dict[str, Any]) -> list[str]:
    raw = event.get("command_full", event.get("command"))
    if isinstance(raw, str):
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return args if isinstance(args, list) and all(isinstance(arg, str) for arg in args) else []
    operation = _json_object(str(event.get("operation", "")))
    command = operation.get("command") if operation else None
    tokens = _shell_command_tokens(command) if isinstance(command, str) else None
    return [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else []


def _powershell_inner_args(args: list[str]) -> list[str] | None:
    """文字列Commandだけを解析する。PowerShell 7.5の引用規則を使い、展開や式は解釈しない。

    典拠: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_quoting_rules
    """
    if not args or args[0].replace("\\", "/").rsplit("/", 1)[-1].casefold() not in {
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }:
        return None
    index = 1
    while index < len(args):
        option = args[index].casefold()
        if option == "-command":
            break
        if option in {"-noprofile", "-nologo", "-noninteractive", "-sta", "-mta"}:
            index += 1
        elif option in {"-executionpolicy", "-windowstyle", "-inputformat", "-outputformat"} and index + 1 < len(args):
            index += 2
        else:
            return None
    if index + 2 != len(args) or args[index + 1] == "-":
        return None
    tokens = _powershell_command_tokens(args[index + 1])
    if (
        tokens
        and len(tokens) >= 4
        and tokens[0] == _ShellToken("cd", False)
        and not tokens[1].operator
        and not tokens[1].value.startswith("-")
        and tokens[2] in {_ShellToken(";", True), _ShellToken("&&", True)}
    ):
        tokens = tokens[3:]
    return [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else None


def _powershell_command_tokens(command: str) -> list[_ShellToken] | None:
    """引用された語と演算子を分ける。確定できないPowerShell式は候補へ残す。"""
    tokens: list[_ShellToken] = []
    word: list[str] = []
    quote = ""
    started = False
    index = 0
    while index < len(command):
        char = command[index]
        following = command[index + 1] if index + 1 < len(command) else ""
        if quote and char == quote:
            if following == quote:
                word.append(char)
                index += 2
                continue
            quote = ""
        elif char == "`" and quote != "'":
            if not following or following in "\r\n":
                return None
            word.append(
                {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}.get(
                    following, following
                )
            )
            index += 2
            started = True
            continue
        elif char in "‘’“”" or char == "$" and quote != "'" or not quote and char in "{}()@#,":
            return None
        elif not quote and char in "'\"":
            quote = char
        elif not quote and char in ";|&<>\r\n":
            if started:
                tokens.append(_ShellToken("".join(word), False))
                word, started = [], False
            operator = char + following if following == char else char
            tokens.append(_ShellToken(operator, True))
            index += len(operator)
            continue
        elif not quote and char.isspace():
            if started:
                tokens.append(_ShellToken("".join(word), False))
                word, started = [], False
            index += 1
            continue
        else:
            word.append(char)
        started = True
        index += 1
    if quote:
        return None
    if started:
        tokens.append(_ShellToken("".join(word), False))
    return tokens


def _event_without_run_command_wrapper(event: dict[str, Any]) -> dict[str, Any]:
    """保存済み子結果の正常な包装だけを子コマンドへ帰属させる。"""
    args = _event_command_args(event)
    if args and _basename(args[0]) in _SHELL_NAMES and len(args) == 3 and args[1] in {"-c", "-lc"}:
        tokens = _shell_command_tokens(args[2])
        args = [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else []
    if len(args) < 4 or _basename(args[0]) != "atk" or args[1] != "run-command" or "--" not in args[2:]:
        return event
    text = str(event.get("diagnostic") or event.get("text") or "")
    results = [_json_object(line) for line in text.splitlines() if line.startswith("{")]
    if len(results) != 1 or results[0] is None:
        return event
    result = results[0]
    child = result.get("argv")
    code = result.get("child_exit_code")
    if (
        not isinstance(child, list)
        or not child
        or not all(isinstance(arg, str) for arg in child)
        or not isinstance(code, int)
        or isinstance(code, bool)
        or code != _failure_exit_code(event)
        or result.get("timed_out") is not False
        or "signal" not in result
        or result["signal"] is not None
        or not isinstance(result.get("record_path"), str)
        or not result["record_path"]
        or any(
            not isinstance(result.get(key), int) or isinstance(result[key], bool) or result[key] < 0
            for key in ("stdout_bytes", "stderr_bytes")
        )
        or child != args[args.index("--") + 1 :]
    ):
        return event
    inner = _event_with_command(event, child)
    inner["exit_code"] = code
    # 検証の検出は出力を伴う。検索の否定では、子の診断が無いことをバイト数で保証する。
    if result["stdout_bytes"] or result["stderr_bytes"]:
        return inner if _is_check_detected(inner) else event
    inner["diagnostic"] = ""
    inner["text"] = f"Exit code {code}" if event.get("tool_name") == "Bash" else "CommandExecution failed"
    return inner


def _command_without_cd_prefix(command: str) -> str | None:
    """先頭の`cd <パス>;`か`cd <パス> &&`を外したコマンド文字列を返す。前置が無い場合は`None`を返す。

    残りの語は引用し直し、引用の外の演算子だけを演算子として残すため、連結の判定は元のコマンドと変わらない。
    """
    tokens = _shell_command_tokens(command)
    if tokens is None or len(tokens) < 4:
        return None
    first, target, separator, *rest = tokens
    if (
        first.operator
        or first.value != "cd"
        or target.operator
        or target.value.startswith("-")
        or not separator.operator
        or separator.value not in _CD_PREFIX_SEPARATORS
        or rest[0].operator
    ):
        return None
    return " ".join(token.value if token.operator else shlex.quote(token.value) for token in rest)


def _is_normal_nonterminal_result(event: dict[str, Any]) -> bool:
    """単独の`atk agents wait`が返す終了3を、正常な待機継続として区分する。

    先頭の`cd`の前置は、呼び出し側が`_event_without_cd_prefix`で外してから渡す。
    """
    if _failure_exit_code(event) != 3:
        return False
    executable, subcommand, command, args = _failure_command_parts(event)
    if executable != "atk" or subcommand != "agents" or args[1:3] != ["agents", "wait"]:
        return False
    tokens = _shell_command_tokens(command)
    # 連結全体の終了コードを待機へ帰属させず、同じコマンドによる継続だけを除外する。
    # 演算子形のデータを含む語も従来どおり除外の対象外とし、待機継続の除外範囲を広げない。
    if tokens is None:
        return False
    return not any(set(token.value) <= _SHELL_OPERATOR_CHARS for token in tokens)


def _is_normal_negative_result(event: dict[str, Any]) -> bool:
    """読取専用の述語が診断なしで偽を返した事象を区分する。"""
    exit_code = event.get("exit_code")
    if not isinstance(exit_code, int) or str(event.get("diagnostic", "")).strip():
        return False
    command_value = event.get("command")
    if not isinstance(command_value, str):
        return False
    try:
        args = json.loads(command_value)
    except json.JSONDecodeError:
        return False
    if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
        return False
    executable = Path(args[0]).name
    if executable in {"bash", "sh", "zsh"}:
        if len(args) < 3 or args[1] not in {"-c", "-lc"}:
            return False
        tokens = _shell_command_tokens(args[2])
        return tokens is not None and _is_negative_search_command(tokens, exit_code)
    return exit_code == 1 and _is_negative_predicate(args)


def _is_normal_atk_no_match(event: dict[str, Any]) -> bool:
    """単独のatkが結果行と終了1で表す該当0件を、生成側の契約から判定する。

    先頭の`cd`の前置は、呼び出し側が`_event_without_cd_prefix`で外してから渡す。
    """
    if event.get("tool") != "CommandExecution" and event.get("tool_name") != "Bash":
        return False
    if _failure_exit_code(event) != 1:
        return False
    executable, _subcommand, command, _args = _failure_command_parts(event)
    if executable != "atk":
        return False
    # 包装の外側の連結も、解除したshellの内部の連結も、終了コードをatkへ帰属できない。
    operation = _json_object(str(event.get("operation", "")))
    original_command = operation.get("command") if operation is not None else None
    commands = [command]
    if isinstance(original_command, str):
        commands.append(original_command)
    for source in commands:
        tokens = _shell_command_tokens(source)
        if tokens is None or any(token.operator for token in tokens):
            return False
    output = event.get("diagnostic") if event.get("tool") == "CommandExecution" else event.get("text")
    if not isinstance(output, str) or _OMISSION_MARK in output:
        return False
    lines = [line.lstrip() for line in output.splitlines()]
    return any(line.startswith(_outcome.NO_MATCH_PREFIX) for line in lines) and not any(
        line.startswith((_outcome.FAILURE_PREFIX, _outcome.WARNING_PREFIX)) for line in lines
    )


def _is_normal_negative_tool_failure(event: dict[str, Any]) -> bool:
    """Claude CodeのBashで、読取専用の述語または検索が出力なしで偽を返した事象を区分する。

    Claude Codeは出力の無い非0終了を`Exit code <N>`だけの失敗として記録する。
    `&&`の全段が読取専用の述語であれば、どの段が偽でも正常な否定結果とする。
    """
    matched = re.fullmatch(r"Exit code (\d+)", str(event.get("text", "")).strip())
    if event.get("tool_name") != "Bash" or matched is None:
        return False
    try:
        operation = json.loads(str(event.get("operation", "")))
    except json.JSONDecodeError:
        return False
    command = operation.get("command") if isinstance(operation, dict) else None
    if not isinstance(command, str):
        return False
    tokens = _shell_command_tokens(command)
    return tokens is not None and _is_negative_search_command(tokens, int(matched.group(1)))


class _ShellToken(NamedTuple):
    """シェルのコマンド文字列から得た語1件。`operator`は引用の外にある演算子であることを表す。"""

    value: str
    operator: bool


_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


def _shell_command_tokens(command: str) -> list[_ShellToken] | None:
    """シェルのコマンド文字列を、演算子を独立した要素とする語の列へ分解する。解釈できない場合は`None`を返す。

    引用とエスケープは`QuotingScanner`で判定し、引用の外にある演算子文字の連続だけを演算子とする。
    引用またはエスケープで渡された演算子形の文字はデータの語に残り、連結構造の判定に使わない。
    """
    scanner = _QuotingScanner(command)
    # 文字ごとの区分を集める。`None`は語の区切り、真偽値は引用の外の演算子文字かを表す。
    pieces: list[tuple[str, bool] | None] = []
    while scanner.index < len(command):
        char = command[scanner.index]
        quote_before = scanner.quote
        escaped_before = scanner.escaped
        if scanner.consume_quoted():
            if escaped_before:
                if quote_before == '"' and char not in _DOUBLE_QUOTE_ESCAPABLE:
                    pieces.append(("\\" + char, False))
                elif char != "\n":
                    pieces.append((char, False))
            elif scanner.escaped:
                pass
            elif quote_before is not None and scanner.quote is None:
                pieces.append(("", False))
            else:
                pieces.append((char, False))
            continue
        if char in {"'", '"'}:
            pieces.append(("", False))
            scanner.enter_quote(char)
            continue
        scanner.index += 1
        if char == "\n":
            pieces.append((char, True))
        elif char.isspace():
            pieces.append(None)
        elif char in _SHELL_OPERATOR_CHARS:
            pieces.append((char, True))
        elif char == "#" and (not pieces or pieces[-1] is None or pieces[-1][1]):
            break
        else:
            pieces.append((char, False))
    if scanner.quote is not None or scanner.escaped:
        return None
    tokens: list[_ShellToken] = []
    current: list[tuple[str, bool]] = []
    for piece in [*pieces, None]:
        operator = None if piece is None else piece[1]
        if current and operator != current[0][1]:
            tokens.append(_ShellToken("".join(text for text, _ in current), current[0][1]))
            current = []
        if piece is not None:
            current.append(piece)
    if not tokens:
        return None
    return tokens


def _is_negative_search_command(tokens: list[_ShellToken], exit_code: int) -> bool:
    """出力の無い非0終了が、検索の一致0件という正常な否定結果に当たるかを返す。

    `&&`で連結した全段が読取専用の述語であれば、終了コード1を正常な否定結果とする。
    それ以外の演算子はパイプ（`|`）だけを許し、パイプラインの終了コードを決める最終段で判定する。
    最終段が読取専用の述語で終了コード1、または最終段が検索を起動する`xargs`で終了コード123
    （起動したコマンドのいずれかが1から125で終わったことを表す）の場合を一致0件とする。
    """
    if _ShellToken("&&", True) in tokens:
        parts: list[list[str]] = [[]]
        for token in tokens:
            if token.operator and token.value == "&&":
                parts.append([])
            elif token.operator:
                return False
            else:
                parts[-1].append(token.value)
        return exit_code == 1 and all(part and _is_negative_predicate(part) for part in parts)
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token.operator:
            if token.value != "|":
                return False
            segments.append([])
        else:
            segments[-1].append(token.value)
    if any(not segment for segment in segments):
        return False
    last = segments[-1]
    if exit_code == 1:
        return _is_negative_predicate(last)
    if exit_code == 123 and Path(last[0]).name == "xargs":
        start = next(
            (index for index, token in enumerate(last[1:], start=1) if Path(token).name in {"rg", "grep", "git"}), None
        )
        return start is not None and _is_negative_predicate(last[start:])
    return False


def _is_negative_predicate(args: list[str]) -> bool:
    """引数列が、偽の結果を終了コード1で返す読取専用の述語であるかを返す。"""
    executable = Path(args[0]).name
    if executable in {"rg", "grep", "git-grep"}:
        return True
    if executable in {"test", "["}:
        return any(flag in args for flag in ("-e", "-f", "-d", "-L"))
    if executable == "command":
        return len(args) >= 3 and args[1] == "-v"
    if executable == "cmp":
        return "-s" in args or "--silent" in args
    if executable != "git":
        return False
    git_args = args[1:]
    while git_args:
        if git_args[0] in {"-C", "-c", "--git-dir", "--work-tree"} and len(git_args) >= 2:
            git_args = git_args[2:]
        elif git_args[0] == "--no-pager":
            git_args = git_args[1:]
        else:
            break
    if not git_args:
        return False
    if git_args[0] == "grep":
        return True
    if git_args[0] == "config":
        return any(option in git_args[1:] for option in ("--get", "--get-all", "--get-regexp"))
    return git_args[0] == "merge-base" and "--is-ancestor" in git_args[1:]


def _is_normal_delegate_return(event: dict[str, Any], *, shell: bool = False, resumed: bool = False) -> bool:
    """想定外事象や改善点を持たず、正常な完了だけを示す委譲返却であるかを返す。

    正常な完了は、`状態: completed`（旧形式の`status: completed`を含む）の行を持ち未解決の指摘が0件の返却、
    `<役割名>.subagent.md`が定める返却値で始まる返却、
    全ての判定が適合または合格の返却、およびコマンド実行の委譲（`shell`）で報告した終了コードが全て0で
    失敗・警告・診断の件数に1以上が無い返却とする。
    `状態: completed`の前に置いた前置きの文は、1回の配送で終えた委譲先に限って正常な完了に含める。
    再開された委譲先（`resumed`）の前置きは、受け取り済みの報告の返し直しのような異常を述べる場合があるためである。
    委譲先は想定外の事象を`想定外事象:`行で返すため、この行を持つ返却と、調査結果のような
    自由記述の返却は、本文の意味の判断を要するため候補に残す。
    改善点の標識行は、完了・適合・shellの各正常分岐より先に候補へ保持する。
    """
    text = event.get("text")
    if not isinstance(text, str):
        return False
    lines = [line.strip() for line in text.splitlines()]
    if any(line.startswith(_IMPROVEMENT_MARKER) for line in lines):
        return False
    if any(line.startswith(_UNEXPECTED_EVENT_PREFIXES) for line in lines):
        return False
    body = [line for line in lines if line and not line.startswith("```")]
    if not body:
        return False
    if body[0] in _COMPLETED_RETURN_LINES or (not _COMPLETED_RETURN_LINES.isdisjoint(body) and not resumed):
        unresolved = [line.partition(":")[2].strip() for line in body if line.startswith(_UNRESOLVED_RETURN_PREFIXES)]
        return all(value == "0" for value in unresolved)
    if body[0] in _DELEGATE_COMPLETION_VALUES:
        return True
    if shell:
        exit_codes = [int(match.group(1)) for match in _REPORTED_EXIT_CODE.finditer(text)]
        return bool(exit_codes) and all(code == 0 for code in exit_codes) and not _REPORTED_NONZERO_COUNT.search(text)
    verdicts = [match.group("value") for line in body if (match := _VERDICT_LINE.match(line)) is not None]
    if not verdicts or "不適合" in text or "不合格" in text:
        return False
    return all(value.startswith(("適合", "合格")) for value in verdicts)


def _delegation_record_kinds(timeline: list[dict[str, Any]], *, main_record_id: str = "main") -> tuple[set[str], set[str]]:
    """コマンド実行の委譲（`start`のshell）を受け取った委譲先と、再開された委譲先の記録IDを返す。

    実行環境が先に注入したユーザーロールの本文を除き、最初の配送本文で
    コマンド実行の委譲かを判定する。配送本文が2件以上ある記録を再開されたものとする。
    """
    user_texts: dict[str, list[str]] = collections.defaultdict(list)
    for event in timeline:
        record, text = event.get("record"), event.get("text")
        if (
            event.get("kind") == "user"
            and isinstance(record, str)
            and record != main_record_id
            and isinstance(text, str)
            and not text.startswith("# AGENTS.md instructions")
        ):
            user_texts[record].append(text)
    shell = {record for record, texts in user_texts.items() if texts and _SHELL_DELEGATION_MARKER in texts[0]}
    resumed = {record for record, texts in user_texts.items() if len(texts) >= 2}
    return shell, resumed


def _hook_originated_event(
    candidate_kind: str,
    event: dict[str, Any],
    hook_notices: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """hookが出力したと記録から判定できる失敗または警告を、hook通知の候補イベントへ変換して返す。

    ツール失敗は本文が`<フック名> hook error:`で始まり、通知の標識が`block`または`warn`の区分を持つ場合に変換する。
    警告は本文が同じセッションのhook通知の本文と一致する場合に、その通知の発生源と区分で変換する。
    変換しない場合は`None`を返す。
    """
    text = event.get("text")
    if not isinstance(text, str):
        return None
    if candidate_kind == "tool-failure":
        hook_name, separator, _ = text.partition(" hook error:")
        if not separator:
            return None
        body = _HOOK_FAILURE_PREFIX.sub("", text, count=1)
        for key in _hook_notice_keys(body, hook_name.strip() or None):
            if key.hook and key.tag in _HOOK_NOTICE_CANDIDATE_TAGS:
                return {
                    **event,
                    "kind": "hook-notice",
                    "text": key.kind_text,
                    "hook": key.hook,
                    "hook_name": key.hook_name,
                    "tag": key.tag,
                }
        return None
    normalized = _hook_notice_kind_text(text)
    for notice in hook_notices:
        notice_text = str(notice.get("text", ""))
        if _same_hook_notice_text(normalized, notice_text):
            return {
                **event,
                "kind": "hook-notice",
                "text": notice_text,
                "hook": notice.get("hook"),
                "hook_name": notice.get("hook_name"),
                "tag": notice.get("tag"),
            }
    return None


def _bounded_hook_variant_indexes(ranked: list[tuple[tuple[str, ...], list[dict[str, Any]]]]) -> set[int]:
    """発生源と区分が同じhook通知の種類のうち、候補に残す種類の順位を返す。

    発生件数の多い順に上限数まで残す。同じ通知の2件目以降は1件目の本文の要約で届き、別の種類になるため、
    残した種類と遮断理由が同じで本文が前方一致する種類は、同じ通知として上限を消費させずに省く。
    加えて、フック名のツール部分ごとに最多の種類を1件ずつ残し、件数の少ないツールの通知も
    他のツールの多数の通知とともに候補に残す。
    """
    kept: set[int] = set()
    kept_keys: list[tuple[str, ...]] = []
    seen_tools: set[str] = set()
    for index, (key, events) in enumerate(ranked):
        if any(key[5] == kept_key[5] and _same_hook_notice_text(key[4], kept_key[4]) for kept_key in kept_keys):
            continue
        tools = {str(event.get("hook_name") or "").partition(":")[2] for event in events}
        if len(kept) < _HOOK_NOTICE_VARIANT_LIMIT or not tools <= seen_tools:
            kept.add(index)
            kept_keys.append(key)
            seen_tools |= tools
    return kept


def _same_hook_notice_text(left: str, right: str) -> bool:
    """正規化したhook通知本文の一方が他方の前方部分であるかを返す。"""
    length = min(len(left), len(right))
    return length >= _HOOK_ORIGIN_MIN_MATCH_LENGTH and left[:length] == right[:length]


def _same_hook_event(candidate_kind: str, event: dict[str, Any], notice: dict[str, Any]) -> bool:
    """同じ記録位置の警告または失敗が構造化hook通知から生じたかを返す。"""
    text = event.get("text")
    notice_text = notice.get("text")
    if not isinstance(text, str) or not isinstance(notice_text, str):
        return False
    if candidate_kind == "warning":
        if _same_hook_notice_text(_hook_notice_kind_text(text), notice_text):
            return True
        # 前方一致の最小一致長に満たない短い警告は、通知の種類本文に含まれるかで代表を判定する。
        normalized = " ".join(text.split())
        return bool(normalized) and normalized in " ".join(notice_text.split())
    hook_name = notice.get("hook_name")
    return isinstance(hook_name, str) and bool(hook_name) and text.startswith(hook_name) and "hook error:" in text


def _is_bounded_hook_group(key: tuple[str, ...]) -> bool:
    """発生源ごとの上位種への限定を適用する候補キーかを返す。

    対象はblockまたはwarnのhook通知とする。他の種別のキーは軸の数が異なるため、
    タグの位置を参照する前に種別と軸の数を確認する。
    """
    return len(key) > 3 and key[0] == "hook-notice" and key[3] in {"block", "warn"}


def _candidate_mechanism(candidate_kind: str, event: dict[str, Any]) -> str:
    """定型接頭辞の後にある原因を候補キーの追加軸として返す。"""
    raw = event.get("text")
    if not isinstance(raw, str):
        return ""
    if candidate_kind == "escalation":
        reason = next(
            (line.partition(":")[2].strip() for line in raw.splitlines() if line.startswith(_ESCALATION_REASON_PREFIXES)), ""
        )
    elif candidate_kind == "hook-notice" and event.get("tag") == "block":
        marker = re.search(r"\b(?:blocked|block):\s*", raw, flags=re.IGNORECASE)
        detail = raw[marker.end() :].strip() if marker else ""
        reason = detail.splitlines()[0].split("。", 1)[0].strip() if detail else ""
    else:
        return ""
    return _CANDIDATE_VARIABLE.sub(_CANDIDATE_VARIABLE_PLACEHOLDER, " ".join(reason.split()))


def _failure_command_parts(event: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    """失敗した実行の包装を外し、実行ファイル、サブコマンド、表示用本文と引数を返す。"""
    raw = event.get("command_full", event.get("command"))
    command = _json_object(str(event.get("operation", "")))
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        args = parsed if isinstance(parsed, list) and all(isinstance(value, str) for value in parsed) else []
    else:
        value = command.get("command") if command is not None else None
        args = _shell_tokens(value) if isinstance(value, str) else []
    # Bashの引用と演算子は原文で保ち、直接渡されたargvでは引用してデータとして扱う。
    value = command.get("command") if command is not None else None
    display = value if not isinstance(raw, str) and isinstance(value, str) else shlex.join(args)
    shell_unwrapped = False
    for _ in range(5):
        if not args:
            break
        name = _basename(args[0])
        if name == "timeout" and len(args) >= 3:
            args = args[2:]
        elif name == "env":
            index = 1
            while index < len(args) and _ENV_ASSIGNMENT.match(args[index]):
                index += 1
            args = args[index:]
        elif name in _SHELL_NAMES and len(args) >= 3 and args[1] in {"-c", "-lc"}:
            if not shell_unwrapped:
                display = args[2]
                shell_unwrapped = True
            args = _shell_tokens(args[2])
        elif (
            not any(
                "cd:" in line or "Set-Location" in line
                for line in str(event.get("diagnostic") or event.get("text") or "").splitlines()
            )
            and (inner := _powershell_inner_args(args)) is not None
        ):
            if not shell_unwrapped:
                display = args[-1]
                shell_unwrapped = True
            args = inner
        elif name == "uv" and len(args) >= 3 and args[1] == "run":
            index = 2
            while index < len(args) and args[index] in {"--frozen", "--locked", "--no-project"}:
                index += 1
            args = args[index:]
        else:
            break
    if not args:
        name = str(event.get("tool_name") or event.get("tool") or "")
        return name, "", display, []
    name = _basename(args[0])
    if name == "git":
        index = 1
        while index < len(args):
            if args[index] in {"-C", "-c", "--git-dir", "--work-tree"} and index + 1 < len(args):
                index += 2
            elif args[index] == "--no-pager":
                index += 1
            else:
                break
        subcommand = args[index] if index < len(args) else ""
    elif (
        name == "atk"
        and len(args) >= 3
        and args[1] in {"run-script", "review-table"}
        or name == "gh"
        and len(args) >= 3
        and args[1] == "run"
    ):
        subcommand = args[2]
    elif name.startswith("python") or name in {"pytest", "wait_ci.py", "wait-ci"}:
        subcommand = ""
    else:
        subcommand = args[1] if len(args) >= 2 and not args[1].startswith("-") else ""
    return name, subcommand, display or " ".join(args), args


def _failure_exit_code(event: dict[str, Any]) -> int | None:
    value = event.get("exit_code")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    match = re.search(r"\bExit code (\d+)\b", str(event.get("text", "")))
    return int(match.group(1)) if match else None


def _failure_diagnostic(event: dict[str, Any]) -> str:
    tail = event.get("diagnostic_last_line")
    if isinstance(tail, str) and tail.strip():
        return tail.strip()
    text = str(event.get("diagnostic") or event.get("text") or "")
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def _failure_signature(candidate_kind: str, event: dict[str, Any]) -> tuple[str, str]:
    """可変値を除いた失敗署名と、`candidates.md`へ表示するコマンド・診断を返す。"""
    name, subcommand, display, _ = _failure_command_parts(event)
    code = _failure_exit_code(event)
    diagnostic = _failure_diagnostic(event)
    if candidate_kind == "tool-failure" and "hook error:" in diagnostic:
        diagnostic = _HOOK_FAILURE_PREFIX.sub("", diagnostic, count=1)
    if diagnostic == "CommandExecution failed" and not display:
        diagnostic = ""
    normalized = normalize_failure_diagnostic(diagnostic)
    if not normalized:
        normalized = display or f"{event.get('record', '')}:{event.get('line', '')}"
    signature = json.dumps([candidate_kind, name, subcommand, code, normalized], ensure_ascii=False, separators=(",", ":"))
    summary = f"{display or name}（終了コード{code if code is not None else '不明'}）: {diagnostic or '診断なし'}"
    return signature, summary


def normalize_failure_diagnostic(diagnostic: str) -> str:
    """診断の理由を残し、対象と識別できるパス・記録ID・UUIDだけを一般化する。"""
    normalized = " ".join(diagnostic.split()).casefold()
    normalized = _FAILURE_RECORD_ID.sub("<record>", normalized)
    normalized = _FAILURE_UUID.sub("<uuid>", normalized)
    return _FAILURE_PATH.sub("<path>", normalized)


def recalculate_failure_signature(signature: str, summary: str) -> str:
    """既存台帳の署名軸と保存済み代表診断から、現在の理由の正規化で署名を再計算する。"""
    parts = json.loads(signature)
    if not isinstance(parts, list) or len(parts) != 5:
        raise ValueError("失敗署名の軸が不正")
    diagnostic = re.search(r"（終了コード(?:\d+|不明)）: (.*)\Z", summary, re.DOTALL)
    if diagnostic is None:
        raise ValueError("失敗の代表診断の形式が不正")
    normalized = normalize_failure_diagnostic(diagnostic[1])
    if normalized and normalized != "診断なし":
        parts[4] = normalized
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _is_check_detected(event: dict[str, Any]) -> bool:
    """検証コマンドが欠陥を検出して終了コード1を返した結果かを判定する。"""
    if _failure_exit_code(event) != 1:
        return False
    name, subcommand, _, args = _failure_command_parts(event)
    return (name, subcommand) in _CHECK_COMMANDS or "--check" in args


def _candidate_key(candidate_kind: str, event: dict[str, Any], normalized_text: str) -> tuple[str, ...]:
    """候補種別ごとの正規化軸を、並べ替え可能な文字列tupleで返す。

    軸には原則として正規化した本文だけを置く。診断を欠くCommandExecutionでは本文による原因の
    区別が成立しないため、構造化コマンド、さらにコマンドも無い場合は記録位置を用いる。
    """
    if candidate_kind == "hook-notice":
        tag = str(event.get("tag", ""))
        return (
            candidate_kind,
            str(event.get("hook", "")),
            "" if tag in {"block", "warn"} else str(event.get("hook_name", "")),
            tag,
            _normalize_hook_candidate_text(normalized_text),
            _candidate_mechanism(candidate_kind, event),
        )
    if candidate_kind in {"command-failure", "tool-failure"}:
        return candidate_kind, _failure_signature(candidate_kind, event)[0]
    if candidate_kind == "escalation":
        return candidate_kind, _normalize_candidate_kind_text(normalized_text), _candidate_mechanism(candidate_kind, event)
    if candidate_kind == "adhoc-processing":
        # 記録（thread）ごとに1件へまとめ、委譲先が多い実行でも候補の件数が呼び出しの件数に比例しないようにする。
        return candidate_kind, str(event["record"])
    if candidate_kind in {"confirmation-request", "wi-user-response"}:
        # 確認ごとに要否を判定するため、同じ回答の文字列（選択肢のlabelなど）でも別の確認をまとめない。
        return candidate_kind, str(event["record"]), f"{int(event['line']):010d}", str(event.get("tag", ""))
    return candidate_kind, _normalize_candidate_kind_text(normalized_text)
