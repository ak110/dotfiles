# セッション記録の構造と集計

本書は、Claude CodeとCodexのセッション記録を集計・分析する際に必要なデータ構造の知識をまとめたものである。ここでいうセッション記録とは、Claude CodeおよびCodexが保存する会話履歴（transcript）を指す。

Claude Codeの記録は`~/.claude/projects`配下に保存される。Codexのロールアウト記録は`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`に保存される（`CODEX_HOME`未設定時は`~/.codex`）。ファイル名に含まれる`<thread-id>`はUUIDであり、末尾の5区画がthread IDに該当する。どちらの記録も形式はJSON Linesであり、各レコードの時刻フィールド名は共通して`timestamp`である。

コンパクション（会話圧縮）の記録形式は、実行環境（runtime）によって異なる。Claude Codeでは`type`が`system`、`subtype`が`compact_boundary`のレコードとして記録され、`compactMetadata`の中に`trigger`、`preTokens`、`postTokens`、`durationMs`が格納される。一方、Codexでは`type`が`compacted`のレコードに、所要時間を除いた情報が記録される。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：H1直下：2026年9月2日」に記載されている。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../session-review/references/analysis.md#「コストの無駄の確認」` | 判断 | `section:agent-toolkit:*/skills/session-review/references/analysis.md#「コストの無駄の確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |

## Claude Codeの記録

会話の要素は、レコードの`type`が`user`または`assistant`であるものだけで判定する。Claude Code 2.1.301に存在する`api-request-blob`はAPIリクエストの控え（コピー）であり、同様の形式を持つ`message.content`内にツール入力・実行結果・発話が含まれるため、形式の一致だけでカウントすると二重計上になってしまう。生のレコード本文や構造を調査する照会では、この控えも含めて表示される。監査記録は`docs/development/audit-records.md`の「セッション記録のAPI要求の写し：2026年10月18日」に記載されている。

- ディレクトリ構造の深さは、メイン記録である深さ2（`<project>/<session-uuid>.jsonl`）と、サブエージェント記録である深さ4（`<session-uuid>/subagents/agent-<agentId>.jsonl`）のみである。孫エージェントの記録も含め、すべて祖先セッション直下の`subagents/`にフラットに配置される。
- `<project>`のディレクトリ名は、セッションを開始した作業ディレクトリの絶対パスに含まれる英数字以外の文字を`-`に置き換えたものである。そのため、worktreeなど別ディレクトリで開始したセッションは、元の複製元とは異なる`<project>`配下に置かれる。Claude Codeのメインエージェントが自身の記録を参照する場合は、SessionStart通知に示されたtranscriptの絶対パスをそのまま使う。更新時刻の順序や現在の作業ディレクトリ（cwd）から推測して特定しない（`rules-main.claude-code.md`「自身の会話の記録」を参照）。特定済みのセッションIDから別の記録を読み取る場合は、`atk run-script session-review-evidence -- --claude-session-id <ID>`へIDを渡す。ファイルパスそのものが必要な場合は、`find ~/.claude/projects -maxdepth 2 -name '<session-uuid>.jsonl'`で1件に確定させる。取得したパスは必ずクォートして渡す（クォートしないままグロブ展開されて複数語に分かれると、後続コマンドが標準入力を待ち続けてしまうため）。
- 親エージェントと子エージェントの記録の間で同一の事象が重複して現れるため、件数を確定する際は重複を除外する。ただし、集計対象を深さ2のメイン記録だけに限定すると、サブエージェント内部のイベント（ツール呼び出しなど）を取りこぼしてしまうため、対象を限定する場合はその適用条件を明記する。
- サブエージェントの起動回数を数える典拠としては、`subagents/*.meta.json`内の`agentType`、`description`、`toolUseId`、`spawnDepth`、`parentAgentId`を用いる。`parentAgentId`は`spawnDepth`が2以上の記録にしか現れないため、階層構造を復元する際は`spawnDepth`も併用する。
- 特定の性質を持つ行を抽出する処理では、その値がツール自身の出力なのか、ツールが読み取った外部ファイルの内容なのかを、レコードの構造から区別する（読み取ったファイルの本文も実行結果と同じ領域に格納されるため、単なる書式の一致だけで判定すると、ファイル内の例示記述まで実行時イベントとして数えてしまう）。
- 文面を含む`thinking`ブロックには、ツール呼び出しの合間に挟まれる地の文をサーバー側で要約したもの（ナレーション要約）が含まれており、エージェントが書いた原文でも拡張思考の全文でもない。そのため、その形状や長さ、使用単語などは、原文の性質を測定する集計対象から除外する。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：Claude Codeの記録：2026年10月6日」に記載されている。

## スキル起動の判定

Claude Codeの記録では、`type`が`user`であるレコードの`message.content`に含まれる`tool_result`要素の`content`が、`Launching skill: <スキル名>`と完全一致しているかどうかで判定する。呼び出し要求側は`type`が`assistant`の`tool_use`要素であり、`name`が`Skill`、`input.skill`にスキル名が設定される。スキル名の部分一致で検索すると、利用可能なスキル一覧を保持する`attachment`型レコードに多数誤検出してしまう。

Codexの記録には、スキルの起動を一意に示す専用のレコードが存在しない（`SKILL.md`の読み込みとして実行されるため、スキル名は一覧表示や文脈の再掲、パスを含むコマンドの入出力など随所に現れる）。
そのためCodexでは、`type`が`response_item`、`payload.type`が`message`、`payload.role`が`user`であるレコードの`content`に含まれる`input_text`要素の`text`に、スキルの起動を依頼したプロンプト本文が含まれていることのみを判定条件とする。なお、最初のuserレコードは実行環境が挿入する前置き文である。このプロンプト本文を経由しない起動については、判定不能として扱う。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：スキル起動の判定：2026年9月10日」に記載されている。

## 集計値の典拠

期間を指定した週ごとのトークン消費量や実行レビューの指標、ならびにセッションごとの推定費用については、`atk run-script cost-metrics -- --since <開始日> --until <終了日>`の出力を典拠とする。
このコマンドは、月曜0時を起点とするローカルの週ごとに、採用されたAWIの件数、全ルートとprocess-wiにおける4成分のトークン量・AWIあたりの数値、レビューのラウンド数・レベル・修正なしで応答した比率を返す。
`--until`を省略した場合は、コマンド実行時刻までを集計範囲とする。
`cost-metrics-hook-notice`は、同じルートセッションと全委譲先のhook通知をルートの開始週へ帰属させ、`week`、`runtime`、`hook`、`hook_name`、`tag`、`kind_text`ごとの`count`を返す。
`tag`は`block`・`warn`・`notice`などの重要度、`kind_text`はパスやセッションIDなどの可変部分を正規化した本文の冒頭であり、`session-review-evidence --hook-notices`と同じ分類を使う。
hook実行の記録とツール結果だけにある遮断を対象とし、同一呼び出しの重複を除く。通常の標準出力や会話中の引用は含めない。
出力に含まれる`cost-metrics-coverage`の走査ルート、集計期間、最古の読み取り記録、読み取り不能件数、未解決委譲件数、および各週の`missing_token_records`を併記し、対象範囲の網羅（カバレッジ）に欠落がある週の値は、完全な総量としては扱わない。

`cost-metrics-session`は、指定期間内に開始されたルートセッションごとに、子孫エージェントを含むAPI料金換算の推定額（USD）を返す。
Claudeの場合は最新の`type: cost-state`レコードに記録された`totalCostUSD`を優先し、その後の応答分や累積額の記録がないセッションについては、同一`message.id`の最後の`usage`とモデルの単価を基に補完する。
親セッションの算出額にあらかじめ含まれているClaudeサブエージェントの費用は二重計上せず、別プロセスで起動された委譲先の費用を加算する。
内訳における`breakdown.self`はその記録自身の累積額を含み、`breakdown.claude_subagents`はそこに含まれない追加分を表す。
Codexの場合は各`token_count.info.last_token_usage`と直前の`turn_context.model`を参照し、キャッシュ読み取りと推論出力を二重計上しないように計算する。
単価が不明な分は`unknown_responses`や`unknown_tokens`として残し、合計の`estimated_usd`をnullとした上で、算出できた範囲を`known_usd`として提示する。
料金表の出所、基準日、区分、計算式、およびTTL不明時の仮定については、`cost-metrics-coverage.pricing`の内容を併記する。
なお、`atk serve`による費用表示もこれと同じ記録収集と計算ロジックを使用する。実支払額との区別については、後述の「複数セッションの比較結果」に従う。

セッション記録から集計したトークン量、リクエスト数、所要時間を成果物やユーザーへの提示文書に記載する場合は、`atk run-script session-review-evidence -- <引数>`の出力を典拠とし、独自に作成した集計結果を根拠としない。
トークン量とリクエスト数を取得する場合は`--stats`を指定し、所要時間を取得する場合は`--elapsed-until <ISO 8601形式の時刻>`を指定する。振り返りの対象候補は`--bundle`が生成する`candidates.jsonl`から取得し、`atk run-script session-review-prepare`を実行して`candidates.md`へ整形する。

別セッションを含む担当別の費用要約を取得する場合は、`--stats --summary-only`を使用する。これは、総量、親セッション、Claudeサブエージェント、agent threadごとの要約と出所、未解決の診断情報を保持したまま出力する表示オプションであり、トークンの定義や合算ロジックは全量表示と完全に同一である。bundleには全量の詳細な内訳が保持され、準備文書には初回確認に必要な索引、総量、上位項目、詳細照会先のみが縮約して記載される。全候補ID、必須性、詳細の対応関係は`bundle/review-index.jsonl`から、担当別・対象別の全量は`bundle/stats.jsonl`および`bundle/resource-usage.jsonl`から取得する。毎回全量を通読する処理と証拠の保持を明確に分離し、候補の選別や原因特定、ならびに`agent-toolkit:session-review`の`references/analysis.md`「コストの無駄の確認」が定める17観点の費用確認に必要な詳細情報のみを追加取得する。

- Claude Codeの記録では、1回のAPI応答が同一の`usage`を持つ複数のレコードに分かれて現れることがある。上記のコマンドは、同一の`message.id`を持つ最後の`usage`に集約した値を返す。
- `--elapsed-until`で出力される`elapsed_seconds`は、セッションの最初のレコードから指定時刻までの経過時間であり、指定時刻より後の工程の所要時間は含まない。
- `--stats`で出力される総量の`elapsed_seconds`は、最初と最後のレコードの時刻差である。一方、工程ごとの`stats-tool`に示される秒数はツール呼び出しごとの実行時間であり、親と委譲先が並行して動いている区間は重複して加算されるため、合計値が全体の総量を超えることがある。比率を示す場合は、この基準の違いを併記する。

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：集計値の典拠：2026年9月3日」に記載されている。

集計値を判断の根拠として利用する前に、計測の始点・終点や数え方が、判断に必要な観点と正しく一致しているかを必ず確認する。過去に不一致が観測された例には以下のものがある。

- 委譲先の所要時間を比較する際は、`stats-agent-thread`や`stats-subagent`の`turn_elapsed_seconds`を用いて比較する。`elapsed_seconds`にはターン完了後にプロセスが残存していた時間や待機時間（`after_last_turn_seconds`）が含まれてしまう。
- `agent-toolkit:process-wi`の複数回の実行履歴から現行工程のパフォーマンスを評価する場合は、集計期間中にその工程を規定する文書が改訂されたかどうかを`git log --since=<期間の開始> -- <文書のパス>`などで確認する。改訂されていた場合は、改訂後の記録のみを現行プロセスの値として扱う。
- 同一ファイルを行範囲に分割して複数回に分けて読み取った場合は、全体で1回の読了として数える。

## 複数セッションの比較結果

カタログにおける親セッションの行は、`--catalog-workflow`による完全一致、`--catalog-known-workflow`によるunknown以外の抽出、および`--catalog-cwd-contains`による部分文字列一致で絞り込める。
複数の行条件を併記した場合は、すべての条件を満たす行のみが返される。
`--catalog-fields`にカンマ区切りで項目名を渡すと、指定された順序で項目が出力され、指定を省略した場合は全項目が出力される。
なお、`kind`は行の種類を示す必須項目のため、常に保持される。
条件で絞り込んだ後も、`catalog-summary`には走査全体の範囲が記録され、走査した親セッション全体のレコード数である`parent_record_count`と、選択された行数である`selected_parent_count`が区別して出力される。
これらの指定方法はClaudeとCodexの親セッション一覧で共通しており、行条件は`--user-events`と併用する場合でも親セッションの選定に適用される。
また、`--catalog-fields`や`--tool-calls`による呼び出し照会は、親セッションの行条件とは分けて指定する。

「最新」「該当なし」「全件」のように全体や終端を表す結論を述べる場合は、走査ルート、実行環境（runtime）、開始境界、観測境界、未解決レコード数を同じ文面に明記し、その調査範囲（カバレッジ）のみを対象として述べる。指定ルート外の記録や、観測境界より後の記録、未解決の記録については、未確認の範囲として明記する。

WIの処理件数を数える場合は、成功結果まで正常に記録された直接の`atk wi`操作のみをカウントし、操作ごとにセッション識別子と記録行のロケーター（位置情報）を残す。workflow名、作業ディレクトリ（cwd）、ブランチ名、時刻、トークン量、処理件数などを記録から特定できない項目については、`unknown`として扱う。

費用を記載する際は、請求書や決済記録から確認した実際の実支払額と、API利用量に公開単価を掛け合わせて計算した見積額を、明確に別の値として提示する。見積額を記載する場合は、価格表の出所、価格の基準日、入力・キャッシュ書き込み・キャッシュ読み取り・出力の区分、および計算式を併記する。なお、契約プランに伴う定額料金は実支払額の区分に含める。

施策の前後の差を因果関係による効果として主張する場合は、変更した変数、比較対象において固定した条件、および反実仮想（施策を実施しなかった場合の想定）を明示する。集計期間、実行環境、対象workflow、処理したWIの由来、あるいは並列度が異なっている比較については、単なる相関関係として記載し、施策自体の純粋な効果は追加の対照比較を行って確定させる。

## 本文の検索

セッション記録（ユーザーの発話、ツールの実行結果、警告、委譲記録など）から本文を取得する場合は`atk run-script session-review-evidence -- <引数>`を使用し、記録の指定方法や照会モードの内容は`-- --help`の案内に従って目的に応じて選定する。

- 検索キーワードから該当箇所を検索する: `--grep`
- 位置が特定できている記録の本文を読み取る: `--detail`
- 複数の固定文字列について一致したエントリ数と全ロケーターのみを取得する: 文字列ごとに`--fixed-string`を個別に実行する（複数の文字列を1つの正規表現にまとめて結合しない）
- 位置が特定できているレコードの構造のみを調べる: `--record-schema`
- ツール呼び出しを呼び出し単位で一覧列挙する: `--tool-calls`（1つのセッションにおける発話とツール呼び出しの時系列推移は、`--bundle`が生成する`conversation.jsonl`に記録される）

件数、判定結果、保存先パスのみを確認したい場合は、照会コマンドに`--summary-only`を追加する。
必要な詳細情報を取得する場合は、同じ検索条件から`--summary-only`を外して再実行する。

| 照会 | 要約で返す値 |
| --- | --- |
| `--grep` | 記録別および全体の合計一致エントリ数（同一エントリ内に複数行の一致があっても1件と数える） |
| `--fixed-string` | 文字列ごとの一致エントリ数（全ロケーターの配列は省略される） |
| `--context-at` | フレーズごとの`context-verdict`に含まれる全フィールド |
| `--tool-calls` | `tool-call-summary`の全フィールド（カタログと併用した場合も同様） |
| `--warn` | 抽出および重複排除を行った後の実際の警告イベント数（0件の場合も明示される） |
| `--bundle` | `bundle-file`、`bundle-evidence-metrics`、`bundle-kind-count`（全量の内容は保存ファイル側で参照する） |

要約表示であっても、データの出所、未解決の記録や委譲、エラー内容、終了コードは保持される。
統計情報の要約については前述の「集計値の典拠」を参照する。
その他の詳細な情報を取得したい場合は、`--summary-only`を指定から外す。

人間の入力内容を検索する場合は、`--user-events --user-match-fixed <文字列>`または`--user-events --user-match-regex <正規表現>`を使用する。
検索処理は人間が入力した値そのものに適用され、確認回答時の`assistant_context`は文脈情報として保持される。
`--with-preceding-assistant`を追加すると、同一記録内で直前に行われたアシスタントの発話全文、位置、時刻が`preceding_assistant`として付与される。
該当する直前発話が存在しない場合、このフィールドの値はnullになる。

カタログ指定と`--user-events`を併用することで、複数プロジェクトやセッションをまたぐ横断検索を行える。
親セッションの絞り込みにはworkflowやcwdの行条件が利用できる。
`--catalog-fields`は親セッションの一覧表示で使用する。
元記録行の時刻が`--since`より後かつ`--observation-boundary`以前である入力を、時刻・記録・位置の安定した順序で、件数上限なしに取得できる。
各一致項目は、入力全文とともに`record`、`line`、`sequence`、`timestamp`、`session_id`を保持する。
時刻情報を持たない入力は`user-event-undated`として扱われ、読み取り不能や未解決の項目は診断情報として出力される。
要約における`parent_record_count`、`selected_parent_count`、`count`は、それぞれ走査した親セッション数、選定された親セッション数、一致したイベント件数を表す。
結果を読む際は、一致件数が0件であることと、調査が及んでいない未確認範囲が存在することを明確に区別する。

```sh
atk run-script session-review-evidence -- --catalog-claude-project <projectの絶対パス> --since 2026-10-01 --observation-boundary 2026-10-08 --user-events --user-match-fixed <文字列> --with-preceding-assistant
```

Codexのセッションを検索する場合は、カタログ指定を`--catalog-codex-history <履歴の絶対パス>`に変更する。
単一の記録ファイルを検索する場合は、カタログ指定を`--transcript <記録の絶対パス>`に置き換えることで、同様に検索条件や直前発話の取得オプションを使用できる。

通常表示モードおよび`--bundle`が出力するファイルには、収集された全記録の出所情報（`record-provenance`）が含まれる。一方、warn、grep、fixed-string、tool-calls、hook-notices、stats、およびbundleの要約表示では、出力結果が直接参照している記録と、その起動元となった記録の`record-provenance`のみが出力される。各イベントの`record`、`role`、`source_record`、`source_line`、`mode`、`role_document`を確認して出所を特定する。親セッションが収集対象に含まれていない情報や、役割文書が関連付けられていない項目の値はnullになる。
役割別のツール呼び出し件数や起動された委譲先のmodeは、`--tool-calls`の出力にある`tool-call-summary.by_role`の`count`、`by_tool`、`by_mode`から確認できる。ここで`document:<役割名>`は最初の配送時に指定された役割文書、`record:<role>`は役割文書を持たない記録の収集区分を表す。カタログと併用した場合でも、収集ルート外で起動された委譲先を含めて集計される。

単一のtranscriptに対する照会では、出力される`kind`が互いに衝突しない組み合わせであれば、複数のオプションを併用して各照会のイベントをまとめて取得できる。ただし、`--grep`、`--hook-notices`、`--user-events`は共通の`summary`領域を使用するため、これらのうち複数を同時に実行したい場合は個別にコマンドを分けて実行する。共通の出所情報や未解決記録については、1回だけ出力される。指定されたロケーターに不正な値が含まれていた場合でも、他の正常な照会結果は出力された上で、終了コード2で終了する。なお、カタログ走査、保存済み発話の照会、および`--bundle`を組み合わせる場合は、各入力形式ごとの排他条件に従って指定する。

検索結果の全量を読み取る際は、生成側が出力した`保存先:`に示される絶対パスから読み取り、出所の証拠を後続へ引き渡す際にもそのファイルを使用する。
JSON Linesファイル群に対して汎用の検索コマンドを用いて無制限に検索したり、セッション記録や`candidates.md`を標準出力へ全量表示させたりしない（行数や1行の長さは入力データに依存するため、極端に巨大な単一行に対して広い正規表現を適用するとマッチング処理の上限に達してしまう）。このコマンドで直接対応できない調査をする場合は、`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」に定められた出力量の判定基準と分離実行の規定を適用する。

## 暗号化された値

以下の値は提供元によって暗号化されて返されるデータであり、提供元の公式ドキュメントにおいて解釈をしないよう明記されている。そのため、事実確認や判別に際しては値の有無と所在のみを確認対象とし、デコード処理や内部構造の解析しない。

- Claude Codeの記録に含まれる`thinking`ブロックの`signature`、および`redacted_thinking`ブロックの`data`
- Codexの記録において`payload.type`が`reasoning`であるレコードの`encrypted_content`

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/session-records.md：暗号化された値：2026年10月7日」に記載されている。

## 進捗更新の判別

公式ドキュメント「Thinking」の「Progress updates between tool calls」節の基準に基づき、ツール呼び出しの間に行われる進捗更新（公式ドキュメント上の「progress update」、Claude Code内部での名称は`narration`）を判別する。
APIリクエストの`display`パラメーターが`"updates"`に設定された応答のうち、本文が空ではない`thinking`ブロックを持つものを進捗更新とみなす。
ただし、記録ファイル自体には`display`の値が直接記録されていないため、設定内容やホスト側の送信条件からその値を確認できた範囲のみを判別済みとし、残りは判別不能として件数を分けて計上する。この節の観測結果についても、前節と同じ監査記録に記載されている。
