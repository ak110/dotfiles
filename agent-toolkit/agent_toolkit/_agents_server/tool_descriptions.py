"""agents_serverのMCPツールと引数の説明文、およびその境界の標識。"""

from __future__ import annotations

import logging
from typing import Literal

from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


# MCPのスキーマとして実行ホストのsystem promptへ入る本文の境界。
_NORMATIVE_ELEMENT = AUTO_INSERTED_ELEMENT


_NORMATIVE_SOURCE = "agents-server"


KIND_MCP_INSTRUCTIONS = "mcp-instructions"


KIND_MCP_TOOL = "mcp-tool"


_KIND_MCP_PARAMETER = "mcp-parameter"


def schema_text(body: str, *, kind: str) -> str:
    """MCPのスキーマへ載る本文へ、生成主体と種別を示す境界を付ける。

    サーバーの説明文とツールの説明文は、呼び出し側のホストがsystem promptへ自動的に載せる。
    受信したエージェントが本リポジトリの生成物と判別できるよう、他の自動注入と同じ形式で囲む。
    """
    return auto_message(body, source=_NORMATIVE_SOURCE, kind=kind)


def parameter_description(body: str) -> str:
    """ツールの引数の説明文へ境界を付ける。"""
    return schema_text(body, kind=_KIND_MCP_PARAMETER)


def shell_prompt(command: str, summary_policy: str) -> str:
    """コマンドと要約方針を、シェル実行委譲先への指示本文へ組み立てる。"""
    return f"次のコマンドを実行し、結果を報告せよ。\n\n実行するコマンド:\n{command}\n\n要約方針:\n{summary_policy}"


StartMode = Literal["task", "delegate", "explore", "write", "shell"]
"""`start`の`mode`が受理する値。`tool_names.START_MODES`と同じ集合を保つ。"""


# 各引数の説明は、ツールの入力と出力の決まり（意味、書式、省略時の動作、modeごとの必須・禁止）だけを書く。
# 委譲するか、どのmodeと工程を選ぶかの方針は、常に文脈へ入るこの説明へ置かない。
CWD_DESCRIPTION = parameter_description(
    "通常起動の全modeで必須。handoff_from指定時は省略する。委譲先の作業ディレクトリで、shellではコマンドを実行するディレクトリとなる。既存ディレクトリの絶対パスを渡す。"
)


HANDOFF_FROM_DESCRIPTION = parameter_description(
    "終了した前任のsession_id。model_typeに後任のモデル候補列を任意指定できる。他の引数は併用しない。"
    "指定なしでは前任の工程の現在設定を使い、用途・cwd・label・起動本文と進捗を復元して新しい会話の後任を起動する。"
    "可用性失敗の前任候補は保持期限にかかわらず除外する。実行中の前任は終了を待つかkillしてから指定する。"
    "応答のsession_idは後任であり、atk agents waitの待機対象になる。"
    "復元したcwdとmodel_typeも応答へ返す。"
)


MODE_DESCRIPTION = parameter_description(
    "通常起動の入力の形と起動条件を選ぶ。省略時は`task`。handoff_from指定時は省略する。"
    "`task`は`<役割名>.subagent.md`の定型作業、`delegate`は自由本文の委譲、`explore`は読み取り専用の調査とレビュー、"
    "`write`は確定済みの文章起草と小規模な定型書込、`shell`はコマンドの実行と結果の要約。"
    "modeごとの必須・禁止の入力は各引数の説明に従い、欠落と混在は委譲先を起動せずに拒否する。\n"
    "task: 手順、権限、検証方法、返却形式は`<役割名>.subagent.md`が定め、サーバーが起動設定・必須入力・宣言外入力を検査する。"
    "task・delegate・explore・writeでは、正常な返却の標識、待機表明、または有効な続行不能の2行を検査する。"
    "形式違反は同じsessionへ1回だけ自動継続し、再違反や継続の配送失敗では元の返却本文と"
    "error.resultContract・error.nextActionを公開する。taskとexploreは有効な続行不能も同じ枠で一度再確認する。"
    "shellは終了コードと出力を返すため、この構造検査の対象外。\n"
    "delegate: 委譲先向けの共通の手順を配送する通常起動。\n"
    "explore・write・shell: 軽量起動。委譲先へ届くのは依頼本文（shellではコマンドと要約方針）と、"
    "委譲先であることの通知、委譲先の硬い制約、modeの役割、返却の決まりだけで、委譲先向けの共通の手順は届かない。"
    "ClaudeとCodexの委譲先は作業ディレクトリのプロジェクト規範（`AGENTS.md`など）も読み込まず、"
    "スキルを使える保証も無い（Claude Codeの委譲先ではスキルを起動できない）。\n"
    "explore: 割当てた探索は長い出力を分割して補い、必要な確認まで行う。"
    "対象成果物・委譲入力・ユーザーデータを作成・変更・削除せず、成果ファイルも出力しない。"
    "読取ツールが管理するキャッシュ・状態・保存出力はこの対象外。"
    "読取手段（Gitの読取照会、rg・find、ファイル読取、読取MCP）で調べ、環境作成・権限拡大・再委譲はしない。"
    "許可された代替手段を試しても外部の前提が足りない単位だけ、その手段・結果・再開条件を返す。"
    "委譲元の文脈へは結果の要約だけが入る。\n"
    "write: 委譲先はファイルの読取・検索・作成・編集だけを行う。\n"
    "shell: 読み取り専用の制約は課さないため、対象を変更するコマンドも実行する。委譲元の文脈へは終了状態と要約だけが入る。"
)


SUBAGENT_MD_PATH_DESCRIPTION = parameter_description(
    "taskで必須、他のmodeでは指定しない。委譲先の手順と返却契約を保持する`<役割名>.subagent.md`の役割名（例: `add-wi`）。"
    "役割名はサーバー自身のplugin rootの`<役割名>.subagent.md`へ解決する。"
    "別のplugin rootの文書を指定する場合はagent-toolkitの`<役割名>.subagent.md`の絶対パスを渡す。"
)


EXTRA_PARAMS_DESCRIPTION = parameter_description(
    "taskだけで受理し、他のmodeでは指定しない。省略時は入力なしとして扱う。"
    "役割文書が宣言する入力名をキー、実行時の値を文字列で渡す。"
    "サーバーが宣言された入力名と必須値を確認し、必須入力の欠落と宣言外の入力名を含む場合は委譲先を起動しない。"
    "待機と再開の方針はサーバーが委譲先へ配送する。"
    "必須入力の`引き継ぎ記録先`を省略した場合は、サーバーが委譲元のセッションのmanaged-tempの直下に`（新規）`の記録先を用意し、"
    "その絶対パスを応答の`handoff_record_path`で返す。継続する担当へは、その値へ`（継続）`を付けて渡す。"
    "通常taskで役割文書が記録先を宣言しない場合も、サーバーが共通入力として専用記録先を加える。軽量起動では生成しない。"
)


PROMPT_DESCRIPTION = parameter_description(
    "delegate・explore・writeで必須、taskとshellでは指定しない。委譲先へ渡す依頼本文。"
    "delegateでは本文を保ち、記録先の行が無い場合にサーバーが専用の引き継ぎ記録先を加える。"
    "本文に記録先の行がある場合はその絶対パスを使い、同じパスをhandoff_record_pathで返す。"
    "explore・writeの委譲先へは、この本文のほかに委譲先であることの通知、委譲先の硬い制約、modeの役割、返却の決まりだけが届く。"
    "`<役割名>.subagent.md`を持つ定型作業はtaskで起動する。"
)


WHOLE_CONTRACT_PATH_DESCRIPTION = parameter_description(
    "task・delegateで受理し、自由本文のexplore・write・shellとhandoff_fromでは指定しない。"
    "担当へ参照を配送する全体作業契約の記録の絶対パス。"
    "役割指定taskでは内部の起動設定を判別せず渡せ、軽量役割のtaskは指定値を参照せず、契約本文・専用記録を追加しない。"
    "初回を含む通常起動では、呼出側が保持する現行の全体作業契約の絶対パスを指定する。"
    "省略時は互換のためサーバーが、直接の委譲元が受領済みの参照、委譲元の会話のmanaged-tempの`work-contract.md`の順に解決する。"
    "委譲先がさらに委譲する場合も受領済みの参照を継承する。"
    "明示したパスは自動解決より優先し、不在・ディレクトリ・UTF-8で読めないパスは起動前に拒否する。"
    "既知の現行契約と自動解決が別の記録を指す場合にも指定する。"
    "期待パスの記録が未作成で拒否されたときは、指定せずに期待パスへ記録を作成して再発行する。"
)


COMMAND_DESCRIPTION = parameter_description("shellで必須、他のmodeでは指定しない。委譲先がシェルで実行するコマンド。")


SUMMARY_POLICY_DESCRIPTION = parameter_description(
    "shellで必須、他のmodeでは指定しない。結果の要約方針。報告へ含める値と粒度を書く。"
)


LABEL_DESCRIPTION = parameter_description(
    "そのsessionを人が識別する短い名前。`show`・`atk agents list`・statuslineへ現れる。"
    "`<…1〜2語>`は英小文字・数字・日本語の語をハイフンで連結した1〜2語とし、依頼本文や文章をそのまま使わない。"
    "modeごとの形式と省略時の値は次のとおり。"
    "taskは省略してよく、`extra_params`に`レーン識別子`があれば`<レーン識別子>-<役割名>`、"
    "無ければ`<役割名>`を生成する"
    "（役割名はファイル名から`.subagent.md`を除いた名前。例: `lane-01-exec`、`pick-wi`）。"
    "delegateは役割を表す短い語（例: `audit`）とし、省略時は依頼本文の先頭にある空でない1行を正規化した値を使う。"
    "exploreは`explore-<調査対象を示す1〜2語>`（例: `explore-pyfltr`）、"
    "writeは`write-<起草対象を示す1〜2語>`（例: `write-awi`）、"
    "shellは`shell-<コマンド名など1〜2語>`（例: `shell-make-test`）とする。"
    "レビューを目的とするdelegateとexploreは`<レビュー対象を表す語>-review`（例: `pr-body-review`）とする。"
    "explore・write・shellの省略時はそれぞれ`explore`、`write`、`shell-<コマンドの最初の語のbasename>`を使う。"
    "labelが`-review`で終わるsessionの完了結果には、指摘の採否を確定する手順を示す`next_action`が付く。"
    "`start`と`send_message`の応答は、そのsessionが保持する担当名（省略時は生成した値）を`label`で返す。"
)


SESSION_ID_DESCRIPTION = parameter_description(
    "対象sessionの識別子。`start`の応答または`list`が返した`session_id`をそのまま渡す。"
)


MODEL_TYPE_DESCRIPTION = parameter_description(
    "委譲先のモデルを選ぶ。工程名（`execute`・`review`・`review_fast`・`chores`・`chores_fast`・`orchestrate`・`write`）か、"
    "ASCIIカンマ区切りの`<claude|codex|agy>:<model>[/<effort>]`か`opencode:<provider>/<model>[@<effort>]`の候補列"
    "（例: `agy:gemini-3.8-flash/medium,claude:opus[1m]/medium`）を指定する。"
    "工程名は対応する`<工程>_model`の候補列を使う。"
    "候補は先頭から試し、起動できない候補を除いて次の候補へ切り替える。"
    "agyの委譲にはGemini APIキー方式の明示選択・APIキー・対応版が必要で、個人ログインへ切り替えない。"
    "delegateでは必須。他のmodeでは省略してよく、省略時はtaskが`<役割名>.subagent.md`に対応する工程別設定、"
    "exploreとshellが`chores_fast`、writeが`write`の設定を使う。"
    "handoff_fromでの省略は前任の候補を継承し、現在設定へ戻すには元の工程名を明示する。"
    "自由本文のdelegate・explore・write・shellで`execute`か`orchestrate`を指定すると、委譲先を起動せずに拒否する。"
    "taskで役割文書の工程と異なる値を指定した場合と、候補列を直接指定した場合は、応答の`warnings`へ示す。"
    "指定した値はそのsessionだけに使い、恒常的な変更は`atk config set`で行う。"
)


# modeごとの必須の入力と受理する入力。`model_type`と`label`は全modeで受理するため含めない。
START_MODE_INPUTS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "task": (("subagent_md_path",), frozenset({"subagent_md_path", "extra_params", "whole_contract_path"})),
    "delegate": (("prompt", "model_type"), frozenset({"prompt", "whole_contract_path"})),
    "explore": (("prompt",), frozenset({"prompt"})),
    "write": (("prompt",), frozenset({"prompt"})),
    "shell": (("command", "summary_policy"), frozenset({"command", "summary_policy"})),
}


# 登録した自由本文のmodeには、対象外の理由を宣言しない限り返却形式を適用する。
START_RESULT_CONTRACT_EXEMPTIONS = {"shell": "コマンドの終了コードと保存出力で検収する"}
FREEFORM_RESULT_CONTRACT_KINDS = START_MODE_INPUTS.keys() - START_RESULT_CONTRACT_EXEMPTIONS.keys() - {"task"}


START_NORMAL_TASK_EXAMPLE = (
    '`{"cwd": "<絶対パス>", "subagent_md_path": "<通常起動の役割名>", "extra_params": {...}, '
    '"whole_contract_path": "<全体作業契約の絶対パス>"}`'
)
"""全体作業契約を配送する通常taskの公開例。"""


START_LIGHTWEIGHT_TASK_EXAMPLE = (
    '`{"cwd": "<絶対パス>", "subagent_md_path": "<軽量起動の役割名>", "extra_params": {...}, '
    '"whole_contract_path": "<全体作業契約の絶対パス>"}`'
)
"""全体作業契約の指定を受理し、参照を省く軽量taskの公開例。"""


START_MODE_EXAMPLES = {
    "task": START_LIGHTWEIGHT_TASK_EXAMPLE,
    "delegate": (
        '`{"cwd": "<絶対パス>", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "chores", '
        '"whole_contract_path": "<全体作業契約の絶対パス>"}`'
    ),
    "explore": '`{"cwd": "<絶対パス>", "mode": "explore", "prompt": "<質問と調べる範囲>", "label": "explore-<対象>"}`',
    "write": '`{"cwd": "<絶対パス>", "mode": "write", "prompt": "<起草の依頼>", "label": "write-<対象>"}`',
    "shell": (
        '`{"cwd": "<絶対パス>", "mode": "shell", "command": "<コマンド>", "summary_policy": "<要約方針>", '
        '"label": "shell-<コマンド名>"}`'
    ),
}


# Claude Codeは初期送信のツール説明とserver instructionsを、設定を変えない状態で各4,096文字に切り詰める
# （ツール検索で後から読む説明は16,384文字）。初期送信の上限内に収め、観測と結果受領の手順を先頭に置く。
# modeごとの入力と委譲先へ届く内容は`mode`の引数説明へ置く。
START_DESCRIPTION = "\n".join(
    (
        "委譲先sessionを開始し、`mode`で入力と起動条件を選ぶ。"
        '引き継ぎ起動は`{"handoff_from": "<終了した前任session_id>"}`を渡し、'
        "後任のモデル候補を指定する場合だけmodel_typeを加える。他の通常起動入力との混在は拒否する。",
        "返した`session_id`の終了と結果本文は、引数なしの単独コマンド`atk agents wait`で受け取る。"
        "waitは`session_id`を引数に取らず、登録済みの全sessionの終了を待つ。結果が不要なら`kill`で破棄する。",
        "",
        "| mode | 用途 | 標準の入力 | 起動条件と`model_type`省略時の設定 |",
        "| --- | --- | --- | --- |",
        "| `task`（省略時） | `<役割名>.subagent.md`を持つ定型作業 | "
        "`subagent_md_path`・`extra_params`・`whole_contract_path` | `<役割名>.subagent.md`の`mode:`、対応する工程別設定 |",
        "| `delegate` | 自由本文の単発作業 | `prompt`・`model_type`・`whole_contract_path` | 通常起動、省略不可 |",
        "| `explore` | 読み取り専用の調査とレビュー | `prompt` | 軽量起動、`chores_fast` |",
        "| `write` | 確定した文章の起草と小規模な定型書込 | `prompt` | 軽量起動、`write` |",
        "| `shell` | コマンド実行と結果の要約 | `command`・`summary_policy` | 軽量起動、`chores_fast` |",
        "",
        "通常起動の委譲先へは委譲先向けの共通の手順が届く。"
        "OpenCodeへは他のbackendと同じ起動の本文と`~/.claude/rules/`のユーザー規範が全modeで届き、"
        "スキルは自動登録せず配送したplugin rootの`skills/`から明示読取する。"
        "Antigravityは登録済みプラグインからスキル・役割とMCPを受け取る。"
        "軽量起動の委譲先へ届く内容とmodeごとの制約は`mode`と各引数の説明にある。"
        "必須入力の欠落、mode外の入力とtaskの宣言外の入力名は起動せず拒否し、該当・受理する入力と呼び出し方を返す。",
        "",
        "例（`cwd`は全modeで必須）:",
        f"- 通常task: {START_NORMAL_TASK_EXAMPLE}",
        f"- 軽量task: {START_LIGHTWEIGHT_TASK_EXAMPLE}。役割内部の起動設定の判別は不要。",
        '- delegate: `{"cwd": "/repo", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "chores", '
        '"whole_contract_path": "/abs/work-contract.md"}`',
        '- explore: `{"cwd": "/repo", "mode": "explore", "prompt": "<質問と範囲>"}`',
        '- write: `{"cwd": "/repo", "mode": "write", "prompt": "<確定した書込依頼>"}`',
        '- shell: `{"cwd": "/repo", "mode": "shell", "command": "make test", '
        '"summary_policy": "終了コードと失敗したテスト名"}`',
        "",
        "Claude Codeのメイン初回`start`では、modが定期再確認のtaskを`CronCreate`で装着する。"
        "装着の成功は通知せず、装着できなかった場合だけ理由を示す警告をこの結果へ添える。"
        "同じmodはバックグラウンドタスクを残したターンの終了時にも装着するため、modが動く環境では手動で先に作らない。",
        "",
        "応答は`session_id`・`status`・担当名の`label`と、保持時の`root_session_id`。"
        "起動不能候補を除外して次を試す。切替時だけ除外候補と根拠、採用した`engine`・`model`・`effort`を加える。"
        "この自動切替は起動時だけに行う。起動後の可用性失敗は終了時の結果のnext_actionに従いhandoff_from付きで起動する。"
        "全候補が可用性の失敗かagyのturn失敗で終了すれば最後の終了応答を返す。"
        "最後のagy候補のbackend開始例外は除外理由付きの例外で返す。起動条件の詳細は`show`で取得する。"
        "開始後の登録・保存が失敗した場合は`persistence_warnings`と、同じ`session_id`で続ける`next_action`を加える。",
    )
)
