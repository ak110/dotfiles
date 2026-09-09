# rules-main.claude-code.md: Claude Codeのメインエージェントだけに適用する規範

本文書はClaude Codeのメインエージェントだけに適用し、`99-claude-code.md`と同じ拘束力を持つ。

## ツールAPIと権限

- `AskUserQuestion`の1回の呼び出しは`questions`を1件以上4件以下、各質問の`options`を2件以上4件以下とする。
  選択肢1件だけの確認は組めない。
  ユーザーが選択肢を選ばずに記述した回答は、選択肢の`label`ではなく記述本文として返る。
  質問ごとの自由記述は当該質問の回答へ記述本文が入る。
  質問へ答えず一般の返答をした場合は`response`欄へ別に入る。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月2日」にある
- `AskUserQuestion`の呼び出しでは、ターン途中の地の文も直前の地の文もハーネスが要約へ置換することがある。
  置換の有無は実行中のモデルと版に依存し、置換されない位置を事前に確定できない。
  判断材料は`question`本文または`options`の`preview`欄へ自己完結で含め、地の文へ置かない。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年8月31日」にある。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月3日」にある。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月5日」にある
- ユーザーへの質問・承認待ちでターンを終える場合は必ず`AskUserQuestion`を使う（地の文の問いかけのみで終えない。地の文の問いかけはターンの終端を回答待ちとして実行環境へ通知せず、回答を得ないまま工程が終わるため）
- `/goal`が設定されたセッションでは、ターンを終えた時点で当該セッション自身が起動した
  未完了のAgent系タスク又はBash背景ジョブが1件も無い場合に目標評価が発動し、その分のトークンを消費する。
  発動はターンを終えた理由に依存しない。判定の入力は当該セッションが起動したタスクに限り、
  別のセッションが起動したタスクは入らない。
  MCPツールの背景移行は当該延期の対象へ入らないため、その移行だけで待つ場合は評価が発動する。
  技術的に実行できる工程は同じターンで実行してから結果を報告する。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-main.claude-code.md：ツールAPIと権限：2026年9月4日」にある
- 自律実行中は計画立案モードへ遷移せず、計画ファイルを直接作成する。
  同モードの終了時にはユーザーの承認要求が発生するが、自律実行では応答を得られず工程が止まる。
  協調実行では同モードを使ってよい
  （厳守規定。自律実行の進行が承認待ちで停止し、依頼された工程が完了しない）
