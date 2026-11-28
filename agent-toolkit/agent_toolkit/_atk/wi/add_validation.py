import datetime
import pathlib
import re
from collections.abc import Mapping

import markdown_it

from agent_toolkit._atk import outcome
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import constants as _constants
from agent_toolkit._atk.wi import entries as _entries
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import headings as _headings
from agent_toolkit._atk.wi import origin_review
from agent_toolkit._atk.wi import priority as _priority
from agent_toolkit._atk.wi import uwi_body as _uwi
from agent_toolkit._atk.wi.constants import REPORT_TITLE_PREFIX, WI_TYPE_AWI, WI_TYPE_UWI
from agent_toolkit._atk.wi.repo import resolve_repo_id_or_raise
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import markdown_headings, skill_modes
from agent_toolkit._common.prevention_kinds import starts_with_kind
from agent_toolkit._plan import structure as _plan_structure

_COVERAGE_SECTION_TITLE = re.compile(r"「[^」]*」|『[^』]*』")
_COVERAGE_EVIDENCE = re.compile(
    r"(?<![\w/])(?:commit[ :：]+`?[0-9a-f]{7,40}\b|[a-z]+:[0-9a-f-]{8,}:[1-9][0-9]*\b|"
    r"CI[ :：]+`[^`\n]+`|[^\s`(、。]*_test\.py\b|scripts/check_[\w-]+\.py\b|"
    r"\.github/workflows/[\w.-]+\.ya?ml\b|[\w./-]+\.(?:jsonl|log|out)\b)"
)
_COVERAGE_MARKDOWN_PATH = re.compile(r"([\w./-]+\.md)\b(?:`?[:：]([1-9][0-9]*)\b)?")
_COVERAGE_RECORD = re.compile(r"観測|実測|検証|対照|試験|実行結果|記録|ログ|verification|evidence|result|report", re.IGNORECASE)
_NORM_DIRS = ("agent-toolkit/skills/", "agent-basics/skills/", ".claude/skills/", ".claude/rules/")


def _has_coverage_evidence(reason: str) -> bool:
    reason = _COVERAGE_SECTION_TITLE.sub(" ", reason).replace("\\", "/")
    if _COVERAGE_EVIDENCE.search(reason):
        return True
    describes_observation = _COVERAGE_RECORD.search(reason) is not None
    return any(
        (lineno or describes_observation)
        and not path.endswith(("/AGENTS.md", "/CLAUDE.md"))
        and path not in {"AGENTS.md", "CLAUDE.md"}
        and all(directory not in path for directory in _NORM_DIRS)
        for path, lineno in _COVERAGE_MARKDOWN_PATH.findall(reason)
    )


def warn_prevention_coverage(body: str, *, original: str | None = None, entry_type: str = WI_TYPE_AWI) -> None:
    if entry_type != WI_TYPE_AWI:
        return
    current = _headings.last_h2_section_lines(body, _CAUSE_ANALYSIS_HEADING)
    previous = _headings.last_h2_section_lines(original, _CAUSE_ANALYSIS_HEADING) if original is not None else None
    if not current or "\n".join(current).strip() == "\n".join(previous or []).strip():
        return
    for line in _plan_structure.cause_record_lines("\n".join(current)):
        if line.label != "再発防止" or not starts_with_kind(line.text):
            continue
        label, covered, reason = line.text.lstrip(" \t　*_`~").partition("被覆")
        if not label and covered and not _has_coverage_evidence(reason):
            outcome.report_warning(
                "再発防止を「被覆」とする根拠に、今回の条件を防ぐ対策の所在が見つかりません。",
                next_action=(
                    "`agent-basics:bugfix/references/prevention.md`「再発防止を採用しない場合」に従い、"
                    "対策が今回の条件を防ぐことを確かめて所在を示すか、既存対策が効かなかった理由を区分して次の案を比べる"
                ),
                source="prevention_coverage",
            )


def validate_report_body(body: str, choices: object) -> None:
    """報告（`question_type: report`）の本文が質問の形を持たないかを確かめる。

    新規保存と本文編集が共有する。  pyfltr: allow-comment
    """
    if not body.lstrip().startswith(REPORT_TITLE_PREFIX):
        raise WebInputError("報告の1行目は`# 報告: `で始める", next_action="本文の1行目を`# 報告: <要点>`にして保存し直す")
    if choices or any(line.strip() == "## 選択肢と帰結" for line in body.splitlines()):
        raise WebInputError(
            "報告は選択肢を持たない",
            next_action="choicesと`## 選択肢と帰結`を除き、本人の判断が必要な事項は別の質問のUWIとして投入する",
        )


_RETROSPECTIVE_SCOPES = frozenset(
    (
        *skill_modes.accepted_scopes("agent-toolkit:review-session", None),
        *skill_modes.accepted_scopes("agent-toolkit:review-session", "batch"),
    )
)
_HANDOFF_NAME = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md")
_CANDIDATE_SUFFIX = re.compile(r"\s*（c[0-9]+(?:[、,]\s*c[0-9]+)*）$")
_UNCERTAIN_LINE = re.compile(r"(?:[-*+]\s+)?未確定:\s*(.*)")


def validate_retrospective_report(private_notes: pathlib.Path, body: str, metadata: Mapping[str, object]) -> None:
    scope = metadata.get("scope")
    if not isinstance(scope, str) or scope not in _RETROSPECTIVE_SCOPES:
        return
    repo = metadata.get("target_repo")
    names: list[tuple[str, str]] = []
    filtered = markdown_headings.without_literal_blocks(body)
    headings = {
        token.map[0]: (level, title)
        for level in (2, 3)
        for token, title in markdown_headings.top_level_atx_headings(filtered, level)
        if token.map is not None
    }
    in_carryover = False
    for index, line in enumerate(filtered.splitlines()):
        if (heading := headings.get(index)) is not None:
            in_carryover = heading == (3, "未達・持ち越し")
        if in_carryover or not (match := _UNCERTAIN_LINE.fullmatch(line.strip())):
            continue
        _, separator, value = match[1].rpartition("; 検証の引き継ぎ:")
        if not separator and match[1].startswith("検証の引き継ぎ:"):
            _, separator, value = match[1].partition("検証の引き継ぎ:")
        handoff = _CANDIDATE_SUFFIX.sub("", value.strip())
        if separator and _HANDOFF_NAME.fullmatch(handoff):
            names.append((handoff, handoff))
        elif not (
            separator
            and (reason := re.fullmatch(r"不要（(.+)）", handoff))
            and reason[1].strip()
            and not any(term in reason[1] for term in ("次回の振り返り", "次の振り返り", "同じ型が出た"))
        ):
            raise _retrospective_handoff_error("「検証の引き継ぎ」の値が不正か未記入")
    if names:
        if not isinstance(repo, str) or not repo:
            raise _retrospective_handoff_error("報告の宛先が未指定")
        _, missing = _entries.read_named_entries(private_notes, names, target_repo=repo, entry_type=(WI_TYPE_AWI,))
        if missing:
            raise _retrospective_handoff_error("報告と同じ宛先に調査AWIが見つからない: " + "、".join(missing))


def _retrospective_handoff_error(reason: str) -> WebInputError:
    return WebInputError(
        f"未確定の問題の引き継ぎ: {reason}。後の振り返りは前の回の再開条件を読まないため、引き継ぎ先が無いと放置になる",
        next_action=(
            "調査AWIを登録してファイル名を書くか、原因が対策の採否を左右しない理由を「不要（…）」で書く。"
            "欠陥が確定しているなら「### 確定した問題と対策」へ移す"
        ),
    )


def _target_repo_error(value: object, error: WebInputError) -> WebInputError:
    """target_repoの解決失敗を、投入を拒否する1件の`WebInputError`へ変換する。"""
    return WebInputError(f"target_repoを解決できません: {value}（{error.reason}）", next_action=error.next_action)


def _parse_leading_frontmatter(message: str) -> tuple[dict[str, object], str]:
    parsed = _frontmatter.parse_frontmatter(message)
    if parsed is None:
        return {}, message
    return parsed


def body_is_effectively_empty(body: str) -> bool:
    non_empty_lines = [line.strip() for line in body.split("\n") if line.strip()]
    if not non_empty_lines:
        return True
    return all(line in ("-", "*", "+") for line in non_empty_lines)


_EMPTY_AWI_ERROR = "AWI本文が実質空です"

_EMPTY_AWI_NEXT_ACTION = "本文を記入して再投入する"

_CAUSE_ANALYSIS_HEADING = _plan_structure.REQUEST_H2_CAUSE
_FINDINGS_HEADING = "調査結果と判断根拠"

"""観測した欠陥を起点とする通常AWIだけが持つH2見出し。

起点が観測した欠陥かは本文から機械判定できないため、必須集合へ加えず、置いた場合の順序と非空だけを判定する。
"""


def parse_entry_message(message: str, *, entry_type: str) -> tuple[dict[str, object], str]:
    """先頭frontmatterと論理本文を返し、`agent-toolkit:atk-mode/references/awi-body.md`「通常AWIの本文」の要件のうち種別共通のものを検証する。"""
    frontmatter, body = _parse_leading_frontmatter(_frontmatter.normalize_newlines(message))
    if entry_type == WI_TYPE_AWI and body_is_effectively_empty(body):
        raise WebInputError(_EMPTY_AWI_ERROR, next_action=_EMPTY_AWI_NEXT_ACTION)
    if entry_type != WI_TYPE_AWI:
        body = _uwi.strip_unanswered_uwi_wrapper(body)
        _uwi.reject_reserved_uwi_markup(body)
        if not _uwi.question_opening(body).strip():
            raise WebInputError(
                "UWIの質問の冒頭が空です",
                next_action=(
                    "本文の先頭（最初のH2見出しより前）に質問の内容を書く。保存形式の`## 質問`・`## 回答`は`atk`が付ける"
                ),
            )
    return frontmatter, body


def require_agent_awi_sections(
    body: str,
    frontmatter: dict[str, object],
    *,
    entry_type: str,
    source: str | None,
) -> None:
    raw_source = frontmatter.get("source", source)
    item_source = raw_source if isinstance(raw_source, str) else source
    if entry_type != WI_TYPE_AWI or not item_source:
        return
    problems = _plan_structure.request_section_problems(_plan_structure.request_sections(body))
    if not problems:
        return
    order = "、".join(_plan_structure.REQUEST_H2_ORDER)
    required = "、".join(_plan_structure.REQUEST_H2_REQUIRED)
    raise WebInputError(
        "。".join(problems)
        + f"。`agent-toolkit:atk-mode/references/awi-body.md`「通常AWIの本文」が、H2を{order}の順に置くこと、"
        + f"{required}を必須とすること、"
        + "設置した節には本文を書くことを定めます。",
        next_action=(f"本文のH2を{order}の順に並べ、{required}へ本文を書いてから再投入する。設置した節にも本文を書く"),
    )


_DIRECT_CAUSE_LABEL = "直接的原因"

"""廃止した原因分析表で原因を記録した行の名前。保存済みの旧形式の本文を判定するために残す。"""

_UNDETERMINED_CAUSE_RE = re.compile(r"(?:未確定|不明|未特定|調査中|未確認)(?:$|[\s。、，,．.:：;；（(「『【\[/／・*_`~])")

"""原因が確定していないことを宣言する値の形。

値の冒頭の語が単独で置かれるか、直後に句読点・空白・括弧・Markdown装飾の閉じ記号などの区切りが続く場合だけを宣言とみなす。
確定した原因の説明に現れる同じ語（「不明だった設定を特定した」「旧値の残存。不明だった由来を特定した」など）は拒否しない。
"""

_TABLE_CELL_DECORATION = "*_`~"


def _strip_table_cell(cell: str) -> str:
    while True:
        stripped = cell.strip().strip(_TABLE_CELL_DECORATION)
        if stripped == cell:
            return cell
        cell = stripped


_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})

UNDETERMINED_CAUSE_NEXT_ACTION = (
    "原因を現物の観測で確定してから本文を書き直して再実行する。"
    "確定できない場合は保存しない（既存項目の編集では保存済みの本文がそのまま残る）"
)


def undetermined_cause_lines(body: str) -> list[str]:
    normalized = _frontmatter.normalize_newlines(body)
    lines = normalized.split("\n")
    found = [
        f"原因: {record.text}"
        for record in _plan_structure.cause_record_lines(normalized)
        if record.label == "原因" and _UNDETERMINED_CAUSE_RE.match(record.text)
    ]
    for token in _MARKDOWN.parse(normalized):
        if token.type != "table_open" or token.map is None:
            continue
        for line in lines[token.map[0] : token.map[1]]:
            cells = [_strip_table_cell(cell) for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] == _DIRECT_CAUSE_LABEL and _UNDETERMINED_CAUSE_RE.match(cells[1]):
                found.append(line.strip())
    return found


def require_confirmed_cause(body: str, *, entry_type: str) -> None:
    """エージェント環境から保存するUWIが、原因を未確定のまま宣言していないことを検証する。

    通常AWIは独立別件の原因仮説と次の問いを保存できる必要があるため、起草と意味検収で本文の妥当性を確認する。
    UWIはユーザーへの判断依頼を未確定な原因分析で代用しない既存条件を保つ。人間の入力は拒否しない。
    """
    if entry_type != WI_TYPE_UWI or not is_agent_environment():
        return
    if found := undetermined_cause_lines(body):
        raise WebInputError(
            "原因が未確定のまま保存しようとした: " + " / ".join(found),
            next_action=UNDETERMINED_CAUSE_NEXT_ACTION,
        )


def require_cause_analysis_format(body: str, *, original: str | None = None, entry_type: str = WI_TYPE_AWI) -> None:
    """エージェントが保存するAWIの`## 原因分析`が、バグ単位ごとに記録の行をそろえていることを確かめる。

    記録の形は`agent-basics:bugfix`の`SKILL.md`「記録」が定め、検査は`cause_record_errors`が行う。
    UWIは`## 原因分析`の記録先ではないため対象外とする。
    既存項目の編集で`## 原因分析`節が変わらない場合（追記など）は、保存済みの旧形式の表を理由に編集を拒否しない。
    """
    if entry_type == WI_TYPE_UWI or not is_agent_environment():
        return
    _require_recurrence_record(body, original)
    section = _headings.last_h2_section_lines(body, _CAUSE_ANALYSIS_HEADING)
    if section is None:
        return
    content = "\n".join(section)
    if original is not None:
        saved = _headings.last_h2_section_lines(original, _CAUSE_ANALYSIS_HEADING)
        if saved is not None and "\n".join(saved).strip() == content.strip():
            return
    if errors := _plan_structure.cause_record_errors(content):
        labels = "・".join(f"`{label}:`" for label in _plan_structure.CAUSE_RECORD_REQUIRED_LABELS)
        raise WebInputError(
            "原因分析の書式が不正です: " + "。".join(errors),
            next_action=(
                f"`## 原因分析`のバグ単位（H3が無ければ節全体、あればH3ごと）に{labels}で始まる行を置き、"
                "各行の本文を同じ行に書く。書く内容は`agent-basics:bugfix`の`SKILL.md`「記録」に従う"
            ),
        )


def _recurrence_sections(body: str) -> tuple[str, str, str]:
    def section(heading: str) -> str:
        lines = _headings.last_h2_section_lines(body, heading)
        return "\n".join(lines) if lines is not None else ""

    findings = section(_FINDINGS_HEADING)
    records = "\n".join((section(_CAUSE_ANALYSIS_HEADING), findings))
    return section(_plan_structure.REQUEST_H2_QUOTATION), records, findings


def _require_recurrence_record(body: str, original: str | None) -> None:
    """ユーザーの原文が反復に触れる原稿に、前回の対策が防げなかった理由の`再発:`の行があることを確かめる。

    再発の指摘を記録せずに保存すると、前回と同じ種類の対策を重ねても検出できない（2026年10月24日以降のcommit 2855件中、
    `再発:`の行を持つものは2件だった）。原因が未確定の原稿は`## 調査結果と判断根拠`に書いた行も数える。
    逐語引用と記録の節が保存済みの本文と変わらない編集（追記など）は、既存の項目を理由に拒否しない。 pyfltr: allow-comment
    """
    quote, records, findings = _recurrence_sections(body)
    if original is not None and _recurrence_sections(original) == (quote, records, findings):
        return
    errors = _plan_structure.recurrence_quote_errors(records, quote) + _plan_structure.findings_recurrence_errors(findings)
    if errors:
        raise WebInputError(
            "原因分析の書式が不正です: " + "。".join(errors),
            next_action=(
                "前回の対策の所在と種類を`agent-basics:bugfix`の`references/recurrence.md`に従って調べ、"
                "`## 原因分析`か`## 調査結果と判断根拠`に区分の語で始まる`再発:`の行を置く"
            ),
        )


def _require_agent_source(frontmatter: dict[str, object], source: str | None) -> None:
    """CLIのエージェント投入でsourceが確定していることを検証する。

    Web UI等が使う共有保存関数はユーザーの手動投入も受け付け、環境変数では呼出元を区別できないため、
    この確認は共有保存関数には適用しない。
    """
    raw_source = frontmatter.get("source", source)
    item_source = raw_source if isinstance(raw_source, str) else source
    if not is_agent_environment():
        return
    if not item_source:
        raise WebInputError(
            "エージェント環境ではsourceの明示が必須です。",
            next_action="--sourceオプション、または本文先頭のfrontmatterで指定してください。",
        )
    if item_source == "human":
        raise WebInputError(
            "エージェント環境ではsourceにhumanを指定できません。",
            next_action=(
                "--sourceまたは本文先頭のfrontmatterのsourceへ、起動中の作業かスキルの名前を指定する"
                "（プラグイン接頭辞を除く。`/atk-mode add-awi-by-user`なら`add-awi-by-user`）。"
                "該当する作業もスキルも無い場合はagentを指定して再実行する"
            ),
        )


def _verify_frontmatter_target_repos(parsed_messages: list[tuple[dict[str, object], str]]) -> None:
    for frontmatter, _body in parsed_messages:
        raw_target_repo = frontmatter.get("target_repo")
        if raw_target_repo is None:
            raise WebInputError(
                "target_repoが指定されていない",
                next_action="`--target-repo`を指定するか、各メッセージのfrontmatterへtarget_repoを記載して再投入する",
            )
        if not isinstance(raw_target_repo, str) or not raw_target_repo.strip():
            raise WebInputError(
                "メッセージfrontmatterのtarget_repoは空でない文字列で指定してください",
                next_action="frontmatterのtarget_repoへローカルworktreeのパスかremote URLを書いて再投入する",
            )
        try:
            resolve_repo_id_or_raise(raw_target_repo, warn_non_origin=True)
        except WebInputError as error:
            raise _target_repo_error(raw_target_repo, error) from error


_RESERVED_ADD_KEY_NEXT_ACTIONS = {
    "effect_check": "採用後の効果判定はatk wi effect-checkで記録する",
    "state": "新規項目はinboxとして保存する。状態の変更には状態遷移コマンドを使う",
    "type": "--typeで指定する",
    "scope": "--scopeで指定する",
    "question_type": "--question-typeで指定する",
    "choices": "--choicesで指定する",
    "depends_on": "--depends-onで指定する",
    "cooldown_until": "--cooldown-untilで指定する",
    "target_commit": "atkによる対象HEADの自動取得に任せる",
    "proposal_fingerprint": "atkによる原稿の指紋の記録に任せる",
    "draft_retry_fingerprint": "atkによる再投入の照合値の記録に任せる",
    "github_source": "GitHubの取り込み処理による出所の記録に任せる",
    "processing_owner": "atk wi start-processingまたはtake-overで担当を記録する",
    "detailed": "計画を作る時点で詳細化したAWIの新規投入では--detailedを指定する",
    "submitter_session": "atkによる投入元セッションの自動取得に任せる",
    "run_skill_run_id": "atkによるrun-skill実行識別子の自動取得に任せる",
    "plan_file": "通常新規投入では除き、移行・復元では--batchを使う",
    "queue_schedule": "通常新規投入では除き、移行・復元では--batchを使う",
    "reservation": "通常新規投入では除き、移行・復元では--batchを使う",
    "reservation_companion": "通常新規投入では除き、移行・復元では--batchを使う",
    "target_commit_history": "通常新規投入では除き、移行・復元では--batchを使う",
    "repair_target": "通常新規投入では除き、修復UWIの生成処理に任せる",
    "repair_kind": "通常新規投入では除き、修復UWIの生成処理に任せる",
}

"""通常新規投入が採用しない管理キーと、指定方法の対応。保存前に存在自体を拒否する。"""

_RETIRED_ADD_KEYS = frozenset(
    {
        "scope_alignment",
        "confirmation",
        "observation_wait",
        "created",
        "origin_session",
        "origin_locator",
        *_priority.LEGACY_REASON_KEYS,
    }
)


def normalize_choices(value: object) -> str | None:
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list) and all(isinstance(part, str) and "," not in part for part in value):
        parts = value
    else:
        return None
    return ",".join(part.strip() for part in parts if part.strip())


def _remove_matching_uwi_fields(
    frontmatter: dict[str, object], *, question_type: str | None, scope: str | None, choices: str | None
) -> str | None:
    body_scope = None
    for key, expected in (("type", WI_TYPE_UWI), ("question_type", question_type), ("scope", scope), ("choices", choices)):
        if key not in frontmatter:
            continue
        raw = frontmatter.pop(key)
        value = normalize_choices(raw) if key == "choices" else raw.strip() if isinstance(raw, str) else None
        if (
            key == "scope"
            and scope is None
            and question_type == _constants.QUESTION_TYPE_REPORT
            and value in _RETROSPECTIVE_SCOPES
        ):
            body_scope = value
            continue
        if value is None or value != expected:
            raise WebInputError(
                f"UWIの{key}が本文frontmatterとCLIで一致しません",
                next_action=f"本文frontmatterの{key}を除くか、--{key.replace('_', '-')}と同じ値を指定する",
            )
    return body_scope


_RESERVED_FRONTMATTER_KEYS = ("target_repo", "target_branch", "source", *_RESERVED_ADD_KEY_NEXT_ACTIONS)

"""生成側が所有するキー。target_repo・target_branch・sourceだけは入力値を優先して採用する。"""


def normalize_cooldown_until(value: str) -> str:
    next_action = "--cooldown-untilへ空でないタイムゾーン付きISO 8601日時を指定する"
    try:
        deadline = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise WebInputError(
            "cooldown_untilはタイムゾーン付きISO 8601日時で指定してください", next_action=next_action
        ) from error
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise WebInputError("cooldown_untilはタイムゾーン付きISO 8601日時で指定してください", next_action=next_action)
    return deadline.isoformat()


def _validate_add_entries(
    *,
    messages: list[str],
    target_repo: str | None,
    entry_type: str,
    question_type: str | None,
    choices: str | None,
    target_commit: str | None,
    scope: str | None = None,
    source: str | None = None,
    cooldown_until: str | None = None,
    origin_reviews: tuple[str, ...] = (),
) -> tuple[list[tuple[dict[str, object], str]], str | None, str | None]:
    if not messages:
        raise WebInputError("messagesには1件以上を指定してください", next_action="本文を1件以上指定して再投入する")
    normalized_cooldown = None
    if cooldown_until is not None:
        if entry_type != WI_TYPE_AWI:
            raise WebInputError(
                "cooldown_untilはAWIでのみ指定できる", next_action="--cooldown-untilを外すか--type=awiで投入する"
            )
        normalized_cooldown = normalize_cooldown_until(cooldown_until)
    if target_commit is not None and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target_commit) is None:
        raise WebInputError(
            "target_commitは解決済みの40桁または64桁OIDで指定してください",
            next_action="対象worktreeで`git rev-parse HEAD`を実行して得た完全なOIDを指定して再投入する",
        )
    normalized_target_repo: str | None = None
    if target_repo is not None:
        try:
            normalized_target_repo = resolve_repo_id_or_raise(target_repo, warn_non_origin=True)
        except WebInputError as error:
            raise _target_repo_error(target_repo, error) from error
    if entry_type != WI_TYPE_AWI and question_type not in _constants.NEW_QUESTION_TYPES:
        raise WebInputError(
            f"UWIの回答形式が不正か未指定です: {question_type}",
            next_action=(
                "問いごとに個別のUWIとし、選択肢から選ぶ問いは`--question-type=choice --choices <A,B,C>`、"
                "2択の可否を問う問いは`--question-type=yes-no`で投入する。選択肢に無い回答は回答欄で受ける"
            ),
        )
    parsed_messages = [parse_entry_message(message, entry_type=entry_type) for message in messages]
    for message, (frontmatter, body) in zip(messages, parsed_messages, strict=True):
        body_scope = None
        origin_review.require(message, body=body, entry_type=entry_type, reviews=origin_reviews)
        for key in _RETIRED_ADD_KEYS:
            frontmatter.pop(key, None)
        if entry_type == WI_TYPE_UWI:
            body_scope = _remove_matching_uwi_fields(frontmatter, question_type=question_type, scope=scope, choices=choices)
        rejected_keys = [key for key in _RESERVED_ADD_KEY_NEXT_ACTIONS if key in frontmatter]
        if rejected_keys:
            raise WebInputError(
                "本文frontmatterでは指定できない予約キーです: " + "、".join(rejected_keys),
                next_action="。".join(
                    f"frontmatterから{key}を除き、{_RESERVED_ADD_KEY_NEXT_ACTIONS[key]}" for key in rejected_keys
                ),
            )
        if entry_type == WI_TYPE_UWI and question_type == _constants.QUESTION_TYPE_REPORT:
            validate_report_body(body, choices)
        require_agent_awi_sections(
            body,
            frontmatter,
            entry_type=entry_type,
            source=source,
        )
        if body_scope is not None:
            frontmatter["scope"] = body_scope
    if normalized_target_repo is None:
        _verify_frontmatter_target_repos(parsed_messages)
    else:
        _verify_frontmatter_target_repos(
            [(frontmatter, body) for frontmatter, body in parsed_messages if "target_repo" in frontmatter]
        )
    if (
        entry_type != WI_TYPE_AWI
        and question_type == "choice"
        and len([part for part in (choices or "").split(",") if part.strip()]) < 2
    ):
        raise WebInputError(
            "choice形式には非空の選択肢が2件以上必要です",
            next_action="`--choices`で選択肢を2件以上指定するか、別のquestion_typeを選ぶ",
        )
    if (
        entry_type == WI_TYPE_UWI
        and question_type in (_constants.QUESTION_TYPE_POST_APPROVAL, _constants.QUESTION_TYPE_REPORT)
        and choices is not None
    ):
        raise WebInputError(
            "report形式は選択肢を持たない"
            if question_type == _constants.QUESTION_TYPE_REPORT
            else "post-approval形式の選択肢は固定です",
            next_action="--choicesを外して再投入する",
        )
    return parsed_messages, normalized_target_repo, normalized_cooldown
