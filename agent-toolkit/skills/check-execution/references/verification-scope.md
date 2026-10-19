# 変更範囲の検証の対象選定

本書は変更範囲の検証で動かすチェックとテストを選ぶエージェント（計画担当、実装担当、実行レビュー担当、計画なしで変更するエージェント）へ、対象を選ぶ判断基準を与える。
変更範囲の検証は変更ファイルとその直接影響範囲を対象にし、リポジトリ全体の検証はCIへ委ねる。全体検証の判定とpush前に実行するチェックは`agent-toolkit:commit`の`references/publish.md`「検証とCI」が定める。
直接影響範囲を外すと、そのテストの失敗はpush後のCIで初めて見つかり、修正と再実行の往復が増える。

## 直接影響範囲の類型

変更された契約を読む・呼び出す主体と、その契約を期待値に持つテストをリポジトリ全体から集める。参照名に加えて共通の入力・出力・起動手段からも関係をたどり、固定文字列検索は所在の補助とする。
想起の手掛かり: import・継承・共有関数、横断テスト、文字コード、コマンド・hook、通知・標準エラー・終了後処理、生成物・スナップショット、設定パーサーと設定・定義を別の文法で読む試験、共有fixtureと試験補助の消費側、実行中の再読込、親子で共有する時間上限。

次の対象を変更した場合は、それを読むチェックとテスト全体が直接影響範囲になる。対象は共有設定、pytestの`conftest.py`の補助やfixture、テストツール・lint・型チェックの設定、チェックとテストを動かすインタープリターの版指定（`.python-version`、CIのmatrixのPython版など）である。読込先が増減しない実装変更も同じ扱いとする。読込範囲が全テストに一致する場合もローカルで実行し、CIだけへ委ねない。

## 範囲の決め方

検証手段の選定時に、`agent-toolkit:wi-standards`の`references/awi-body.md`「完成条件」の自己観測と未確認範囲の扱いを適用する。

- 横断テストはプロジェクト規範の識別手段と実行形で選ぶ。先行チェックの結果を再利用できるのは、同じ内容に対する実行が完了している場合だけとする。後続hookの対象と自動実行のない対象も含め、修正・rebase・統合で実体が変われば再実行する
- 複数の呼び出し手段が共有する処理や契約を変え、読む側を列挙しきれない場合は、検証単位全体（パッケージ全体のテストなど）を実行する
- 名前を削除または改名する変更では、変更ファイルの外に残る参照を検出する静的解析（型チェックなど）をリポジトリ全体へ実行する
- 差分指定（差分オプション、`git diff --name-only`など）を使う場合は、読む側のテストを別の引数で加える
- 対象は実差分で確定し、計画の列挙との差を読む側まで補う。除外理由は検証記録（計画では`## 要件・外部仕様`の直接影響範囲）へ残す
- 既存の検証コマンド（計画の検証行、前回の実行、CI記録の再実行など）を再利用する実行も対象選定に当たる。実行の直前に実差分から直接消費側を求め直し、コマンドの対象との差を加える。修正・会話圧縮の後の再開でも同じとする
- 入力パスから試験を選ぶツールの成功とテスト総数は、直接消費側を被覆した根拠にならない。契約から求めた消費側の試験ごとに保存済みの実行結果の合否を対応付け、結果に無い試験は加えて再実行する。対応付けを列挙しきれない共有契約は、前掲の検証単位全体の実行へ切り替える

## 実行のしかた

- 計画で確定した検証行は本スキルの`SKILL.md`「統合実行ツール経由の起動」の`plan-verify --summary-only`で実行し、順序・コマンド・状態・終了コード・記録参照・保存失敗と未記録集合を一覧で受領する。凍結後の訂正済み検証表を引き継いだ場合は、同節の`--verification-plan`を指定して訂正後の条件を実行する。訂正表の書式・保持・引き継ぎは`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「検証と終端工程」に従う
- 保存済みの結果は子を再実行せずに読む。plan-verifyの結果まとめは`results_path`を`atk run-command --records-file <絶対パス>`へ渡す。計画なしの保存済み実行は`--record <record_path>`の反復で渡す。どちらの方法でも順序・argv・cwd・取得版・Git状態・子とwrapperの終了状態・両出力へ到達する。旧記録の`unrecorded`は未記録として保持し、読取失敗・欠損・破損を成功と扱わない
- 保存結果の試験・診断を完成条件へ対応付けるときは、`atk run-script`の`verification-record`か`exec-review-evidence-check`で、後掲「構造化試験結果の取得と記入」の入力を使う。診断は`--diagnostics-file`・`--diagnostics-record`・`--diagnostics-conditions`と必要な`--diagnostics-source`から読む。`--list-results --results-summary`と完全テスト名・診断識別子の詳細で意味を判定する。`--select-row <配列:N>`と`--result-test`・`--result-diagnostic`・`--evidence-file`・`--mode`で要求行へ保存する（原文・sourceの転記や手製のJSONは不要）。診断の比較条件と保存時本文は取得時の保存物を使う
- ロックファイルかインタープリターの版指定を変えた後は、対象リポジトリの開発手順が定める環境同期を実行してから検証する（同期前の仮想環境はCIと異なる結果を返す）
- 初回は変更ファイルに適用できるチェックを全て動かし、修正後の再実行は失敗したチェックへ限定してよい
- 並行する別の書込主体が新しい呼び出し元を加えると結果が変わる検証（共有契約やパッケージ外の呼び出し元を変えた変更の検証、全体を走査するチェック）は、プロジェクト規範が統合後の検証として定める場合に統合時にも実行する

### 構造化試験結果の取得と記入

試験はプロジェクトの起動形に次の出力指定を加え、`atk run-command`で実行記録とともに保存する。
結果ファイルはmanaged-temp内の絶対パスを指定し、返された`record_path`を取込みへ渡す。

| runner | `atk run-command --cwd <worktree> --`に続ける実行形 | 両公開操作へ渡す入力 |
| --- | --- | --- |
| pytest | `uv run --frozen pytest -o junit_family=xunit1 --junitxml=<結果XMLの絶対パス> <対象>` | `--junit-xml <結果XML> --junit-record <実行記録>` |
| vitest | `pnpm exec vitest run --reporter=json --outputFile=<結果JSONの絶対パス> <対象>` | `--test-format vitest --test-file <結果JSON> --test-record <実行記録>` |
| Playwright | `env PLAYWRIGHT_JSON_OUTPUT_NAME=<結果JSONの絶対パス> pnpm exec playwright test --reporter=json <対象>` | `--test-format playwright --test-file <結果JSON> --test-record <実行記録>` |
| dotnet test | `dotnet test <対象> --logger "trx;LogFileName=<結果TRXの絶対パス>"` | `--test-format trx --test-file <結果TRX> --test-record <実行記録>` |

各入力は`verification-record -- --output <未判定検証記録>`か`exec-review-evidence-check -- <完成条件証拠>`に続けて指定する。
まず`--list-results --results-summary`で状態を読み、`--result-test <完全テスト名>`を反復して詳細を取得する。
同じ入力とテスト名へ`--select-row <配列:N> --mode append`を指定すると、その要求行へ根拠を記入できる。
レビュー側は行ごとの`--row-outcome`と`--reviewed-head`も指定し、実装側は判定欄を空に保つ。
基準版を比べる場合は`--baseline-junit-xml`・`--baseline-junit-record`か、`--baseline-test-format`・`--baseline-test-file`・`--baseline-test-record`も渡す。
名前・状態・所在と基準版との差は公開操作が生成し、担当は各結果の要求への適合を判定する。
形式と実行記録の不一致、不明な形式、破損と完全名の重複は、保存前の診断が示す次の操作に従って直す。
