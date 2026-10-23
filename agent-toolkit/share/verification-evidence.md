# 検証結果と要求別の証拠保存

実装するメインとレーン担当が、手動観測と未判定検証記録を保存する共通手順である。手動観測の前は「手動観測の保存」、要求別の記録を作成・更新する前は「完成条件証拠の検証記録」を読む。工程の認可、実装とレビューの分担は呼出側の契約を使う。
最終観測の取得順序は`agent-toolkit:commit`の`SKILL.md`「検証」、共有する検証のまとめ実行と保存結果の被覆判定は`agent-basics:check-execution`の`references/verification-scope.md`「実行条件と再実行」に従う。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 受入シナリオを起動する前 | `../skills/check-execution/references/acceptance-scenarios.md` | 判断 | `read:agent-toolkit:*/skills/check-execution/references/acceptance-scenarios.md` |
| 訂正済み検証表を使う前 | `../skills/plan-mode/references/plan-file-standards.md#「検証と終端工程」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/references/plan-file-standards.md#「検証と終端工程」` |
| 保存出力を取得する前 | `../skills/check-execution/SKILL.md#「保存した実行出力の取得」` | 判断 | `section:agent-toolkit:*/skills/check-execution/SKILL.md#「保存した実行出力の取得」` |
| 取得版の証拠を記入・再利用する前 | `../skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` |
| レビューへ渡す最終観測を取得する前 | `../skills/commit/SKILL.md#「検証」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「検証」` |
| 保存済みの成功集合と限定再検証から被覆を判定する前 | `agent-basics:check-execution/references/verification-scope.md#「実行条件と再実行」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「実行条件と再実行」` |

## 手動観測の保存

受入シナリオか完成条件を手動で観測する直前に、受入シナリオ検証（`agent-toolkit:check-execution`の`references/acceptance-scenarios.md`）の起動条件で対象を起動する。MCPで記録を取得できない有限終了のコマンドは、対象worktreeで`atk run-command [--cwd DIR] [--timeout SECONDS] -- COMMAND [ARG...]`で実行する。返却の`record_path`はcwd・argv・`git_head`・`git_status`・終了状態・両出力パスを保持するため、その絶対パスと出力の保存先を検証結果と未判定検証記録の`evidence`へ渡す。保存失敗を示す非0の終了が無いことを確かめ、記録の無い観測は保存する形でやり直す（実行レビュー担当は未保存の観測を補えない）。PATH上のコマンドなどインストール済みの別実体を実行した観測は、その実体の版を記録する

## 完成条件証拠の検証記録

実行レビューが完成条件証拠を作成する対象では、実装完了前に要求単位ごとの未判定検証記録JSONをmanaged-tempへ作成する（レビューの出力先とは別のファイル）。

- 生成: `atk run-script verification-record -- --output <記録の絶対パス> --plan <計画の絶対パス> --list`で原文と出所の雛形を保存し、配列・1始まりの行番号・出所も同じ操作で得る。WI直渡しでは`--plan`の代わりに`--wi <WIファイル名>`を反復する。対象リポジトリは計画メタ情報の値を使い、WI直渡しと対象を持たない既存計画だけcwdを使う。計画だけのユーザー指示も変更履歴の逐語発言から生成される。既存記録を読むだけなら`--output <同じパス> --list`
- 根拠の記入: `--output <同じパス> --select-row <配列:N> --evidence-file <根拠のUTF-8ファイルの絶対パス> --mode replace`で、その要求単位の現在の根拠へ更新する。`--mode append`は同じ要求単位の現在の根拠に不足する観測の補完に使う
- 行別の記入: 選択した行ごとに異なる根拠を渡す場合は`--row-evidence-file <配列:N> <根拠の絶対パス>`を反復する。行別の試験・診断は`--row-result-test <配列:N> <完全テスト名>`・`--row-result-diagnostic <配列:N> <診断識別子>`を反復する。行別指定だけで更新する場合は`--select-row`を省略でき、行別指定に書いた行が更新先になる。`--evidence-file`・`--result-test`・`--result-diagnostic`は全選択行への共通指定で、同じ種類の行別指定と分けて使い、共通指定だけで更新する場合は`--select-row`で行を選ぶ。`--select-row`を明示した場合は、選んだ行の外への行別指定が拒否される。単一値の入力を異なる値で反復すると拒否されるため、モード・取得版などが異なる更新は呼び出しを分割し、行別の詳細は既存の`--updates-file`でも渡せる
- 構造化結果の取得: 試験は`atk run-command -- ... pytest -v -o junit_family=xunit1 --junitxml=<XMLの絶対パス> <対象...>`で実行時にXML・実行記録・両出力を保存する。診断はpyfltrのrunのJSON Lines全体（header・command・summaryを事前に除く必要はない）、`show-run --commands <対象> --output-format=jsonl`かMCPの構造化結果を保存する。取得版・対象範囲・オプション・依存版・並列度・実行環境と必要な保存時本文を対応付ける。サブプロジェクトを作業ディレクトリとした実行でも、取り込んだテスト名と診断のファイルはworktreeのルートからの相対パスになる
  - pyfltrでpytestを起動するプロジェクトでは、pyfltrの`run`・`run-for-agent`・`ci`・`fast`を`atk run-command`で包んで実行してもよく、保存した記録を後掲の「構造化結果からの記入」の`--junit-record`へそのまま渡す。起動形の例は次のとおり
    - `atk run-command -- pyfltr run-for-agent --commands=pytest --output-format=jsonl "--pytest-args=-o junit_family=xunit1 --junitxml=<XMLの絶対パス> <対象>"`
  - 取込み処理は`--junitxml`・`junit_family=xunit1`・`rootdir:`をpyfltrの実行記録から読むため、`--no-archive`を付けた実行の記録は受理されない
- pytest以外の構造化試験: 各プロジェクトの試験コマンドへ次の指定を加え、`atk run-command`で結果ファイルと実行記録を保存する
  - vitestは`--reporter=json --outputFile=<結果の絶対パス>`を指定する
  - Playwrightは`env PLAYWRIGHT_JSON_OUTPUT_NAME=<結果の絶対パス> <Playwrightの起動形> test --reporter=json`で起動する
  - dotnet testは`--logger "trx;LogFileName=<結果の絶対パス>"`を指定する
- pytest以外の結果入力: `--test-format <vitest|playwright|trx> --test-file <保存結果> --test-record <実行記録>`を渡す。基準版は同じ組の`--baseline-test-format`・`--baseline-test-file`・`--baseline-test-record`へ指定する。一覧・詳細・行への記入は次の構造化結果の記入と同じ公開操作を使う
- ラッパー・コンテナーを介す試験: `atk run-command --result-file <ホストの結果の絶対パス> --path-map <子の絶対接頭辞>=<ホストの絶対接頭辞> -- <試験コマンド>`で結果と実行を対応付ける。同じパスを使う場合は`--path-map`を省く。取込み時に結果の作成・更新と終了後の内容保持を確かめるため、結果を終了直後に取り込む。JUnitは全testcaseのfile属性とrootdirの出力を保存する
- 保存出力の読取: 本文表示、構造化診断、試験結果の関連付けは、`agent-toolkit:check-execution`の「保存した実行出力の取得」で用途に合う公開指定を選ぶ。保存済みログの本文は`pyfltr show-run <run_id> --commands <コマンド名> --output --output-format=text`で読み、生成元に形式・対象の指定があれば保存前に選ぶ。構造化診断のJSONLと結果ファイルの関連付けは前後の取得・記入手順を使う
- 構造化結果からの記入: 入力形式は`atk run-script verification-record -- --help`で取得する。試験は`--junit-xml <保存XML> --junit-record <実行記録>`で直接指定する。診断は`--diagnostics-file <保存JSONL> --diagnostics-record <実行記録> --diagnostics-conditions <取得時の条件JSON>`と必要な`--diagnostics-source <相対ファイル> <保存時本文>`の反復で直接指定する。相対ファイルはworktreeのルートを起点とする。基準比較は同じ組の`--baseline-junit-*`・`--baseline-diagnostics-*`へ保存物を指定する。`--output <初回記録の絶対パス> --list-results --results-counts`で状態・基準版との差・診断群の件数を読む。`--results-counts`を`--result-status <状態>`・`--result-change <差の種類>`・`--result-diagnostic-group <群番号>`の必要な選択へ替え、`--results-identifiers`でその群の全識別子を読む。識別子表示を外せば選択群の詳細・取得版・所在を読め、完全名による`--result-test`と診断識別子による`--result-diagnostic`の反復も使える。全詳細と、全識別子付きの`--results-summary`も選べる。代表の1件で全件の意味を判断せず、比較不能は不足として補う。記入では`--list-results`を外し、`--select-row <配列:N>`の反復と各要求を直接満たす完全テスト名・診断識別子を渡す。必要なら`--evidence-file`と`--mode append|replace`も渡し、同じ保存物から更新する（sourceは処理側が現行行から取得する）。不正な行が1件でもあれば全保存が拒否される。既存のJSON入力の契約も保持され、未選択行は変わらない

各`evidence`には受入シナリオの公開された呼び出し手段・入力・期待結果に対応する実結果として、テスト完全名と成功結果の所在、または保存済み観測の入力・操作・期待結果・観測結果の箇所を書く。pytestのテスト名は、同じ版の保存済み出力（`-v`付き）にその名前と結果の行があることを確かめる。点表示と総数は個別の合否の根拠にならない。後続工程や不採用・割当の行には、検証済みの範囲と採否・後続工程を記録した箇所を書く。計画確定時に決まった後続工程は計画の`## 実施内容`、実装中に作成した後続工程は対象AWIへの追記先、同じセッション内で読まれるものは引き継ぎ記録を示す。`outcome`と`reviewed_head`は空欄を保つ（判定は実行レビュー担当だけが書く）。
観測を取得した版（実行記録の`git_head`など）は観測ファイルの参照と裸のOIDで書き、`commit:<OID>`は現行成果物を指す参照だけに使う。`--rewrite-map`は履歴書換えで`commit:`標識だけを新しいOIDへ更新するため、取得版をこの標識で書くと実行していない版へ変わる。書き分けの詳細は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う。
各行へ保存するのは、その要求単位に直接必要な観測と現在の成果物へ適用できる理由とする。
旧取得版の観測が現在も必要なら、取得版と適用理由を保持し、失効した説明や別要求の経緯は置換する。
修正ラウンドの経緯・応答・履歴統合はレビュー指摘管理表と観測記録へ残し、各行には判定に必要な所在と観測内容を対応付ける。
複数要求が同じログを使う場合も、各要求に該当する観測の所在と内容を行ごとに記す。
不足する根拠は自身の操作の範囲で補い、入力・権限・外部状態を要する不足は続行できない理由で返す。記録の絶対パスは初回生成時に進捗ログ（計画なしでは引き継ぎ記録）へ残し、`検証結果`にも記す。
レビュー修正担当はどの操作よりも先にその記録から初回パスを解決し（解決できなければ入力不足を返す）、全操作を同じ`--output`へ行う。再生成は既存根拠を保持して不足行を加え、修正が影響する行だけを置換する。CI記録だけのレビューで完成条件証拠が`なし`ならこの記録は不要とする。
