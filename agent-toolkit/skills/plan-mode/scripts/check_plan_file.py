"""計画に必要な情報と実体が揃っているか確かめる。

計画メタ情報、見出し構造、`関連WI`、スキル・サブエージェント参照を共有parserで読み取り、基準を満たすか判定する。
旧単一ファイル形式と旧二ファイル形式は読み取り互換で受理し、現行形式への移行をwarningで案内する。
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import subprocess
import sys
import typing

import yaml

try:
    from agent_toolkit._common import next_action as _next_action
    from agent_toolkit._plan import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
        locations as _plan_file,
    )
    from agent_toolkit._plan import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
        selection as _selection,
    )
    from agent_toolkit._plan import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
        structure as _plan_format,
    )
except ImportError as _import_error:
    _SELF = pathlib.Path(__file__).resolve()
    print(
        f"agent_toolkitパッケージを解決できません: {_import_error}\n"
        # パッケージを読めない場合に実行されるため共通の出力関数を使えず、同じ標識を直接書く。
        "次の操作: `atk run-script plan-check -- <計画ファイルの絶対パス>`で起動する",
        file=sys.stderr,
    )
    sys.exit(2)

_PLUGIN_DIR = pathlib.Path(_plan_file.__file__).resolve().parents[2]

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_SKILL_TOOL_PREFIX_RE = re.compile(r"Skillツールで$")
_SKILL_NOUN_PREFIX_RE = re.compile(r"スキル$")
_SKILL_SUFFIX_MARKER_RE = re.compile(r"^スキルを(?:起動|呼び出)")
_DIRECT_INVOCATION_RE = re.compile(r"^を(?:起動|呼び出)")
_AGENT_CALL_RE = re.compile(r"(?:Agentツールで|subagent_type:\s*)`?([A-Za-z0-9:_-]+)`?")
_GENERIC_AGENT_TYPES = frozenset({"claude", "Explore", "Plan"})
# 計画の分量を警告する行数の閾値。
# 既存計画380件を集計した分布（中央値443行、第75百分位732行、第90百分位1312行、最大3476行）の
# 第75百分位と第90百分位の間から選び、通常規模の計画を警告せず肥大した計画だけを検出する。
# 閾値を超えても計画として成立し得るため、エラーではなく警告に留める。
_PLAN_LINE_WARNING_THRESHOLD = 1200

# 選定結果のdecisionが既存計画での再開を示すキーと、行を省略した場合に使う値（`pick-wi.subagent.md`「出力」）。
_RESUME_POSITION_KEY = "再開位置"
_RESUME_POSITION_NONE = "なし"

type _WarningKind = typing.Literal["migration", "advisory"]
type _ClassifiedWarning = tuple[_WarningKind, str]


def _check_lane_selection(
    text: str,
    selection_file: pathlib.Path,
    lane: str,
    prior_plans: tuple[pathlib.Path, ...],
    work_dir: pathlib.Path,
) -> list[str]:
    """選定済みWI集合と人間由来行の根拠が計画に対応するか確かめる。

    計画と比べる集合は、指定レーンのうち`再開位置`を持たない（キーが無いか値が`なし`の）選定結果の項目とする。
    旧欄名で書かれた選定結果も`agent_toolkit._plan.selection`で新しい欄名へそろえて読む。
    再開位置を持つ項目は再開位置が指す既存計画で続け、新しい計画の対象にしないためである。
    不一致は、割り当てた要求を実行とレビューへ渡せない致命的な問題としてerrorにする。
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
        if decision.get(_RESUME_POSITION_KEY, _RESUME_POSITION_NONE) == _RESUME_POSITION_NONE:
            expected.append(decision[_selection.WI_KEY])
    if not lane_awis:
        raise ValueError(f"選定結果にレーンがない: {lane}")
    if len(lane_awis) != len(set(lane_awis)):
        raise ValueError(f"選定結果のレーンにWIが重複する: {lane}")
    if not expected:
        return [f"レーン{lane}の全WIが再開位置を持ち、新しい計画の対象となるWIが無い。再開位置が指す既存計画で続ける"]

    metadata, metadata_errors = _plan_format.parse_plan_metadata(text)
    if metadata_errors:
        return metadata_errors
    errors: list[str] = []
    related = list(metadata.related_wi) if metadata is not None else []
    for prior_plan in prior_plans:
        if not prior_plan.is_absolute():
            raise ValueError(f"先行計画は絶対パスで指定する: {prior_plan}")
        prior_text = prior_plan.read_text(encoding="utf-8")
        prior_metadata, prior_errors = _plan_format.parse_plan_metadata(prior_text)
        if prior_errors or prior_metadata is None:
            errors.extend(f"先行計画{prior_plan}: {error}" for error in prior_errors or ["計画メタ情報がない"])
            continue
        if prior_metadata.values.get("対象リポジトリ") != str(work_dir.resolve()):
            errors.append(f"先行計画の対象リポジトリが異なる: {prior_plan}")
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
            f"関連WIとレーン{lane}の選定結果が一致しない: 欠落={missing}, 余剰={extra}。"
            "計画メタ情報の`関連WI`を、選定結果のうち再開位置を持たないそのレーンのWIへそろえる"
        )

    headings = _plan_format.extract_headings(text)
    section_index = _plan_format.find_heading_index(headings, 2, _plan_format.PLAN_H2_ACTION)
    if section_index is None:
        return errors
    start, end = _plan_format.heading_subtree_range(headings, section_index)
    lines = _plan_format.lines_within(list(_plan_format.iter_markdown_body_lines(text)), start, end)
    for table in _plan_format.extract_tables(lines):
        if table.header != _plan_format.PLAN_HUMAN_ACTION_TABLE_HEADER:
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


def _git_root(work_dir: pathlib.Path) -> tuple[pathlib.Path | None, str | None]:
    """作業ディレクトリが属するGitルートの正規化済みパスを返す。"""
    result = subprocess.run(
        ["git", "-C", str(work_dir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or "作業ディレクトリのGitルートを解決できない"
    return pathlib.Path(result.stdout.strip()).resolve(), None


def _check_target_repo(declared_value: str | None, work_dir: pathlib.Path) -> list[str]:
    """宣言された対象リポジトリと作業ディレクトリのGitルートが一致するか確かめる。"""
    if declared_value is None:
        return []
    declared_text = declared_value[1:-1] if declared_value.startswith("`") and declared_value.endswith("`") else declared_value
    declared_path = pathlib.Path(declared_text).expanduser()
    if not declared_path.is_absolute():
        declared_path = work_dir / declared_path
    declared_root = declared_path.resolve()
    actual_root, error = _git_root(work_dir)
    if error is not None:
        return [error]
    if declared_root != actual_root:
        return [
            f"計画メタ情報の対象リポジトリが作業ディレクトリのGitルートと一致しない: 計画={declared_root}, 実際={actual_root}"
        ]
    return []


def _check_references(text: str, work_dir: pathlib.Path) -> list[str]:
    """コードフェンスを除く本文のスキル・専用agent参照が実在するか確かめる。"""
    inline_text = _plan_format.markdown_body_text(text)
    errors: list[str] = []
    agent_calls = set(_AGENT_CALL_RE.findall(inline_text)) - _GENERIC_AGENT_TYPES
    skill_calls = _classify_skill_references(inline_text) - agent_calls
    for skill in sorted(skill_calls):
        namespace, separator, qualified_name = skill.partition(":")
        if separator and namespace != "agent-toolkit":
            errors.append(_missing_reference_message("スキル", skill, _known_skill_names(work_dir)))
            continue
        name = qualified_name if separator else namespace
        plugin_candidates = (_PLUGIN_DIR / "skills" / name / "SKILL.md",)
        project_candidates = (
            work_dir / ".claude" / "skills" / name / "SKILL.md",
            work_dir / ".agents" / "skills" / name / "SKILL.md",
        )
        candidates = plugin_candidates if separator else plugin_candidates + project_candidates
        if not any(path.exists() for path in candidates):
            errors.append(_missing_reference_message("スキル", skill, _known_skill_names(work_dir)))
    for agent in sorted(agent_calls):
        namespace, separator, qualified_name = agent.partition(":")
        if separator and namespace != "agent-toolkit":
            errors.append(_missing_reference_message("サブエージェント", agent, _known_agent_names(work_dir)))
            continue
        name = qualified_name if separator else namespace
        plugin_candidates = (_PLUGIN_DIR / "agents" / f"{name}.md",)
        project_candidates = (work_dir / ".claude" / "agents" / f"{name}.md",)
        candidates = plugin_candidates if separator else plugin_candidates + project_candidates
        if not any(path.exists() for path in candidates):
            errors.append(_missing_reference_message("サブエージェント", agent, _known_agent_names(work_dir)))
    return errors


def _known_skill_names(work_dir: pathlib.Path) -> list[str]:
    """参照先として実在するスキル名を、プラグイン修飾付きとプロジェクトの素の名前で返す。"""
    names = [f"agent-toolkit:{path.parent.name}" for path in (_PLUGIN_DIR / "skills").glob("*/SKILL.md")]
    for root in (work_dir / ".claude" / "skills", work_dir / ".agents" / "skills"):
        names.extend(path.parent.name for path in root.glob("*/SKILL.md"))
    return sorted(set(names))


def _known_agent_names(work_dir: pathlib.Path) -> list[str]:
    """参照先として実在するサブエージェント名を返す。"""
    names = [f"agent-toolkit:{path.stem}" for path in (_PLUGIN_DIR / "agents").glob("*.md")]
    names.extend(path.stem for path in (work_dir / ".claude" / "agents").glob("*.md"))
    return sorted(set(names))


def _missing_reference_message(kind: str, reference: str, known: list[str]) -> str:
    """実在しない参照の違反文へ、近い名前の候補と直し方を加える。"""
    candidates = difflib.get_close_matches(reference, known, n=3, cutoff=0.6)
    hint = f"候補: {', '.join(candidates)}。" if candidates else ""
    return f"実在しない{kind}参照: {reference}。{hint}実在する{kind}名へ直すか、起動の形の参照をやめる"


def _classify_skill_references(text: str) -> set[str]:
    """起動または呼び出しを指示するスキル参照だけを返す。"""
    references: set[str] = set()
    for match in _INLINE_CODE_RE.finditer(text):
        reference = match.group(1)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        suffix = text[match.end() : None if line_end < 0 else line_end].lstrip()
        prefix = text[line_start : match.start()].rstrip()
        has_tool_prefix = _SKILL_TOOL_PREFIX_RE.search(prefix) is not None
        has_suffix = _SKILL_SUFFIX_MARKER_RE.match(suffix) is not None
        has_noun_prefix = _SKILL_NOUN_PREFIX_RE.search(prefix) is not None
        has_direct_invocation = _DIRECT_INVOCATION_RE.match(suffix) is not None
        has_marker = has_tool_prefix or has_suffix or (has_noun_prefix and has_direct_invocation)
        is_direct_plugin_call = reference.startswith("agent-toolkit:") and has_direct_invocation
        if has_marker or is_direct_plugin_call:
            references.add(reference)
    return references


def _check_plan_size(lines: list[str]) -> list[_ClassifiedWarning]:
    """計画の行数が閾値を超える場合に警告を返す。"""
    if len(lines) <= _PLAN_LINE_WARNING_THRESHOLD:
        return []
    return [
        (
            "advisory",
            f"計画の行数が閾値を超えている: {len(lines)}行（閾値{_PLAN_LINE_WARNING_THRESHOLD}行）。"
            "重複する記述を単一の情報源へ集約し、`plan-file-standards.md`の`### 実装資料と完了条件`が定める配置規約に従って"
            "逐語本文を付属素材へ分離する",
        )
    ]


def _detail_path_for(plan_path: pathlib.Path) -> pathlib.Path:
    """計画ファイル（メイン）のパスから対応する計画ファイル（詳細）の絶対パスを返す（stem導出）。"""
    return plan_path.with_name(f"{plan_path.stem}{_plan_format.PLAN_DETAIL_SUFFIX}")


def _main_path_for_detail(detail_path: pathlib.Path) -> pathlib.Path:
    """計画ファイル（詳細）のパスからstem対応する計画ファイル（メイン）のパスを返す。"""
    suffix = _plan_format.PLAN_DETAIL_SUFFIX
    if detail_path.name.endswith(suffix):
        return detail_path.with_name(f"{detail_path.name[: -len(suffix)]}.md")
    return detail_path.with_suffix(".md")


def _check_bug_file_reference(
    plan_path: pathlib.Path,
    text: str,
    work_type: str | None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[list[str], list[_ClassifiedWarning]]:
    """`計画ファイル（バグ）`行を持つバグ対応計画の分離先の実在、stemの一致、構造が基準を満たすかを確かめる。

    同行を持たない計画は関連WIの`## 原因分析`に従うため、分離先の参照を判定しない。
    同行の要否は計画構造の自動チェックが計画メタ情報の`関連WI`から判定する。

    新しい参照値は接頭辞を展開せず計画ファイルのディレクトリを基準に解決し、
    既存の可搬表記と絶対パスは読み取り互換として従来と同じ処理で解決する。
    統廃合前の行構成を持つ調査表は読み取りで受理し、新規作成・改訂では移行warningをエラーへ変える。
    """
    if work_type != "バグ対応":
        return [], []
    reference = _plan_format.extract_bug_file_reference(text)
    if reference is None:
        return [], []

    if _plan_file.is_plan_adjunct_reference(reference):
        try:
            reference_path = _plan_file.resolve_plan_adjunct_reference(reference, plan_path=plan_path)
        except (OSError, ValueError) as error:
            return [f"バグ調査ファイルの参照値が不正です: {reference}: {error}"], []
    elif reference.startswith(_plan_file.PORTABLE_PLAN_PREFIX):
        try:
            reference_path = _plan_file.resolve_plan_file(reference, private_notes=private_notes, home=home)
        except (OSError, ValueError) as error:
            return [f"バグ調査ファイルの可搬参照パスが不正です: {reference}: {error}"], []
    else:
        reference_path = pathlib.Path(reference)
        if not reference_path.is_absolute():
            return [
                "バグ調査ファイルの参照は"
                f"`{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}<ファイル名>`、可搬表記または絶対パスにする: {reference}"
            ], []
        try:
            reference_path = _plan_file.resolve_plan_file(reference_path)
        except (OSError, ValueError) as error:
            return [f"バグ調査ファイルの参照パスが不正です: {reference}: {error}"], []

    expected_path = plan_path.with_name(f"{plan_path.stem}.bugs.md")
    if reference_path.resolve() != expected_path.resolve():
        return [f"バグ調査ファイルの参照パスが計画stemと一致しない: 計画={reference_path}, 期待={expected_path}"], []
    if not reference_path.is_file():
        return [f"バグ調査ファイルが実在しない: {reference_path}"], []

    bug_text = reference_path.read_text(encoding="utf-8")
    warnings: list[_ClassifiedWarning] = []
    if _plan_format.has_legacy_bug_investigation_table(bug_text):
        warnings.append(
            (
                "migration",
                "バグ調査ファイルの調査表が統廃合前の行構成である。新規作成・改訂では"
                f"{list(_plan_format.PLAN_BUG_TABLE_ROWS)}の行へ移行する",
            )
        )
    return _plan_format.check_bug_file_structure(bug_text), warnings


def _legacy_action_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧3列表の実施内容表を新4列表へ移行するwarningを返す。"""
    if not _plan_format.has_legacy_action_table(text):
        return []
    return [("migration", "実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する")]


def _legacy_bug_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧形式の本文内バグ調査表を分離先ファイルへ移行するwarningを返す。"""
    if not _plan_format.has_legacy_bug_table(text):
        return []
    return [("migration", "バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する")]


def _legacy_refactoring_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧2列4行のリファクタリング表を現行3列表へ移行するwarningを返す。"""
    if not _plan_format.has_legacy_refactoring_table(text):
        return []
    return [
        (
            "migration",
            "リファクタリング表が旧2列4行形式である。新規作成・改訂では`対象`、`現状の問題`、`対応`の3列表へ移行する",
        )
    ]


def _legacy_acceptance_warnings(text: str) -> list[_ClassifiedWarning]:
    """改名前の列名を持つ受入シナリオ表を現行の列名へ移行するwarningを返す。"""
    if not _plan_format.has_legacy_acceptance_table(text):
        return []
    header = "`, `".join(_plan_format.PLAN_ACCEPTANCE_TABLE_HEADER)
    return [("migration", f"受入シナリオ表の列名が旧形式である。新規作成・改訂では`{header}`の6列表へ移行する")]


def _legacy_h2_warnings(text: str) -> list[_ClassifiedWarning]:
    """新書式で旧見出し別名を使っている場合の移行warningを返す。"""
    if not _plan_format.is_canonical_main_format(text):
        return []
    headings = _plan_format.extract_headings(text)
    names = {heading.text for heading in headings if heading.level == 2}
    warnings: list[_ClassifiedWarning] = []
    if _plan_format.PLAN_H2_LEGACY_AGENT_JUDGMENT in names:
        warnings.append(
            ("migration", "エージェント提案の詳細の見出しが旧形式である。新規作成・改訂では`## エージェント提案詳細`へ移行する")
        )
    if _plan_format.has_legacy_history_user_event(text):
        warnings.append(
            (
                "migration",
                "変更履歴のユーザー発言見出しが旧形式である。新規作成・改訂では"
                f"`### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}<1から始まる連番>`へ移行する",
            )
        )
    if _plan_format.PLAN_H2_LEGACY_HISTORY in names:
        warnings.append(("migration", "変更履歴の見出しが旧形式である。新規作成・改訂では`## 変更履歴（計画時）`へ移行する"))
    if _plan_format.PLAN_H2_LEGACY_PROGRESS in names:
        warnings.append(("migration", "進捗ログの見出しが旧形式である。新規作成・改訂では`## 進捗ログ（実行時）`へ移行する"))
    return warnings


def _legacy_wi_origin_warnings(text: str) -> list[_ClassifiedWarning]:
    """実施内容表の`由来`欄が改名前のWI区分を使っている場合の移行warningを返す。"""
    return [
        ("migration", f"`## 実施内容`の`由来`が旧形式である。新規作成・改訂では`{canonical}`へ移行する")
        for canonical in _plan_format.legacy_wi_origins(text)
    ]


def _legacy_fixed_notation_warnings(text: str) -> list[_ClassifiedWarning]:
    """読み取り互換で受理した旧形式の固定記法に移行警告を返す。"""
    warnings: list[_ClassifiedWarning] = []
    metadata, _errors = _plan_format.parse_plan_metadata(text)
    if metadata is not None and any(
        field == _plan_format.PLAN_METADATA_LEGACY_DETAIL_FIELD for field, _value in metadata.entries
    ):
        warnings.append(("migration", "計画メタ情報の項目名が旧形式である。新規作成・改訂では`計画ファイル（詳細）`へ移行する"))
    if metadata is not None and any(
        field == _plan_format.PLAN_METADATA_LEGACY_RELATED_FEEDBACK_FIELD for field, _value in metadata.entries
    ):
        warnings.append(
            (
                "migration",
                "計画メタ情報の項目名が旧形式である。新規作成・改訂では"
                f"`{_plan_format.PLAN_METADATA_RELATED_WI_FIELD}`へ移行する",
            )
        )
    warnings.extend(_legacy_wi_origin_warnings(text))
    if metadata is not None and _plan_format.PLAN_METADATA_DETAIL_FIELD in metadata.values:
        warnings.append(
            (
                "migration",
                "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
            )
        )
    headings = _plan_format.extract_headings(text)
    if _plan_format.find_heading_index(headings, 2, _plan_format.PLAN_H2_MATERIALS) is not None:
        warnings.append(
            (
                "migration",
                "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の"
                f"`{_plan_format.PLAN_METADATA_RELATED_WI_FIELD}`へ移行する",
            )
        )
    if any(
        line.strip().startswith(_plan_format.PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX)
        for _lineno, line in _plan_format.iter_markdown_body_lines(text)
    ):
        warnings.append(
            (
                "migration",
                "バグ調査ファイル参照が旧形式である。新規作成・改訂では`- 計画ファイル（バグ）:`へ移行する",
            )
        )
    reference = _plan_format.extract_bug_file_reference(text)
    if reference is not None and not _plan_file.is_plan_adjunct_reference(reference):
        warnings.append(
            (
                "migration",
                "計画本文の付属ファイル参照が旧表記である。新規作成・改訂では"
                f"`{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}<ファイル名>`へ移行する",
            )
        )
    return warnings


def _legacy_verification_name_warnings(text: str) -> list[_ClassifiedWarning]:
    """旧検証名の表を読取互換で受理し、改訂時だけ移行を求める。"""
    tables = _plan_format.extract_tables(list(_plan_format.iter_markdown_body_lines(text)))
    legacy_headers = (
        _plan_format.PLAN_LEGACY_CURRENT_IMPLEMENTATION_UNITS_TABLE_HEADER,
        _plan_format.PLAN_LEGACY_CURRENT_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
    )
    if any(
        table.header in legacy_headers
        or (
            table.header == _plan_format.PLAN_VERIFICATION_TABLE_HEADER
            and table.row_labels() == _plan_format.PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS
        )
        for table in tables
    ):
        return [("migration", "検証名が旧形式である。新規作成・改訂では`変更範囲の検証`へ移行する")]
    return []


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
    work_type, main_errors = _plan_format.check_plan_main_structure(
        text,
        origin_notices=origin_notices,
        origin_skips=origin_skips,
        private_notes=private_notes,
        home=home,
    )
    errors.extend(main_errors)
    # 実施内容表の移行を促す指摘とし、環境要因の省略は現行形式でも成立する助言とする。
    warnings.extend(("migration", notice) for notice in origin_notices)
    warnings.extend(("advisory", skip) for skip in origin_skips)

    parsed, _ambiguity_errors = _plan_format.parse_plan_metadata(text)
    metadata = parsed.values if parsed is not None else {}

    detail_text = detail_path.read_text(encoding="utf-8")
    warnings.extend(_legacy_verification_name_warnings(detail_text))
    detail_lines = detail_text.splitlines()
    detail_body_start = _plan_format.markdown_body_start_index(detail_text)
    detail_structure_lines = ["" if index < detail_body_start else line for index, line in enumerate(detail_lines)]
    _outside_detail, detail_fence_errors = _outside_fences(detail_structure_lines)
    errors.extend(detail_fence_errors)
    errors.extend(_plan_format.check_plan_detail_structure(detail_text, work_type))
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
    if parsed is None or _plan_format.PLAN_METADATA_RELATED_WI_FIELD not in parsed.values:
        materials, _material_errors = _plan_format.parse_plan_materials(text)
    warnings.extend(_legacy_h2_warnings(text))
    warnings.extend(_legacy_action_warnings(text))
    warnings.extend(_legacy_fixed_notation_warnings(text))
    is_canonical = _plan_format.is_canonical_main_format(text)
    if is_canonical and not _plan_format.has_human_action_table(text):
        errors.append("canonical形式の`## 実施内容`には人間向け4列表が必要である")
    elif not _plan_format.has_human_action_table(text):
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
    work_type, errors = _plan_format.check_plan_single_file_structure(
        text,
        origin_notices=origin_notices,
        origin_skips=origin_skips,
        private_notes=private_notes,
        home=home,
    )
    warnings: list[_ClassifiedWarning] = [("migration", notice) for notice in origin_notices]
    warnings.extend(("advisory", skip) for skip in origin_skips)
    metadata, _metadata_errors = _plan_format.parse_plan_metadata(text)
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
    errors = _plan_format.check_plan_structure(text)
    materials, _material_errors = _plan_format.parse_plan_materials(text)
    warnings: list[_ClassifiedWarning] = []
    warnings.extend(_legacy_action_warnings(text))
    warnings.extend(_legacy_bug_warnings(text))
    warnings.extend(_legacy_refactoring_warnings(text))
    warnings.extend(_legacy_fixed_notation_warnings(text))
    if materials is not None and materials.is_legacy:
        warnings.append(("migration", "提示素材が旧形式である。新規作成・改訂では素材表と要求表へ移行する"))
    parsed, _ambiguity_errors = _plan_format.parse_plan_metadata(text)
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
    working_root = _plan_file.working_plans_root(home).resolve(strict=False)
    if plan_path.parent.resolve(strict=False) != working_root:
        return []
    try:
        _plan_file.validate_working_plan_relative_path(plan_path.name)
    except ValueError as error:
        return [f"`~/.claude/plans`直下の計画ファイル名が保存工程の受理条件を満たさない: {plan_path.name}: {error}"]
    return []


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
    body_start = _plan_format.markdown_body_start_index(text)
    structure_lines = ["" if index < body_start else line for index, line in enumerate(lines)]
    _outside, errors = _outside_fences(structure_lines)
    errors.extend(_check_working_plan_filename(plan_path, home))

    detail_path = _detail_path_for(plan_path)
    progress_heading = _plan_format.PLAN_H2_PROGRESS
    if detail_path.is_file():
        format_errors, classified_warnings = _check_new_format(detail_path, text, work_dir, private_notes, home)
        if not any(kind == "migration" for kind, _message in classified_warnings):
            classified_warnings.append(("migration", "旧二ファイル書式である。新規作成・改訂では現行の1ファイル書式へ移行する"))
    else:
        h2_names = {heading.text for heading in _plan_format.extract_headings(text) if heading.level == 2}
        current_markers = {
            _plan_format.PLAN_H2_REQUIREMENTS,
            _plan_format.PLAN_H2_CURRENT_PERMANENCE,
        }
        if current_markers <= h2_names:
            progress_heading = _plan_format.PLAN_H2_CURRENT_PROGRESS
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
    if (selection_file is None) != (lane is None):
        raise ValueError("選定結果の出力先ファイルとレーン識別子は組で指定する")
    if prior_plans and selection_file is None:
        raise ValueError("先行計画は選定結果の出力先ファイルとレーン識別子とともに指定する")
    if selection_file is not None and lane is not None:
        if not selection_file.is_absolute() or re.fullmatch(r"lane-\d{2}", lane) is None:
            raise ValueError("選定結果の出力先ファイルは絶対パス、レーン識別子はlane-NN形式で指定する")
        if plan_path in prior_plans or len(prior_plans) != len(set(prior_plans)):
            raise ValueError("追加計画と先行計画に同じファイルを重複指定できない")
        errors.extend(_check_lane_selection(text, selection_file, lane, prior_plans, work_dir))
    if reject_progress_log_rows and _plan_format.has_progress_log_rows(text):
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


def _origin_skip_next_action(warnings: list[_ClassifiedWarning]) -> str | None:
    """由来をWI本文と比べられなかった場合に限り、原因ごとの解消手段を一度ずつ案内する。"""
    skips = [
        message
        for kind, message in warnings
        if kind == "advisory" and message.startswith("`## 実施内容`の由来をWI本文と比べられなかった。")
    ]
    if not skips:
        return None
    actions = []
    if any("private-notesが実在しない:" in message for message in skips):
        actions.append("`atk info`でprivate-notesの場所を確認し、`AGENT_TOOLKIT_PRIVATE_NOTES`をその場所へ合わせて再実行する")
    if any("WIファイルを特定できない:" in message for message in skips):
        actions.append("実施内容と関連WIのファイル名を`atk wi show <ファイル名>`で確かめ、誤りを直して再実行する")
    if any("WI本文を取得できない:" in message for message in skips):
        actions.append("表示された権限・文字コードのエラーの原因を解消して再実行する")
    actions.append(
        "解消できない場合は、由来をWI本文と比べられなかったWIファイル名と原因を委譲元（メインから起動した場合はユーザー）へ報告する"
    )
    return "。".join(actions)


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、計画が基準を満たすか確かめる。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_file", type=pathlib.Path, metavar="PATH", help="検証する計画ファイル（`<計画名>.md`）の絶対パス")
    parser.add_argument(
        "--work-dir",
        type=pathlib.Path,
        metavar="DIR",
        default=pathlib.Path.cwd(),
        help="計画メタ情報の`対象リポジトリ`と一致するかを確かめるGit作業ツリーのパス（省略すると現在のディレクトリを使う）",
    )
    parser.add_argument("--selection-file", type=pathlib.Path, metavar="PATH", help="pickerが保存した選定結果の絶対パス")
    parser.add_argument("--lane", help="選定結果内のlane-NN形式のレーン識別子")
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
        errors, warnings = check(
            args.plan_file,
            args.work_dir,
            reject_migration_warnings=args.reject_migration_warnings,
            reject_progress_log_rows=args.reject_progress_log_rows,
            selection_file=args.selection_file,
            lane=args.lane,
            prior_plans=tuple(args.prior_plan or ()),
            agent_rule_paths=tuple(args.agent_rule_path or ()),
            warning_details=warning_details,
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as error:
        _next_action.report(
            f"計画を確認するための入力を読み込めない: {error}",
            next_action=(
                "計画ファイル（位置引数）と`--prior-plan`へ実在するUTF-8の計画ファイルの絶対パスを、"
                "`--selection-file`へpickerが保存したYAMLの絶対パスを、`--lane`へ`lane-NN`形式の識別子を渡して再実行する"
            ),
        )
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)
    if (next_action := _origin_skip_next_action(warning_details)) is not None:
        print(_next_action.next_action_line(next_action), file=sys.stderr)
    if errors:
        print(
            _next_action.next_action_line(
                "各行が示す規定のとおりに計画ファイルを直し、同じコマンドでもう一度確かめる"
                "（規定は`agent-toolkit:plan-mode`の`references/plan-file-standards.md`に従う）"
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
