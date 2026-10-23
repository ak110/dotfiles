# 検証対象の品質基準と記録操作

品質の定義元は`agent-basics:check-execution`の`references/verification-scope.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。取得済みの同版本文は再取得せず使う。

検証対象の選定では`agent-toolkit:wi-standards`の`references/awi-body.md`「完成条件」の自己観測と未確認範囲の扱い、公開前の全体検証では`agent-toolkit:commit`の`references/publish.md`「検証とCI」を適用する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

<!-- read-table-source: verification-scope-delivery-1.md -->

## 実行のしかた

- 計画で確定した検証行は本スキルの`SKILL.md`「統合実行ツール経由の起動」の`plan-verify --summary-only`で実行し、順序・コマンド・状態・終了コード・記録参照・保存失敗と未記録集合を一覧で受領する。凍結後の訂正済み検証表を引き継いだ場合は、同節の`--verification-plan`を指定して訂正後の条件を実行する。訂正表の書式・保持・引き継ぎは`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「検証と終端工程」に従う
- 保存済みの結果は子を再実行せずに読む。plan-verifyの結果まとめは`results_path`を`atk run-command --records-file <絶対パス>`へ渡す。計画なしの保存済み実行は`--record <record_path>`の反復で渡す。どちらの方法でも順序・argv・cwd・取得版・Git状態・子とwrapperの終了状態・両出力へ到達する。旧記録の`unrecorded`は未記録として保持し、読取失敗・欠損・破損を成功と扱わない
- 先行結果と限定再実行を合わせるときは、品質の定義元の「実行条件と再実行」で各成功集合の適用可否を判定する。各保存物を後掲の公開操作へ渡し、完全テスト名・取得版・合否を要求へ対応付ける。先行成功と追加成功の根拠は同じ要求行へ`--mode append`で保存できる
- 保存結果の試験・診断を完成条件へ対応付けるときは、`atk run-script`の`verification-record`か`exec-review-evidence-check`で、後掲「構造化試験結果の取得と記入」の入力を使う。診断は`--diagnostics-file`・`--diagnostics-record`・`--diagnostics-conditions`と必要な`--diagnostics-source`から読む。`--list-results --results-counts`で状態・基準差・診断群の件数を読む。続いて`--result-status`・`--result-change`・`--result-diagnostic-group`の選択と`--results-identifiers`で全識別子へ進み、選択群か完全テスト名・診断識別子の詳細で全対象の意味を判定する。`--select-row <配列:N>`と`--result-test`・`--result-diagnostic`・`--evidence-file`・`--mode`で要求行へ保存する（原文・sourceの転記や手製のJSONは不要）。診断の比較条件と保存時本文は取得時の保存物を使う

### 構造化試験結果の取得と記入

試験はプロジェクトの起動形に次の出力指定を加え、`atk run-command`で実行記録とともに保存する。
結果ファイルはmanaged-temp内の絶対パスを指定し、返された`record_path`を取込みへ渡す。
makeやコンテナーを介す場合は、`atk run-command --result-file <ホストの結果の絶対パス> --path-map <子の絶対接頭辞>=<ホストの絶対接頭辞> -- <プロジェクトの試験コマンド>`で宣言する。
同じホストのパスを使う場合は`--path-map`を省く。
取込みは宣言した結果が実行中に作成・更新され、終了後に変わっていないことを確かめ、結果中の絶対パスを最長一致の対応で変換する。
JUnitは全testcaseのfile属性とrootdirの出力を必要とする。

| runner | `atk run-command --cwd <worktree> --`に続ける実行形 | 両公開操作へ渡す入力 |
| --- | --- | --- |
| pytest | `uv run --frozen pytest -o junit_family=xunit1 --junitxml=<結果XMLの絶対パス> <対象>` | `--junit-xml <結果XML> --junit-record <実行記録>` |
| vitest | `pnpm exec vitest run --reporter=json --outputFile=<結果JSONの絶対パス> <対象>` | `--test-format vitest --test-file <結果JSON> --test-record <実行記録>` |
| Playwright | `env PLAYWRIGHT_JSON_OUTPUT_NAME=<結果JSONの絶対パス> pnpm exec playwright test --reporter=json <対象>` | `--test-format playwright --test-file <結果JSON> --test-record <実行記録>` |
| dotnet test | `dotnet test <対象> --logger "trx;LogFileName=<結果TRXの絶対パス>"` | `--test-format trx --test-file <結果TRX> --test-record <実行記録>` |

各入力は`verification-record -- --output <未判定検証記録>`か`exec-review-evidence-check -- <完成条件証拠>`に続けて指定する。
まず`--list-results --results-counts`で状態と基準差・診断群の件数を読む。`--result-status <状態>`・`--result-change <差の種類>`・`--result-diagnostic-group <群番号>`と`--results-identifiers`で対象群の全識別子へ進む。続いて識別子表示を外して群の詳細、または`--result-test <完全テスト名>`・`--result-diagnostic <識別子>`の反復で個別詳細を取得する。全詳細と全識別子付き要約の`--results-summary`も使える。
同じ入力とテスト名へ`--select-row <配列:N> --mode append`を指定すると、その要求行へ根拠を記入できる。
レビュー側は行ごとの`--row-outcome`と`--reviewed-head`も指定し、実装側は判定欄を空に保つ。
基準版を比べる場合は`--baseline-junit-xml`・`--baseline-junit-record`か、`--baseline-test-format`・`--baseline-test-file`・`--baseline-test-record`も渡す。
名前・状態・所在と基準版との差は公開操作が生成し、担当は各結果の要求への適合を判定する。
形式と実行記録の不一致、不明な形式、破損は、保存前の診断が示す次の操作に従って直す。同じ完全名が複数ある結果は、結果中の出現順の1始まりの`#<番号>`を付けた完全名として取得・指定する。

## 直接影響範囲の類型

品質の定義は`agent-basics:check-execution`の`references/verification-scope.md`「直接影響範囲の類型」を適用する。

## 範囲の決め方

品質の定義は`agent-basics:check-execution`の`references/verification-scope.md`「範囲の決め方」を適用する。
