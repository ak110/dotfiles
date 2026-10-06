"""選定結果の`書込対象`がWI本文の反映先パスを覆うか確かめる。

pickerは`選定`の各項目の`書込対象`をAWI本文の`## 反映内容と反映先`、または作業を求める回答済みUWIの
質問と回答から手で書き写すため、反映先の一部を欠いた値や、個別ファイルの代わりに上位ディレクトリだけを書いた値がレーン分けへ渡り得る。
本スクリプトは`レーン`が`なし`でない各項目について、反映先パスを文章とインラインコードから抽出し、
`書込対象`・`公開工程の書込対象`・`書き込まない反映先`の3区分に照らして次の7区分の違反を報告する。
抽出の対象は、AWIでは`## 反映内容と反映先`、frontmatterの`type`が`uwi`の項目ではfrontmatterを除く本文全体
（質問、選択肢と帰結、判断材料、回答）とする。UWIは反映先の節を持たず、作業範囲が回答と判断材料に現れるためである。
旧欄名（`decisions`、`awi`、`lane`、`write_files`、`excluded_paths`）で書かれた選定結果も同じ意味で読む。

- 未被覆: 反映先パスが`書込対象`の同じパスにも、`書込対象`のディレクトリ範囲の配下にも、`書き込まない反映先`にも無い
- 広すぎる範囲: `書込対象`のディレクトリ範囲の配下に反映先パスがあるのに、反映先がその範囲自身もそれを含む範囲も挙げていない
- `書き込まない反映先`の不正: 反映先パスに無いパスを`書き込まない反映先`が含む
- 区分間の重複: 3区分のうち複数が同じパスまたは包含関係にある範囲を持つ
- `公開工程の書込対象`の根拠不足: 対象リポジトリの規範、節およびpathがレーンの根拠に無い
- 別レーンの重複根拠不足: 共通ファイルまたは狭い方の範囲が双方のレーンの根拠に無い
- 同じ再開計画の別レーン割当: pickerが出力した通常中断または観測のみの再開位置が同じ計画を指す項目を別レーンへ置いた

7区分はいずれも、レーン分けと重なりの判定が実際の共有書込対象と異なる結果になるため、違反として終了コード1を返す。
分類と定義の独立性の意味判断はpickerとメインの読解へ委ねる。

被覆を比べる前に、選定結果をYAMLとして読み、`pick-wi.subagent.md`「出力」が定める欄名、必須の欄と値の型を確かめる。
pickerの保存直後とメインの受領時はどちらも本スクリプトを実行するため、両者は同じ構造を受理する。
チェックを開始できない入力には終了コード2を返し、内容の違反と区別する。
終了コード2の失敗は原因で2群に分かれ、次の操作も群ごとに異なる。
選定結果のYAML構文、欄名、必須の欄および値の型の誤りは、選定結果を直して同じコマンドを再実行する。
選定結果のファイル、`--work-dir`およびprivate-notesを解決できない失敗は、パスや引数を直すか、その場所を確かめる。
次の操作は原因が分かる送出側で`InputError`へ渡し、捕捉側では固定の案内を付けない。

UWIの本文がリポジトリ相対パスを明示しない場合、比べるパスが無いため違反を報告しない。
この成功はUWIの書込範囲を検証した結果ではない。パスを明示しない回答の書込範囲は、
pickerの限定調査とメインの読解による検収が確かめる。
"""

from __future__ import annotations

import argparse
import collections.abc
import functools
import itertools
import pathlib
import re
import subprocess
import sys
import typing

import markdown_it
import yaml

try:
    from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
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
_UWI_TYPE = "uwi"
_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})
# 末尾の`:<行番号>`と`:<行番号>-<行番号>`は参照位置の付記であり、パスの一部ではない。
_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")
# 絶対パス、ホーム起点、変数展開、プレースホルダーはリポジトリ相対パスではない。
_NON_PATH_PREFIXES = ("/", "~", "$", "<")
# ワイルドカードとURLは個別のパスを指さない。
_NON_PATH_FRAGMENTS = ("*", "://")
_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_./:$~<\-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*"
    r"|(?<![A-Za-z0-9_./:$~<\-])[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+"
)
_LIST_GAP_RE = re.compile(r"(?:\s*(?:の|と|および|ならびに|、|・|,))+\s*")
# 語の直後に続くと、その語がグロブや波括弧展開の途中で途切れたことを示す記号。
_GLOB_CHARS = frozenset("*?[{")
# 新設先として採るファイル名は、拡張子までそろった完全な名前に限る。
_COMPLETE_FILE_NAME_RE = re.compile(r"[^/]*[^/.]\.[A-Za-z0-9]+")

_STALENESS_KEY = "鮮度"
_UPSTREAM_TARGETS_KEY = "上流投入先"
_IMPLEMENTATION_SECONDS_KEY = "実装秒数"
_INTEGRATION_SECONDS_KEY = "統合秒数"
_BLOCKERS_KEY = "続行できない理由"
_DECISION_STRING_KEYS = ("再開位置", "プロジェクト固有の公開後の操作の順序", "プロジェクト規範の指定", "上流投入", "上流要求")
_DECISION_KEYS = frozenset(
    {
        _selection.WI_KEY,
        _selection.LANE_KEY,
        _STALENESS_KEY,
        _selection.WRITE_FILES_KEY,
        _selection.PUBLIC_WRITE_FILES_KEY,
        _selection.MODEL_TYPES_KEY,
        _selection.EXCLUDED_PATHS_KEY,
        _UPSTREAM_TARGETS_KEY,
        *_DECISION_STRING_KEYS,
    }
)
_DECISION_REQUIRED_KEYS = (_selection.WI_KEY, _selection.LANE_KEY, _STALENESS_KEY, _selection.WRITE_FILES_KEY)
# 旧形式の選定結果は秒数と先行レーンを英字の欄名で持つ。`_selection.lane_costs`が新しい欄名へそろえない欄名も、
# 版の異なるpickerが書いた選定結果を未知の欄として拒否しないよう既知の欄に含める。
_LEGACY_SECONDS_KEYS = {"implementation_seconds": _IMPLEMENTATION_SECONDS_KEY, "integration_seconds": _INTEGRATION_SECONDS_KEY}
_LANE_COST_KEYS = frozenset(
    {
        _selection.LANE_KEY,
        _selection.STAGE_KEY,
        _selection.PRIOR_LANES_KEY,
        _IMPLEMENTATION_SECONDS_KEY,
        _INTEGRATION_SECONDS_KEY,
        _selection.RATIONALE_KEY,
        "after_lanes",
        *_LEGACY_SECONDS_KEYS,
    }
)
_TOP_LEVEL_KEYS = frozenset({_selection.DECISIONS_KEY, "decisions", _selection.LANE_COSTS_KEY, "lane_costs", _BLOCKERS_KEY})
_MODEL_ROLES = ("実装担当", "実行レビュー担当")
_MODEL_TYPE_RE = re.compile(r"(?:claude|codex|agy):[^,/\s]+/[^,/\s]+")
_OBSERVATION_PLAN_RE = re.compile(r"計画:\s*([^）]+)")
_ABSOLUTE_PLAN_RE = re.compile(r"(?<!\S)(/\S+?\.md)(?=$|[\s、。）])")

_FIX_PATHS = "位置引数へpickerが保存した選定結果YAMLの絶対パスを、`--work-dir`へ対象リポジトリの絶対パスを渡して再実行する"
_FIX_PRIVATE_NOTES = "`atk config get private_notes`が返す場所が実在し読み取れることを確かめてから、同じコマンドを再実行する"
_FIX_YAML = (
    "選定結果のYAML構文を直す。文字列の値を単一引用符で囲み、値の中の`'`は`''`と重ねて書き直してから、同じコマンドを再実行する"
)
_FIX_CONTENT = "選定結果の該当する欄を`pick-wi.subagent.md`「出力」の欄名と型へ直してから、同じコマンドを再実行する"
_FIX_MODEL = "`担当モデル`は`実装担当`か`実行レビュー担当`のキーごとに`<claude|codex|agy>:<model>/<effort>`の値へ直す"


class InputError(_next_action.ActionableError):
    """チェックを開始できない入力の問題。送出側が原因に合う次の操作を持つ。"""


def reflected_paths(text: str, work_dir: pathlib.Path) -> set[str]:
    """WIファイルの全文から反映先パスの集合を返す。

    UWIはfrontmatterを除く本文全体、それ以外は`## 反映内容と反映先`の節を抽出の対象にする。
    """
    parsed = _wi_frontmatter.parse_frontmatter(_markdown_headings.normalize_newlines(text))
    if parsed is not None and parsed[0].get("type") == _UWI_TYPE:
        return _explicit_paths(parsed[1], work_dir)
    section = _section_text(text, _TARGET_SECTION)
    if section is None:
        return set()
    return _explicit_paths(section, work_dir)


def _explicit_paths(text: str, work_dir: pathlib.Path) -> set[str]:
    """文章とインラインコードに明示されたリポジトリ相対パスの集合を返す。

    抽出結果は、作業ツリーに実在するパスか、親ディレクトリが実在する完全なファイル名の新設先に限る。
    `/`を含む候補のうち、末尾が`/`のディレクトリ範囲は実在するディレクトリだけを採る。
    実在しないファイルの候補は、追跡ファイルのパス末尾と1件だけ一致すればその追跡ファイルへ読み替え、
    それ以外は`_new_file_path`の新設先の条件で採る。
    `/`を含まない候補は、`work_dir`直下に実在するファイルの場合だけ採る。
    ディレクトリに続き区切り記号で列挙されたファイル名は、そのディレクトリ内の候補として同じ条件で採る。
    孤立した語はコマンド名や識別子であることが多く、作業ツリー直下の実在ファイルだけを採用する。
    直後にグロブ記号が続く語は、グロブや波括弧展開の途中で途切れた断片であり個別のパスを指さないため採らない。
    """
    paths: set[str] = set()
    for run in _inline_runs(text):
        directory: str | None = None
        last_end = 0
        for match in _PATH_TOKEN_RE.finditer(run):
            candidate = _normalize_candidate(match.group())
            gap = run[last_end : match.start()]
            if directory is not None and not _LIST_GAP_RE.fullmatch(gap):
                directory = None
            last_end = match.end()
            if candidate is None or run[match.end() : match.end() + 1] in _GLOB_CHARS:
                directory = None
                continue
            if "/" in candidate:
                resolved = _repository_path(candidate, work_dir)
                if resolved is None:
                    directory = None
                    continue
                paths.add(resolved)
                directory = resolved if resolved.endswith("/") else resolved.rsplit("/", 1)[0] + "/"
                continue
            if (work_dir / candidate).is_file():
                paths.add(candidate)
            elif directory is not None and (resolved := _repository_path(directory + candidate, work_dir)) is not None:
                paths.add(resolved)
    return paths


def _repository_path(candidate: str, work_dir: pathlib.Path) -> str | None:
    """`/`を含む候補を、実在するパス・読み替えた追跡ファイル・新設先のいずれかへ解決する。

    いずれにも当たらない候補（不在のディレクトリ範囲、途切れた名前など）は`None`を返す。
    """
    target = work_dir / candidate
    if candidate.endswith("/"):
        return candidate if target.is_dir() else None
    if target.exists():
        return candidate
    suffix = "/" + candidate
    matches = [path for path in _tracked_files(work_dir) if path.endswith(suffix)]
    if len(matches) == 1:
        return matches[0]
    return _new_file_path(candidate, work_dir)


def _new_file_path(candidate: str, work_dir: pathlib.Path) -> str | None:
    """拡張子までそろった完全なファイル名で親ディレクトリが実在する候補を、新設先として返す。"""
    name = candidate.rsplit("/", 1)[-1]
    if _COMPLETE_FILE_NAME_RE.fullmatch(name) and (work_dir / candidate).parent.is_dir():
        return candidate
    return None


@functools.cache
def _tracked_files(work_dir: pathlib.Path) -> tuple[str, ...]:
    """`work_dir`の追跡ファイルのリポジトリ相対パスを返す。Gitで取得できない場合は空にする。"""
    result = subprocess.run(
        ["git", "-C", str(work_dir), "ls-files", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return ()
    return tuple(path for path in result.stdout.split("\0") if path)


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


def _inline_runs(text: str) -> list[str]:
    """コードフェンスの外にある文章とインラインコードを段落ごとに連結する。"""
    runs: list[str] = []
    for token in _MARKDOWN.parse(text):
        if token.type != "inline" or not token.children:
            continue
        runs.append("".join(child.content for child in token.children if child.type in {"text", "code_inline"}))
    return runs


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
    public_write_files: list[str],
    excluded_paths: list[str],
) -> list[str]:
    """選定結果の1件の項目の違反を、AWIのファイル名・区分・パスを含む行の一覧で返す。"""
    errors: list[str] = []
    for path in sorted(reflected):
        if path in excluded_paths or any(_covers(entry, path) for entry in [*write_files, *public_write_files]):
            continue
        errors.append(f"{awi}: 未被覆: {path}")
    reflected_ranges = [path for path in reflected if _is_range(path)]
    for entry in [*write_files, *public_write_files]:
        if not _is_range(entry):
            continue
        inner = [path for path in reflected if path != entry and path.startswith(entry)]
        if inner and not any(entry.startswith(scope) for scope in reflected_ranges):
            errors.append(f"{awi}: 広すぎる範囲: {entry}")
    errors.extend(f"{awi}: 書き込まない反映先の不正: {path}" for path in excluded_paths if path not in reflected)
    sections = (
        (_selection.WRITE_FILES_KEY, write_files),
        (_selection.PUBLIC_WRITE_FILES_KEY, public_write_files),
        (_selection.EXCLUDED_PATHS_KEY, excluded_paths),
    )
    for (first_name, first_paths), (second_name, second_paths) in itertools.combinations(sections, 2):
        overlaps = {
            second if _covers(first, second) else first
            for first in first_paths
            for second in second_paths
            if _covers(first, second) or _covers(second, first)
        }
        errors.extend(f"{awi}: 区分間の重複: {path}（`{first_name}`と`{second_name}`）" for path in sorted(overlaps))
    return errors


def _check_public_write_rationales(items: list[dict[str, object]], costs: list[dict[str, object]]) -> list[str]:
    """公開工程所有の各pathを、対象リポジトリ規範の節へ対応付けた根拠だけに限定する。"""
    rationales = {
        row[_selection.LANE_KEY]: row.get(_selection.RATIONALE_KEY, "")
        for row in costs
        if isinstance(row, dict) and isinstance(row.get(_selection.LANE_KEY), str)
    }
    errors: list[str] = []
    for item in items:
        lane = item.get(_selection.LANE_KEY)
        rationale = rationales.get(lane, "") if isinstance(lane, str) else ""
        for path in _string_list(item, _selection.PUBLIC_WRITE_FILES_KEY):
            pattern = re.compile(r"(?<![A-Za-z0-9_./-])" + re.escape(path) + r"(?![A-Za-z0-9_./-])")
            if (
                not isinstance(rationale, str)
                or not pattern.search(rationale)
                or "規範" not in rationale
                or "節" not in rationale
            ):
                errors.append(
                    f"{item.get(_selection.WI_KEY, 'WI不明')}: `{_selection.PUBLIC_WRITE_FILES_KEY}`の根拠不足: {path}。"
                    "レーンの所要時間の根拠へ対象リポジトリの規範、節およびpathを記録する"
                )
    return errors


def check(selection_file: pathlib.Path, work_dir: pathlib.Path, private_notes: pathlib.Path) -> list[str]:
    """選定結果の全項目を確かめ、違反の行を返す。"""
    selection = load_selection(selection_file)
    items = typing.cast(list[dict[str, object]], _selection.decisions(selection))
    costs = typing.cast(list[dict[str, object]], _selection.lane_costs(selection))
    if not private_notes.is_dir():
        raise InputError(f"private-notesが実在しない: {private_notes}", next_action=_FIX_PRIVATE_NOTES)
    errors: list[str] = []
    for decision in items:
        awi = typing.cast(str, decision[_selection.WI_KEY])
        if decision[_selection.LANE_KEY] == _LANE_NONE:
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
            errors.append(f"{awi}: 本文を特定できない: private-notes（{private_notes}）の状態ディレクトリに無い")
            continue
        try:
            body = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise InputError(f"WI本文を読み込めない: {source}: {error}", next_action=_FIX_PRIVATE_NOTES) from error
        errors.extend(check_decision(awi, reflected_paths(body, work_dir), write_files, public_write_files, excluded_paths))
    errors.extend(_check_lane_models(items))
    errors.extend(_check_resume_plan_lanes(items))
    errors.extend(_check_lane_stages(items, costs))
    errors.extend(_check_lane_overlaps(items, costs))
    errors.extend(_check_public_write_rationales(items, costs))
    return errors


def load_selection(selection_file: pathlib.Path) -> dict[str, object]:
    """選定結果を読み、YAML構文、欄名、必須の欄および値の型を確かめて返す。

    ファイルを開けない失敗はパスの誤りとして、読めた内容の誤りは選定結果の修正として、別の次の操作を付けて送出する。
    """
    try:
        text = selection_file.read_text(encoding="utf-8")
    except OSError as error:
        raise InputError(f"選定結果を読み込めない: {selection_file}: {error}", next_action=_FIX_PATHS) from error
    except UnicodeDecodeError as error:
        raise InputError(
            f"選定結果がUTF-8ではない: {selection_file}: {error}",
            next_action="選定結果をUTF-8で保存し直してから、同じコマンドを再実行する",
        ) from error
    try:
        selection = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise InputError(f"選定結果のYAML構文が不正: {selection_file}: {error}", next_action=_FIX_YAML) from error
    errors, model_errors = _structure_errors(selection)
    if errors or model_errors:
        next_action = f"{_FIX_MODEL}。{_FIX_CONTENT}" if model_errors else _FIX_CONTENT
        raise InputError(
            "\n".join([f"選定結果の内容が不正: {selection_file}", *model_errors, *errors]), next_action=next_action
        )
    return typing.cast(dict[str, object], selection)


def _structure_errors(selection: object) -> tuple[list[str], list[str]]:
    """選定結果の構造の誤りを、`担当モデル`以外の誤りと`担当モデル`の誤りに分けて返す。

    1回の実行で全ての誤りを返し、直すたびに次の誤りが現れる往復を避ける。
    """
    if not isinstance(selection, dict):
        return ["最上位が写像ではない"], []
    errors = [f"未知の欄: {key}" for key in selection if key not in _TOP_LEVEL_KEYS]
    model_errors: list[str] = []
    items = _selection.decisions(selection)
    if items is None:
        errors.append(f"`{_selection.DECISIONS_KEY}`の列がない")
        items = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"`{_selection.DECISIONS_KEY}`の{index}件目が写像ではない")
            continue
        label = item.get(_selection.WI_KEY) if isinstance(item.get(_selection.WI_KEY), str) else f"{index}件目"
        errors.extend(f"{label}: 未知の欄: {key}" for key in item if key not in _DECISION_KEYS)
        errors.extend(f"{label}: 必須の欄がない: {key}" for key in _DECISION_REQUIRED_KEYS if key not in item)
        errors.extend(
            f"{label}: `{key}`が文字列ではない: {item[key]!r}"
            for key in (_selection.WI_KEY, _selection.LANE_KEY, *_DECISION_STRING_KEYS)
            if key in item and not isinstance(item[key], str)
        )
        if _STALENESS_KEY in item and not isinstance(item[_STALENESS_KEY], dict):
            errors.append(f"{label}: `{_STALENESS_KEY}`が写像ではない: {item[_STALENESS_KEY]!r}")
        errors.extend(
            f"{label}: `{key}`が文字列の列ではない: {item[key]!r}"
            for key in (
                _selection.WRITE_FILES_KEY,
                _selection.PUBLIC_WRITE_FILES_KEY,
                _selection.EXCLUDED_PATHS_KEY,
            )
            if key in item and not _is_string_list(item[key])
        )
        upstream_targets = item.get(_UPSTREAM_TARGETS_KEY, _LANE_NONE)
        if upstream_targets != _LANE_NONE and not _is_string_list(upstream_targets):
            errors.append(f"{label}: `{_UPSTREAM_TARGETS_KEY}`が文字列の列ではない: {upstream_targets!r}")
        model_types = item.get(_selection.MODEL_TYPES_KEY, {})
        if not isinstance(model_types, dict) or any(
            role not in _MODEL_ROLES or not isinstance(value, str) or not _MODEL_TYPE_RE.fullmatch(value)
            for role, value in model_types.items()
        ):
            model_errors.append(
                f"{label}: `{_selection.MODEL_TYPES_KEY}`が担当別のengine:model/effortではない: {model_types!r}"
            )
    costs = _selection.lane_costs(selection)
    if costs is None:
        errors.append(f"`{_selection.LANE_COSTS_KEY}`の列がない")
        costs = []
    for index, row in enumerate(costs, start=1):
        if not isinstance(row, dict):
            errors.append(f"`{_selection.LANE_COSTS_KEY}`の{index}件目が写像ではない")
            continue
        label = row.get(_selection.LANE_KEY) if isinstance(row.get(_selection.LANE_KEY), str) else f"{index}件目"
        errors.extend(f"{label}: 未知の欄: {key}" for key in row if key not in _LANE_COST_KEYS)
        if not isinstance(row.get(_selection.LANE_KEY), str):
            errors.append(f"{label}: `{_selection.LANE_KEY}`が文字列ではない")
        stage = row.get(_selection.STAGE_KEY, 1)
        if not isinstance(stage, int) or isinstance(stage, bool) or stage < 1:
            errors.append(f"{label}: `{_selection.STAGE_KEY}`が1以上の整数ではない: {stage!r}")
        prior = row.get(_selection.PRIOR_LANES_KEY, [])
        if not _is_string_list(prior) or len(prior) != len(set(prior)):
            errors.append(f"{label}: `{_selection.PRIOR_LANES_KEY}`が重複のない文字列の列ではない: {prior!r}")
        for key in (_IMPLEMENTATION_SECONDS_KEY, _INTEGRATION_SECONDS_KEY):
            legacy = next(name for name, current in _LEGACY_SECONDS_KEYS.items() if current == key)
            value = row.get(key, row.get(legacy))
            if not _is_non_negative_number(value):
                errors.append(f"{label}: `{key}`が0以上の数値ではない: {value!r}")
        rationale = row.get(_selection.RATIONALE_KEY)
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"{label}: `{_selection.RATIONALE_KEY}`が空でない文字列ではない: {rationale!r}")
    blockers = selection.get(_BLOCKERS_KEY, [])
    if not _is_string_list(blockers):
        errors.append(f"`{_BLOCKERS_KEY}`が文字列の列ではない: {blockers!r}")
    return errors, model_errors


def _is_string_list(value: object) -> typing.TypeGuard[list[str]]:
    """値が文字列だけの列かを返す。"""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_non_negative_number(value: object) -> bool:
    """値が真偽値以外の0以上の数値かを返す。"""
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def _check_lane_models(items: list[dict[str, object]]) -> list[str]:
    """同一レーンで両立しない実装モデル指定を拒否する。"""
    assigned: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    for item in items:
        models = typing.cast(dict[str, str], item.get(_selection.MODEL_TYPES_KEY, {}))
        lane = item.get(_selection.LANE_KEY)
        model = models.get("実装担当")
        if not isinstance(lane, str) or lane == _LANE_NONE or model is None:
            continue
        previous = assigned.setdefault(lane, (str(item[_selection.WI_KEY]), model))
        if previous[1] != model:
            errors.append(f"{lane}: 実装担当のモデル指定が衝突: {previous[0]}={previous[1]}、{item[_selection.WI_KEY]}={model}")
    return errors


def _resume_plan_id(value: object) -> str | None:
    """pickerが出力した再開位置から同一計画を表す値だけを正規化して返す。"""
    if not isinstance(value, str) or not value.strip() or value.strip() in {_LANE_NONE, "計画なし"}:
        return None
    observation = _OBSERVATION_PLAN_RE.search(value)
    candidate = observation.group(1).strip() if observation is not None else None
    if candidate == "計画なし":
        return None
    if candidate is None:
        matched = _ABSOLUTE_PLAN_RE.search(value)
        candidate = matched.group(1) if matched is not None else None
    if candidate is None:
        return None
    # 通常中断の`~/.claude/plans`と観測のみの`private-notes/plans/`は親ディレクトリが異なるため、
    # 両方が保持する計画ファイル名を同一計画の識別値にする。
    return pathlib.PurePosixPath(candidate).name


def _check_resume_plan_lanes(items: list[dict[str, object]]) -> list[str]:
    """同じ再開計画を指す項目が複数レーンへ分かれた選定を拒否する。"""
    assigned: dict[str, tuple[str, str]] = {}
    errors: list[str] = []
    for item in items:
        lane = item.get(_selection.LANE_KEY)
        plan = _resume_plan_id(item.get("再開位置"))
        if not isinstance(lane, str) or lane == _LANE_NONE or plan is None:
            continue
        awi = str(item[_selection.WI_KEY])
        previous = assigned.setdefault(plan, (awi, lane))
        if previous[1] != lane:
            errors.append(
                f"同じ再開計画を別レーンへ割り当てている: {plan}: "
                f"{previous[0]}（{previous[1]}）と{awi}（{lane}）。同じレーンへまとめる"
            )
    return errors


def _check_lane_stages(items: collections.abc.Sequence[object], costs: collections.abc.Sequence[object]) -> list[str]:
    """後段の開始条件が先行レーンの統合順序を守るか確かめる。"""
    lanes = {
        item[_selection.LANE_KEY]
        for item in items
        if isinstance(item, dict) and isinstance(item.get(_selection.LANE_KEY), str) and item[_selection.LANE_KEY] != _LANE_NONE
    }
    rows: dict[str, tuple[int, list[str]]] = {}
    errors: list[str] = []
    for row in costs:
        if not isinstance(row, dict) or not isinstance(row.get(_selection.LANE_KEY), str):
            continue
        lane = row[_selection.LANE_KEY]
        rows[lane] = (
            typing.cast(int, row.get(_selection.STAGE_KEY, 1)),
            typing.cast(list[str], row.get(_selection.PRIOR_LANES_KEY, [])),
        )
    ordered_stages = sorted({stage for stage, _ in rows.values()})
    for expected, stage in enumerate(ordered_stages, start=1):
        if stage != expected:
            errors.append(f"段階は1から連続する正整数を指定する: 段階{expected}がない")
            break
    for lane, (stage, prior) in rows.items():
        if stage > 1 and not prior:
            errors.append(f"{lane}: 後段には先行レーンを指定する")
        for predecessor in prior:
            if predecessor not in lanes or predecessor not in rows or rows[predecessor][0] >= stage:
                errors.append(f"{lane}: 先行レーン{predecessor}は前の段階の対象レーンでなければならない")
    return errors


def _check_lane_overlaps(items: collections.abc.Sequence[object], costs: collections.abc.Sequence[object]) -> list[str]:
    """別レーンの重複パスが双方の根拠にあるか確かめ、意味の独立性は担当の読解へ残す。"""
    rationales = {
        row[_selection.LANE_KEY]: row.get(_selection.RATIONALE_KEY, "")
        for row in costs
        if isinstance(row, dict) and isinstance(row.get(_selection.LANE_KEY), str)
    }
    stages = {
        row[_selection.LANE_KEY]: (row.get(_selection.STAGE_KEY, 1), row.get(_selection.PRIOR_LANES_KEY, []))
        for row in costs
        if isinstance(row, dict)
        and isinstance(row.get(_selection.LANE_KEY), str)
        and isinstance(row.get(_selection.STAGE_KEY, 1), int)
        and isinstance(row.get(_selection.PRIOR_LANES_KEY, []), list)
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
        left_stage, left_prior = stages.get(lanes[0], (1, []))
        right_stage, right_prior = stages.get(lanes[1], (1, []))
        if left_stage != right_stage and (
            (left_stage < right_stage and lanes[0] in right_prior) or (right_stage < left_stage and lanes[1] in left_prior)
        ):
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
    """`load_selection`で型を確かめた項目の列の欄を返す。行の不在は空列として扱う。"""
    return typing.cast(list[str], decision.get(key, []))


def _resolve_work_dir(value: pathlib.Path | None) -> pathlib.Path:
    """`--work-dir`の値か、現在のディレクトリが属するGitルートを返す。"""
    if value is not None:
        if not value.is_dir():
            raise InputError(f"`--work-dir`がディレクトリではない: {value}", next_action=_FIX_PATHS)
        return value.resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise InputError(f"現在のディレクトリからGitルートを解決できない: {result.stderr.strip()}", next_action=_FIX_PATHS)
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
        _next_action.report(error.reason, next_action=error.next_action)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(
            _next_action.next_action_line(
                "未被覆のパスは`書込対象`へ加えるか、書き込まない場合は`書き込まない反映先`へ加える。"
                "広すぎる範囲は反映先が挙げる個別のパスへ置き換える。不正な`書き込まない反映先`は除く。"
                "区分間で重複するパスは所有する1区分だけへ残す。`公開工程の書込対象`の根拠不足は、"
                "レーンの所要時間の根拠へ対象リポジトリの規範、節およびpathを記録する。"
                "直した後に同じコマンドで確かめる"
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
