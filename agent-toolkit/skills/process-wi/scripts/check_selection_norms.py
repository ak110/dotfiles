"""対象リポジトリの規範指定と公開工程の根拠の検査。"""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib
import typing

import selection_contract
from check_selection_common import _FIX_PATHS, _LANE_NONE, InputError, Violation, _string_list
from check_selection_paths import _normalize_candidate

from agent_toolkit._common import markdown_headings as _markdown_headings
from agent_toolkit._plan import selection as _selection
from agent_toolkit._plan.structure import is_agent_doc_target_file as _is_agent_doc_target_file

_NEXT_NORM_SPEC = (
    "`--work-dir`の`pyproject.toml`の`[tool.agent-toolkit.pick-wi-check]`が定める条件に従い、"
    "該当項目の`プロジェクト規範の指定`へ欠けた記載を書く。`--extend-write-targets`の追加では、"
    "追加入力の該当項目へ更新後の`プロジェクト規範の指定`の全文を書いて同じ追加を再実行する"
)


_NEXT_PUBLIC_RATIONALE = (
    "各WIの`公開工程の規範根拠`へ、公開工程の各パスと実在する規範ファイルのリポジトリ相対パス・見出しの節を1件ずつ記録する"
)


_NORM_SPEC_KEY = "プロジェクト規範の指定"


_NORM_CONFIG_TABLE = ("tool", "agent-toolkit", "pick-wi-check")


_TARGET_SECONDS_KEY = "target-seconds"


_NORM_CONDITION_KEYS = frozenset({"name", "paths", "suffixes", "agent-doc", "require-paths", "require-text"})


@dataclasses.dataclass(frozen=True)
class NormCondition:
    """`プロジェクト規範の指定`に作用する対象リポジトリの条件1件。

    `paths`（リポジトリ相対のファイルか`/`で終わる範囲）と`suffixes`、または`agent-doc`（エージェント向け文書の判定）で
    項目の両書込区分から当たるパスを選ぶ。当たるパスがある項目には、`require-paths`なら当たった各パスを、
    `require-text`ならその各文字列を指定へ書くことを求める。
    """

    name: str
    paths: tuple[str, ...]
    suffixes: tuple[str, ...]
    agent_doc: bool
    require_paths: bool
    require_text: tuple[str, ...]

    def matches(self, path: str) -> bool:
        """パスがこの条件の対象かを返す。"""
        if self.agent_doc and _is_agent_doc_target_file(path):
            return True
        in_scope = any(path == entry or (entry.endswith("/") and path.startswith(entry)) for entry in self.paths)
        return in_scope and (not self.suffixes or path.endswith(self.suffixes))


def _load_check_table(work_dir: pathlib.Path) -> dict[str, object] | None:
    """`work_dir`の`pyproject.toml`から`[tool.agent-toolkit.pick-wi-check]`の表を読む。表が無ければNoneを返す。"""
    config = work_dir / "pyproject.toml"
    if not config.is_file():
        return None
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise InputError(f"条件の設定を読み込めない: {config}: {error}", next_action=_FIX_PATHS) from error
    except tomllib.TOMLDecodeError as error:
        raise InputError(
            f"条件の設定のTOML構文が不正: {config}: {error}",
            next_action=f"{config}のTOML構文を直してから、同じコマンドを再実行する",
        ) from error
    table: object = data
    for key in _NORM_CONFIG_TABLE:
        table = table.get(key) if isinstance(table, dict) else None
    if table is None:
        return None
    if not isinstance(table, dict):
        raise InputError(
            f"{config}の[{'.'.join(_NORM_CONFIG_TABLE)}]が表ではない",
            next_action=f"{config}の[{'.'.join(_NORM_CONFIG_TABLE)}]を表へ直してから、同じコマンドを再実行する",
        )
    return table


def load_norm_conditions(work_dir: pathlib.Path) -> list[NormCondition]:
    """`work_dir`の`pyproject.toml`から`プロジェクト規範の指定`の条件を読む。表が無ければ空の一覧を返す。"""
    config = work_dir / "pyproject.toml"
    location = f"{config}の[{'.'.join(_NORM_CONFIG_TABLE)}]"
    fix = (
        f"{location}の`norm-spec`を、`name`（文字列）、`paths`・`suffixes`・`require-text`（文字列の配列）、"
        "`agent-doc`・`require-paths`（真偽値）だけを持つ表の配列へ直してから、同じコマンドを再実行する"
    )
    table = _load_check_table(work_dir)
    # 同じ表に目標所要秒数だけを置くプロジェクトもあるため、`norm-spec`の欠落は条件なしとして読む。
    if table is None or "norm-spec" not in table:
        return []
    entries = table["norm-spec"]
    if not isinstance(entries, list):
        raise InputError(f"{location}の`norm-spec`が表の配列ではない", next_action=fix)
    conditions: list[NormCondition] = []
    for index, entry in enumerate(entries, start=1):
        problems = _norm_condition_problems(entry)
        if problems:
            raise InputError(f"{location}の`norm-spec`の{index}件目が不正: {'、'.join(problems)}", next_action=fix)
        assert isinstance(entry, dict)
        conditions.append(
            NormCondition(
                name=entry["name"],
                paths=tuple(entry.get("paths", [])),
                suffixes=tuple(entry.get("suffixes", [])),
                agent_doc=entry.get("agent-doc", False),
                require_paths=entry.get("require-paths", False),
                require_text=tuple(entry.get("require-text", [])),
            )
        )
    return conditions


def load_target_seconds(work_dir: pathlib.Path) -> float | None:
    """`work_dir`の`pyproject.toml`が定める1回の処理の目標所要秒数を読む。設定が無ければNoneを返す。

    目標はプロジェクトごとの方針のため配布物に値を持たず、プロジェクトの検査設定から受け取る。
    """
    table = _load_check_table(work_dir)
    if table is None or _TARGET_SECONDS_KEY not in table:
        return None
    value = table[_TARGET_SECONDS_KEY]
    if not selection_contract.is_non_negative_number(value) or value == 0:
        location = f"{work_dir / 'pyproject.toml'}の[{'.'.join(_NORM_CONFIG_TABLE)}]"
        raise InputError(
            f"{location}の`{_TARGET_SECONDS_KEY}`がbool以外の正の有限数ではない: {value!r}",
            next_action=f"{location}の`{_TARGET_SECONDS_KEY}`を正の秒数へ直すか削除してから、同じコマンドを再実行する",
        )
    return typing.cast(float, value)


def _norm_condition_problems(entry: object) -> list[str]:
    """条件1件の欄名と型の誤りを返す。"""
    if not isinstance(entry, dict):
        return ["表ではない"]
    problems = [f"未知の欄: {key}" for key in entry if key not in _NORM_CONDITION_KEYS]
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        problems.append("`name`が空でない文字列ではない")
    problems.extend(
        f"`{key}`が文字列の配列ではない"
        for key in ("paths", "suffixes", "require-text")
        if key in entry and not selection_contract.is_string_list(entry[key])
    )
    problems.extend(
        f"`{key}`が真偽値ではない"
        for key in ("agent-doc", "require-paths")
        if key in entry and not isinstance(entry[key], bool)
    )
    if not entry.get("paths") and entry.get("agent-doc") is not True:
        problems.append("対象を選ぶ`paths`か`agent-doc = true`がない")
    if entry.get("require-paths") is not True and not entry.get("require-text"):
        problems.append("求める記載の`require-paths = true`か`require-text`がない")
    return problems


def check_norm_spec(awi: str, decision: dict[str, object], conditions: list[NormCondition]) -> list[Violation]:
    """条件に当たる項目の`プロジェクト規範の指定`が、条件の求める記載を持つか確かめて違反の行を返す。

    書込予定の区分だけを対象とし、参照先の分類を要求しない。
    """
    write_paths = [
        *_string_list(decision, _selection.WRITE_FILES_KEY),
        *_string_list(decision, _selection.PUBLIC_WRITE_FILES_KEY),
    ]
    candidates = sorted(set(write_paths))
    spec = decision.get(_NORM_SPEC_KEY)
    filled = isinstance(spec, str) and spec.strip() not in {"", _LANE_NONE}
    errors: list[Violation] = []
    for condition in conditions:
        matched = [path for path in candidates if condition.matches(path)]
        if not matched:
            continue
        if not filled:
            errors.append(
                Violation(
                    f"{awi}: プロジェクト規範の指定の不足: {condition.name}（{', '.join(matched)}が当たる）: "
                    f"`{_NORM_SPEC_KEY}`が省略・`なし`・空文字列のいずれか",
                    next_action=_NEXT_NORM_SPEC,
                )
            )
            continue
        assert isinstance(spec, str)
        missing = [*(path for path in matched if condition.require_paths and path not in spec)]
        missing.extend(text for text in condition.require_text if text not in spec)
        if missing:
            errors.append(
                Violation(
                    f"{awi}: プロジェクト規範の指定の不足: {condition.name}: 欠けた記載: {', '.join(missing)}",
                    next_action=_NEXT_NORM_SPEC,
                )
            )
    return errors


def _check_public_write_rationales(items: list[dict[str, object]], work_dir: pathlib.Path) -> list[Violation]:
    """公開工程の各パスに対応する規範ファイルと見出しの実在を確かめる。意味はメインが検収する。"""
    errors: list[Violation] = []
    for item in items:
        expected = set(_string_list(item, _selection.PUBLIC_WRITE_FILES_KEY))
        records = typing.cast(list[dict[str, str]], item.get(_selection.PUBLIC_WRITE_RATIONALES_KEY, []))
        paths = [record["パス"] for record in records]
        failures = []
        if set(paths) != expected or len(paths) != len(set(paths)):
            failures.append(f"パスの対応が不一致: 必須={sorted(expected)}、記録={paths}")
        for record in records:
            norm = record["規範ファイル"]
            target = work_dir / norm
            if not norm or _normalize_candidate(norm) != norm or not target.is_file():
                failures.append(f"規範ファイルがリポジトリ相対の通常ファイルではない: {norm}")
                continue
            try:
                text = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                failures.append(f"規範ファイルを読めない: {norm}: {error}")
                continue
            headings = {heading for level in range(1, 7) for _, heading in _markdown_headings.parse_headings(text, level)}
            if not record["節"].strip() or record["節"] not in headings:
                failures.append(f"規範ファイルに節の見出しがない: {norm}: {record['節']}")
        if failures:
            errors.append(
                Violation(
                    f"{item[_selection.WI_KEY]}: `{_selection.PUBLIC_WRITE_FILES_KEY}`の根拠不足: {'; '.join(failures)}",
                    next_action=_NEXT_PUBLIC_RATIONALE,
                )
            )
    return errors
