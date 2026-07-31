# plan-impl-executor完了報告の受領

`plan-impl-executor`の完了報告とリポジトリ実体を呼び出し元が照合し、
実装からCI通過確認までの完遂状態を確定する。

## 起動前

呼び出し元は計画着手前の`HEAD`を記録する。
構成済みリモートを列挙し、各リモートに対する`git ls-remote --heads --tags <remote>`の
ref名とOIDを委譲前スナップショットとして保存する。

本節を`plan-impl-executor`の呼び出し元側の起動契約の正本とする。
必須入力は計画ファイルと対象リポジトリの作業ディレクトリの絶対パスとする。
同executorの中断作業を継続する場合は、該当系統の経路、識別子、履歴を渡す。
計画作成時の経路、識別子、履歴は実装系統へ引き継がない。
追加指示、変更意図、意図的に許容した挙動変化は存在する場合だけ渡す。
継続情報が無い系統は新しく開始し、追加指示等が無い場合は該当事項なしとして扱う。

`subagent_type: agent-toolkit:plan-impl-executor`を指定し、`model`と`name`を省略する。
`agent-toolkit:plan-mode`からの通常起動は`run_in_background`を省略し、
実際の受領経路を起動結果から判定する。
`agent-toolkit:process-feedbacks`の計画実装型フィードバックから起動する場合は、
`process-feedbacks/references/plan-impl-feedback-flow.md`が定めるbackground経路を適用する。
この分岐は呼び出し元が観測する起動スキルで判定し、受信側の推測へ依存しない。
受信側の解釈は`agent-toolkit/agents/plan-impl-executor.md`の`## 入力`を正本とし、
改訂時はペアで更新する。呼び出し元の読込対象は本referenceに限定する。

## 完了報告の検収

次の全欄について実在と値を検査する。

- `status`
- `summary`
- `changed`
- `verification`
- `commit_sha`
- `review_status`
- `review_final_findings`
- `review_skip_instruction`
- `review_caller_verification`
- `pending_confirmations`
- `plan_gaps`
- `applied_instructions`
- `implementation_thread_id`
- `plan_review_thread_id`
- `independent_review_thread_id`
- `implementation_agent_id`
- `plan_review_agent_id`
- `independent_review_agent_id`
- `implementation_route`
- `plan_review_route`
- `independent_review_route`
- `review_rounds`
- `implementation_history`
- `plan_review_history`
- `independent_review_history`
- `review_resolution`

`status: needs_escalation`では`blockers`も必須とする。
必須欄の欠落または値の矛盾は未完遂として扱い、未完了項目と実測結果へ縮減して再委譲する。

`needs_escalation`は次のように処理する。

- 利用者判断または破壊的操作の確認は、ユーザー回答を同じexecutorへ再入力する
- 技術的に解消できる検証失敗や対象未網羅は、修正指摘を再入力する
- 同じ検証失敗を3回連続で解消できない場合は、呼び出し元が原因調査を引き継ぐ
- 同一プロンプトを再送せず、未完了項目と新しい判断材料へ縮減する

いずれかのrouteが`unavailable`の場合、呼び出し元が不能な系統だけをClaude代替する。
実装・修正系は`plan-codex-implementation.md`と
`plan-codex-implementation-task.md`を用いる。
計画準拠系は`plan-codex-implementation-review.md`と
`plan-codex-implementation-plan-review-task.md`を用いる。
独立系は同じ実行手順referenceと
`plan-codex-implementation-independent-review-task.md`を用いる。
代替応答全文と対象系統をexecutorへ再入力し、実体照合と後続工程を継続させる。

codex routeでは対応するthreadが「なし」以外かつAgent識別子が「なし」、
Claude routeではthreadが「なし」かつAgent識別子が「なし」以外であることを確認する。
受領した系統別Agent識別子は同じ系統名で次回executor入力へ搬送する。
呼び出し元はexecutor配下のAgentへ直接`SendMessage`を実行しない。
2つのレビュー系は同じ`review_rounds`で完了し、各履歴を混在させない。
`review_resolution`の全`P-*`・`I-*`を各レビュー履歴と照合し、
採否または重複先、および採用指摘の修正・再検証結果が1対1で埋まったことを確認する。

通常完了の値は次を満たす。

- `review_status`は`実施完了...`
- `review_final_findings`は`計画準拠系N件・独立系M件`で、NとMは非負整数
- `review_skip_instruction`は`なし`
- `review_caller_verification`は`不要`
- 両レビューrouteは`codex`または`claude`
- `review_rounds`は1〜5
- 両review historyと`review_resolution`は「なし」ではない

ユーザー指示によるレビュー省略は次を満たす。

- `review_status`は`レビューは実施しない（ユーザー指示）`
- `review_final_findings`は`対象外`
- `review_skip_instruction`は計画の`レビュー省略のユーザー指示原文`と完全一致
- `review_caller_verification`は`ユーザー指示原文との照合が必要`
- 両レビューrouteは`not_started`
- 両review thread、両review history、`review_resolution`は「なし」
- `review_rounds`は0

`status: needs_escalation`では`review_status`を`レビュー未完了`とし、
`review_final_findings: 未確定`、`review_skip_instruction: なし`、
`review_caller_verification: 未完了事項の確認が必要`とする。
`not_started`または`unavailable`のrouteに対応するthreadとAgent識別子は「なし」とする。
`plan-file-finalizer`の値との照合は行わない。

## 実体照合

`commit_sha`を実際の`HEAD`と照合する。
`changed`と計画の対象ファイル一覧は次の総差分で1対1確認する。

```text
git diff <計画着手前SHA>..<commit_sha>
```

`git log --reverse --format=%H <計画着手前SHA>..<commit_sha>`でコミット列を確定し、
各コミットを`git show --stat --oneline <sha>`で計画のコミット境界と照合する。
同一ファイルを分割した場合は`git show --patch <sha> -- <path>`も確認する。

`verification`のコマンド、終了コード、警告を実測と照合する。
`review_status`が実施完了の場合は、二系統の同一スナップショット利用、
採用指摘の反映、再検証、レビュー前後の成果物不変性を確認する。
レビュー省略の場合は、計画の`## 実行方法`に同じ明示文字列があることを確認する。
併せて`review_skip_instruction`と計画本文に保存されたユーザー指示原文を照合する。
`レビュー未完了`は完了扱いしない。

## 未完了事項の処理

`pending_confirmations`、`plan_gaps`、`applied_instructions`を入力事実と照合し、
未処理項目を残さない。処理結果は計画ファイルの`## 進捗ログ`へ転記する。

## リモート状態の照合

完了報告の受領後に構成済みリモートと新規追加されたリモートを列挙する。
各リモートへ`git ls-remote --heads --tags <remote>`を再実行し、
委譲前後のref名とOIDを比較する。

意図しない追加、削除、更新を検出した場合は後続工程へ進まない。
リモートrefの削除など破壊的操作を含む場合は、自律モードでもユーザー確認を得る。

## 実体照合と後続工程

実装、検証、コミットと、二系統レビュー完了またはユーザー指示によるレビュー省略の成立後に、
呼び出し元が`git push`とCI通過確認へ進む。
完遂順序は「実装→検証→コミット→二系統レビュー→push→CI通過確認」とする。
委譲先が停滞して呼び出し元が巻き取る場合も、この順序と必須工程を引き継ぐ。

push後のCI通過確認は`agent-toolkit:commit`スキルに従う。
CI完了までの待機はpushを実行した主体が行う。
