# session-records.md: セッション記録の構造と集計

Claude CodeとCodexのセッション記録を集計・分析する場合の構造知識を扱う。

Claude Codeの記録は`~/.claude/projects`配下、Codexのロールアウトは
`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`に置かれる
（`CODEX_HOME`が空の場合は`~/.codex`）。
ファイル名の`<thread-id>`はUUIDであり、末尾5区画がthread IDに当たる。
いずれの記録もJSON Linesであり、各レコードの時刻フィールドはどちらも`timestamp`である。

コンパクションの記録はruntimeで形が異なる。
Claude Codeでは`type`が`system`、`subtype`が`compact_boundary`のレコードとして残り、`compactMetadata`が`trigger`・`preTokens`・`postTokens`・`durationMs`を持つ。
Codexでは`type`が`compacted`のレコードとして残り、所要時間の欄を持たない（2026年9月2日に`~/.claude/projects`配下と`~/.codex/sessions`配下の記録で実測した。再検証は同じ2箇所を当該キーで検索する）。

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
- `parentAgentId`は`spawnDepth`が2以上の記録にだけ現れる。深さ1のサブエージェントは親がセッション本体であり当該欄を持たないため、階層を復元する用途では`spawnDepth`を併用する

## 集計値の典拠

セッション記録から集計したトークン量、リクエスト数又は所要時間を成果物へ書く場合と利用者へ提示する場合は、抽出器の出力を典拠とする。
抽出器は`agent-toolkit:session-review`の`session-review/scripts/session_review_evidence.py`とし、`--stats`を付けて実行する。
自作の集計を典拠にしない。
Claude Codeの記録では1回のAPI応答が複数のレコードへ分かれて同じ`usage`を持つため、同一`message.id`の重複を除かずに合算した値は実際の消費量より大きくなる。抽出器は当該重複を最後の`usage`だけへ畳み込んだ値を返す。
`--stats`が返す総量と工程別の内訳は基準が異なる。
総量の`elapsed_seconds`はセッションの最初と最後のレコードの時刻差である。
工程別の`stats-tool`が示す秒はツール呼び出しごとの区間であり、親セッションと委譲先が並行して動く区間は重複して計上される。
このため工程別の合計は総量を超えることがある。
比率を提示する場合は、この基準差を同じ本文へ併記する。
本節の記述は2026年9月3日に`agent-toolkit/skills/session-review/scripts/session_review_evidence.py`の`_latest_claude_usages`と`_stats_summary_data`を読んで確認した。
再検証は同じ2つの関数を読む。
