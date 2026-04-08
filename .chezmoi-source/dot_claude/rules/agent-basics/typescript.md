---
paths:
  - "**/*.ts"
  - "**/*.tsx"
---

# TypeScript記述スタイル

- importについて
  - 型のみのimportには `import type` を使用する (実行時のランタイム依存を減らしバンドルサイズを抑えるため)
  - barrel export (`index.ts`) の乱用を避ける（ツリーシェイキングを阻害するため）
- モジュールシステム
  - モダンプロジェクトは ESM (`"type": "module"`) を使う
  - Default export より Named export を優先する (tree-shaking が効きやすく、リネーム時の追従や IDE 補完も確実になるため)
- 厳格な型付けを行う (`strict: true`) (null 安全・暗黙 any 排除を徹底するため)
  - 可能であれば `noUncheckedIndexedAccess` も有効化する (配列・`Record` アクセス結果を `T | undefined` として扱い、境界外アクセスを型で検知できる)
- 型について
  - `any` の使用は極力避ける (型チェックをすり抜けるため)。やむを得ない場合は `unknown` + 型ガードを優先する
  - `as` による型アサーションより型ガード (`is` / `satisfies`) を優先する (実行時の型不一致を防ぐため)
  - union型 (`"a" | "b"`) を `enum` より優先する（tree-shakingしやすく、型の絞り込みも自然なため）
  - `switch` の網羅性チェックには `satisfies never` を使用する
- JSDocコメントを記述する
  - ファイルの先頭に`@fileoverview`で概要を記述
  - 関数・クラス・メソッドには機能を説明するコメントを記述
  - 自明な`@param`や`@returns`は省略する
- エラーハンドリング
  - `catch` の引数は `unknown` として扱い、`instanceof` で型を絞り込む
- `null`は使わず`undefined`を使用、APIから`null`が返される場合は`?? undefined`で変換 (「値がない」表現を 1 つに統一するため)
- 未使用の変数・引数には `_` プレフィックスを付ける
- セキュリティ上の危険パターン
  - `eval()` / `new Function()` はユーザー入力に対して使わない
  - `innerHTML` / `dangerouslySetInnerHTML` を避け、テキスト挿入には `textContent` やフレームワークのエスケープ機構を使う
  - `JSON.parse()` は信頼できない入力に対してtry-catchで囲み、結果をバリデーションする（zodなどのスキーマバリデーション推奨）
  - SQLはプレースホルダやクエリビルダーを使い、テンプレートリテラルで直接組み立てない
  - オブジェクトのマージ・コピーでプロトタイプ汚染を防ぐ（`Object.create(null)` やキーの検証。`__proto__`・`constructor`・`prototype` のキーを拒否する）
  - URL・ファイルパスは文字列結合ではなく `URL` / `path.join` 等の専用APIで構築する
- 他で指定が無い場合のツール推奨:
  - パッケージマネージャー: `pnpm`（厳密な依存解決でphantom dependencyを防止）
  - 一度限りのコマンド実行には `npx` の代わりに `pnpx` を使う (pnpm と同じ依存解決・キャッシュを再利用できるため)
  - リンター/フォーマッター: `Biome`（lint + formatを1ツールで高速に処理）
    - Biomeが対応していないルール（React固有等）が必要な場合のみESLint + Prettierを併用
- 新しい TypeScript バージョンの機能を積極的に使う (LLM の知識は古く、古い書き方が出現する傾向があるため明記する)
  - TS 5.0+: `const` 型パラメータ (`function f<const T>(x: T)`) でリテラル型を自動保持する
    (呼び出し側で `as const` を書かずにリテラル推論が効くため)
  - TS 5.0+: `export type *` で型のみの再エクスポートを明示する (実行時コードと型の分離を徹底するため)
  - TS 5.2+: `using` / `await using` 宣言でリソースを自動解放する
    (`try { } finally { dispose() }` が不要。`Symbol.dispose` / `Symbol.asyncDispose` の実装が前提)
  - TS 5.4+: `NoInfer<T>` でデフォルト引数等の型推論から特定の型パラメータを除外する
    (デフォルト値と他引数の型不一致をコンパイル時に検出できるため)
  - TS 5.5+: 推論される型述語 (inferred type predicates) を活用する
    (`array.filter(x => x !== null)` の結果が `T[]` に絞り込まれ、明示的な型ガードが不要なケースが増える)
