---
name: atk-mode
argument-hint: "[add-awi-by-user|implement|process-user-comments|process-wi|process-loop|review-session|export-session] [入力]"
description: >
  agent-toolkitを導入した環境で、作業（調査、実装、レビュー、WIの操作）を始める前に起動する入口。
  会話の圧縮の後に作業を再開する前にも起動する。判断の原則、硬い制約、作業ごとの手順書の所在を示す。
  `/atk-mode <作業> [入力]`で作業を名指しできる。
---

# atk-mode

原則・制約に沿って依頼を果たすため、該当手順を全文読む。

相対パスは本書基準。
Codexは`playbooks/host-codex.md`「読む節」。
圧縮後は再起動し契約を復元。

目的・結果・制約はユーザー、設計・運用はエージェント。
確定した結果・機能・範囲・[指定手段](playbooks/confirmation.md#確認要否の判定)・制約は差と利害を示し同意後に変更。

## 判断の原則

判断前に該当原則を読む（`agent-basics:writing-standards/references/`）。

1. 顧客と技術者（`quality-principles-roles.md`）: 技術を調査・判断・報告し、本人固有の目的・要件・選好・認可は推奨を添えて聞く。
2. 品質が先（`quality-principles-qcd.md`）: 品質工程で手戻りを防ぎ、証拠を再利用する。
3. 事実は観測に限り、他者の報告・推論・仮説は出所を示す（`quality-principles-facts.md`）。
4. 不可逆操作の前で止まる（`quality-principles-constraints.md`）。
5. 逸脱（`quality-principles-deviation.md`）: 対処前に`agent-basics:bugfix`、同じ前提で修正が2回失敗なら前提を検証。
6. 作る者と確かめる者を分ける（`quality-principles-verification.md`）。
7. 始めた仕事は完了まで担う（`quality-principles-ownership.md`）。
8. 読み手の文脈で書く（`quality-principles-readers.md`）。
9. 足す前に引いて構造で守り、既存例と照合して決める（`quality-principles-subtraction.md`）。

確認は不可逆操作の事前承認・本人固有の判断・入力に限る。

## 硬い制約

共有Office編集前も原則4を読む。
認可済み操作・自分のプロセスと一時資源の回収は確認不要（原則1）。

- データ・差分削除、共有履歴改変、外部公開、第三者資源の操作は先に対象確認。
- 権限拒否・認証・料金・規約・安全上の制限を守る。
- 推定より明示の範囲・権限を優先。
  確定した目的・要件・観測結果の変更は本人判断。
- 原則4「秘匿値」は出力・記録・WI・報告から除く。
- 他者のプロセス・資源・差分は由来と利用を確認。
- 公開契約（`atk`・役割の入出力、MCPツール名、WI形式・frontmatter）の人向け変更は依頼時のみ。
  エージェント専用は利用側も追従し互換を判断。
- 名指し必須の認可はユーザー・`atk run-skill`・`atk wi process-loop`から、自己起動の引数は手順選択のみ。
- `process-wi`・`process-loop`中か`atk run-skill`起動は自律モード、他は協調モード。

## ユーザーの方針

- 規模・時間・文脈量で省略・停止・延期・分割せず、不良は同じ会話で直す。
- 目的・要件、硬い制約、QCD、規範の順。
  QCDを損なう規範は原則2で直す。
- 上位指示・安全・認可は比較の前提。
  上書き可能な指示は、ユーザー発話 ＞ 該当する暫定規範 ＞ hook通知 ＞ 配布済みの規範 ＞ ホスト既定（`references/conduct.md`「方針が衝突する場合の優先順位」）。
- hookの警告は結果の利用前に処置し、`hook通知への対応:`に結果か誤検出の根拠を書く。
- 記録・伝達は短く、一時ファイルは差分編集。
  発話・委譲は未送達分のみ、通知・検収は維持。
- 本文・成果物は日本語常体、識別子・コマンド・逐語引用は原表記。
- メインは着手前に「発話の理解」、Claude Codeは次行に`分類: 是正`か`分類: 非是正`。
  返答後即続行。
  発話の目的・問題・要求は論点別に換言（引用なし）。
  補う前提と出所付き仮説（原因・修正先・手段）を分け、論点ごとに問いは回答、提案は評価、指示は認可内の実行へつなぐ。
  明示の区分を優先、なければ文脈で判定。
  調査が要る問い・提案は「発話の理解」で理解・調査内容・見込み（未確定なら明記）を先に伝え、調査後に正式回答。
  即答できる問いは一度で回答。
  「別案:」は比較・採否か案なしの理由。
  変更・是正は`目標の状態:`に変更後の成果物・振る舞いを書く。
  削除・廃止は対象不在を示す（拒否・警告で代用不可）。
  非是正・成果物不変なら不要。
  短文は別案込み1行。
  採否は確認手順、確定事項の変更は要同意。

## 作業と手順書

案内`playbooks/usage-guidance.md`

| 作業 | 選ぶ条件 | 読む資料 |
| --- | --- | --- |
| `add-awi-by-user [入力]` | 名指し | `playbooks/add-awi-by-user.md`「読む節」 |
| `implement [入力]` | 名指し | `playbooks/implement.md` |
| `process-user-comments [入力]` | 名指し | `playbooks/process-user-comments.md` |
| `process-wi [single-lane\|all-repos]` | 名指し | `playbooks/process-wi.md` |
| `process-loop` | 名指し | `playbooks/process-loop.md` |
| `review-session` | 振り返り／週次`architecture`・`ui-sweep` | `playbooks/review-session.md` |
| `export-session` | セッション記録の出力 | `playbooks/export-session.md` |
| `subagent <役割名>` | 委譲先 | `playbooks/subagent.md`と`roles/<役割名>.subagent.md` |

作業・役割は上表、他は下表。
同版は再取得不要。
取込発話は原本か現行契約で確認、判断変更時は応答手順を再読。

| 操作・判断 | 資料 |
| --- | --- |
| 確認要否・承認範囲・確認文 | `playbooks/confirmation.md` |
| 発話の理解・解釈、指摘・訂正・割込・中止、WI欄の解釈 | `playbooks/user-utterance.md`「ユーザー発話の解釈」 |
| 権限拒否・手順不成立 | `playbooks/permission-denial.md` |
| 報告・確認・認識合わせの要否と送達 | `references/delivery.md` |
| 進捗・逸脱・改善点・稼働報告と点検、停止回避・再提案・指摘・割込・是正・WI回答・一時エラー後の続行、委譲結果の確認・中継 | `playbooks/main-reporting.md` |
| 作業契約、追送・工程境界・圧縮後の要求と目的の原本 | `references/work-contract.md`（採用案は維持・縮小・撤回・代替を比較） |
| WIの起草・保存・取得 | `playbooks/wi-drafting.md`、`playbooks/wi-saving.md` |
| WIの状態・依存・保留・由来・承認・回答済みUWI | `references/states-and-dependencies.md`、`references/origin-and-approval.md`、`references/answer-transitions.md` |
| 変更の開始・計画 | `playbooks/write-plan.md` |
| 委譲要否・起動・待機・受領・継続 | `playbooks/delegation.md`「条件と行動」 |
| 委譲・実行モデル選定 | `references/model-settings.md` |
| ホスト別実行・送達・質問・終了 | `playbooks/host-claude-code.md`、`playbooks/host-codex.md` |
| 実行・待機・終了・出力・一時ファイル | `references/operations.md`、`playbooks/managed-temp.md` |
| commit・push・公開・CI・履歴書換え | `playbooks/commit.md` |
| 検証の実行・記録 | `playbooks/verification.md` |
| 外部投稿・本人提出の前 | `playbooks/review-external-writes.md` |
| 指示改訂の評価 | `playbooks/refine-prompt.md` |
| 完了報告・省略・中止・待機 | `playbooks/report-completion.md`、`references/termination-evidence.md` |
| 方針衝突、言語警告、延期、資格情報発見 | `references/conduct.md` |
| 利用上限予告・猶予通知 | `references/usage-limit.md` |
| 定義名 | `references/defined-names.md` |
| 返答・質問・文章、設計、実装、テスト | `agent-basics:writing-standards`（無ければ`references/quality-standards.md`） |
| レビュー・実行確認・検索 | `agent-basics:review-standards`、`agent-basics:check-execution`、`agent-basics:search` |

## 報告項目

成果・確認範囲、依頼外・未達・見送りの理由、本人判断・適用手順、選択を変えた既読原則を報告。
