# C#記述スタイル

## 言語スタイル

- .NETバージョン
  - 現行のLTS以降を前提にする
  - プロジェクト設定では`<Nullable>enable</Nullable>`と`<TreatWarningsAsErrors>true</TreatWarningsAsErrors>`を有効にする
- 非同期処理
  - `async void`は使わない（例外が捕捉できないため）
    - 例外としてWinForms／WPFのイベントハンドラのみ許容し、その場合はハンドラ内で必ず例外をキャッチする
  - `ConfigureAwait(false)`はUIに依存しないライブラリ・ユーティリティ層で付ける
    - WinForms／WPF／Blazor等のSynchronizationContextに依存するアプリケーション層では付けない
- 例外処理
  - 広域キャッチが正当な場面（フックコールバック、`WndProc`、ワーカースレッドの最終防御層など）の対処:
    - `catch (Exception ex) when (...)`で`when`フィルタを使う
    - もしくは`#pragma warning disable CA1031`をローカルに付け、コメントで理由を明記する
  - 再スローは`throw;`を使う。`throw ex;`はスタックトレースが失われるため使わない
- リソース管理
  - イベントハンドラは`+=`で購読したら対応する`-=`で必ず解除する
   （解除し忘れると購読先オブジェクトがGC対象にならず、メモリーリークや多重発火の原因になるため）
- EF Coreでは`Include`でeager loadingを明示するか`Select`で射影する（暗黙の遅延ロードによるN+1クエリを防ぐため）
- ドキュメントコメントはXMLドキュメント（`///`）で書き、公開APIには`<summary>`を記述する（内容が自明な場合は省略してよい）
- セキュリティ上の危険パターン
  - SQLはパラメーター化クエリを使う（`SqlCommand.Parameters`／Dapper／EF Coreのパラメーター）
  - `Process.Start`は`ProcessStartInfo.ArgumentList`で引数を渡す（文字列結合は避ける）
  - 信頼できないXMLは`XmlResolver = null`でXXEを無効化する
  - `BinaryFormatter`は使わない（非推奨・安全でない）。`System.Text.Json`やMessagePackで代替
  - 乱数はセキュリティ用途なら`RandomNumberGenerator`、それ以外は`Random.Shared`
- 他で指定が無い場合のツール推奨:
  - ビルド: `dotnet` CLI
  - フォーマッター: `dotnet format`
  - アナライザー: Roslynアナライザー + `Microsoft.CodeAnalysis.NetAnalyzers`（`.editorconfig`で設定）
- 新しいC#／.NETバージョンの機能を積極的に使う
  - C# 12+: collection expressions（`[1, 2, 3]`／`[..existing, x]`）で配列・リスト・Spanを簡潔に初期化する
    - コンテキストに応じた最適な型が選ばれる
    - 中間コレクションの割り当ても削減される
  - C# 12+: primary constructorsを非recordのクラス／構造体でも使う
    - コンストラクタ引数のフィールド代入ボイラープレートを削減できるため
  - C# 12+: `using MyTuple = (string Name, int Age);`の形式で任意の型をエイリアス化する
    - タプル型や関数ポインタなどの複雑な型を可読な名前で扱えるため
  - C# 12+: ラムダ式のデフォルトパラメーター（`(x, y = 10) => ...`）を活用する
  - C# 13+: `params ReadOnlySpan<T>`／`params IEnumerable<T>`等で配列以外の`params`を受け取る
    - 呼び出し側の割り当てを抑えられるため
  - C# 13+（.NET 9+）: 同期ブロックには`System.Threading.Lock`型を使う
    - `lock(myLock) { ... }`が`Lock.EnterScope()`ベースの高速パスに最適化される
    - 従来の`lock(object)`より低オーバーヘッドになる
  - .NET 9+: LINQの`CountBy`／`AggregateBy`を使う
   （`GroupBy`ベースの集計で発生する中間コレクション生成を回避できるため）

## テストコード（xUnit）

- テストフレームワークは`xUnit`を優先する（.NETのデファクト）
- 非同期処理の完了待ちは`ManualResetEventSlim`／`CountdownEvent`／`TaskCompletionSource`等のイベント駆動同期を使う
  - `Thread.Sleep`／`Task.Delay`による固定待機は避ける
- モック／スタブには`NSubstitute`または`Moq`を使う
  - 外部依存はインターフェース経由で注入し、テスト時に差し替える
- 時刻は`TimeProvider`（.NET 8以降）を注入し、テストでは`FakeTimeProvider`で固定する
- ファイルI/Oのテストには一意な一時ディレクトリ（`Path.GetTempPath()` + `Guid.NewGuid()`）を使い、
  `try`／`finally`で確実にクリーンアップする
- テストプロジェクト名は`xxx.Tests`の規約に揃える
