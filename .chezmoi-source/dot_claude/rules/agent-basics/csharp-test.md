---
paths:
  - "**/*Tests.cs"
  - "**/*Test.cs"
---

# C#テストコード記述スタイル

- テストフレームワークは `xUnit` を優先する （.NETのデファクト）
- テスト関数は `[Fact]` または `[Theory]` で書く
- パラメーター化は `[Theory]` + `[InlineData]` / `[MemberData]` / `[ClassData]` を使う
- アサーションは `Assert.Equal` / `Assert.True` など標準APIを使う
  - より読みやすい記述が必要な場合は `FluentAssertions` を検討する
- 非同期テストは `async Task` を返す。`async void` は使わない （例外が捕捉できない）
- 非同期処理の完了待ちは `ManualResetEventSlim` / `CountdownEvent` / `TaskCompletionSource` 等のイベント駆動同期を使う
  - `Thread.Sleep` / `Task.Delay` による固定待機は避ける
- セットアップ/ティアダウン
  - コンストラクタ = 各テスト前、`IDisposable.Dispose` = 各テスト後
  - クラス単位の共有は `IClassFixture<T>`、コレクション単位は `ICollectionFixture<T>` を使う
- モック/スタブには `NSubstitute` または `Moq` を使う
  - 外部依存はインターフェース経由で注入し、テスト時に差し替える
- 時刻は `TimeProvider` （.NET 8以降） を注入し、テストでは `FakeTimeProvider` で固定する
- ファイルI/Oのテストには一意な一時ディレクトリ (`Path.GetTempPath()` + `Guid.NewGuid()`) を使い、`try`/`finally` で確実にクリーンアップする
- テストプロジェクト名は `xxx.Tests` の規約に揃える
