# セッション記録の構造と集計：本文の検索

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 本文の検索

セッション記録（ユーザーの発話、ツールの実行結果、警告、委譲記録など）から本文を取得する場合は`atk run-script session-review-evidence -- <引数>`を使用し、記録の指定方法や照会モードの内容は`-- --help`の案内に従って目的に応じて選定する。

記録をまたいで固定文字列を含むファイルの所在を探す場合は、`rg -l -F -- '<文字列>' <記録のディレクトリ>`で列挙する。
ファイル名・件数だけを返すBashの検索は原記録の本文参照の警告から除く。
`rg -L`はシンボリックリンクを追従して本文を返す指定であり、ファイル名だけを返す`grep -L`と区別する。
見つかった各パスを`--transcript <記録の絶対パス>`へ渡し、`--grep <正規表現>`・`--fixed-string <文字列>`・`--detail <record:line>`で本文を照会する。
`--fixed-string`は単一の`--transcript`だけで使える。
期間と親セッションの一覧が必要な場合は、カタログへ`--since`と`--observation-boundary`を指定する。

- 検索キーワードから該当箇所を検索する: `--grep`
- 位置が特定できている記録の本文を読み取る: `--detail`
- 記録済みの改善点を出所付きで全行取得する: `--reported-improvements`。
  同文の別出所や1発話の複数行も保持する
- 複数の固定文字列について一致したエントリ数と全ロケーターのみを取得する: 文字列ごとに`--fixed-string`を個別に実行する（複数の文字列を1つの正規表現にまとめて結合しない）
- 位置が特定できているレコードの構造のみを調べる: `--record-schema`
- ツール呼び出しを呼び出し単位で一覧列挙する: `--tool-calls`（1つのセッションにおける発話とツール呼び出しの時系列推移は、`--bundle`が生成する`conversation.jsonl`に記録される）

既知のCodex形式の`--detail`は入力構造、結果本文、コマンドと終了状態を役割別に返す。
元レコード全体の構造は`--record-schema`で調べる。
候補の証拠は索引の`candidate_id`と`evidence_path`から取得する。
`bundle/timeline.jsonl`の`reported_improvements`はイベント直下の文字列配列であり、各要素の出所は同じイベントが持つ。

`--reported-improvements`は件数を末尾の`reported-improvement-summary`へ返し、本文は`reported-improvement`の`record`・`line`・`timestamp`・`text`から読む。
`--summary-only`、カタログ、`--bundle`とは併用しない。
対応する他の照会で件数、判定結果、保存先パスのみを確認したい場合は、照会コマンドに`--summary-only`を追加する。
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
統計情報の要約については`session-records-metrics.md`「集計値の典拠」を参照する。
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
カタログの`--grep`では、家族収集でroot外も含めて解決した記録を最終的な未解決から除く。
診断の`scope=scan-root`は全走査root、`scope=selected-family`は選択家族の未確認範囲を示す。
要約の`scan_root_unresolved_record_count`と`query_unresolved_record_count`を併記し、`unresolved_record_count`は両範囲の合併として読む。
読込失敗の理由と失敗行、所在未解決、時刻のない一致行はそれぞれの診断で確認し、全走査rootの限界と選択した親・期間の照会の限界を区別する。
`line`は参照元位置、読込失敗時に付く`failure_line`は読み取れなかった記録内の行番号である。
時刻がない記録を期間外と推定せず、`undated_match_count`は一致エントリ数とは別の一致行数として読む。

```sh
atk run-script session-review-evidence -- --catalog-claude-project <projectの絶対パス> --since 2026-10-01 --observation-boundary 2026-10-08 --user-events --user-match-fixed <文字列> --with-preceding-assistant
```

Codexのセッションを検索する場合は、カタログ指定を`--catalog-codex-history <履歴の絶対パス>`に変更する。
単一の記録ファイルを検索する場合は、カタログ指定を`--transcript <記録の絶対パス>`に置き換えることで、同様に検索条件や直前発話の取得オプションを使用できる。

通常表示モードおよび`--bundle`が出力するファイルには、収集された全記録の出所情報（`record-provenance`）が含まれる。
一方、warn、grep、fixed-string、tool-calls、hook-notices、stats、およびbundleの要約表示では、出力結果が直接参照している記録と、その起動元となった記録の`record-provenance`のみが出力される。
各イベントの`record`、`role`、`source_record`、`source_line`、`mode`、`role_document`を確認して出所を特定する。
親セッションが収集対象に含まれていない情報や、役割文書が関連付けられていない項目の値はnullになる。
役割別のツール呼び出し件数や起動された委譲先のmodeは、`--tool-calls`の出力にある`tool-call-summary.by_role`の`count`、`by_tool`、`by_mode`から確認できる。
ここで`document:<役割名>`は最初の配送時に指定された役割文書、`record:<role>`は役割文書を持たない記録の収集区分を表す。
カタログと併用した場合でも、収集ルート外で起動された委譲先を含めて集計される。

単一のtranscriptに対する照会では、出力される`kind`が互いに衝突しない組み合わせであれば、複数のオプションを併用して各照会のイベントをまとめて取得できる。
ただし、`--grep`、`--hook-notices`、`--user-events`は共通の`summary`領域を使用するため、これらのうち複数を同時に実行したい場合は個別にコマンドを分けて実行する。
共通の出所情報や未解決記録については、1回だけ出力される。
指定されたロケーターに不正な値が含まれていた場合でも、他の正常な照会結果は出力された上で、終了コード2で終了する。
なお、カタログ走査、保存済み発話の照会、および`--bundle`を組み合わせる場合は、各入力形式ごとの排他条件に従って指定する。

検索結果の全量を読み取る際は、生成側が出力した`保存先:`に示される絶対パスから読み取り、出所の証拠を後続へ引き渡す際にもそのファイルを使用する。
JSON Linesファイル群に対して汎用の検索コマンドを用いて無制限に検索したり、セッション記録や`candidates.md`を標準出力へ全量表示させたりしない（行数や1行の長さは入力データに依存するため、極端に巨大な単一行に対して広い正規表現を適用するとマッチング処理の上限に達してしまう）。
このコマンドで直接対応できない調査をする場合は、`agent-toolkit/share/rules/common/02-agent-operations.md`「ツール・コマンド運用」に定められた出力量の判定基準と分離実行の規定を適用する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| セッション記録の構造と集計：集計値の典拠の基準を適用する前 | `session-records-metrics.md` |
