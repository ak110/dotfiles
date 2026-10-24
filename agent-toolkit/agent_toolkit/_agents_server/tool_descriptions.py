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


EXPLORE_COMMON_CAPABILITIES = (
    "explore: 対象成果物は読み取り専用。大きな検索結果は条件の絞込みか分割取得で必要範囲を最後まで読み、"
    "省略や未読を不在・件数・網羅性の根拠にしない。"
    "完成条件から全文読取と所在探索を分け、割当範囲の未読・未走査は担当内で完了する。"
    "切り詰め・保存出力はread-fileのnextからeofまで回収する。"
    "権限・到達不能・入力不足など自身で解消できない未確認だけを範囲と理由付きで返す。環境作成・権限拡大・再委譲は禁止。"
    "確定した操作はshell、自由な判断を伴う書込はdelegateへ渡す。"
    "shellとdelegateは書込を拒否せず、対象と許可する操作は委譲元が定める。"
)
"""公開説明と全backendの探索プロンプトが共有する成果契約。"""


EXPLORE_BACKEND_CAPABILITIES = {
    engine: (
        "全backendでGitの読取照会、本文・属性検索、Python標準ライブラリの分割取得、"
        "atk read-fileなどの読取CLIとpyfltr grepなどの読取MCPを使える。"
        "read-file -- <絶対パス>で開始し、先頭JSONのnextをread-file --start <next> -- <同じ絶対パス>へ渡し、"
        "eof: trueまで本文を改行ごと保持する。位置は文字単位。--max-bytesはメタデータ込みのUTF-8バイト予算。"
        "Codexでは内側コマンドと外側セルの両方へ収め、合計量が不明な全文取得は別セルに分ける。"
        "対象成果物・委譲入力・ユーザーデータの作成・変更・削除は禁止。"
        "実行環境が管理するキャッシュ・状態・保存出力はこの変更禁止対象と区別する。"
    )
    for engine in ("claude", "codex", "agy", "opencode")
}
"""各backendの起動へ選択して配送する調査手段。"""


EXPLORE_CAPABILITIES = EXPLORE_COMMON_CAPABILITIES + EXPLORE_BACKEND_CAPABILITIES["codex"]
"""起動前の公開schemaへ配送する全backendの能力説明。"""


DELEGATION_INPUT_GUIDANCE = (
    "入力は委譲先が他から得られない目的・要件・制約・完了条件、ユーザー発話の逐語と出所、確認済み事実と実行時の値に限定し、"
    "参照できる記録は絶対パスと対象IDで示す。必須入力とユーザー指定の手段を保つ。"
    "調査途中の仮説、自ら選んだ実装手順やcommit件名は足さない。"
    "手段の指定はユーザー指定か、要件達成に必要と確定した根拠を伴う場合に限る。"
    "軽量起動やAntigravityへ届かない規範のうち作業に必要な指示は補う。"
    "判定の定義はdelegation/references/base-contract.md「入力の適格性と追送」に従う。"
)
"""開始と追送の公開説明で共有する入力の適格性の要約。"""


LIGHTWEIGHT_MODE_SELECTION = (
    "委譲の要否は独立した文脈の必要性、段位と役割契約、起動・自動配送と明示読取・"
    "委譲元の受領レビュー・統合の総費用で選ぶ。本文量だけで分割しない。"
    "同じ担当の連続した作業はまとめ、独立した要件と独立文脈レビューは分ける。"
    "全体点検は公開操作・共通定義・呼出元と必要本文量から範囲を定め、既存の列挙・参照を再利用し、不明なら所在と接続を先に探す。"
    "委譲元が全対象を保持し、独立経路群を欠落なく割り当て、完成条件・根拠の所在・担当内の完了範囲を渡す。"
    "文書群は単一応答の上限と全体の読取量を別に評価し、分割取得する単一担当と独立群への分担を選ぶ。"
    "explore・shell・writeは自己完結した1回の入力と返却に使い、追加対話と要求の再構成を要する作業は通常taskかdelegateへ渡す。"
    "既存の担当が詳しい文脈を持つ調査と判断は、新しく本文を読む前にその担当へ戻す。"
    "exploreは結論と所在で後続の判断が成立する調査、shellは終了状態と要約で後続の判断が成立する実行へ使う。\n"
    "工程: execute担当は作業を判断部分と、読取・調査・要約・走査・確定済みの書込・コマンド実行の部分へ極力細分化し、"
    "後者を定型走査・事実収集・コマンド実行はchores_fast、中規模の読取・要約・走査と内容の確定した書込作業はchoresへ委ねる"
    "（自身で行う方が総費用で安い小さな作業を除く）。"
    "委譲先へexecuteを指定するのは、計画・設計判断・原因分析など分割できない判断を独立した担当へ任せる場合に限る。"
    "観点の選定と返った結果の判定・統合は委譲元のexecute担当自身が行う。"
)
"""server instructionsとmodeの説明が共有する、コンテキストと費用の節約の目安。"""


# 各引数の説明は、委譲元がサーバーの`instructions`や外部のエージェント向け文書を読まずに、
# 意味、書式、省略時の動作およびmodeごとの必須・禁止を判断できる内容にする。
CWD_DESCRIPTION = parameter_description(
    "通常起動の全modeで必須。handoff_from指定時は省略する。委譲先の作業ディレクトリで、shellではコマンドを実行するディレクトリとなる。既存ディレクトリの絶対パスを渡す。"
)


HANDOFF_FROM_DESCRIPTION = parameter_description(
    "終端した前任のsession_id。指定時はこの引数だけを渡す。mode・model_type・用途・cwd・label・起動本文と進捗を復元して後任を起動する。"
    "可用性失敗の前任候補は保持期限にかかわらず除外する。実行中の前任は終端を待つかkillしてから指定する。"
    "応答のsession_idは後任であり、atk agents waitの待機対象になる。"
    "復元したcwdとmodel_typeも応答へ返す。"
)


MODE_DESCRIPTION = parameter_description(
    "通常起動の入力の形と起動条件を選ぶ。省略時は`task`。handoff_from指定時は省略する。"
    "`task`は`<役割名>.subagent.md`の定型作業、`delegate`は自由本文の通常委譲、`explore`は読み取り専用の調査とレビュー、"
    "`write`は確定済みの文章起草と小規模な定型書込、`shell`はコマンドの実行と結果の要約に使う。"
    "modeごとの必須・禁止の入力は各引数の説明に従い、欠落と混在は委譲先を起動せずに拒否する。\n"
    "選び方: `<役割名>.subagent.md`がある作業はtaskで渡す。"
    "手順、権限、検証方法、返却形式は同ファイルが定めるため、委譲プロンプトへ書き足さない。"
    "委譲元は呼び元の起動・受領契約から入力名と実行時の値を確定して`extra_params`へ渡す。"
    "起動設定・必須入力・宣言外入力の機械検査はサーバーが行う。定義本文の読取は委譲先自身の手順、編集・調査と返却後の検収で行う。"
    "explore・write・shellの委譲先へは委譲先向けの規範（`share/rules-subagent.md`）が配送されず、"
    "ClaudeとCodexの委譲先では作業ディレクトリのプロジェクト規範（`AGENTS.md`など）も読み込まれず、"
    "スキルを使える保証も無い（Claude Codeの委譲先ではスキルを起動できない）。作業に必要な指示は全て`prompt`へ書く。"
    "読み取り専用、応答言語、担当の範囲は各modeの`share/agents-server-*.md`が既に定めるため書かない。"
    "スキルの手順を要する作業にはtaskかdelegateを使う。\n" + LIGHTWEIGHT_MODE_SELECTION + "\nexplore: "
    "対象成果物・委譲入力・ユーザーデータの作成、変更および削除を禁止し、成果ファイルの出力を依頼しない。"
    "読取ツールが管理するキャッシュ・状態・保存出力はこの対象と区別する。"
    "委譲元の文脈へは結果の要約だけが入る。"
    "exploreはmodel_typeを省略したchores_fastを基本とし、軽量側では判断材料が不足する読取・要約にはchoresを指定する。"
    "現状調査、全文読取と要約、所在・値・件数の確認、該当候補の抽出と非該当の判定をexecuteで起動しない。"
    "多い対象は重複のない群へ分けて並列に委ねる。"
    "観点・判定基準の選定と、返った該当候補・判定保留の対象の判定は委譲元のexecute担当自身が行う。"
    "判断を含む依頼も読取・走査の部分を切り出して軽量側へ渡し、全体をexecuteにしない。\n" + EXPLORE_CAPABILITIES + "\n"
    "write: 設計、調査、レビューおよび公開操作を依頼せず、成果物種別、読者、事実、根拠、反映先と完成形を`prompt`へ明記する。"
    "読者が異なる文章は別の依頼にする。委譲先はファイルの読取・検索・作成・編集だけを行う。\n"
    "shell: 読み取り専用の制約は課さないため、対象を変更する自動チェックも渡せる。委譲元の文脈へは終了状態と要約だけが入る。"
)


SUBAGENT_MD_PATH_DESCRIPTION = parameter_description(
    "taskで必須、他のmodeでは指定しない。委譲先の手順と返却契約を保持する`<役割名>.subagent.md`の役割名（例: `add-wi`）。"
    "役割名はサーバー自身のplugin rootの`share/<役割名>.subagent.md`へ解決する。"
    "別のplugin rootの文書を指定する場合はagent-toolkitの`share/*.subagent.md`の絶対パスを渡す。"
)


EXTRA_PARAMS_DESCRIPTION = parameter_description(
    "taskだけで受理し、他のmodeでは指定しない。省略時は入力なしとして扱う。"
    "呼び元の起動・受領契約が示す入力名をキー、実行時の値を文字列で渡す。"
    "サーバーが役割文書で宣言された入力名と必須値を確認するため、起動準備のための定義本文の読取は不要。"
    "待機と再開の方針はサーバーが伝えるため、委譲プロンプトへ書き足さない。"
    "必須入力の欠落と宣言外の入力名を含む場合は委譲先を起動しない。"
    "ただし必須入力の`引き継ぎ記録先`を省略した場合は、サーバーが委譲元のセッションのmanaged-tempの直下に`（新規）`の記録先を用意し、"
    "その絶対パスを応答の`handoff_record_path`で返す。継続する担当へは、その値へ`（継続）`を付けて渡す。"
    "通常taskで役割文書が記録先を宣言しない場合も、サーバーが共通入力として専用記録先を加える。軽量起動では生成しない。"
    + DELEGATION_INPUT_GUIDANCE
)


PROMPT_DESCRIPTION = parameter_description(
    "delegate・explore・writeで必須、taskとshellでは指定しない。委譲先へ渡す依頼本文。"
    "delegateでは本文を保ち、サーバーが専用の引き継ぎ記録先を加え、同じパスをhandoff_record_pathで返す。"
    "explore・writeの委譲先は委譲先向けの規範（`share/rules-subagent.md`）を受け取らず、"
    "ClaudeとCodexでは作業ディレクトリのプロジェクト規範も読まないため、作業に必要な指示を全て書く。"
    "`<役割名>.subagent.md`を指す本文は渡さず、taskで起動する。" + DELEGATION_INPUT_GUIDANCE
)


WHOLE_CONTRACT_PATH_DESCRIPTION = parameter_description(
    "task・delegateの通常起動だけで受理し、軽量起動（explore・write・shellと`mode:`が軽量の役割）とhandoff_fromでは指定しない。"
    "担当へ参照を配送する全体作業契約（`share/work-contract.md`の記録）の絶対パス。"
    "初回を含む通常起動では、呼出側が保持する現行の全体作業契約の絶対パスを指定する。"
    "省略時は互換のためサーバーが、直接の委譲元が受領済みの参照、委譲元の会話のmanaged-tempの`work-contract.md`の順に解決する。"
    "委譲先がさらに委譲する場合も受領済みの参照を継承する。"
    "明示したパスは自動解決より優先し、既知の現行契約と自動解決が別の記録を指す場合にも指定する。"
    "期待パスの記録が未作成で拒否されたときは、指定せずに期待パスへ記録を作成して再発行する。"
    "指定時は自動解決より優先し、不在・ディレクトリ・UTF-8で読めないパスは起動前に拒否する。"
)


COMMAND_DESCRIPTION = parameter_description("shellで必須、他のmodeでは指定しない。委譲先がシェルで実行するコマンド。")


SUMMARY_POLICY_DESCRIPTION = parameter_description(
    "shellで必須、他のmodeでは指定しない。結果の要約方針。報告へ含める値と粒度を書く。" + DELEGATION_INPUT_GUIDANCE
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
    "explore・write・shellは同じ種類のsessionを区別できるよう明示する。"
    "省略時はそれぞれ`explore`、`write`、`shell-<コマンドの最初の語のbasename>`を使う。"
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
    "候補は先頭から試し、起動できない候補を除いて次の候補へ切り替える。"
    "delegateでは必須。他のmodeでは省略してよく、省略時はtaskが`<役割名>.subagent.md`に対応する工程別設定、"
    "exploreとshellが`chores_fast`、writeが`write`の設定を使う。"
    "作業は判断部分とそれ以外へ極力細分化し、定型走査・事実収集・コマンド実行は`chores_fast`（exploreの省略時）、"
    "中規模の読取・要約・走査と、計画・設計判断で内容の確定した書込作業（単発のコーディング、確定済みの修正など）は`chores`へ渡す。"
    "委譲先へ`execute`を指定するのは、計画・設計判断・原因分析など分割できない判断を独立した担当（工程担当のdelegateなど）へ"
    "任せる場合に限る。観点・判定基準の選定と、返った結果の判定・統合は委譲元のexecute担当自身が行う。"
    "工程名の候補は、対応する`<工程>_model`の候補列を直接使う。"
    "`orchestrate`はprocess-loop・run-skillなど最上位セッションのorchestrate_modelを使う。"
    "WI投入担当のadd-wiは専念型・付随型ともmodel_typeを省略し、役割に定めたexecute工程を使う。"
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


START_NORMAL_TASK_EXAMPLE = (
    '`{"cwd": "<絶対パス>", "subagent_md_path": "<通常起動の役割名>", "extra_params": {...}, '
    '"whole_contract_path": "<全体作業契約の絶対パス>"}`'
)
"""全体作業契約を配送する通常taskの公開例。"""


START_LIGHTWEIGHT_TASK_EXAMPLE = '`{"cwd": "<絶対パス>", "subagent_md_path": "<軽量起動の役割名>", "extra_params": {...}}`'
"""全体作業契約を受理しない軽量taskの公開例。"""


START_MODE_EXAMPLES = {
    "task": START_LIGHTWEIGHT_TASK_EXAMPLE,
    "delegate": (
        '`{"cwd": "<絶対パス>", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "execute", '
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
# modeの選び方は`mode`の引数説明へ置く。
START_DESCRIPTION = "\n".join(
    (
        "委譲先sessionを開始し、`mode`で入力と起動条件を選ぶ。"
        '引き継ぎ起動は`{"handoff_from": "<終端した前任session_id>"}`だけを渡す。通常起動の入力との混在は拒否する。',
        DELEGATION_INPUT_GUIDANCE,
        "返した`session_id`は同じ応答で`atk agents wait`を単独実行して観測し、不要なら`kill`で破棄する。"
        "waitは`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。",
        "exploreは対象成果物への書込禁止。読取ツール管理の保存出力を使え、成果ファイル生成の調査はdelegateへ。返却は委譲元が保存する。",
        EXPLORE_CAPABILITIES,
        "",
        "| mode | 用途 | 標準の入力 | 起動条件と`model_type`省略時の設定 |",
        "| --- | --- | --- | --- |",
        "| `task`（省略時） | `share/<役割名>.subagent.md`を持つ定型作業 | `subagent_md_path`。"
        "通常起動は`whole_contract_path`も渡す | "
        "`<役割名>.subagent.md`の`mode:`、対応する工程別設定 |",
        "| `delegate` | `<役割名>.subagent.md`の無い単発作業 | `prompt`・`model_type`・`whole_contract_path` | "
        "通常起動。委譲先規範が届き、Claude・Codex・Antigravityはスキルを使える。"
        "OpenCodeへは共通規範・委譲先規範・ユーザー規範が全modeで届く。"
        "スキルは自動登録せず配送したplugin rootの`skills/`から明示読取する。"
        "プロジェクト規範のCLI読込は未確認で、必要な本文は委譲入力へ含める。"
        "Antigravityは登録済みプラグインから常時規範・スキル・役割とMCPを受け取る |",
        "| `explore` | 読み取り専用の調査とレビュー | `prompt` | 軽量起動、`chores_fast` |",
        "| `write` | 確定した文章の起草と小規模な定型書込 | `prompt` | 軽量起動、`write` |",
        "| `shell` | コマンド実行と結果の要約 | `command`・`summary_policy` | 軽量起動、`chores_fast` |",
        "",
        "選び方は`mode`、制約は各引数を参照。"
        "必須入力の欠落、mode外の入力とtaskの宣言外の入力名は起動せず拒否し、該当・受理する入力と呼び出し方を返す。",
        "",
        "例（`cwd`は全modeで必須）:",
        f"- 通常task: {START_NORMAL_TASK_EXAMPLE}",
        f"- 軽量task: {START_LIGHTWEIGHT_TASK_EXAMPLE}。`whole_contract_path`を渡さない。",
        '- delegate: `{"cwd": "/repo", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "execute", '
        '"whole_contract_path": "/abs/work-contract.md"}`',
        '- explore: `{"cwd": "/repo", "mode": "explore", "prompt": "<質問と範囲>"}`',
        '- write: `{"cwd": "/repo", "mode": "write", "prompt": "<確定した書込依頼>"}`',
        '- shell: `{"cwd": "/repo", "mode": "shell", "command": "make test", '
        '"summary_policy": "終了コードと失敗したテスト名"}`',
        "",
        "Claude Codeのメイン初回`start`でmodが定期再確認を装着する。"
        "装着失敗時と、通知もtaskも無く`CronCreate`を使えるメインが待機でターンを終える時は、"
        "`agent-toolkit:delegation`の`references/claude-code-runtime.md`「待機中の定期再確認と背景転換」に従って装着する。",
        "",
        "応答は`session_id`・`status`・担当名の`label`と、保持時の`root_session_id`。"
        "起動不能候補を除外して次を試す。切替時だけ除外候補と根拠、採用した`engine`・`model`・`effort`を加える。"
        "この自動切替は起動時だけに行う。起動後の可用性失敗は終端結果のnext_actionに従いhandoff_from付きで起動する。"
        "全候補が可用性の失敗かagyのturn失敗で終端すれば最後の終端応答を返す。"
        "最後のagy候補のbackend開始例外は除外理由付きの例外で返す。起動条件の詳細は`show`で取得する。"
        "開始後の登録・保存が失敗した場合は`persistence_warnings`と、同じ`session_id`で続ける`next_action`を加える。",
    )
)
