# セッション記録の形式と照会

本書は、Claude Code、Codex、OpenCode、Antigravityが保存する会話の記録（セッション記録）を集計・照会する時の、記録の形式、集計値の典拠、照会の選び方を定める。
件数、不在、全件、所要時間を述べる時は、原則3（`agent-basics:writing-standards/references/quality-principles-facts.md`）に従い、照会した範囲と未確認の範囲を分けて書く。

| ホスト | 記録の所在 | 照会での指定 |
| --- | --- | --- |
| Claude Code | `~/.claude/projects`配下（本書「Claude Codeの記録」） | `--claude-session-id <ID>`か`--transcript <絶対パス>` |
| Codex | `<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`（`CODEX_HOME`が未設定なら`~/.codex`） | `--codex-thread-id <thread ID>` |
| OpenCode | `XDG_DATA_HOME`（未設定なら`~/.local/share`）の`opencode/opencode.db`にあるsession・message・partの表 | `--opencode-session-id <ses_で始まるID>`（別のDBは`--opencode-db`） |
| Antigravity | `~/.gemini/antigravity-cli/brain/<session_id>/.system_generated/logs/`の`transcript_full.jsonl`か`transcript.jsonl` | `agents_server`が起動した会話は`atk agents logs <session_id>`（Antigravityを直接起動した会話のDBは対象外） |

`agents_server`が起動した委譲先は、どの実行エンジンでも`atk run-script session-review-prepare -- --agents-session-id <session_id>`が登録簿から記録を解決する。
Claude CodeとCodexの記録はJSON Linesで、各レコードの時刻の欄は共通して`timestamp`である。
Codexのファイル名に含まれる`<thread-id>`はUUIDであり、末尾の5区画がthread IDに該当する。
OpenCodeの記録は読み取り専用で取得され、照会結果の`line`はmessageとpartの順から得た論理位置で、元の表と行IDは`opencode_source`に残る。

コンパクション（会話圧縮）の記録形式は、実行環境（runtime）によって異なる。
Claude Codeでは`type`が`system`、`subtype`が`compact_boundary`のレコードとして記録され、`compactMetadata`の中に`trigger`、`preTokens`、`postTokens`、`durationMs`が格納される。
Codexでは`type`が`compacted`のレコードに、所要時間を除いた情報が記録される。
監査記録は`docs/development/audit-records-session-records.md`の「agent-toolkit/skills/atk-mode/references/session-records.md：H1直下：2026年9月2日」に記載されている。

## Claude Codeの記録

会話の要素は、レコードの`type`が`user`か`assistant`のものだけで判定する。
Claude Code 2.1.301にある`api-request-blob`はAPIリクエストの控え（コピー）であり、同じ形式の`message.content`にツールの入力、実行結果、発話を含むため、形式の一致だけで数えると二重に数える。
生のレコードの本文や構造を調べる照会では、この控えも表示される。
監査記録は`docs/development/audit-records-session-records.md`の「セッション記録のAPI要求の写し：2026年10月18日」に記載されている。

- 記録は、メイン記録の深さ2（`<project>/<session-uuid>.jsonl`）と、サブエージェント記録の深さ4（`<session-uuid>/subagents/agent-<agentId>.jsonl`）にだけある。
  孫エージェントの記録も、祖先のセッションの直下の`subagents/`にフラットに置かれる。
- `<project>`は、セッションを開始した作業ディレクトリの絶対パスの英数字以外を`-`に置き換えた名前である。
  worktreeなど別のディレクトリで開始したセッションは、複製元とは別の`<project>`に置かれる。
- 自身の記録は、更新時刻の順やcwdから推測せず、SessionStartの通知が示すtranscriptの絶対パスをそのまま使う。
  特定済みのセッションIDから別の記録を読む時は、`atk run-script session-review-evidence -- --claude-session-id <ID>`へIDを渡す。
  ファイルパスそのものが必要な時は`find ~/.claude/projects -maxdepth 2 -name '<session-uuid>.jsonl'`で1件に確定し、取得したパスはクォートして渡す（グロブ展開で複数の語に分かれると、後続のコマンドが標準入力を待ち続ける）。
- 親と子の記録には同じ事象が重複して現れるため、件数を確定する時は重複を除く。
  深さ2のメイン記録だけに限るとサブエージェント内部のイベント（ツール呼び出しなど）を取りこぼすため、限る時はその条件を明記する。
- サブエージェントの起動回数の典拠は、`subagents/*.meta.json`の`agentType`、`description`、`toolUseId`、`spawnDepth`、`parentAgentId`である。
  `parentAgentId`は`spawnDepth`が2以上の記録にだけ現れるため、階層の復元では`spawnDepth`も使う。
- ツールが読み取ったファイルの本文は実行結果と同じ領域に入るため、特定の性質を持つ行の抽出では、ツール自身の出力か読み取ったファイルの内容かをレコードの構造で区別する（書式の一致だけでは、ファイル内の例示まで実行時のイベントとして数える）。
- 文面を持つ`thinking`ブロックは、ツール呼び出しの合間の地の文をサーバー側で要約したもの（ナレーション要約）であり、エージェントが書いた原文とも拡張思考の全文とも別のものである。
  原文の性質（形状、長さ、使う語）を測る集計からは除く。
  監査記録は`docs/development/audit-records-session-records.md`にある。
  該当節は「agent-toolkit/skills/atk-mode/references/session-records.md：Claude Codeの記録：2026年10月6日」である。

## スキル起動の判定

Claude Codeの記録では、`type`が`user`のレコードの`message.content`にある`tool_result`要素の`content`が、`Launching skill: <スキル名>`と完全一致するかで判定する。
呼び出し側は`type`が`assistant`の`tool_use`要素で、`name`が`Skill`、`input.skill`にスキル名、`input.args`に引数が入る。
`agent-toolkit:atk-mode`をSkillで起動した記録では、作業の名前（`review-session`など）は`input.args`にある。
スキル名の部分一致で探すと、利用できるスキルの一覧を持つ`attachment`型のレコードに多数誤って一致する。

Codexの記録には、スキルの起動を一意に示す専用のレコードが無い（`SKILL.md`の読み込みとして実行されるため、スキル名は一覧の表示、文脈の再掲、パスを含むコマンドの入出力など随所に現れる）。
Codexでは、`type`が`response_item`、`payload.type`が`message`、`payload.role`が`user`のレコードの`content`にある`input_text`要素の`text`に、スキルの起動を依頼したプロンプトの本文が含まれることだけを判定の条件とする。
最初のuserレコードは実行環境が挿入する前置きの文である。
このプロンプトの本文を経由しない起動は、判定不能として扱う。
監査記録は`docs/development/audit-records-session-records.md`にある。
該当節は「agent-toolkit/skills/atk-mode/references/session-records.md：スキル起動の判定：2026年9月10日」である。

## 暗号化された値

次の値は提供元が暗号化して返すデータであり、提供元の公式ドキュメントが解釈しないよう明記している。
事実の確認や判別で確かめる対象は値の有無と所在に限り、デコードと内部構造の解析は対象外とする。

- Claude Codeの記録にある`thinking`ブロックの`signature`と、`redacted_thinking`ブロックの`data`
- Codexの記録で`payload.type`が`reasoning`のレコードの`encrypted_content`

監査記録は`docs/development/audit-records-session-records.md`の「agent-toolkit/skills/atk-mode/references/session-records.md：暗号化された値：2026年10月7日」に記載されている。

## 進捗更新の判別

公式ドキュメント「Thinking」の「Progress updates between tool calls」節の基準で、ツール呼び出しの間の進捗更新（公式ドキュメントの「progress update」、Claude Code内部の名前は`narration`）を判別する。
APIリクエストの`display`パラメーターが`"updates"`の応答のうち、本文が空でない`thinking`ブロックを持つものを進捗更新とみなす。
記録ファイルには`display`の値が残らないため、設定内容やホストの送信条件から値を確かめられた範囲だけを判別済みとし、残りは判別不能として件数を分けて数える。
この節の観測結果についても、前節と同じ監査記録に記載されている。

## 集計値の典拠

### 週ごとの指標と推定費用

期間を指定した週ごとのトークン消費量、実行レビューの指標、セッションごとの推定費用は、`atk run-script cost-metrics -- --since <開始日> --until <終了日>`の出力を典拠とする（`--until`を省くと実行時刻まで）。
週は月曜0時を起点とするローカルの週で、採用されたAWIの件数、全ルートとprocess-wiの4成分のトークン量とAWIあたりの値、レビューのラウンド数、レベル、修正なしで応答した比率を返す。
出力の`cost-metrics-coverage`（走査ルート、集計期間、最古の読み取り記録、読み取り不能の件数、未解決の委譲の件数）と各週の`missing_token_records`を併記し、網羅に欠けがある週の値は、欠けた範囲を添えた部分の総量として扱う。

`cost-metrics-session`は、期間内に開始したルートセッションごとに、子孫を含むAPI料金換算の推定額（USD）を返す。
料金を二重に数えないよう、次の典拠と合算を使う。

- Claudeは最新の`type: cost-state`レコードの`totalCostUSD`を優先し、その後の応答と、累積額の記録が無いセッションだけを、同じ`message.id`の最後の`usage`とモデルの単価で補う。
- 親の算出額に含まれるClaudeサブエージェントの費用は加えず、別プロセスで起動された委譲先の費用を加える。
  内訳の`breakdown.self`はその記録自身の累積額、`breakdown.claude_subagents`はそこに含まれない追加分、`breakdown.delegates`は別プロセスの委譲先の追加分である。
- Codexは各`token_count.info.last_token_usage`と直前の`turn_context.model`を使い、キャッシュ読み取りと推論出力はそれぞれ1回だけ数える。
- OpenCodeは`step_finish`のpartが持つ`tokens`（`input`、`output`、`cache.read`、`cache.write`）を使う。
- 単価が不明な応答（単価表に無いモデルと、モデル名を持たないOpenCodeの応答）は`unknown_responses`と`unknown_tokens`に残り、合計の`estimated_usd`はnull、算出できた範囲は`known_usd`になる。
  `unknown_models`はこれらの応答をモデル名ごとに数える。
  モデル名が記録されていない応答のキーは空文字列になる。
- 料金表の出所、基準日、区分、計算式、TTL不明時の仮定は、`cost-metrics-coverage.pricing`の内容を併記する。

`atk serve`の費用の表示も同じ記録の収集と計算を使う。
実支払額との区別は本書「複数セッションの比較結果」に従う。

### 初回品質とレビューの指標

段階別の初回品質は、レビュー、最終検証、公開CIを別の母集団とし、初回の正式な合否を観測できた論理作業単位（明示された1つの管理表、無ければ正式な初回と前回HEADの連鎖）のうち初回に合格した割合で比べる。
期間、対象workflow、版、環境、分母、合格数、不明、未実行、未解決を併記し、欠けた指摘表や空の指摘表は合格へ補わず不明として残す。
再判定数、修正版数、範囲変更数は元の記録と比べるための観測値で、初回品質の`elapsed_seconds`は最初と最後の正式結果の時刻差（修正作業だけの所要時間ではない）である。
正当な範囲の追加や環境の差と、初回の不足を分ける。
合格率が低い時は実装の不足と誤った指摘を比べ、指摘0件が続く時は差分、契約の被覆、証拠、独立性を確かめ、代表の評価入力で欠陥を検出できるかを確かめる（指摘数はノルマにしない）。

レビューの指標は実際のレビュー指摘を対象とし、明示的な実装中の判断記録を除く（除外は`excluded_judgment_count`と`excluded_judgments`の`table`・1始まりの`row`で追跡でき、採否集計の`review-adoption`も同じ分類を使う）。
移管先を明示した行は`transferred`として採用、不採用、未応答と別に数え、修正なしで応答した比率の分子から除く。
不採用の理由の区分は`判断主体:`を典拠とし、ラベルの無い旧記録は自由文から推定せず`判断主体不明`とする。
`cost-metrics-hook-notice`は、ルートと全委譲先のhook通知をルートの開始週へ帰属させ、`week`、`runtime`、`hook`、`hook_name`、`tag`、`kind_text`ごとの`count`を返す。
分類は`session-review-evidence --hook-notices`と同じである。

### 証拠抽出の集計値

セッション記録から集計したトークン量、リクエスト数、所要時間を成果物やユーザー向けの文面に書く時は、`atk run-script session-review-evidence -- <引数>`の出力を典拠とする（独自の集計は根拠から除く）。
トークン量とリクエスト数は`--stats`、所要時間は`--elapsed-until <ISO 8601形式の時刻>`で取得する。
別セッションを含む担当別の費用の要約は`--stats --summary-only`で取得する（トークンの定義と合算は全量の表示と同じ）。
振り返りの対象候補は`--bundle`が生成する`candidates.jsonl`から取り、`atk run-script session-review-prepare`が`candidates.md`へ整形する。
準備の文書は初回の確認に必要な索引、総量、上位、詳細の照会先だけを載せ、全量は`bundle/`に残る。
全候補ID、必須性、詳細の対応は`bundle/review-index.jsonl`、担当別・対象別の全量は`bundle/stats.jsonl`と`bundle/resource-usage.jsonl`から、候補の選別、原因の特定、費用の確認に必要な範囲だけを取得する。

時間とトークンを二重に数えないよう、次の違いを守る。

- Claude Codeの記録では、1回のAPI応答が同じ`usage`を持つ複数のレコードに分かれて現れることがあり、上のコマンドは同じ`message.id`の最後の`usage`に集約した値を返す。
- `--elapsed-until`の`elapsed_seconds`は、セッションの最初のレコードから指定時刻までの経過であり、指定時刻より後の工程は範囲の外にある。
- `--stats`の総量の`elapsed_seconds`は、最初と最後のレコードの時刻差である。
  工程ごとの`stats-tool`の秒数はツール呼び出しごとの実行時間で、親と委譲先が並行した区間は重なって加算されるため、合計が総量を超えることがある。
  比率を示す時は、この基準の違いを併記する。
- 巡回のレーンの工程時間は、引き継ぎ記録、所有登録、検証記録（旧形式は計画の進捗ログも）の時刻差を使い、セッション統計の値と区別して時刻の出所と区間を残す。

監査記録は`docs/development/audit-records-session-records.md`の「agent-toolkit/skills/atk-mode/references/session-records.md：集計値の典拠：2026年9月3日」に記載されている。

集計値を判断の根拠に使う前に、計測の始点、終点、数え方が判断の観点と一致するかを確かめる。
過去に不一致を観測した例を次に示す。

- 委譲先の所要時間は、`stats-agent-thread`か`stats-subagent`の`turn_elapsed_seconds`で比べる。
  `elapsed_seconds`はターンの完了後にプロセスが残った時間や待機（`after_last_turn_seconds`）を含む。
- `agent-toolkit:atk-mode`の作業`process-wi`の複数回の実行から現行の工程の性能を評価する時は、集計期間中に工程を定める文書が改訂されたかを`git log --since=<期間の開始> -- <文書のパス>`などで確かめ、改訂後の記録だけを現行の値として扱う。
- 同じファイルを行範囲に分けて複数回読んだ取得は、全体で1回の読了として数える。

## 複数セッションの比較結果

複数の親セッションの一覧（カタログ）は、`--catalog-claude-project <DIR>`か`--catalog-codex-history <DIR>`に`--since`と`--observation-boundary`を付けて取る。
親の行の条件と表示項目は`-- --help`の`--catalog-*`から選ぶ。
カタログに`--grep <正規表現>`か`--user-events`を併せると、期間内の本文と入力を記録横断で検索でき、結果の`record`と`line`から単一記録の`--detail`へ進む。
要約の`parent_record_count`（走査した親）、`selected_parent_count`（条件で選んだ親）、一致の件数を分けて読み、一致0件と、調査が及んでいない未確認の範囲を区別する。

「最新」「該当なし」「全件」のように全体や終端を表す結論は、走査ルート、実行環境（runtime）、開始境界、観測境界、未解決のレコード数を同じ文面に書き、その範囲だけについて述べる。
指定ルート外の記録、観測境界より後の記録、未解決の記録は、未確認の範囲として書く。

- 時刻を確定できない一致は`undated_match_count`（一致エントリ数とは別の一致行数）へ返り、期間の内外は未確認として扱う。
- カタログの`--grep`の診断の`scope=scan-root`は全走査root、`scope=selected-family`は選んだ親の家族の未確認範囲を示す。
  `unresolved_record_count`は`scan_root_unresolved_record_count`と`query_unresolved_record_count`の合併である。
- `reason=not-found-in-root`（指定root内に無い）と`reason=record-location-unresolved`（家族収集でも所在を取れない）は削除済みや恒久の欠損の証拠ではなく、参照元が期間外でも未取得の参照先が期間外とは判定できない。
  読み込みの失敗は`reason`と`failure_line`（読めなかった行）で、参照元は`source_record`と`source_line`で区別する。
- 補助の広域走査で取れなかった範囲は限界として報告でき、要求、認可、採否、原因の判断に欠かせない原記録の不足とは分ける。

WIの処理件数は、成功の結果まで記録された直接の`atk wi`操作だけを数え、操作ごとにセッションIDと記録位置を残す。
workflow名、作業ディレクトリ（cwd）、ブランチ名、時刻、トークン量、処理件数のうち記録から特定できない項目は`unknown`とする。

費用は、請求書や決済記録で確かめた実支払額と、API利用量に公開単価を掛けた見積額を別の値として示す。
見積額には価格表の出所、価格の基準日、入力・キャッシュ書き込み・キャッシュ読み取り・出力の区分、計算式を併記し、契約プランの定額料金は実支払額の区分に入れる。

施策の前後の差を因果による効果として主張する時は、変えた変数、比較で固定した条件、反実仮想（施策をしなかった場合の想定）を示す。
集計期間、実行環境、対象workflow、処理したWIの由来、並列度が違う比較は相関として書き、施策の効果は対照の比較で確定する。

## 本文の検索

セッション記録の本文（ユーザーの発話、ツールの実行結果、警告、委譲の記録など）は`atk run-script session-review-evidence -- <引数>`で照会し、記録の指定と照会の種類は`-- --help`から目的に合うものを選ぶ。
結果の全量は生成側が示した`保存先:`の絶対パスから読み、出所の証拠を後続へ渡す時もそのファイルを使う。
JSON Linesの記録への無制限の汎用検索と、記録や`candidates.md`の標準出力への全量表示はしない（1行の長さは入力しだいで、極端に長い1行へ広い正規表現を当てると正規表現の処理が上限に達する）。
このコマンドで扱えない調査は、`agent-toolkit:atk-mode/references/operations.md`「ツール・コマンド運用」の出力量の判定と分離実行の規定に従う。

| 目的 | 照会 |
| --- | --- |
| 語句から該当箇所を探す | `--grep <正規表現>` |
| 複数の固定文字列ごとに一致エントリ数と全位置だけを得る | 文字列ごとに`--fixed-string <文字列>`を個別に指定する（1つの正規表現へまとめない） |
| 位置が分かっている記録の本文を読む | `--detail <record>:<line>` |
| 位置が分かっているレコードの構造だけを調べる | `--record-schema <record>:<line>` |
| 記録済みの改善点を出所付きで全行得る | `--reported-improvements`（同文の別出所と1発話の複数行も残る） |
| 特定のツールの入力に限定する | `--tool-calls --tool <名前> --input-regex <正規表現>` |
| ツール呼び出しを1件ずつ並べる | `--tool-calls`（発話とツール呼び出しの時系列は`--bundle`が生成する`conversation.jsonl`にある） |
| 失敗したツール結果の文言を探す | `--tool-calls --failure-fixed-string <文字列>` |
| 人間の入力を探す | `--user-events --user-match-fixed <文字列>`か`--user-match-regex <正規表現>`（直前のアシスタント発話は`--with-preceding-assistant`） |
| 条文がその時点の文脈にあったかを調べる | `--context-at <record>:<line> --phrase=<文字列>` |

- 固定文字列を含む記録ファイルを記録をまたいで探す時は、`rg -l -F -- '<文字列>' <記録のディレクトリ>`で列挙し、見つかった各パスを`--transcript <記録の絶対パス>`へ渡して照会する。
  ファイル名と件数だけを返すBashの検索は、原記録の本文参照の警告の対象外である。
  `rg -L`はシンボリックリンクの先の本文を返す指定で、ファイル名だけを返す`grep -L`とは別物である。
- `--fixed-string`は単一の`--transcript`だけで使え、`--grep`とは排他である。
  ハイフンで始まる文字列は`--fixed-string=--help`のように等号で渡す。
- 失敗文言の横断照会は、複数のClaude projectを収めるディレクトリなら`--scan-claude-projects <絶対パス>`、Codexの履歴なら`--scan-codex-history <絶対パス>`のどちらか1つを指定する。
  走査の窓は`--since`と`--observation-boundary`で付ける。
  失敗として数えるのは失敗したツール結果だけで、成功した結果や会話内の引用だけの一致は除く。
- 件数、判定、保存先だけが必要な時は`--summary-only`を足し、詳細が必要な時は同じ条件から外して再実行する。
  要約でも、出所、未解決の記録と委譲、エラー、終了コードは残る。
  `--reported-improvements`は`--summary-only`、カタログ、`--bundle`と排他である。
- 単一のtranscriptへの照会では、出力の`kind`が衝突しない照会をまとめて指定できる。
  `--grep`、`--hook-notices`、`--user-events`は共通の`summary`を使うため、別々のコマンドに分ける。
  位置の指定に不正な値があると、他の照会の結果を出したうえで終了コード2で終わる。
- 破損した記録の正常な部分だけを照会する時は、evidenceかprepareに`--allow-partial-records`を明示し、全ての欠測を`record-gap`の`record`、`line`、`reason`で確かめる。
  `partial: true`を持つ結果と、欠測をまたぐ文脈、対応、集計は、部分の取得として扱う。
- 出所は各イベントの`record`、`role`、`source_record`、`source_line`、`mode`、`role_document`で確かめる。
  通常の表示と`--bundle`は全記録の`record-provenance`を出し、他の照会は直接参照した記録とその起動元の分だけを出す。
- 候補の証拠は、索引の`candidate_id`と`evidence_path`から取る。
  `bundle/timeline.jsonl`の`reported_improvements`はイベント直下の文字列の配列で、各要素の出所は同じイベントが持つ。
- 既知のCodex形式の`--detail`は、入力の構造、結果の本文、コマンドと終了状態を役割別に返し、元のレコード全体の構造は`--record-schema`で調べる。

人間の入力を親セッションをまたいで探すか数える時は、カタログと`--user-events`を併用する。
検証用の入力か実際の利用かは、抽出後に原文と操作の目的から区別する。
Codexではカタログの指定を`--catalog-codex-history <履歴の絶対パス>`に、単一の記録では`--transcript <記録の絶対パス>`に置き換える。

```sh
atk run-script session-review-evidence -- --catalog-claude-project <projectの絶対パス> --since 2026-10-01 --observation-boundary 2026-10-08 --user-events --user-match-fixed <文字列> --with-preceding-assistant
```

## 会話の少量取得

復元、進捗点検、状況照会、引き継ぎ、操作の調査で会話を少量だけ取得する時は、`agent-toolkit:atk-mode/references/work-contract.md`「会話の少量取得」に従う。
抽出には`atk run-script session-review-prepare -- --conversation-only`を使う。
