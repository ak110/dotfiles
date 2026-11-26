from __future__ import annotations

import pathlib
import re

import yaml
from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
from agent_toolkit._atk.wi.constants import DETAILED_SOURCES, WI_TYPE_AWI, is_detailed, normalized_wi_type
from agent_toolkit._plan import locations as _plan_locations
from agent_toolkit._plan import selection as _selection
from agent_toolkit._plan import structure
from agent_toolkit._plan.structure.formal_verification import missing_judgment_message_for_wis

# 選定結果のdecisionが既存計画での再開を示すキーと、行を省略した場合に使う値
# （`process-wi-selection-format.md`「選定結果の書式」）。
_RESUME_POSITION_KEY = "再開位置"


_RESUME_POSITION_NONE = "なし"


def _check_lane_selection(
    plans: tuple[tuple[pathlib.Path | None, str], ...],
    selection_file: pathlib.Path,
    lane: str,
    prior_plans: tuple[pathlib.Path, ...],
    work_dir: pathlib.Path,
    added_wi: tuple[str, ...] = (),
) -> list[str]:
    """選定済みWI集合と人間由来行の根拠が計画の集合に対応するか確かめる。  pyfltr: allow-comment

    計画と比べる集合は、指定レーンのうち`再開位置`を持たない（キーが無いか値が`なし`の）未統合の選定結果の全項目とする。
    計画は詳細化の要否によらずレーンの全WIを列挙するため、`詳細化要否`はこの照合に使わない。
    `added_wi`は、作業を伴う回答済みUWIの詳細化で新しく作り、計画の一覧へ加えたAWIで、比べる集合へ加える。
    旧欄名で書かれた選定結果も`agent_toolkit._plan.selection`で新しい欄名へそろえて読む。
    再開位置を持つ項目は再開位置が指す既存計画で続け、新しい計画の対象にしないためである。
    不一致は、割り当てた要求を実行とレビューへ渡せない致命的な問題としてerrorにする。
    `plans`は今回確かめる計画（初回に分割した計画の集合では全件）の本文と、診断の先頭に付けるパスの組とする。
    1件だけを確かめる場合はパスを`None`にして従来と同じ診断を返す。
    """
    selection = yaml.safe_load(selection_file.read_text(encoding="utf-8"))
    items = _selection.decisions(selection)
    if items is None:
        raise ValueError(f"選定結果の`{_selection.DECISIONS_KEY}`がYAMLの配列ではない")
    lane_awis: list[str] = []
    expected: list[str] = []
    for decision in items:
        if (
            not isinstance(decision, dict)
            or not isinstance(decision.get(_selection.LANE_KEY), str)
            or not isinstance(decision.get(_selection.WI_KEY), str)
        ):
            raise ValueError(f"選定結果の項目に`{_selection.WI_KEY}`または`{_selection.LANE_KEY}`がない")
        if decision[_selection.LANE_KEY] != lane:
            continue
        lane_awis.append(decision[_selection.WI_KEY])
        if decision.get(_RESUME_POSITION_KEY, _RESUME_POSITION_NONE) != _RESUME_POSITION_NONE:
            continue
        if decision.get(_selection.INTEGRATION_STATE_KEY) == _selection.INTEGRATED:
            continue
        expected.append(decision[_selection.WI_KEY])
    if not lane_awis:
        raise ValueError(f"選定結果にレーンがない: {lane}")
    if len(lane_awis) != len(set(lane_awis)):
        raise ValueError(f"選定結果のレーンにWIが重複する: {lane}")
    expected.extend(name for name in added_wi if name not in expected)
    if not expected:
        return [f"レーン{lane}の全WIが再開位置を持ち、新しい計画の対象となるWIが無い。再開位置が指す既存計画で続ける"]

    return _check_wi_selection(plans, tuple(expected), f"レーン{lane}の選定結果", prior_plans, work_dir)


def related_wi_bodies(text: str, private_notes: pathlib.Path | str | None) -> list[str]:
    metadata, metadata_errors = structure.parse_plan_metadata(text)
    if metadata_errors or metadata is None:
        return []
    root = _plan_locations.private_notes_root(private_notes)
    if not root.is_dir():
        root = None
    bodies: list[str] = []
    for name, _summary in metadata.related_wi:
        source = _plan_locations.find_wi_source(name, root) if root is not None else None
        parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8")) if source is not None else None
        if parsed is not None:
            bodies.append(parsed[1])
    return bodies


def wi_list_target_checks(
    text: str,
    private_notes: pathlib.Path | str | None,
    agent_rule_paths: tuple[str, ...],
    handoff_texts: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """WIリスト形式の計画が列挙したWIの本文を読み、実装の着手前に要る検査を当てる。  pyfltr: allow-comment

    計画は本文を持たないため、計画本文に当てていた検査を一覧のWI本文の連結へ向ける。
    詳細化の印の検査は、着手の時点で一覧の全AWIの本文を凍結するためである（対象外のsourceのAWIは置換自体を拒否する）。
    選定結果を使わないため、選定の無い協調モードの計画にも同じ検査が働く。
    """
    metadata, metadata_errors = structure.parse_plan_metadata(text)
    if metadata_errors or metadata is None:
        return [], []
    root = _plan_locations.private_notes_root(private_notes)
    if not root.is_dir():
        root = None
    errors: list[str] = []
    bodies: list[str] = []
    unmarked: list[str] = []
    for name, _summary in metadata.related_wi:
        source = _plan_locations.find_wi_source(name, root) if root is not None else None
        parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8")) if source is not None else None
        if parsed is None:
            errors.append(f"計画の対象WIを取得できない: {name}。`atk wi show {name} --pull`で取得してから再実行する")
            continue
        data, body = parsed
        bodies.append(body)
        if (
            normalized_wi_type(data.get("type")) == WI_TYPE_AWI
            and data.get("source") not in DETAILED_SOURCES
            and not is_detailed(data)
        ):
            unmarked.append(name)
    if unmarked:
        errors.append(
            f"詳細化が要るAWIに詳細化済みの印が無い: {', '.join(unmarked)}。"
            "`atk wi edit --detail`（回答から作るAWIは`atk wi add --detailed --hold`）で詳細化を保存してから実装に着手する"
        )
    # 詳細化しないWIは本文を置き換えられないため、そのWIで編集するパスは引き継ぎ記録へ書く。  # pyfltr: allow-comment
    joined = "\n".join((*bodies, *handoff_texts))
    errors.extend(
        f"返却予定のエージェント向け文書のパスが計画の対象WIの本文にも引き継ぎ記録にもない: {relative}。"
        "詳細化するWIでは本文へ、詳細化しないWIでは--handoff-fileの引き継ぎ記録へ明記し、同じ一覧でもう一度確かめる"
        for relative in agent_rule_paths
        if relative not in joined
    )
    judgment = missing_judgment_message_for_wis(tuple(bodies), handoff_texts)
    return errors, [judgment] if judgment is not None else []


def _check_wi_selection(
    plans: tuple[tuple[pathlib.Path | None, str], ...],
    expected: tuple[str, ...],
    selection_label: str,
    prior_plans: tuple[pathlib.Path, ...],
    work_dir: pathlib.Path,
) -> list[str]:
    errors: list[str] = []
    duplicate_expected = sorted({name for name in expected if expected.count(name) > 1})
    if duplicate_expected:
        errors.append(f"期待WIが重複する: {duplicate_expected}。比較元の各WIを1回ずつ指定する")
    related: list[tuple[str, str]] = []
    for label, text in plans:
        metadata, metadata_errors = structure.parse_plan_metadata(text)
        if metadata_errors:
            errors.extend(_labeled(label, error) for error in metadata_errors)
            continue
        if metadata is not None:
            related.extend(metadata.related_wi)
    if errors and len(plans) == 1:
        return errors
    for prior_plan in prior_plans:
        if not prior_plan.is_absolute():
            raise ValueError(f"先行計画は絶対パスで指定する: {prior_plan}")
        prior_text = prior_plan.read_text(encoding="utf-8")
        prior_metadata, prior_errors = structure.parse_plan_metadata(prior_text)
        if prior_errors or prior_metadata is None:
            errors.extend(f"先行計画{prior_plan}: {error}" for error in prior_errors or ["計画メタ情報がない"])
            continue
        declared = prior_metadata.values.get("対象リポジトリ")

        if declared is None or pathlib.Path(declared).expanduser().resolve() != work_dir.resolve():
            errors.append(
                f"先行計画の対象リポジトリが異なる: {prior_plan}。"
                f"宣言={prior_metadata.values.get('対象リポジトリ')}、--work-dir={work_dir.resolve()}。"
                "先行計画の選択が違う場合は--prior-planへ同じ対象の計画を指定する。"
                "実行場所が違う場合は--work-dirへ対象worktreeを指定する。"
                "宣言自体が誤っている場合は先行計画の対象リポジトリを修正して再実行する"
            )
        related.extend(prior_metadata.related_wi)
    filenames = [filename for filename, _summary in related]
    duplicates = sorted({filename for filename in filenames if filenames.count(filename) > 1})
    if duplicates:
        errors.append(f"計画間で関連WIが重複する: {duplicates}")
    actual = set(filenames)
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))
    if missing or extra:
        errors.append(
            f"関連WIと{selection_label}が一致しない: 欠落={missing}, 余剰={extra}。"
            "計画メタ情報の`関連WI`を、比較元の全計画対象へそろえる"
        )

    for label, text in plans:
        errors.extend(_labeled(label, error) for error in _missing_human_reasons(text))
    return errors


def _labeled(label: pathlib.Path | None, message: str) -> str:
    return message if label is None else f"{label}: {message}"


def _validate_expected_wi(expected_wi: tuple[str, ...], selection_file: pathlib.Path | None, lane: str | None) -> None:
    if expected_wi and (selection_file is not None or lane is not None):
        raise ValueError("--expected-wiは--selection-file・--laneと同時指定できない。比較元を1方式で指定する")
    invalid = [name for name in expected_wi if re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md", name) is None]
    if invalid:
        raise ValueError(f"期待WIのファイル名が不正: {invalid}。--expected-wiへyyyymmdd-hhmmss-nnn.md形式で指定する")


def _missing_human_reasons(text: str) -> list[str]:
    headings = structure.extract_headings(text)
    section_index = structure.find_heading_index(headings, 2, structure.PLAN_H2_ACTION)
    if section_index is None:
        return []
    start, end = structure.heading_subtree_range(headings, section_index)
    lines = structure.lines_within(list(structure.iter_markdown_body_lines(text)), start, end)
    errors: list[str] = []
    for table in structure.extract_tables(lines):
        if table.header != structure.PLAN_HUMAN_ACTION_TABLE_HEADER:
            continue
        for index, row in enumerate(table.rows):
            if len(row) != len(table.header):
                continue
            origin = row[table.header.index("由来")]
            if (origin.startswith("人間由来のWI (") or origin == "ユーザー指示") and row[
                table.header.index("根拠")
            ].strip() in {"", "-"}:
                errors.append(
                    f"人間由来行の根拠がない: {table.row_location(index)}。"
                    "`根拠`列へ原文の要求単位ごとの分解と、各単位を実施範囲へ採るか外すかを書く"
                )
    return errors
