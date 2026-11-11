"""WI投入本文・frontmatter・原因分析の検証。"""

import datetime
import re

import markdown_it

from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk.wi import constants as _constants
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import headings as _headings
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi.constants import WI_TYPE_AWI, WI_TYPE_UWI
from agent_toolkit._atk.wi.repo import resolve_repo_id_or_raise
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._plan.structure import constants as _plan_constants
from agent_toolkit._plan.structure import markdown as _plan_markdown
from agent_toolkit._plan.structure import sections as _plan_sections


def _target_repo_error(value: object, error: WebInputError) -> WebInputError:
    """target_repoの解決失敗を、投入を拒否する1件の`WebInputError`へ変換する。"""
    return WebInputError(f"target_repoを解決できません: {value}（{error.reason}）", next_action=error.next_action)


def _parse_leading_frontmatter(message: str) -> tuple[dict[str, object], str]:
    """共有frontmatterパーサーへの薄いラッパー。"""
    parsed = _frontmatter.parse_frontmatter(message)
    if parsed is None:
        return {}, message
    return parsed


def body_is_effectively_empty(body: str) -> bool:
    """本文が実質空か判定する。

    実質空とは、空文字・空白のみ、または全ての非空行が箇条書きマーカー
    （`-`・`*`・`+`のいずれか単独文字）のみで構成される状態を指す。
    review-session自動投入・ユーザー直接投入のどちらでも、
    実効的な指示・観察事象を含まない投入をCLI側で一律検出するための基準とする。
    """
    non_empty_lines = [line.strip() for line in body.split("\n") if line.strip()]
    if not non_empty_lines:
        return True
    return all(line in ("-", "*", "+") for line in non_empty_lines)


_EMPTY_AWI_ERROR = "AWI本文が実質空です"

_EMPTY_AWI_NEXT_ACTION = "本文を記入して再投入する"

_REQUIRED_AWI_HEADINGS: tuple[str, ...] = ("反映内容と反映先",)

"""`source`を持つ通常AWIが必須とするH2見出し。

`agent-toolkit:wi-standards`の`## 通常AWIの本文`が定める必須節をそのまま写す。
規範が必須と定める集合を1箇所で定義し、本処理もその集合を使って判定することで、
規範の一部だけを判定する状態が生じないようにする。規範側の必須節を変える改訂では本定数も同じ変更単位で更新する。
"""

_CAUSE_ANALYSIS_HEADING = "原因分析"

"""観測した欠陥を起点とする通常AWIだけが持つH2見出し。

起点が観測した欠陥かは本文から機械判定できないため、必須集合へ加えず、置いた場合の順序と非空だけを判定する。
"""

_AWI_HEADING_ORDER: tuple[str, ...] = (
    "反映内容と反映先",
    _CAUSE_ANALYSIS_HEADING,
    "適用範囲",
    "実現性",
    "調査結果と判断根拠",
    "完成条件",
    "ユーザー指摘の逐語引用",
)

"""同じ規範が定めるH2の並び順。

`原因分析`と`ユーザー指摘の逐語引用`は条件付きで必須なため順序の判定にだけ用いる。
"""


def parse_entry_message(message: str, *, entry_type: str) -> tuple[dict[str, object], str]:
    """先頭frontmatterと論理本文を返し、`agent-toolkit:wi-standards`「通常AWIの本文」の要件のうち種別共通のものを検証する。"""
    frontmatter, body = _parse_leading_frontmatter(_frontmatter.normalize_newlines(message))
    if entry_type == WI_TYPE_AWI and body_is_effectively_empty(body):
        raise WebInputError(_EMPTY_AWI_ERROR, next_action=_EMPTY_AWI_NEXT_ACTION)
    if entry_type != WI_TYPE_AWI:
        body = _uwi.strip_unanswered_uwi_wrapper(body)
        _uwi.reject_reserved_uwi_markup(body)
    return frontmatter, body


def require_agent_awi_sections(
    body: str,
    frontmatter: dict[str, object],
    *,
    entry_type: str,
    source: str | None,
) -> None:
    """`source`を持つ通常AWIが必須H2を全件持ち、規定順序に従うことを検証する。

    不足と順序の不一致は1件の例外へまとめて返す。
    1件ずつ返すと、起草側が本文を書き直して再投入する往復が不足節の件数だけ生じるためである。
    """
    raw_source = frontmatter.get("source", source)
    item_source = raw_source if isinstance(raw_source, str) else source
    if entry_type != WI_TYPE_AWI or not item_source:
        return
    sections = _headings.h2_sections(body)
    filled = {name for name, has_body in sections if has_body}
    appeared = [name for name, _has_body in sections if name in _AWI_HEADING_ORDER]
    problems: list[str] = []
    if missing := [name for name in _REQUIRED_AWI_HEADINGS if name not in filled]:
        problems.append(f"非空の必須節がありません: {'、'.join(missing)}")
    if empty := [
        name for name, has_body in sections if not has_body and name not in (*_REQUIRED_AWI_HEADINGS, "ユーザーコメント")
    ]:
        problems.append(f"本文が空の節があります: {'、'.join(empty)}")
    order_indexes = [_AWI_HEADING_ORDER.index(name) for name in appeared]
    if order_indexes != sorted(order_indexes):
        problems.append(f"H2の順序が規定と異なります: {'、'.join(appeared)}")
    if not problems:
        return
    current_order = tuple(name for name in _AWI_HEADING_ORDER if name not in ("適用範囲", "実現性"))
    raise WebInputError(
        "。".join(problems)
        + f"。agent-toolkit:wi-standardsの`## 通常AWIの本文`が、H2を{'、'.join(current_order)}の順に置くこと、"
        + f"{'、'.join(_REQUIRED_AWI_HEADINGS)}を必須とすること、"
        + "設置した節には本文を書くことを定めます。",
        next_action=(
            f"本文のH2を{'、'.join(current_order)}の順に並べ、{'、'.join(_REQUIRED_AWI_HEADINGS)}へ本文を書いてから"
            "再投入する。設置した節にも本文を書く"
        ),
    )


_DIRECT_CAUSE_LABEL = "直接的原因"

_UNDETERMINED_CAUSE_RE = re.compile(r"(?:未確定|不明|未特定|調査中|未確認)(?:$|[\s。、，,．.:：;；（(「『【\[/／・*_`~])")

"""原因が確定していないことを宣言する値の形。

値の冒頭の語が単独で置かれるか、直後に句読点・空白・括弧・Markdown装飾の閉じ記号などの区切りが続く場合だけを宣言とみなす。
確定した原因の説明に現れる同じ語（「不明だった設定を特定した」「旧値の残存。不明だった由来を特定した」など）は拒否しない。
"""

_TABLE_CELL_DECORATION = "*_`~"


def _strip_table_cell(cell: str) -> str:
    """表のセル値から前後の空白とMarkdown装飾を除く。

    空白はUnicodeの空白（全角空白を含む）として扱い、装飾と空白が交互に並ぶ場合もすべて除く。
    """
    while True:
        stripped = cell.strip().strip(_TABLE_CELL_DECORATION)
        if stripped == cell:
            return cell
        cell = stripped


_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})

UNDETERMINED_CAUSE_NEXT_ACTION = (
    "直接的原因を現物の観測で確定してから本文を書き直して再実行する。"
    "確定できない場合は保存しない（既存項目の編集では保存済みの本文がそのまま残る）"
)


def undetermined_cause_rows(body: str) -> list[str]:
    """表の`直接的原因`行のうち、値が原因の未確定を宣言する行を返す。

    コードフェンスとインデントコードブロックの内側にある表は引用・例示として扱い、判定しない。
    """
    normalized = _frontmatter.normalize_newlines(body)
    lines = normalized.split("\n")
    rows: list[str] = []
    for token in _MARKDOWN.parse(normalized):
        if token.type != "table_open" or token.map is None:
            continue
        for line in lines[token.map[0] : token.map[1]]:
            cells = [_strip_table_cell(cell) for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] == _DIRECT_CAUSE_LABEL and _UNDETERMINED_CAUSE_RE.match(cells[1]):
                rows.append(line.strip())
    return rows


def require_confirmed_cause(body: str, *, entry_type: str) -> None:
    """エージェント環境から保存するUWIが、直接的原因を未確定のまま宣言していないことを検証する。

    通常AWIは独立別件の原因仮説と次の問いを保存できる必要があるため、起草と意味検収で本文の妥当性を確認する。
    UWIはユーザーへの判断依頼を未確定な原因分析で代用しない既存条件を保つ。人間の入力は拒否しない。
    """
    if entry_type != WI_TYPE_UWI or not is_agent_environment():
        return
    if rows := undetermined_cause_rows(body):
        raise WebInputError(
            "直接的原因が未確定のまま保存しようとした: " + " / ".join(rows),
            next_action=UNDETERMINED_CAUSE_NEXT_ACTION,
        )


AWI_CAUSE_TABLE_ROWS: tuple[str, ...] = (
    "対策",
    "回帰防止策",
    "類似見直し観点（作り込み）",
    "類似見直し観点（見逃し）",
    "再発防止策（作り込み）",
    "再発防止策（見逃し）",
    "類似見直しの採否",
    "再発防止策の採否",
)

"""AWIの`## 原因分析`の2列表の行。定義元は`wi-standards/references/analysis-records.md`「原因分析の記録先」。"""


def _awi_cause_table_errors(section: str) -> list[str]:
    """AWIの原因分析の2列表が8行をこの順で持ち、全行に内容があることを確かめる。

    `対策`行か旧5行表の`直接的原因`行を持つ2列表を処置表として識別する。
    4区分の候補行と採否行のいずれかが欠けた記録や空の行と、バグ単位の原因分析表に対応する処置表が無い記録を拒否し、
    候補・採否の意味の判定は意味検収が担う。
    """
    errors: list[str] = []
    tables = _plan_markdown.extract_tables(list(_plan_markdown.iter_markdown_body_lines(section)))
    cause_tables = sum(table.header == _plan_constants.PLAN_BUG_CAUSE_TABLE_HEADER for table in tables)
    found = 0
    for table in tables:
        labels = table.row_labels()
        if table.header != _plan_constants.PLAN_BUG_TABLE_HEADER or not {"対策", _DIRECT_CAUSE_LABEL} & set(labels):
            continue
        found += 1
        if labels != AWI_CAUSE_TABLE_ROWS:
            errors.append(f"原因分析の表は{'、'.join(AWI_CAUSE_TABLE_ROWS)}の8行をこの順に置く: {table.row_location(0)}")
            continue
        for index, row in enumerate(table.rows):
            if len(row) < 2 or not _strip_table_cell(row[1]):
                errors.append(f"{AWI_CAUSE_TABLE_ROWS[index]}の行が空である: {table.row_location(index)}")
    if found < max(cause_tables, 1):
        errors.append(
            f"原因分析の処置表（{'、'.join(AWI_CAUSE_TABLE_ROWS)}の8行）が{found}件で、"
            f"バグ単位の原因分析表{cause_tables}件に足りない"
        )
    return errors


def require_cause_analysis_format(body: str, *, original: str | None = None, entry_type: str = WI_TYPE_AWI) -> None:
    """エージェントが保存する原因分析のL4と系統別候補、AWIの表の行構成を確かめる。

    8行表の規定はAWIの`## 原因分析`の記録先定義であり、UWIには適用しない。

    既存項目の編集で`## 原因分析`節が変わらない場合（追記など）は、保存済みの旧書式を理由に編集を拒否しない。
    """
    if not is_agent_environment():
        return
    section = _headings.last_h2_section_lines(body, _CAUSE_ANALYSIS_HEADING)
    if section is None:
        return
    if original is not None and _headings.last_h2_section_lines(original, _CAUSE_ANALYSIS_HEADING) == section:
        return
    content = "\n".join(section)
    errors = _plan_sections.check_cause_analysis_format(content)
    if entry_type != WI_TYPE_UWI:
        errors += _awi_cause_table_errors(content)
    if errors:
        raise WebInputError(
            "原因分析の書式が不正です: " + "。".join(errors),
            next_action=(
                "L4へ説明または理由付きの該当なしを書く。"
                f"2列表は{'、'.join(AWI_CAUSE_TABLE_ROWS)}の8行とし、作り込み・見逃しの各行へ候補を1件以上、"
                "採否の行へ候補ごとの採否と理由、回帰テストを回帰防止策の行へ書く"
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
                "--sourceまたは本文先頭のfrontmatterのsourceへ、起動スキル名（プラグイン接頭辞を除く）を指定する。"
                "スキルが無い場合はagentを指定して再実行する"
            ),
        )


def _verify_frontmatter_target_repos(parsed_messages: list[tuple[dict[str, object], str]]) -> None:
    """`target_repo`省略時に、全メッセージのfrontmatterが解決可能な対象リポジトリを持つことを検証する。

    キー欠落・非文字列値・空文字列・解決不能をいずれも原因別の`WebInputError`で拒否し、
    `_wi_sync.repo_lock`取得前に全件を確定する。
    """
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
    "submitter_session": "atkによる投入元セッションの自動取得に任せる",
    "run_skill_run_id": "atkによるrun-skill実行識別子の自動取得に任せる",
    "origin_session": "通常新規投入では除き、移行・復元では--batchを使う",
    "origin_locator": "通常新規投入では除き、移行・復元では--batchを使う",
    "plan_file": "通常新規投入では除き、移行・復元では--batchを使う",
    "queue_schedule": "通常新規投入では除き、移行・復元では--batchを使う",
    "reservation": "通常新規投入では除き、移行・復元では--batchを使う",
    "reservation_companion": "通常新規投入では除き、移行・復元では--batchを使う",
    "target_commit_history": "通常新規投入では除き、移行・復元では--batchを使う",
    "repair_target": "通常新規投入では除き、修復UWIの生成処理に任せる",
    "repair_kind": "通常新規投入では除き、修復UWIの生成処理に任せる",
}

"""通常新規投入が採用しない管理キーと、指定方法の対応。保存前に存在自体を拒否する。"""

_RESERVED_FRONTMATTER_KEYS = ("target_repo", "source", *_RESERVED_ADD_KEY_NEXT_ACTIONS)

"""生成側が所有するキー。target_repoとsourceだけは入力値を優先して採用する。"""


def normalize_cooldown_until(value: str) -> str:
    """投入と編集の外部入力を、タイムゾーン付きISO 8601日時へ正規化する。"""
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
    source: str | None = None,
    cooldown_until: str | None = None,
) -> tuple[list[tuple[dict[str, object], str]], str | None, str | None]:
    """保存前の入力検証を行い、正規化済みの値を返す。"""
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
    for frontmatter, body in parsed_messages:
        rejected_keys = [key for key in _RESERVED_ADD_KEY_NEXT_ACTIONS if key in frontmatter]
        if rejected_keys:
            raise WebInputError(
                "本文frontmatterでは指定できない予約キーです: " + "、".join(rejected_keys),
                next_action="。".join(
                    f"frontmatterから{key}を除き、{_RESERVED_ADD_KEY_NEXT_ACTIONS[key]}" for key in rejected_keys
                ),
            )
        require_agent_awi_sections(
            body,
            frontmatter,
            entry_type=entry_type,
            source=source,
        )
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
    if entry_type == WI_TYPE_UWI and question_type == _constants.QUESTION_TYPE_POST_APPROVAL and choices is not None:
        raise WebInputError(
            "post-approval形式の選択肢は固定です",
            next_action="--choicesを外して再投入する",
        )
    return parsed_messages, normalized_target_repo, normalized_cooldown
