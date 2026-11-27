# Svelte／SvelteKit記述スタイル

Svelteで実装する担当が状態の保有範囲と描画の境界を定め、ユーザーが初回表示から一貫した画面を使い、自分のデータを保てるようにする。
対象のバージョン: Svelte 5系、SvelteKit 2〜3系。
本書は、runesモードで書くSvelteとSvelteKitのコードで、公式ドキュメント（<https://svelte.dev/docs>）のうち誤りやすいバージョンに固有の事項を示す。
サーバーの状態、CSRF、秘匿値の規定は`agent-basics:writing-standards/references/quality-principles-constraints.md`の適用である。

## runesの使い分け

- リアクティブな状態は、トップレベルの`let`ではなく`$state`で宣言する。
- 派生する値は`$effect`の中で同期せず`$derived`で宣言し、副作用だけを`$effect`に置く。
  runesモードでは`$:`の代わりに`$derived`と`$effect`を使う。
- 外部ライブラリとのDOMの同期は、`$effect`より`{@attach ...}`を選ぶ（公式の推奨）。
- propsは`$props()`の分割代入で宣言し、`export let`はレガシーモードだけで使う。

## スニペットとコンポーネント分割

- 同じスコープで完結する表示の断片はsnippet、独立した状態やファイルをまたぐ再利用が要るものはコンポーネントにする。
- タグの中に直接書いた内容は、暗黙の`children`スニペットとして`$props()`で受け取る。
  名前付きスニペットは挿入する場所が複数要る時だけ足し、通常のpropには`children`以外の名前を使う。
- スニペットは`{@render name()}`で描画する。

## SvelteKitのload関数

- データベースやファイルを扱う処理はserver load（`+page.server.ts`、`+layout.server.ts`）に置く。
  universal load（`+page.ts`、`+layout.ts`）はブラウザーでも実行されるため、公開してよい情報だけを扱う。
- 同じルートにserver loadとuniversal loadがある時は、server loadの戻り値がuniversal loadの引数`data`になる。
- フォームの送信とデータの変更はform actions（`+page.server.ts`の`actions`）を優先する。
- CSRFの防御は組み込みのOrigin検査が有効な時に働き、`kit.csrf.trustedOrigins`に`"*"`を含める（SvelteKit 3。2系では`csrf.checkOrigin: false`）と全面的に無効になる。
  無効にする時は代わりの防御を置き、プロキシ経由のOriginの不一致はアダプターの`ORIGIN`設定か`Sec-Fetch-Site`による判定で解決する。
- APIキーなどの秘匿値は、サーバー専用のモジュール（`+page.server.ts`、`+layout.server.ts`、`+server.ts`、サーバーフック、`$lib/server`の下）だけで扱う。

## SSR前提の実装

- `window`や`document`などのブラウザー専用APIは、`$app/environment`の`browser`で判定してから呼ぶ。
  判定はトップレベルのscriptや`$derived.by`などSSRでも実行される箇所に置く（サーバーで実行されない`$effect`の中は判定が要らないと公式が示す）。

## セキュリティと状態共有

- `{@html}`はエスケープを無効にするため、システムが生成した静的な内容かサニタイズ済みの文字列だけを渡す。
- サーバーのモジュールスコープやグローバルの変数にユーザー固有のデータを持つことと、load関数でストアやグローバルの状態を書き換えることを避ける。
  サーバーのプロセスは複数のユーザーで共有されるため、他のユーザーへ情報が漏れる。
- SSRでcontextを使って状態を共有する時は、親から子への一方向にする。
  SSR中に子で更新した状態は描画済みの親に反映されず、hydrationで画面がちらつく。
