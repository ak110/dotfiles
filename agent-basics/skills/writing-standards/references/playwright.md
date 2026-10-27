# Playwrightテスト記述スタイル

本書は、Playwright Testを用いてブラウザテストを作成・レビューする際の実装基準をまとめたものである。
対象バージョンはPlaywright Test v1系（参考実利用バージョン: 1.60）である。公式のベストプラクティスについては、<https://playwright.dev/docs/best-practices>を参照する。
本書はPythonバインディング（`playwright.async_api`、`playwright.sync_api`）を使用するテストにも適用し、メソッド名などの綴りが異なるAPIはPython側の対応する名前に読み替えて適用する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `testing.md#「安定性: flakyテスト防止」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「安定性: flakyテスト防止」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「テストの原因調査」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「テストの原因調査」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `testing.md#「失敗テストの除外による回避禁止」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「失敗テストの除外による回避禁止」` |

## ロケーター戦略

- ロケーターの選択は、エンドユーザーが画面上で認識するロール（role）と表示名（accessible name）を最優先の候補とし、これらで一意に特定できない要素には`testId`属性を使用する。DOMの階層構造に依存したセレクター指定は、他の手段が取れない場合の最終手段とする。

## アサーション

- 画面の観測可能な状態変化は、自動リトライ機能を備えたWeb-first assertionsを用いて検証し、特定の1時点のDOM状態を取得して同期的に判定するような検証は避ける。
- 要素の状態変化を待つ際は、ロケーターが備える自動待機（Actionability checks）や`expect().toPass()`を活用する。固定時間を待機するAPI（JavaScriptの`page.waitForTimeout`やPythonの`page.wait_for_timeout`）や、ループによる独自のリトライ待機処理の実装は避ける。その根拠は、`testing.md`の「安定性: flakyテスト防止」に記載されている。
- 画面の初期表示に伴う自動選択やデータ取得、自動リロード、遅れて適用されるCSS、再レンダリングなど、バックグラウンドの非同期処理がある場合は、処理結果の画面反映を待ってから後続の検証に進む。これには外部状態の変更や要素サイズの測定、ネットワークリクエストの検証などが含まれる。最初に画面へ要素が表示されたことだけをもって全体の準備完了とみなす判断は避ける。ローカル環境ではタイミングよく成功する場合でも、リソースが逼迫したCI環境では非同期処理の反映がテストの操作よりも遅れて届くためである。

## テスト分離

- 各テストケースは実行順序や他のテストの結果に依存しないように独立させ、テスト間の状態を確実に隔離・破棄する。ユーザーのログイン認証状態は、認証セットアップ用のプロジェクトと`storageState`を活用して効率的に再利用する。
- 外部のサードパーティサイトなど制御できない外部サービスはPlaywrightのNetwork API（モックやルーティング）で代用し、テストの合否は手元で制御可能なレスポンスに基づいて判定する。

## flaky対策

テストが不安定（flaky）になった際の原因調査や修正方針を決定する前には、`agent-basics:bugfix`を起動し、`agent-basics:bugfix/references/root-cause-analysis.md`の「テストの原因調査」を適用する。本節で述べるトレース採取や再試行設定は、その原因調査に必要なデータを収集するための手段である。

- テストの自動再試行（`retries`）はCI環境でのみ有効化し（ローカル環境では0とする）、その目的は後述するトレースデータの採取に限定する。あわせて`failOnFlakyTests`設定（CLI引数では`--fail-on-flaky-tests`）を有効にし、再試行によって最終的にパスしたテストについても、`testing.md`の「失敗テストの除外による回避禁止」に従って根本原因を特定・修正する。
- 設定ファイルで`trace: 'on-first-retry'`を指定し、CI環境で発生した失敗のデバッグにはトレースビューアーを優先して活用する。

## Page Object Model採用の判断基準

Page Object Model（POM）はテストコードを構造化するための一手法であり、コードの再利用性やUI変更時の修正箇所の集約といったメリットが、POMクラスの保守コストを上回る場合に採用する。小規模な操作手順であれば、テスト関数内に直接記述してよい。

## 並列実行・CI設定

- CIでのテスト実行はコード変更に近いタイミングで実行し、計測された総実行時間に応じてシャーディング（sharding）による並列分割を検討する。
- テスト対象のブラウザ環境はPlaywright設定の`projects`で明確に分離し、Playwright本体および同梱されるブラウザバイナリを定期的に最新化する。
- 非同期関数の呼び出しにおける`await`の付け忘れなどによる処理の完了待ちは、テスト実行時だけでなく静的解析（lint）の段階でも検知できるように設定しておく。
