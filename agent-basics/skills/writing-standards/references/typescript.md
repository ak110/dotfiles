# TypeScript記述スタイル

本書は、TypeScriptのコードおよびテストコードを記述するにあたり、一般的なコーディング作法だけでは判断しにくい固有のルールをまとめたものである。
共通の設計基準や実装方針は`implementation-time.md`を、セキュリティに関する一般的な留意事項は同資料の「セキュリティ・ロギング・エラー処理」を参照する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time.md` |
| Svelteの分割代入の例外を判断する時 | `svelte.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/svelte.md` |

## 言語スタイル

- 内部処理における「値が存在しない」状態の表現は`undefined`に統一する。外部APIから返された`null`は、内部ロジックに取り込む段階で`?? undefined`を用いて変換する。ただし、JSONデータやデータベースへの保存時など、`null`と未設定（値の不在）を厳密に区別する必要がある外部境界へ渡す値については、`null`をそのまま維持する。
- `any`を使わず、型の分からない値は`unknown`として受け取り、`implementation-time.md`「コードの簡潔さ」が定める境界で検証して型を付ける。型注釈はexportする公開境界と、推論だけでは型が読み取れない箇所に限り、それ以外は型推論に任せる。

```typescript
// 良い例
export function parseSettings(value: unknown) {
    return SettingsSchema.parse(value);
}

// 悪い例
export function parseSettings(value: any): Settings {
    return value;
}
```

- 名前の衝突にはnamespace import（`import * as X`）またはexport側の名前を使い、それ以外の手段がない場合にだけimportの別名を使う。型だけを参照するときは`import type`を使う。namespace importと値の動的import（`await import("…")`）は用途に応じて使う。

```typescript
// 良い例
import type { Settings } from "./settings.js";
import * as api from "./api.js";

// 悪い例
import { load as loadSettings } from "./settings.js";
type Settings = import("./settings.js").Settings;
```

- オブジェクトのプロパティはドット記法で参照し、値の出どころが読み取れる形を保つ。`svelte.md`が定める`$props()`の分割代入による宣言は例外とする。

```typescript
// 良い例
show(user.name);

// 悪い例
const { name } = user;
show(name);
```

- 配列の変換と抽出はfor文より`map`、`filter`、`flatMap`を使い、`filter`には型ガードを付けて後続の型推論を保つ。`await`を順番に行うループなど、for文でなければ書けない処理は例外とする。

```typescript
// 良い例
const names = users.filter((user): user is User => user !== undefined).map((user) => user.name);

// 悪い例
const names: string[] = [];
for (const user of users) if (user !== undefined) names.push(user.name);
```

## 非同期処理

- 戻り値の`Promise`を意図的に待機せず無視する場合は、`await`の記述漏れと区別し、`no-floating-promises`ルールのチェックを通すために`void func()`と明示する。例外の伝播を防ぐ設計とする場合は、あわせて`.catch(...)`を付与する

## テストコード（vitest）

- テストフレームワークは各対象プロジェクトで採用されているものに合わせ、新規に導入する場合は`vitest`を選定する
- `vi.mock()`はファイルの先頭にホイスティング（巻き上げ）されるため、実行時の動的な変数値などを参照できない点に留意する
- 時間の経過に依存するテストは、fake timersなどを活用して結果が常に一意に定まるように制御し、使用したタイマーやモックはテスト終了後に必ず後始末する
