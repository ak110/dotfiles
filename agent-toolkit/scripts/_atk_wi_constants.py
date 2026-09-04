"""ワークアイテムの保存状態と項目種別の値を定義する共有モジュール。

`_atk_wi_common`と`_uwi_scan`の双方が本モジュールをimportする。
両者は依存関係を持つため、状態集合をどちらかへ置くと循環importになる。
本モジュールは他の配布物モジュールへ依存せず、状態集合の唯一の定義箇所とする。
"""

WI_STATE_INBOX = "inbox"
"""次の処理主体による取得待ち。"""

WI_STATE_PROCESSING = "processing"
"""`start-processing`で処理中に移動された途中状態。"""

WI_STATE_HOLD = "hold"
"""ユーザーまたはエージェントが編集中であり、自動処理の対象外。"""

WI_STATE_ADOPTED = "adopted"
"""採用として最終処理された状態。"""

WI_STATE_REJECTED = "rejected"
"""不採用として最終処理された状態。"""

WI_STATES = (
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_STATE_HOLD,
    WI_STATE_ADOPTED,
    WI_STATE_REJECTED,
)
"""管理repoのroot直下に置く状態フォルダー名の全体。"""

WI_ACTIVE_STATES = (WI_STATE_INBOX, WI_STATE_PROCESSING, WI_STATE_HOLD)
"""未終端の項目を表示する一覧集合。項目の種別によらず同じ集合とする。"""

WI_PROCESSABLE_STATES = (WI_STATE_INBOX, WI_STATE_PROCESSING)
"""自動処理へ渡せる一覧集合。着手可否は別途判定する。"""

TRANSITION_EXPLICIT_STATES = {
    "start-processing": (WI_STATE_HOLD,),
    "return-to-inbox": (WI_STATE_REJECTED,),
    "adopt": (WI_STATE_HOLD,),
    "reject": (WI_STATE_INBOX, WI_STATE_HOLD),
    "remove": (
        WI_STATE_INBOX,
        WI_STATE_PROCESSING,
        WI_STATE_HOLD,
        WI_STATE_ADOPTED,
        WI_STATE_REJECTED,
    ),
}
"""操作ごとに明示`state`として受理する遷移元の状態。

暗黙解決（`inbox`・`processing`）は各操作の既定として別に扱い、本表は明示指定だけを統治する。
`hold`は自動処理からの除外だけを意味し、保留操作以外の操作を妨げないため各操作の遷移元へ含める。
`remove`は終端状態（`adopted`・`rejected`）も受理し、状態を戻さずに削除できる。
"""

WI_TYPE_AWI = "awi"
"""frontmatterの`type`がエージェントワークアイテムであることを示す値。"""

WI_TYPE_UWI = "uwi"
"""frontmatterの`type`がユーザーワークアイテムであることを示す値。"""

WI_TYPES = (WI_TYPE_AWI, WI_TYPE_UWI)
"""frontmatterの`type`が取り得る値の全体。"""

LEGACY_WI_TYPES = {"feedback": WI_TYPE_AWI, "tbd": WI_TYPE_UWI}
"""`atk wi migrate`の実行前に保存された`type`値と、現行の値の対応。

配布は移行より先に届くため、移行前のprivate-notesが旧値のまま残る期間がある。
読み取り経路だけが本表を参照し、書き込み経路は常に現行の値を保存する。
`atk wi migrate`が保存値を変換した後は、本表に一致する値が残らない。
"""


def normalized_wi_type(value: object) -> str | None:
    """保存された`type`値を現行の値へ正規化する。

    現行の値と旧値のいずれでもない場合と、文字列でない場合は`None`を返す。
    """
    if not isinstance(value, str) or not value:
        return None
    if value in WI_TYPES:
        return value
    return LEGACY_WI_TYPES.get(value)
