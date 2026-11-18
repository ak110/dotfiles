# C#記述スタイル

C#のコードとテストを書く時とレビューする時の、プロジェクト設定、非同期と例外の決まり、セキュリティで採る手段を示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用として、C#に固有の決まりだけを置く。
.NETのコード分析（`CA`の規則）が有効な環境では、その診断に従う。

## 言語スタイル

- 現行の.NET LTS以降を前提にし、`Nullable`と`TreatWarningsAsErrors`を有効にする。
- `async void`はWindows FormsとWPFのイベントハンドラーに限り、ハンドラーの中で例外を捕捉する。
- `ConfigureAwait(false)`はUIに依存しないライブラリ層で付け、`SynchronizationContext`に依存するアプリケーション層（Windows Forms、WPF、Blazorなど）では付けない。
- 広い例外の捕捉は、フックのコールバック、`WndProc`、ワーカースレッドの最上位などに限る。
  `when`フィルターか、箇所を限った`#pragma warning disable CA1031`と理由のコメントで書く。
- 再送出は`throw;`で書く（`CA2200`）。
- SQLはパラメーター化し（`CA2100`）、外部プロセスは`ProcessStartInfo.ArgumentList`で起動する。
- 信頼できないXMLは`XmlResolver = null`で読み（`CA3075`）、`BinaryFormatter`の代わりに`System.Text.Json`かMessagePackを使う（`CA2300`）。
- セキュリティ用途の乱数は`RandomNumberGenerator`で作る（`CA5394`）。

## テストコード（xUnit）

- フレームワークは対象プロジェクトの採用に合わせる。
  新しく選ぶ時は、`dotnet test`で動き、並列実行とデータ駆動のテストを標準で持つもの（xUnitなど）にする。
- 現在時刻は`TimeProvider`（.NET 8以降）などで外から注入できる設計にする。
- テストプロジェクトの名前は`<対象>.Tests`にする。
