# Playwrightテスト記述スタイル

本書はPlaywright Testでブラウザーテストを書き、レビューするときの基準を示す。
対象バージョン: Playwright Test v1系（参考実利用バージョン: 1.60）。公式のベストプラクティスは<https://playwright.dev/docs/best-practices>を参照する。
本書はPythonバインディング（`playwright.async_api`・`playwright.sync_api`）を用いるテストへも適用し、綴りが異なるAPIは同じ機能のPython側の名前へ読み替える。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `testing.md#「安定性: flakyテスト防止」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「安定性: flakyテスト防止」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md#「テストの原因調査」` | 判断 | `section:agent-basics:*/skills/bugfix/SKILL.md#「テストの原因調査」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `testing.md#「失敗テストの除外による回避禁止」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「失敗テストの除外による回避禁止」` |

## ロケーター戦略

- エンドユーザーが認識するroleと名前を第一候補にし、特定できない要素はtestIdを使う。DOM構造を前提とする選択は最終手段とする

## アサーション

- 観測可能な状態をweb-first assertionで検証し、一時点の判定で非同期の完了を代用しない
- ロケーターの自動待機・actionabilityチェックまたは`expect().toPass()`で状態変化を待つ。固定時間待機のAPI（JavaScriptの`page.waitForTimeout`、Pythonの`page.wait_for_timeout`）と独自の待機ループの使用は禁止する。根拠は`testing.md`「安定性: flakyテスト防止」にある
- 画面が操作によらず始める非同期処理（初期表示での自動選択と本文の取得、外部変更の通知による再読込、遅れて加わるスタイル、再描画による要素の置換など）は、その観測できる結果を待ってから外部状態の変更、寸法の測定、要求の記録へ進む。最初に可視になった要素を準備完了とみなさない。ローカルでテストが成功しても、負荷の高いCIでは処理の結果がテストの操作より後に届く

## テスト分離

- テストは順序と他の結果から独立させ、状態を隔離・回収する。認証はsetupとstorageStateで再利用する
- 制御できない外部サイトはNetwork APIで代用し、テストの合否は制御下の応答から確定する

## flaky対策

原因の調査と是正の選定の前に`agent-basics:bugfix`を起動する。`agent-basics:bugfix`の`SKILL.md`「テストの原因調査」を適用する。本節のtraceと再試行はその調査の入力を採取する手段である。

- `retries`はCIでだけ有効にし（ローカルは0）、目的は次項のトレースの採取に限る。`failOnFlakyTests`（CLIでは`--fail-on-flaky-tests`）を併せて有効にし、再試行で成功したテストは`testing.md`「失敗テストの除外による回避禁止」に従って根本原因を直す
- `trace: 'on-first-retry'`を設定し、CIのデバッグはトレースビューアーを優先する

## Page Object Model採用の判断基準

POMは構造化の選択肢とし、再利用と変更箇所の集約が維持費用を上回る場合に採る。小規模な操作列はテスト本文へ置いてよい。

## 並列実行・CI設定

- CIは変更に近い時点で実行し、測定した所要時間に応じてshardingを選ぶ
- 対応するブラウザーをprojectsで分離し、Playwrightと同梱ブラウザーを更新する
- `await`の欠落などによる非同期処理の未完了は静的解析でも検出する
