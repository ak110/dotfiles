# Tailwind CSS記述スタイル

本書はTailwind CSSを使うコードを書き、レビューするエージェントへ、v4系で判断を誤りやすい箇所の基準を示す。
対象バージョン: Tailwind CSS v4系（参考実利用バージョン: 4.3）。v3からの移行点を含む公式ドキュメントは<https://tailwindcss.com/docs>を参照する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time.md` |

## セットアップ・設定（v4のCSS-first設定）

- `tailwind.config.js`を新規作成せず、CSSの`@theme`でデザイントークンを定義する（努力目標。JS設定は互換目的の`@config`経由でだけ読まれる）
- エントリCSSは`@import "tailwindcss";`で読み込む
- トークンは`@theme { --color-brand-500: oklch(...); }`のようにCSS変数として定義する（ユーティリティとCSS変数の双方から参照できる）。カスタムトークンも`--breakpoint-*`・`--color-*`などの名前空間へそろえる
- 標準パレットを丸ごと置き換える場合は`--color-*: initial;`で初期化してから定義する
- ダークモードの判定基準は`@custom-variant dark (&:where(.dark, .dark *));`のようにCSSで再定義する（v4に`darkMode`設定キーは無い）
- プレフィックスは`@import "tailwindcss" prefix(tw);`で指定する

## ユーティリティファースト運用

- ユーティリティを第一選択とし、反復する見た目はコンポーネントへまとめる。`@apply`による二重管理を避ける
- CSSモジュールやVue/Svelteの`<style>`ブロックなど別バンドルのスタイルでは、`@apply`より`var(--color-blue-500)`のようなCSS変数の直接参照を優先する（`@apply`・`@variant`には`@reference`によるテーマ読み込みが要り、ビルド性能も下がる）
- 反復する任意値はトークンへまとめ、特異度はセレクタと読込順で是正する。importantの常用による上書きは禁止する

## クラス整列

- クラス整列はプロジェクトのformatterに委ねる（努力目標）。選択は`implementation-time.md`の標準ツール表に従い、Prettierではprettier-plugin-tailwindcssを使う

## 動的クラス名の禁止

- 条件分岐や対応表には、`bg-red-600`のような完全なクラス名を書く。Tailwindはソースをプレーンテキストとしてスキャンするため、実行時にしか確定しない名前は生成されない
