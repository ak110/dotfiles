# 委譲：explore・shellの起動

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
| 工程別設定の対象外でモデル・effortを選ぶ前、委譲した成果が受入れ基準に届かず再委譲やeffort・モデルの変更を判断する前 | `model-and-effort.md` | | | |
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

## explore・shellの起動

個別の調査やコマンド実行を自身で行うか、explore・shellへ委譲するかは、agents_serverのserver instructionsに記載されている採算の目安に基づいて判断する。
`routing.md`の全文読込は、作業全体の分割を判定する際に行う。
両モードの起動から受領までに満たすべき必須の要件は本節で定める。
括弧内の参照先は詳細情報の所在を示すものであり、実際の読込義務は前述の読込表の指定に従う。

1. 全体点検する際は、`routing-investigation.md`「コンテキスト消費が大きい調査の切り出し」に基づき、起動・初回読込・圧縮後再読・検収を含む総費用と分担の利点を比較する。
   公開操作、共通定義、呼び出し元、必要な情報量から担当範囲を定め、全体の管理と統合を委譲元が担う。

2. 起動前に対象、読取・書込の権限、完了条件、返却すべき観測値を確定する。
   委譲先が読み込む既存ファイルの絶対パスの実在、比較基準、実行する版を確認する。
   分量の多い調査では読取ツールの範囲指定を渡し、出力の切り詰めや上限到達を網羅性の根拠として扱わない（base-contract.md「起動前の確認」「分離実行の受領物」）。

3. 作業を分割する場合は`routing-decisions.md`「要否判定」を用い、モデルを選択する場合は`process-model-settings.md`「工程別モデル設定」に従う。
   明示的な指定がない場合は工程別の設定値を使用し、WIにモデル指定がある場合はそれを優先する。
   受領時には、指定値、起動候補、観測された実際の値を区別して扱う。

4. exploreは読取専用とする。
   shellの書込先は、生成物も含めて他の担当と重複しない範囲に限定し、同一担当の交代は先行する担当の終了後に実施する。
   同一worktreeでの書込の分担、作業ツリー全体へ影響する操作の直列化、別worktreeへ分離する条件は`mandatory-rules.md`「作業ツリーの保護」に従う。
   軽量起動では自動配送されないプロジェクト固有の指示や、コマンドの入力境界、終了状態、標準出力・標準エラー出力の保持を確実に引き渡す（base-contract.md「起動前の確認」、sending.md「権限と成果物の取り決め」）。

5. 入力は命令1文と必要な値（元の要求、対象、完了条件、返却形式、基準記録）にとどめ、すでに配布済みの制約、経緯、推測、既存記録の転記は含めない。
   自身で裏付けをとっていない値は未検証情報として明示する。
   exploreは読取専用として結果を返し、後続の入力には`atk agents wait`が保存した`agent_message_path`を直接指定する（base-contract.md「入力の適格性と追送」、sending.md「委譲プロンプトの構成」「未検証情報の取り扱い」）。

6. 各モードの入力項目や機能は、公開されている`start`の説明とスキーマで確認し、`cwd`に作業ディレクトリの絶対パスを渡す。
   exploreでは`prompt`を、shellでは`command`と`summary_policy`を使用する。
   スキルの手順を必要とする作業は`task`または`delegate`に委ねる（sending.md「起動と観測」）。

7. 返却された`session_id`を保持し、`atk agents wait`を用いて終了状態と結果本文を受け取る。
   待機の実行形式、終了コード、バックグラウンド化、通知後の受領手順は、読込表にある`waiting-and-monitoring.md`に従い、ホスト別の待機手順は後述の「軽量起動のホスト別待機」に従う。

8. 終了後に対象ファイルの差分、実行結果、診断情報を確認し、返却形式ならびに対象・権限・完了条件との整合性を検収する。
   件数、不在、網羅性は、省略のない取得結果に基づいて判定し、事実の主張は実物の根拠で裏付ける。
   受領前の段階で得られた情報のみで完了とみなさず、不足がある場合は同一担当へ差し戻す（receiving.md「返却形式の適合確認」「終了と報告内容の確認」、sending.md「権限と成果物の取り決め」）。

   結果本文の再保存は行わず、後続処理が参照する`agent_message_path`や待機実行の保存成果物は、参照が完了するまで保持する。
   保存期間と回収の手順は`agent-toolkit:managed-temp`に従う。

### 軽量起動のホスト別待機

Codexは`codex-runtime-waiting.md`「agents_serverの二層待機」、Claude Codeは`claude-code-runtime-waiting.md`「待機中の定期再確認と背景転換」に従う。
軽量起動にも同じ識別子保持、回収、定期再確認の設定・再利用・削除条件を適用する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Claude Codeでの委譲の実装：検証コマンドの実行形態の基準を適用する前 | `claude-code-runtime-waiting.md` |
| Codexでの委譲方法：agents_serverの二層待機の基準を適用する前 | `codex-runtime-waiting.md` |
| 委譲の要否判定と委譲単位の確定：要否判定の基準を適用する前 | `routing-decisions.md` |
| 委譲の要否判定と委譲単位の確定：コンテキスト消費が大きい調査の切り出しの基準を適用する前 | `routing-investigation.md` |
