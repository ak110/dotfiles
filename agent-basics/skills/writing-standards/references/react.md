# React記述スタイル

本書は、Reactのコードを書く・直すエージェントに向けて、Effect（`useEffect`）の使い方の基準を定める。
対象は関数コンポーネントとHooksで書くReactのコードである。

## Effectを使う場面

- Effectは、コンポーネントを外部システム（ブラウザーAPI、ネットワーク、サードパーティのウィジェット、React以外で管理するDOMなど）と同期させるときに使う。
- propsやstateから計算できる値の導出は描画中の計算で、ユーザー操作への応答はその操作のイベントハンドラーで扱い、Effectを使わない。
- 個々の場面の書き方は、公式ドキュメント「You Might Not Need an Effect」（<https://react.dev/learn/you-might-not-need-an-effect>）の該当する場面と照らして決める。

## Effectの関数の名前

- `useEffect`へ渡す関数は、無名のアロー関数にせず、処理内容を表す名前を付けた関数にする。
  後からEffectの要否を見直すとき、本体を読まずに目的を判断できるようにするためである。

```tsx
useEffect(function syncScrollPosition() {
  window.scrollTo(0, savedScrollY);
}, [savedScrollY]);
```

- 名前が「値を計算する」「操作に応じる」内容になる場合は、前節の基準でEffect自体が不要でないかを見直す。
