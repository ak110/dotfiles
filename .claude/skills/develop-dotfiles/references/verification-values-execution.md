# dotfilesの検証で使う値：検証コマンドの実行

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| テストとCIの所要基準を使う前 | `../../../../agent-basics/skills/writing-standards/references/testing-speed.md` |
| テストとCIの所要基準を使う前 | `agent-basics:check-execution/references/diagnostics-results.md` |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations-launch.md` |

## 検証コマンドの実行

1. 消費側を漏らさず検証するため、`verification-values-scope.md`「変更範囲の検証の値」で対象を確定する。
   `.venv`を再作成し得るチェック（`uv run --python`や依存更新など）は同じ仮想環境パスで直列に実行し、並行する場合は別の仮想環境パスを指定する。
2. 差分を保護するため、実行目的に合う修正条件を選ぶ。
   自動修正には`make format`（`uv run --frozen pyfltr fast`）、調査・計測には`--no-fix`を使う。
   フォーマッター段（`ruff-format`、`uv-sort`、`shfmt`、`prek`、`sync-generated-files`）は`--no-fix`でも書き換えて終了0となるため、読取専用ではこれらの段を除く。
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
     枠はホスト全体で2つあり（pyfltr 3.30.0以降）、3件目以降の検証は空きを待つ。
     入れ子の実行は祖先の所有を継承する。
4. 確定した対象へ全チェックを適用し、共有実行枠の占有を減らすため、静的チェックを先に通してからpytestを含む実行を1回行う。
   pyfltrは1回の実行の間ずっと枠を保持するため、静的チェックで失敗する実行にpytestを含めると、枠を数分ずつ無駄に占有する。
   2026年11月12日の同時数12の実測では、実装担当の`pyfltr run`のうち失敗した18件中12件が静的チェックだけの失敗で、その12件でもpytestが流れていた。
   - 初回は`uv run --frozen pyfltr run --disable pytest <対象ファイルの絶対パス>`で静的チェックを実行し、失敗が無くなるまで直す。
     修正後の再実行は失敗したチェックへ限定する。
   - 静的チェックが通ったら、commitする内容を確定してから、MCPの`run`へパスを渡すか`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を`commands`指定なしで1回実行する。
     このpytestが失敗した場合は直した後に同じ形で実行し直す。
   失敗テストだけの再確認は`--only-failed --from-run <初回のrun_id>`、単一試験の確認は対象ファイルと必要なら`-k`を渡した直接pytestを使い、収集件数と想定の一致を確かめる。
   `--from-run`を省いた`--only-failed`は、他の作業ツリーを含むホスト全体の直近の実行を参照する。
   その実行に失敗が無ければ何も実行せずに終了0となり、自分の失敗が残っていても成功として報告される。
   pytestを含む実行には両プロジェクトの`pytest-always-targets`が加わるため、本書の横断テストの項で重複実行を避ける。
   commitのhookは、同じ内容でpytestが成功した`pyfltr run`があればpytestを外して実行する（判定条件は`scripts/pyfltr_precommit.py`の冒頭）。
   pytestを含む実行の後にファイルを変えた場合と、commitしない差分を作業ツリーに残した場合は、hookはpytestを再び実行する。
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
