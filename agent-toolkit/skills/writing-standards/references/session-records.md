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
  用いる場合は適用条件を明示する
- サブエージェントの起動を1件ずつ数える用途では`subagents/*.meta.json`の
  `agentType`・`description`・`toolUseId`・`spawnDepth`・`parentAgentId`を典拠とする
- `parentAgentId`は`spawnDepth`が2以上の記録にだけ現れる。深さ1のサブエージェントは親がセッション本体でありこの欄を持たないため、階層を復元する用途では`spawnDepth`を併用する
- 記録から特定の性質を持つ行を抽出する処理では、抽出対象の領域を格納場所と次の区別の両方で決める。
  その領域の値が実行したツール自身の出力か、ツールが読み取った外部の内容かを、記録の構造から取得した値で区別する。
  ツールが読み取ったファイルの本文は実行結果と同じ領域に入るため、書式の一致だけで判定すると
  その本文に含まれる例示が実行時の事象として報告される

## スキル起動の判定

Claude Codeの記録では、スキルの起動を`Skill`ツールの結果レコードで判定する。
`type`が`user`のレコードの`message.content`にある`tool_result`要素の`content`が`Launching skill: <スキル名>`と完全一致することを条件とする。
同じ起動の要求側は`type`が`assistant`のレコードの`tool_use`要素であり、`name`が`Skill`、`input.skill`がスキル名を持つ。
判定にはこの完全一致を用いる。スキル名の部分一致による検索では、利用可能なスキルの一覧を持つ`attachment`型のレコードが多数一致するためである。

Codexの記録には、スキルの起動を一意に示すレコードが無い。
Codexはスキルを`SKILL.md`の読み取りとして実行するため、スキル名は利用可能なスキルの一覧、文脈の再掲、及び`SKILL.md`のパスを含むコマンドの入出力へ現れる。
Codexでスキルの起動を判定する場合は、そのスキルの起動を依頼したプロンプト本文の包含だけを条件とする。
判定の対象は、`type`が`response_item`、`payload.type`が`message`、`payload.role`が`user`のレコードの`content`にある`input_text`要素の`text`とする。
最初のuser役のレコードは実行環境が挿入する前置きであるため、判定は本文の包含だけで行う。
このプロンプト本文を経ない起動は判定不能として扱う。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：スキル起動の判定：2026年9月10日」にある。

## 集計値の典拠

セッション記録から集計したトークン量、リクエスト数又は所要時間を成果物へ書く場合と利用者へ提示する場合は、抽出器の出力を典拠とする。
抽出器は`atk run-script session-review-evidence -- <引数>`とする。
振り返りの全候補は同抽出器の`--bundle`が生成する`candidates.jsonl`を正本とし、一次選別結果からの報告生成と構造検査には`atk run-script session-review-report -- <引数>`を用いる。
トークン量とリクエスト数には`--stats`、所要時間には`--elapsed-until <ISO 8601の時刻>`を付けて実行する。
自作の集計は典拠の対象の外に置く。
Claude Codeの記録では1回のAPI応答が複数のレコードへ分かれて同じ`usage`を持つため、同一`message.id`の重複を除かずに合算した値は実際の消費量より大きくなる。抽出器はこの重複を最後の`usage`だけへ畳み込んだ値を返す。
`--elapsed-until`が返す`elapsed_seconds`は、セッションの最初のレコードの時刻から指定した時刻までの差であり、その時刻より後に実施する工程を含まない。
`--stats`が返す総量と工程別の内訳は基準が異なる。
総量の`elapsed_seconds`はセッションの最初と最後のレコードの時刻差であり、抽出した時点より後の工程を含まない。
工程別の`stats-tool`が示す秒はツール呼び出しごとの区間であり、親セッションと委譲先が並行して動く区間は重複して計上される。
このため工程別の合計は総量を超えることがある。
比率を提示する場合は、この基準差を同じ本文へ併記する。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：集計値の典拠：2026年9月3日」にある。

## 複数セッションの比較結果

「最新」「該当なし」「全件」といった全称又は終端を表す結論は、走査root、runtime、開始境界、観測境界及び未解決記録数を同じ本文へ書き、その被覆範囲だけを対象とする。指定root外の記録、観測境界より後の記録及び未解決記録は未確認範囲へ記録する。

WIの処理件数は、成功結果まで記録された直接の`atk wi`操作を数え、操作ごとにセッション識別子と記録行のlocatorを残す。workflow名、cwd、branch、時刻、トークン又は処理件数を記録から確定できない場合は`unknown`とする。

費用を記載する場合は、請求書又は決済記録から確認した実支払額と、API利用量へ公開単価を掛けた見積額を別の値として表示する。見積額には価格表の出所、価格の基準日、認証済み入力とキャッシュ入力などの区分、及び算出式を併記する。契約プランの定額料金は実支払額の区分へ置く。

改善前後の差を因果効果として述べる場合は、変更した変数、比較対象で固定した条件及び反実仮想を明示する。期間、runtime、対象workflow、処理したWIの由来又は並列度が異なる比較は相関として記載し、変更の効果は追加の対照比較から確定する。

## 本文の検索

セッション記録から本文を取得する場合は、`atk run-script session-review-evidence -- <引数>`を用いる。
対象は、Claude CodeとCodexの記録に含まれる利用者発話、ツール結果、警告と委譲記録とする。
Claude Codeの記録はtranscriptの絶対パスを位置引数へ、Codexの記録は`--codex-thread-id <thread ID>`へ渡す。
検索語から該当箇所を探す場合は`--grep <Pythonの正規表現>`、位置が確定している記録の本文を読む場合は`--detail <記録>:<行番号>`、出力を保存する場合は`--output-file <絶対パス>`を付ける。
本節の手段はこの抽出器に限り、検索対象を限定しないJSONLファイル群への汎用CLIによる検索と、セッション記録及び候補一覧の標準出力への全量表示は対象の外に置く。
記録は行数と1行の長さが入力に依存し、巨大な単一行へ広い正規表現を適用すると照合の上限に達するためである。
抽出器が受理しない調査には、`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」の出力量の判定と分離実行の規定を適用する。
