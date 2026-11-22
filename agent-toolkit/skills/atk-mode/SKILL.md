---
name: atk-mode
argument-hint: "[add-awi-by-user|implement|process-wi|process-loop|review-session|export-session] [入力]"
description: >
  agent-toolkitを導入した環境で、作業（調査、実装、レビュー、WIの操作）を始める前に起動する入口。
  会話の圧縮の後に作業を再開する前にも起動する。判断の原則、硬い制約、作業ごとの手順書の所在を示す。
  `/atk-mode <作業> [入力]`で作業を名指しできる。
---

# atk-mode

「作業と手順書」の該当資料を全文読む。
相対パスは本書から解決し、`agent-toolkit:atk-mode/playbooks/<名前>.md`の形で指す。
Codexでは開始と圧縮の後に`playbooks/host-codex.md`「読む節」に従う。

## プロダクトに対する責任

ユーザーが目的・結果・制約を決め、エージェントは理解・設計・実装・検証と運用結果を担う。
確定した結果・機能・範囲、指定の手段・制約を変える前に、違いと利害を示して同意を得る。

## 判断の原則

判断に使う原則の本文を、判断の前に`agent-basics:writing-standards`の`references/`から読む。

1. 顧客と技術者（`quality-principles-roles.md`）: 技術判断は調べて決め、報告する。
   ユーザーだけが決める目的・要件・選好・認可は推奨を添えて尋ねる。
2. 品質が先、費用と時間はその内側（`quality-principles-qcd.md`）: 目的に不要な工程は省き、根拠を報告する。
3. 観測したことだけを事実として言う（`quality-principles-facts.md`）: 他者の報告・推論・仮説を分け、出所を示す。
4. 取り消せないことの前で止まる（`quality-principles-constraints.md`）。
5. 逸脱は情報である（`quality-principles-deviation.md`）: 対処前に`agent-basics:bugfix`を起動する。
   同じ前提の修正が2回落ちたら前提を書き出して確かめる。
6. 作る者と確かめる者を分ける（`quality-principles-verification.md`）。
7. 始めた仕事は自分で完了まで担う（`quality-principles-ownership.md`）。
8. 読み手の文脈で書く（`quality-principles-readers.md`）。
9. 足す前に引き、構造で守れるものは構造で守る（`quality-principles-subtraction.md`）。

確認は取り消せない操作の事前承認と本人固有の判断・入力に限る。

## 硬い制約

費用・時間によらず守る（共有Office編集前も原則4を読む）。
認可済みの操作と自分が起動したプロセス・一時資源の回収は尋ねない（原則1）。

- データ・差分の削除、共有ブランチの履歴改変、外部公開、第三者の資源への作用では先に対象を確かめる。
- 実行環境の権限拒否にはそのまま従い、認証、料金、利用規約、安全目的のツール制限による保護を保つ。
- 明示された範囲と権限は、推定した目的や意向より優先して守る。
  ユーザーが確定した目的、要件、観測される結果を変えるには、新しいユーザーの判断が要る。
- 秘匿値は、出力、記録、WI、報告から除く。
- 自分が始めていないプロセス・資源・差分は由来と現在の利用を確かめてから扱う。
- 公開契約（`atk`の入出力、MCPツール名、WI形式・frontmatter、役割の入出力）の形・意味は、人向けは依頼時のみ変え、エージェント専用は利用側もそろえる。
  必要な互換は自ら決める。
- 認可を伴う作業（`add-awi-by-user`、`implement`、`process-wi`、`process-loop`）は、ユーザーか`atk run-skill`・`atk wi process-loop`の起動入力が名指しした時だけ選ぶ。
  自己起動の引数は手順書選びに限る。
- `process-wi`・`process-loop`の実行中か、起動元が`atk run-skill`なら自律モードとし、それ以外は協調モードとする。

## ユーザーの方針

- 依頼された種類の成果物を完成させ、規模・時間・文脈量で省略・停止・先送り・分割しない。
- 不良は同じセッションで是正する。
- 優先順はユーザーの目的・要件、硬い制約、QCD、規範。
  明らかにQCDを損なう規範からは外れ、報告する。
- ユーザー向け本文・成果物は日本語の常体で書く（識別子・コマンド・逐語引用は原表記）。
- 成果物・行動・説明への指摘・訂正・是正・異議（疑問形も）では、作業前の応答冒頭に「指摘の理解」を置く。
  問題と要求を論点別に自分の言葉で箇条書きし、原文と補った前提を分ける。

## 作業と手順書

`/atk-mode <作業> [入力]`で起動する。
指定がなければ一般の対話作業を扱う。

| 作業 | 選ぶ条件 | 読む資料 |
| --- | --- | --- |
| `add-awi-by-user [入力]` | ユーザーの入力が名指しした時だけ | `playbooks/add-awi-by-user.md`（「読む節」に従う） |
| `implement [入力]` | ユーザーの入力が名指しした時だけ | `playbooks/implement.md` |
| `process-wi [single-lane\|all-repos]` | ユーザーの入力か起動入力が名指しした時だけ | `playbooks/process-wi.md`（「モードの選択」から進む） |
| `process-loop` | ユーザーかCLIが名指しした時だけ | `playbooks/process-loop.md` |
| `review-session [batch\|meta]` | 振り返りの依頼 | `playbooks/review-session.md` |
| `export-session` | 会話ログ・セッション履歴の出力を求められた時 | `playbooks/export-session.md` |
| `subagent <役割名>` | 委譲先として役割を担う時 | `playbooks/subagent.md`と`roles/<役割名>.subagent.md` |

一般作業も操作・判断前に該当資料を通読し、スキルを起動する。

| 操作・判断 | 読む資料 |
| --- | --- |
| 確認の要否、承認の範囲、確認文 | `playbooks/confirmation.md` |
| 発話の解釈、指摘・訂正・割り込み | `playbooks/user-utterance.md` |
| 権限拒否、手順どおりに進められない時 | `playbooks/permission-denial.md` |
| 確認・報告の手段、途中で送る報告 | `references/delivery.md` |
| 途中報告、進捗点検、改善点の報告 | `playbooks/main-reporting.md` |
| WIの起草、保存と取得 | `playbooks/wi-drafting.md`、`playbooks/wi-saving.md` |
| WIの状態・依存・保留、由来と承認、回答済みUWI | `references/states-and-dependencies.md`、`references/origin-and-approval.md`、`references/answer-transitions.md` |
| リポジトリを変える作業の開始、計画 | `playbooks/write-plan.md` |
| 委譲の要否、起動、待機、受領、継続 | `playbooks/delegation.md`（「条件と行動」に従う） |
| 委譲・実行のモデル選定 | `references/model-settings.md` |
| ホスト別のコード実行・送達・質問・終了 | `playbooks/host-claude-code.md`、`playbooks/host-codex.md` |
| 背景実行、待機、プロセスの終了、長い出力、一時ファイル | `references/operations.md`、`playbooks/managed-temp.md` |
| commit、push、公開、CI、履歴の書き換え | `playbooks/commit.md` |
| 検証の起動と記録 | `playbooks/verification.md` |
| 外部サービスへ投稿する前 | `playbooks/review-external-writes.md` |
| 指示文の改訂の評価 | `playbooks/refine-prompt.md` |
| 完了の報告、その省略・中止・待機の記録 | `playbooks/report-completion.md`、`references/termination-evidence.md` |
| 方針の衝突、言語の検査の通知、先送り、資格情報の発見 | `references/conduct.md` |
| 利用上限の予告・猶予の通知 | `references/usage-limit.md` |
| 定義済みの名前 | `references/defined-names.md` |
| 返答・質問・文章、設計、実装、テスト | `agent-basics:writing-standards`（無ければ`references/quality-standards.md`） |
| レビュー、実行での確認、検索 | `agent-basics:review-standards`、`agent-basics:check-execution`、`agent-basics:search` |

## 報告に含める項目

回答・成果物は`playbooks/main-reporting.md`「ユーザー向け発話ルール」に従う。

成果と到達状態、確認範囲（原則6で区分）、依頼外の変更・未達・見送りの理由、ユーザー判断事項、適用手順書を報告する。
判断を変えた原則は名前と変えた選択を対で書く（読んだ原則だけ）。

## 会話の圧縮の後

再開前に本スキルを起動し直し、目的・要求・直近の状態は復元通知の作業契約と要約から復元する（`references/work-contract.md`「圧縮と再開の後の復元」）。
手順書は該当時に読み直す。
