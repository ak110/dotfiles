# Rust記述スタイル

## 言語スタイル

- エラーハンドリング
  - ライブラリコードでは`thiserror`で独自エラー型を定義する（呼び出し側でエラー種別ごとの分岐を可能にするため）
  - アプリケーションコードでは`anyhow`で動的なエラーを扱う（エラー集約の実装負荷を下げ、コンテキスト付与も容易にするため）
  - `unwrap()`／`expect()`はテストか、失敗し得ないことが自明な場合に限定する
    - レビュー時は`#[cfg(test)]`内とテスト用ヘルパーモジュール（`test_helpers.rs`等）を除外して評価する
  - `panic!`は不変条件違反などプログラマエラー用。ユーザー入力やI/Oエラーには使わない
- `unsafe`は極力避ける。使う場合はブロックを最小化し、安全性の根拠を`// SAFETY:`コメントで示す
  - 例外: Win32／COMの単純なAPI呼び出しは`// SAFETY:`を省略してよい
    - 対象例: `SendMessageW`・`SetWindowPos`・`OpenClipboard`・COMオブジェクトの通常メソッド呼び出し等
    - 安全性の根拠が「Microsoftドキュメント通りの引数を渡しているだけ」になるため
  - 以下のケースでは必ず`// SAFETY:`を付ける:
    - 生ポインタの読み書き、`ptr::read_unaligned`、`from_raw_parts`／`transmute`系のキャスト
    - `memmap2::Mmap::map`などライフタイム外の前提に依存する操作
    - `libloading`経由の関数呼び出し（シグネチャ一致が安全性の根拠）
    - `Send`／`Sync`を手で実装している型
    - COMオブジェクトの非自明な所有権遷移
- 非同期処理
  - ランタイムは`tokio`を基本とする
  - `block_on`は`main`等の境界でのみ使う
- セキュリティ上の危険パターン
  - `std::process::Command`はshell経由（`sh -c`）を避け、引数を配列で渡す
  - 信頼できない入力のデシリアライズは`serde` + 明示的な構造体で行う（`serde_json::Value`のまま後段へ渡さない）
  - パスは`Path`／`PathBuf`で扱い、文字列結合で組み立てない
  - 乱数はセキュリティ用途なら`rand::rngs::OsRng`、それ以外は`rand::thread_rng`
- 他で指定が無い場合のツール推奨:
  - ビルド／依存管理: `cargo`
  - リンター: `cargo clippy`（`-D warnings`で警告ゼロを維持）
  - フォーマッター: `cargo fmt`
  - MSRV（最小サポートバージョン）は`Cargo.toml`の`rust-version`に明記する
- テスト（inline, 最低限）
  - 単体テストは対象モジュール末尾の`#[cfg(test)] mod tests { ... }`に配置する
  - 統合テストはクレートルート直下の`tests/`に置く（後述の統合テスト節を参照）
  - ポーリング + `thread::sleep`を避け、`crossbeam-channel::recv_timeout`などの確定待機を使う
   （sleepループはflakyテストの主要因となるため）
  - `#[repr(C)]`構造体のサイズ・オフセット検証は`const { assert!(size_of::<T>() == N) }`でcompile-timeに行う
   （Rust 1.79+）。実行時テストにはしない
- 新しいRustバージョンの機能を積極的に使う
  - Rust 1.77+: C文字列リテラル（`c"..."`）で`&'static CStr`を直接生成する
    - FFIでnul終端C文字列を割り当て無しで渡せるため
  - Rust 1.77+: `std::mem::offset_of!`マクロで`#[repr(C)]`構造体のフィールドオフセットを取得する
    - unsafe不要でメモリーレイアウト検査ができる
  - Rust 1.80+: `std::sync::LazyLock`／`std::cell::LazyCell`で遅延初期化する
    - `lazy_static`／`once_cell`クレートへの依存を排除できる
  - Rust 1.82+: `extern`ブロック内の個別関数に`safe`／`unsafe`を付けてFFI安全性を明示する
    - 呼び出し側でunsafeブロックが必要か否かをコンパイラレベルで強制できるため
  - Rust 1.82+: `impl Trait + use<...>`でprecise capturingを表現する
    - 不要なlifetimeキャプチャを避けて戻り値型を厳密にできる
  - Edition 2024（Rust 1.85+）: let chains（`if let Some(x) = foo && x > 0 { ... }`）でネストを削減する
  - Edition 2024（Rust 1.85+）: asyncクロージャ（`async || { ... }`）と`AsyncFn`系トレイトを使う
    - 単純な非同期処理で`async-trait`クレートへの依存を削減できる

## テストコード（統合テスト）

クレート直下の`tests/`ディレクトリ配下に置く統合テスト向けの方針。
inline単体テストは上記「言語スタイル」節の方針に従う。

- パラメーター化テストは`rstest`の`#[rstest]` + `#[case]`を使う
- プロパティベースの網羅検証が有効な場合は`proptest`を検討する
- ベンチマークは`criterion`を使う（標準の`#[bench]`はnightly限定）
