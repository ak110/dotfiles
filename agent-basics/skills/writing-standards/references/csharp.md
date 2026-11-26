# C#記述スタイル

C#で実装する担当がC#に固有の決まりに従い、ユーザーが非同期、例外、セキュリティの誤りを含むコードを受け取らないようにする。
C#のコードとテストを書く時とレビューする時の、プロジェクト設定、非同期と例外の決まり、セキュリティで採る手段を示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用として、C#に固有の決まりだけを置く。
.NETのコード分析（`CA`の規則）が有効な環境では、その診断に従う。

## 言語スタイル

- 識別子やプロトコル語など機械向けの文字列は、`StringComparison.Ordinal`または`OrdinalIgnoreCase`で比較し、変換には`ToUpperInvariant()`・`ToLowerInvariant()`を使う。
  引数なしの`ToUpper()`・`ToLower()`、カルチャを取らない`char.ToUpper`・`char.ToLower`、検索値に`string`を渡し比較方式を省略する`StartsWith`・`EndsWith`・`IndexOf`や`Compare`など、現在のカルチャに依存する形は人が読む文に限る。
- カルチャ指定と序数比較の漏れを検出するため、CA1304・CA1309・CA1310・CA1311を警告以上で有効にする。
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
- 経過や待機を試験で進める必要がある時は、`TimeProvider`（.NET 8以降）などで時計を置き換える。
  現在時刻の値だけを使う判定には、その値を渡す。
- テストプロジェクトの名前は`<対象>.Tests`にする。
