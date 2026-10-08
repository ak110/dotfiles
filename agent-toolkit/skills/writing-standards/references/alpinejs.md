# Alpine.js記述スタイル

本書はAlpine.jsを用いる実装の記述スタイル基準を定める。
対象バージョン: Alpine.js v3系。公式ドキュメントは<https://alpinejs.dev/start-here>を参照する。

## 適用範囲の判断

- Alpine.jsは既存HTMLへの軽量な振る舞い付与に限定して使う（ルーティング・状態管理基盤を要するSPA的用途ではVue/Reactを選ぶ）
- x-dataは機能境界で分け、状態の依存を局所化する

## x-data・Alpine.data()の使い分け

- 単発の局所状態はx-data、再利用や非自明な判定はAlpine.dataへ分け、ロジックの読解と検証を容易にする

## x-if/x-showの使い分け

- 表示の切替はx-show、DOMの生存期間を変える場合はx-ifを選ぶ
- x-ifは対象要素へ直接付与せず、templateで囲む

## x-forとkey指定

- `x-for`は`<template>`タグに付与し、直下に単一のルート要素を置く
- 配列の並び替え・挿入・削除が発生し得るループでは`:key`にID等の一意な値を指定する（キー未指定だとAlpineがDOM要素を誤って再利用し、フォーム入力値やイベントリスナーの紐付けがずれるため）

## CSP対応・セキュリティ

- Content Security Policyで`unsafe-eval`を許可できない環境では標準ビルドではなく`@alpinejs/csp`ビルドを使う。標準ビルドはテンプレート式の評価に`eval`相当の機構を使うため、`script-src`に`nonce`ベースの`strict-dynamic`のみを許可する構成と両立しない
- CSPビルド利用時は複雑なインライン式を避け、ロジックを`Alpine.data()`の関数・getterへ抽出する（CSPビルドの評価器はJavaScript式の安全なサブセットのみを解釈するため）
- `x-html`はユーザー入力・外部由来の信頼できないデータへ使わない（`innerHTML`へ直接反映するためXSSに直結する）。テキストの表示には自動エスケープされる`x-text`・テキスト補間を使う
- `$el.insertAdjacentHTML()`等のDOM直接操作による代替HTML注入も同様に避ける（`x-html`と同一のXSSリスクを持ち、CSPビルドではサポート対象外）。HTML挿入が必要な場合はサニタイズ処理を経由した信頼済み文字列に限定する

## 初期化タイミングとFOUC対策

- deferでDOM解析後に初期化し、プラグイン・data・storeは初期化前のalpine:initで登録する
- 未処理のDOMの露出はx-cloakで防ぐ。`[x-cloak] { display: none !important; }`のCSSも必要である

## ストア（Alpine.store）とコンポーネント間通信

- 共有状態はAlpine.storeへ集約し、状態と更新操作の所有をそろえる。単一値で完結する状態は単一値登録でよい
