# session-records.md: セッション記録の構造と集計

Claude CodeとCodexのセッション記録を集計・分析する場合の構造知識を扱う。

Claude Codeの記録は`~/.claude/projects`配下、Codexのロールアウトは
`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`に置かれる
（`CODEX_HOME`が空の場合は`~/.codex`）。
ファイル名の`<thread-id>`はUUIDであり、末尾5区画がthread IDに当たる。
いずれの記録もJSON Linesであり、各レコードの時刻フィールドはどちらも`timestamp`である。

コンパクションの記録はruntimeで形が異なる。
Claude Codeでは`type`が`system`、`subtype`が`compact_boundary`のレコードとして残り、`compactMetadata`が`trigger`・`preTokens`・`postTokens`・`durationMs`を持つ。
Codexでは`type`が`compacted`のレコードとして残り、所要時間の欄を持たない。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：H1直下：2026年9月2日」にある。

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
- 記録から特定の性質を持つ行を抽出する処理では、抽出対象の領域を格納場所だけで決めない。
  当該領域の値が実行したツール自身の出力か、ツールが読み取った外部の内容かを、記録の構造から取得した値で区別する。
  ツールが読み取ったファイルの本文は実行結果と同じ領域に入るため、書式の一致だけで判定すると
  当該本文に含まれる例示が実行時の事象として報告される

## 集計値の典拠

セッション記録から集計したトークン量、リクエスト数又は所要時間を成果物へ書く場合と利用者へ提示する場合は、抽出器の出力を典拠とする。
抽出器は`agent-toolkit:session-review`の`session-review/scripts/session_review_evidence.py`とする。
トークン量とリクエスト数には`--stats`、所要時間には`--elapsed-until <ISO 8601の時刻>`を付けて実行する。
自作の集計を典拠にしない。
Claude Codeの記録では1回のAPI応答が複数のレコードへ分かれて同じ`usage`を持つため、同一`message.id`の重複を除かずに合算した値は実際の消費量より大きくなる。抽出器は当該重複を最後の`usage`だけへ畳み込んだ値を返す。
`--elapsed-until`が返す`elapsed_seconds`は、セッションの最初のレコードの時刻から指定した時刻までの差であり、当該時刻より後に実施する工程を含まない。
`--stats`が返す総量と工程別の内訳は基準が異なる。
総量の`elapsed_seconds`はセッションの最初と最後のレコードの時刻差であり、抽出した時点より後の工程を含まない。
工程別の`stats-tool`が示す秒はツール呼び出しごとの区間であり、親セッションと委譲先が並行して動く区間は重複して計上される。
このため工程別の合計は総量を超えることがある。
比率を提示する場合は、この基準差を同じ本文へ併記する。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：集計値の典拠：2026年9月3日」にある。

## 本文の検索

セッション記録から本文を取得する場合は、`agent-toolkit:session-review`の`session-review/scripts/session_review_evidence.py`を用いる。
対象は、Claude CodeとCodexの記録に含まれる利用者発話、ツール結果、警告と委譲記録とする。
Claude Codeの記録はtranscriptの絶対パスを位置引数へ、Codexの記録は`--codex-thread-id <thread ID>`へ渡す。
検索語から該当箇所を探す場合は`--grep <Pythonの正規表現>`、位置が確定している記録の本文を読む場合は`--detail <記録>:<行番号>`、出力を保存する場合は`--output-file <絶対パス>`を付ける。
検索対象を限定しないJSONLファイル群への汎用CLIによる検索と、セッション記録及び候補一覧の標準出力への全量表示は、本節の経路にしない。
記録は行数と1行の長さが入力に依存し、巨大な単一行へ広い正規表現を適用すると照合の上限に達するためである。
抽出器が受理しない調査には、`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」の出力量の判定と分離実行の規定を適用する。
