"""計画に必要な情報と実体が揃っているか確かめる。

計画メタ情報、見出し構造、`関連WI`、スキル・サブエージェント参照を共有parserで読み取り、基準を満たすか判定する。
旧単一ファイル形式と旧二ファイル形式は読み取り互換で受理し、現行形式への移行をwarningで案内する。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml
from check_plan_file_common import _ClassifiedWarning
from check_plan_file_formats import (
    _check_legacy_format,
    _check_new_format,
    _check_single_file_format,
    _check_working_plan_filename,
    _detail_path_for,
    _outside_fences,
)
from check_plan_file_references import TARGET_REPO_NEXT_ACTION
from check_plan_file_selection import _check_lane_selection, _check_wi_selection, _labeled, _validate_expected_wi

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._plan import structure


def check(
    plan_path: pathlib.Path,
    work_dir: pathlib.Path,
    *,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
    reject_migration_warnings: bool = False,
    reject_progress_log_rows: bool = False,
    selection_file: pathlib.Path | None = None,
    lane: str | None = None,
    expected_wi: tuple[str, ...] = (),
    prior_plans: tuple[pathlib.Path, ...] = (),
    agent_rule_paths: tuple[str, ...] = (),
    warning_details: list[_ClassifiedWarning] | None = None,
) -> tuple[list[str], list[str]]:
    """計画ファイルが基準を満たすか判定し、エラーと警告を返す。

    `~/.claude/plans`直下の新形式と、既存の日付階層形式を同じ構造契約で受理する。
    `~/.claude/plans`直下の対象では、保存工程と同じ条件でファイル名が所定の形式であるかも確かめる。
    対応する`<stem>.detail.md`があれば旧二ファイル形式として扱う。
    detailが無く、現行H2集合を持つ場合は現行の1ファイル形式、それ以外は旧単一ファイル形式として扱う。
    警告は、旧形式からの移行を促す`migration`と、現行形式でも成立する`advisory`に分類する。
    種類を分けずに新規作成を失敗させると、行数の助言だけを伴う現行形式の計画まで遮断する。
    移行警告の拒否と、起草時の`## 進捗ログ`内容行の拒否は呼び出し側が独立に指定する。
    既存計画の改訂では移行警告を拒否し、実行工程が記録した進捗行は保持するためである。
    """
    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    body_start = structure.markdown_body_start_index(text)
    structure_lines = ["" if index < body_start else line for index, line in enumerate(lines)]
    _outside, errors = _outside_fences(structure_lines)
    errors.extend(_check_working_plan_filename(plan_path, home))

    detail_path = _detail_path_for(plan_path)
    progress_heading = structure.PLAN_H2_PROGRESS
    if detail_path.is_file():
        format_errors, classified_warnings = _check_new_format(detail_path, text, work_dir, private_notes, home)
        if not any(kind == "migration" for kind, _message in classified_warnings):
            classified_warnings.append(("migration", "旧二ファイル書式である。新規作成・改訂では現行の1ファイル書式へ移行する"))
    else:
        h2_names = {heading.text for heading in structure.extract_headings(text) if heading.level == 2}
        current_markers = {
            structure.PLAN_H2_REQUIREMENTS,
            structure.PLAN_H2_CURRENT_PERMANENCE,
        }
        if current_markers <= h2_names:
            progress_heading = structure.PLAN_H2_CURRENT_PROGRESS
            format_errors, classified_warnings = _check_single_file_format(plan_path, text, work_dir, private_notes, home)
        else:
            format_errors, classified_warnings = _check_legacy_format(plan_path, text, work_dir, private_notes, home)
    errors.extend(format_errors)
    errors.extend(
        f"返却予定のエージェント向け文書のパスが計画本文にない: {relative}。"
        "編集対象のパスを計画本文へ明記し、同じ一覧でもう一度確かめる"
        for relative in agent_rule_paths
        if relative not in text
    )
    _validate_expected_wi(expected_wi, selection_file, lane)
    if (selection_file is None) != (lane is None):
        raise ValueError("選定結果の出力先ファイルとレーン識別子は組で指定する")
    if prior_plans and selection_file is None and not expected_wi:
        raise ValueError("先行計画は選定結果の出力先ファイルとレーン識別子とともに指定する")
    if selection_file is not None and lane is not None:
        if not selection_file.is_absolute() or re.fullmatch(r"lane-\d{2}", lane) is None:
            raise ValueError("選定結果の出力先ファイルは絶対パス、レーン識別子はlane-NN形式で指定する")
        if plan_path in prior_plans or len(prior_plans) != len(set(prior_plans)):
            raise ValueError("追加計画と先行計画に同じファイルを重複指定できない")
        errors.extend(_check_lane_selection(((None, text),), selection_file, lane, prior_plans, work_dir))
    if expected_wi:
        errors.extend(_check_wi_selection(((None, text),), expected_wi, "期待WI集合", prior_plans, work_dir))
    if reject_progress_log_rows and structure.has_progress_log_rows(text):
        errors.append(f"`## {progress_heading}`は起草時に内容行を置かない")
    warnings: list[str] = []
    for kind, message in classified_warnings:
        if reject_migration_warnings and kind == "migration":
            errors.append(message)
        else:
            warnings.append(message)
            if warning_details is not None:
                warning_details.append((kind, message))
    return errors, warnings


def check_plan_set(
    plan_paths: tuple[pathlib.Path, ...],
    work_dir: pathlib.Path,
    *,
    reject_migration_warnings: bool = False,
    reject_progress_log_rows: bool = False,
    selection_file: pathlib.Path | None = None,
    lane: str | None = None,
    expected_wi: tuple[str, ...] = (),
    prior_plans: tuple[pathlib.Path, ...] = (),
    agent_rule_paths: tuple[str, ...] = (),
    warning_details: list[_ClassifiedWarning] | None = None,
) -> tuple[list[str], list[str]]:
    """初回に分割した複数の計画を1回で確かめ、診断の先頭へ対象の計画のパスを付けて返す。

    各計画の構造・行数・移行警告は`check`と同じ規則で個別に判定する。
    選定レーンのWI集合は、全ての計画と先行計画の`関連WI`の和集合と1回で比べる。
    1計画ずつ先行計画を指定して確かめると、まだ確かめていない後続計画のWIが集合に入らず途中の計画で不一致になるためである。
    返却予定のエージェント向け文書のパスは、いずれかの計画の本文にあれば受理する。
    文書を変更する計画だけがそのパスを持つため、全計画へ一律には求めない。
    """
    resolved = [path.resolve() for path in (*plan_paths, *prior_plans)]
    if len(resolved) != len(set(resolved)):
        raise ValueError("確かめる計画と先行計画に同じファイルを重複指定できない")
    _validate_expected_wi(expected_wi, selection_file, lane)
    if (selection_file is None) != (lane is None):
        raise ValueError("選定結果の出力先ファイルとレーン識別子は組で指定する")
    if prior_plans and selection_file is None and not expected_wi:
        raise ValueError("先行計画は選定結果の出力先ファイルとレーン識別子とともに指定する")
    errors: list[str] = []
    warnings: list[str] = []
    texts: list[tuple[pathlib.Path | None, str]] = []
    for plan_path in plan_paths:
        plan_details: list[_ClassifiedWarning] = []
        plan_errors, plan_warnings = check(
            plan_path,
            work_dir,
            reject_migration_warnings=reject_migration_warnings,
            reject_progress_log_rows=reject_progress_log_rows,
            warning_details=plan_details,
        )
        errors.extend(_labeled(plan_path, error) for error in plan_errors)
        warnings.extend(_labeled(plan_path, warning) for warning in plan_warnings)
        if warning_details is not None:
            warning_details.extend(plan_details)
        texts.append((plan_path, plan_path.read_text(encoding="utf-8")))
    errors.extend(
        f"返却予定のエージェント向け文書のパスがどの計画の本文にもない: {relative}。"
        "編集対象のパスを、その文書を変更する計画の本文へ明記し、同じ一覧でもう一度確かめる"
        for relative in agent_rule_paths
        if not any(relative in text for _path, text in texts)
    )
    if selection_file is not None and lane is not None:
        if not selection_file.is_absolute() or re.fullmatch(r"lane-\d{2}", lane) is None:
            raise ValueError("選定結果の出力先ファイルは絶対パス、レーン識別子はlane-NN形式で指定する")
        errors.extend(_check_lane_selection(tuple(texts), selection_file, lane, prior_plans, work_dir))
    if expected_wi:
        errors.extend(_check_wi_selection(tuple(texts), expected_wi, "期待WI集合", prior_plans, work_dir))
    return errors, warnings


def _wi_comparison_skip_next_action(warnings: list[_ClassifiedWarning]) -> str | None:
    """由来や移管先をWI本文と比べられなかった場合と、削除済みの移管先を履歴で受理した場合の後続操作を一度ずつ案内する。"""
    skips = [
        message
        for kind, message in warnings
        if kind == "advisory"
        and message.startswith(
            ("`## 実施内容`の由来をWI本文と比べられなかった。", "`## 実施内容`の移管先をWI本文と比べられなかった。")
        )
    ]
    deleted_transfers = [
        message
        for kind, message in warnings
        if kind == "advisory"
        and message.startswith("`## 実施内容`の移管先WIは現在の配置に無いが、private-notesの履歴で登録済み:")
    ]
    if not skips and not deleted_transfers:
        return None
    actions = []
    if deleted_transfers:
        # 受理は登録の記録だけに基づく。削除が移管先の引受（別環境への移動など）によるものかは履歴の本文で確かめる。
        actions.append(
            '削除commitの内容を`git -C "$(atk config get private_notes)" show --stat <削除commit>`で確かめ、'
            "移管先の引受による削除なら計画の確認を続ける。誤った削除ならWIを復元してから再実行する"
        )
    if not skips:
        return "。".join(actions)
    if any("private-notesが実在しない:" in message for message in skips):
        actions.append(
            "`atk config get private_notes`でprivate-notesの場所を確認し、"
            "`AGENT_TOOLKIT_PRIVATE_NOTES`をその場所へ合わせて再実行する"
        )
    if any("WIファイルを特定できない:" in message for message in skips):
        actions.append("実施内容と関連WIのファイル名を`atk wi show <ファイル名>`で確かめ、誤りを直して再実行する")
    if any("WI本文を取得できない:" in message for message in skips):
        actions.append("表示された権限・文字コードのエラーの原因を解消して再実行する")
    actions.append(
        "解消できない場合は、WI本文との比較を省略した対象と原因を委譲元（メインから起動した場合はユーザー）へ報告する"
    )
    return "。".join(actions)


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、計画が基準を満たすか確かめる。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "plan_file",
        type=pathlib.Path,
        nargs="+",
        metavar="PATH",
        help=(
            "検証する計画ファイル（`<計画名>.md`）の絶対パス。初回に分割した計画の集合は全件を並べ、"
            "各計画の構造と選定レーンへのWIの対応を1回で確かめる"
        ),
    )
    parser.add_argument(
        "--work-dir",
        type=pathlib.Path,
        metavar="DIR",
        default=pathlib.Path.cwd(),
        help="計画メタ情報の`対象リポジトリ`と一致するかを確かめるGit作業ツリーのパス（省略すると現在のディレクトリを使う）",
    )
    parser.add_argument("--selection-file", type=pathlib.Path, metavar="PATH", help="メインが保存した選定結果の絶対パス")
    parser.add_argument("--lane", help="選定結果内のlane-NN形式のレーン識別子")
    parser.add_argument(
        "--expected-wi",
        action="append",
        default=None,
        metavar="WI",
        help="保存済み対応表の全計画対象のWIファイル名を反復指定する。選定YAML方式とは併用しない",
    )
    parser.add_argument(
        "--agent-rule-path",
        action="append",
        default=None,
        help="返却予定のエージェント向け文書のリポジトリ相対パス。全件を反復指定し、計画本文に固定文字列として含まれるか確かめる",
    )
    parser.add_argument(
        "--prior-plan",
        action="append",
        type=pathlib.Path,
        metavar="PATH",
        default=None,
        help="同じレーンで確認を終えた先行計画の絶対パス。全件を反復指定する",
    )
    parser.add_argument(
        "--reject-migration-warnings",
        action="store_true",
        help="旧形式からの移行警告をエラーとして扱う",
    )
    parser.add_argument(
        "--reject-progress-log-rows",
        action="store_true",
        help="起草時の進捗ログに内容行がある場合はエラーとして扱う",
    )
    try:
        args = parser.parse_args(argv)
        warning_details: list[_ClassifiedWarning] = []
        prior_plans = tuple(args.prior_plan or ())
        agent_rule_paths = tuple(args.agent_rule_path or ())
        if len(args.plan_file) == 1:
            errors, warnings = check(
                args.plan_file[0],
                args.work_dir,
                reject_migration_warnings=args.reject_migration_warnings,
                reject_progress_log_rows=args.reject_progress_log_rows,
                selection_file=args.selection_file,
                lane=args.lane,
                expected_wi=tuple(args.expected_wi or ()),
                prior_plans=prior_plans,
                agent_rule_paths=agent_rule_paths,
                warning_details=warning_details,
            )
        else:
            errors, warnings = check_plan_set(
                tuple(args.plan_file),
                args.work_dir,
                reject_migration_warnings=args.reject_migration_warnings,
                reject_progress_log_rows=args.reject_progress_log_rows,
                selection_file=args.selection_file,
                lane=args.lane,
                expected_wi=tuple(args.expected_wi or ()),
                prior_plans=prior_plans,
                agent_rule_paths=agent_rule_paths,
                warning_details=warning_details,
            )
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as error:
        _next_action.report(
            f"計画を確認するための入力を読み込めない: {error}",
            next_action=(
                "計画ファイル（位置引数）と`--prior-plan`へ実在するUTF-8の計画ファイルの絶対パスを、"
                "`--selection-file`へメインが保存したYAMLの絶対パスを、`--lane`へ`lane-NN`形式の識別子を渡して再実行する"
            ),
        )
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)
    if (next_action := _wi_comparison_skip_next_action(warning_details)) is not None:
        print(_next_action.next_action_line(next_action), file=sys.stderr)
    if errors:
        if all(
            "対象リポジトリが作業ディレクトリのGitルートと一致しない" in error or "先行計画の対象リポジトリが異なる:" in error
            for error in errors
        ):
            return 1
        print(
            _next_action.next_action_line(
                "各行が示す規定のとおりに計画ファイルを直し、同じコマンドでもう一度確かめる"
                "（規定は`agent-toolkit:write-plan`の`references/plan-file-standards.md`に従う）"
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["TARGET_REPO_NEXT_ACTION"]
