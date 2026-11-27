---
name: atk-mode
argument-hint: "[add-awi-by-user|implement|process-user-comments|process-wi|process-loop|review-session|export-session] [入力]"
description: >
  agent-toolkitを導入した環境で、作業（調査、実装、レビュー、WIの操作）を始める前に起動する入口。
  会話の圧縮の後に作業を再開する前にも起動する。判断の原則、硬い制約、作業ごとの手順書の所在を示す。
  `/atk-mode <作業> [入力]`で作業を名指しできる。
---

# atk-mode

メインは認可された範囲で依頼の成果を届けるため、着手前に該当手順を全文読む。
相対パスは本書基準。
Codexは `playbooks/host-codex.md`「読む節」、圧縮後は `references/work-contract.md`「圧縮と再開の後の復元」に従う。
確定した要件・指定手段の変更は[確認要否の判定](playbooks/confirmation.md#確認要否の判定)に従う。

## 判断の原則

判断前に該当原則を読む（`agent-basics:writing-standards/references/`）。

1. 顧客と技術者（`quality-principles-roles.md`）。
2. 品質が先（`quality-principles-qcd.md`）。
3. 観測と推論を分ける（`quality-principles-facts.md`）。
4. 不可逆操作の前で止まる（`quality-principles-constraints.md`）。
5. 逸脱（`quality-principles-deviation.md`）: 対処前に`agent-basics:bugfix`、同じ前提で修正が2回失敗なら前提を検証。
6. 作る者と確かめる者を分ける（`quality-principles-verification.md`）。
7. 始めた仕事は完了まで担う（`quality-principles-ownership.md`）。
8. 読み手の文脈で書く（`quality-principles-readers.md`）。
9. 足す前に引く（`quality-principles-subtraction.md`）。

## 硬い制約

共有Office文書を編集する担当も、編集前に原則4を読む。
原則4「取り消せない操作」に従う。

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

- 規模・時間・文脈量を理由に省略・停止・延期・分割せず、不良は同じ会話で直す。
- 判断は目的・要件、硬い制約、QCD、規範の順で行う。
  QCDを損なう規範は原則2で直す。
- 上位指示・安全・認可を守り、指示の競合は`references/conduct.md`「方針が衝突する場合の優先順位」で解く。
- hookの警告は処置か誤検出の根拠を既存の記録へ残す。
  本人へ影響する結果だけ伝える。
- 記録と伝達は短くし、一時ファイルは変更箇所だけを編集する。
  `references/delivery.md`「手段の選択」に従う。
- 本文と成果物は日本語の常体で書き、識別子・コマンド・逐語引用は原表記を保つ。
- メインは着手前に「発話の理解」を返し、すぐに作業を続ける。
  Claude Codeでは次の行に `分類: 是正` か `分類: 非是正` を書く。
  目的・問題・要求を引用せず論点別に言い換え、補った前提と出所付きの仮説（原因・修正先・手段）を分ける。
  明示がなければ文脈で区分し、問いには答え、提案は評価し、指示は認可内で実行する。
  問い・提案に新たな調査が要るなら、短い確認でも先に解釈・調査内容・見込み（未確定ならその旨）を伝え、調査後に正式に答える。
  確認済みの根拠で足りる問いは一度で答える。
  案を比較し採否か別案がない理由を具体的に示す。
  短答は別案込みで1行にする。
  提案の採否は `playbooks/confirmation.md` に従う。
  変更・是正は `目標の状態:` に変更後の成果物・振る舞いを示す。
  削除・廃止なら対象の不在を示し、拒否・警告で代えない。
  非是正で成果物も変わらない時はこの行を省く。

## 作業と手順書

使い方だけを尋ねられたら `playbooks/usage-guidance.md` で案内し、作業は開始しない。

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

作業と役割は上表で選ぶ。
その中で個々の操作や判断に進む時は、下表の該当する資料を使う。
資料の取得は`agent-basics:writing-standards/references/read-method.md`に従う。
判断を変える前に、発話への応答手順を読み直す。

| 操作・判断 | 資料 |
| --- | --- |
| 確認要否・承認範囲・確認文 | `playbooks/confirmation.md` |
| 発話の理解・解釈、指摘・訂正・割込・中止、WI欄の解釈 | `playbooks/user-utterance.md`「ユーザー発話の解釈」 |
| 権限拒否・手順不成立 | `playbooks/permission-denial.md` |
| 報告・確認・認識合わせの要否と送達 | `references/delivery.md` |
| 進捗・逸脱・改善点・稼働の報告と点検、続行（停止回避・指摘・割込・是正・一時エラー）、再提案・WI回答、委譲結果の確認・中継 | `playbooks/main-reporting.md` |
| 作業契約、追送・工程境界・圧縮後の原本 | `references/work-contract.md`（採用案の維持・縮小・撤回・代替） |
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
| 返答・質問・文章、設計、実装、テスト | `agent-basics:writing-standards`（無ければ文章の基準に限り`references/quality-standards.md`） |
| レビュー・実行確認・検索 | `agent-basics:review-standards`、`agent-basics:check-execution`、`agent-basics:search` |

## 報告項目

成果・確認範囲、依頼外・未達・見送りの理由、本人判断・適用手順、選択を変えた既読原則を報告。
