# C#記述スタイル

本書はC#のコードとテストコードの記述スタイル基準を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「セキュリティ・ロギング・エラー処理」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「セキュリティ・ロギング・エラー処理」` |

## 言語スタイル

- 現行の.NET LTS以降を前提とし、NullableとTreatWarningsAsErrorsを有効にする
- `async void`はWinForms／WPFのイベントハンドラだけに使い、ハンドラ内で例外をキャッチする
- `ConfigureAwait(false)`はUIに依存しないライブラリ・ユーティリティ層で付け、SynchronizationContextに依存するアプリケーション層（WinForms／WPF／Blazorなど）では付けない
- 広域キャッチが正当な場面（フックコールバック、`WndProc`、ワーカースレッドの最終防御層など）では、`when`フィルタを使うか、`#pragma warning disable CA1031`をローカルに付けて理由をコメントする。再スローは`throw;`とする
- 発行元が購読側より長く生存するイベントは、`+=`の購読を対応する`-=`で解除する
- EF Coreは取得量とN+1を考慮し、eager loadingと射影を選ぶ
- 公開APIの意図はXMLドキュメントのsummaryへ書く。自明な内容は省略してよい
- セキュリティの一般作法は`implementation-time.md`の「セキュリティ・ロギング・エラー処理」が定める。C#では次を使う
  - パラメーター化クエリ（`SqlCommand.Parameters`・Dapper・EF Core）と`ProcessStartInfo.ArgumentList`
  - 信頼できないXMLでの`XmlResolver = null`と、`BinaryFormatter`に代わる`System.Text.Json`やMessagePack
  - セキュリティ用途の乱数には`RandomNumberGenerator`

## テストコード（xUnit）

- テストフレームワークは対象プロジェクトの採用へそろえる。新規に選ぶ場合は`dotnet test`で実行でき、並列実行とデータ駆動テストを標準で持つもの（`xUnit`など）を選ぶ
- 時刻は`TimeProvider`（.NET 8以降）などで注入する
- テストプロジェクト名は`xxx.Tests`の規約に揃える
