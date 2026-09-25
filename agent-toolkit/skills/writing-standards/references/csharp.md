# C#記述スタイル

本書はC#のコードとテストコードの記述スタイル基準を定める。

## 言語スタイル

- .NETバージョン
  - 現行のLTS以降を前提にする
  - プロジェクト設定では`<Nullable>enable</Nullable>`と`<TreatWarningsAsErrors>true</TreatWarningsAsErrors>`を有効にする
- 非同期処理
  - `async void`は使わない（例外が捕捉できないため）
    - 例外としてWinForms／WPFのイベントハンドラのみ許容し、その場合はハンドラ内で例外をキャッチする（捕捉しない例外がプロセスを終了させるため）
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
- セキュリティの一般作法は`implementation-time.md`の「セキュリティ・ロギング・エラー処理」が定める。C#での対応は次のとおり
  - SQLは`SqlCommand.Parameters`／Dapper／EF Coreのパラメーター化クエリを使う
  - `Process.Start`は`ProcessStartInfo.ArgumentList`で引数を渡す
  - 信頼できないXMLは`XmlResolver = null`でXXEを無効化し、安全でない復元を避けるため`BinaryFormatter`に代えて`System.Text.Json`やMessagePackを使う
  - 乱数はセキュリティ用途なら`RandomNumberGenerator`、それ以外は`Random.Shared`
- 対象プロジェクトの`LangVersion`・`TargetFramework`で利用できる機能は公式ドキュメントで確認する
  <https://learn.microsoft.com/dotnet/csharp/whats-new/>

## テストコード（xUnit）

- テストフレームワークは、対象プロジェクトが既に採用するものへそろえる。
  新規に選ぶ場合は、`dotnet test`で実行でき、並列実行とデータ駆動テストを標準で持つものを選ぶ（`xUnit`など）
- 非同期処理の完了待ちは`ManualResetEventSlim`／`CountdownEvent`／`TaskCompletionSource`等のイベント駆動同期を使う
  - `Thread.Sleep`／`Task.Delay`による固定待機は避ける
- モック／スタブは、インターフェースからテスト用の実装を生成でき、呼び出し引数を検証できるライブラリを使う（`NSubstitute`・`Moq`など）
  - 外部依存はインターフェース経由で注入し、テスト時に差し替える
- 時刻は`TimeProvider`（.NET 8以降）を注入し、テストでは`FakeTimeProvider`で固定する
- ファイルI/Oのテストには一意な一時ディレクトリ（`Path.GetTempPath()` + `Guid.NewGuid()`）を使い、
  `try`／`finally`で確実にクリーンアップする
- テストプロジェクト名は`xxx.Tests`の規約に揃える
