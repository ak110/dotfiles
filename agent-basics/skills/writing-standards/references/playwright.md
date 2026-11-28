# Playwrightテスト記述スタイル

Playwrightでテストを書く担当が画面に現れる結果を互いに状態を共有しないテストで確かめ、ユーザーが再現できる操作結果から合否を判断できるようにする。
対象のバージョン: Playwright Test v1系とPythonのバインディング（公式のベストプラクティス <https://playwright.dev/docs/best-practices>）。
本書は、Playwrightでブラウザーのテストを書く・直す・レビューする時に、誤りやすい待機と診断の採り方を示す。
ロケーター、アサーション、隔離の基準はPythonのバインディングにも当てはめ、設定はランナーごとの契約（Playwright TestとPythonで別）に従う。

## ロケーター戦略

- ロケーターはエンドユーザーが認識するロールとアクセシブルな名前を優先し、一意に決まらない要素には`testId`を使い、DOMの階層に依存するセレクターは最後の手段にする。

## アサーション

- 画面の状態の変化は、ある時点のDOMを取って同期的に判定する代わりに、自動で再試行するWeb-first assertionsで確かめる。
- 変化はロケーターの自動待機（Actionability checks）か`expect().toPass()`で待ち、固定時間の待機（`page.waitForTimeout`、Pythonの`page.wait_for_timeout`）と自前の再試行ループはこれらへ置き換える。
- 初期表示の後に非同期の処理（自動選択、データ取得、自動リロード、遅れて当たるCSS、再描画など）がある画面では、その結果が画面に反映されるのを待ってから、外部の状態の変更、要素の寸法の測定、通信の検証へ進む。
  最初の要素の表示だけで準備完了とみなすと、資源の逼迫したCIでは処理の反映が操作より遅れて失敗する。

## テスト分離

- 各テストは実行順と他のテストの結果に依存させず、ログインの状態は認証用のセットアッププロジェクトと`storageState`で再利用する。
- 制御できない外部のサービスはNetwork API（モックとルーティング）で代用し、合否を手元で制御できる応答で決める。

## flaky対策

テストが不安定（flaky）になったら、`agent-basics:bugfix/SKILL.md`「バグ対応」に従う。
本節のトレースの採取と再試行は、その調査のデータを集める手段である。

- Playwright Testでは`retries`をCIでだけ有効にし（ローカルは0）、目的をトレースの採取に限る。
- あわせて`failOnFlakyTests`（CLIでは`--fail-on-flaky-tests`）を有効にし、再試行で成功したテストも`agent-basics:writing-standards/references/testing.md`「失敗テストの除外による回避禁止」に従って根本原因を直す。
- `trace: 'on-first-retry'`を指定し、CIの失敗はトレースビューアーで調べる。

Pythonのpytest-playwrightの標準のfixtureでは、同プラグインの`--tracing=retain-on-failure`と出力先を使う。
このCLIの設定は自作の`browser.new_context()`には適用されないため、自作のコンテキストを持つfixtureは次を自分で管理する。

- 操作の前に`context.tracing.start()`、終了時に`stop()`を呼び、失敗時は保持して成功時は破棄する。
- traceはassertionを含まないため、試験の識別子とランナーの失敗内容も対応付けて残す。
- 保存先はfixtureの後始末の対象から外し、CIのartifactの取得まで残ることを確かめる。
- 証拠の保存の失敗で元の試験の失敗を上書きせず、ブラウザーとサーバーは通常どおり終了する。
- 再試行を必須にするのは、診断の採取以外の理由がある時に限る。

## Page Object Model採用の判断基準

Page Object Modelは、再利用とUI変更時の修正箇所の集約による便益がクラスの保守の費用を上回る時に採用し、小さな操作はテスト関数の中に直接書く。

## 並列実行・CI設定

- 対象のブラウザーはPlaywright Testでは`projects`、Pythonではランナーのブラウザーの選択とfixtureで分ける。
- CIでは計測した総実行時間に応じてシャーディングで分割し、`await`の付け忘れはlintでも検出できるように設定する。
