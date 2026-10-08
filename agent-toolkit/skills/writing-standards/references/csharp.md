# C#記述スタイル

本書はC#のコードとテストコードの記述スタイル基準を定める。

## 言語スタイル

- 現行の.NET LTS以降を前提とし、NullableとTreatWarningsAsErrorsを有効にする
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
  - イベントの発行元が購読側より長く生存する場合は、`+=`で購読したイベントハンドラを対応する`-=`で必ず解除する
   （解除し忘れると購読先オブジェクトがGC対象にならず、メモリーリークや多重発火の原因になるため）
- EF Coreは取得量とN+1を考慮し、eager loadingと射影を選ぶ
- XMLドキュメントで公開APIの意図をsummaryへ記述する。自明な内容は省略してよい
- セキュリティの一般作法は`implementation-time.md`の「セキュリティ・ロギング・エラー処理」が定める。C#での対応は次のとおり
  - SQLは`SqlCommand.Parameters`／Dapper／EF Coreのパラメーター化クエリを使う
  - `Process.Start`は`ProcessStartInfo.ArgumentList`で引数を渡す
  - 信頼できないXMLは`XmlResolver = null`でXXEを無効化し、安全でない復元を避けるため`BinaryFormatter`に代えて`System.Text.Json`やMessagePackを使う
  - 乱数はセキュリティ用途なら`RandomNumberGenerator`、それ以外は`Random.Shared`

## テストコード（xUnit）

- テストフレームワークは、対象プロジェクトが既に採用するものへそろえる。
  新規に選ぶ場合は、`dotnet test`で実行でき、並列実行とデータ駆動テストを標準で持つものを選ぶ（`xUnit`など）
- 非同期処理は固定待機に頼らず、完了を観測できる決定論的な同期で確かめる
- 外部依存はインターフェース経由で差し替え、必要な呼出契約を検証する
- 時刻は`TimeProvider`（.NET 8以降）等で注入し、テストの時間を制御する
- ファイルI/Oは一時領域へ隔離し、終了時に回収する
- テストプロジェクト名は`xxx.Tests`の規約に揃える
