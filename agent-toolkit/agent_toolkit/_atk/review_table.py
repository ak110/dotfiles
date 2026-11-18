"""レビュー指摘管理表の7列TSVを排他更新する。

列とラベルの書式は`review_table_format`、`atk review-table`の引数の解析は`review_table_cli`が扱う。

排他ロックはロックファイルのディレクトリ（`agent_toolkit._common.state_paths.lock_dir`）へ置く。
表の配置先には`~/.claude/plans`が含まれる。兄弟のロックファイルを生成すると、計画バンドルの回収後も
ロックだけが`~/.claude/plans`へ残存し、計画の一覧と親ディレクトリの回収を妨げる。
表の本体ファイル自身へのロックには移行できない。更新は一時ファイルの原子的置換で行い、
置換のたびにinodeが変わるため、本体を開いて取得したロックは後続の更新と同じ実体を指さない。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shlex
from collections.abc import Callable, Generator
from pathlib import Path
from typing import NotRequired, TypedDict

from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.review_table_format import (
    _KEY_COLUMN_COUNT,
    _OWN_RECORD_PREFIX,
    _ROUND_RE,
    _TRACK_READ_VALUES,
    _TRANSFER_TARGET_RE,
    COLUMNS,
    DECISION_MAKER_VALUES,
    NO_RESPONSE_REASON_LABELS,
    RECHECK_LABEL,
    RECHECK_VERDICTS,
    RESPONSE_LABELS,
    TRACK_VALUES,
    _cell,
    _key,
    _label_values,
    _normalize_track,
    _normalized,
    _parse_text,
    _require_labels,
    _row_position,
    _validate_rows,
)
from agent_toolkit._common import body_match as _body_match
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write

_INPUT_GUIDANCE = (
    "計画ファイルと同じstemの`.exec-review.tsv`、またはCI対応レビュー指摘管理表の"
    f"`{_atk_help.CI_REVIEW_TABLE_NAME_FORMAT}`を"
    "通常ファイルの絶対パスで指定する。"
    "標準入力、パイプおよびプロセス置換は受理しない"
)
# `add`の不一致はlocation列かissue列で生じ、`respond`は応答列だけを更新するため、案内を操作ごとに分ける。
_SAVED_ADD_MISMATCH_NEXT_ACTION = (
    "保存は済んでいる。`atk review-table show <PATH>`で追加した行のlocation列とissue列を確認し、"
    "送信元本文と異なる場合は表ファイルの該当行を送信元本文へ直す"
)
_SAVED_RESPONSE_MISMATCH_NEXT_ACTION = (
    "保存は済んでいる。`atk review-table show <PATH>`で保存結果を確認し、"
    "`atk review-table respond <PATH> --row-id <ROW_ID>`で直す"
)
_SAVED_UNRESOLVED_NEXT_ACTION = (
    "`atk review-table show <PATH>`で保存結果を確認する。解消しない場合はagent-toolkitの不具合としてユーザーへ報告する"
)
_ActionableError = _next_action.ActionableError


class _PreviousFindings(TypedDict):
    """同じtrackの過去roundと保存順の行識別子。"""

    round: int
    row_ids: list[int]


class _Summary(TypedDict):
    """現在roundの応答件数と、比較できる過去指摘への到達情報。"""

    round: int
    track: str
    total_count: int
    unanswered_count: int
    answered_count: int
    previous_findings: NotRequired[list[_PreviousFindings]]
    history_command: NotRequired[str]
    repetition_guidance: NotRequired[str]
    recheck_round: NotRequired[int]
    recheck_pending_row_ids: NotRequired[list[int]]
    recheck_pending_count: NotRequired[int]
    recheck_counts: NotRequired[dict[str, int]]


def _path(raw_path: str) -> Path:
    """表ファイルパスを絶対パスへ解決する。"""
    return Path(raw_path).expanduser().resolve()


def _read_table_text(path: Path) -> str:
    """レビュー指摘管理表の本文をUTF-8で読む。

    通常ファイル以外は読み込みを試みずに拒否する。標準入力とプロセス置換は`/dev/fd`配下の
    パイプとして渡り、読み込むと書き込み側を待って停止するためである。
    存在しない場合と通常ファイルでない場合を別の文面にするのは、パイプを`/dev/stdin`として
    渡すと`_path`の解決が存在しないパスへ至り、同じ文面では原因を判別できないためである。
    """
    if not path.exists():
        raise _ActionableError(f"レビュー指摘管理表を読み込めない: {path}: 存在しない", next_action=_INPUT_GUIDANCE)
    if not path.is_file():
        raise _ActionableError(f"レビュー指摘管理表を読み込めない: {path}: 通常ファイルではない", next_action=_INPUT_GUIDANCE)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise _ActionableError(f"レビュー指摘管理表を読み込めない: {path}: {error}", next_action=_INPUT_GUIDANCE) from error


def _read(path: Path) -> list[list[str]]:
    """TSVを読み、JSONデコード済みの行一覧を返す。"""
    return [row for _, row in _parse_text(_read_table_text(path))]


def read_rows(path: str | Path) -> list[list[str]]:
    """レビュー指摘管理表の各行をデコード済みのセル列として返す。読み込めない場合は理由と次の操作を持つ例外を送出する。"""
    return _read(Path(path))


def validate(path: str | Path, *, require_responses: bool = True) -> int:
    """表全体を検証し、件数を標準出力へ表示する。"""
    target = _path(str(path))
    rows = _read(target)
    _validate_rows(rows, require_responses=require_responses)
    label = "応答欄を含めて検証した" if require_responses else "構造を検証した"
    _outcome.report_success(f"{label}: {target}（{len(rows)}件）")
    return 0


def _write_atomic(path: Path, rows: list[list[str]]) -> None:
    """行を一時ファイルへ書き、同一ディレクトリ内で原子的に置換する。"""
    content = "".join("\t".join(_cell(value) for value in row) + "\n" for row in rows)
    atomic_write(path, content, fsync=True)


def lock_path(path: str | Path) -> Path:
    """レビュー指摘管理表の排他に使うロックファイルのパスを返す。

    格納先を表と同じディレクトリから分離し、対象の絶対パスのダイジェストで名前を一意にする。
    分離の理由と本体ファイル自身をロックできない理由はモジュールのdocstringが述べる。
    ロックファイルは解放後も削除しない。削除してから再取得するまでの間に別の主体が同名のファイルを
    生成した場合、別々の実体へロックを取得した2主体が同時に表を更新する。
    """
    target = Path(path).expanduser().resolve(strict=False)
    digest = hashlib.sha256(target.as_posix().encode("utf-8")).hexdigest()[:32]
    return _state_paths.lock_dir() / "review-table" / f"{digest}.lock"


@contextlib.contextmanager
def _table_lock(path: Path) -> Generator[None]:
    """レビュー指摘管理表の排他ロックを取得し、離脱時に解放する。"""
    target_lock = lock_path(path)
    target_lock.parent.mkdir(parents=True, exist_ok=True)
    with target_lock.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        try:
            yield
        finally:
            _file_lock.release_lock(lock_file)


def _locked_update(
    path: Path,
    updater: Callable[[list[list[str]]], list[list[str]]],
    *,
    verify: Callable[[list[list[str]], list[list[str]]], None] | None = None,
) -> list[list[str]]:
    """ロック内で再読込・検証・更新・原子的置換を実行する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _table_lock(path):
        rows = _read(path) if path.exists() else []
        _validate_rows(rows)
        updated = updater(rows)
        _validate_rows(updated)
        _write_atomic(path, updated)
        if verify is not None:
            verify(updated, _read(path))
        return updated


def init(path: str | Path) -> int:
    """存在しない表を作成する。"""
    target = _path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    with _table_lock(target):
        if target.exists():
            raise _ActionableError(f"レビュー指摘管理表が既に存在する: {target}", next_action="既存の表をそのまま使う")
        _write_atomic(target, [])
    _outcome.report_success(f"レビュー指摘管理表を作成した: {target}", _outcome.ResultKind.VALUE_OUTPUT)
    print(target)
    return 0


def add(path: str | Path, round_value: str, track: str, location: str, issue: str, level: str = "詳細") -> int:
    """レビュー担当の指摘行を追加する。"""
    row = [round_value, _normalize_track(track), location, issue, level, "", ""]
    return _add_rows(_path(str(path)), [row])


def _add_rows(target: Path, additions: list[list[str]]) -> int:
    """全入力を検証して1回追記し、ロック内で保存した全行を読み戻す。"""
    _validate_rows(additions)

    def updater(rows: list[list[str]]) -> list[list[str]]:
        if {_key(row) for row in additions} & {_key(existing) for existing in rows}:
            raise _ActionableError(
                "先頭4列の複合キーが重複している",
                next_action="同じ指摘なら追加は不要。別の指摘ならlocationかissueを区別して追加し直す",
            )
        return [*rows, *additions]

    def verify(expected_rows: list[list[str]], saved_rows: list[list[str]]) -> None:
        by_key: dict[tuple[str, str, str, str], list[list[str]]] = {}
        for saved in saved_rows:
            by_key.setdefault(_key(saved), []).append(saved)
        for row in additions:
            matches = by_key.get(_key(row), [])
            if len(matches) != 1:
                raise _ActionableError(
                    f"追加した行を保存済みの表から一意に解決できない: {len(matches)}件",
                    next_action=_SAVED_UNRESOLVED_NEXT_ACTION,
                )
            for column in ("location", "issue"):
                index = COLUMNS.index(column)
                expected, saved = row[index], matches[0][index]
                if _body_match.verdict(expected, saved) == "一致":
                    continue
                position = _body_match.first_difference(expected, saved)
                raise _ActionableError(
                    f"保存本文が送信元本文と一致しない: {target}\n"
                    f"不一致の列: {column}\n"
                    f"最初の差異: {position}文字目\n"
                    f"送信元本文:\n{expected}\n"
                    f"保存本文:\n{saved}",
                    next_action=_SAVED_ADD_MISMATCH_NEXT_ACTION,
                )
        if expected_rows != saved_rows:
            raise _ActionableError(
                f"保存済みの表の全行が更新内容と一致しない: {target}",
                next_action=_SAVED_ADD_MISMATCH_NEXT_ACTION,
            )

    rows = _locked_update(target, updater, verify=verify)
    _outcome.report_success(f"指摘行を{len(additions)}件追加した: {target}（{len(rows)}件）")
    return 0


def _row_index(row_id: int, row_count: int) -> int:
    """表示と応答が共有する保存順の行識別子を添字へ変換する。"""
    if row_id < 1 or row_id > row_count:
        raise _ActionableError(
            f"row-idが範囲外である: {row_id}（行数: {row_count}）",
            next_action="`atk review-table show <PATH>`でrow-idを確認して指定し直す",
        )
    return row_id - 1


def _format_key_diagnostic(rows: list[list[str]], given: list[tuple[int, str]], matches: list[int]) -> str:
    """一意に解決できない部分キーとデコード済み候補行を整形する。"""
    requested = ", ".join(f"{COLUMNS[index]}={value}" for index, value in given) or "なし"
    candidate_rows = [rows[index] for index in matches] if matches else rows
    candidate_lines = [
        "  - "
        + ", ".join(f"{column}={_normalized(value)}" for column, value in zip(COLUMNS[:_KEY_COLUMN_COUNT], row, strict=False))
        for row in candidate_rows
    ]
    candidates = "\n".join(candidate_lines) or "  - 候補行なし"
    return (
        f"指定された部分キー: {requested}\n"
        f"候補行（デコード済み）:\n{candidates}\n"
        "レビュー指摘管理表のセルはJSON文字列として保存されるため、キーにはデコード後の値を指定する。\n"
        "改行・記号を含む`location`は、シェルが改行や記号を解釈しないよう`--location-file <絶対パス>`で渡す。"
    )


def respond(
    path: str | Path,
    round_value: str,
    track: str,
    location: str,
    issue: str,
    response: str,
    no_response_reason: str,
    row_id: int | None = None,
) -> int:
    """レビューイーの応答欄だけを部分キーで更新する。

    `round`・`track`・`location`・`issue`のうち非空で与えられた列だけを比較対象とし、
    該当行を特定する。該当行が1件でない場合は複合キー解決不能として拒否する。
    対応要否は対応内容と対応不要理由のどちらを渡したかで決まる。双方を渡した場合と
    どちらも渡さなかった場合は`ValueError`で拒否する。
    """
    target = _path(str(path))
    replacement = response.strip()
    reason = no_response_reason.strip()
    if replacement and reason:
        raise _ActionableError(
            "対応内容と対応不要理由は同時に指定できない",
            next_action="--response-fileと--no-response-reason-fileのどちらか一方だけを指定する",
        )
    if not replacement and not reason:
        raise _ActionableError(
            "対応内容と対応不要理由のどちらも指定されていない",
            next_action="--response-fileか--no-response-reason-fileのどちらか一方を指定する",
        )
    needed = "yes" if replacement else "no"
    if replacement:
        _require_labels(replacement, RESPONSE_LABELS, "--response-file")
    else:
        _require_labels(reason, NO_RESPONSE_REASON_LABELS, "--no-response-reason-file")
        makers = _label_values(reason, "判断主体:")
        if len(makers) != 1 or makers[0] not in DECISION_MAKER_VALUES:
            raise _ActionableError(
                "--no-response-reason-fileの判断主体が正規値1件ではない",
                next_action=f"判断主体: の行を1つ置き、値を{', '.join(DECISION_MAKER_VALUES)}から指定する",
            )
        targets = _label_values(reason, "移管先:")
        if targets and (len(targets) != 1 or not _TRANSFER_TARGET_RE.fullmatch(targets[0])):
            raise _ActionableError(
                "--no-response-reason-fileの移管先がWIのファイル名1件ではない",
                next_action="移管先: の行を1つ置き、登録済みWIのファイル名（YYYYMMDD-HHMMSS-NNN.md）を書く",
            )
    track = _normalize_track(track)
    given = [
        (index, _normalized(value)) for index, value in enumerate((round_value, track, location, issue)) if _normalized(value)
    ]
    if row_id is not None and row_id < 1:
        raise _ActionableError(f"row-idが1未満である: {row_id}", next_action="row-idは1以上の整数で指定する")
    if row_id is not None and given:
        raise _ActionableError(
            "row-idはround・track・location・issueと同時に指定できない",
            next_action="--row-idか、--round・--track・--location-file・--issue-fileのどちらか一方で行を指定する",
        )

    def updater(rows: list[list[str]]) -> list[list[str]]:
        if row_id is not None:
            matches = [_row_index(row_id, len(rows))]
        else:
            matches = [
                row_index
                for row_index, row in enumerate(rows)
                if all(_normalized(row[column_index]) == value for column_index, value in given)
            ]
        if len(matches) != 1:
            diagnostic = _format_key_diagnostic(rows, given, matches)
            raise _ActionableError(
                f"応答対象の複合キーが一意に解決できない: {len(matches)}件\n{diagnostic}",
                next_action="`atk review-table show <PATH>`でrow-idを確認し、--row-idで指定し直す",
            )
        updated = [*rows]
        updated[matches[0]] = [*updated[matches[0]][:_KEY_COLUMN_COUNT], updated[matches[0]][4], replacement, reason]
        return updated

    _locked_update(target, updater)
    current_rows = _read(target)
    saved_rows = (
        [current_rows[row_id - 1]]
        if row_id is not None and 1 <= row_id <= len(current_rows)
        else [row for row in current_rows if all(_normalized(row[column_index]) == value for column_index, value in given)]
    )
    if len(saved_rows) != 1:
        raise _ActionableError(
            f"更新した行を保存済みの表から一意に解決できない: {len(saved_rows)}件", next_action=_SAVED_UNRESOLVED_NEXT_ACTION
        )
    saved_row = saved_rows[0]
    saved_body = saved_row[5] if needed == "yes" else saved_row[6]
    expected_body = replacement if needed == "yes" else reason
    if _body_match.verdict(expected_body, saved_body) != "一致":
        position = _body_match.first_difference(expected_body, saved_body)
        column = "response" if needed == "yes" else "no_response_reason"
        raise _ActionableError(
            f"保存本文が送信元本文と一致しない: {target}\n"
            f"不一致の列: {column}\n"
            f"最初の差異: {position}文字目\n"
            f"送信元本文:\n{expected_body}\n"
            f"保存本文:\n{saved_body}",
            next_action=_SAVED_RESPONSE_MISMATCH_NEXT_ACTION,
        )
    _outcome.report_success(f"応答欄を更新した: {target}")
    return 0


def recheck(path: str | Path, row_id: int, round_value: str, verdict: str) -> int:
    """再レビューでレビュー担当が前回の指摘行の応答を確かめた判定を、応答のある列の末尾へ1行で追記する。"""
    target = _path(str(path))
    normalized_round = _normalized(round_value)
    if _ROUND_RE.match(normalized_round) is None or int(normalized_round) < 2:
        raise _ActionableError(
            "再確認のroundは2以上の整数が必要である", next_action="--roundへ現在の再レビューのラウンド番号を指定する"
        )
    if verdict not in RECHECK_VERDICTS:
        raise _ActionableError(
            f"再確認の判定が正規値ではない: {verdict}", next_action=f"--verdictを{', '.join(RECHECK_VERDICTS)}から指定する"
        )
    block = f"{RECHECK_LABEL} ラウンド{normalized_round} {verdict}"
    column_holder: list[int] = []

    def updater(rows: list[list[str]]) -> list[list[str]]:
        index = _row_index(row_id, len(rows))
        row = rows[index]
        if int(_normalized(row[0])) >= int(normalized_round):
            raise _ActionableError(
                f"{_row_position(row_id)}は前のラウンドの指摘ではない",
                next_action="再確認は現在のラウンドより前の指摘行へ記録する",
            )
        if row[2].strip().startswith(_OWN_RECORD_PREFIX):
            raise _ActionableError(
                f"{_row_position(row_id)}は担当の記録でありレビュー担当の指摘ではない",
                next_action="再確認はレビュー担当の指摘行だけへ記録する",
            )
        column = 5 if row[5].strip() else 6 if row[6].strip() else None
        if column is None:
            raise _ActionableError(
                f"{_row_position(row_id)}は未応答である",
                next_action="レビューイーの応答の記録を待ってから再確認する。未応答の指摘は未解決として扱う",
            )
        if _label_values(row[column], RECHECK_LABEL):
            raise _ActionableError(
                f"{_row_position(row_id)}の再確認は記録済みである",
                next_action="`atk review-table show <PATH> --row-id <ROW_ID> --field response`で記録済みの判定を確認する",
            )
        column_holder.append(column)
        updated = [*rows]
        updated[index] = [*row]
        updated[index][column] = f"{row[column].rstrip()}\n{block}"
        return updated

    _locked_update(target, updater)
    saved = _read(target)[row_id - 1]
    if not column_holder or not saved[column_holder[0]].endswith(block):
        raise _ActionableError(f"再確認の保存を確認できない: {target}", next_action=_SAVED_UNRESOLVED_NEXT_ACTION)
    _outcome.report_success(f"再確認を記録した: {target}（{_row_position(row_id)}、{verdict}）")
    return 0


def _recheck_state(rows: list[list[str]], round_value: int, track: str) -> tuple[int, list[int], dict[str, int]] | None:
    """現在より前で指摘のある最新ラウンドについて、再確認の無い行と判定別の件数を返す。"""
    candidates = [
        (row_id, row)
        for row_id, row in enumerate(rows, start=1)
        if row[1] == track and int(_normalized(row[0])) < round_value and not row[2].strip().startswith(_OWN_RECORD_PREFIX)
    ]
    if not candidates:
        return None
    latest = max(int(_normalized(row[0])) for _, row in candidates)
    pending: list[int] = []
    counts: dict[str, int] = dict.fromkeys(RECHECK_VERDICTS, 0)
    for row_id, row in candidates:
        if int(_normalized(row[0])) != latest:
            continue
        verdicts = [value for cell in (row[5], row[6]) for value in _label_values(cell, RECHECK_LABEL)]
        if not verdicts:
            pending.append(row_id)
            continue
        verdict = verdicts[-1].split()[-1] if verdicts[-1].split() else ""
        if verdict in counts:
            counts[verdict] += 1
    return latest, pending, counts


def summary(path: str | Path, round_value: int, *, track: str = "exec-review") -> _Summary:
    """指定ラウンドに登録した指摘を用いて未応答件数を導き、過去の応答を混ぜない。"""
    if round_value < 1:
        raise _ActionableError("roundは1以上の整数で指定する", next_action="現在のレビューの--roundを指定して再実行する")
    rows = _read(_path(str(path)))
    _validate_rows(rows, require_responses=False)
    selected = [row for row in rows if _normalized(row[0]) == str(round_value) and row[1] == _normalize_track(track)]
    unanswered = sum(not row[5].strip() and not row[6].strip() for row in selected)
    result: _Summary = {
        "round": round_value,
        "track": _normalize_track(track),
        "total_count": len(selected),
        "unanswered_count": unanswered,
        "answered_count": len(selected) - unanswered,
    }
    recheck_state = _recheck_state(rows, round_value, _normalize_track(track))
    if recheck_state is not None:
        result["recheck_round"], result["recheck_pending_row_ids"], result["recheck_counts"] = recheck_state
        result["recheck_pending_count"] = len(recheck_state[1])
    previous: dict[int, list[int]] = {}
    if selected:
        for row_id, row in enumerate(rows, start=1):
            previous_round = int(_normalized(row[0]))
            if row[1] == _normalize_track(track) and previous_round < round_value:
                previous.setdefault(previous_round, []).append(row_id)
    if previous:
        result["previous_findings"] = [
            {"round": previous_round, "row_ids": row_ids} for previous_round, row_ids in sorted(previous.items())
        ]
        result["history_command"] = shlex.join(
            ["atk", "review-table", "show", str(_path(str(path))), f"--track={_normalize_track(track)}", "--format=jsonl"]
        )
        result["repetition_guidance"] = (
            "過去の指摘・応答と今回の指摘を読み、"
            "agent-toolkit/share/review-loop-coordination.md「反復原因の判定」と"
            "agent-toolkit/share/exec-review-responses.md「同じ指摘の反復」に従い、"
            "違反規定と発生条件を比較して反復原因を判定する"
        )
    return result


def show(
    path: str | Path,
    track: str | None = None,
    output_format: str = "tsv",
    round_value: int | None = None,
    *,
    row_id: int | None = None,
    field: str | None = None,
) -> int:
    """表を保存順で表示し、指定時はtrackとラウンドで限定する。"""
    if field is not None and (row_id is None or output_format != "tsv"):
        raise _ActionableError(
            "本文の取得には--row-idとtsv出力形式が必要である",
            next_action="`atk review-table show <PATH> --row-id <ROW_ID> --field response`で本文を取得する",
        )
    if field is not None and field not in ("response", "no-response-reason"):
        raise _ActionableError(
            f"本文の列が正規値ではない: {field}", next_action="--fieldにresponseかno-response-reasonを指定する"
        )
    if output_format == "summary":
        if row_id is not None:
            raise _ActionableError("summaryとrow-idは同時に指定できない", next_action="件数の取得は--row-idを外して再実行する")
        if round_value is None:
            raise _ActionableError(
                "件数の生成にはroundが必要である", next_action="--format=summaryへ現在のレビューの--roundを加えて再実行する"
            )
        print(json.dumps(summary(path, round_value, track=track or "exec-review"), ensure_ascii=False))
        return 0
    target = _path(str(path))
    text = _read_table_text(target)
    rows = _parse_text(text)
    if row_id is not None:
        _row_index(row_id, len(rows))
    if track is not None and track not in _TRACK_READ_VALUES:
        raise _ActionableError(
            f"trackが正規値ではない: {track}", next_action=f"trackを{', '.join(TRACK_VALUES)}のいずれかで指定し直す"
        )
    track = _normalize_track(track) if track is not None else None
    selected = [
        (saved_row_id, raw_line, row)
        for saved_row_id, (raw_line, row) in enumerate(rows, start=1)
        if (row_id is None or row_id == saved_row_id)
        and (track is None or row[1] == track)
        and (round_value is None or row[0] == str(round_value))
    ]
    if field is not None:
        if not selected:
            raise _ActionableError(
                "指定行はtrack・roundの表示条件に一致しない",
                next_action="--trackと--roundを外して同じ--row-idで再実行する",
            )
        print(selected[0][2][COLUMNS.index(field)], end="")
        return 0
    if output_format == "tsv":
        print("".join(f"{row_id}\t{raw_line}" for row_id, raw_line, _ in selected), end="")
        return 0
    for saved_row_id, _, row in selected:
        print(json.dumps({"row-id": saved_row_id, **dict(zip(COLUMNS, row, strict=True))}, ensure_ascii=False))
    return 0
