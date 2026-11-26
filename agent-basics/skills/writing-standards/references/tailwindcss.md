# Tailwind CSS記述スタイル

Tailwind CSSで実装する担当がビルドで検出できるクラスとテーマを使い、画面を使う人に操作に応じた表示を届ける。
対象の版: Tailwind CSS v4系（公式ドキュメント <https://tailwindcss.com/docs>。v3からの移行点を含む）。
本書は、Tailwind CSSを使うコードを書く・直す・レビューする時に、v4で誤りやすい事項を示す。
一般知識で導ける作法を書かないのは`agent-basics:writing-standards/references/quality-principles-subtraction.md`の適用である。

## セットアップと設定（v4のCSSファースト設定）

- `tailwind.config.js`を新しく作らず、CSSの`@theme`でデザイントークンを定義する（努力目標。JavaScriptの設定は互換のための`@config`経由でだけ読まれるため）。
- エントリポイントのCSSでは`@import "tailwindcss";`で読み込む。
- トークンは`@theme { --color-brand-500: oklch(...); }`のようにCSS変数で定義し、独自のトークンも`--color-*`や`--breakpoint-*`などの公式の名前空間に合わせる。
- 組み込みのカラーパレットを全て置き換える時は、`--color-*: initial;`で消してから定義する。
- ダークモードの条件を変える時は`@custom-variant dark (&:where(.dark, .dark *));`でバリアントを定義し直す（v4に`darkMode`の設定キーは無い）。
- クラス名の接頭辞は`@import "tailwindcss" prefix(tw);`で指定する。

## ユーティリティファーストの運用原則

- スタイルはユーティリティクラスで指定し、繰り返すUIのパターンはコンポーネントにまとめ、`@apply`の多用でスタイルを二重に管理しない。
- CSS ModulesやVue・Svelteの`<style>`など別のバンドルで処理されるスタイルでは、`@apply`より`var(--color-blue-500)`のようなCSS変数の参照を選ぶ。
  `@apply`と`@variant`には`@reference`でのテーマの読込が要り、ビルドも遅くなる。
- 繰り返す任意の値（`w-[123px]`など）はトークンにし、詳細度の競合はセレクターか読込の順序で解決して`!important`を常用しない。

## クラス名の並び順

- クラス名の並びはプロジェクトのフォーマッターに任せ、Prettierを使う環境では`prettier-plugin-tailwindcss`を使う（努力目標。手で並べる手間を省くため）。

## 動的なクラス名生成の回避

- 条件分岐や対応表の中でも`bg-red-600`のように完全なクラス名を書く。
  Tailwindはビルド時にソースを文字列として走査するため、実行時に結合して作るクラス名のCSSは生成されない。
