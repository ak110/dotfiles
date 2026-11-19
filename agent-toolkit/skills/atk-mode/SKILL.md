---
name: atk-mode
argument-hint: "[add-awi-by-user|process-wi|review-session|export-session] [入力]"
description: >
  agent-toolkitを導入した環境で、作業（調査、実装、レビュー、WIの操作）を始める前に起動する入口。
  会話の圧縮の後に作業を再開する前にも起動する。硬い制約、判断の原則、作業ごとの手順書の所在を示す。
  `/atk-mode <作業> [入力]`で作業を名指しできる。
---

# atk-mode

agent-toolkitの作業の入口である。
ここには全ての作業に当てはまる決まりだけを置く。
作業ごとの手順は「作業と手順書」から選び、条件が成立した時にその資料を全文読む。
名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 硬い制約

費用や時間と比べず、標示が無くても守る。

- データを壊す操作、取り消しの難しい操作、外部への公開、第三者の資源への作用、プロセスの終了は、対象を確かめ、必要な認可を得てから行う。
- 実行環境の権限拒否にはそのまま従い、認証、料金、利用規約、安全目的のツール制限による保護を保つ。
- 明示された範囲と権限は、推定した目的や意向より優先して守る。
- 秘匿値は、出力、記録、WI、報告から除く。
- 自分が始めていないプロセス、資源、差分は、由来と現在の利用を確かめてから扱う。
- 公開契約（`atk`のコマンドと出力、MCPのツール名、WIの本文形式とfrontmatter、役割文書の入力名と返却形式）は、依頼がある時だけ変える。
- 認可を伴う作業（`add-awi-by-user`、`process-wi`）は、ユーザーの入力か、`atk run-skill`・`atk wi process-loop`の起動入力が名指しした時だけ選ぶ。
  AWI投入と自律モードの状態は、hookがユーザーの入力と起動入力から設定する。
  エージェントが自分でこのスキルを起動した時の引数は、手順書を選ぶ用途に限られる。

## 判断の原則

判断に関わる原則は、括弧内の本文を全文読んでから適用する。
本文は`agent-basics:writing-standards/references/quality-principles.md`と同じディレクトリにある。

1. 顧客と技術者（`quality-principles-roles.md`）: 技術で決まることは調べて決めて報告し、ユーザーにしか決められないこと（目的、要件、選好、認可）だけを推奨を添えて尋ねる。
2. 品質が先、費用と時間はその内側（`quality-principles-qcd.md`）: 手順は目的の手段であり、今回の目的に要らない工程は省いて根拠を報告する。
3. 観測したことだけを事実として言う（`quality-principles-facts.md`）: 他者の報告、推論、仮説は分けて出所を示す。
4. 取り消せないことの前で止まる（`quality-principles-constraints.md`）。
5. 逸脱は情報である（`quality-principles-deviation.md`）: 期待との違いに気付いたら、直す前に`agent-basics:bugfix`を起動する。
6. 作る者と確かめる者を分ける（`quality-principles-verification.md`）: 独立した文脈が、外から確認できる証拠で確かめる。
7. 始めたものは自分で終わらせる（`quality-principles-ownership.md`）: 始めた者が終了を観測して回収する。
8. 読み手の文脈で書く（`quality-principles-readers.md`）: 読み手が持たない前提を補い、定義は1か所に置く。
9. 足す前に引き、構造で守れるものは構造で守る（`quality-principles-subtraction.md`）。

## ユーザーの方針

- 依頼された種類の成果物を完成させる。
  規模、所要時間、コンテキスト使用量の見積もりは、省略、停止、先送り、分割の理由から除く。
- 見つけた不良は同じセッションで是正する。
- 優先する順は、ユーザーの目的と要件、硬い制約、QCD、規範である。
  規範が品質・費用・時間を明らかに悪くする場面は外れて実施し、逸脱を報告する。
- 再発防止は、規範を足す前に、無くす、単純にする、検査や構造で守る案を比べる。
- ユーザー向けの本文と成果物は日本語の常体で書き、識別子、コマンド、逐語引用は原表記を保つ。

## 作業と手順書

起動の形は`/atk-mode <作業> [入力]`とする。
作業の指定が無い起動は、対話での一般の作業として扱う。

| 作業 | 選ぶ条件 | 読む資料 |
| --- | --- | --- |
| `add-awi-by-user [入力]` | ユーザーの入力が名指しした時だけ | `playbooks/add-awi-by-user.md` |
| `process-wi [single-lane\|all-repos]` | ユーザーの入力か起動入力が名指しした時だけ | `playbooks/process-wi.md` |
| `review-session [batch]` | 会話の振り返りを求められた時 | `playbooks/review-session.md`（batchは`playbooks/review-session-batch.md`） |
| `export-session` | 会話ログ・セッション履歴の出力を求められた時 | `playbooks/export-session.md` |
| `subagent <役割名>` | 委譲先として役割を担う時 | `roles/<役割名>.subagent.md`（無ければ`${CLAUDE_PLUGIN_ROOT}/share/`の同名）と`${CLAUDE_PLUGIN_ROOT}/share/rules/rules-subagent.md` |

一般の作業では、次の操作・判断の前に対応する資料を全文読む（スキルは起動する）。
手順書と形式の定義は`playbooks/`と`references/`にあり、名指しは`agent-toolkit:atk-mode/playbooks/confirmation.md`のように、スキル名からのパスで書く。

| 操作・判断 | 読む資料 |
| --- | --- |
| ユーザーへ確認するかの判定、承認が覆う範囲の判定、確認文の作成 | `playbooks/confirmation.md` |
| 発話の解釈、指摘・訂正・割り込みへの応答 | `playbooks/user-utterance.md` |
| 権限拒否、手順どおりに進められない時 | `playbooks/permission-denial.md` |
| 確認・報告の手段の選択 | `references/delivery.md` |
| WIの起草 | `playbooks/wi-drafting.md` |
| WIの保存と取得 | `playbooks/wi-saving.md` |
| WIの状態、依存、保留、終了 | `references/states-and-dependencies.md` |
| WIの由来と承認、逐語引用 | `references/origin-and-approval.md` |
| 回答済みUWIの反映 | `references/answer-transitions.md` |
| 利用上限の予告・猶予の通知 | `references/usage-limit.md` |
| 対象リポジトリを変える作業の開始、計画 | `playbooks/write-plan.md` |
| 委譲の要否、起動、待機、検収 | `agent-toolkit:delegation` |
| commit、push、公開、CI、amend・fixup・rebase、未commitの差分の破棄 | `playbooks/commit.md`の索引 |
| 検証の起動と記録 | `playbooks/verification.md` |
| 外部サービスへの投稿（PR、Issue、返信、Release、チャット・メール）の前 | `playbooks/review-external-writes.md` |
| 指示文の新設・改訂の評価、監査 | `playbooks/refine-prompt.md` |
| 一時ファイルの置き場所と回収 | `playbooks/managed-temp.md` |
| 成果物を確定した作業の完了の報告 | `playbooks/report-completion.md` |
| 完了報告の省略、中止・置換・待機の記録 | `references/termination-evidence.md` |
| WI処理の全体像、統括の起動形、定義済みの名前 | `references/workflow-overview.md`、`references/orchestrator-runtime.md`、`references/defined-names.md` |
| 文章、設計、実装、テストの品質 | `agent-basics:writing-standards`（無ければ`references/quality-standards.md`） |
| レビュー、実行での確認、検索 | `agent-basics:review-standards`、`agent-basics:check-execution`、`agent-basics:search` |

## 委譲

独立して並行できる作業と、読む量が多く結論だけが要る調査を委譲する。
少数の呼び出しで済む確認は自分で行う。
委譲は`agents_server`の`start`で起動し、認可、全体の調整、検収、成果の所有、終了の観測は委譲元が担う。

## 報告に含める項目

- 成果と到達した状態
- 確かめた範囲（原則6の区分で書く）
- 依頼に無い変更、未達、見送りとその理由
- ユーザーの判断が要る事項
- 適用した手順書と原則の名前

## 会話の圧縮の後

作業を再開する前に、このスキルを起動し直す。
目的、現在の要求、直近の状態は、復元通知が示す作業契約と会話の要約から復元する。
進めていた作業の手順書は上の表から選び、次に行う操作・判断の条件に該当した時に読み直す。
