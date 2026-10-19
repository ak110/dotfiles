"""選定結果の`書込対象`がWI本文の反映先パスを覆うか確かめる。

メインは`選定`の各項目の`書込対象`をAWI本文の`## 反映内容と反映先`、または作業を求める回答済みUWIの
質問と回答から手で書き写すため、反映先の一部を欠いた値や、個別ファイルの代わりに上位ディレクトリだけを書いた値がレーン分けへ渡り得る。
本スクリプトは`レーン`が`なし`でない各項目について、反映先パスを文章とインラインコードから抽出し、
`書込対象`・`公開工程の書込対象`・`書き込まない反映先`の3区分と選定全体の関係を確かめ、次の違反を報告する。
抽出の対象は、AWIでは`## 反映内容と反映先`、frontmatterの`type`が`uwi`の項目ではfrontmatterを除く本文全体
（質問、選択肢と帰結、判断材料、回答）とする。UWIは反映先の節を持たず、作業範囲が回答と判断材料に現れるためである。
旧欄名（`decisions`、`awi`、`lane`、`write_files`、`excluded_paths`）で書かれた選定結果も同じ意味で読む。

- 未被覆: 反映先パスが`書込対象`の同じパスにも、`書込対象`のディレクトリ範囲の配下にも、`書き込まない反映先`にも無い。
  ただし同じ抽出結果に配下の別のパスを持つディレクトリ範囲（範囲説明）は、変更範囲を説明する記述として被覆を求めない。
  範囲説明を覆える区分は`書込対象`だけであり、被覆を求めると全レーンの`書込対象`が広い範囲で包含関係になり、
  レーン間の重なりの判定が働かなくなるためである。範囲説明の配下の個別パスには被覆を求める
- 広すぎる範囲: `書込対象`のディレクトリ範囲の配下に反映先パスがあるのに、反映先がその範囲自身もそれを含む範囲も挙げていない
- `書き込まない反映先`の不正: 反映先パスに無いパスを`書き込まない反映先`が含む
- 区分間の重複: 3区分のうち複数が同じパスまたは包含関係にある範囲を持つ。
  ただし`書き込まない反映先`の範囲が書込区分（`書込対象`・`公開工程の書込対象`）のパスを真に含む組は除く。
  AWI本文が変更しない範囲として挙げる上位ディレクトリの配下で個別のファイルを書く選定を表すためであり、
  そのパスは書込区分の指定を優先し、範囲の残りを書き込まない扱いとする
- `公開工程の書込対象`の根拠不足: 公開工程の規範根拠が各パスに対応せず、規範ファイルや節が実在しない
- 別レーンの重複根拠不足: 共通ファイルまたは狭い方の範囲が双方のレーンの根拠に無い
- 同じ再開計画の別レーン割当: メインが出力した通常中断または観測のみの再開位置が同じ計画を指す項目を別レーンへ置いた
- 導出記録の不正: 完全な新設名・要求の反映範囲・書込区分への対応が成立しない、または同じ新設先を重複宣言した
- 比較値の欠落: 段階2以上の単一段階案比較、または6000秒超の単一段階で段階案比較が無い
- 補正の記録の不正: 補正前の2値・係数・参照した記録が欠けるか、補正後の値が積から1秒を超えてずれる
- 書込範囲の理由の不正: 本文に無い範囲やパスを明示しないUWIの書込対象に理由が対応しない

これらは共有書込の判定や配分の比較が成立しないため、違反として終了コード1を返す。

前記のうち作業ツリーを読む項目ごとの判定（反映先パスの抽出、未被覆・広すぎる範囲・書き込まない反映先の不正・
区分間の重複・書込区分のパスと導出記録の不正、プロジェクト規範の指定、公開工程の規範根拠）は、未統合の項目にだけ適用する。
統合済みの項目は、項目か所属レーンの`統合状態`が`統合済み`の項目と、同じ呼び出しの`--mark-integrated`が指定したレーンの項目とする。
これらの判定は作業前の作業ツリーで成り立つ選定時の前提であり、統合後の作業ツリーで判定し直すと、
レーンの作業による改名・削除・追加で統合済みの項目を拒否し、統合状態の記録と以後の検査が止まるためである。
本文の特定、target_repoとoriginの一致、レーン間の関係と初回配分の判定は全項目へ適用する。
各違反は理由と次の操作の組で生成し、出力では違反の理由の行の後に、出力に現れた違反の次の操作だけを1回ずつまとめた行を置く。
分類と定義の独立性の意味判断はメインの読解へ委ねる。

`--work-dir`の`pyproject.toml`が`[tool.agent-toolkit.pick-wi-check]`の`norm-spec`で`プロジェクト規範の指定`の条件を
定める場合は、条件に当たる項目の指定の省略・`なし`・空文字列と、必要な対象パスや記載の欠落も違反として終了コード1を返す。
条件の内容（規範ファイルの範囲、読む要求の文面）は対象リポジトリの設定が持ち、本スクリプトは設定を消費するだけにする。
共有のスクリプトへプロジェクト固有のパスと文面を書くと、内容の所有者がプロジェクトから共有実装へ移るためである。
表を持たないリポジトリには条件を課さない。設定の構文と型の誤りはチェックを開始できない入力として終了コード2を返す。

被覆を比べる前に、選定結果をYAMLとして読み、`selection-format.md`「選定結果の書式」が定める欄名、必須の欄と値の型を確かめる。
メインは保存直後に本スクリプトを実行して構造を検収する。
チェックを開始できない入力には終了コード2を返し、内容の違反と区別する。
終了コード2の失敗は原因で2群に分かれ、次の操作も群ごとに異なる。
選定結果のYAML構文、欄名、必須の欄および値の型の誤りは、選定結果を直して同じコマンドを再実行する。
選定結果のファイル、`--work-dir`およびprivate-notesを解決できない失敗は、パスや引数を直すか、その場所を確かめる。
次の操作は原因が分かる送出側で`InputError`へ渡し、捕捉側では固定の案内を付けない。

追加選定の統合では`--merge <追加YAML>`・`--lane-map <対応JSON>`・`--output <保存先>`を一括指定する。
対応JSONは追加のレーン識別子を最終レーン識別子へ対応付ける。型を確認したデータの合流はselection_merge.pyが担当し、
本スクリプトは統合後の選定の全体検査が成功した場合だけ結果を原子的に保存する。
`--body-wi`は本文の被覆検査を指定したWIへ限定する。レーンの参照・段階・重なりなどの関係検査は統合結果全体へ適用する。
任意の`--lane-cost-updates <JSON>`は、追加レーンの最終割当に対して確定した補正前後の実装秒数・統合秒数・根拠を渡す。
段階2以上では最上位の`単一段階案の完了見込み秒数`を必須とする。統合時は追加YAMLで全体配分の比較値を再確定する。
本文に新設名を明示しない要求は、`導出した新設先`の範囲と両書込区分への対応を確かめる。要求と配置根拠の意味はメインが検収する。

保存済みWIの書込対象の追加は`--extend-write-targets <追加YAML>`・`--output <保存先>`で行う。
追加YAMLは最上位の`選定`に`WI`・追加の`書込対象`・必要な`導出した新設先`を持つ項目の列を置き、
`レーン間の重なり`に必要な記録の列を置く。省略した列は空列として扱い、同じ値は重ねず入力順に追記する。
記録だけの追記も同じ操作で受け取る。初回配分と他の欄を保持し、全本文の被覆と全体の関係を確かめて成功した場合だけ保存する。
他の更新操作と`--body-wi`は併用できない。

違反が無い場合（終了コード0）だけ、標準出力へ選定の要約を書く。要約はWI総数、通常レーン数、レーンごとのWI件数・段階・
先行レーン・実装秒数・統合秒数・WIファイル名の一覧と、`レーン: なし`の項目の一覧を持つ。受領側がレーン構成を出力ファイルを
開かずに把握できるようにするためであり、根拠と分類の意味の検収は選定結果の本文の読解で行う。
比較欄がある場合は同名の値行も要約へ含める。

UWIの本文がリポジトリ相対パスを明示しない場合、書込対象の各パスに書込範囲の理由を求める。
この成功はUWIの書込範囲を検証した結果ではない。パスを明示しない回答の書込範囲は、
メインの限定調査と読解による検収が確かめる。
"""

from __future__ import annotations

import argparse
import collections.abc
import dataclasses
import functools
import itertools
import json
import pathlib
import re
import sys
import tomllib
import typing
import unicodedata

import markdown_it
import selection_contract
import yaml
from selection_merge import extend_write_targets, merge_selection

from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
from agent_toolkit._common import markdown_headings as _markdown_headings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import remote as _git_remote
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import selection as _selection
from agent_toolkit._plan.structure import is_agent_doc_target_file as _is_agent_doc_target_file

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
_LIST_GAP_RE = re.compile(r"(?:\s*(?:と|および|ならびに|、|・|,))+\s*")
# 語の直後に続くと、その語がグロブや波括弧展開の途中で途切れたことを示す記号。
_GLOB_CHARS = frozenset("*?[{")
# 新設先として採るファイル名は、拡張子までそろった完全な名前に限る。
_COMPLETE_FILE_NAME_RE = re.compile(r"[^/]*[^/.]\.[A-Za-z0-9]+")

_IMPLEMENTATION_SECONDS_KEY = "実装秒数"
_INTEGRATION_SECONDS_KEY = "統合秒数"
_LEGACY_SECONDS_KEYS = {"implementation_seconds": _IMPLEMENTATION_SECONDS_KEY, "integration_seconds": _INTEGRATION_SECONDS_KEY}
_OBSERVATION_PLAN_RE = re.compile(r"計画:\s*([^）]+)")
_ABSOLUTE_PLAN_RE = re.compile(r"(?<!\S)(/\S+?\.md)(?=$|[\s、。）])")

_FIX_PATHS = "位置引数へメインが保存した選定結果YAMLの絶対パスを、`--work-dir`へ対象リポジトリの絶対パスを渡して再実行する"
_FIX_PRIVATE_NOTES = "`atk config get private_notes`が返す場所が実在し読み取れることを確かめてから、同じコマンドを再実行する"
_FIX_YAML = (
    "選定結果のYAML構文を直す。文字列の値を単一引用符で囲み、値の中の`'`は`''`と重ねて書き直してから、同じコマンドを再実行する"
)
_FIX_CONTENT = "選定結果の該当する欄を`selection-format.md`「選定結果の書式」の欄名と型へ直してから、同じコマンドを再実行する"
_FIX_MODEL = "`担当モデル`は`実装担当`か`実行レビュー担当`のキーごとに`<claude|codex|agy>:<model>/<effort>`の値へ直す"


class InputError(_next_action.ActionableError):
    """チェックを開始できない入力の問題。送出側が原因に合う次の操作を持つ。"""


# 終了コード1の違反は理由と次の操作の組で生成し、`main`は出力に現れた違反の次の操作だけを1回ずつ出力する。
# 次の操作を違反の生成箇所に持たせると、違反の種類を加えたときも次の操作を欠いた違反を生成できない。
Violation = _next_action.ActionableError

_NEXT_WRITE_PATH = (
    "`書込対象`・`公開工程の書込対象`の不正な要素を、実在ファイル、末尾/付き実在ディレクトリ、"
    "本文明示の新設先または正当な`導出した新設先`の個別パスへ直す。本文に無い新設先は"
    "`パス`・`反映範囲`・`要求`・`配置根拠`の4欄を`導出した新設先`へ記録する"
)
_NEXT_UNCOVERED = (
    "未被覆の反映先は、該当WIの元本文の変更要求と限定調査から`書込対象`・`公開工程の書込対象`・"
    "`書き込まない反映先`のいずれか1区分へ分類する。抽出パスや存在だけで区分を決めず、"
    "変更領域が変われば競合とレーン配分も再評価する"
)
_NEXT_BROAD = "広すぎる範囲を、診断行が示す配下の本文の反映先の個別パスへ置き換える"
_NEXT_EXCLUDED = "`書き込まない反映先`から本文の反映先に無いパスを除く"
_NEXT_SECTION_OVERLAP = "区分間で重複するパスは、所有する1区分だけへ残す"
_NEXT_DERIVED = (
    "`導出した新設先`の記録を診断行が示す条件に合わせて直す。新設パスは実在する親ディレクトリと拡張子を持つ完全な名前にし、"
    "反映範囲を配置根拠のある実在ディレクトリへ対応させ、同じパスを1件だけ記録して書込区分へ個別に置く"
)
_NEXT_NORM_SPEC = (
    "`--work-dir`の`pyproject.toml`の`[tool.agent-toolkit.pick-wi-check]`が定める条件に従い、"
    "該当項目の`プロジェクト規範の指定`へ欠けた記載を書く"
)
_NEXT_PUBLIC_RATIONALE = (
    "各WIの`公開工程の規範根拠`へ、公開工程の各パスと実在する規範ファイルのリポジトリ相対パス・見出しの節を1件ずつ記録する"
)
_NEXT_STAGED = "比較した段階案の秒数か`成立する段階案なし`を最上位の`段階案の完了見込み秒数`へ書く"
_NEXT_CORRECTION = (
    "最上位の`見込みの補正`へ係数・参照した記録・除外した記録を、各行へ`補正前の実装秒数`・`補正前の統合秒数`を書き、"
    "実装秒数・統合秒数を補正前の値×係数の1秒以内へそろえる。係数が1以外なら参照した記録を1件以上挙げる"
)
_NEXT_SCOPE_REASON = "該当WIの`書込範囲の理由`へ、理由が必要なパスごとに空でない理由を1件だけ記録し、不要なパスの記録を除く"
_NEXT_MISSING_BODY = "`atk wi list`の出力で対象のWIファイル名を確かめ、選定の`WI`をprivate-notesにあるファイル名へ直す"
_NEXT_SINGLE_STAGE = (
    "比較した単一段階案の秒数を最上位の`単一段階案の完了見込み秒数`へ、配分の説明を`レーンの所要時間`の`根拠`へ書く"
)
_NEXT_SOURCE_MISMATCH = "一致しない欄を、元のWIのfrontmatterの`depends_on`・`target_repo`と選定の`再開位置`が示す計画の値へ直す"
_NEXT_LANE_MODEL = "同じレーンの項目の`担当モデル`の`実装担当`を1つの値へそろえるか、指定の異なる項目を別のレーンへ割り当てる"
_NEXT_RESUME_PLAN = "同じ再開計画を指す項目を同じレーンへまとめる"
_NEXT_STAGE_SEQUENCE = "`レーンの所要時間`の`段階`を1から欠番なく連続する値へ直す"
_NEXT_PRIOR_MISSING = "段階2以上のレーンの`先行レーン`へ、開始前に統合する前段のレーンを挙げる"
_NEXT_PRIOR_INVALID = "`先行レーン`を、選定に存在し自レーンより前の段階にあるレーンへ直す"
_NEXT_OVERLAP_DUPLICATE = "`レーン間の重なり`の記録を共通パスごとに1件へ直す"
_NEXT_OVERLAP_SURPLUS = "`レーン間の重なり`の記録を、実際に`書込対象`を共有するレーンの組と共通パスへ直す"
_NEXT_OVERLAP_SAME_DEFINITION = "`判定`を`交わる`へ直すか、両レーンの定義を互いに独立した値へ直す"
_NEXT_OVERLAP_SAME_STAGE = "定義が交わる2つのレーンを同じレーンへまとめるか、`段階`と`先行レーン`で順に処理する"
_NEXT_OVERLAP_MISSING = "共通パスごとに双方の定義と判定を`レーン間の重なり`へ1件記録する"
_NEXT_PRIOR_UNFOUNDED = "`先行レーン`から、交わる重なりの記録に基づかないレーンを除く"
_NEXT_PRIOR_REQUIRED = "交わる重なりの記録が示す前段のレーンを`先行レーン`へ記録する"
_NEXT_RERUN = "直した後に同じコマンドで確かめる"


_NORM_SPEC_KEY = "プロジェクト規範の指定"
_NORM_CONFIG_TABLE = ("tool", "agent-toolkit", "pick-wi-check")
_NORM_CONDITION_KEYS = frozenset({"name", "paths", "suffixes", "agent-doc", "require-paths", "require-text"})


@dataclasses.dataclass(frozen=True)
class NormCondition:
    """`プロジェクト規範の指定`に作用する対象リポジトリの条件1件。

    `paths`（リポジトリ相対のファイルか`/`で終わる範囲）と`suffixes`、または`agent-doc`（エージェント向け文書の判定）で
    項目の書込対象と反映先から当たるパスを選ぶ。当たるパスがある項目には、`require-paths`なら当たった各パスを、
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


def load_norm_conditions(work_dir: pathlib.Path) -> list[NormCondition]:
    """`work_dir`の`pyproject.toml`から`プロジェクト規範の指定`の条件を読む。表が無ければ空の一覧を返す。"""
    config = work_dir / "pyproject.toml"
    location = f"{config}の[{'.'.join(_NORM_CONFIG_TABLE)}]"
    fix = (
        f"{location}の`norm-spec`を、`name`（文字列）、`paths`・`suffixes`・`require-text`（文字列の配列）、"
        "`agent-doc`・`require-paths`（真偽値）だけを持つ表の配列へ直してから、同じコマンドを再実行する"
    )
    if not config.is_file():
        return []
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
        return []
    entries = table.get("norm-spec") if isinstance(table, dict) else None
    if not isinstance(table, dict) or not isinstance(entries, list):
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


def check_norm_spec(
    awi: str, decision: dict[str, object], reflected: set[str], conditions: list[NormCondition]
) -> list[Violation]:
    """条件に当たる項目の`プロジェクト規範の指定`が、条件の求める記載を持つか確かめて違反の行を返す。

    対象のパスは、書込区分のパスと、`書き込まない反映先`に覆われない反映先のパスとする。
    書き込まない参照先として挙げた規範ファイルにまで指定を求めないためである。
    """
    write_paths = [
        *_string_list(decision, _selection.WRITE_FILES_KEY),
        *_string_list(decision, _selection.PUBLIC_WRITE_FILES_KEY),
    ]
    excluded = _string_list(decision, _selection.EXCLUDED_PATHS_KEY)
    candidates = sorted({*write_paths, *(path for path in reflected if not any(_covers(entry, path) for entry in excluded))})
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


@dataclasses.dataclass(frozen=True)
class _Segment:
    """段落の文章かインラインコードの1区間。`code`は内容全体を1つのパス候補とするインラインコードかを表す。"""

    text: str
    code: bool


def _explicit_paths(text: str, work_dir: pathlib.Path) -> set[str]:
    """文章とインラインコードに明示されたリポジトリ相対パスの集合を返す。

    インラインコードは、内容全体が解決可能なパスの場合に空白を含めて1つの候補とし、非ASCIIの文字を含む
    パス（`docs/dev/ログ監視.md`、`ログ/a.md`）も末尾まで読む。文章の中のパスはASCIIの文字で区切って探し、
    直後に文字か数字の非ASCII文字が続く場合は、その位置から伸ばした文字列のうち作業ツリーに実在する最長のパスを候補とする。
    実在するパスが無く、ASCIIの部分が`/`で終わらず最後の要素に`.`も持たない場合（`docs/design/LLM`）は、
    パスの途中で終わった部分として採らない。
    抽出結果は、作業ツリーに実在するパスか、親ディレクトリが実在する完全なファイル名の新設先に限る。
    `/`を含む候補のうち、末尾が`/`のディレクトリ範囲は実在するディレクトリだけを採用する。
    実在しないファイルの候補は、追跡ファイルのパス末尾と1件だけ一致すればその追跡ファイルへ読み替え、
    それ以外は`_new_file_path`の新設先の条件で採る。
    `/`を含まない候補は、`work_dir`直下に実在するファイルの場合だけ採る。
    ディレクトリに続き区切り記号で列挙されたファイル名は、そのディレクトリ内の候補として同じ条件で採る。
    孤立した語はコマンド名や識別子であることが多く、作業ツリー直下の実在ファイルだけを採用する。
    直後にグロブ記号が続く語は、グロブや波括弧展開の途中で途切れた断片であり個別のパスを指さないため採らない。
    """
    paths: set[str] = set()
    for segments in _inline_runs(text):
        run = "".join(segment.text for segment in segments)
        directory: str | None = None
        explicit_directory = False
        directory_particle = False
        last_end = 0
        for start, end in _path_tokens(segments, work_dir):
            candidate = _normalize_candidate(run[start:end])
            gap = run[last_end:start]
            if directory is not None and not (_LIST_GAP_RE.fullmatch(gap) or directory_particle and gap.strip() == "の"):
                directory = None
            last_end = end
            if candidate is None or run[end : end + 1] in _GLOB_CHARS:
                directory = None
                continue
            if "/" in candidate:
                resolved = _repository_path(candidate, work_dir)
                if resolved is None:
                    directory = None
                    continue
                paths.add(resolved)
                explicit_directory = resolved.endswith("/")
                directory_particle = explicit_directory
                directory = resolved if resolved.endswith("/") else resolved.rsplit("/", 1)[0] + "/"
                continue
            if (work_dir / candidate).is_file():
                paths.add(candidate)
            elif (
                directory is not None
                and (
                    resolved := _repository_path(
                        directory + candidate, work_dir, abbreviated_name=None if explicit_directory else candidate
                    )
                )
                is not None
            ):
                paths.add(resolved)
            directory_particle = False
    return paths


def _path_tokens(segments: list[_Segment], work_dir: pathlib.Path) -> list[tuple[int, int]]:
    """段落を連結した文字列の中で、パス候補の開始と終了の位置を出現順に返す。"""
    tokens: list[tuple[int, int]] = []
    offset = 0
    for segment in segments:
        content = segment.text
        # グロブや波括弧展開を含むインラインコードは個別のパスを指さないため、文章と同じ規則で断片を除く。
        if (
            segment.code
            and content.strip()
            and not any(char in _GLOB_CHARS for char in content)
            and ("/" in content or "." in content)
            and (_repository_path(content.strip(), work_dir) is not None or (work_dir / content.strip()).is_file())
        ):
            tokens.append((offset, offset + len(content)))
            offset += len(content)
            continue
        for match in _PATH_TOKEN_RE.finditer(content):
            end = match.end()
            if end < len(content) and _is_non_ascii_word_char(content[end]):
                extended = _existing_extension(content, match.start(), end, work_dir)
                if extended is None and not _is_complete_ascii_part(match.group()):
                    continue
                end = extended or end
            tokens.append((offset + match.start(), offset + end))
        offset += len(content)
    return tokens


def _is_non_ascii_word_char(char: str) -> bool:
    """非ASCIIの文字か数字（かな、漢字、アクセント付きのラテン文字など）かを返す。句読点、括弧、`・`は含まない。"""
    return ord(char) > 0x7F and unicodedata.category(char)[0] in {"L", "N"}


def _existing_extension(content: str, start: int, end: int, work_dir: pathlib.Path) -> int | None:
    """ASCIIの候補の直後に続く非ASCIIの文字まで伸ばした文字列のうち、作業ツリーに実在する最長のパスの終了位置を返す。"""
    limit = end
    while limit < len(content) and (
        _is_non_ascii_word_char(content[limit])
        or content[limit] in "/._-"
        or content[limit].isascii()
        and content[limit].isalnum()
    ):
        limit += 1
    for stop in range(limit, end, -1):
        if (work_dir / content[start:stop]).exists():
            return stop
    return None


def _is_complete_ascii_part(value: str) -> bool:
    """ASCIIの候補が`/`で終わるか最後の要素に`.`を持つ（パスの途中で終わっていない）かを返す。"""
    return value.endswith("/") or "." in value.rsplit("/", 1)[-1]


def _repository_path(candidate: str, work_dir: pathlib.Path, *, abbreviated_name: str | None = None) -> str | None:
    """`/`を含む候補を、実在するパス・読み替えた追跡ファイル・新設先のいずれかへ解決する。

    推測した親に実体が無い略記は元の名前を追跡ファイルの末尾と比べ、複数候補には明示を求める。
    いずれにも当たらない候補（不在のディレクトリ範囲、途切れた名前など）は`None`を返す。
    """
    target = work_dir / candidate
    if candidate.endswith("/"):
        return candidate if target.is_dir() else None
    if target.is_dir():
        return candidate + "/"
    if target.is_file():
        return candidate
    suffix = "/" + (abbreviated_name or candidate)
    matches = [path for path in _tracked_files(work_dir) if path.endswith(suffix)]
    if len(matches) == 1:
        return matches[0]
    if abbreviated_name is not None and len(matches) > 1:
        raise InputError(
            f"略記 `{abbreviated_name}` の候補が複数ある: {', '.join(sorted(matches))}。"
            "意図するファイルをリポジトリ相対パスで明示する",
            next_action="本文の略記を意図するファイルのリポジトリ相対パスへ直し、選定を再検証する",
        )
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
    result = _git_command.run(["-C", str(work_dir), "ls-files", "-z"], capture_output=True, text=True, check=False, timeout=60)
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


def _inline_runs(text: str) -> list[list[_Segment]]:
    """コードフェンスの外にある文章とインラインコードを、段落ごとに出現順の区間の列で返す。"""
    runs: list[list[_Segment]] = []
    for token in _MARKDOWN.parse(text):
        if token.type != "inline" or not token.children:
            continue
        runs.append(
            [
                _Segment(child.content, child.type == "code_inline")
                for child in token.children
                if child.type in {"text", "code_inline"}
            ]
        )
    return runs


def _normalize_candidate(candidate: str) -> str | None:
    """インラインコードの内容をリポジトリ相対パスの候補へ整え、パスでなければ`None`を返す。"""
    value = _LINE_SUFFIX_RE.sub("", candidate.strip().split("#", 1)[0])
    if not value:
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
    work_dir: pathlib.Path | None = None,
    derived_new_paths: list[dict[str, str]] | None = None,
) -> list[Violation]:
    """選定結果の1件の項目の違反を、AWIのファイル名・区分・パスを含む理由と次の操作の組の一覧で返す。"""
    errors: list[Violation] = []
    derived_paths: set[str] = set()
    derived_scopes: set[str] = set()
    if work_dir is not None:
        derived_paths, derived_scopes, derived_errors = _check_derived_paths(
            awi, [*write_files, *public_write_files], derived_new_paths or [], work_dir
        )
        errors.extend(derived_errors)
        declared = {record["パス"] for record in derived_new_paths or []}
        for name, entries in (
            (_selection.WRITE_FILES_KEY, write_files),
            (_selection.PUBLIC_WRITE_FILES_KEY, public_write_files),
        ):
            for entry in entries:
                failure = _write_path_failure(entry, work_dir, reflected, derived_paths, declared)
                if failure is not None:
                    errors.append(Violation(f"{awi}: `{name}`のパスの不正: {entry}: {failure}", next_action=_NEXT_WRITE_PATH))
    range_descriptions = {
        path for path in reflected if _is_range(path) and any(other != path and other.startswith(path) for other in reflected)
    }
    for path in sorted(reflected - range_descriptions - derived_scopes):
        if path in excluded_paths or any(_covers(entry, path) for entry in [*write_files, *public_write_files]):
            continue
        errors.append(Violation(f"{awi}: 未被覆: {path}", next_action=_NEXT_UNCOVERED))
    reflected_ranges = [path for path in reflected if _is_range(path)]
    for entry in [*write_files, *public_write_files]:
        if not _is_range(entry):
            continue
        inner = sorted(path for path in reflected if path != entry and path.startswith(entry))
        if inner and not any(entry.startswith(scope) for scope in reflected_ranges):
            errors.append(
                Violation(f"{awi}: 広すぎる範囲: {entry}（配下の本文の反映先: {', '.join(inner)}）", next_action=_NEXT_BROAD)
            )
    errors.extend(
        Violation(f"{awi}: 書き込まない反映先の不正: {path}", next_action=_NEXT_EXCLUDED)
        for path in excluded_paths
        if path not in reflected
    )
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
            if (_covers(first, second) or _covers(second, first))
            # 書き込まない範囲が書込区分のパスを真に含む組は、範囲の残りを書き込まない選定として受理する。
            and not (second_name == _selection.EXCLUDED_PATHS_KEY and first != second and _covers(second, first))
        }
        errors.extend(
            Violation(f"{awi}: 区分間の重複: {path}（`{first_name}`と`{second_name}`）", next_action=_NEXT_SECTION_OVERLAP)
            for path in sorted(overlaps)
        )
    return errors


def _check_derived_paths(
    awi: str, writes: list[str], records: list[dict[str, str]], work_dir: pathlib.Path
) -> tuple[set[str], set[str], list[Violation]]:
    """導出記録のパス・範囲・書込への対応を確かめ、正当な範囲だけを被覆へ渡す。"""
    paths: set[str] = set()
    scopes: set[str] = set()
    seen: set[str] = set()
    errors: list[Violation] = []
    for record in records:
        path, scope = record["パス"], record["反映範囲"]
        failures = _derived_record_failures(path, scope, work_dir)
        if path in seen:
            failures.append("同じパスを重複して宣言している")
        if path not in writes:
            failures.append("書込区分（`書込対象`・`公開工程の書込対象`）に同じパスが無い")
        if failures:
            errors.append(
                Violation(
                    f"{awi}: `{_selection.DERIVED_NEW_PATHS_KEY}`の対応が不正: {path}（{scope}）: {'; '.join(failures)}",
                    next_action=_NEXT_DERIVED,
                )
            )
        else:
            paths.add(path)
            scopes.add(scope)
        seen.add(path)
    return paths, scopes, errors


def _derived_record_failures(path: str, scope: str, work_dir: pathlib.Path) -> list[str]:
    """導出記録1件のパスと反映範囲について、成立しない条件を比べた値とともに返す。"""
    failures: list[str] = []
    if (
        _normalize_candidate(path) != path
        or any(char in path for char in "*?[]{}\\")
        or pathlib.PurePosixPath(path).as_posix() != path
    ):
        failures.append("パスが正規化したリポジトリ相対パスではない")
    elif (work_dir / path).exists() and not (work_dir / path).is_file():
        failures.append("パスの実在先が通常ファイルではない")
    elif _new_file_path(path, work_dir) != path:
        failures.append("パスが実在する親ディレクトリと拡張子を持つ完全な新設名ではない")
    if (
        _normalize_candidate(scope) != scope
        or not scope.endswith("/")
        or pathlib.PurePosixPath(scope).as_posix() + "/" != scope
        or not (work_dir / scope).is_dir()
    ):
        failures.append("反映範囲が末尾/付きの実在ディレクトリではない")
    elif not _covers(scope, path):
        failures.append("反映範囲がパスを含まない")
    return failures


def _write_path_failure(
    entry: str, work_dir: pathlib.Path, reflected: set[str], derived_paths: set[str], declared: set[str]
) -> str | None:
    """書込区分の要素1件が成立しない条件を返す。成立する場合は`None`を返す。"""
    target = work_dir / entry
    if _normalize_candidate(entry) != entry:
        return "正規化したリポジトリ相対パスではない"
    if target.is_dir():
        return None if entry.endswith("/") else "実在ディレクトリだが末尾/が無い"
    if target.is_file():
        return None if not entry.endswith("/") else "実在ファイルだが末尾/が付く"
    if target.exists():
        return "実在先がファイルでもディレクトリでもない"
    if entry in reflected or entry in derived_paths:
        return None
    if entry in declared:
        return (
            "未作成のパスで、同じWIの`導出した新設先`の記録が不正なため新設先として受理しない（その記録の診断を直す対象とする）"
        )
    return "未作成のパスで、本文の反映先にも`導出した新設先`にも無い"


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


def _check_scope_reasons(decision: dict[str, object], reflected: set[str], body: str) -> list[Violation]:
    """本文から導いた書込範囲に必要な理由をパスごとに対応付ける。"""
    writes = _string_list(decision, _selection.WRITE_FILES_KEY)
    parsed = _wi_frontmatter.parse_frontmatter(_markdown_headings.normalize_newlines(body))
    pathless_uwi = parsed is not None and parsed[0].get("type") == _UWI_TYPE and not reflected
    required = {path for path in writes if pathless_uwi or path.endswith("/") and path not in reflected}
    records = typing.cast(list[dict[str, str]], decision.get(_selection.WRITE_SCOPE_REASONS_KEY, []))
    paths = [record["パス"] for record in records]
    empty_reasons = [record["パス"] for record in records if not record["理由"].strip()]
    if set(paths) != required or len(paths) != len(set(paths)) or empty_reasons:
        return [
            Violation(
                f"{decision[_selection.WI_KEY]}: `{_selection.WRITE_SCOPE_REASONS_KEY}`が不一致: "
                f"必須={sorted(required)}、記録={paths}、理由が空={empty_reasons}",
                next_action=_NEXT_SCOPE_REASON,
            )
        ]
    return []


def _check_estimate_records(selection: dict[str, object], costs: list[dict[str, object]]) -> list[Violation]:
    """段階案の比較と補正の記録を、所要時間の値へ対応付ける。"""
    errors: list[Violation] = []
    costs = [dict(row) for row in costs]
    for row in costs:
        for legacy, current in _LEGACY_SECONDS_KEYS.items():
            if current not in row and legacy in row:
                row[current] = row[legacy]
    if (
        costs
        and all(row.get(_selection.STAGE_KEY, 1) == 1 for row in costs)
        and any(typing.cast(float, row[_IMPLEMENTATION_SECONDS_KEY]) > 6000 for row in costs)
        and _selection.STAGED_ESTIMATE_KEY not in selection
    ):
        errors.append(Violation(f"6000秒超の単一段階案に`{_selection.STAGED_ESTIMATE_KEY}`がない", next_action=_NEXT_STAGED))
    correction = selection.get(_selection.ESTIMATE_CORRECTION_KEY)
    if not isinstance(correction, dict):
        return [*errors, Violation(f"`{_selection.ESTIMATE_CORRECTION_KEY}`がない", next_action=_NEXT_CORRECTION)]
    coefficient = correction["係数"]
    if coefficient != 1 and not correction["参照した記録"]:
        errors.append(Violation("係数が1以外の`見込みの補正`に参照した記録がない", next_action=_NEXT_CORRECTION))
    for row in costs:
        for corrected, raw in (
            (_IMPLEMENTATION_SECONDS_KEY, _selection.UNCORRECTED_IMPLEMENTATION_KEY),
            (_INTEGRATION_SECONDS_KEY, _selection.UNCORRECTED_INTEGRATION_KEY),
        ):
            if raw not in row or abs(typing.cast(float, row[corrected]) - typing.cast(float, row[raw]) * coefficient) > 1:
                errors.append(
                    Violation(
                        f"{row[_selection.LANE_KEY]}: `{raw}`の欠落または`{corrected}`と補正の積の不一致: "
                        f"補正前={row.get(raw)!r}、係数={coefficient}、補正後={row[corrected]}",
                        next_action=_NEXT_CORRECTION,
                    )
                )
    return errors


def check(
    selection_file: pathlib.Path,
    work_dir: pathlib.Path,
    private_notes: pathlib.Path,
    body_wis: set[str] | None = None,
    *,
    selection: dict[str, object] | None = None,
    integrating: collections.abc.Set[str] = frozenset(),
) -> list[Violation]:
    """指定した集合の本文と選定全体の関係を確かめ、違反を返す。省略時は全本文を対象とする。

    統合済みの項目（項目か所属レーンの`統合状態`が`統合済み`の項目と、`integrating`が示す同じ呼び出しで
    統合状態を記録するレーンの項目）には、作業ツリーに依存する項目ごとの判定を適用しない。
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
    integrated_lanes = _selection.integrated_lanes(selection) | set(integrating)
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
        errors.extend(_check_scope_reasons(decision, reflected, body))
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
        errors.extend(check_norm_spec(awi, decision, reflected, conditions))
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
            items, costs, typing.cast(list[dict[str, object]], selection.get(_selection.LANE_OVERLAPS_KEY, []))
        )
    )
    errors.extend(_check_public_write_rationales(pending, work_dir))
    errors.extend(_check_estimate_records(selection, costs))
    errors.extend(selection_contract.allocation_errors(selection))
    errors.extend(_check_allocation_sources(selection, private_notes))
    return errors


def _is_integrated(decision: dict[str, object], integrated_lanes: set[str]) -> bool:
    """項目の`統合状態`か所属レーンの統合状態から、作業を統合済みの項目かを返す。"""
    return (
        decision.get(_selection.INTEGRATION_STATE_KEY) == _selection.INTEGRATED
        or decision[_selection.LANE_KEY] in integrated_lanes
    )


def _needs_body_check(decision: dict[str, object], body_wis: set[str] | None) -> bool:
    """レーンを持ち、本文検査の対象集合に含まれる項目かを返す。"""
    return decision[_selection.LANE_KEY] != _LANE_NONE and (body_wis is None or decision[_selection.WI_KEY] in body_wis)


def _check_allocation_sources(selection: dict[str, object], private_notes: pathlib.Path) -> list[Violation]:
    """依存と再開計画の型付き根拠を元のWIのfrontmatterと選定の再開位置へ対応付け、一致しない欄と両方の値を示す。"""
    data = typing.cast(dict[str, typing.Any], selection[_selection.INITIAL_ALLOCATION_KEY])
    items = {
        item[_selection.WI_KEY]: item for item in typing.cast(list[dict[str, typing.Any]], _selection.decisions(selection))
    }
    writes, stages = selection_contract.current_write_stages(selection)
    sources: dict[str, dict[str, typing.Any]] = {}
    errors: list[Violation] = []
    for group in data["不可分成分"]:
        for edge in group["結合条件"]:
            first, second, kind, evidence = (edge[key] for key in ("WI1", "WI2", "種別", "根拠"))
            # 書込重複は成立の判定が選定の現在値を使うため、比べる元入力を持たない。
            if (
                kind == "書込重複"
                or first not in items
                or second not in items
                or selection_contract.allocation_edge_failures(edge, writes, stages)
            ):
                continue
            mismatches: list[str] = []
            if kind == "依存":
                for name in (first, second):
                    if name not in sources:
                        try:
                            source = _plan_file.find_wi_source(name, private_notes)
                            parsed = None
                            if source is not None:
                                parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8"))
                        except (OSError, UnicodeDecodeError) as error:
                            raise InputError(
                                f"依存元のWI本文を読み込めない: {name}: {error}", next_action=_FIX_PRIVATE_NOTES
                            ) from error
                        sources[name] = parsed[0] if parsed else {}
                source_name = evidence["依存元"]
                actual = sources[source_name].get("depends_on", [])
                if actual != evidence["depends_on"]:
                    mismatches.append(f"depends_on: 根拠={evidence['depends_on']}、{source_name}のfrontmatter={actual}")
                for index, name in enumerate((first, second), 1):
                    repository = sources[name].get("target_repo")
                    resolved = _git_remote.resolve_repo_identifier(repository) if isinstance(repository, str) else None
                    if resolved != evidence[f"リポジトリ{index}"]:
                        mismatches.append(
                            f"リポジトリ{index}: 根拠={evidence[f'リポジトリ{index}']}、{name}のtarget_repo={resolved}"
                        )
            else:
                for index, name in enumerate((first, second), 1):
                    planned = _resume_plan_id(items[name].get("再開位置"))
                    recorded = pathlib.PurePosixPath(evidence[f"計画{index}"]).name
                    if planned != recorded:
                        mismatches.append(f"計画{index}: 根拠の計画={recorded}、{name}の再開位置の計画={planned}")
            if mismatches:
                errors.append(
                    Violation(
                        f"初回配分: {first}と{second}の{kind}の結合根拠が元入力と一致しない: {'; '.join(mismatches)}",
                        next_action=_NEXT_SOURCE_MISMATCH,
                    )
                )
    return errors


def _candidate_records(path: pathlib.Path) -> list[dict[str, typing.Any]]:
    """取得済みwi listのJSON LinesまたはJSON配列を読む。新しい候補照会は行わない。"""
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ValueError("候補情報はJSON LinesまたはJSON objectの配列で指定する")
        return data
    except (OSError, UnicodeError, ValueError) as error:
        raise InputError(
            f"取得済み候補情報を読めない: {path}: {error}",
            next_action="--with-stalenessで取得済みのwi listの保存先を--candidate-fileへ指定する",
        ) from error


def _generate_selection(judgments: object, candidates: pathlib.Path, *, additional: bool) -> dict[str, object]:
    @functools.cache
    def metadata(name: str) -> dict[str, typing.Any]:
        source = _plan_file.find_wi_source(name, _plan_file.private_notes_root())
        if source is None:
            raise InputError(f"生成に使うWI本文が無い: {name}", next_action=_FIX_PRIVATE_NOTES)
        parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8"))
        header = dict(parsed[0]) if parsed else {}
        repo = header.get("target_repo")
        if isinstance(repo, str):
            header["target_repo"] = _git_remote.resolve_repo_identifier(repo)
        return header

    try:
        return selection_contract.generate_selection(
            judgments, _candidate_records(candidates), metadata, _resume_plan_id, additional=additional
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise InputError(
            str(error), next_action="候補情報と意味判断をselection-format.mdの生成入力へそろえて再実行する"
        ) from error


def load_selection(
    selection_file: pathlib.Path,
    *,
    additional: bool = False,
    candidate_file: pathlib.Path | None = None,
) -> dict[str, object]:
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
    if candidate_file is not None:
        selection = _generate_selection(selection, candidate_file, additional=additional)
    errors, model_errors = selection_contract.structure_errors(selection)
    if isinstance(selection, dict) and not additional and _selection.INITIAL_ALLOCATION_KEY not in selection:
        errors.append(
            "初回配分がない。初回候補WI・レーン割当・不可分成分の構成WIと結合条件を、"
            "元候補と初回選定の記録から補い、selection-format.mdの初回配分の形式で再実行する"
        )
    if errors or model_errors:
        next_action = f"{_FIX_MODEL}。{_FIX_CONTENT}" if model_errors else _FIX_CONTENT
        raise InputError(
            "\n".join([f"選定結果の内容が不正: {selection_file}", *model_errors, *errors]), next_action=next_action
        )
    return typing.cast(dict[str, object], selection)


def _check_lane_models(items: list[dict[str, object]]) -> list[Violation]:
    """同一レーンで両立しない実装モデル指定を拒否する。"""
    assigned: dict[str, tuple[str, str]] = {}
    errors: list[Violation] = []
    for item in items:
        models = typing.cast(dict[str, str], item.get(_selection.MODEL_TYPES_KEY, {}))
        lane = item.get(_selection.LANE_KEY)
        model = models.get("実装担当")
        if not isinstance(lane, str) or lane == _LANE_NONE or model is None:
            continue
        previous = assigned.setdefault(lane, (str(item[_selection.WI_KEY]), model))
        if previous[1] != model:
            errors.append(
                Violation(
                    f"{lane}: 実装担当のモデル指定が衝突: {previous[0]}={previous[1]}、{item[_selection.WI_KEY]}={model}",
                    next_action=_NEXT_LANE_MODEL,
                )
            )
    return errors


def _resume_plan_id(value: object) -> str | None:
    """メインが出力した再開位置から同一計画を表す値だけを正規化して返す。"""
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


def _check_resume_plan_lanes(items: list[dict[str, object]]) -> list[Violation]:
    """同じ再開計画を指す項目が複数レーンへ分かれた選定を拒否する。"""
    assigned: dict[str, tuple[str, str]] = {}
    errors: list[Violation] = []
    for item in items:
        lane = item.get(_selection.LANE_KEY)
        plan = _resume_plan_id(item.get("再開位置"))
        if not isinstance(lane, str) or lane == _LANE_NONE or plan is None:
            continue
        awi = str(item[_selection.WI_KEY])
        previous = assigned.setdefault(plan, (awi, lane))
        if previous[1] != lane:
            errors.append(
                Violation(
                    f"同じ再開計画を別レーンへ割り当てている: {plan}: {previous[0]}（{previous[1]}）と{awi}（{lane}）",
                    next_action=_NEXT_RESUME_PLAN,
                )
            )
    return errors


def _check_lane_stages(items: collections.abc.Sequence[object], costs: collections.abc.Sequence[object]) -> list[Violation]:
    """後段の開始条件が先行レーンの統合順序を守るか確かめる。"""
    lanes = {
        item[_selection.LANE_KEY]
        for item in items
        if isinstance(item, dict) and isinstance(item.get(_selection.LANE_KEY), str) and item[_selection.LANE_KEY] != _LANE_NONE
    }
    rows: dict[str, tuple[int, list[str]]] = {}
    integrated = _selection.integrated_lanes({_selection.LANE_COSTS_KEY: list(costs)})
    errors: list[Violation] = []
    for row in costs:
        if not isinstance(row, dict) or not isinstance(row.get(_selection.LANE_KEY), str):
            continue
        lane = row[_selection.LANE_KEY]
        rows[lane] = (
            typing.cast(int, row.get(_selection.STAGE_KEY, 1)),
            typing.cast(list[str], row.get(_selection.PRIOR_LANES_KEY, [])),
        )
    last_pending_stage = max((stage for lane, (stage, _) in rows.items() if lane not in integrated), default=0)
    # 元配分の完了した前段は残し、完了済みの後段だけを新たな待機条件から外す。
    ordered_stages = sorted({stage for stage, _ in rows.values() if stage <= last_pending_stage})
    for expected, stage in enumerate(ordered_stages, start=1):
        if stage != expected:
            errors.append(Violation(f"段階が1から連続しない: 段階{expected}がない", next_action=_NEXT_STAGE_SEQUENCE))
            break
    for lane, (stage, prior) in rows.items():
        if lane not in integrated and stage > 1 and not prior:
            errors.append(Violation(f"{lane}: 段階{stage}の後段に先行レーンがない", next_action=_NEXT_PRIOR_MISSING))
        for predecessor in prior:
            if (
                predecessor not in lanes
                or predecessor not in rows
                or (lane not in integrated and predecessor not in integrated and rows[predecessor][0] >= stage)
            ):
                errors.append(
                    Violation(
                        f"{lane}: 先行レーン{predecessor}が選定に無いか、前の段階のレーンではない",
                        next_action=_NEXT_PRIOR_INVALID,
                    )
                )
    return errors


def _check_lane_overlaps(
    items: collections.abc.Sequence[object], costs: collections.abc.Sequence[object], records: list[dict[str, object]]
) -> list[Violation]:
    """全共有パスに記録があり、段階・先行関係が交わる定義に従うか確かめる。"""
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
    expected: set[tuple[str, str, str]] = set()
    for left, right in itertools.combinations(assigned, 2):
        lanes = sorted((left[_selection.LANE_KEY], right[_selection.LANE_KEY]))
        if lanes[0] == lanes[1]:
            continue
        overlaps = {
            second if _covers(first, second) else first
            for first in _string_list(left, _selection.WRITE_FILES_KEY)
            for second in _string_list(right, _selection.WRITE_FILES_KEY)
            if _covers(first, second) or _covers(second, first)
        }
        expected.update((lanes[0], lanes[1], path) for path in overlaps)
    errors: list[Violation] = []
    seen: set[tuple[str, str, str]] = set()
    dependencies: dict[str, set[str]] = {}
    integrated = _selection.integrated_lanes({_selection.LANE_COSTS_KEY: list(costs)})
    for record in records:
        left, right = sorted((str(record["レーン1"]), str(record["レーン2"])))
        path = str(record["共通パス"])
        key = (left, right, path)
        label = f"{left}と{right}: {path}"
        if key in seen:
            errors.append(Violation(f"{label}: 重なりの記録が重複", next_action=_NEXT_OVERLAP_DUPLICATE))
        seen.add(key)
        if key not in expected:
            errors.append(Violation(f"{label}: 余剰の重なりの記録", next_action=_NEXT_OVERLAP_SURPLUS))
            continue
        if record["判定"] == "交わらない":
            first = typing.cast(list[str], record["レーン1の定義"])
            second = typing.cast(list[str], record["レーン2の定義"])
            if {value.strip() for value in first} & {value.strip() for value in second}:
                errors.append(Violation(f"{label}: 交わらない判定の定義が同一", next_action=_NEXT_OVERLAP_SAME_DEFINITION))
            continue
        left_stage = stages.get(left, (1, []))[0]
        if left in integrated or right in integrated:
            continue
        right_stage = stages.get(right, (1, []))[0]
        if left_stage == right_stage:
            errors.append(Violation(f"{label}: 同じ段階で定義が交わる", next_action=_NEXT_OVERLAP_SAME_STAGE))
        else:
            earlier, later = (left, right) if left_stage < right_stage else (right, left)
            dependencies.setdefault(later, set()).add(earlier)
    for left, right, path in sorted(expected - seen):
        errors.append(Violation(f"{left}と{right}: 重複パスの記録不足: {path}", next_action=_NEXT_OVERLAP_MISSING))
    for lane, (_, prior) in stages.items():
        if lane in integrated:
            continue
        for predecessor in prior:
            if predecessor in integrated:
                continue
            if not _reachable(lane, predecessor, dependencies):
                errors.append(Violation(f"{lane}: 根拠の無い直列化: {predecessor}", next_action=_NEXT_PRIOR_UNFOUNDED))
    actual = {lane: set(prior) for lane, (_, prior) in stages.items()}
    for lane, predecessors in dependencies.items():
        for predecessor in predecessors:
            if not _reachable(lane, predecessor, actual):
                errors.append(
                    Violation(f"{lane}: 交わる先行レーン{predecessor}への依存がない", next_action=_NEXT_PRIOR_REQUIRED)
                )
    return errors


def _reachable(lane: str, target: str, dependencies: dict[str, set[str]]) -> bool:
    """`lane`から先行関係を直接または推移的にたどって`target`へ到達するかを返す。循環があっても探索は停止する。"""
    pending = list(dependencies.get(lane, ()))
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current not in seen:
            seen.add(current)
            pending.extend(dependencies.get(current, ()))
    return False


def _string_list(decision: dict[str, object], key: str) -> list[str]:
    """`load_selection`で型を確かめた項目の列の欄を返す。行の不在は空列として扱う。"""
    return typing.cast(list[str], decision.get(key, []))


def summary_lines(selection: dict[str, object], body_wis: set[str] | None = None) -> list[str]:
    """確認を通った選定結果から、レーン構成の要約行を返す。

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
    lines.append("形式・抽出パスの被覆と全体の関係を確認した。分類と配分の意味はWIの元本文と限定調査の結果で検収する。")
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


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、選定結果を確かめる。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection_file", type=pathlib.Path, metavar="PATH", help="メインが保存した選定結果の絶対パス")
    parser.add_argument("--work-dir", type=pathlib.Path, metavar="DIR", default=None, help="対象リポジトリの絶対パス")
    parser.add_argument("--body-wi", action="append", metavar="WI", help="本文検査するWIファイル名。反復指定可、省略時は全件")
    parser.add_argument("--merge", type=pathlib.Path, metavar="PATH", help="追加選定YAMLの絶対パス")
    parser.add_argument(
        "--extend-write-targets", type=pathlib.Path, metavar="PATH", help="保存済みWIの書込対象・新設先・重なりを追加するYAML"
    )
    parser.add_argument("--lane-map", type=pathlib.Path, metavar="PATH", help="追加レーンから最終レーンへの対応JSON")
    parser.add_argument("--output", type=pathlib.Path, metavar="PATH", help="全体チェックに成功した選定結果の保存先")
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
        help=("検収済みレーンと所属項目の統合状態を記録する。その項目には作業ツリーに依存する判定を適用しない。反復指定可"),
    )
    parser.add_argument(
        "--lane-cost-updates", type=pathlib.Path, metavar="PATH", help="最終レーンの確定した秒数と根拠のJSON（統合時だけ）"
    )
    args = parser.parse_args(argv)
    if args.extend_write_targets is not None and (
        args.output is None
        or any(
            value is not None
            for value in (
                args.merge,
                args.lane_map,
                args.candidate_file,
                args.mark_integrated,
                args.lane_cost_updates,
                args.body_wi,
            )
        )
    ):
        parser.error("--extend-write-targetsは--outputと組で指定し、他の更新操作・--body-wiと併用しない")
    if args.candidate_file is not None and (args.mark_integrated is not None or args.output is None):
        parser.error("--candidate-fileは--outputと組で指定し、--mark-integratedと併用しない")
    if args.mark_integrated is not None and (args.merge is not None or args.lane_map is not None or args.output is None):
        parser.error("--mark-integratedは--outputと組で指定し、--merge・--lane-mapと併用しない")
    if (
        args.mark_integrated is None
        and args.extend_write_targets is None
        and not (args.candidate_file is not None and args.merge is None and args.lane_map is None)
        and any(value is not None for value in (args.merge, args.lane_map, args.output))
        and not all(value is not None for value in (args.merge, args.lane_map, args.output))
    ):
        parser.error("--merge・--lane-map・--outputは組で指定する")
    if args.lane_cost_updates is not None and args.merge is None:
        parser.error("--lane-cost-updatesは--merge・--lane-map・--outputとともに指定する")
    body_wis = set(args.body_wi) if args.body_wi is not None else None
    try:
        work_dir = _resolve_work_dir(args.work_dir)
        selection = load_selection(args.selection_file, candidate_file=args.candidate_file if args.merge is None else None)
        if args.extend_write_targets is not None:
            try:
                addition = yaml.safe_load(args.extend_write_targets.read_text(encoding="utf-8"))
                selection = extend_write_targets(selection, addition)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                raise InputError(
                    str(error),
                    next_action=(
                        "--extend-write-targetsのYAMLを直して再実行する。`選定`の各項目へ`WI`と追加の`書込対象`、"
                        "必要な`導出した新設先`の4欄（`パス`・`反映範囲`・`要求`・`配置根拠`）を置き、"
                        "`レーン間の重なり`へ共通パス・双方の定義・判定を置く"
                    ),
                ) from error
        if args.mark_integrated is not None:
            rows = typing.cast(list[dict[str, object]], _selection.lane_costs(selection))
            unknown = set(args.mark_integrated) - {str(row[_selection.LANE_KEY]) for row in rows}
            if unknown:
                raise InputError(f"統合状態の対象レーンがない: {sorted(unknown)}", next_action=_FIX_CONTENT)
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
                selection = merge_selection(selection, addition, mapping, updates)
            except (OSError, ValueError) as error:
                raise InputError(
                    str(error), next_action="追加入力・レーン対応JSON・費用更新JSONを直して同じ統合操作を再実行する"
                ) from error
            structural, models = selection_contract.structure_errors(selection)
            if structural or models:
                raise InputError("\n".join([*models, *structural]), next_action=_FIX_CONTENT)
        errors = check(
            args.selection_file,
            work_dir,
            _plan_file.private_notes_root(),
            body_wis,
            selection=selection,
            integrating=set(args.mark_integrated or []),
        )
    except InputError as error:
        _next_action.report(error.reason, next_action=error.next_action)
        return 2
    for error in errors:
        print(error.reason, file=sys.stderr)
    if errors:
        # 同じ次の操作を持つ違反が複数あっても、その次の操作は最初に現れた位置で1回だけ出力する。
        actions = list(dict.fromkeys(error.next_action for error in errors))
        print(_next_action.next_action_line("。".join([*actions, _NEXT_RERUN])), file=sys.stderr)
        return 1
    if args.mark_integrated is not None:
        rows = typing.cast(list[dict[str, object]], _selection.lane_costs(selection))
        for row in rows:
            if row[_selection.LANE_KEY] in args.mark_integrated:
                row[_selection.INTEGRATION_STATE_KEY] = _selection.INTEGRATED
        selection[_selection.LANE_COSTS_KEY] = rows
        # 後で同じレーンへ追加が合流してレーン行が未統合へ戻っても、統合済みの項目を区別できるよう項目にも記録する。
        for item in typing.cast(list[dict[str, object]], selection.get(_selection.DECISIONS_KEY, selection.get("decisions"))):
            if item.get(_selection.LANE_KEY, item.get("lane")) in args.mark_integrated:
                item[_selection.INTEGRATION_STATE_KEY] = _selection.INTEGRATED
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
    print("\n".join(summary_lines(selection, body_wis)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
