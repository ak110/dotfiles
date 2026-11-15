# dotfilesの検証で使う値：検証コマンドの実行

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations-launch.md` |

## 検証コマンドの実行

1. 消費側を漏らさず検証するため、`verification-values-scope.md`「変更範囲の検証の値」で対象を確定する。
   `.venv`を再作成し得るチェック（`uv run --python`や依存更新など）は同じ仮想環境パスで直列に実行し、並行する場合は別の仮想環境パスを指定する。
2. 差分を保護するため、実行目的に合う修正条件を選ぶ。
   自動修正には`make format`（`uv run --frozen pyfltr fast`）、調査・計測には`--no-fix`を使う。
   統合準備と最終検証の前に、通常runで必要な整形・生成物更新を済ませ、変更内容を確定する。
   以後の読取runは`--no-fix`に加え、通常段でも書き換えるフォーマッターを`--commands`の対象から外す。
   対象版の公開runのheaderにある有効全コマンドと、`pyfltr command-info <コマンド名> --output-format=json`の実効情報を確認する。
   現在の書換え段は`ruff-format`、`uv-sort`、`shfmt`、`prek`、`sync-generated-files`である。
   それらを除いた必要な全チェックを`--commands=<カンマ区切りのコマンド名>`へ指定し、対象版・選定した全コマンド・除外理由を既存の検証記録へ残す。
   設定や版が変わったら一覧を取り直し、追加されたチェックも含めて選ぶ。
   読取runでも、変更範囲・特定した利用側試験・不変条件試験の被覆とverify-runの受理条件は保つ。
   分岐の効果を確かめる差分は`git diff --no-ext-diff --src-prefix=a/ --dst-prefix=b/`で保存し、`git apply`・`git apply -R`で適用・復元する。
   `diff.noprefix=true`では既定の`git apply`が接頭辞なしのパスを解決できないため、この起動形を使い、既存差分とindexは別々に保護する。
3. 起動条件を保つため、直接pytestと共有実行枠の条件を確認する。
   - デバッガー・最小再現・環境切り分けでは直接pytestを使える。
     `-o`・`-p`はpytest固有で、`pyfltr run`へ渡すと終了2となる。
     `-o addopts=''`で設定全体を外す場合は`-p no:cacheprovider`も指定する。
     並列化だけを止める場合は`-n 0`を使う。
     `-p no:xdist`では既存の`-n 4 --dist=worksteal`が未認識となり収集前に終了4となる。
   - 共有実行枠の対象は`make test`、`uv run --frozen pyfltr run`、`git commit`内の`pyfltr fast`、共通pytestプラグインを読むpytestである。
     両プロジェクトの`addopts`は同プラグインを`-p`で読み、`make test-browser`など`-o addopts=''`で解除するものは枠の対象外となる。
     シェルからの起動は`agent-toolkit:/share/check-execution-operations-launch.md`「統合実行ツール経由の起動」の形式と3分類に従う。
   - pytestと統合検証は、開始からworker・子孫の回収まで共有実行枠を保持する。
     収集だけのpytest（`--collect-only`）は枠を取らない。
     枠の数は`agent-toolkit:/share/check-execution-operations-launch.md`「統合実行ツール経由の起動」の定義に従う。
     dotfilesのロックファイルの版では、既定で2枠になる。
     入れ子の実行は祖先の所有を継承する。
4. 確定した対象へ全チェックを適用し、共有実行枠の占有を減らすため、静的チェックを先に通してからpytestを含む実行を1回行う。
   pyfltrは1回の実行の間ずっと枠を保持するため、静的チェックで失敗する実行にpytestを含めると、枠を数分ずつ無駄に占有する。
   2026年11月12日の同時数12の実測では、実装担当の`pyfltr run`のうち失敗した18件中12件が静的チェックだけの失敗で、その12件でもpytestが流れていた。
   - 初回は`uv run --frozen pyfltr run --disable pytest <対象ファイルの絶対パス>`で静的チェックを実行し、失敗が無くなるまで直す。
     修正後の再実行は失敗したチェックへ限定する。
   - 静的チェックが通ったら、commitする内容を確定してから、MCPの`run`へパスを渡すか`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を`commands`指定なしで1回実行する。
     このpytestが失敗した場合は直した後に同じ形で実行し直す。
     `develop`・`master`・`main`以外のブランチ（レーンと内部群の作業ブランチ）では、この実行に`--no-always-targets`を付け、変更ファイルと、変更した契約を利用する試験を実行する（pyfltr 3.31.0以降）。
     非試験モジュールのパスだけでは、そのモジュールを使う別名の試験は選ばれない。
     `uv run --frozen python scripts/pyfltr_precommit.py --consumer-tests <変更ファイル...>`で根拠付き候補JSONを取得する。
     `required=true`の直接import試験を引数へ加え、残る候補は実際の利用関係から採否を決める。
     元のJSONを保持し、曖昧候補ごとに`source`・`test`・`include`（真偽値）・`reason`（非空文字列）を持つ`decisions`配列を追加して既存の検証証拠へ保存する。
     HEAD・変更ソース・候補集合と本文のハッシュが変わった判定は作り直す。
     動的な呼出し、設定の移行、通知の旧期待値、CLI全体の一覧は候補の自動列挙だけで網羅できないため、`verification-values-scope.md`の契約別の探索で補う。
     不変条件試験の全量は、後述のレーンの統合前の実行で1回だけ実行する。
   失敗テストだけの再確認は`--only-failed --from-run <初回のrun_id>`、単一試験の確認は対象ファイルと必要なら`-k`を渡した直接pytestを使い、収集件数と想定の一致を確かめる。
   `--from-run`を省いた`--only-failed`は、他の作業ツリーを含むホスト全体の直近の実行を参照する。
   その実行に失敗が無ければ何も実行せずに終了0となり、自分の失敗が残っていても成功として報告される。
   pytestを含む実行には両プロジェクトの`pytest-always-targets`が加わるため、本書の横断テストの項で重複実行を避ける。
   commitのhookは、`develop`・`master`・`main`以外のブランチ（レーンと内部群の作業ブランチ）ではpytestを外して実行する。
   統合先のブランチでは、同じ内容でpytestが成功した`pyfltr run`があればpytestを外して実行する（判定条件は`scripts/pyfltr_precommit.py`の冒頭）。
   - 統合準備では、手順2の読取runで変更範囲を焦点検証し、失敗箇所の修正と直接影響範囲の再確認、独立文脈レビューの収束を完了する。
     成功済み範囲と修正後の再確認範囲、未実施の最終検証を既存の検証記録へ対応付けて返す。
     この時点では、中間の全量成功runを取得するためだけの再実行は任意とする。
     準備の受理は統合許可を出す判断であり、最終検証の成功やAWIの完成とは区別する。
   - 統合する担当は、許可後に統合先の最新内容へ追随し、最終の統合入力を確定してから、手順2で選んだ全チェックを使う`uv run --frozen pyfltr run --no-fix --commands=<選定した全コマンド> <レーンの変更ファイルと特定した利用側試験の絶対パス>`を成功させる。
     この実行では`--no-always-targets`を付けず、変更ファイル・特定した利用側・不変条件試験をまとめる。
     起動前に`uv run --frozen python scripts/pyfltr_precommit.py --check-argv '<予定argvのJSON配列>' --consumer-review <判定JSONの絶対パス>`で引数と必須範囲を確かめる。
     argvは`["run", "--no-fix", "--commands=...", "<対象パス>"]`の形とし、公開ヘルプと事前確認の診断を使って修正する。
     曖昧候補が無ければ`--consumer-review`を省略できる。
     終了0でも実run成功・版・cwd・HEAD/index・実行後不変は未確認なので、実行後のverify-runは省略しない。
     対象はこの集合であり、リポジトリ全体のpytestを意味しない。
     利用側は群の焦点検証と同じ方法で候補を列挙し、契約別の探索で補った試験も引数へ加える。
     mergeが必要なら新しい内容で実行し、不要なら現在の統合入力を使う。
     最終の統合入力と本文・環境・対象が同じことを既存の実行記録から確認できる成功runは再利用する。
     indexと作業ツリーを最終統合入力HEADへ一致させる。
     ff直前に`uv run --frozen python scripts/pyfltr_precommit.py --verify-run <run_id> --consumer-review <判定JSONの絶対パス>`を実行し、終了0を確かめる。
     意味判定が不要なら`--consumer-review`は省略する。
     このコマンドは、最終内容の単一runにおけるpytestの成功、変更状態と実行時点、および列挙できた利用側試験の被覆を確認する。
     最終run_idとverify-runの結果は、既存の検証記録と統合結果へ対応付ける。
     manifestへ渡すときは、run成功・run_id取得・同じclean入力のverify-run成功までを失敗伝達付きの1操作へまとめる。
     以下のshellファイルは、ログの絶対パスprefix・意味判定JSONの絶対パス・選定済みのrun引数を順に受け取る例である。

     ```bash
     #!/usr/bin/env bash
     set -euo pipefail
     log_prefix=$1
     consumer_review=$2
     shift 2
     uv run --frozen pyfltr run --output-format=jsonl "$@" > "$log_prefix.out" 2> "$log_prefix.err"
     run_id=$(jq -er 'select(.kind == "header") | .run_id' "$log_prefix.out")
     uv run --frozen python scripts/pyfltr_precommit.py --verify-run "$run_id" --consumer-review "$consumer_review"
     ```

     スクリプトは自身のmanaged-tempへ保存し、次のargvをverificationの1項目へ渡す。
     山括弧の値を実在値へ置換し、複数の対象パスは別々の要素にする。

     ```json
     ["bash", "<スクリプトの絶対パス>", "<ログprefixの絶対パス>", "<意味判定JSONの絶対パス>", "--no-fix", "--commands=<選定した全チェック>", "<対象パス>"]
     ```

     意味判定が不要な場合は、その入力と`--consumer-review`の指定を省いたスクリプトを使う。
     手動で最終runを完了した場合も、同入力のverify-runをff前に実行し、両成功とHEAD/indexの証拠を保持してから統合へ渡す。
     検証だけの保存成功を理由にverificationを空にせず、空にする場合は受理まで完了した同入力の両出力・終了状態を既存記録へ対応付ける。
     最終検証が失敗した場合はffせず、既存の是正・必要な再レビュー・統合再開へ戻す。
     検証後の編集や統合先の前進で入力が変わった場合は、追随後の新しい内容で必要な検証と確認をやり直す。
     準備時の成功や過去の別内容の成功は、この最終確認へ代用しない。
     commitごとの不変条件試験を避け、統合直前の最終入力へ1回集約する。
   統合先のブランチでは、pytestを含む実行の後にファイルを変えた場合と、commitしない差分を作業ツリーに残した場合に、hookはpytestを再び実行する。
   後者を避けるため、そのcommitに含めない差分は先に別のcommitへ分けるか、作業ツリーから外してからcommitする。
   - CIが無い、または公開先でCIが実行されずローカルの全体pytestが必要な場合は、`--maxfail=10`を指定し、計画からmanifestまで同じ値を保つ。
     CIが全体を担う本リポジトリの通常のレーン内・統合時・公開前は関連範囲だけを実行する。
     直接pytestでは`--maxfail=10`、pyfltr経由では`--pytest-args='--maxfail=10'`を使い、他のpytest追加引数は同じ値へ併記する。
     少数の独立した失敗を1回で集めつつ、共通の準備処理が壊れて全試験が失敗した場合の出力を有限に抑えるためである。
     10件を超える失敗では未実行の試験が残り、xdistの実行中の試験により最終の失敗数は10を超えうる。
     `addopts`の既定値5、限定検証と失敗箇所の再確認、`make test`の通常起動、CIの`--maxfail=0`、時間上限は変えない。
5. 意図した版と結果を確かめてからコミット対象を確定するため、`git status`で整形による差分を確認する。
   実行時間は`uv run --frozen pyfltr list-runs`で得た実行IDを使い、`uv run --frozen pyfltr show-run <run_id>`で確認する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| dotfilesの検証で使う値：変更範囲の検証の値の基準を適用する前 | `verification-values-scope.md` |
