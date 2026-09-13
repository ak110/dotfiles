# TypeScript記述スタイル

## 言語スタイル

- importについて
  - 型のみのimportには`import type`を使う（実行時のランタイム依存を減らしバンドルサイズを抑えるため）
  - barrel export（`index.ts`）の乱用を避ける（ツリーシェイキングを阻害するため）
- モジュールシステム
  - モダンプロジェクトはESM（`"type": "module"`）を使う
  - Default exportよりNamed exportを優先する（tree-shakingが機能しやすく、リネーム時の追従やIDE補完も確実になるため）
- 厳格な型付けを採用する（`strict: true`。null安全・暗黙any排除を徹底するため）
  - 可能であれば`noUncheckedIndexedAccess`も有効化する
   （配列・`Record`アクセス結果を`T | undefined`として扱い、境界外アクセスを型で検知できる）
- 型について
  - `any`の使用は極力避ける（型チェックを回避してしまうため）。やむを得ない場合は`unknown` + 型ガードを優先する
  - `as`による型アサーションより型ガード（`is`／`satisfies`）を優先する（実行時の型不一致を防ぐため）
  - union型（`"a" | "b"`）を`enum`より優先する（tree-shakingしやすく、型のnarrowingも自然なため）
  - `switch`の網羅性チェックには`satisfies never`を使う
- JSDocコメントは公開APIと非自明な関数・クラスに記述する
  - コードから自明な内容は省略する（`@param`・`@returns`も同様）
  - ファイル概要の`@fileoverview`は内容がファイル名から自明でない場合に記述する
- エラーハンドリング
  - `catch`の引数は`unknown`として扱い、`instanceof`で型を特定する
- `null`は使わず`undefined`を使い、APIから`null`が返される場合は`?? undefined`で変換する
 （「値がない」表現を1つに統一するため）
- 未使用の変数・引数には`_`プレフィックスを付ける
- セキュリティ上の危険パターン
  - `eval()`／`new Function()`はユーザー入力に対して使わない
  - `innerHTML`／`dangerouslySetInnerHTML`を避け、テキスト挿入には`textContent`やフレームワークのエスケープ機構を使う
  - `JSON.parse()`は信頼できない入力に対してtry-catchで囲み、結果をバリデーションする（zodなどのスキーマバリデーション推奨）
  - SQLはプレースホルダやクエリビルダーを使い、テンプレートリテラルで直接組み立てない
  - オブジェクトのマージ・コピーでプロトタイプ汚染を防ぐ
   （`Object.create(null)`やキーの検証。`__proto__`・`constructor`・`prototype`のキーを拒否する）
  - URL・ファイルパスは文字列結合ではなく`URL`／`path.join`等の専用APIで構築する
- 対象プロジェクトのTypeScriptバージョンで利用できる機能は公式リリースノートで確認する
  <https://www.typescriptlang.org/docs/handbook/release-notes/overview.html>

## 非同期処理

- 非同期関数の呼び出しは既定で`await`する。
  後続処理が結果に依存する場合は必ず`await`を用いる
- 戻り値の`Promise`を意図的に無視する（fire-and-forget）場合は
  `void func()`形式で明示する。`await`忘れとの区別を付けるため`void`を省略しない
- 例外を握り潰さない。`void`で無視する非同期処理でも
  例外が伝播しない設計の場合は明示的に`.catch(...)`を付ける
- Biomeやtypescript-eslintの`no-floating-promises`ルールを
  有効化している場合、`void`明示で当該ルールを通過させる

## テストコード（vitest）

- テストコードは`vitest`で書く
- `describe`は原則1階層、ネストは2階層まで
- 類似パターンの網羅には`it.each()`を活用する
- テストデータ生成ヘルパー（`makeXxx(overrides)`）で本質的でないセットアップを共通化する
- 非同期テストでは`await expect(...).resolves`／`.rejects`を活用する
- セットアップ／ティアダウン
  - `beforeAll`／`afterAll`でコストの高い初期化を共有する
  - 時間依存のテストは`vi.useFakeTimers()`で制御し、実時間の`sleep`を避ける
  - `afterEach`で`vi.restoreAllMocks()`／`vi.useRealTimers()`を確実に呼ぶ
- モック／スパイ
  - `vi.mock()`はファイル先頭へホイスティングされる点に注意（動的な値の参照不可）
  - 外部モジュール全体のモックより`vi.spyOn()`での部分モックを優先する
