# C#記述スタイル

本書では、C#のプロダクションコードおよびテストコードにおける記述スタイルの基準を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「セキュリティ・ロギング・エラー処理」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「セキュリティ・ロギング・エラー処理」` |

## 言語スタイル

- 現行の.NET LTS以降のバージョンを前提とし、プロジェクト設定でNullable（Null許容参照型）とTreatWarningsAsErrors（警告をエラーとして処理）を有効にする。
- `async void`はWindows FormsやWPFのイベントハンドラーにのみ限定して使用し、ハンドラー内部で例外を確実にキャッチする。
- `ConfigureAwait(false)`はUIに依存しないライブラリ層やユーティリティ層で付与し、SynchronizationContextに依存するアプリケーション層（Windows Forms、WPF、Blazorなど）では付与しない。
- フックのコールバック、`WndProc`、ワーカースレッドの最上位防御層など、広範囲な例外キャッチが正当化される場面では、`when`フィルターを使用するか、対象箇所に限定して`#pragma warning disable CA1031`を指定した上で理由をコメントに明記する。例外を再スローする場合はスタックトレースを維持するために`throw;`を用いる。
- イベントの発行元オブジェクトが購読側よりも長く生存する場合は、`+=`によるイベント購読を対応する`-=`で明示的に解除する。
- Entity Framework Core（EF Core）ではデータ取得量とN+1問題に配慮し、必要に応じて先行読み込み（eager loading）や射影（Select句による特定列の取得）を選択する。
- 公開APIの設計意図はXMLドキュメントコメントのsummaryタグに記述する。自明な内容であれば記述を省略してよい。
- セキュリティ全般の一般的な指針は`implementation-time.md`「セキュリティ・ロギング・エラー処理」に従う。C#固有の実装では次の手段を採用する。
  - SQLクエリのパラメーター化（`SqlCommand.Parameters`、Dapper、EF Coreの利用）および外部プロセス実行時の`ProcessStartInfo.ArgumentList`の使用
  - 外部由来の信頼できないXMLを処理する際の`XmlResolver = null`の設定、および非推奨となった`BinaryFormatter`の代用としての`System.Text.Json`やMessagePackの採用
  - セキュリティ用途で乱数を生成する際の`RandomNumberGenerator`の利用

## テストコード（xUnit）

- テストフレームワークは対象プロジェクトで採用されているものに合わせる。新規に選定する場合は、`dotnet test`で実行可能であり、並列実行とパラメーター指定テスト（データ駆動テスト）を標準でサポートしているもの（`xUnit`など）を採用する。
- 現在時刻に依存する処理のテストでは、`TimeProvider`（.NET 8以降）などを利用して外部から時刻を注入できる設計にする。
- テストプロジェクトの名称は、対象に合わせて`xxx.Tests`という命名規約に統一する。
