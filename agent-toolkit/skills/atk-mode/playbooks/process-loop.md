# 登録リポジトリを巡回する

ユーザーか `atk wi process-loop` が名指しした時だけ使う。
CLIは起動・再起動と制御、セッションはWIの処理と更新を担う。

## 巡回する

1. `atk config repos list` で登録した対象のパス、系統、直接編集と検証の条件を確かめる。
   全体作業契約には目的、現行要求と保つ対象だけを書き、再開した場合は既存の検証・公開の結果を読み戻す。
2. キューを同期し、開始時に処理可能なWIを原則すべて処理候補にする。
   必要なら `atk wi check-alerts` でCIの失敗を別に確認する。
   進められる対象が1つも無ければ、常駐では60秒待って手順2に戻り、単回実行では `atk agents-exit-session` で終了する。
3. `agent-toolkit:atk-mode/playbooks/process-wi.md` で対象ごとの設定・認可と候補を確認し、選定、専用worktree、実行レビュー、統合、公開を進める。
   複数対象の配分と担当への受け渡しは `agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md` を使う。
   登録元checkoutは直接編集せず、対象と系統ごとの専用作業ツリーから各レーンを作る。
4. 主な公開・リリースが終わったら `update-dotfiles` を実行し、出力と終了状態を確認する。
   単発の `review-session` は終了条件にしない。
   既存のbatch待ちがあれば追加の振り返りは非同期の `review-session batch` に任せ、無ければ既知の対応事項だけを必要に応じてAWIに残す。
   更新に失敗したら、`agent-basics:bugfix` を起動してその場で対処し、結果を確かめてから反映へ進む。
   他の不良の処置時機は `agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「即時対応」で決める。
5. 更新に成功したら、Claude Codeでは必要なpluginやMCPを `apply_runtime_changes` で反映する。
   同じセッションでの圧縮が必要なら、途中の作業を保存し、`compact_conversation` の `resume` を指定して現ターンを終える。
   本体側の更新を読むか区切りで新しい会話を使う時は、残工程を記録し `atk agents-exit-session` で正常終了する。
   CodexとAntigravityでは自動反映と自動再開を前提にせず、必要な場合は手動で再起動する。

## 中断と再開

- ユーザーの中断要求を受けたら、新たな対象の選定を止め、書込担当と公開の結果を回収して、未達の工程を記録する。
- 再開時は候補・認可・検証・操作結果を読み戻し、成功した公開と保存を再利用して未完了工程から進める。
- 追加指示、中断、再開の予約は `atk wi process-loop --help` を参照する。
