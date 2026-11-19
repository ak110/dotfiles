# React記述スタイル

本書は、関数コンポーネントとHooksで書くReactのコードで、Effect（`useEffect`）を使うかと書き方を決める時に読む。
要らないEffectを足さないのは`agent-basics:writing-standards/references/quality-principles-subtraction.md`の適用である。

## Effectを使う場面

- Effectは、コンポーネントを外部のシステム（ブラウザーAPI、ネットワーク、サードパーティのウィジェット、React以外が管理するDOMなど）と同期させる時に使う。
- propsやstateから計算できる値は描画中に計算し、ユーザー操作への応答はそのイベントハンドラーで扱う。
- 個々の場面は、公式ドキュメント「You Might Not Need an Effect」（<https://react.dev/learn/you-might-not-need-an-effect>）の該当する場面と照らして決める。

## Effectの関数の名前

- `useEffect`へ渡す関数は無名のアロー関数にせず、処理を表す名前を付ける（例: `useEffect(function syncScrollPosition() { ... }, [savedScrollY])`）。
  後でEffectの要否を見直す時に、本体を読まずに目的が分かるようにするためである。
- 名前が「値を計算する」「操作に応じる」内容になる時は、本書「Effectを使う場面」に照らしてEffect自体が要らないかを見直す。
