"""選定の書込予定とレーン配分の構造を確かめる。

書込予定のパス、広すぎる範囲、区分間の重複、導出した新設先、公開工程の規範根拠、
対象リポジトリのnorm-specによる規範指定を検証する。本文から抽出した非書込パスの全分類は求めない。
条件は`--work-dir`の`pyproject.toml`の`[tool.agent-toolkit.pick-wi-check]`が所有し、共有実装は消費だけを行う。
条件に当たる書込予定には指定の省略・なし・空文字列と必要記載の欠落を拒否する。設定が無ければ条件を課さず、
設定の構文・型が不正なら終了コード2とする。
AWIの反映先は`## 反映内容と反映先`から、UWIは質問・判断材料・回答を含む本文から抽出する。
書込予定の個別対象の欠落と分類の意味は、メインが元本文と限定調査の現物から検収する。

同一段階の未統合レーンで共有するパスには、双方の独立した定義の記録を求める。
別段階は現在の書込予定の重複から先行関係を導き、先行指定の欠落・逆転を検出する。
別段階・統合済み相手の詳細は省略でき、保存済みの詳細記録は履歴として受理する。
形式、担当モデル、再開計画、初回配分と上限、段階比較、見込み補正の契約も検証する。
これらの内容違反は配分を成立させないため終了コード1、構文・型・取得の入力不備は終了コード2を返す。
違反は理由と次の操作を持ち、出力時に同じ次の操作を1回にまとめる。

作業ツリーを読む項目ごとの判定は未統合に限る。統合済みは、項目または所属レーンの統合状態と、
同じ呼び出しの`--mark-integrated`から判定する。改名・削除・規範変更後も統合記録を保存できるよう、
統合済み項目の選定時の前提は再検査しない。本文の特定、所属リポジトリ、レーン関係と初回配分は保持する。
旧英字欄名の選定も読み、撤去済みの`書込範囲の理由`は読取時に除く。

初回生成は意味判断YAMLと取得済み候補を`--candidate-file`で受け取り、鮮度と初回配分を生成する。
追加mergeは`--merge`・`--lane-map`・`--output`、書込予定の追加は`--extend-write-targets`・`--output`で受ける。
書込予定の追加は新設先・重なりの記録と規範指定も更新でき、追加パスと同じか配下の非書込要素を除く。
追加する列の省略は空列とし、記録だけの追記も受理する。既存の順序を保ち同じ値を重ねない。
規範指定は記載時だけ全文を置換し、省略なら保存値を保つ。追加操作は他の更新操作と`--body-wi`を併用できない。
追加済みで未着手のWIの別レーンへの移管は`--transfer-wi`・`--to-lane`・`--lane-cost-updates`・`--output`で受ける。
初回割当・統合済み・凍結した再開計画のWIは移管できない。重なるWIの組がそのまま写る記録は移管先へ付け替え、
組が加わる・分かれる記録は除くため、記録不足になる重なりは`--extend-write-targets`を併せて記録し直す。移管元は項目の`移管元レーン`へ残し、累計上限に数える。
mergeも同じ規則で記録を合わせ、既存レーンへ合流した追加WIとの組のように記録が判定していない組が加わる記録は除く。
その組は追加YAMLの`レーン間の重なり`へ記録する。統合済みの項目を含む組は記録を求めないため、記録を残す妨げにしない。
mergeの任意入力`--lane-cost-updates`は最終割当先の補正前後の実装・統合秒数と根拠を受け取る。
段階2以上では単一段階案の比較値を必須とし、追加時は全体配分について再確定した比較値を用いる。
`--body-wi`は項目ごとの本文検査のみを限定し、レーン関係は全体を検査する。
更新はいずれも全体検査が成功した場合だけ原子的に保存し、初回配分と既存の統合履歴を保持する。
成功時の要約はWI総数、レーンごとのWI・段階・先行関係・秒数、本文検査対象を示す。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import typing

import selection_contract
import yaml
from agent_toolkit._agents_server import admission as _admission
from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import remote as _git_remote
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import selection as _selection
from agent_toolkit._plan import selection_estimate as _estimate
from check_selection_common import (
    _FIX_CONTENT,
    _FIX_PATHS,
    _FIX_PRIVATE_NOTES,
    _IMPLEMENTATION_SECONDS_KEY,
    _INTEGRATION_SECONDS_KEY,
    _LANE_NONE,
    _LEGACY_SECONDS_KEYS,
    InputError,
    Violation,
    _is_integrated,
    _string_list,
)
from check_selection_decisions import (
    _NEXT_BROAD,
    _NEXT_DERIVED,
    _NEXT_EXCLUDED,
    _NEXT_SECTION_OVERLAP,
    _NEXT_WRITE_PATH,
    check_decision,
)
from check_selection_lanes import (
    _NEXT_CORRECTION,
    _NEXT_LANE_MODEL,
    _NEXT_OVERLAP_DUPLICATE,
    _NEXT_OVERLAP_MISSING,
    _NEXT_OVERLAP_MISSING_MERGE,
    _NEXT_OVERLAP_MISSING_TRANSFER,
    _NEXT_OVERLAP_SAME_DEFINITION,
    _NEXT_OVERLAP_SAME_STAGE,
    _NEXT_OVERLAP_SURPLUS,
    _NEXT_PRIOR_INVALID,
    _NEXT_PRIOR_MISSING,
    _NEXT_PRIOR_REQUIRED,
    _NEXT_PRIOR_UNFOUNDED,
    _NEXT_RESUME_PLAN,
    _NEXT_STAGE_SEQUENCE,
    _NEXT_STAGED,
    _check_estimate_records,
    _check_lane_models,
    _check_lane_overlaps,
    _check_lane_stages,
    _check_resume_plan_lanes,
    _completion_estimate,
    missing_overlap_reason,
    process_estimate_warnings,
)
from check_selection_loading import _NEXT_SOURCE_MISMATCH, _check_allocation_sources, load_selection
from check_selection_norms import (
    _NEXT_NORM_SPEC,
    _NEXT_PUBLIC_RATIONALE,
    _check_public_write_rationales,
    check_norm_spec,
    load_norm_conditions,
    load_target_seconds,
)
from check_selection_paths import clear_caches, reflected_paths
from selection_merge import (
    TemporaryLaneCollisionError,
    extend_write_targets,
    merge_rerecord_pairs,
    merge_selection,
    reopen_wis,
    transfer_rerecord_pairs,
    transfer_wi,
)

_NEXT_MISSING_BODY = "`atk wi list`の出力で対象のWIファイル名を確かめ、選定の`WI`をprivate-notesにあるファイル名へ直す"


_NEXT_SINGLE_STAGE = (
    "比較した単一段階案の秒数を最上位の`単一段階案の完了見込み秒数`へ、配分の説明を`レーンの所要時間`の`根拠`へ書く"
)


_NEXT_RERUN = "直した後に同じコマンドで確かめる"


def _guide_rerecord(errors: list[Violation], pairs: dict[tuple[str, str, str], list[str]], action: str) -> list[Violation]:
    """記録不足の違反へ、記録し直す組と合流・移管に合う記録の書き先を添える。

    汎用の案内どおり最終レーン名で追加YAMLへ記録すると、既存の組の判定と解釈されて同じ拒否が返るため置き換える。
    """
    reasons = {missing_overlap_reason(*key): values for key, values in pairs.items()}
    guided: list[Violation] = []
    for error in errors:
        if error.next_action == _NEXT_OVERLAP_MISSING:
            listed = reasons.get(error.reason)
            reason = f"{error.reason}（未判定の組: {'、'.join(listed)}）" if listed else error.reason
            error = Violation(reason, next_action=action)
        guided.append(error)
    return guided


def check(
    selection_file: pathlib.Path,
    work_dir: pathlib.Path,
    private_notes: pathlib.Path,
    body_wis: set[str] | None = None,
    *,
    selection: dict[str, object] | None = None,
    validate_execution: bool = True,
) -> list[Violation]:
    """指定した集合の本文と選定全体の関係を確かめ、違反を返す。省略時は全本文を対象とする。

    統合済みの項目には、作業ツリーの状態を前提とする項目ごとの判定を適用しない。
    """
    if selection is None:
        selection = load_selection(selection_file)
    items = typing.cast(list[dict[str, object]], _selection.decisions(selection))
    costs = typing.cast(list[dict[str, object]], _selection.lane_costs(selection))
    unknown = (body_wis or set()) - {str(item[_selection.WI_KEY]) for item in items}
    if unknown:
        raise InputError(
            f"`--body-wi`に選定結果に無いWIがある: {', '.join(sorted(unknown))}",
            next_action="`--body-wi`へ全体YAMLに含まれる追加WIのファイル名を指定して再実行する",
        )
    if items and not private_notes.is_dir():
        raise InputError(f"private-notesが実在しない: {private_notes}", next_action=_FIX_PRIVATE_NOTES)
    integrated_lanes = _selection.integrated_lanes(selection)
    # 選定時の前提は作業前の作業ツリーで成り立つ条件であり、作業を統合した項目は履歴として扱う。
    # 統合後の作業ツリーで判定し直すと、レーンの作業による改名・削除・追加で統合済みの項目を拒否する。
    pending = [item for item in items if not _is_integrated(item, integrated_lanes)]
    conditions = load_norm_conditions(work_dir) if any(_needs_body_check(item, body_wis) for item in pending) else []
    errors: list[Violation] = []
    work_repo: str | None = None
    for decision in items:
        awi = typing.cast(str, decision[_selection.WI_KEY])
        if not _needs_body_check(decision, body_wis):
            continue
        write_files = _string_list(decision, _selection.WRITE_FILES_KEY)
        public_write_files = _string_list(decision, _selection.PUBLIC_WRITE_FILES_KEY)
        excluded_paths = _string_list(decision, _selection.EXCLUDED_PATHS_KEY)
        try:
            source = _plan_file.find_wi_source(awi, private_notes)
        except OSError as error:
            raise InputError(
                f"private-notesを走査できない: {private_notes}: {error}", next_action=_FIX_PRIVATE_NOTES
            ) from error
        if source is None:
            errors.append(
                Violation(
                    f"{awi}: 本文を特定できない: private-notes（{private_notes}）の状態ディレクトリに無い",
                    next_action=_NEXT_MISSING_BODY,
                )
            )
            continue
        try:
            body = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise InputError(f"WI本文を読み込めない: {source}: {error}", next_action=_FIX_PRIVATE_NOTES) from error
        parsed = _wi_frontmatter.parse_frontmatter(body)
        saved_repo = parsed[0].get("target_repo") if parsed else None
        if isinstance(saved_repo, str):
            if work_repo is None:
                origin = _git_remote.origin_url(work_dir, timeout=60)
                work_repo = _git_remote.resolve_repo_identifier(origin) if origin else None
                if work_repo is None:
                    raise InputError(
                        f"`--work-dir`のoriginの識別子を解決できない: {work_dir}: origin={origin!r}",
                        next_action="対象worktreeと`git remote get-url origin`を確かめ、同じ検査を再実行する",
                    )
            if _git_remote.resolve_repo_identifier(saved_repo) != work_repo:
                errors.append(
                    Violation(
                        f"{awi}: target_repoとworktreeのoriginが一致しない: 保存値={saved_repo}、"
                        f"worktree={work_dir}、originの識別子={work_repo}",
                        next_action=(
                            "ユーザーの明示指定か移転・別名対応の記録で所属を確かめ、移転が確定したWIは"
                            "start-processing前に`atk wi edit <WIファイル名> --target-repo=<保存済みtarget_repo> "
                            "--body-file <原稿>`でtarget_repoと"
                            "本文中の操作先を揃え、worktree指定の一覧と同じ選定検査を再実行する。"
                            "同じOIDだけでは移転と判定しない"
                        ),
                    )
                )
        if _is_integrated(decision, integrated_lanes):
            continue
        reflected = reflected_paths(body, work_dir)
        errors.extend(
            check_decision(
                awi,
                reflected,
                write_files,
                public_write_files,
                excluded_paths,
                work_dir,
                typing.cast(list[dict[str, str]], decision.get(_selection.DERIVED_NEW_PATHS_KEY, [])),
            )
        )
        errors.extend(check_norm_spec(awi, decision, conditions))
    errors.extend(_check_lane_models(items))
    errors.extend(_check_resume_plan_lanes(items))
    errors.extend(_check_lane_stages(items, costs))
    if (
        any(typing.cast(int, row.get(_selection.STAGE_KEY, 1)) >= 2 for row in costs)
        and _selection.SINGLE_STAGE_ESTIMATE_KEY not in selection
    ):
        errors.append(
            Violation(f"段階2以上の選定に`{_selection.SINGLE_STAGE_ESTIMATE_KEY}`がない", next_action=_NEXT_SINGLE_STAGE)
        )
    errors.extend(
        _check_lane_overlaps(
            items,
            costs,
            typing.cast(list[dict[str, object]], selection.get(_selection.LANE_OVERLAPS_KEY, [])),
            integrated_lanes,
        )
    )
    errors.extend(_check_public_write_rationales(pending, work_dir))
    errors.extend(
        _check_estimate_records(selection, costs, load_target_seconds(work_dir), validate_execution=validate_execution)
    )
    errors.extend(selection_contract.allocation_errors(selection))
    errors.extend(_check_allocation_sources(selection, private_notes))
    return errors


def _needs_body_check(decision: dict[str, object], body_wis: set[str] | None) -> bool:
    """レーンを持ち、本文検査の対象集合に含まれる項目かを返す。"""
    return decision[_selection.LANE_KEY] != _LANE_NONE and (body_wis is None or decision[_selection.WI_KEY] in body_wis)


def summary_lines(
    selection: dict[str, object],
    body_wis: set[str] | None = None,
    target_seconds: float | None = None,
    *,
    history: bool = False,
) -> list[str]:
    """確認に合格した選定結果から、レーン構成の要約行を返す。

    WIファイル名は入力順に全件を示す。段階と先行レーンの省略は読み取り契約の省略時の値（1と空の列）で示し、
    秒数は旧欄名（`implementation_seconds`・`integration_seconds`）の値も読む。根拠と書込対象は再掲しない。
    """
    items = typing.cast(list[dict[str, object]], _selection.decisions(selection) or [])
    costs = {
        row[_selection.LANE_KEY]: row
        for row in typing.cast(list[dict[str, object]], _selection.lane_costs(selection) or [])
        if isinstance(row.get(_selection.LANE_KEY), str)
    }
    lanes: dict[str, list[str]] = {}
    unassigned: list[str] = []
    for item in items:
        awi, lane = str(item[_selection.WI_KEY]), str(item[_selection.LANE_KEY])
        if lane == _LANE_NONE:
            unassigned.append(awi)
        else:
            lanes.setdefault(lane, []).append(awi)
    lines = [f"WI総数: {len(items)}", f"通常レーン数: {len(lanes)}"]
    plan = selection.get(_selection.PROCESS_ESTIMATE_KEY)
    correction = selection.get(_selection.ESTIMATE_CORRECTION_KEY)
    if plan is not None:
        result = _estimate.forecast(plan, machine_limit=_admission.configured_limit().value)
        total = "未確認" if result.total_seconds is None else f"{result.total_seconds:g}"
        normal = "未確認" if result.normal_seconds is None else f"{result.normal_seconds:g}"
        lines.extend(
            (
                f"全工程の壁時計見込み秒数: {total}",
                f"通常対象の完了見込み秒数: {normal}",
                f"承認済み巨大WI: {json.dumps(list(result.excluded_wis), ensure_ascii=False)}",
                f"モデル仕事・共有検証等の処理秒数: {json.dumps(result.resource_work_seconds, ensure_ascii=False)}",
                f"資源待ち秒数（全体へ再加算しない）: {json.dumps(result.resource_wait_seconds, ensure_ascii=False)}",
            )
        )
        if result.issues:
            lines.append(f"実行可能性の未確認: {json.dumps(list(result.issues), ensure_ascii=False)}")
        if target_seconds is not None:
            difference = "未確認" if result.normal_seconds is None else f"{result.normal_seconds - target_seconds:g}"
            lines.append(f"目標所要秒数: {target_seconds:g}（通常対象との差 {difference}）")
    elif isinstance(correction, dict) and _selection.SHARED_WAIT_SECONDS_KEY in correction:
        wait = typing.cast(float, correction[_selection.SHARED_WAIT_SECONDS_KEY])
        estimate = _completion_estimate(list(costs.values()), wait)
        lines.append(f"履歴の局所見込み秒数: {estimate}（共有待ち秒数 {wait}を1回含む、全工程は未確認）")
    if _selection.SINGLE_STAGE_ESTIMATE_KEY in selection:
        lines.append(f"{_selection.SINGLE_STAGE_ESTIMATE_KEY}: {selection[_selection.SINGLE_STAGE_ESTIMATE_KEY]}")
    if _selection.STAGED_ESTIMATE_KEY in selection:
        lines.append(f"{_selection.STAGED_ESTIMATE_KEY}: {selection[_selection.STAGED_ESTIMATE_KEY]}")
    for lane, names in lanes.items():
        row = costs.get(lane, {})
        seconds = [
            row.get(key, row.get(next(name for name, current in _LEGACY_SECONDS_KEYS.items() if current == key)))
            for key in (_IMPLEMENTATION_SECONDS_KEY, _INTEGRATION_SECONDS_KEY)
        ]
        prior = json.dumps(row.get(_selection.PRIOR_LANES_KEY, []), ensure_ascii=False)
        lines.append(
            f"{lane}: WI {len(names)}件、段階 {row.get(_selection.STAGE_KEY, 1)}、先行レーン {prior}、"
            f"実装秒数 {seconds[0]}、統合秒数 {seconds[1]}、WI {json.dumps(names, ensure_ascii=False)}"
        )
    lines.append(f"レーン: なし: {json.dumps(unassigned, ensure_ascii=False)}")
    checked = [
        str(item[_selection.WI_KEY])
        for item in items
        if item[_selection.LANE_KEY] != _LANE_NONE and (body_wis is None or str(item[_selection.WI_KEY]) in body_wis)
    ]
    lines.append(f"本文検査対象: {json.dumps(checked, ensure_ascii=False)}")
    if history:
        lines.append("履歴表示: これから実行する配分の目標内成立は検収していない。")
    lines.append("形式・書込予定の妥当性と全体の関係を確認した。分類と配分の意味はWIの元本文と限定調査の結果で検収する。")
    if plan is not None:
        lines.append("資源・費用の根拠と巨大WIの承認は原記録から意味を検収する。数値の計算成功だけで保証しない。")
    return lines


def _resolve_work_dir(value: pathlib.Path | None) -> pathlib.Path:
    """`--work-dir`の値か、現在のディレクトリが属するGitルートを返す。"""
    if value is not None:
        if not value.is_dir():
            raise InputError(f"`--work-dir`がディレクトリではない: {value}", next_action=_FIX_PATHS)
        return value.resolve()
    result = _git_command.run(["rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise InputError(f"現在のディレクトリからGitルートを解決できない: {result.stderr.strip()}", next_action=_FIX_PATHS)
    return pathlib.Path(result.stdout.strip())


def _mark_integrated(selection: dict[str, object], lanes: set[str], processed_wis: set[str] | None) -> None:
    """今回の処置済みの印をメモリ上で付け、書き込む内容そのものを検査に渡せるようにする。"""
    items = typing.cast(list[dict[str, object]], selection.get(_selection.DECISIONS_KEY, selection.get("decisions")))
    for item in items:
        if item.get(_selection.LANE_KEY, item.get("lane")) in lanes and (
            processed_wis is None or str(item.get(_selection.WI_KEY, item.get("awi"))) in processed_wis
        ):
            item[_selection.INTEGRATION_STATE_KEY] = _selection.INTEGRATED
    rows = typing.cast(list[dict[str, object]], _selection.lane_costs(selection))
    for row in rows:
        if row[_selection.LANE_KEY] in lanes and all(
            item.get(_selection.INTEGRATION_STATE_KEY) == _selection.INTEGRATED
            for item in items
            if item.get(_selection.LANE_KEY, item.get("lane")) == row[_selection.LANE_KEY]
        ):
            row[_selection.INTEGRATION_STATE_KEY] = _selection.INTEGRATED
    selection[_selection.LANE_COSTS_KEY] = rows


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、選定結果を確かめる。"""
    # 分割したモジュールはスクリプト本体を読み直しても残るため、前回の起動で読んだ作業ツリーの状態を使わない。
    clear_caches()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection_file", type=pathlib.Path, metavar="PATH", help="メインが保存した選定結果の絶対パス")
    parser.add_argument("--work-dir", type=pathlib.Path, metavar="DIR", default=None, help="対象リポジトリの絶対パス")
    parser.add_argument("--body-wi", action="append", metavar="WI", help="本文検査するWIファイル名。反復指定可、省略時は全件")
    parser.add_argument(
        "--history", action="store_true", help="保存済み選定の履歴を表示する。実行配分の成立判定や更新と併用しない"
    )
    parser.add_argument("--merge", type=pathlib.Path, metavar="PATH", help="追加選定YAMLの絶対パス")
    parser.add_argument(
        "--extend-write-targets", type=pathlib.Path, metavar="PATH", help="保存済みWIの書込対象・新設先・重なりを追加するYAML"
    )
    parser.add_argument("--lane-map", type=pathlib.Path, metavar="PATH", help="追加レーンから最終レーンへの対応JSON")
    parser.add_argument("--output", type=pathlib.Path, metavar="PATH", help="全体チェックに成功した選定結果の保存先")
    parser.add_argument("--require-integrated", action="store_true", help="公開前に全レーンの統合完了を読み取り専用で確認する")
    parser.add_argument("--publication-lane", action="append", metavar="LANE", help="公開単位のレーン。先行レーンも確認する")
    parser.add_argument(
        "--reopen-wi", action="append", metavar="WI", help="既存WIを元レーンで再開する。--outputと組で反復指定可"
    )
    parser.add_argument(
        "--transfer-wi",
        metavar="WI",
        help="追加済みで未着手のWIを別の既存レーンへ移す。--to-lane・--lane-cost-updates・--outputと組で指定",
    )
    parser.add_argument("--to-lane", metavar="LANE", help="--transfer-wiの移管先の既存レーン")
    parser.add_argument("--new-plan", action="store_true", help="要求変更による再開。--reopen-wiの対象だけ再開位置をなしへ戻す")
    parser.add_argument(
        "--candidate-file",
        type=pathlib.Path,
        metavar="PATH",
        help="取得済み候補JSONL。初回はPATH、追加は--mergeの意味判断から選定を生成する",
    )
    parser.add_argument(
        "--mark-integrated",
        action="append",
        metavar="LANE",
        help=(
            "検収済みレーンと所属項目の統合状態を記録する。その項目には作業ツリーの状態を前提とする判定を適用しない。反復指定可"
        ),
    )
    parser.add_argument(
        "--mark-integrated-wis",
        metavar="JSON",
        help="指定レーンのうち処置済みのWI名のJSON配列。省略すると指定レーンの全WIを記録する",
    )
    parser.add_argument(
        "--lane-cost-updates", type=pathlib.Path, metavar="PATH", help="最終レーンの確定した秒数と根拠のJSON（統合時だけ）"
    )
    args = parser.parse_args(argv)
    processed_wis: set[str] | None = None
    if args.mark_integrated_wis is not None:
        error = "--mark-integrated-wisは--mark-integratedと組で、重複のないWI名のJSON配列を指定する"
        try:
            values = json.loads(args.mark_integrated_wis)
        except ValueError:
            parser.error(error)
        if (
            args.mark_integrated is None
            or not isinstance(values, list)
            or not all(isinstance(wi, str) for wi in values)
            or len(values) != len(set(values))
        ):
            parser.error(error)
        processed_wis = set(values)
    other_updates = (
        args.merge,
        args.lane_map,
        args.candidate_file,
        args.mark_integrated,
        args.extend_write_targets,
        args.lane_cost_updates,
        args.body_wi,
        args.transfer_wi,
        args.to_lane,
    )
    if args.publication_lane is not None and not args.require_integrated:
        parser.error("--publication-laneは--require-integratedと組で指定する")
    if args.new_plan and args.reopen_wi is None:
        parser.error("--new-planは--reopen-wiと組で指定する")
    if args.history and (
        args.output is not None
        or args.reopen_wi is not None
        or args.require_integrated
        or any(value is not None for value in other_updates)
    ):
        parser.error("--historyは更新操作・--require-integrated・--body-wiと併用しない")
    if args.require_integrated:
        if args.output is not None or args.reopen_wi is not None or any(value is not None for value in other_updates):
            parser.error("--require-integratedは更新操作・--body-wiと併用しない")
        try:
            selection = load_selection(args.selection_file)
            pending = _selection.pending_lanes(selection)
            if args.publication_lane is not None:
                rows = typing.cast(list[dict[str, typing.Any]], _selection.lane_costs(selection))
                costs = {str(row[_selection.LANE_KEY]): row for row in rows}
                wanted = set(args.publication_lane)
                unknown = wanted - costs.keys()
                if unknown:
                    raise ValueError(f"公開単位のレーンが選定に無い: {sorted(unknown)}")
                remaining = list(wanted)
                while remaining:
                    lane = remaining.pop()
                    for prior in costs[lane].get(_selection.PRIOR_LANES_KEY, []):
                        if prior not in costs:
                            raise ValueError(f"先行レーンが選定に無い: {prior}")
                        if prior not in wanted:
                            wanted.add(prior)
                            remaining.append(prior)
                pending = [row for row in pending if row[_selection.LANE_KEY] in wanted]
        except (InputError, ValueError) as error:
            _next_action.report(str(error), next_action=_FIX_CONTENT)
            return 2
        if pending:
            for row in pending:
                print(f"未統合: {json.dumps(row, ensure_ascii=False)}", file=sys.stderr)
            print(
                _next_action.next_action_line(
                    "未起動・未完了レーンを開始・継続する。延期するWIは理由・解除条件・再開位置を検収する"
                ),
                file=sys.stderr,
            )
            return 1
        print("公開単位の全レーン統合済み" if args.publication_lane is not None else "全レーン統合済み")
        return 0
    if args.reopen_wi is not None and (args.output is None or any(value is not None for value in other_updates)):
        parser.error("--reopen-wiは--outputと組で指定し、他の更新操作・--body-wiと併用しない")
    if (args.transfer_wi is not None or args.to_lane is not None) and (
        args.transfer_wi is None
        or args.to_lane is None
        or args.lane_cost_updates is None
        or args.output is None
        or args.reopen_wi is not None
        or any(value is not None for value in (args.merge, args.lane_map, args.candidate_file))
        or args.mark_integrated is not None
        or args.body_wi is not None
    ):
        parser.error(
            "--transfer-wi・--to-lane・--lane-cost-updates・--outputは組で指定し、"
            "--extend-write-targets以外の更新操作・--body-wiと併用しない"
        )
    if args.extend_write_targets is not None and (
        args.output is None
        or any(
            value is not None
            for value in (
                args.merge,
                args.lane_map,
                args.candidate_file,
                args.mark_integrated,
                args.body_wi,
            )
        )
        # 移管の--lane-cost-updatesは移管操作の入力であり、書込対象の追加と併用できる。
        or (args.lane_cost_updates is not None and args.transfer_wi is None)
    ):
        parser.error("--extend-write-targetsは--outputと組で指定し、--transfer-wi以外の更新操作・--body-wiと併用しない")
    if args.candidate_file is not None and (args.mark_integrated is not None or args.output is None):
        parser.error("--candidate-fileは--outputと組で指定し、--mark-integratedと併用しない")
    if args.mark_integrated is not None and (args.merge is not None or args.lane_map is not None or args.output is None):
        parser.error("--mark-integratedは--outputと組で指定し、--merge・--lane-mapと併用しない")
    if (
        args.mark_integrated is None
        and args.reopen_wi is None
        and args.extend_write_targets is None
        and args.transfer_wi is None
        and not (args.candidate_file is not None and args.merge is None and args.lane_map is None)
        and any(value is not None for value in (args.merge, args.lane_map, args.output))
        and not all(value is not None for value in (args.merge, args.lane_map, args.output))
    ):
        parser.error("--merge・--lane-map・--outputは組で指定する")
    if args.lane_cost_updates is not None and args.merge is None and args.transfer_wi is None:
        parser.error("--lane-cost-updatesは--merge・--lane-map・--outputまたは--transfer-wiとともに指定する")
    body_wis = set(args.body_wi) if args.body_wi is not None else None
    # 合流・移管で記録不足になった鍵へ、記録し直す組と記録の書き先を添える案内。
    rerecord: tuple[dict[tuple[str, str, str], list[str]], str] | None = None
    try:
        work_dir = _resolve_work_dir(args.work_dir)
        selection = load_selection(args.selection_file, candidate_file=args.candidate_file if args.merge is None else None)
        if args.reopen_wi is not None:
            try:
                selection = reopen_wis(selection, args.reopen_wi, new_plan=args.new_plan)
            except ValueError as error:
                raise InputError(
                    str(error), next_action="保存済み選定の元WI名と所属を確認して同じ再開操作を実行する"
                ) from error
        if args.transfer_wi is not None:
            assert args.to_lane is not None and args.lane_cost_updates is not None
            try:
                updates = json.loads(args.lane_cost_updates.read_text(encoding="utf-8"))
                selection = transfer_wi(selection, args.transfer_wi, args.to_lane, updates)
            except (OSError, UnicodeError, ValueError) as error:
                raise InputError(
                    str(error),
                    next_action="移管対象・移管先・費用更新JSONを直して同じ移管操作を再実行する。移管できない種別のWIは元レーンで扱う",
                ) from error
        if args.extend_write_targets is not None:
            try:
                addition = yaml.safe_load(args.extend_write_targets.read_text(encoding="utf-8"))
                selection = extend_write_targets(selection, addition)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                raise InputError(
                    str(error),
                    next_action=(
                        "--extend-write-targetsのYAMLを直して再実行する。`選定`の各項目へ`WI`と追加の`書込対象`、"
                        "必要な`導出した新設先`の4欄（`パス`・`反映範囲`・`要求`・`配置根拠`）、"
                        "更新する場合は更新後の`プロジェクト規範の指定`（文字列）を置き、"
                        "`レーン間の重なり`へ共通パス・双方の定義・判定を置く"
                    ),
                ) from error
        if args.transfer_wi is not None:
            rerecord = transfer_rerecord_pairs(selection), _NEXT_OVERLAP_MISSING_TRANSFER
        if args.mark_integrated is not None:
            rows = typing.cast(list[dict[str, object]], _selection.lane_costs(selection))
            unknown = set(args.mark_integrated) - {str(row[_selection.LANE_KEY]) for row in rows}
            if unknown:
                raise InputError(f"統合状態の対象レーンがない: {sorted(unknown)}", next_action=_FIX_CONTENT)
            if processed_wis is not None:
                names = {
                    str(item[_selection.WI_KEY])
                    for item in typing.cast(list[dict[str, object]], _selection.decisions(selection) or [])
                    if item.get(_selection.LANE_KEY, item.get("lane")) in args.mark_integrated
                }
                unknown = set(processed_wis) - names
                if unknown:
                    raise InputError(f"指定レーンに属さないWI: {sorted(unknown)}", next_action=_FIX_CONTENT)
        if args.merge is not None:
            assert args.lane_map is not None
            try:
                mapping = json.loads(args.lane_map.read_text(encoding="utf-8"))
                updates = None
                if args.lane_cost_updates is not None:
                    updates = json.loads(args.lane_cost_updates.read_text(encoding="utf-8"))
                    if not isinstance(updates, dict):
                        raise ValueError("費用更新JSONは最終レーンから補正前後の実装秒数・統合秒数と根拠の5欄への写像とする")
                addition = load_selection(args.merge, additional=True, candidate_file=args.candidate_file)
                selection_contract.require_shared_wait_basis(addition.get(_selection.ESTIMATE_CORRECTION_KEY))
                for data in (selection, addition):
                    estimate_errors = _check_estimate_records(
                        data, typing.cast(list[dict[str, object]], _selection.lane_costs(data)), validate_execution=False
                    )
                    if estimate_errors:
                        raise ValueError("\n".join(error.reason for error in estimate_errors))
                merged = merge_selection(selection, addition, mapping, updates)
                rerecord = merge_rerecord_pairs(selection, addition, merged), _NEXT_OVERLAP_MISSING_MERGE
                selection = merged
            except TemporaryLaneCollisionError as error:
                raise InputError(
                    str(error), next_action="仮名を既存レーン名と重ならない名前へ付け替えて同じコマンドを再実行する"
                ) from error
            except (OSError, ValueError) as error:
                raise InputError(
                    str(error), next_action="追加入力・レーン対応JSON・費用更新JSONを直して同じ統合操作を再実行する"
                ) from error
            structural, models = selection_contract.structure_errors(selection)
            if structural or models:
                raise InputError("\n".join([*models, *structural]), next_action=_FIX_CONTENT)
        if args.mark_integrated is not None:
            _mark_integrated(selection, set(args.mark_integrated), processed_wis)
        errors = check(
            args.selection_file,
            work_dir,
            _plan_file.private_notes_root(),
            body_wis,
            selection=selection,
            validate_execution=not args.history and args.mark_integrated is None,
        )
    except InputError as error:
        _next_action.report(error.reason, next_action=error.next_action)
        return 2
    if rerecord is not None:
        errors = _guide_rerecord(errors, *rerecord)
    for error in errors:
        print(error.reason, file=sys.stderr)
    if errors:
        # 同じ次の操作を持つ違反が複数あっても、その次の操作は最初に現れた位置で1回だけ出力する。
        actions = list(dict.fromkeys(error.next_action for error in errors))
        print(_next_action.next_action_line("。".join([*actions, _NEXT_RERUN])), file=sys.stderr)
        return 1
    if args.output is not None:
        try:
            if args.merge is not None and args.output.resolve() == args.merge.resolve():
                raise OSError("追加YAMLを保存先に指定できない")
            if args.extend_write_targets is not None and args.output.resolve() == args.extend_write_targets.resolve():
                raise OSError("書込対象の追加入力YAMLを保存先に指定できない")
            atomic_write(args.output, yaml.safe_dump(selection, allow_unicode=True, sort_keys=False))
        except OSError as error:
            _next_action.report(str(error), next_action="保存先を確認して同じ統合操作を再実行する")
            return 2
        print(f"統合結果: {args.output.resolve()}")
    # 目標の設定は先行する`check`の呼出しで検証済みのため、ここでは読込の失敗が起きない。
    if not args.history and args.mark_integrated is None:
        for warning in process_estimate_warnings(
            selection,
            typing.cast(list[dict[str, object]], _selection.lane_costs(selection)),
            load_target_seconds(work_dir),
        ):
            print(f"警告: {warning}", file=sys.stderr)
    print("\n".join(summary_lines(selection, body_wis, load_target_seconds(work_dir), history=args.history)))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "_NEXT_BROAD",
    "_NEXT_CORRECTION",
    "_NEXT_DERIVED",
    "_NEXT_EXCLUDED",
    "_NEXT_LANE_MODEL",
    "_NEXT_NORM_SPEC",
    "_NEXT_OVERLAP_DUPLICATE",
    "_NEXT_OVERLAP_MISSING",
    "_NEXT_OVERLAP_SAME_DEFINITION",
    "_NEXT_OVERLAP_SAME_STAGE",
    "_NEXT_OVERLAP_SURPLUS",
    "_NEXT_PRIOR_INVALID",
    "_NEXT_PRIOR_MISSING",
    "_NEXT_PRIOR_REQUIRED",
    "_NEXT_PRIOR_UNFOUNDED",
    "_NEXT_PUBLIC_RATIONALE",
    "_NEXT_RESUME_PLAN",
    "_NEXT_SECTION_OVERLAP",
    "_NEXT_SOURCE_MISMATCH",
    "_NEXT_STAGED",
    "_NEXT_STAGE_SEQUENCE",
    "_NEXT_WRITE_PATH",
    "load_selection",
    "reflected_paths",
]
