# 委譲：`<役割名>.parent.md`を持つ委譲の起動

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 委譲プロンプトや返却の文章を作成・検収する前 | `quality-standards.md` | | | |
| Codexで委譲、待機、状態確認または中断へ着手する前（agents_serverのexplore・shellの起動から受領までを除く） | `codex-runtime.md` | | | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `base-contract.md` | | | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `mandatory-rules.md` | | | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `sending.md` | | | |
| 作業の分担・委譲単位を決める前（計画起草前、独立した複数単位や長い工程の着手前を含む）、起動契約に手順と必須入力がない委譲を開始する前（explore・shellを除く）、確定した委譲構成を変更する前 | `routing.md` | | | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `runtime-routing.md` | | | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `process-model-settings.md` | | | |
| 元担当の継続・回復・置換・再起動または役割引継ぎを選ぶ前 | `continuation.md` | | | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `continuation.md` | | | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `agent-tool-continuation.md` | | | |
| 可用性の障害を確認して代替起動を判断する前 | `fallback-launch.md` | | | |
| 工程別設定の対象外でモデル・effortを選ぶ前 | `model-and-effort.md` | | | |
| Claude Codeで`Agent`ツールの委譲先やバックグラウンドタスクを初回起動する前、定期再確認タスクを自身で設定する前、設定や回収の失敗通知に対処する時、Claude Code固有の手順を確定する時（explore・shellの起動から受領までの操作は除き、本書の同名節に従う） | `claude-code-runtime.md` | | | |
| 完了通知の処理、ならびに待機や停滞の判定を行う時 | `waiting-and-monitoring.md` | | | |
| 判定不能や拒否で待機を開始できない時、`atk agents wait`が終了コード4・8・10、または待機対象0件の非ゼロで終了した時、待機呼び出しがホストの上限により失敗した時、ホストからバックグラウンド待機の停止通知を受信した時、委譲先や自身のツール呼び出しが終了ステータスを返さずに終わった時、`agents_server`のMCPサーバーが再起動した時 | `wait-anomalies.md`の該当節 | | | |
| 稼働中の委譲先へ追加指示を送る前 | `base-contract.md` | | | |
| 委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前 | 本書「継続と新規起動」、`routing.md` | | | |
| 同一worktreeに対して後続または並列の書き込み担当を起動する前（shellを除く。shellは本書「explore・shellの起動」に従う） | `mandatory-rules.md` | | | |
| 実装担当・レビュー担当のworktreeと書込所有権を割り当てる前、およびそれらの担当を起動する前 | `implementation-worktree.md` | | | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `receiving.md` | | | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `mandatory-rules.md` | | | |
| 委譲先がプロンプトで`引き継ぎ記録先`を受け取り、最初の記録を書き込む前 | `handoff-record.md` | | | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:process-wi`の場合） | `../../process-wi/references/run-lanes.md` | | | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:write-plan`による直接起動の場合） | `../../write-plan/SKILL.md` | | | |
| 指摘の採否と修正方針を確定する時 | `agent-basics:review-standards`を起動し、同スキルの`references/reviewee.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` | |
| Claude Codeで長時間待機前の会話圧縮、またはバックグラウンドタスクの起動形を判断する時 | `waiting-claude-code.md` | | | |
| 停滞の検知と巻き取りを判断する時 | `waiting-stalls.md` | | | |
| 結果本文の保存期間と回収手順を決める前 | `agent-toolkit:managed-temp/SKILL.md` | | | |
| 役割文書を持つ委譲を起動する前 | 呼び出し元の起動・受領契約。共有契約は`${CLAUDE_PLUGIN_ROOT}/share/<役割名>.parent.md`、単一の呼び出し元に統合された契約はその呼び出し元が示す節と条件付きで挙げる資料 | 操作 | `delegation` | `role-contract` |
| 事象単位の調査を委譲する前 | `agent-toolkit:delegation/references/investigation-delegation.md` | | | |
| 受入シナリオの文書試行・常駐サービスの公開前観測の前 | `agent-toolkit:delegation/references/acceptance-scenarios.md` | | | |
| エージェント向け文書の責務と配置を決める前 | `agent-toolkit:delegation/references/agent-documents-basics.md` | | | |
| ユーザーの入力素材を整理して取り込む時 | `agent-toolkit:delegation/references/writing.md` | | | |
| textlintなど文章チェックの診断へ対応する時 | `agent-toolkit:delegation/references/textlint-violations.md` | | | |
| 文章の書き直しを委ねる時 | `agent-toolkit:delegation/references/textlint-violations.md` | | | |
| エージェント向け文書の置き場所を選ぶ時 | `agent-toolkit:delegation/references/delivery-scope.md` | | | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `sub-agents.md`、`sub-agents-declarations.md`、`sub-agents-context.md` | | | |
| レビュー指摘への応答、修正、修正後の完了を判断する前 | `agent-toolkit:/share/exec-review-responses.md` | | | |

## `<役割名>.parent.md`を持つ委譲の起動

呼び出し元の契約で起動手順が定められている委譲は、次の定型手順で起動する。
本節の規定は、共有の`<役割名>.parent.md`を用いる場合と、単一の呼び出し元規範に統合された契約を用いる場合の両方に適用する。
委譲元は起動前に該当する契約を読み、前提条件、入力値の決定規則、受領と検収の要件に従う。
各契約には役割ごとに異なる引数値のみを記述する。

1. 入力をそろえるため、起動契約が定める絶対パスを`cwd`、役割名（例: `add-wi`）を`subagent_md_path`、名前付き入力を`extra_params`へ設定する。
   サーバーが自身のplugin rootの`share/<役割名>.subagent.md`を解決する。
   `mode`は指定しない。
   `model_type`は起動契約に値の決定規則がある場合にのみ指定する。
   省略された場合のmodeやmodel_typeは、サーバーが役割文書のfrontmatterから自動的に解決する。
   `引き継ぎ記録先`の扱いについては、`base-contract-handoff.md`「引き継ぎ記録先」の規定に従う。
   `extra_params`に設定する値には、`sending-prompt.md`「委譲プロンプトの構成」の「値に含めない内容」と「未検証の値」、ならびに`base-contract.md`の`forwarded-user-input`標識と絶対パスの規定を適用する。
   必須入力の漏れや未宣言の入力名の拒否、`引き継ぎ記録先`の準備、応答言語の伝達、`<役割名>.subagent.md`の実在確認、`${CLAUDE_PLUGIN_ROOT}`の展開はサーバー側で処理する。
   サーバーから起動を拒否された場合は、エラー応答に示された項目と指示に従って再起動する。

2. 担当を開始するため、確定した引数で`agents_server`の`start`を1回呼び出す。

3. 起動後は返却された`session_id`を`atk agents wait`で観測する。
   Claude Code環境では、初回の`start`処理の中でagent-toolkitのmodが定期再確認タスクを設定し、その結果が`start`の返却後に通知される。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 基本委譲契約：引き継ぎ記録先の基準を適用する前 | `base-contract-handoff.md` |
| 委譲プロンプトの送信：委譲プロンプトの構成の基準を適用する前 | `sending-prompt.md` |
