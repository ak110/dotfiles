# 計画ファイルのcodexレビュー

`agent-toolkit:plan-mode`の呼び出し元が、正規計画の機械チェック・修正と総合レビューを
別系統へ委譲するための実行手順を定める。
比較基準、累積差分、採否、反映、限定再レビュー、ラウンド上限は、
`agent-toolkit/skills/plan-mode/references/plan-review-delegation.md`を正本とする。

## 用途別task reference

委譲の用途ごとに次のtask referenceを1件だけ選ぶ。

- 機械チェック・修正: `plan-codex-review-fix-task.md`
- 総合レビュー: `plan-codex-review-task.md`

## 総合レビューの初回委譲

総合レビュー系の資料契約は`plan-codex-review-task.md`の`## 入力`を正本とする。
送信側は同節が要求する絶対パス、作業ディレクトリ、対象、完了条件、
初回または限定再レビューの別、限定再レビュー時の差分一式を渡す。
このほかは呼び出し元が許可した追加指示だけを渡す。
観点、ファイル、文書種別による複数起動は行わない。

Codex経路は、呼び出し元へ返るツール戻り値または完了通知を受領経路とする。
中間のCodexがさらに委譲する場合は、子孫の完了本文を中間層が受領し、自身の完了報告へ含める。
`list_agents`は稼働状態の確認に限って使い、完了本文を取得する手段として扱わない。
完了本文を直接の呼び出し元へ返せる経路を確認できない場合は、その経路を開始しない。

## 機械チェック委譲

実装・修正系へは、本ファイル、機械チェック・修正用の`plan-codex-review-fix-task.md`、
および同task referenceが参照する`plan-codex-implementation-task.md`を渡す。
`agent-toolkit:plan-mode`の呼び出し元が実装・修正系へ渡すタスク本文には、
次の検査、違反修正、再検査を全て含める。

1. 正規計画へ次のコマンドを実行する

   ```text
   uv run --script ${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/scripts/check_plan_file.py \
     --work-dir <対象リポジトリの絶対パス> \
     <正規計画ファイルの絶対パス>
   ```

2. 終了コード1のerror区分違反を全件修正し、終了コード0まで再実行する
3. 終了コード2では、対象パス、読み取り権限、文字エンコーディングを是正して再実行する
4. 終了コード2を解消できない場合は、原因を`escalation_points`へ記録して`needs_escalation`で返す
5. `[warn]`接頭辞付きの出力は内容を確認し、修正するか許容理由を`check_results`へ記録する
6. MCP経由の`run_for_agent`へ正規計画のパスを渡し、`no_fix`と`allow_external_paths`を真として検査する。
   `work_dir`には対象リポジトリを指定する。MCPを利用できない場合は、次のCLI形式を使う

   ```text
   uvx pyfltr run --no-fix --allow-external-paths \
     --work-dir <対象リポジトリの絶対パス> \
     --commands=typos,markdownlint,textlint,designmd,lychee,colloquial-check \
     --enable=colloquial-check \
     <正規計画ファイルの絶対パス>
   ```

7. 違反または警告を正規計画で修正し、終了コード0まで同じコマンドを再実行する
8. 次のコマンドを正規計画へ実行し、検出内容を修正して終了コード0まで再実行する

   ```text
   uv run --script ${CLAUDE_PLUGIN_ROOT}/skills/writing-standards/scripts/check_dash.py <正規計画ファイルの絶対パス>
   ```

9. 修正後に3検査を再実行し、終了コード、error件数、warning件数を`check_results`へ記録する

## 継続

限定再レビューは、同じCodex threadまたは完了本文を受領できる同じAgentを継続する。
`list_agents`で生存を確認できることだけを継続可能の根拠にしない。
同じ経路の完了本文を受領できない場合は、前回応答と確定済み状態を全文で渡して新しい経路を開始する。
