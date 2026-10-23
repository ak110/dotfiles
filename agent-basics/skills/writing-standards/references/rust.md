# Rust記述スタイル

本書はRustのコードとテストコードの記述スタイル基準を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「セキュリティ・ロギング・エラー処理」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「セキュリティ・ロギング・エラー処理」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `testing.md#「安定性: flakyテスト防止」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「安定性: flakyテスト防止」` |

## 言語スタイル

- エラーハンドリング
  - ライブラリコードは`thiserror`で独自エラー型を定義し、アプリケーションコードは`anyhow`で扱う
  - `unwrap()`／`expect()`はテストか失敗し得ないことが自明な場合に限る。レビューではテストビルドにだけ含まれるコード（`#[cfg(test)]`配下と`[dev-dependencies]`経由でだけ到達するコード）を除いて評価する
  - `panic!`はプログラマエラー用とし、ユーザー入力やI/Oエラーへの使用は禁止する
- `unsafe`は極力避け、使う場合はブロックを最小化して安全性の根拠を`// SAFETY:`コメントで示す
  - Win32／COMの単純なAPI呼び出し（`SendMessageW`・`SetWindowPos`・`OpenClipboard`・COMオブジェクトの通常メソッド呼び出し等）で、所有権の遷移と生ポインタの受け渡しを伴わないものは省略してよい（根拠がドキュメント通りの引数を渡すことに尽きるため）
  - 次の場合は必ず付ける。生ポインタの読み書き、`ptr::read_unaligned`、`from_raw_parts`／`transmute`系のキャスト、`memmap2::Mmap::map`などライフタイム外の条件を前提とする操作である。
    `libloading`経由の関数呼び出し、手で実装した`Send`／`Sync`、COMオブジェクトの非自明な所有権遷移も含む
- 非同期ランタイムは`tokio`を基本とし、`block_on`は`main`等の境界でだけ使う
- セキュリティ（一般作法は`implementation-time.md`「セキュリティ・ロギング・エラー処理」）: `std::process::Command`へ引数を配列で渡しパスは`Path`／`PathBuf`で扱う。信頼できない入力は`serde`と明示的な構造体でデシリアライズし、`serde_json::Value`のまま後段へ渡さない。乱数はセキュリティ用途なら`rand::rngs::OsRng`、それ以外は`rand::thread_rng`
- MSRVは`Cargo.toml`の`rust-version`に明記する

## テストコード

### 単体テスト

- 単体テストは対象モジュール末尾の`#[cfg(test)]`のtestsモジュールに置き、最低限に留める
- 同期点は完了を観測できる決定論的な待機で確かめ、`thread::sleep`のポーリングにしない（`testing.md`「安定性: flakyテスト防止」）
- `#[repr(C)]`構造体のABI契約はサイズとオフセットをコンパイル時に検証する（努力目標。inline constはRust 1.79以降）

### 統合テスト

統合テストはクレートルート直下の`tests/`に置き、表駆動には`rstest`、性質による検証には`proptest`、性能の測定には`criterion`を選ぶ（標準の`#[bench]`はnightly限定）。
