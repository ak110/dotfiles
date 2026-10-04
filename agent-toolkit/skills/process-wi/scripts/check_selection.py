"""選定結果の`書込対象`がAWI本文の反映先パスを覆うか確かめる。

pickerは`選定`の各項目の`書込対象`をAWI本文の`## 反映内容と反映先`から手で書き写すため、
反映先の一部を欠いた値や、個別ファイルの代わりに上位ディレクトリだけを書いた値がレーン分けへ渡り得る。
本スクリプトは`レーン`が`なし`でない各項目について、同節のインラインコードから反映先パスを抽出し、
`書込対象`と`書き込まない反映先`の双方に照らして次の4区分の違反を報告する。
旧欄名（`decisions`、`awi`、`lane`、`write_files`、`excluded_paths`）で書かれた選定結果も同じ意味で読む。

- 未被覆: 反映先パスが`書込対象`の同じパスにも、`書込対象`のディレクトリ範囲の配下にも、`書き込まない反映先`にも無い
- 広すぎる範囲: `書込対象`のディレクトリ範囲の配下に反映先パスがあるのに、反映先がその範囲自身もそれを含む範囲も挙げていない
- `書き込まない反映先`の不正: 反映先パスに無いパスを`書き込まない反映先`が含む

- 別レーンの重複根拠不足: 共通ファイルまたは狭い方の範囲が双方のレーンの根拠に無い

4区分はいずれも、レーン分けと重なりの判定が実際の書込対象と異なる結果になるため、違反として終了コード1を返す。
分類と定義の独立性の意味判断はpickerとメインの読解へ委ねる。
入力を読めない場合はチェックを開始できないため終了コード2を返し、内容の違反と区別する。
"""

from __future__ import annotations

import argparse
import itertools
import pathlib
import re
import subprocess
import sys

import markdown_it
import yaml

try:
    from agent_toolkit._atk.wi.frontmatter import observation_wait_metadata as _observation_wait_metadata
    from agent_toolkit._common import markdown_headings as _markdown_headings
    from agent_toolkit._common import next_action as _next_action
    from agent_toolkit._plan import locations as _plan_file
    from agent_toolkit._plan import selection as _selection
except ImportError as _import_error:
    print(
        f"agent_toolkitパッケージを解決できません: {_import_error}\n"
        # パッケージを読めない場合に実行されるため共通の出力関数を使えず、同じ標識を直接書く。
        "次の操作: `atk run-script pick-wi-check -- <選定結果の出力先ファイルの絶対パス>`で起動する",
        file=sys.stderr,
    )
    sys.exit(2)

_TARGET_SECTION = "反映内容と反映先"
_LANE_NONE = "なし"
_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})
# 末尾の`:<行番号>`と`:<行番号>-<行番号>`は参照位置の付記であり、パスの一部ではない。
_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")
# 絶対パス、ホーム起点、変数展開、プレースホルダーはリポジトリ相対パスではない。
_NON_PATH_PREFIXES = ("/", "~", "$", "<")
# ワイルドカードとURLは個別のパスを指さない。
_NON_PATH_FRAGMENTS = ("*", "://")


class InputError(Exception):
    """チェックを開始できない入力の問題。"""


def reflected_paths(body: str, work_dir: pathlib.Path) -> set[str]:
    """AWI本文の`## 反映内容と反映先`から反映先パスの集合を返す。

    `/`を含む候補は、`work_dir`からの相対パスとして実在するか、親ディレクトリが実在する場合に採る。
    親ディレクトリだけの実在で採るのは、反映先が挙げる新設ファイルを含めるためである。
    `/`を含まない候補は、`work_dir`直下に実在するファイルの場合だけ採る。
    `/`を含まない語はコマンド名や識別子であることが多く、実在するファイルだけをパスとみなすためである。
    """
    section = _section_text(body, _TARGET_SECTION)
    if section is None:
        return set()
    paths: set[str] = set()
    for candidate in _inline_codes(section):
        path = _normalize_candidate(candidate)
        if path is None:
            continue
        target = work_dir / path
        if "/" in path:
            if target.exists() or target.parent.is_dir():
                paths.add(path)
        elif target.is_file():
            paths.add(path)
    return paths


def _section_text(body: str, heading: str) -> str | None:
    """トップレベルのATX H2のうち`heading`の節の本文を返す。節が無ければ`None`を返す。"""
    normalized = _markdown_headings.normalize_newlines(body)
    lines = normalized.split("\n")
    headings = _markdown_headings.top_level_atx_headings(normalized, 2)
    for index, (token, content) in enumerate(headings):
        if content.strip() != heading:
            continue
        assert token.map is not None
        next_token = headings[index + 1][0] if index + 1 < len(headings) else None
        end = next_token.map[0] if next_token is not None and next_token.map is not None else len(lines)
        return "\n".join(lines[token.map[1] : end])
    return None


def _inline_codes(text: str) -> list[str]:
    """コードフェンスの外にあるインラインコードの内容を出現順で返す。"""
    codes: list[str] = []
    for token in _MARKDOWN.parse(text):
        if token.type != "inline" or not token.children:
            continue
        codes.extend(child.content for child in token.children if child.type == "code_inline")
    return codes


def _normalize_candidate(candidate: str) -> str | None:
    """インラインコードの内容をリポジトリ相対パスの候補へ整え、パスでなければ`None`を返す。"""
    value = _LINE_SUFFIX_RE.sub("", candidate.strip())
    if not value or any(char.isspace() for char in value):
        return None
    if value.startswith(_NON_PATH_PREFIXES) or any(fragment in value for fragment in _NON_PATH_FRAGMENTS):
        return None
    if ".." in value.rstrip("/").split("/"):
        return None
    return value


def _is_range(path: str) -> bool:
    """末尾の`/`でディレクトリ範囲を表す。"""
    return path.endswith("/")


def _covers(entry: str, path: str) -> bool:
    """`entry`が`path`を同じパスとして、またはパス要素単位の配下として含むかを返す。"""
    return entry == path or (_is_range(entry) and path.startswith(entry))


def check_decision(
    awi: str,
    reflected: set[str],
    write_files: list[str],
    excluded_paths: list[str],
) -> list[str]:
    """選定結果の1件の項目の違反を、AWIのファイル名・区分・パスを含む行の一覧で返す。"""
    errors: list[str] = []
    for path in sorted(reflected):
        if path in excluded_paths or any(_covers(entry, path) for entry in write_files):
            continue
        errors.append(f"{awi}: 未被覆: {path}")
    reflected_ranges = [path for path in reflected if _is_range(path)]
    for entry in write_files:
        if not _is_range(entry):
            continue
        inner = [path for path in reflected if path != entry and path.startswith(entry)]
        if inner and not any(entry.startswith(scope) for scope in reflected_ranges):
            errors.append(f"{awi}: 広すぎる範囲: {entry}")
    errors.extend(f"{awi}: 書き込まない反映先の不正: {path}" for path in excluded_paths if path not in reflected)
    return errors


def check(selection_file: pathlib.Path, work_dir: pathlib.Path, private_notes: pathlib.Path) -> list[str]:
    """選定結果の全項目を確かめ、違反の行を返す。"""
    try:
        selection = yaml.safe_load(selection_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise InputError(f"選定結果を読み込めない: {selection_file}: {error}") from error
    items = _selection.decisions(selection)
    if items is None:
        raise InputError(f"選定結果に`{_selection.DECISIONS_KEY}`の列がない: {selection_file}")
    if not private_notes.is_dir():
        raise InputError(f"private-notesが実在しない: {private_notes}")
    errors: list[str] = []
    for decision in items:
        if not isinstance(decision, dict) or not isinstance(decision.get(_selection.WI_KEY), str):
            raise InputError(f"`{_selection.WI_KEY}`を持たない項目がある: {decision!r}")
        awi = decision[_selection.WI_KEY]
        if decision.get(_selection.LANE_KEY) == _LANE_NONE:
            continue
        write_files = _string_list(decision, _selection.WRITE_FILES_KEY)
        excluded_paths = _string_list(decision, _selection.EXCLUDED_PATHS_KEY)
        try:
            source = _plan_file.find_wi_source(awi, private_notes)
        except OSError as error:
            raise InputError(f"private-notesを走査できない: {private_notes}: {error}") from error
        if source is None:
            errors.append(f"{awi}: 本文を特定できない: private-notes（{private_notes}）の状態ディレクトリに無い")
            continue
        try:
            body = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise InputError(f"AWI本文を読み込めない: {source}: {error}") from error
        errors.extend(check_decision(awi, reflected_paths(body, work_dir), write_files, excluded_paths))
    errors.extend(_check_waiting_selection(selection, items))
    errors.extend(_check_lane_overlaps(items, _selection.lane_costs(selection) or []))
    return errors


def _check_waiting_selection(selection: object, items: list[object]) -> list[str]:
    """通常候補と選定の一致、別に返す観測待ちの形式と非重複を確かめる。"""
    assert isinstance(selection, dict)
    selected = [item[_selection.WI_KEY] for item in items if isinstance(item, dict)]
    errors: list[str] = []
    normal = selection.get("通常候補")
    if normal is not None:
        if not isinstance(normal, list) or any(not isinstance(name, str) for name in normal):
            raise InputError("通常候補にはWIファイル名の列が必要です")
        if len(normal) != len(set(normal)) or set(normal) != set(selected):
            errors.append("通常候補と選定のWI集合が一致しない、または候補が重複している")
    waiting = selection.get("観測待ち", [])
    if not isinstance(waiting, list):
        raise InputError("観測待ちには項目の列が必要です")
    names: set[str] = set()
    for item in waiting:
        if not isinstance(item, dict) or not isinstance(item.get("WI"), str):
            raise InputError("観測待ちの各項目にはWIファイル名が必要です")
        name = item["WI"]
        try:
            _observation_wait_metadata(
                {
                    "observation_wait": {
                        "condition": item.get("条件"),
                        "plan_file": item.get("計画ファイル"),
                        "commit": item.get("実装commit"),
                    }
                }
            )
        except ValueError as error:
            errors.append(f"{name}: 観測待ちの形式が不正: {error}")
        if name in names or name in selected:
            errors.append(f"{name}: 観測待ちが重複、または通常選定へ含まれている")
        names.add(name)
    return errors


def _check_lane_overlaps(items: list[object], costs: list[object]) -> list[str]:
    """別レーンの重複パスが双方の根拠にあるか確かめ、意味の独立性は担当の読解へ残す。"""
    rationales = {
        row[_selection.LANE_KEY]: row.get(_selection.RATIONALE_KEY, "")
        for row in costs
        if isinstance(row, dict) and isinstance(row.get(_selection.LANE_KEY), str)
    }
    assigned = [
        item
        for item in items
        if isinstance(item, dict) and isinstance(item.get(_selection.LANE_KEY), str) and item[_selection.LANE_KEY] != _LANE_NONE
    ]
    errors: list[str] = []
    for left, right in itertools.combinations(assigned, 2):
        lanes = (left[_selection.LANE_KEY], right[_selection.LANE_KEY])
        if lanes[0] == lanes[1]:
            continue
        overlaps = {
            second if _covers(first, second) else first
            for first in _string_list(left, _selection.WRITE_FILES_KEY)
            for second in _string_list(right, _selection.WRITE_FILES_KEY)
            if _covers(first, second) or _covers(second, first)
        }
        for path in sorted(overlaps):
            pattern = re.compile(r"(?<![A-Za-z0-9_./-])" + re.escape(path) + r"(?![A-Za-z0-9_./-])")
            missing = [
                lane for lane in lanes if not isinstance(rationales.get(lane), str) or not pattern.search(rationales[lane])
            ]
            if missing:
                errors.append(
                    f"{left[_selection.WI_KEY]}（{lanes[0]}）と{right[_selection.WI_KEY]}（{lanes[1]}）: "
                    f"重複パスの根拠不足: {path}（{', '.join(missing)}）。"
                    "双方のレーンの根拠へ共通パスと交わらない定義を記し、交わる場合は同じレーンへまとめる"
                )
    return errors


def _string_list(decision: dict[str, object], key: str) -> list[str]:
    """項目の列の欄を文字列の一覧で返す。行の不在は空列として扱う。"""
    value = decision.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InputError(f"{decision.get(_selection.WI_KEY)}の`{key}`が文字列の列ではない: {value!r}")
    return value


def _resolve_work_dir(value: pathlib.Path | None) -> pathlib.Path:
    """`--work-dir`の値か、現在のディレクトリが属するGitルートを返す。"""
    if value is not None:
        if not value.is_dir():
            raise InputError(f"`--work-dir`がディレクトリではない: {value}")
        return value.resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise InputError(f"現在のディレクトリからGitルートを解決できない: {result.stderr.strip()}")
    return pathlib.Path(result.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、選定結果を確かめる。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection_file", type=pathlib.Path, help="pickerが保存した選定結果の絶対パス")
    parser.add_argument("--work-dir", type=pathlib.Path, default=None, help="対象リポジトリの絶対パス")
    args = parser.parse_args(argv)
    try:
        work_dir = _resolve_work_dir(args.work_dir)
        errors = check(args.selection_file, work_dir, _plan_file.private_notes_root())
    except InputError as error:
        _next_action.report(
            str(error),
            next_action=(
                "位置引数へpickerが保存した選定結果YAMLの絶対パスを、`--work-dir`へ対象リポジトリの絶対パスを渡して再実行する。"
                "private-notesが実在しない場合は`atk config get private_notes`が返す場所を確かめる"
            ),
        )
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(
            _next_action.next_action_line(
                "未被覆のパスは`write_files`へ加えるか、書き込まない場合は`excluded_paths`へ加える。"
                "広すぎる範囲は反映先が挙げる個別のパスへ置き換える。不正な`excluded_paths`は除く。"
                "直した後に同じコマンドで確かめる"
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
