# plan-impl-executor完了報告の受領

`plan-impl-executor`の完了報告とリポジトリ実体を呼び出し元が照合し、
実装からCI通過確認までの完遂状態を確定する。

## 起動前

呼び出し元は計画着手前の`HEAD`を記録する。
構成済みリモートを列挙し、各リモートに対する`git ls-remote --heads --tags <remote>`の
ref名とOIDを委譲前スナップショットとして保存する。

計画作成の`threadId`を`plan-impl-executor`へ引き継がない。
実装担当が実装・修正系とレビュー系の`threadId`を自身で新規に開始する。
起動プロンプトは必須引数（計画ファイルの絶対パス・作業ディレクトリの絶対パス・完遂範囲・
完了報告の必須欄）へ限定する。`plan-impl-executor`はHaiku固定のため、
規範本文の引用転記・経路説明・背景説明を載せない。
作業ディレクトリの絶対パスは自己解決せず、受領した値をそのまま渡す。
起動は`name`と`run_in_background`を省略する。
背景実行へ転換された場合も「完了報告の検収」節の手順で検収する。

## 完了報告の検収

次の主要欄を検査する。

- `status`
- `summary`
- `changed`
- `verification`
- `commit_sha`
- `review_handoff`
- `pending_confirmations`
- `plan_gaps`
- `applied_instructions`
- `implementation_thread_id`
- `review_thread_id`
- `implementation_route`
- `review_route`
- `review_rounds`

`status: needs_escalation`の場合は`blockers`も必須とする。
必須欄の欠落は未完遂として扱い、未完了項目と実測結果を縮減したプロンプトで再委譲する。

`status: completed`を確認する。
`needs_escalation`の場合は`blockers`の内容で次のように分岐する。

- ユーザー判断または破壊的操作の確認が必要な場合は、ユーザーへ確認して結果を再委譲する
- 検証失敗や対象未網羅など技術的に解消できる場合は、そのまま修正指摘を再委譲する
- 同じ検証失敗を3回連続で解消できない場合は、呼び出し元が原因調査を引き継ぐ
- 同一プロンプトを再送せず、未完了項目と新しい判断材料へ縮減する

`plan-impl-executor`が`route: unavailable`を理由に`needs_escalation`を返した場合は、
呼び出し元がAgentツールでClaude代替を起動する。
計画実装・修正では`plan-codex-implementation.md`と
`plan-codex-implementation-task.md`を用いる。
実装差分レビューでは`plan-codex-implementation-review.md`と
`plan-codex-implementation-review-task.md`を用いる。
起動プロンプトには用途に対応する統括reference、task reference、計画、品質規範を含める。
そのほかは作業ディレクトリの絶対パス、対象、完了条件だけを含める。
代替応答全文をexecutorへ再入力し、executor自身に実体照合と後続工程を継続させる。

是正指示後は`applied_instructions`で反映状況を確認する。
未反映の指示が残る間は後続工程へ進まない。

実装経路と、レビュー実施時はレビュー経路およびレビューラウンド数を確認する。
codex経路では該当系統の`threadId`が記録され、Claude代替経路では`なし`であり
各回の履歴が完了報告へ含まれることを確認する。
ユーザー指示によるレビュー省略時は、`review_route: not_started`、`review_thread_id: なし`、
`review_rounds: 0`、`review_history: なし`であることを確認する。
`plan-file-finalizer`の値との照合は行わない（計画作成と実装は別コンテキストのため）。

## 実体照合

`commit_sha`を実際の`HEAD`と照合する。
`changed`と計画の対象ファイル一覧は、次のコミット範囲の総差分で1対1確認する。

```text
git diff <計画着手前SHA>..<commit_sha>
```

`git log --reverse --format=%H <計画着手前SHA>..<commit_sha>`でコミット列を確定する。
各コミットを`git show --stat --oneline <sha>`で確認し、計画の想定コミット単位と境界を照合する。
同一ファイルを複数コミットへ分けた場合は、`git show --patch <sha> -- <path>`で実差分も確認する。
各中間`HEAD`に対応する近接検証が`verification`へ記録されていることを確認する。

`verification`のコマンド、終了コード、警告を実測と照合する。
終了コード0だけでなく、警告の解消または許容理由も確認する。
`review_handoff`がレビュー完了を示す場合は、採用指摘の反映と再検証が完了していることを確認する。
`レビューは実施しない（ユーザー指示）`を示す場合は、計画の`## 実行方法`と
照合して同じ記載があることを確認する。
いずれにも該当しない`レビュー未開始`は未完遂として扱う。

## 未完了事項の処理

`pending_confirmations`が非空の場合は、登録済みの確認事項と突き合わせる。
内部実装に閉じる項目は呼び出し元が判断して進捗ログへ記録する。
公開インターフェースへ波及する項目は、協調モードではユーザー回答を反映する。
自律モードでは適用中の規範に従って暫定判断と記録更新を行う。

`plan_gaps`は計画本文へ反映し、必要な実装・検証を完遂する。
`applied_instructions`は追加指示の全件と照合し、未反映項目を残さない。
3欄の処理結果は計画ファイルの`## 進捗ログ`へ転記する。

## リモート状態の照合

完了報告の受領後に構成済みリモートと新規追加されたリモートを列挙する。
各リモートへ`git ls-remote --heads --tags <remote>`を再実行し、
委譲前後のref名とOIDを比較する。

意図しない追加・削除・更新を検出した場合は後続工程へ進まない。
意図した公開先への反映と意図しない公開先の除去まで是正する。
リモートrefの削除など破壊的操作を含む場合は、自律モードでもユーザー確認を得る。

## 実体照合と後続工程

実装・検証・コミットと、レビュー完了またはユーザー指示によるレビュー省略が成立した後に、
呼び出し元が`git push`とCI通過確認へ進む。
完遂順序は「実装→検証→コミット→レビュー→push→CI通過確認」とする。
ユーザー指示によるレビュー省略時は、レビュー工程の位置で省略状態を検収してからpushへ進む。
委譲先が停滞して呼び出し元が巻き取る場合も、この順序と必須工程を引き継ぐ。

push後のCI通過確認は`agent-toolkit:commit`スキルに従う。
CI runが未登録の場合も待機を終了せず、対象コミットに対応するrunの登録を確認する。
失敗を検出した場合はログを取得して原因を解消し、再実行後の通過まで確認する。
CI完了までの待機は`agent-toolkit:shell-exec`へ委譲せず、pushを実行した主体が行う。
