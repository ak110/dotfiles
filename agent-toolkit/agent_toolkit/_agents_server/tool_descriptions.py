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


EXPLORE_CAPABILITIES = (
    "explore: ClaudeはRead/Glob/Grep（Bashなし）、Codexは読取専用sandbox、Antigravity非対応。"
    "Codexでは読取用atkもuvのキャッシュ書込で起動に失敗し得る。"
    "読取・検索はcat/rg、UTF-8文字分割は配送済みPython標準ライブラリを使う。"
    "atk固有情報を確定できなければ失敗コマンド・診断・未確認事項を委譲元へ返す。環境作成・権限拡大・再委譲は禁止。"
    "Git等はmodel_typeのCodex候補、確定操作はshell、自由な判断はdelegateへ。"
    "後2者は書込拒否外、対象・許可操作は委譲元が定める。"
)
"""起動前の説明とexplore委譲先へ配送する、エンジン別の能力と調査の選び方。"""


# 各引数の説明は、委譲元がサーバーの`instructions`や外部のエージェント向け文書を読まずに、
# 意味、書式、省略時の動作およびmodeごとの必須・禁止を判断できる内容にする。
CWD_DESCRIPTION = parameter_description(
    "全modeで必須。委譲先の作業ディレクトリで、shellではコマンドを実行するディレクトリとなる。既存ディレクトリの絶対パスを渡す。"
)


MODE_DESCRIPTION = parameter_description(
    "入力の形と起動条件を選ぶ。省略時は`task`。"
    "`task`は`<役割名>.subagent.md`の定型作業、`delegate`は自由本文の通常委譲、`explore`は読み取り専用の調査とレビュー、"
    "`write`は確定済みの文章起草と小規模な定型書込、`shell`はコマンドの実行と結果の要約に使う。"
    "modeごとの必須・禁止の入力は各引数の説明に従い、欠落と混在は委譲先を起動せずに拒否する。\n"
    "選び方: `<役割名>.subagent.md`がある作業はtaskで渡す。"
    "手順、権限、検証方法、返却形式は同ファイルが定めるため、委譲プロンプトへ書き足さない。"
    "`<役割名>.subagent.md`を読み、同ファイルが宣言した入力名と`extra_params`が一致するか確かめてから起動する。"
    "explore・write・shellの委譲先へは委譲先向けの規範（`share/rules-subagent.md`）が配送されず、"
    "ClaudeとCodexの委譲先では作業ディレクトリのプロジェクト規範（`AGENTS.md`など）も読み込まれず、"
    "スキルを使える保証も無い（Claude Codeの委譲先ではスキルを起動できない）。作業に必要な指示は全て`prompt`へ書く。"
    "読み取り専用、応答言語、担当の範囲は各modeの`share/agents-server-*.md`が既に定めるため書かない。"
    "スキルの手順を要する作業にはtaskかdelegateを使う。\n"
    "explore: 読み取りが数回で確定する調査は自ら実行し、多数のファイルを横断する調査や大量の本文を読む調査を委譲する。"
    "委譲先はファイルを作成、変更および削除しないため、成果ファイルの出力を依頼しない。"
    "委譲元の文脈へは結果の要約だけが入る。"
    "新しい候補・評価軸を導く調査と案出しは、exploreでもdelegateでもmodel_type=high_tierを明示する。"
    "既に決めた問いの所在・値・件数などの機械的な事実確認と区別し、混合する依頼はhigh_tierを使う。\n"
    + EXPLORE_CAPABILITIES
    + "\n"
    "write: 設計、調査、レビューおよび公開操作を依頼せず、成果物種別、読者、事実、根拠、反映先と完成形を`prompt`へ明記する。"
    "読者が異なる文章は別の依頼にする。委譲先はファイルの読取・検索・作成・編集だけを行う。\n"
    "shell: 出力が4,000トークン（英数字主体で約16,000バイト、300行程度）を超える見込みのコマンドを委譲し、"
    "1,000トークン未満に収まる見込みのコマンドは自ら実行する。"
    "読み取り専用の制約は課さないため、対象を変更する自動チェックも渡せる。委譲元の文脈へは終了状態と要約だけが入る。"
)


SUBAGENT_MD_PATH_DESCRIPTION = parameter_description(
    "taskで必須、他のmodeでは指定しない。委譲先の手順と返却契約を保持する`<役割名>.subagent.md`の役割名（例: `add-wi`）。"
    "役割名はサーバー自身のplugin rootの`share/<役割名>.subagent.md`へ解決する。"
    "別のplugin rootの文書を指定する場合はagent-toolkitの`share/*.subagent.md`の絶対パスを渡す。"
)


EXTRA_PARAMS_DESCRIPTION = parameter_description(
    "taskだけで受理し、他のmodeでは指定しない。省略時は入力なしとして扱う。"
    "`<役割名>.subagent.md`が`## 入力`で宣言した入力名（必須入力名と任意入力名）をキー、文字列を値とする。"
    "待機と再開の方針はサーバーが伝えるため、委譲プロンプトへ書き足さない。"
    "必須入力の欠落と宣言外の入力名を含む場合は委譲先を起動しない。"
    "ただし必須入力の`引き継ぎ記録先`を省略した場合は、サーバーが委譲元のセッションのmanaged-tempの直下に`（新規）`の記録先を用意し、"
    "その絶対パスを応答の`handoff_record_path`で返す。継続する担当へは、その値へ`（継続）`を付けて渡す。"
)


PROMPT_DESCRIPTION = parameter_description(
    "delegate・explore・writeで必須、taskとshellでは指定しない。委譲先へ渡す依頼本文。"
    "explore・writeの委譲先は委譲先向けの規範（`share/rules-subagent.md`）を受け取らず、"
    "ClaudeとCodexでは作業ディレクトリのプロジェクト規範も読まないため、作業に必要な指示を全て書く。"
    "`<役割名>.subagent.md`を指す本文は渡さず、taskで起動する。"
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
    "explore・write・shellは同じ種類のsessionを区別できるよう明示する。"
    "省略時はそれぞれ`explore`、`write`、`shell-<コマンドの最初の語のbasename>`を使う。"
    "labelが`-review`で終わるsessionの完了結果には、指摘の採否を確定する手順を示す`next_action`が付く。"
    "`start`と`send_message`の応答は、そのsessionが保持する担当名（省略時は生成した値）を`label`で返す。"
)


SESSION_ID_DESCRIPTION = parameter_description(
    "対象sessionの識別子。`start`の応答または`list`が返した`session_id`をそのまま渡す。"
)


MODEL_TYPE_DESCRIPTION = parameter_description(
    "委譲先のモデルを選ぶ。モデル段位の種別（例: `high_tier`、`medium_tier`、`low_tier`、`write`）か、"
    "ASCIIカンマ区切りの`<claude|codex|agy>:<model>[/<effort>]`の候補列"
    "（例: `agy:gemini-3.8-flash/medium,claude:opus[1m]/medium`）を指定する。"
    "候補は先頭から試し、起動できない候補を除いて次の候補へ切り替える。"
    "delegateでは必須。他のmodeでは省略してよく、省略時はtaskが`<役割名>.subagent.md`に対応する工程別設定、"
    "exploreとshellが`low_tier`、writeが`write`の設定を使う。"
    "新しい候補・評価軸を導く調査と案出しは、explore・delegateを問わず`high_tier`を明示する。"
    "機械的な事実確認と区別し、両者が混在する依頼も`high_tier`を使う。"
    "機械的な事実確認で軽量側の候補では判断材料が不足する調査には、exploreで`medium_tier`を指定する。"
    "指定した値はそのsessionだけに使い、恒常的な変更は`atk config set`で行う。"
)


# modeごとの必須の入力と受理する入力。`model_type`と`label`は全modeで受理するため含めない。
START_MODE_INPUTS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "task": (("subagent_md_path",), frozenset({"subagent_md_path", "extra_params"})),
    "delegate": (("prompt", "model_type"), frozenset({"prompt"})),
    "explore": (("prompt",), frozenset({"prompt"})),
    "write": (("prompt",), frozenset({"prompt"})),
    "shell": (("command", "summary_policy"), frozenset({"command", "summary_policy"})),
}


START_MODE_EXAMPLES = {
    "task": '`{"cwd": "<絶対パス>", "subagent_md_path": "<役割名>", "extra_params": {...}}`',
    "delegate": '`{"cwd": "<絶対パス>", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "high_tier"}`',
    "explore": '`{"cwd": "<絶対パス>", "mode": "explore", "prompt": "<質問と調べる範囲>", "label": "explore-<対象>"}`',
    "write": '`{"cwd": "<絶対パス>", "mode": "write", "prompt": "<起草の依頼>", "label": "write-<対象>"}`',
    "shell": (
        '`{"cwd": "<絶対パス>", "mode": "shell", "command": "<コマンド>", "summary_policy": "<要約方針>", '
        '"label": "shell-<コマンド名>"}`'
    ),
}


# Claude Codeはツール説明とserver instructionsを、設定を変えない状態で各2,048文字に切り詰める。
# 観測と結果受領の手順を先頭に置き、modeの選び方は`mode`の引数説明へ置いて上限内に収める。
START_DESCRIPTION = "\n".join(
    (
        "委譲先sessionを開始し、`mode`で入力と起動条件を選ぶ。",
        "返した`session_id`は同じ応答で`atk agents wait`を単独実行して観測し、不要なら`kill`で破棄する。"
        "waitは`session_id`を引数に取らず、登録済みの全sessionの終端を待つ。",
        "exploreはファイル作成・変更・削除禁止。全量保存はshell、成果ファイル生成の調査はdelegateへ。返却は委譲元が保存する。",
        EXPLORE_CAPABILITIES,
        "",
        "| mode | 用途 | 必須の入力 | 起動条件と`model_type`省略時の設定 |",
        "| --- | --- | --- | --- |",
        "| `task`（省略時） | `share/<役割名>.subagent.md`を持つ定型作業 | `subagent_md_path` | "
        "`<役割名>.subagent.md`の`mode:`、対応する工程別設定 |",
        "| `delegate` | `<役割名>.subagent.md`の無い単発作業 | `prompt`・`model_type` | "
        "通常起動。委譲先規範が届き、ClaudeとCodexはスキルを使える。"
        "Antigravityへは`rules/`配下とagent-toolkitのスキルが届かない |",
        "| `explore` | 読み取り専用の調査とレビュー | `prompt` | 軽量起動、`low_tier` |",
        "| `write` | 確定した文章の起草と小規模な定型書込 | `prompt` | 軽量起動、`write` |",
        "| `shell` | コマンド実行と結果の要約 | `command`・`summary_policy` | 軽量起動、`low_tier` |",
        "",
        "選び方は`mode`、制約は各引数を参照。"
        "必須入力の欠落とmode外の入力は起動せず拒否し、受理する入力と呼び出し方を返す。"
        "taskは必須入力欠落と宣言外の入力名も拒否し、該当・受理項目名を返す。",
        "",
        "例（`cwd`は全modeで必須）:",
        '- task: `{"cwd": "/repo", "subagent_md_path": "exec-review", "extra_params": {"計画": "/abs/plan.md"}}`',
        '- delegate: `{"cwd": "/repo", "mode": "delegate", "prompt": "<依頼本文>", "model_type": "high_tier"}`',
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
        "全候補が可用性かagyのturn失敗で終端すれば最後の終端応答を返す。"
        "最後のagy候補のbackend開始例外は除外理由付きの例外で返す。起動条件の詳細は`show`で取得する。",
    )
)
