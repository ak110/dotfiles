"""計画の新旧書式と付属ファイルの検査。"""

from __future__ import annotations

import pathlib
import re

from check_plan_file_common import _ClassifiedWarning
from check_plan_file_migration import (
    _legacy_acceptance_warnings,
    _legacy_action_warnings,
    _legacy_bug_warnings,
    _legacy_fixed_notation_warnings,
    _legacy_h2_warnings,
    _legacy_refactoring_warnings,
    _legacy_verification_name_warnings,
)
from check_plan_file_references import _check_references, _check_target_repo

from agent_toolkit._plan import bundle_kinds, locations, structure

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$", re.MULTILINE)


# 計画の分量を警告する行数の閾値。
# 既存計画380件を集計した分布（中央値443行、第75百分位732行、第90百分位1312行、最大3476行）の
# 第75百分位と第90百分位の間から選び、通常規模の計画を警告せず肥大した計画だけを検出する。
# 閾値を超えても計画として成立し得るため、警告に留める。
_PLAN_LINE_WARNING_THRESHOLD = 1200


def _outside_fences(lines: list[str]) -> tuple[list[bool], list[str]]:
    """Markdownフェンス外の行と未閉鎖フェンスのエラーを返す。"""
    outside: list[bool] = []
    marker: str | None = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if marker is None:
            outside.append(True)
            if match:
                marker = match.group(1)
        else:
            outside.append(False)
            if match and match.group(1)[0] == marker[0] and len(match.group(1)) >= len(marker) and not match.group(2).strip():
                marker = None
    errors = ["閉じていないMarkdownフェンスがある"] if marker is not None else []
    return outside, errors


def _check_plan_size(lines: list[str]) -> list[_ClassifiedWarning]:
    """計画の行数が閾値を超える場合に警告を返す。"""
    if len(lines) <= _PLAN_LINE_WARNING_THRESHOLD:
        return []
    return [
        (
            "advisory",
            f"計画の行数が閾値を超えている: {len(lines)}行（閾値{_PLAN_LINE_WARNING_THRESHOLD}行）。"
            "重複する記述を単一の情報源へ集約する",
        )
    ]


def _detail_path_for(plan_path: pathlib.Path) -> pathlib.Path:
    """計画ファイル（メイン）のパスから対応する計画ファイル（詳細）の絶対パスを返す（stem導出）。"""
    return plan_path.with_name(bundle_kinds.DETAIL.name_for(plan_path.stem))


def _main_path_for_detail(detail_path: pathlib.Path) -> pathlib.Path:
    """計画ファイル（詳細）のパスからstem対応する計画ファイル（メイン）のパスを返す。"""
    if bundle_kinds.DETAIL.matches(detail_path.name):
        return detail_path.with_name(bundle_kinds.MAIN.name_for(detail_path.name.removesuffix(bundle_kinds.DETAIL.suffix)))
    return detail_path.with_suffix(".md")


def _check_bug_file_reference(
    plan_path: pathlib.Path,
    text: str,
    work_type: str | None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[list[str], list[_ClassifiedWarning]]:
    """`計画ファイル（バグ）`行を持つバグ対応計画について、参照先の計画ファイル（バグ）の実在、stemの一致、構造が基準を満たすかを確かめる。

    同行を持たない計画は関連WIの`## 原因分析`に従うため、計画ファイル（バグ）の参照を判定しない。
    同行の要否は計画構造の自動チェックが計画メタ情報の`関連WI`から判定する。

    新しい参照値は接頭辞を展開せず計画ファイルのディレクトリを基準に解決し、
    既存の可搬表記と絶対パスは読み取り互換として従来と同じ処理で解決する。
    統廃合前と6区分化前の行構成を持つ調査表は読み取りで受理し、新規作成・改訂では移行warningをエラーへ変える。
    """
    if work_type != "バグ対応":
        return [], []
    reference = structure.extract_bug_file_reference(text)
    if reference is None:
        return [], []

    if locations.is_plan_adjunct_reference(reference):
        try:
            reference_path = locations.resolve_plan_adjunct_reference(reference, plan_path=plan_path)
        except (OSError, ValueError) as error:
            return [f"バグ調査ファイルの参照値が不正です: {reference}: {error}"], []
    elif reference.startswith(locations.PORTABLE_PLAN_PREFIX):
        try:
            reference_path = locations.resolve_plan_file(reference, private_notes=private_notes, home=home)
        except (OSError, ValueError) as error:
            return [f"バグ調査ファイルの可搬参照パスが不正です: {reference}: {error}"], []
    else:
        reference_path = pathlib.Path(reference)
        if not reference_path.is_absolute():
            return [
                "バグ調査ファイルの参照は"
                f"`{locations.PLAN_ADJUNCT_REFERENCE_PREFIX}<ファイル名>`、可搬表記または絶対パスにする: {reference}"
            ], []
        try:
            reference_path = locations.resolve_plan_file(reference_path)
        except (OSError, ValueError) as error:
            return [f"バグ調査ファイルの参照パスが不正です: {reference}: {error}"], []

    expected_path = plan_path.with_name(bundle_kinds.BUGS.name_for(plan_path.stem))
    if reference_path.resolve() != expected_path.resolve():
        return [f"バグ調査ファイルの参照パスが計画stemと一致しない: 計画={reference_path}, 期待={expected_path}"], []
    if not reference_path.is_file():
        return [f"バグ調査ファイルが実在しない: {reference_path}"], []

    bug_text = reference_path.read_text(encoding="utf-8")
    warnings: list[_ClassifiedWarning] = []
    if structure.has_legacy_bug_investigation_table(bug_text):
        warnings.append(
            (
                "migration",
                "バグ調査ファイルの調査表が現行より前の行構成である。新規作成・改訂では"
                f"{list(structure.PLAN_BUG_TABLE_ROWS)}の行へ移行する",
            )
        )
    return structure.check_bug_file_structure(bug_text), warnings


def _check_new_format(
    detail_path: pathlib.Path,
    text: str,
    work_dir: pathlib.Path,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[list[str], list[_ClassifiedWarning]]:
    """二ファイル形式の計画が基準を満たすか判定し、エラーと警告を返す。

    呼び出し元の`check`は`detail_path.is_file()`が真の場合だけ本関数を呼ぶため、
    計画ファイル（詳細）の実在は呼び出し前提として扱う。
    """
    errors: list[str] = []
    warnings: list[_ClassifiedWarning] = []
    origin_notices: list[str] = []
    origin_skips: list[str] = []
    work_type, main_errors = structure.check_plan_main_structure(
        text,
        origin_notices=origin_notices,
        origin_skips=origin_skips,
        private_notes=private_notes,
        home=home,
    )
    errors.extend(main_errors)
    # 由来の注記は実施内容表の移行を促すmigration警告とし、環境が原因で由来と原文の突き合わせを省いた通知は
    # 現行形式のままでも計画が成立するためadvisory警告とする。
    warnings.extend(("migration", notice) for notice in origin_notices)
    warnings.extend(("advisory", skip) for skip in origin_skips)

    parsed, _ambiguity_errors = structure.parse_plan_metadata(text)
    metadata = parsed.values if parsed is not None else {}

    detail_text = detail_path.read_text(encoding="utf-8")
    warnings.extend(_legacy_verification_name_warnings(detail_text))
    detail_lines = detail_text.splitlines()
    detail_body_start = structure.markdown_body_start_index(detail_text)
    detail_structure_lines = ["" if index < detail_body_start else line for index, line in enumerate(detail_lines)]
    _outside_detail, detail_fence_errors = _outside_fences(detail_structure_lines)
    errors.extend(detail_fence_errors)
    errors.extend(structure.check_plan_detail_structure(detail_text, work_type))
    bug_errors, bug_warnings = _check_bug_file_reference(
        _main_path_for_detail(detail_path), detail_text, work_type, private_notes, home
    )
    errors.extend(bug_errors)
    warnings.extend(bug_warnings)
    errors.extend(_check_references(detail_text, work_dir))
    warnings.extend(_check_plan_size(detail_lines))
    warnings.extend(_legacy_bug_warnings(detail_text))
    warnings.extend(_legacy_refactoring_warnings(detail_text))
    warnings.extend(_legacy_fixed_notation_warnings(detail_text))

    materials = None
    if parsed is None or structure.PLAN_METADATA_RELATED_WI_FIELD not in parsed.values:
        materials, _material_errors = structure.parse_plan_materials(text)
    warnings.extend(_legacy_h2_warnings(text))
    warnings.extend(_legacy_action_warnings(text))
    warnings.extend(_legacy_fixed_notation_warnings(text))
    is_canonical = structure.is_canonical_main_format(text)
    if is_canonical and not structure.has_human_action_table(text):
        errors.append("canonical形式の`## 実施内容`には人間向け4列表が必要である")
    elif not structure.has_human_action_table(text):
        warnings.append(("migration", "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する"))
    if is_canonical and materials is not None and materials.is_legacy:
        errors.append("canonical形式の`## 提示素材`には素材表と要求表が必要である")
    elif materials is not None and materials.is_legacy:
        warnings.append(("migration", "提示素材が旧形式である。新規作成・改訂では素材表と要求表へ移行する"))
    errors.extend(_check_target_repo(metadata.get("対象リポジトリ"), work_dir))
    errors.extend(_check_references(text, work_dir))
    warnings.extend(_check_plan_size(text.splitlines()))
    return errors, warnings


def _check_single_file_format(
    plan_path: pathlib.Path,
    text: str,
    work_dir: pathlib.Path,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[list[str], list[_ClassifiedWarning]]:
    """現行の1ファイル計画が基準を満たすか判定し、エラーと警告を返す。"""
    origin_notices: list[str] = []
    origin_skips: list[str] = []
    work_type, errors = structure.check_plan_single_file_structure(
        text,
        origin_notices=origin_notices,
        origin_skips=origin_skips,
        private_notes=private_notes,
        home=home,
    )
    warnings: list[_ClassifiedWarning] = [("migration", notice) for notice in origin_notices]
    warnings.extend(("advisory", skip) for skip in origin_skips)
    metadata, _metadata_errors = structure.parse_plan_metadata(text)
    values = metadata.values if metadata is not None else {}
    errors.extend(_check_target_repo(values.get("対象リポジトリ"), work_dir))
    bug_errors, bug_warnings = _check_bug_file_reference(plan_path, text, work_type, private_notes, home)
    errors.extend(bug_errors)
    warnings.extend(bug_warnings)
    warnings.extend(_legacy_refactoring_warnings(text))
    warnings.extend(_legacy_acceptance_warnings(text))
    warnings.extend(_legacy_verification_name_warnings(text))
    errors.extend(_check_references(text, work_dir))
    warnings.extend(_check_plan_size(text.splitlines()))
    return errors, warnings


def _check_legacy_format(
    plan_path: pathlib.Path,
    text: str,
    work_dir: pathlib.Path,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[list[str], list[_ClassifiedWarning]]:
    """旧形式（単一ファイル9節）が基準を満たすか判定し、エラーと警告を返す。読み取り互換であり新規作成では生成しない。"""
    lines = text.splitlines()
    errors = structure.check_plan_structure(text)
    materials, _material_errors = structure.parse_plan_materials(text)
    warnings: list[_ClassifiedWarning] = []
    warnings.extend(_legacy_action_warnings(text))
    warnings.extend(_legacy_bug_warnings(text))
    warnings.extend(_legacy_refactoring_warnings(text))
    warnings.extend(_legacy_fixed_notation_warnings(text))
    if materials is not None and materials.is_legacy:
        warnings.append(("migration", "提示素材が旧形式である。新規作成・改訂では素材表と要求表へ移行する"))
    parsed, _ambiguity_errors = structure.parse_plan_metadata(text)
    metadata = parsed.values if parsed is not None else {}
    errors.extend(_check_target_repo(metadata.get("対象リポジトリ"), work_dir))
    bug_errors, bug_warnings = _check_bug_file_reference(plan_path, text, metadata.get("作業種別"), private_notes, home)
    errors.extend(bug_errors)
    warnings.extend(bug_warnings)
    errors.extend(_check_references(text, work_dir))
    warnings.extend(_check_plan_size(lines))
    return errors, warnings


def _check_working_plan_filename(plan_path: pathlib.Path, home: pathlib.Path | str | None) -> list[str]:
    """`~/.claude/plans`直下の計画ファイル名が保存工程と同じ受理条件を満たすか確かめる。

    保存工程は`~/.claude/plans`直下のファイル名へ`validate_working_plan_relative_path()`の条件を課す。
    起草時の判定で同じ関数を呼ばないと、合格した計画が保存で初めて拒否され、
    計画バンドルの改名と内部参照の修正という手戻りが生じる。判定規則を本スクリプトへ書き写さない。
    `~/.claude/plans`直下に無い対象は`private-notes/plans/`の日付階層などを含むため、ファイル名が形式を満たすかは判定しない。
    """
    working_root = locations.working_plans_root(home).resolve(strict=False)
    if plan_path.parent.resolve(strict=False) != working_root:
        return []
    try:
        locations.validate_working_plan_relative_path(plan_path.name)
    except ValueError as error:
        return [f"`~/.claude/plans`直下の計画ファイル名が保存工程の受理条件を満たさない: {plan_path.name}: {error}"]
    return []
