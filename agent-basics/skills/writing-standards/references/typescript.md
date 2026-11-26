# TypeScript記述スタイル

TypeScriptで実装する担当が固有の書き方に従い、ユーザーが型の抜け穴や実行時の誤りを含むコードを受け取らないようにする。
本書は、TypeScriptのコードとテストを書く・直す時に、一般的な作法だけでは決まらない固有の書き方を示す。
一般知識で導ける作法を書かないのは`agent-basics:writing-standards/references/quality-principles-subtraction.md`の適用である。
Biomeやtypescript-eslintなどの検査が導入されている環境では、その診断に従う。

## 言語スタイル

- 識別子、拡張子、検索キーなど機械向けの大小文字変換には`toLowerCase()`・`toUpperCase()`を使う。
  `toLocaleLowerCase()`・`toLocaleUpperCase()`は人が読む自然言語に限る。
- 内部で値が無い状態は`undefined`に統一し、外部APIが返す`null`は取り込む時に`?? undefined`で変換する。
- JSONやデータベースへの保存など、`null`と未設定を区別する外部との境界へ渡す値は`null`のまま保つ。
- `any`を使わず、型の分からない値は`unknown`で受けて境界で検証して型を付ける（例: `SettingsSchema.parse(value)`）。
- 型注釈はexportする公開境界と推論で型が読み取れない箇所に限り、他は推論に任せる。
- 名前の衝突はnamespace import（`import * as X`）かexport側の名前で避け、importの別名は他に手段が無い時だけ使う。
- 型だけを参照する時は`import type`を使う。
- オブジェクトのプロパティはドット記法で参照し、値の出どころを明示する。
  Svelteの`$props()`の分割代入による宣言は例外とする（`agent-basics:writing-standards/references/svelte.md`「runesの使い分け」）。
- 配列の変換と抽出はfor文より`map`、`filter`、`flatMap`を使い、`filter`には型ガードを付けて後続の推論を保つ。
  順に`await`するループなど、for文でしか書けない処理は例外とする。

## 非同期処理

- 戻り値の`Promise`を意図して待たない時は`void func()`と書き、`await`の書き忘れと区別する（typescript-eslintの`no-floating-promises`）。
  例外を外へ伝えない設計では、あわせて`.catch(...)`を付ける。

## テストコード（vitest）

- テストフレームワークは既存のものに合わせ、新しく選ぶ時は`vitest`とする。
- `vi.mock()`はファイルの先頭へ巻き上げられるため、実行時の変数を参照できない。
- 時間の経過に依存するテストはfake timersなどで結果を一意にし、使ったタイマーとモックはテストの後で戻す。
