# plan-impl-executor完了報告の受領

`plan-impl-executor`の完了報告とリポジトリ実体を呼び出し元が照合し、
実装からCI通過確認までの完遂状態を確定する。

## 起動前

呼び出し元は計画着手前の`HEAD`を記録する。
構成済みリモートを列挙し、各リモートに対する`git ls-remote --heads --tags <remote>`の
ref名とOIDを委譲前スナップショットとして保存する。

計画作成の`threadId`を`plan-impl-executor`へ引き継がない。
実装担当が実装・修正系、計画準拠実装レビュー系、独立実装レビュー系の
`threadId`を自身で新規に開始する。
起動プロンプトは必須引数へ限定し、規範本文、経路説明、背景説明を載せない。
作業ディレクトリの絶対パスは受領した値をそのまま渡す。
起動は`name`と`run_in_background`を省略する。

## 完了報告の検収

次の主要欄を検査する。

- `status`
- `summary`
- `changed`
- `verification`
- `commit_sha`
- `review_status`
- `pending_confirmations`
- `plan_gaps`
- `applied_instructions`
- `implementation_thread_id`
- `plan_review_thread_id`
- `independent_review_thread_id`
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

codex routeでは対応するthreadが「なし」以外、Claude routeでは「なし」であることを確認する。
2つのレビュー系は同じ`review_rounds`で完了し、各履歴を混在させない。
`review_resolution`の全`P-*`・`I-*`を各レビュー履歴と照合し、
採否または重複先、および採用指摘の修正・再検証結果が1対1で埋まったことを確認する。

通常完了の値は次を満たす。

- `review_status`は`実施完了...`
- 両レビューrouteは`codex`または`claude`
- `review_rounds`は1〜5
- 両review historyと`review_resolution`は「なし」ではない

ユーザー指示によるレビュー省略は次を満たす。

- `review_status`は`レビューは実施しない（ユーザー指示）`
- 両レビューrouteは`not_started`
- 両review thread、両review history、`review_resolution`は「なし」
- `review_rounds`は0

`status: needs_escalation`では`review_status`を`レビュー未完了`とし、
`not_started`または`unavailable`のrouteに対応するthreadは「なし」とする。
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
