# 読込の配送宣言

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Codexで委譲、待機、状態確認または中断へ着手する前（agents_serverのexplore・shellの起動から受領までを除く） | `references/codex-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/codex-runtime.md` | |
| 呼び元の起動契約が手順を定めない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 呼び元の起動契約が手順を定めない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 呼び元の起動契約が手順を定めない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` | |
| 作業へ着手する前に、担当範囲の一部を委譲先へ分けるかと委譲単位を判定する時（計画の起草前、独立した複数の作業単位か長い工程を含む作業の着手前） | `references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 呼び元の起動契約が手順と必須入力を定めていない委譲を開始する前（explore・shellを除く）と、確定した委譲単位の構成を変更する前 | `references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 委譲先とモデルを選ぶ時点（explore・shellと、役割文書を持つ起動で`model_type`を指定しない場合を除く） | `references/runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` | |
| 元担当の継続・回復・置換・再起動または役割引継ぎを選ぶ前 | `references/runtime-routing.md`の「継続と新規起動」 | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「継続と新規起動」` | |
| Claude Codeで`Agent`ツールの委譲先かバックグラウンドタスクを最初に起動する前、定期再確認を自ら装着する前、装着・回収の失敗通知に対処する時、およびClaude Code固有の手順を確定する時（explore・shellの起動から受領までの操作は除き、本書の同名節に従う） | `references/claude-code-runtime.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime.md` | |
| 完了通知および待機・停滞を確定する時 | `references/waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` | |
| `atk agents wait`が終了コード4・8・10または待機対象0件の非0で終わった時、待機の呼び出しがホストの上限で失敗した時、ホストから背景待機の停止通知を受けた時、委譲先か自身のツール呼び出しが終了statusを返さずに終わった時、`agents_server`のMCPサーバーが再起動した後 | `references/wait-anomalies.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies.md` | |
| 稼働中の委譲先へ追送する前 | `references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` | |
| 委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前 | 本書「継続と新規起動」、`references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| 同じworktreeへ後続または並列の書込担当を起動する前（shellを除く。shellは本書「explore・shellの起動」に従う） | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 委譲先の完了報告を受領して検収する前（explore・shellを除く） | `references/receiving.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/receiving.md` | |
| 委譲先の完了報告を受領して検収する前（explore・shellを除く） | `references/mandatory-rules.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/mandatory-rules.md` | |
| 委譲先が、委譲プロンプトで`引き継ぎ記録先`を受け取って最初の記録を書く前 | `references/handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` | |
| 実行工程を委譲するときの受領の取り決めを確定する時（explore・shellを除く） | `../process-wi/references/run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` | |
| 実行工程を委譲するときの受領の取り決めを確定する時（explore・shellを除く） | `../plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` | |
| 指摘の採否と修正を確定する時 | `agent-toolkit:review-standards`を起動し、同スキルの`references/reviewee.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/references/reviewee.md` | |
