"""計画バンドルを構成するファイルの種別、接尾辞、表示名と、ファイル名から種別を判定する関数。

計画バンドルはメイン計画`<stem>.md`と、同じstemへ種別ごとの接尾辞を付けた付属ファイルから成る。
接尾辞と種別ごとの扱い（メイン計画か、付属ファイルか、一覧に載せるか、現行形式か）は本モジュールだけが持ち、
計画の取得と保存、計画ファイルの自動チェック、`atk serve`の計画ファイル画面とSSH先のヘルパーが同じ定義を読む。
SSH先のヘルパーもimportするため、標準ライブラリだけに依存する。
判定の入力はファイル名だけとする。計画rootの内外と保存形式の検証を含むパスの判定は`_plan.path_kinds`が担う。
"""

from __future__ import annotations

import dataclasses
import enum


class Role(enum.Enum):
    """計画バンドルの中でファイルが担う役割。"""

    MAIN = "main"
    """メイン計画。"""
    ATTACHMENT = "attachment"
    """同じstemのメイン計画に属する付属ファイル。"""
    STANDALONE = "standalone"
    """計画rootに置くが、メイン計画に属さないファイル。"""


@dataclasses.dataclass(frozen=True)
class BundleKind:
    """計画バンドルを構成するファイルの1種別。"""

    key: str
    """種別の識別名。"""
    suffix: str
    """ファイル名の末尾。メイン計画は拡張子`.md`だけを持つ。"""
    role: Role
    current: bool
    """現行形式の計画が作成する種別か。偽の種別は保存済みの旧形式を読むためだけに残す。"""
    display_name: str = ""
    """計画ファイル画面が付属計画間のリンクに使う表示名。空の種別はリンクに載せない。"""
    stored: bool = True
    """計画バンドルとしてprivate-notesへ保存する種別か。偽の種別は`~/.claude/plans`の局所状態に限る。"""
    review_table: bool = False
    """レビュー指摘管理表の種別か。"""

    def matches(self, name: str) -> bool:
        """ファイル名がこの種別の接尾辞で終わるかを返す。

        メイン計画の接尾辞は付属ファイルの接尾辞の末尾と重なるため、メイン計画の判定には`kind_of_name`を使う。
        """
        return name.endswith(self.suffix)

    def name_for(self, stem: str) -> str:
        """計画のstemからこの種別のファイル名を返す。"""
        return f"{stem}{self.suffix}"


MAIN = BundleKind("main", ".md", Role.MAIN, current=True, display_name="メイン")
DETAIL = BundleKind("detail", ".detail.md", Role.ATTACHMENT, current=False, display_name="詳細")
BUGS = BundleKind("bugs", ".bugs.md", Role.ATTACHMENT, current=True, display_name="バグ")
PLAN_REVIEW = BundleKind(
    "plan-review", ".plan-review.tsv", Role.ATTACHMENT, current=False, display_name="計画レビュー指摘管理表", review_table=True
)
EXEC_REVIEW = BundleKind(
    "exec-review", ".exec-review.tsv", Role.ATTACHMENT, current=True, display_name="実行レビュー指摘管理表", review_table=True
)
WI_COMMITS = BundleKind("wi-commits", ".wi-commits.jsonl", Role.ATTACHMENT, current=True)
OWNER_RECORD = BundleKind("owner", ".owner.json", Role.ATTACHMENT, current=True, stored=False)
HANDOFF = BundleKind("handoff", ".handoff.md", Role.STANDALONE, current=True)
LEGACY_REVIEW = BundleKind("review", ".review.md", Role.ATTACHMENT, current=False)
WORKAROUND_CHECK = BundleKind("workaround-check", "-workaround-check.md", Role.ATTACHMENT, current=False)
CODEX_LOG = BundleKind("codex-log", ".codex.log", Role.ATTACHMENT, current=False)

ALL_KINDS: tuple[BundleKind, ...] = (
    MAIN,
    DETAIL,
    BUGS,
    PLAN_REVIEW,
    EXEC_REVIEW,
    WI_COMMITS,
    OWNER_RECORD,
    HANDOFF,
    LEGACY_REVIEW,
    WORKAROUND_CHECK,
    CODEX_LOG,
)
"""全種別。"""

LINKED_ATTACHMENTS: tuple[BundleKind, ...] = tuple(
    kind for kind in ALL_KINDS if kind.role is Role.ATTACHMENT and kind.display_name
)
"""計画ファイル画面がメイン計画と相互にリンクする付属ファイルの種別。並びはリンクの表示順とする。"""

STORED_ATTACHMENTS: tuple[BundleKind, ...] = tuple(
    kind for kind in ALL_KINDS if kind.role is Role.ATTACHMENT and kind.current and kind.stored
)
"""現行形式の計画バンドルとしてメイン計画と一緒に取得・保存する付属ファイルの種別。"""

REVIEW_TABLES: tuple[BundleKind, ...] = tuple(kind for kind in ALL_KINDS if kind.review_table)
"""レビュー指摘管理表の種別。"""

_VIEWABLE_EXTENSIONS = (".md", ".tsv")

# 付属ファイルの接尾辞はメイン計画の`.md`で終わるものがあるため、接尾辞の長い種別から順に一致を調べる。
_MATCH_ORDER: tuple[BundleKind, ...] = tuple(
    sorted((kind for kind in ALL_KINDS if kind is not MAIN), key=lambda kind: len(kind.suffix), reverse=True)
)


def kind_of_name(name: str) -> BundleKind | None:
    """ファイル名の種別を返す。いずれの種別にも当たらないファイル名は`None`を返す。

    付属ファイルの接尾辞を持たない`.md`はメイン計画とする。
    """
    for kind in _MATCH_ORDER:
        if kind.matches(name):
            return kind
    return MAIN if MAIN.matches(name) else None


def is_main_name(name: str) -> bool:
    """ファイル名がメイン計画の名前かを返す。"""
    return kind_of_name(name) is MAIN


def main_name_of(name: str) -> str | None:
    """メイン計画か付属ファイルの名前から、同じ計画のメイン計画の名前を返す。

    メイン計画に属さない種別と、いずれの種別にも当たらない名前は`None`を返す。
    """
    kind = kind_of_name(name)
    if kind is None or kind.role is Role.STANDALONE:
        return None
    return MAIN.name_for(name.removesuffix(kind.suffix))


def is_viewable_name(name: str, *, current_only: bool = False) -> bool:
    """計画ファイル画面が本文を表示する種別の名前かを返す。

    `current_only`が真の場合は、現行形式の種別に限る。
    """
    kind = kind_of_name(name)
    if kind is None or not kind.suffix.endswith(_VIEWABLE_EXTENSIONS):
        return False
    return kind.current or not current_only


def is_listed_name(name: str, *, main_exists: bool) -> bool:
    """計画一覧へ独立した項目として載せる種別の名前かを返す。

    メイン計画とメイン計画に属さない種別は載せ、付属ファイルは載せない。
    レビュー指摘管理表は、同じstemのメイン計画が無い場合（`main_exists`が偽）だけ載せる。
    CI対応レビュー指摘管理表のように、メイン計画を持たずに単独で扱う表があるためである。
    """
    kind = kind_of_name(name)
    if kind is None:
        return False
    if kind.review_table:
        return not main_exists
    return kind.role is not Role.ATTACHMENT


def is_review_table_name(name: str) -> bool:
    """ファイル名がレビュー指摘管理表の名前かを返す。"""
    kind = kind_of_name(name)
    return kind is not None and kind.review_table
