"""ワークアイテムの保存状態と項目種別の値を定義する共有モジュール。

`_atk_wi_common`と`_uwi_scan`の双方が本モジュールをimportする。
両者は依存関係を持つため、状態集合をどちらかへ置くと循環importになる。
本モジュールは他の配布物モジュールへ依存せず、状態集合の唯一の定義箇所とする。
"""

PROCESS_WI_GOAL_BODY = "`agent-toolkit:process-wi`を完遂してください。"
"""`atk wi process-loop`が子セッションへ渡す目的文の本体。

最初のプロンプトを組み立てる側と、セッション記録からそのセッションを判別する側が同じ値を使う。
最初のプロンプトは`atk-auto`要素で囲むため、記録側は本文の包含で判定する。
"""

OBSERVATION_RESUME_HEADING = "反映後の観測の再開記録"
"""反映後の観測だけが残るAWIへprocess-wiが追記する再開記録のH2見出し名。

書式は`agent-toolkit/skills/process-wi/references/finish-session.md`のテンプレートが定め、
pickerは最後の同名節を再開記録として読む。`atk serve`の一覧も同じ読み方で表示を決める。
"""

OBSERVATION_ONLY_RESUME_LINE = "- 再開区分: 反映後の観測だけが残る"
"""再開記録のうち、反映後の観測だけが残ることを示す行。"""

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

WI_EDITABLE_STATES = (WI_STATE_HOLD, WI_STATE_PROCESSING, WI_STATE_INBOX)
"""本文の編集と追記が受理する保存状態の唯一の定義。

並び順は対象解決の探索順を表す。
"""

WI_PROCESSABLE_STATES = (WI_STATE_INBOX, WI_STATE_PROCESSING)
"""自動処理へ渡せる一覧集合。着手可否は別途判定する。"""

WI_AGENT_REMOVABLE_STATES = (WI_STATE_INBOX, WI_STATE_HOLD)
"""エージェント環境から削除できる状態。"""

WI_USER_REMOVABLE_STATES = (
    WI_STATE_PROCESSING,
    WI_STATE_INBOX,
    WI_STATE_HOLD,
    WI_STATE_ADOPTED,
    WI_STATE_REJECTED,
)
"""ユーザー環境から暗黙解決する削除状態。先頭ほど同名項目の解決を優先する。"""

TRANSITION_EXPLICIT_STATES = {
    "start-processing": (WI_STATE_HOLD,),
    "return-to-inbox": (WI_STATE_REJECTED, WI_STATE_ADOPTED),
    "hold": (WI_STATE_PROCESSING, WI_STATE_REJECTED, WI_STATE_ADOPTED),
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

ファイル名指定の暗黙解決は各操作で指定を省いた場合の動作として別に扱い、本表は明示指定だけを統治する。
暗黙解決は多くの操作で`inbox`・`processing`を探索し、`adopt`・`reject`は`BULK_SOURCE_STATES`から導いた`inbox`・`processing`・`hold`を探索する。
`hold`は自動処理からの除外だけを意味し、保留操作以外の操作を妨げないため各操作の遷移元へ含める。
`remove`は終端状態（`adopted`・`rejected`）も受理し、状態を戻さずに削除できる。
`hold`の`processing`は、エージェント環境で処理中の自項目を保留する場合の明示指定に使う。
エージェント環境のファイル名指定は`processing`を暗黙に解決しない。
"""

BULK_SOURCE_STATES = {
    "start-processing": (WI_STATE_INBOX, WI_STATE_HOLD),
    "hold": (WI_STATE_INBOX, WI_STATE_PROCESSING, WI_STATE_REJECTED, WI_STATE_ADOPTED),
    "unhold": (WI_STATE_HOLD,),
    "return-to-inbox": (WI_STATE_PROCESSING, WI_STATE_REJECTED, WI_STATE_ADOPTED),
    "adopt": (WI_STATE_INBOX, WI_STATE_PROCESSING, WI_STATE_HOLD),
    "reject": (WI_STATE_INBOX, WI_STATE_PROCESSING, WI_STATE_HOLD),
    "remove": WI_USER_REMOVABLE_STATES,
}
"""操作ごとの遷移元状態集合。`--all`の候補はこの集合に属する項目だけとする。

各値は、個別指定時の暗黙解決が探索する状態と`TRANSITION_EXPLICIT_STATES`が受理する状態の和集合と一致する。
`remove`と`hold`は呼出主体で値が変わるため、本表は非エージェント環境の値を持ち、
エージェント環境の値は`bulk_source_states`が返す。
"""

BULK_ACTION_LABELS = {
    "start-processing": "処理開始",
    "hold": "保留",
    "unhold": "保留解除",
    "return-to-inbox": "差し戻し",
    "adopt": "採用",
    "reject": "不採用",
    "remove": "削除",
}
"""一括操作の候補0件、確認および確認後の一致判定を伝える各メッセージが使う操作名。"""


def bulk_source_states(action: str, *, actor_is_agent: bool) -> tuple[str, ...]:
    """呼出主体を加味した、その操作の遷移元状態集合を返す。"""
    if action == "remove":
        return WI_AGENT_REMOVABLE_STATES if actor_is_agent else WI_USER_REMOVABLE_STATES
    if action == "hold" and actor_is_agent:
        return tuple(state for state in BULK_SOURCE_STATES[action] if state != WI_STATE_PROCESSING)
    return BULK_SOURCE_STATES[action]


WI_TYPE_AWI = "awi"
"""frontmatterの`type`がエージェントワークアイテムであることを示す値。"""

WI_TYPE_UWI = "uwi"
"""frontmatterの`type`がユーザーワークアイテムであることを示す値。"""

WI_TYPES = (WI_TYPE_AWI, WI_TYPE_UWI)
"""frontmatterの`type`が取り得る値の全体。"""

LEGACY_WI_TYPES = {"feedback": WI_TYPE_AWI, "tbd": WI_TYPE_UWI}
"""旧形式で保存された`type`値と、現行の値の対応。

private-notesには旧値を持つ項目が残り得る。
本表は読み取り時にだけ参照する。書き込み時は常に現行の値を保存する。
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


def unrepairable_entry_next_action(name: str) -> str:
    """frontmatterの破損・必須キー欠落の項目に対する次の操作を返す。

    `atk wi edit`で非対話の編集をする場合も同じ検証を先に行って拒否するため、エージェントが直せる操作として案内しない。
    """
    return (
        f"`atk wi show {name}`で保存内容を確かめ、ユーザーへ報告する"
        "（この状態の項目は`atk wi edit`の`--body-file`で本文を渡しても同じ理由で拒否される）"
    )


QUESTION_TYPE_CHOICE = "choice"
"""UWIの回答形式のうち、`choices`の選択肢から選ぶ形式。"""

QUESTION_TYPE_YES_NO = "yes-no"
"""UWIの回答形式のうち、はい・いいえの2択の形式。"""

QUESTION_TYPE_FREE_FORM = "free-form"
"""保存済みのUWIだけが持つ自由記述の回答形式。

AskUserQuestionが選択肢を必須とし自由記述を「Other」で受ける形にそろえ、新規作成では受理しない。
選択肢に無い回答は、選択肢形式のUWIの回答欄へ書き足して受ける。
保存済みの項目の表示、回答と本文編集のためだけに読む。
"""

NEW_QUESTION_TYPES = (QUESTION_TYPE_CHOICE, QUESTION_TYPE_YES_NO)
"""UWIを新規作成するときと、回答形式を変える本文編集で受理する回答形式。"""

STORED_QUESTION_TYPES = (*NEW_QUESTION_TYPES, QUESTION_TYPE_FREE_FORM)
"""保存済みのUWIが持ち得る回答形式の全体。"""
