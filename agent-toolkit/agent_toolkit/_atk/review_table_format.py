"""レビュー指摘管理表の列・ラベル・本文の書式を定め、表の本文を解析・検証する。

ファイルの読み書きと排他制御は`review_table`、`atk review-table`の引数の解析は`review_table_cli`が扱う。
"""

from __future__ import annotations

import json
import re
import unicodedata

from agent_toolkit._common import next_action as _next_action

_ActionableError = _next_action.ActionableError

# 列の定義と旧形式の判別は本モジュールだけが持ち、`atk serve`の計画ファイル画面も`display_rows`で同じ解析を使う。
COLUMNS = (
    "round",
    "track",
    "location",
    "issue",
    "level",
    "response",
    "no-response-reason",
)
COLUMN_LABELS = (
    "ラウンド",
    "系統",
    "箇所",
    "指摘内容",
    "指摘レベル",
    "対応内容",
    "対応不要理由",
)
"""`COLUMNS`の各列を画面へ表示する日本語の列名。"""
_COLUMN_COUNT = len(COLUMNS)
# 保存済みの旧形式の列数。8列は`level`と`response-needed`の双方を持ち、7列は`response-needed`だけを持つ。
_LEGACY_WIDE_COLUMN_COUNT = 8
_KEY_COLUMN_COUNT = 4
TRACK_VALUES = ("plan-review", "exec-review", "plan-conformance", "independent")
_TRACK_ALIASES = {"implementation-review": "exec-review"}
_TRACK_READ_VALUES = (*TRACK_VALUES, *_TRACK_ALIASES)
_TRACK_INPUT_VALUES = ("exec-review",)
LEVEL_VALUES = ("要件", "仕様", "詳細", "実装")
_RECOVERY_GUIDANCE = (
    f"期待列数は{_COLUMN_COUNT}、trackの位置はroundの直後、"
    f"levelの位置はissueの直後、levelの正規値集合は{', '.join(LEVEL_VALUES)}、"
    f"trackの正規値集合は{', '.join(TRACK_VALUES)}。"
    "implementation-reviewはexec-reviewとして読み取る。"
    f"保存済み{_LEGACY_WIDE_COLUMN_COUNT}列形式はresponse-neededを読み込みの対象から外す。"
    f"保存済み{_COLUMN_COUNT}列形式のうち5列目がyes/noの値域を持つ行は旧形式とみなし、"
    "levelを空としてresponse-neededを読み込みの対象から外す"
)
_FIX_FORMAT_NEXT_ACTION = f"表を次の形式へ直して再実行する。{_RECOVERY_GUIDANCE}"
_FIX_ROW_NEXT_ACTION = "`atk review-table show <PATH>`で該当行を確認し、表を直して再実行する"
# 応答本文の必須ラベル。各ラベルに書く内容は`agent-toolkit:/share/exec-review-responses.md`が定める。
# 類似見直しを終えた件数と不採用の根拠を記録時点で書かせるため、列を増やさず本文の行頭ラベルで表す。
# 書く欄は増えるほど記録が遅くなるため、類似見直しは文章でなく件数だけを書かせる。
SIMILAR_REVIEW_LABEL = "類似見直し完了件数:"
RESPONSE_LABELS = ("違反を確認した規定:", SIMILAR_REVIEW_LABEL, "採用する修正範囲:", "採用しない修正方針:")
NO_RESPONSE_REASON_LABELS = ("根拠の所在:", "判断主体:")
DECISION_MAKER_VALUES = ("メイン", "レーン担当", "統括", "ユーザー", "レビュー担当")
_TRANSFER_TARGET_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md")
_LABEL_DEFINITION = "`agent-toolkit:/share/exec-review-responses.md`「レビュー指摘管理表への応答」"
# レビュー担当の指摘本文の必須ラベル。類似見直しを終えた件数を書く欄が無いと、見直しをしない選択が残るため必須にする。
# 2回目以降のラウンドでは、指摘が前回の修正不足・修正の副作用・前回の見落とし・新しい変更・要求追加のどれかを区分で書かせる。
ISSUE_LABELS = (SIMILAR_REVIEW_LABEL,)
_COUNT_RE = re.compile(r"[0-9]+")
CLASSIFICATION_LABEL = "区分:"
CLASSIFICATION_VALUES = ("前回から残存", "修正に起因", "前回の見落とし", "新しい変更", "要求追加")
_ISSUE_LABEL_DEFINITION = "`agent-toolkit:/share/exec-review-recording-issues.md`「レビュー指摘管理表への登録」"
# レビュー担当の指摘ではない担当の記録の書き出し（`agent-toolkit:/share/review-table-operations.md`）。
# この行は指摘本文の必須ラベルと再確認の対象外とする。
_OWN_RECORD_PREFIX = "担当の記録:"
# 再レビューでレビュー担当が前回の指摘行の応答を確かめた判定。応答のある列の末尾へ1行ずつ追記する。
# 対応不十分・未対応の詳細は`recheck`が現在のroundへ登録する行に書くため、判定の行には根拠の文章を書かせず、
# 登録した行の識別子だけを書く。登録先の行をレビュー担当に選ばせると、別の指摘の行を指しても収束と判定できたため、
# 登録はこのCLIだけが行い、登録した行へ元の指摘の識別子（`元の指摘: 行<row-id>`）を書く。
RECHECK_LABEL = "再確認:"
RECHECK_VERDICTS = ("完了", "対応不十分", "未対応")
RECHECK_DONE = RECHECK_VERDICTS[0]
REREGISTERED_CLASSIFICATION = "前回から残存"
ORIGIN_LABEL = "元の指摘:"
_RECHECK_LINE_RE = re.compile(rf"{RECHECK_LABEL} ラウンド([1-9][0-9]*) ({'|'.join(RECHECK_VERDICTS)})(?: 行([1-9][0-9]*))?")
_ORIGIN_LINE_RE = re.compile(rf"{ORIGIN_LABEL} 行([1-9][0-9]*)")
_YES_VALUES = frozenset({"yes", "true", "1", "required", "対応要"})
_NO_VALUES = frozenset({"no", "false", "0", "not-required", "対応不要"})
_WHITESPACE_RE = re.compile(r"\s+")
_ROUND_RE = re.compile(r"^[1-9][0-9]*$")


def _cell(value: str) -> str:
    """セル文字列をJSON文字列へ符号化する。"""
    return json.dumps(value, ensure_ascii=False)


def _row_position(row_id: int) -> str:
    """showとrespondが使う行識別子を診断へ表示する。"""
    return f"row-id {row_id}"


def _physical_position(line: int, column: int | None = None) -> str:
    """TSVの構文不正の物理位置を表示する。"""
    position = f"物理行番号 {line}"
    return position if column is None else f"{position}、列番号 {column}"


def _decode_cell(value: str, *, line: int, column: int) -> str:
    """JSON文字列セルをデコードし、形式不正をエラーにする。"""
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"{_physical_position(line, column)}がJSON文字列ではない") from error
    if not isinstance(decoded, str):
        raise ValueError(f"{_physical_position(line, column)}が文字列ではない")
    return decoded


def _normalize_track(value: str) -> str:
    """レビューtrackの読み取り互換値を正規値へ変換する。"""
    return _TRACK_ALIASES.get(value, value)


def _drop_legacy_response_needed(row: list[str]) -> list[str]:
    """保存済みの旧形式から`response-needed`列を除き、現行の7列へそろえる。

    列数が8の行は`level`と`response-needed`の双方を持つ旧形式である。
    列数が7の行は現行形式と、`level`を持たない旧形式のいずれかであり、5列目の値域で判別する。
    5列目が`level`の正規値であれば現行形式、yes/noの値域であれば旧形式とする。
    5列目が空の行は、残る2列が空であれば両解釈が同じ値へ帰着するため現行形式として扱い、
    いずれかが埋まっていればlevelを空とする現行形式と確定する。
    """
    if len(row) == _LEGACY_WIDE_COLUMN_COUNT:
        return [*row[:5], *row[6:]]
    fifth = _normalized(row[4]).casefold()
    if fifth in _YES_VALUES or fifth in _NO_VALUES:
        return [*row[:4], "", *row[5:]]
    return row


def _decode_text(text: str) -> list[tuple[str, list[str]]]:
    """Raw TSVを検証し、元の行と、旧形式の列を現行の7列へそろえたデコード済み行を対応づけて返す。

    trackの読み取り互換値は変換しない。
    """
    rows: list[tuple[str, list[str]]] = []
    for line_number, raw_line in enumerate(text.splitlines(keepends=True), start=1):
        line = raw_line.rstrip("\r\n")
        if not line:
            continue
        cells = line.split("\t")
        if len(cells) not in (_COLUMN_COUNT, _LEGACY_WIDE_COLUMN_COUNT):
            raise _ActionableError(
                f"{_physical_position(line_number)}の列数が{_COLUMN_COUNT}ではない: {len(cells)}",
                next_action=_FIX_FORMAT_NEXT_ACTION,
            )
        row = [_decode_cell(cell, line=line_number, column=index) for index, cell in enumerate(cells, start=1)]
        rows.append((raw_line, _drop_legacy_response_needed(row)))
    return rows


def _parse_text(text: str) -> list[tuple[str, list[str]]]:
    """Raw TSVを検証し、元の行とtrack正規化済みのデコード済み行を対応づけて返す。"""
    rows = _decode_text(text)
    for _raw_line, row in rows:
        row[1] = _normalize_track(row[1])
    return rows


def display_rows(text: str) -> list[list[str]]:
    """表示のために、レビュー指摘管理表の本文を`COLUMNS`の順の7列の行へ解析して返す。

    保存済みの旧形式は現行の7列へそろえ、trackは記録された値のまま返す。
    形式が不正な表は`ValueError`を送出し、表示側が表として描画しない扱いを選ぶ。
    """
    return [row for _raw_line, row in _decode_text(text)]


def _label_values(body: str, label: str) -> list[str]:
    return [line.strip()[len(label) :].strip() for line in body.splitlines() if line.strip().startswith(label)]


def response_state(row: list[str]) -> str:
    """記録された応答を採用・移管・不採用・未応答へ分類する。旧記録の自由文は推定しない。"""
    if row[5].strip():
        return "adopted"
    if not row[6].strip():
        return "unanswered"
    targets = _label_values(row[6], "移管先:")
    if len(targets) == 1 and _TRANSFER_TARGET_RE.fullmatch(targets[0]):
        return "transferred"
    return "rejected"


def rejection_reason_class(body: str) -> str:
    """対応不要理由の判断主体ラベルだけから理由区分を求める。"""
    makers = _label_values(body, "判断主体:")
    if len(makers) != 1 or makers[0] not in DECISION_MAKER_VALUES:
        return "判断主体不明"
    return "レビュー担当の前提の誤り・撤回" if makers[0] == "レビュー担当" else "判断による見送り"


def _normalized(value: str) -> str:
    """複合キー用にUnicode、前後空白および連続空白を正規化する。"""
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", value).strip())


def _key(row: list[str]) -> tuple[str, str, str, str]:
    return (
        _normalized(row[0]),
        _normalized(row[1]),
        _normalized(row[2]),
        _normalized(row[3]),
    )


def _validate_rows(rows: list[list[str]], *, require_responses: bool = False) -> None:
    """7列、先頭4列の複合キー一意性、指摘レベルおよび応答分岐を検証する。

    対応要否は専用の列を持たず、`response`と`no-response-reason`のどちらが埋まっているかで表す。
    双方が空である行を未応答とし、双方が埋まっている行を矛盾として拒否する。
    """
    keys: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if len(row) != _COLUMN_COUNT:
            raise _ActionableError(
                f"{_row_position(index)}の列数が{_COLUMN_COUNT}ではない", next_action=_FIX_FORMAT_NEXT_ACTION
            )
        if any(not _normalized(value) for value in row[:_KEY_COLUMN_COUNT]):
            raise _ActionableError(f"{_row_position(index)}の先頭4列は空にできない", next_action=_FIX_ROW_NEXT_ACTION)
        if _ROUND_RE.match(_normalized(row[0])) is None:
            raise _ActionableError(f"{_row_position(index)}のラウンドが1以上の整数ではない", next_action=_FIX_ROW_NEXT_ACTION)
        if row[1] not in TRACK_VALUES:
            raise _ActionableError(f"{_row_position(index)}のtrackが正規値ではない", next_action=_FIX_FORMAT_NEXT_ACTION)
        if row[4] and row[4] not in LEVEL_VALUES:
            raise _ActionableError(f"{_row_position(index)}のlevelが正規値ではない", next_action=_FIX_FORMAT_NEXT_ACTION)
        key = _key(row)
        if key in keys:
            raise _ActionableError(f"{_row_position(index)}の先頭4列が重複している", next_action=_FIX_ROW_NEXT_ACTION)
        keys.add(key)
        response = row[5].strip()
        reason = row[6].strip()
        if response and reason:
            raise _ActionableError(
                f"{_row_position(index)}は対応内容と対応不要理由を同時に持てない",
                next_action=f"`atk review-table respond <PATH> --row-id {index}`で対応内容か対応不要理由の一方だけを記録し直す",
            )
        if require_responses and not response and not reason:
            raise _ActionableError(
                f"{_row_position(index)}が未応答である",
                next_action=(
                    f"`atk review-table respond <PATH> --row-id {index}`へ--response-fileか"
                    "--no-response-reason-fileを指定して応答する"
                ),
            )


def _require_labels(body: str, labels: tuple[str, ...], option: str, definition: str = _LABEL_DEFINITION) -> None:
    """本文が各必須ラベルを行頭に持ち、ラベルの後に空でない記述を持ち、類似見直し完了件数が整数だけであることを確かめる。"""
    lines = [line.strip() for line in body.splitlines()]
    missing = [label for label in labels if not any(line.startswith(label) and line[len(label) :].strip() for line in lines)]
    if missing:
        raise _ActionableError(
            f"{option}の本文に必須ラベルが無いか、ラベルの後の記述が空である: {'、'.join(missing)}",
            next_action=(
                f"欠けたラベルを行頭に置いて同じ行へ内容を書き、同じコマンドを再実行する。各ラベルに書く内容は{definition}が定める"
            ),
        )
    if SIMILAR_REVIEW_LABEL in labels:
        counts = _label_values(body, SIMILAR_REVIEW_LABEL)
        if len(counts) != 1 or _COUNT_RE.fullmatch(counts[0]) is None:
            raise _ActionableError(
                f"{option}の{SIMILAR_REVIEW_LABEL}が整数1件ではない",
                next_action=f"{SIMILAR_REVIEW_LABEL} の行を1つ置き、類似見直しで確かめた箇所の件数を整数だけで書く",
            )


def _require_issue_labels(location: str, issue: str, round_value: str, option: str) -> None:
    """レビュー担当の指摘本文が類似見直し完了件数と、2回目以降のラウンドでは区分を持つことを確かめる。

    担当の記録（指摘箇所が`担当の記録:`で始まる行）はレビュー担当の指摘ではないため対象外とする。
    """
    if location.strip().startswith(_OWN_RECORD_PREFIX):
        return
    _require_labels(issue, ISSUE_LABELS, option, _ISSUE_LABEL_DEFINITION)
    normalized_round = _normalized(round_value)
    if _ROUND_RE.match(normalized_round) is None or int(normalized_round) < 2:
        return
    values = _label_values(issue, CLASSIFICATION_LABEL)
    if len(values) != 1 or values[0] not in CLASSIFICATION_VALUES:
        raise _ActionableError(
            f"{option}の本文に2回目以降のラウンドで必須の区分が1件ない",
            next_action=(
                f"{CLASSIFICATION_LABEL} の行を1つ置き、値を{', '.join(CLASSIFICATION_VALUES)}のいずれか1つだけにする。"
                f"各区分の意味は{_ISSUE_LABEL_DEFINITION}が定める"
            ),
        )
    if values[0] == REREGISTERED_CLASSIFICATION:
        raise _ActionableError(
            f"{option}の区分{REREGISTERED_CLASSIFICATION}はaddでは登録できない",
            next_action=(
                "前回の指摘が残る場合は`atk review-table recheck <PATH> --row-id <前回の指摘の行> --round <現在のround> "
                "--verdict <対応不十分|未対応> --issue-file <残る問題の本文>`で記録する。"
                "区分と元の指摘の行を書いた指摘行をrecheckが登録する"
            ),
        )


def classification(issue: str) -> str | None:
    """指摘本文の区分を1件だけ持つ場合にその値を返す。"""
    values = _label_values(issue, CLASSIFICATION_LABEL)
    return values[0] if len(values) == 1 else None


def recheck_entries(body: str) -> list[tuple[int, str, int | None]]:
    """recheckが記録した判定行を、記録したround・判定・登録した行へ分解する。形式外の行は判定として扱わない。"""
    entries: list[tuple[int, str, int | None]] = []
    for line in body.splitlines():
        matched = _RECHECK_LINE_RE.fullmatch(line.strip())
        if matched is not None:
            row = matched.group(3)
            entries.append((int(matched.group(1)), matched.group(2), int(row) if row else None))
    return entries


def carried_origin(issue: str) -> int | None:
    """recheckが登録した指摘本文の`元の指摘: 行<row-id>`を1件だけ持つ場合に、その識別子を返す。"""
    origins = [matched.group(1) for line in issue.splitlines() if (matched := _ORIGIN_LINE_RE.fullmatch(line.strip()))]
    return int(origins[0]) if len(origins) == 1 else None


def require_carried_issue(issue: str, option: str) -> None:
    """recheckへ渡す残る問題の本文が類似見直し完了件数を持ち、CLIが書く区分と元の指摘の行を持たないことを確かめる。"""
    _require_labels(issue, ISSUE_LABELS, option, _ISSUE_LABEL_DEFINITION)
    if any(line.strip().startswith((CLASSIFICATION_LABEL, ORIGIN_LABEL)) for line in issue.splitlines()):
        raise _ActionableError(
            f"{option}の本文に{CLASSIFICATION_LABEL}か{ORIGIN_LABEL}で始まる行がある",
            next_action="区分と元の指摘の行はrecheckが書くため、その行を除いて同じコマンドを再実行する",
        )


def carried_issue(issue: str, origin_row_id: int) -> str:
    """recheckが登録する指摘本文を、区分と元の指摘の行を先頭に置いて組み立てる。"""
    return f"{CLASSIFICATION_LABEL} {REREGISTERED_CLASSIFICATION}\n{ORIGIN_LABEL} 行{origin_row_id}\n{issue}"


def withdrawn_reason(origin_row_id: int, round_value: int) -> str:
    """同じroundの判定を完了へ記録し直したときに、recheckが登録済みの行へ書く対応不要理由（レビュー担当の撤回）を返す。"""
    return f"根拠の所在: 行{origin_row_id}のラウンド{round_value}の再確認を完了へ記録し直した\n判断主体: レビュー担当"


def reject_recheck_label(body: str, option: str) -> None:
    """再確認ラベルはレビュー担当の判定だけに使うため、応答本文での使用を拒否する。"""
    if any(line.strip().startswith(RECHECK_LABEL) for line in body.splitlines()):
        raise _ActionableError(
            f"{option}の本文に{RECHECK_LABEL}で始まる行がある",
            next_action=(
                f"{RECHECK_LABEL}はレビュー担当が`atk review-table recheck`で記録する判定に使うため、"
                "その行を除くか行頭の語を変えて同じコマンドを再実行する"
            ),
        )
