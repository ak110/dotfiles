# Alpine.js記述スタイル

対象の版: Alpine.js v3系（公式ドキュメント <https://alpinejs.dev/start-here>）。
本書は、Alpine.jsを使う実装を書く・直す時に、版に固有の書き方と誤りやすい事項を示す。
CSPとHTMLの挿入の規定は`agent-basics:writing-standards/references/quality-principles-constraints.md`の適用である。

## 適用範囲の判断

- Alpine.jsは既存のHTMLへ軽い振る舞いを足す用途に限り、ルーティングや大きな状態管理が要るSPAにはVueやReactを選ぶ。
- `x-data`は機能の境界ごとに分け、状態の依存をコンポーネントの中に閉じる。

## x-dataとAlpine.data()の使い分け

- 1つのコンポーネントで完結する状態は`x-data`に書き、複数の箇所で使うロジックや複雑な条件は`Alpine.data()`へ切り出す。

## x-ifとx-showの使い分け

- 表示の切り替えだけなら`x-show`、DOMの生成と破棄を制御する時は`x-if`を使い、`x-if`は`<template>`に付ける。

## x-forとkeyの指定

- `x-for`は`<template>`に付け、その直下にルート要素を1つだけ置く。
- 並び替え、挿入、削除が起こりうるループでは、`:key`にIDなどの一意な値を指定する（無いとDOMが再利用され、入力値やイベントリスナーの対応がずれる）。

## CSP対応とセキュリティ

- CSPで`unsafe-eval`を許可できない環境では`@alpinejs/csp`のビルドを使う。
  標準のビルドはテンプレート式を`eval`相当の仕組みで評価するため、`nonce`による`strict-dynamic`だけを許すCSPでは動かない。
- CSPビルドはJavaScript式の安全な部分集合だけを解釈するため、複雑なインライン式は`Alpine.data()`の関数やgetterへ移す。
- 信頼できないデータを`x-html`や`$el.insertAdjacentHTML()`へ渡さず、テキストは`x-text`か補間で描画し、HTMLはサニタイズ済みの文字列だけを挿入する。
  `$el.insertAdjacentHTML()`はCSPビルドでは使えない。

## 初期化のタイミングとFOUC対策

- scriptに`defer`を付け、プラグイン、コンポーネントのデータ、ストアの登録は、初期化の前に発火する`alpine:init`の中で行う。
- 初期化前のDOMが表示される現象（FOUC）は`x-cloak`で防ぎ、CSSに`[x-cloak] { display: none !important; }`を定義する。

## ストア（Alpine.store）とコンポーネント間通信

- 複数のコンポーネントで共有する状態は`Alpine.store`に集め、状態と更新のメソッドを1か所で管理する。
