# TypeScript記述スタイル

本書は、TypeScriptのコードとテストコードについて、一般的な作法から導けない事項を定める。
共通の設計と実装の基準は`implementation-time.md`が、セキュリティの一般作法は同資料の「セキュリティ・ロギング・エラー処理」が定める。

## 言語スタイル

- `null`は使わず`undefined`を使い、APIから`null`が返される場合は`?? undefined`で変換する
 （「値がない」表現を1つに統一するため）
- 対象プロジェクトのTypeScriptバージョンで利用できる機能は公式リリースノートで確認する
  <https://www.typescriptlang.org/docs/handbook/release-notes/overview.html>

## 非同期処理

- 戻り値の`Promise`を意図的に無視する（fire-and-forget）場合は`void func()`形式で明示する。
  `await`忘れとの区別を付けるため`void`を省略しない。
  Biomeやtypescript-eslintの`no-floating-promises`ルールを有効化している場合、この`void`明示で当該ルールを通過させる
- `void`で無視する非同期処理でも、例外が伝播しない設計の場合は明示的に`.catch(...)`を付ける

## テストコード（vitest）

- テストコードは`vitest`で書く
- `vi.mock()`はファイル先頭へホイスティングされるため、動的な値を参照できない
- 時間依存のテストは`vi.useFakeTimers()`で制御し、`afterEach`で`vi.restoreAllMocks()`と`vi.useRealTimers()`を呼ぶ
