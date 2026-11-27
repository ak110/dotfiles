# Rust記述スタイル

Rustで実装する担当が型、エラー処理、テストの規定を適用し、ユーザーが安全性の前提を確かめてコードを保守できるようにする。
Rustのコードとテストを書く時とレビューする時の、採用するライブラリと`unsafe`の根拠の書き方を示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用として、Rustに固有の決まりだけを置く。

## 言語スタイル

- エラー型はライブラリで`thiserror`、アプリケーションで`anyhow`を使う。
- `unwrap()`と`expect()`はテストと失敗しないことが自明な箇所に限る。
  レビューでは、テストビルドだけでコンパイルされるコード（`#[cfg(test)]`配下、`[dev-dependencies]`経由）を対象から外す。
- `unsafe`は安全な代替が無い時だけ範囲を最小にして使い、安全性の根拠を`// SAFETY:`コメントに書く。
  - 所有権の移動と生ポインタの受け渡しを伴わないWin32・COMの単純な呼び出し（`SendMessageW`、`OpenClipboard`など）では公式資料どおりの引数が根拠になるため、コメントを省いてよい。
  - 生ポインタの読み書き、`ptr::read_unaligned`、`from_raw_parts`・`transmute`などのキャスト、`memmap2::Mmap::map`のようにライフタイム管理の外の前提を要する操作には必ず書く。
    `libloading`経由の呼び出し、手で実装した`Send`・`Sync`、COMオブジェクトの非自明な所有権の移動も同じである。
- 非同期ランタイムは`tokio`を使い、`block_on`は`main`などの実行の境界だけで使う。
- 信頼できない外部入力は`serde`で明示した構造体へ変換してから後続へ渡す（`serde_json::Value`のまま渡すことを避ける）。
- セキュリティ用途の乱数はOSの乱数源から作る（rand 0.10以降は[`rand::rngs::SysRng`](https://docs.rs/rand/0.10.4/rand/rngs/struct.SysRng.html)、0.9は`rand::rngs::OsRng`）。
- MSRVは`Cargo.toml`の`rust-version`に書く。

## テストコード

### 単体テスト

`#[repr(C)]`の構造体のABI契約（サイズ、フィールドのオフセット）はコンパイル時に検証する（努力目標。`inline const`はRust 1.79以降）。

### 統合テスト

表駆動のテストは`rstest`、性質の検証は`proptest`、ベンチマークは`criterion`を使う（`#[bench]`はnightly限定）。
