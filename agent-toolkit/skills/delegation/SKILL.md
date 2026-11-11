---
name: delegation
description: >
  作業へ着手する前に担当範囲の一部を委譲先へ分けるかと委譲単位を判定する時
  （計画の起草前、独立した複数の作業単位か長い工程を含む作業の着手前）、
  委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前、
  サブエージェントまたは委譲先を起動、観測、継続もしくは巻き取るとき、または停滞検知・複数のエージェントの調整が必要な場合に起動し、委譲の共通契約と実行手段ごとの契約を提供する。
user-invocable: false
---

# 委譲

エージェントを起動した側を委譲元と呼ぶ。
委譲先は、`Agent`ツールで起動する子エージェント（サブエージェント）と`agents_server`で起動する子セッションの総称である。
委譲先がさらに別のエージェントを起動した場合も、起動した側を基準として委譲先と呼ぶ。
「孫」や「子孫」という表現は、基準となるエージェントを同一文中で明示する場合に限り用いる。
委譲先が担う用途固有の作業内容は、委譲先自身が読み込む`<役割名>.subagent.md`やagent定義で指定する。

実際に委譲するには、エージェントの起動や終了を受け取る手段と、参照すべき手順資料が必要である。
これらが備わっていない環境では、作業の分担案と委譲プロンプトの下書きを作成して返し、実際の起動や待機は該当機能を利用できるClaude CodeまたはCodexで実施する。
下書きを作成する際は[文章の品質基準](references/quality-standards.md)に従い、計画上の分担と実際に起動した担当を区別して記述する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲プロンプトや返却の文章を作成・検収する前 | `references/quality-standards.md` |

## 読込表

次に行う委譲・受領・待機の操作と、自身の役割から条件を選び、判断前に必要な資料の全文を読む。
hookの通知を待たずに選び、同じ版の取得済み本文は再利用する。
会話圧縮後は現在の要求・認可・実行状態を復元し、再開する工程で必要な範囲を選び直す。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Codexで委譲、待機、状態確認または中断へ着手する前（agents_serverのexplore・shellの起動から受領までを除く） | `references/codex-runtime.md` | | | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/base-contract.md` | | | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/mandatory-rules.md` | | | |
| 呼び出し元の起動契約で手順が定められていない委譲（`start`の`delegate`・`write`、`Agent`ツール、Codexの組み込み委譲）を開始する前 | `references/sending.md` | | | |
| 作業の分担・委譲単位を決める前（計画起草前、独立した複数単位や長い工程の着手前を含む）、起動契約に手順と必須入力がない委譲を開始する前（explore・shellを除く）、確定した委譲構成を変更する前 | `references/routing.md` | | | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `references/runtime-routing.md` | | | |
| 委譲先とモデルを選定する時（explore・shell、および役割文書を持ち`model_type`を指定しない起動を除く） | `references/process-model-settings.md` | | | |
| 元担当の継続・回復・置換・再起動または役割引継ぎを選ぶ前 | `references/continuation.md` | | | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `references/continuation.md` | | | |
| Claude Codeの`Agent`ツールで起動した同一担当を継続する前 | `references/agent-tool-continuation.md` | | | |
| 可用性の障害を確認して代替起動を判断する前 | `references/fallback-launch.md` | | | |
| 工程別設定の対象外でモデル・effortを選ぶ前 | `references/model-and-effort.md` | | | |
| Claude Codeで`Agent`ツールの委譲先やバックグラウンドタスクを初回起動する前、定期再確認タスクを自身で設定する前、設定や回収の失敗通知に対処する時、Claude Code固有の手順を確定する時（explore・shellの起動から受領までの操作は除き、本書の同名節に従う） | `references/claude-code-runtime.md` | | | |
| 完了通知の処理、ならびに待機や停滞の判定を行う時 | `references/waiting-and-monitoring.md` | | | |
| 判定不能や拒否で待機を開始できない時、`atk agents wait`が終了コード4・8・10、または待機対象0件の非ゼロで終了した時、待機呼び出しがホストの上限により失敗した時、ホストからバックグラウンド待機の停止通知を受信した時、委譲先や自身のツール呼び出しが終了ステータスを返さずに終わった時、`agents_server`のMCPサーバーが再起動した時 | `references/wait-anomalies.md`の該当節 | | | |
| 稼働中の委譲先へ追加指示を送る前 | `references/base-contract.md` | | | |
| 委譲済みの要求について自ら本文を読む、追加調査する、回答を作成する前 | 本書「継続と新規起動」、`references/routing.md` | | | |
| 同一worktreeに対して後続または並列の書き込み担当を起動する前（shellを除く。shellは本書「explore・shellの起動」に従う） | `references/mandatory-rules.md` | | | |
| 実装担当・レビュー担当のworktreeと書込所有権を割り当てる前、およびそれらの担当を起動する前 | `references/implementation-worktree.md` | | | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `references/receiving.md` | | | |
| 委譲先からの完了報告を受領して検収する前（explore・shellを除く） | `references/mandatory-rules.md` | | | |
| 委譲先がプロンプトで`引き継ぎ記録先`を受け取り、最初の記録を書き込む前 | `references/handoff-record.md` | | | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:process-wi`の場合） | `../process-wi/references/run-lanes.md` | | | |
| 実行工程を委譲する際の受領ルールを確定する時（explore・shellを除く。`起動経路`が`agent-toolkit:write-plan`による直接起動の場合） | `../write-plan/SKILL.md` | | | |
| 指摘の採否と修正方針を確定する時 | `agent-basics:review-standards`を起動し、同スキルの`references/reviewee.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` | |
| Claude Codeで長時間待機前の会話圧縮、またはバックグラウンドタスクの起動形を判断する時 | `references/waiting-claude-code.md` | | | |
| 停滞の検知と巻き取りを判断する時 | `references/waiting-stalls.md` | | | |
| agents_serverのexplore・shellを起動する前 | `references/delegation-lightweight.md` | | | |
| 結果本文の保存期間と回収手順を決める前 | `agent-toolkit:managed-temp/SKILL.md` | | | |
| 役割文書を持つ委譲を起動する前 | 呼び出し元の起動・受領契約。共有契約は`${CLAUDE_PLUGIN_ROOT}/share/<役割名>.parent.md`、単一の呼び出し元に統合された契約はその呼び出し元が示す節と条件付きで挙げる資料 | 操作 | `delegation` | `role-contract` |

## 送信

役割指定の初回入力の構成を含め、`references/delegation-sending.md`「送信」に従う。

## 継続と新規起動

`references/continuation.md`「継続と新規起動」の番号順に進める。
元担当の回復・置換・再起動・代替起動・引継ぎの前に、`references/codex-runtime-continuation.md`「後続操作の共通先行条件」を適用する。

## 受領と検収

explore・shellは`references/delegation-lightweight.md`「explore・shellの起動」の最終手順に従う。
それ以外は`references/receiving.md`の番号順に、受領、形式と実体の検収、指摘の採否・改善点の処理を終えてから上位へ返却する。

## 停滞の検知と巻き取り

観測の優先順位は`references/waiting-and-monitoring-intervals.md`「待機区間の構成」に従う。
Codex環境で元の担当者が稼働している間の処理保留は`references/codex-runtime-continuation.md`「後続操作の共通先行条件」に従う。

- Codex以外の環境で、同一種別・同一用途のエージェント起動を繰り返しても収束しない場合は、`references/continuation.md`「継続と新規起動」に従って担当者、作業単位、モデル、実行手段を見直す。
- 委譲先の役割を自身で巻き取る場合は、その役割の`<役割名>.subagent.md`に定められた義務（判断の責任主体、加工せずに伝達すべき対象、収束条件）を自身に適用して遂行する。

## 再委譲する場合に守る事項

上位のエージェントから委譲を受けたエージェントがさらに別のエージェントへ委譲する場合は、本スキルが委譲元に対して定めている規定を自身に適用する。

- 上位から受け取った取り決め（作業場所、書込を行う主体、出力言語、プロセスの所有権、不可逆操作の権限と許可範囲）を同一の意味で引き継がせ、子孫エージェントの完了本文を自身で受け取って自身の完了報告に含める。

## 工程で参照する運用資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 事象単位の調査を委譲する前 | `agent-toolkit:delegation/references/investigation-delegation.md` |
| 受入シナリオの文書試行・常駐サービスの公開前観測の前 | `agent-toolkit:delegation/references/acceptance-scenarios.md` |
| エージェント向け文書の責務と配置を決める前 | `agent-toolkit:delegation/references/agent-documents-basics.md` |
| ユーザーの入力素材を整理して取り込む時 | `agent-toolkit:delegation/references/writing.md` |
| textlintなど文章チェックの診断へ対応する時 | `agent-toolkit:delegation/references/textlint-violations.md` |
| 文章の書き直しを委ねる時 | `agent-toolkit:delegation/references/textlint-violations.md` |
| エージェント向け文書の置き場所を選ぶ時 | `agent-toolkit:delegation/references/delivery-scope.md` |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `references/sub-agents.md`、`references/sub-agents-declarations.md`、`references/sub-agents-context.md` |

## 参照資料の取得

| 時点または条件 | 全文読む資料 |
| --- | --- |
| レビュー指摘への応答、修正、修正後の完了を判断する前 | `agent-toolkit:/share/exec-review-responses.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 送信に関する判断・作業の前 | `references/delegation-sending.md` |
| explore・shellの起動に関する判断・作業の前 | `references/delegation-lightweight.md` |
| `<役割名>.parent.md`を持つ委譲の起動に関する判断・作業の前 | `references/delegation-role-launch.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Codexでの委譲方法：後続操作の共通先行条件の基準を適用する前 | `references/codex-runtime-continuation.md` |
| 待機と観測：待機区間の構成の基準を適用する前 | `references/waiting-and-monitoring-intervals.md` |
