# session-records.md: セッション記録の構造と集計

Claude CodeとCodexのセッション記録を集計・分析する場合の構造知識を扱う。

Claude Codeの記録は`~/.claude/projects`配下、Codexのロールアウトは
`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`に置かれる
（`CODEX_HOME`が空の場合は`~/.codex`）。
ファイル名の`<thread-id>`はUUIDであり、末尾5区画がthread IDに当たる。
いずれの記録もJSON Linesであり、各レコードの時刻フィールドはどちらも`timestamp`である。

## Claude Codeの記録

- 記録階層は深さ2（`<project>/<session-uuid>.jsonl`、セッション本体）と
  深さ4（`<session-uuid>/subagents/agent-<agentId>.jsonl`、サブエージェント記録）の2値のみである。
  孫エージェントの記録も祖先セッション直下へフラット格納されるため、深さは常に4である
- 同一事象が親セッションと子セッションの記録へ重複して現れる構造を先に確認し、
  重複を除外したうえで件数を確定する
- セッション単位の事象（起動・完了報告等）を数える用途では深さ2限定が有効だが、
  サブエージェント内部のイベント（ツール呼び出し等）は深さ2限定では取りこぼす。
  適用条件を明示せず用いない
- サブエージェントの起動を1件ずつ数える用途では`subagents/*.meta.json`の
  `agentType`・`description`・`toolUseId`・`spawnDepth`・`parentAgentId`を典拠とする
