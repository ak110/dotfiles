# 委譲：送信

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 委譲プロンプトや返却の文章を作成・検収する前 | `quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/quality-standards.md` | |
| Codexで委譲、待機、状態確認または中断へ着手する前（agents_serverのexplore・shellの起動から受領までを除く） | `codex-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/codex-runtime.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` | |
| 作業の分担・委譲単位を決める前（計画起草前、独立した複数単位や長い工程の着手前を含む）、起動契約に手順と必須入力がない委譲を開始する前（explore・shellを除く）、確定した委譲構成を変更する前 | `routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` | |
| 元担当の継続・回復・置換・再起動または役割引継ぎを選ぶ前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `agent-tool-continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-tool-continuation.md` | |
| 可用性の障害を確認して代替起動を判断する前 | `fallback-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/fallback-launch.md` | |
| 工程別設定の対象外でモデル・effortを選ぶ前 | `model-and-effort.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/model-and-effort.md` | |
| Claude Codeで`Agent`ツールの委譲先やバックグラウンドタスクを初回起動する前、定期再確認タスクを自身で設定する前、設定や回収の失敗通知に対処する時、Claude Code固有の手順を確定する時（explore・shellの起動から受領までの操作は除き、本書の同名節に従う） | `claude-code-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime.md` | |
| 完了通知の処理、ならびに待機や停滞の判定を行う時 | `waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` | |
| 判定不能や拒否で待機を開始できない時、`atk agents wait`が終了コード4・8・10、または待機対象0件の非ゼロで終了した時、待機呼び出しがホストの上限により失敗した時、ホストからバックグラウンド待機の停止通知を受信した時、委譲先や自身のツール呼び出しが終了ステータスを返さずに終わった時、`agents_server`のMCPサーバーが再起動した時 | `wait-anomalies.md`の該当節 | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies.md` | |
| 稼働中の委譲先へ追加指示を送る前 | `base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前 | 本書「継続と新規起動」、`routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 同一worktreeに対して後続または並列の書き込み担当を起動する前（shellを除く。shellは本書「explore・shellの起動」に従う） | `mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 実装担当・レビュー担当のworktreeと書込所有権を割り当てる前、およびそれらの担当を起動する前 | `implementation-worktree.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/implementation-worktree.md` | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `receiving.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/receiving.md` | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 委譲先がプロンプトで`引き継ぎ記録先`を受け取り、最初の記録を書き込む前 | `handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:process-wi`の場合） | `../../process-wi/references/run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:write-plan`による直接起動の場合） | `../../write-plan/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/SKILL.md` | |
| 指摘の採否と修正方針を確定する時 | `agent-basics:review-standards`を起動し、同スキルの`references/reviewee.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` | |
| Claude Codeで長時間待機前の会話圧縮、またはバックグラウンドタスクの起動形を判断する時 | `waiting-claude-code.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-claude-code.md` | |
| 停滞の検知と巻き取りを判断する時 | `waiting-stalls.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-stalls.md` | |
| 結果本文の保存期間と回収手順を決める前 | `agent-toolkit:managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` | |
| 役割文書を持つ委譲を起動する前 | 呼び出し元の起動・受領契約。共有契約は`${CLAUDE_PLUGIN_ROOT}/share/<役割名>.parent.md`、単一の呼び出し元に統合された契約はその呼び出し元が示す節と条件付きで挙げる資料 | 操作 | `delegation` | `role-contract` |
| 事象単位の調査を委譲する前 | `agent-toolkit:delegation/references/investigation-delegation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/investigation-delegation.md` | |
| 受入シナリオの文書試行・常駐サービスの公開前観測の前 | `agent-toolkit:delegation/references/acceptance-scenarios.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/acceptance-scenarios.md` | |
| エージェント向け文書の責務と配置を決める前 | `agent-toolkit:delegation/references/agent-documents-basics.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-documents-basics.md` | |
| ユーザーの入力素材を整理して取り込む時 | `agent-toolkit:delegation/references/writing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/writing.md` | |
| textlintなど文章チェックの診断へ対応する時 | `agent-toolkit:delegation/references/textlint-violations.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/textlint-violations.md` | |
| 文章の書き直しを委ねる時 | `agent-toolkit:delegation/references/textlint-violations.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/textlint-violations.md` | |
| エージェント向け文書の置き場所を選ぶ時 | `agent-toolkit:delegation/references/delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/delivery-scope.md` | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `sub-agents.md`、`sub-agents-declarations.md`、`sub-agents-context.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sub-agents.md`、`read:agent-toolkit:*/skills/delegation/references/sub-agents-declarations.md`、`read:agent-toolkit:*/skills/delegation/references/sub-agents-context.md` | |
| レビュー指摘への応答、修正、修正後の完了を判断する前 | `agent-toolkit:/share/exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` | |

## 送信

全起動経路の共通返却・エスカレーション契約は`share/rules/rules-subagent.md`「返却形式の受け渡し」が所有し、agents_serverが通常起動へ全文、軽量起動へ自己完結した短節を届ける。
呼出元は役割固有の入力と結果だけを補い、形式未指定時は同節の正常結果の形式を用いる。
taskの不正な続行不能に対する1回の自動回復と、その後の受領は`receiving-format.md`「返却形式の適合確認」に従う。

1. 担当と契約をそろえるため、定型の委譲内容は、呼び出し元の起動・受領契約と`<役割名>.subagent.md`で定義する。
   単一の呼び出し元だけで使う契約はその規範内へ統合し、複数の呼び出し元で共有する契約は`<役割名>.parent.md`として配置できる。
   評価の観点、判定基準、具体的な手順、返却形式は役割文書に記載する。
   委譲元は呼び出し元の契約に基づき、対象と実行時の値のみを渡す。
   自由記述のプロンプトによる起動は、役割文書が存在しない単発の作業に限定して用いる。
   役割固有の返却形式は役割文書の`## 出力`が定義を所有し、呼出側の既存の起動・受領契約へ必要な部分だけを生成配置する。
   委譲元はその配置先で入力と返却形式を確認し、起動と検収を進める。
   委譲元は起動の準備段階で`<役割名>.subagent.md`の本文を読み取らない。
   起動設定や必須入力の形式チェックはサーバーに任せる。
   この読取の省略は、ファイル自体の編集のための読取、委譲先自身による読取、完了後の形式確認とは区別する。

2. 適用する版をそろえるため、規範の変更を適用する委譲先には、作業ツリー原本の絶対パス、適用する節、対象の版を渡し、委譲先自身が判断前に読み込む。
   同じplugin rootに属する起動契約と役割定義をセットで使用し、すでに稼働しているCLI、フック、MCPが参照している版と作業ツリーの版を区別して扱う。
   役割文書を指定する委譲は、委譲元の会話履歴を引き継がない方法で起動する。
   Codex環境で`agents_server`が使えず組み込みの委譲で代用する場合は、`spawn_agent`に`fork_turns: "none"`を明示する。
   会話履歴を引き継ぐforkを選択できる条件は、`routing-fork.md`「会話を引き継ぐ委譲」に従う。

3. 入力を確定するため、委譲プロンプトは、1行目の命令と必須入力の列挙という2つの要素で構成する。
   具体的な構成と列挙書式は`sending-prompt.md`「委譲プロンプトの構成」に従う。
   プロンプトに含めてよい要素は、`base-contract-inputs.md`「入力の適格性と追送」で定める。
   exploreおよびshellの起動については次節に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 基本委譲契約：入力の適格性と追送の基準を適用する前 | `base-contract-inputs.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract-inputs.md` |
| 委譲先の完了報告の受領と検収：受領手段と報告様式の選択の基準を適用する前 | `receiving-format.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/receiving-format.md` |
| 委譲の要否判定と委譲単位の確定：会話を引き継ぐ委譲の基準を適用する前 | `routing-fork.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing-fork.md` |
| 委譲プロンプトの送信：委譲プロンプトの構成の基準を適用する前 | `sending-prompt.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending-prompt.md` |
