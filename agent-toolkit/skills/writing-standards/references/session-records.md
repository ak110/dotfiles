# セッション記録の構造と集計

Claude CodeとCodexのセッション記録を集計・分析する場合の構造知識を扱う。セッション記録はClaude CodeとCodexが保存する会話の記録（transcript）を指す。

Claude Codeの記録は`~/.claude/projects`配下、Codexのロールアウトは
`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`に置かれる
（`CODEX_HOME`が空の場合は`~/.codex`）。
ファイル名の`<thread-id>`はUUIDであり、末尾5区画がthread IDに当たる。
いずれの記録もJSON Linesであり、各レコードの時刻フィールドはどちらも`timestamp`である。

コンパクションの記録はruntimeで形が異なる。
Claude Codeでは`type`が`system`、`subtype`が`compact_boundary`のレコードとして残り、`compactMetadata`が`trigger`・`preTokens`・`postTokens`・`durationMs`を持つ。
Codexでは`type`が`compacted`のレコードに、所要時間を除いた情報が残る。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：H1直下：2026年9月2日」にある。

## Claude Codeの記録

会話の要素はエントリの`type`が`user`・`assistant`のものだけで判定する。Claude Code 2.1.301の`api-request-blob`はAPI要求の写しであり、同じ形の`message.content`にツール入力・結果・発話を持つため、形だけで数えると二重計数になる。生の行の本文・構造を調べる照会は写しも表示する。監査記録は`docs/development/audit-records.md`の「セッション記録のAPI要求の写し：2026年10月18日」にある。

- 記録階層は深さ2（`<project>/<session-uuid>.jsonl`、メイン記録）と深さ4（`<session-uuid>/subagents/agent-<agentId>.jsonl`、サブエージェント記録）だけであり、孫エージェントの記録も祖先セッション直下へフラットに置かれる
- `<project>`の名前はセッションを開始した作業ディレクトリの絶対パスの英数字以外を`-`へ置き換えた形であり、worktreeなどで開始したセッションは複製元と別の`<project>`に置かれる。Claude Codeのメインが自身の記録を使う場合は、SessionStartの通知が示すtranscriptの絶対パスをそのまま使い、更新時刻順やcwdから推測しない（`rules-main.claude-code.md`「自身の会話の記録」）。特定済みのセッションIDから別の記録を読む場合は`atk run-script session-review-evidence -- --claude-session-id <ID>`へIDを渡す。所在のパスが要る場合は`find ~/.claude/projects -maxdepth 2 -name '<session-uuid>.jsonl'`で1件に確定する。得たパスは引用して渡す（引用しないグロブ展開が複数語へ分かれると後続のコマンドが標準入力を待ち続ける）
- 同一事象が親と子の記録へ重複して現れるため、重複を除いてから件数を確定する。深さ2に限るとサブエージェント内部のイベント（ツール呼び出し等）を取りこぼすため、限る場合は適用条件を明示する
- サブエージェントの起動を数える典拠は`subagents/*.meta.json`の`agentType`・`description`・`toolUseId`・`spawnDepth`・`parentAgentId`とする。`parentAgentId`は`spawnDepth`が2以上の記録にだけ現れるため、階層の復元には`spawnDepth`を併用する
- 特定の性質を持つ行を抽出する処理では、値がツール自身の出力か、ツールが読み取った外部の内容かを記録の構造から区別する（読み取ったファイルの本文は実行結果と同じ領域に入るため、書式の一致だけで判定すると本文中の例示まで実行時の事象と数える）
- 文面を持つ`thinking`ブロックはツール呼び出しの間の地の文をサーバーが要約したもの（ナレーション要約）を含み、書き手の原文でも拡張思考の全文でもない。その形、長さ、語は原文の性質を数える集計から外す。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：Claude Codeの記録：2026年10月6日」にある

## スキル起動の判定

Claude Codeの記録では、`type`が`user`のレコードの`message.content`にある`tool_result`要素の`content`が`Launching skill: <スキル名>`と完全一致することで判定する。要求側は`type`が`assistant`の`tool_use`要素で、`name`が`Skill`、`input.skill`がスキル名を持つ。スキル名の部分一致は、利用可能なスキルの一覧を持つ`attachment`型のレコードに多数一致する。

Codexの記録にはスキルの起動を一意に示すレコードが無い（`SKILL.md`の読み取りとして実行され、スキル名は一覧、文脈の再掲、パスを含むコマンドの入出力に現れる）。
Codexでは、`type`が`response_item`、`payload.type`が`message`、`payload.role`が`user`のレコードの`content`にある`input_text`要素の`text`が、起動を依頼したプロンプト本文を含むことだけを条件とする。最初のuser役のレコードは実行環境が挿入する前置きである。このプロンプト本文を経ない起動は判定不能として扱う。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：スキル起動の判定：2026年9月10日」にある。

## 集計値の典拠

期間を指定した週ごとの消費量・実行レビューの指標と、セッション別の推定費用は、`atk run-script cost-metrics -- --since <開始日> --until <終了日>`の出力を典拠にする。
月曜0時からのローカル週ごとに、採用AWI件数、全ルートとprocess-wiの4成分のトークン・AWIあたりの値、レビューのラウンド・レベル・修正せずに応答した率を返す。
`--until`省略時は実行時刻まで集計する。
`cost-metrics-coverage`の走査root・期間・最古の読取記録・読取不能件数・未解決委譲件数と、各週の`missing_token_records`を併記し、被覆が欠けた週の値を完全な総量と扱わない。

`cost-metrics-session`は期間内に開始したルートごとに、子孫を含むAPI料金換算のUSD推定額を返す。
Claudeは最新の`type: cost-state`レコードの`totalCostUSD`を優先し、その後の応答と累積額の無い記録を、同一`message.id`の最後のusage・モデルの単価で補う。
親の算出額に含まれるClaudeサブエージェント分を重ねず、別プロセスの委譲先を加える。
内訳の`breakdown.self`はその記録の累積額を含み、`breakdown.claude_subagents`はそこへ含まれない追加分である。
Codexは各`token_count.info.last_token_usage`と直前の`turn_context.model`を使い、キャッシュ読取と推論出力を二重計上しない。
単価不明は`unknown_responses`と`unknown_tokens`へ残し、合計の`estimated_usd`をnull、算出できた部分を`known_usd`とする。
料金表の出所・基準日・区分・算出式とTTL不明時の仮定は、`cost-metrics-coverage.pricing`を併記する。
`atk serve`の費用表示も同じ記録収集と計算を使う。実支払額との区別は「複数セッションの比較結果」に従う。

セッション記録から集計したトークン量、リクエスト数、所要時間を成果物やユーザーへの提示に書く場合は、`atk run-script session-review-evidence -- <引数>`の出力を典拠とし、自作の集計は典拠にしない。
トークン量とリクエスト数には`--stats`、所要時間には`--elapsed-until <ISO 8601の時刻>`を付ける。振り返りの全候補は`--bundle`が生成する`candidates.jsonl`から取り、`atk run-script session-review-prepare`がこれを`candidates.md`へ整形する。

別セッションを含む担当別の費用の要約は`--stats --summary-only`で取得する。総量・親・Claudeサブエージェント・agent threadの要約と由来・未解決診断を保持する表示選択であり、トークンの定義と合算方法は全量表示と同じである。bundleと準備文書は詳細内訳を使うため全量を保持する。

- Claude Codeの記録では1回のAPI応答が同じ`usage`を持つ複数のレコードへ分かれる。同コマンドは同一`message.id`を最後の`usage`へ畳み込んだ値を返す
- `--elapsed-until`の`elapsed_seconds`はセッションの最初のレコードから指定時刻までの差であり、その後の工程を含まない
- `--stats`の総量の`elapsed_seconds`は最初と最後のレコードの時刻差である。工程別の`stats-tool`の秒はツール呼び出しごとの区間で、親と委譲先の並行区間を重複して数えるため、合計が総量を超えることがある。比率を示す場合はこの基準差を併記する

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：集計値の典拠：2026年9月3日」にある。

集計値を判断の根拠に使う前に、始点・終点と数え方が判断に必要な量と一致するかを確かめる。一致しなかった観測例は次のとおり。

- 委譲先の所要時間は`stats-agent-thread`と`stats-subagent`の`turn_elapsed_seconds`で比べる。`elapsed_seconds`はturn完了後に残るプロセスや待機（`after_last_turn_seconds`）を含む
- `agent-toolkit:process-wi`の複数回の実行から現行の工程を評価する場合は、期間内にその工程を定める文書が変わったかを`git log --since=<期間の開始> -- <文書のパス>`などで確かめ、変わっていれば変更後の記録だけを現行の値とする
- 同じファイルを行範囲に分けて取得した回数は1回の読了と数える

## 複数セッションの比較結果

カタログの親セッション行は`--catalog-workflow`の完全一致、`--catalog-known-workflow`のunknown以外、`--catalog-cwd-contains`の部分文字列で選べる。
併記した行条件は全て満たす行を返す。
`--catalog-fields`へ項目名をASCIIカンマで並べると、その順で項目を返し、指定を省くと全項目を返す。
`kind`は行の種類を示すため常に残る。
選択後も`catalog-summary`は走査範囲を保持し、走査した親セッションの記録数の`parent_record_count`と選択した行数の`selected_parent_count`を区別する。
これらの指定はClaudeとCodexの親セッション一覧に共通し、行条件は`--user-events`との併用でも親を選ぶ。
`--catalog-fields`と`--tool-calls`による呼出し照会は、親の行条件と分けて使う。

「最新」「該当なし」「全件」といった全称または終端を表す結論は、走査root、runtime、開始境界、観測境界および未解決記録数を同じ本文へ書き、その被覆範囲だけを対象とする。指定root外の記録、観測境界より後の記録および未解決記録は未確認範囲へ記録する。

WIの処理件数は、成功結果まで記録された直接の`atk wi`操作を数え、操作ごとにセッション識別子と記録行のlocatorを残す。workflow名、cwd、branch、時刻、トークンまたは処理件数を記録から確定できない場合は`unknown`とする。

費用を記載する場合は、請求書または決済記録から確認した実支払額と、API利用量へ公開単価を掛けた見積額を別の値として表示する。見積額には価格表の出所、価格の基準日、入力・キャッシュ書込・キャッシュ読取・出力の区分、および算出式を併記する。契約プランの定額料金は実支払額の区分へ置く。

改善前後の差を因果効果として述べる場合は、変更した変数、比較対象で固定した条件および反実仮想を明示する。期間、runtime、対象workflow、処理したWIの由来または並列度が異なる比較は相関として記載し、変更の効果は追加の対照比較から確定する。

## 本文の検索

セッション記録（ユーザー発話、ツール結果、警告と委譲記録）から本文を取得する場合は`atk run-script session-review-evidence -- <引数>`を使い、記録の指定方法と照会modeの内容は`-- --help`に従って目的で選ぶ。

- 検索語から該当箇所を探す: `--grep`
- 位置が確定している記録の本文を読む: `--detail`
- 複数の固定文字列の一致entry数と全locatorだけを得る: `--fixed-string`を文字列ごとに反復する（正規表現へ結合しない）
- 位置が確定したレコードの構造だけを調べる: `--record-schema`
- ツール呼び出しを呼び出し単位で列挙する: `--tool-calls`（1セッションの発話とツール呼び出しの時系列は`--bundle`が書く`conversation.jsonl`）

件数・判定・保存先だけを読む場合は、次の照会へ`--summary-only`を加える。
必要な詳細は同じ条件で`--summary-only`を外して取得する。

| 照会 | 要約で返す値 |
| --- | --- |
| `--grep` | 記録別と総計の一致entry数（同じentry内の複数の一致行は1件） |
| `--fixed-string` | 文字列別の一致entry数。全locatorの配列を省く |
| `--context-at` | phraseごとの`context-verdict`の全フィールド |
| `--tool-calls` | `tool-call-summary`の全フィールド。カタログとの併用も同じ |
| `--warn` | 抽出・重複除去後の実際の警告イベント数。0件も明示する |
| `--bundle` | `bundle-file`、`bundle-evidence-metrics`、`bundle-kind-count`。全量は保存ファイルで読む |

要約でも由来、未解決記録・委譲、エラーと終了コードを保持する。
統計の要約は「集計値の典拠」を参照する。
他の照会では`--summary-only`を外す。

人間の入力は`--user-events --user-match-fixed <文字列>`か`--user-events --user-match-regex <正規表現>`で検索する。
検索は人間が入力した値へ適用し、確認回答の`assistant_context`は文脈として保持する。
`--with-preceding-assistant`を加えると、直前の同じ記録のアシスタント発話の全文・位置・時刻を`preceding_assistant`へ添える。
該当発話が無い場合の値はnullである。

横断検索はカタログの指定と`--user-events`を併用する。
親の選定にはworkflowとcwdの行条件を使える。
`--catalog-fields`は親一覧で使う。
元記録行の時刻が`--since`より後、`--observation-boundary`以前の入力を、時刻・記録・位置の安定順で件数上限なしに返す。
各一致は全文、`record`・`line`・`sequence`・`timestamp`・`session_id`を持つ。
時刻なし入力は`user-event-undated`、読取不能・未解決は診断で示す。
要約の`parent_record_count`、`selected_parent_count`、`count`は走査した親、選定した親、一致イベントをそれぞれ数える。
一致0件と未確認範囲を区別して読む。

```sh
atk run-script session-review-evidence -- --catalog-claude-project <projectの絶対パス> --since 2026-10-01 --observation-boundary 2026-10-08 --user-events --user-match-fixed <文字列> --with-preceding-assistant
```

Codexではカタログの指定を`--catalog-codex-history <履歴の絶対パス>`へ替える。
単一記録ではカタログの指定を`--transcript <記録の絶対パス>`へ替え、同じ検索と前発話の指定を使える。

通常表示と`--bundle`が書くファイルは、全収集記録の`record-provenance`を返す。warn・grep・fixed-string・tool-calls・hook-notices・statsとbundleの要約は、結果が参照する記録とその起動元の記録の`record-provenance`だけを返す。同イベントの`record`・`role`・`source_record`・`source_line`・`mode`・`role_document`を読む。親を収集していない情報と役割文書が無い値はnullである。
役割別の呼び出し件数と起動した委譲先のmodeは、`--tool-calls`の`tool-call-summary.by_role`（`document:<役割名>`は最初の配送の役割文書、`record:<role>`は役割文書を持たない記録の収集区分）の`count`・`by_tool`・`by_mode`から読む。カタログと併用した場合も収集したroot外の委譲先を含む。

単一transcriptの照会は結果の`kind`を区別できる組なら併用でき、全照会のイベントを返す。`--grep`・`--hook-notices`・`--user-events`は`summary`を共有するため、このうち複数を含む組は個別に実行する。共通の由来と未解決記録は一度だけ返す。不正なlocatorがあれば他の照会の結果も返し、終了コード2で終える。カタログ走査・保存済み発話照会・`--bundle`との組み合わせは入力形式ごとの排他条件に従う。

生成側が返す`保存先:`の絶対パスから全量を読み、出所の引き渡しにも同じファイルを使う。
JSONLファイル群への汎用CLIでの無限定の検索と、記録や`candidates.md`の標準出力への全量表示はしない（行数と1行の長さが入力に依存し、巨大な単一行へ広い正規表現を当てるとマッチングの上限に達する）。同コマンドが受理しない調査には`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」の出力量の判定と分離実行の規定を適用する。

## 暗号化された値

次の値は提供元が暗号化して返す値であり、提供元の公式文書が開いて解釈しないよう定めているため、裏付けと判別には値の有無と所在だけを使い、デコードと内部構造の解析はしない。

- Claude Codeの記録の`thinking`ブロックの`signature`と`redacted_thinking`ブロックの`data`
- Codexの記録で`payload.type`が`reasoning`のレコードの`encrypted_content`

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：暗号化された値：2026年10月7日」にある。

## 進捗更新の判別

公式文書「Thinking」の節「Progress updates between tool calls」の条件で、ツール呼び出しの間の進捗更新（公式文書の「progress update」、Claude Code本体の内部名は`narration`）を判別する。
リクエストの`display`が`"updates"`の応答で文面が空でない`thinking`ブロックを進捗更新とする。
記録は`display`の値を持たないため、設定やホストの送信条件から値を確かめた範囲だけを判別済みとし、残りは判別不能として件数を分ける。この節の観測も前節と同じ監査記録にある。
